"""Reproduce Python regression checks in fresh, restricted Docker containers.

The model can propose a recipe; only measured test outcomes decide the verdict.
No repository commands execute on the host. Other test adapters are deferred.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from fleet.history.patch import PathKind, classify_path
from fleet.workspace import git, write_json


def run(args: list[str], *, timeout: int = 600, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)


def snapshot(root: Path, sha: str, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    result = run(["git", "-C", str(root), "archive", "--format=tar", sha])
    if result.returncode:
        raise ValueError("Cannot materialize the requested historical snapshot")
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        archive.extractall(target, filter="data")


def junit(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("JUnit report missing or unsafe")
    if path.stat().st_size > 20_000_000:
        raise ValueError("JUnit report exceeds size limit")
    tree = ET.parse(path)
    tests = {}
    for case in tree.iter("testcase"):
        key = f"{case.get('classname', '')}::{case.get('name', '')}"
        if key in tests:
            raise ValueError(f"Duplicate test identity: {key}")
        status = "pass"
        for tag, value in (("skipped", "skip"), ("failure", "fail"), ("error", "error")):
            if case.find(tag) is not None:
                status = value
        tests[key] = status
    if not tests:
        raise ValueError("No tests collected")
    return tests


def compare(before: dict[str, str], after: dict[str, str]) -> dict:
    f2p = sorted(k for k, v in before.items() if v == "fail" and after.get(k) == "pass")
    p2p = sorted(k for k, v in before.items() if v == "pass" and after.get(k) == "pass")
    errors = []
    if set(before) != set(after):
        errors.append("Test identities changed between comparison states")
    if "error" in before.values():
        errors.append("Pre-fix run has harness/collection errors")
    if any(v not in {"pass", "skip"} for v in after.values()):
        errors.append("Gold run contains failures or errors")
    if any(after.get(k) != "pass" for k, v in before.items() if v in {"pass", "fail"}):
        errors.append("A previously runnable test no longer passes")
    if not f2p:
        errors.append("No meaningful fail-to-pass regression")
    if not p2p:
        errors.append("No pass-to-pass regression guard")
    return {"fail_to_pass": f2p, "pass_to_pass": p2p, "errors": errors}


def validate(candidate: dict, root: Path, out: Path, recipe: dict, *, timeout: int = 600,
             progress=None) -> dict:
    """Runs base once and each comparison state twice; stores every outcome."""
    out.mkdir(parents=True, exist_ok=True)
    result = {"candidate_id": candidate["id"], "status": "not_verified", "errors": [], "runs": [],
              "validator_version": "1", "measured_at": datetime.now(UTC).isoformat(),
              "base_sha": candidate["base_sha"], "fixed_sha": candidate["fixed_sha"], "recipe": recipe,
              "meaning": "Reproduced selected checks only; not a complete correctness proof or published task."}
    try:
        if candidate.get("status") != "ready_for_review":
            raise ValueError("Candidate needs issue/integration enrichment before validation")
        if not any(i.get("state") == "closed" and i.get("body") for i in candidate.get("issues", [])):
            raise ValueError("A closed issue with a problem description is required")
        image = recipe.get("image", "")
        install = recipe.get("install_command", "")
        paths = recipe.get("test_paths", [])
        if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]+", image):
            raise ValueError("Recipe needs a Docker image reference")
        if not isinstance(install, str) or not install.strip() or len(install) > 4000:
            raise ValueError("Recipe needs a bounded installation command")
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or p.startswith(("-", "/")) or ".." in Path(p.split("::")[0]).parts for p in paths):
            raise ValueError("test_paths must be nonempty repository-relative pytest paths")
        files = candidate["patch"]["files"]
        source_paths = [f["path"] for f in files if classify_path(f["path"]) is PathKind.SOURCE]
        test_paths = [f["path"] for f in files if classify_path(f["path"]) is PathKind.TEST]
        config_paths = [f["path"] for f in files if classify_path(f["path"]) is PathKind.CONFIG]
        # This adapter intentionally handles separated Python source/test files.
        if not source_paths or any(not p.endswith(".py") for p in source_paths):
            raise ValueError("Current runtime adapter supports Python source changes only")
        if not test_paths:
            raise ValueError("No separate regression-test patch; synthesis/inline tests are not implemented")
        manifests = {"setup.py", "setup.cfg", "pyproject.toml", "requirements.txt", "requirements.in", "uv.lock", "poetry.lock"}
        if any(Path(p).name in manifests for p in config_paths):
            raise ValueError("Dependency-changing patch needs an explicit environment migration recipe")
        for p in paths:
            if not git(root, "ls-tree", "--name-only", candidate["base_sha"], "--", p.split("::")[0]):
                raise ValueError("Select an existing test directory/file so the base health run is meaningful")
        docker = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
        if docker.returncode:
            raise ValueError("Docker daemon unavailable")
        if progress:
            progress("Preparing historical Python environment")
        image_check = run(["docker", "image", "inspect", image], timeout=30)
        if image_check.returncode:
            pulled = run(["docker", "pull", image], timeout=600)
            (out / "pull.log").write_bytes(pulled.stdout + pulled.stderr)
            if pulled.returncode:
                raise ValueError("Docker base image pull failed; see pull.log")
            image_check = run(["docker", "image", "inspect", image], timeout=30)
        image_data = json.loads(image_check.stdout)[0]
        pinned = (image_data.get("RepoDigests") or [image_data["Id"]])[0]
        result["base_image_id"] = image_data["Id"]
        result["base_image_reference"] = pinned
        key = hashlib.sha256(json.dumps([candidate["repo"], candidate["base_sha"], pinned, install, "validator-v1"], sort_keys=True).encode()).hexdigest()[:24]
        tag = f"fleet-validation:{key}"
        with tempfile.TemporaryDirectory(prefix="fleet-validate-") as tmp:
            work = Path(tmp)
            base = work / "base"
            snapshot(root, candidate["base_sha"], base)
            dockerfile = "\n".join([f"FROM {pinned}", "WORKDIR /workspace", "COPY base/ /workspace/",
                                    "RUN " + json.dumps(["/bin/sh", "-lc", install]), "ENV HOME=/tmp"]) + "\n"
            (work / "Dockerfile").write_text(dockerfile)
            (out / "Dockerfile").write_text(dockerfile)
            cached = run(["docker", "image", "inspect", tag], timeout=30)
            result["environment_cache_hit"] = cached.returncode == 0
            if cached.returncode:
                built = run(["docker", "build", "--tag", tag, str(work)], timeout=900)
                (out / "build.log").write_bytes(built.stdout + built.stderr)
                if built.returncode:
                    raise ValueError("Environment build failed; see build.log")
            identity = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], timeout=30)
            if identity.returncode:
                raise ValueError("Cannot identify built environment")
            built_id = identity.stdout.decode().strip()
            result["environment_image_id"] = built_id
            patches = {}
            for kind, selected in (("test", test_paths), ("source", source_paths)):
                patch = run(["git", "-C", str(root), "diff", "--binary", candidate["base_sha"], candidate["fixed_sha"], "--", *selected])
                if patch.returncode:
                    raise ValueError("Patch extraction failed")
                patches[kind] = patch.stdout
                (out / f"{kind}.patch").write_bytes(patch.stdout)
            result["patch_sha256"] = {k: hashlib.sha256(v).hexdigest() for k, v in patches.items()}
            for state in ("base", "before-1", "after-1", "before-2", "after-2"):
                if progress:
                    progress(f"Running {state}")
                checkout = work / f"run-{state}"
                shutil.copytree(base, checkout)
                for kind in (() if state == "base" else (("test", "source") if state.startswith("after") else ("test",))):
                    applied = run(["git", "apply", "--binary", "-"], cwd=checkout, input=patches[kind])
                    if applied.returncode:
                        raise ValueError(f"{kind} patch does not apply cleanly to {state}")
                evidence = out / state
                evidence.mkdir(exist_ok=True)
                # Never reuse a JUnit file left by a previous attempt.
                (evidence / "junit.xml").unlink(missing_ok=True)
                name = f"fleet-{uuid.uuid4().hex[:16]}"
                args = ["docker", "run", "--rm", "--name", name, "--network=none", "--cap-drop=ALL",
                        "--security-opt=no-new-privileges", "--memory=2g", "--cpus=2", "--pids-limit=256",
                        "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=512m", "--user", f"{os.getuid()}:{os.getgid()}",
                        "--mount", f"type=bind,src={checkout},dst=/workspace",
                        "--mount", f"type=bind,src={evidence.resolve()},dst=/results",
                        "--workdir", "/workspace", "--entrypoint", "python", built_id,
                        "-m", "pytest", "-q", "--junitxml=/results/junit.xml", *paths]
                try:
                    executed = run(args, timeout=timeout)
                except subprocess.TimeoutExpired:
                    run(["docker", "rm", "-f", name], timeout=30)
                    raise ValueError(f"{state} timed out")
                (evidence / "run.log").unlink(missing_ok=True)
                (evidence / "run.log").write_bytes(executed.stdout + executed.stderr)
                outcomes = junit(evidence / "junit.xml")
                result["runs"].append({"state": state, "exit_code": executed.returncode, "tests": outcomes})
                if state == "base" and (executed.returncode or any(v not in {"pass", "skip"} for v in outcomes.values())):
                    raise ValueError("Historical base is not healthy for the selected suite")
            states = {r["state"]: r for r in result["runs"]}
            measured = compare(states["before-1"]["tests"], states["after-1"]["tests"])
            result.update(measured)
            result["pass_to_pass"] = [k for k in result["pass_to_pass"] if states["base"]["tests"].get(k) == "pass"]
            if not result["pass_to_pass"]:
                result["errors"].append("No existing base tests survived as pass-to-pass guards")
            for side in ("before", "after"):
                if states[f"{side}-1"]["tests"] != states[f"{side}-2"]["tests"]:
                    result["errors"].append(f"{side} test outcomes are not reproducible")
            if any(states[f"after-{i}"]["exit_code"] != 0 for i in (1, 2)):
                result["errors"].append("Gold command did not exit successfully")
            if any(states[f"before-{i}"]["exit_code"] != 1 for i in (1, 2)):
                result["errors"].append("Pre-fix command did not report ordinary pytest test failure")
            if not result["errors"]:
                result["status"] = "verified_checks"
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, ET.ParseError, tarfile.TarError) as exc:
        result["errors"].append(str(exc))
    write_json(out / "validation.json", result)
    return result
