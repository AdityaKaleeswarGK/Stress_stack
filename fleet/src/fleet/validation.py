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


_LOAD_ERRORS = ("ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError")


def failure_kinds(path: Path) -> dict[str, str]:
    """Why each failing test failed: `assertion`, `load_error` or `exception`.

    Evidence, not a verdict. A test that fails before the fix because its body
    cannot import a name the fix introduces is a real fail-to-pass, but it shows
    the feature was absent, not that behaviour was wrong — a reviewer deciding
    whether this is a bug-fix task needs to see which one it was.
    """
    kinds = {}
    for case in ET.parse(path).iter("testcase"):
        node = case.find("failure")
        if node is None:
            continue
        text = (node.get("message") or "") + "\n" + (node.text or "")
        head = text.strip().split(":", 1)[0].rsplit(".", 1)[-1].strip()
        if head in _LOAD_ERRORS or any(f"E   {name}" in text for name in _LOAD_ERRORS):
            kind = "load_error"
        elif head == "AssertionError" or head.startswith("assert") or "\nE   assert " in text:
            kind = "assertion"
        else:
            kind = "exception"
        kinds[f"{case.get('classname', '')}::{case.get('name', '')}"] = kind
    return kinds


def source_roots(checkout: Path) -> list[str]:
    """Container paths that must outrank an installed copy of the package.

    The image is built by installing the *base* snapshot; the run mounts a
    patched checkout over it. With a non-editable install of a `src/` layout
    project, `import pkg` resolves to site-packages — the base code — in every
    state, so before and after measure the same thing and a real fix is
    reported as "no fail-to-pass". Putting the mounted tree first makes the
    code under test the code that was patched, whatever the install command did.
    """
    roots = ["/workspace/src"] if (checkout / "src").is_dir() else []
    return [*roots, "/workspace"]


def stable(runs: list[dict[str, str]]) -> tuple[dict[str, str], list[str]]:
    """Each test's status where every repeat agrees, and the tests where they did not.

    A test missing from some repeats but present in others is unstable too:
    collection that changes between identical runs is the same defect as a
    verdict that does.
    """
    keys = set().union(*runs) if runs else set()
    agreed, flaky = {}, []
    for key in sorted(keys):
        seen = {run.get(key, "absent") for run in runs}
        if len(seen) == 1:
            agreed[key] = seen.pop()
        else:
            flaky.append(key)
    return agreed, flaky


def judge(base: dict[str, str] | None, before_runs: list[dict[str, str]],
          after_runs: list[dict[str, str]]) -> dict:
    """Classify every test across the three states, then decide.

    `base` is the base code with its own tests, `before` the base code with the
    fix's tests (the no-op control) and `after` the fixed code with them (the
    oracle). `base` is optional: without it, a guard only has to pass before and
    after, and tests already failing at the base are not separately known. Three earlier rules rejected whole candidates for problems confined
    to tests that decide nothing, and are replaced:

    * A test already failing at the base is *recorded*, not fatal. Historical
      commits routinely carry one broken test; rejecting the candidate threw
      away every fix landed while it was broken.
    * A test that disagrees across repeats is *quarantined* — excluded from both
      fail-to-pass and pass-to-pass, as CI systems do — instead of failing the
      candidate. It is fatal only if nothing measurable is left.
    * A test that errors (cannot be collected) before and passes after is kept
      apart as `error_to_pass`. The literature's validated-task false positives
      were exactly this case, so it is evidence, never a fail-to-pass.

    What stays fatal is what makes the task wrong: no stable fail-to-pass, a
    test that passed before and fails, vanishes or skips after the fix, or no
    pass-to-pass guard at all.
    """
    before, flaky_before = stable(before_runs)
    after, flaky_after = stable(after_runs)
    quarantined = sorted(set(flaky_before) | set(flaky_after))
    steady = lambda key: key not in quarantined
    f2p = sorted(k for k, v in before.items() if v == "fail" and after.get(k) == "pass" and steady(k))
    error_to_pass = sorted(k for k, v in before.items() if v == "error" and after.get(k) == "pass" and steady(k))
    absent_before = sorted(k for k, v in after.items() if v == "pass" and k not in before and steady(k))
    p2p = sorted(k for k, v in before.items()
                 if v == "pass" and after.get(k) == "pass" and (base is None or base.get(k) == "pass") and steady(k))
    pass_to_fail = sorted(k for k, v in before.items()
                          if v == "pass" and after.get(k) in {"fail", "error"} and steady(k))
    vanished = sorted(k for k, v in before.items() if v == "pass" and k not in after and steady(k))
    pass_to_skip = sorted(k for k, v in before.items() if v == "pass" and after.get(k) == "skip" and steady(k))
    unresolved = sorted(k for k, v in after.items()
                        if v in {"fail", "error"} and before.get(k) in {"fail", "error"} and steady(k))
    preexisting = sorted(k for k, v in (base or {}).items() if v in {"fail", "error"})

    errors = []
    if not f2p:
        causes = []
        if error_to_pass or absent_before:
            causes.append(f"{len(error_to_pass)} error-to-pass and {len(absent_before)} absent-before,"
                          " which could not run before the fix and so show the code was missing,"
                          " not that it misbehaved")
        if quarantined:
            causes.append(f"{len(quarantined)} quarantined as unstable across repeats")
        errors.append("No stable fail-to-pass test" + (f" ({'; '.join(causes)})" if causes else ""))
    if pass_to_fail:
        errors.append(f"{len(pass_to_fail)} test(s) passed before the fix and fail after it")
    if vanished:
        errors.append(f"{len(vanished)} passing test(s) disappeared under the fix")
    if pass_to_skip:
        errors.append(f"{len(pass_to_skip)} passing test(s) became skipped under the fix")
    if not p2p:
        errors.append("No pass-to-pass regression guard")
    return {
        "fail_to_pass": f2p, "pass_to_pass": p2p, "errors": errors,
        "error_to_pass": error_to_pass, "absent_before": absent_before,
        "quarantined": quarantined, "pass_to_fail": pass_to_fail, "vanished": vanished,
        "pass_to_skip": pass_to_skip, "unresolved": unresolved, "preexisting_failures": preexisting,
    }


def compare(before: dict[str, str], after: dict[str, str]) -> dict:
    """One before run against one after run, with no base run: the single-sample case."""
    measured = judge({k: "pass" for k, v in before.items() if v == "pass"}, [before], [after])
    return {k: measured[k] for k in ("fail_to_pass", "pass_to_pass", "errors")}


_HUNK = re.compile(rb"^@@ ", re.MULTILINE)


def split_hunks(patch: bytes) -> list[dict]:
    """One self-contained patch per hunk, for reverting a single hunk at a time.

    Files git cannot express as plain text hunks — binary, renamed, mode-only —
    are left out: a probe needs a change it can take back exactly.
    """
    hunks = []
    for chunk in re.split(rb"(?m)^(?=diff --git )", patch):
        if not chunk.startswith(b"diff --git "):
            continue
        header_end = _HUNK.search(chunk)
        if header_end is None or b"GIT binary patch" in chunk or b"\nrename from " in chunk:
            continue
        header = chunk[:header_end.start()]
        path = header.split(b"\n", 1)[0].split(b" b/", 1)[-1].decode(errors="replace")
        bodies = _HUNK.split(chunk[header_end.start():])
        for index, body in enumerate(b for b in bodies if b):
            hunks.append({"path": path, "index": index, "patch": header + b"@@ " + body})
    return hunks


def pin_date(root: Path, sha: str) -> str:
    """The commit's date as an RFC 3339 UTC timestamp, for upload-date pinning."""
    stamp = datetime.fromisoformat(git(root, "show", "-s", "--format=%cI", sha))
    return stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# One run per state by default: before the fix and after it. Repeats (and the
# flaky-test quarantine they enable), the separate base run and the hunk
# probes are built and tested but off, to keep the first end-to-end runs
# simple and cheap (decided 2026-11-21; see graph_audit/PIPELINE_STATUS.md).
# The known cost: with one run per side, a test that fails by chance before
# the fix and passes after it looks like a real fail-to-pass test.
DEFAULT_REPEATS = 1
MAX_HUNK_PROBES = 8
# pytest exits 0 (all passed) or 1 (some failed) when it ran and reported.
# --continue-on-collection-errors is what lets a fix's new test module that
# cannot import before the fix be recorded as an error while every other test
# still reports, instead of pytest stopping with exit 2 and reporting nothing.
_RESULT_EXIT_CODES = {0, 1}


def sandbox_runtime() -> str:
    """The OCI runtime test containers run under: gVisor when Docker offers it.

    runc shares the host kernel with the code under test; gVisor puts a
    user-space kernel between them, which is the boundary hosted agent
    sandboxes treat as the minimum for untrusted code. Measured on this
    project's laptop setup (Colima, arm64), gVisor gave identical per-test
    results on pluggy (144 tests) and glom (202) at about 25-35% more test
    time and 0.3-0.4 s more per container.

    `FLEET_SANDBOX_RUNTIME` overrides: `runc`, `runsc`, or `auto` (default).
    An explicit `runsc` that Docker does not offer fails at launch, which is
    the point: asking for the stronger boundary must not silently get the
    weaker one.
    """
    choice = os.environ.get("FLEET_SANDBOX_RUNTIME", "auto").strip().lower()
    if choice in {"runc", "runsc"}:
        return choice
    try:
        listed = run(["docker", "info", "--format", "{{json .Runtimes}}"], timeout=20)
        runtimes = json.loads(listed.stdout or b"{}") if listed.returncode == 0 else {}
    except (ValueError, OSError, subprocess.SubprocessError):
        runtimes = {}
    return "runsc" if isinstance(runtimes, dict) and "runsc" in runtimes else "runc"


def work_directory(out: Path):
    """Scratch space for checkouts, inside the evidence folder.

    Not the system temp folder: Docker Desktop shares `/var/folders` with its
    VM, but Colima shares only the home folder, so a checkout under
    `$TMPDIR` cannot be mounted and every run fails before a test starts.
    """
    out.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=".work-", dir=out)


def _execute(state: str, checkout: Path, evidence: Path, image_id: str, paths: list[str],
             timeout: int, runtime: str = "runc") -> dict:
    """One sandboxed pytest run. Infrastructure failures raise; test failures do not."""
    evidence.mkdir(parents=True, exist_ok=True)
    # Colima's file sharing shows host folders to the VM as root-owned 0755.
    # runc does not enforce that on the share, but gVisor checks permissions
    # itself, so the unprivileged test user could not write its JUnit report
    # and every run under gVisor failed as "JUnit report missing". Only this
    # per-run results folder is opened; the code mount stays read-only.
    evidence.chmod(0o777)
    # Never reuse a JUnit file left by a previous attempt.
    (evidence / "junit.xml").unlink(missing_ok=True)
    name = f"fleet-{uuid.uuid4().hex[:16]}"
    # --init reaps test subprocesses (zombies count against the PID ceiling);
    # --memory-swap equal to --memory disables swap, so 2g means 2g on every
    # host rather than 4g where swap exists.
    args = ["docker", "run", "--rm", "--init", f"--runtime={runtime}", "--name", name, "--network=none", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--memory=2g", "--memory-swap=2g", "--cpus=2", "--pids-limit=256",
            "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=512m", "--user", f"{os.getuid()}:{os.getgid()}",
            "--env", "PYTHONPATH=" + ":".join(source_roots(checkout)),
            "--mount", f"type=bind,src={checkout},dst=/workspace",
            "--mount", f"type=bind,src={evidence.resolve()},dst=/results",
            "--workdir", "/workspace", "--entrypoint", "python", image_id,
            "-m", "pytest", "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors",
            "--junitxml=/results/junit.xml", *paths]
    try:
        executed = run(args, timeout=timeout)
    except subprocess.TimeoutExpired:
        run(["docker", "rm", "-f", name], timeout=30)
        raise ValueError(f"{state} timed out")
    (evidence / "run.log").unlink(missing_ok=True)
    (evidence / "run.log").write_bytes(executed.stdout + executed.stderr)
    if executed.returncode == 137:
        # SIGKILL: with no swap and a 2g ceiling this is the OOM killer far
        # more often than anything else. Say so, instead of "JUnit missing".
        raise ValueError(f"{state} was killed (exit 137; likely the memory ceiling)")
    if executed.returncode not in _RESULT_EXIT_CODES:
        raise ValueError(f"{state}: pytest exited {executed.returncode}, which is a harness failure, not a test result")
    return {"state": state, "exit_code": executed.returncode, "tests": junit(evidence / "junit.xml")}


def _apply(checkout: Path, patch: bytes, *, reverse: bool = False) -> None:
    """Apply `patch` to a plain (non-git) checkout, and prove it took effect.

    Inside another repository's work tree `git apply` treats the checkout as
    a subdirectory of that repository, silently skips every path "outside"
    it and still exits 0. Checkouts live in the evidence folder, which can
    sit inside a repository (Fleet's own `.fleet/`), so discovery is stopped
    at the checkout's parent, and a reverse `--check` confirms the change is
    now present rather than trusting the exit code.
    """
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(checkout.resolve().parent)}
    direction = ["-R"] if reverse else []
    applied = run(["git", "apply", "--binary", *direction, "-"], cwd=checkout, input=patch, env=env)
    if applied.returncode:
        raise ValueError("patch does not apply cleanly")
    if patch.strip():
        undo = [] if reverse else ["-R"]
        check = run(["git", "apply", "--binary", "--check", *undo, "-"], cwd=checkout, input=patch, env=env)
        if check.returncode:
            raise ValueError("patch reported success but its changes are not in the checkout")


def probe_hunks(hunks: list[dict], fail_to_pass: list[str], execute) -> dict:
    """Revert one source hunk at a time and ask whether a fail-to-pass test notices.

    A hunk whose reversal leaves every fail-to-pass test passing is a change
    the tests do not check: either it is unrelated to the fix (a tangled
    commit) or the tests are too weak to pin it, which is how a wrong patch
    later passes. Recorded as a label, never a rejection: the audits this
    comes from found such flags right about half the time, so they route a
    task to review rather than delete it. With one hunk the probe is the
    no-op control again and is skipped.
    """
    if len(hunks) < 2:
        return {"status": "not_applicable", "hunks": len(hunks)}
    probed, survived, killed = [], [], []
    for hunk in hunks[:MAX_HUNK_PROBES]:
        label = f"{hunk['path']}#{hunk['index']}"
        try:
            tests = execute(hunk)
        except ValueError as exc:
            probed.append({"hunk": label, "outcome": "unmeasured", "detail": str(exc)[:200]})
            continue
        noticed = sorted(k for k in fail_to_pass if tests.get(k) != "pass")
        outcome = "killed" if noticed else "survived"
        (killed if noticed else survived).append(label)
        probed.append({"hunk": label, "outcome": outcome, "noticed_by": noticed})
    return {"status": "measured", "hunks": len(hunks), "probed": probed, "killed": killed,
            "survived": survived, "not_probed": max(0, len(hunks) - MAX_HUNK_PROBES)}


def validate(candidate: dict, root: Path, out: Path, recipe: dict, *, timeout: int = 600,
             repeats: int = DEFAULT_REPEATS, base_run: bool = False, hunk_probes: bool = False,
             era: dict | None = None, require_issue: bool = True, progress=None) -> dict:
    """The no-op (before) and oracle (after) states, `repeats` times each.

    `require_issue=False` validates commit-only candidates (no linked closed
    issue); the problem statement then has to come from the review stage.

    By default that is two container runs. `base_run` adds a run of the base
    commit with its own tests, which separates tests already failing there;
    `repeats` of 2 or more turns on the flaky-test quarantine; `hunk_probes`
    reverts each source hunk of a passing candidate to see which ones the
    tests notice.

    The environment image belongs to the candidate's dependency `era`
    (`history.eras`), so every candidate whose base declares the same
    dependencies reuses one image. Pass the era computed over the whole
    candidate set; without one, the base commit is its own era.
    """
    out.mkdir(parents=True, exist_ok=True)
    result = {"candidate_id": candidate["id"], "status": "not_verified", "errors": [], "runs": [],
              "labels": [], "validator_version": "2", "measured_at": datetime.now(UTC).isoformat(),
              "base_sha": candidate["base_sha"], "fixed_sha": candidate["fixed_sha"], "recipe": recipe,
              "checks": {"repeats": repeats, "base_run": base_run, "hunk_probes": hunk_probes},
              "meaning": "Reproduced selected checks only; not a complete correctness proof or published task."}
    try:
        if repeats < 1:
            raise ValueError("Each state needs at least one run")
        if require_issue and candidate.get("status") != "ready_for_review":
            raise ValueError("Candidate needs issue/integration enrichment before validation")
        if require_issue and not any(i.get("state") == "closed" and i.get("body") for i in candidate.get("issues", [])):
            raise ValueError("A closed issue with a problem description is required")
        image = recipe.get("image", "")
        install = recipe.get("install_command", "")
        paths = recipe.get("test_paths", [])
        pin = recipe.get("pin_index_date", True)
        if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]+", image):
            raise ValueError("Recipe needs a Docker image reference")
        if not isinstance(install, str) or not install.strip() or len(install) > 4000:
            raise ValueError("Recipe needs a bounded installation command")
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or p.startswith(("-", "/")) or ".." in Path(p.split("::")[0]).parts for p in paths):
            raise ValueError("test_paths must be nonempty repository-relative pytest paths")
        if type(pin) is not bool:
            raise ValueError("pin_index_date must be true or false")
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
        # Resolve dependencies as of the base commit, not as of today: a fresh
        # install of a 2021 commit otherwise pulls 2026 releases, and the
        # failures that causes look like a broken repository. Effective only
        # with pip >= 26 (`--uploaded-prior-to`) or uv (`--exclude-newer`);
        # older pip ignores the variable, so the record says which was asked
        # for, not that it took effect.
        if era is None:
            from fleet.history.eras import assign

            era = assign(root, [candidate["base_sha"]], resolve_python=False)[candidate["base_sha"]]
        if candidate["base_sha"] not in era["commits"]:
            raise ValueError("The supplied era does not contain this candidate's base commit")
        result["era"] = {k: era.get(k) for k in ("key", "representative", "cutoff", "resolution",
                                                 "lockfiles", "python", "python_status")}
        result["era"]["commits"] = len(era["commits"])
        cutoff = era["cutoff"] if pin else None
        result["index_cutoff"] = cutoff
        install_prefix = ["/usr/bin/env", f"UV_EXCLUDE_NEWER={cutoff}", f"PIP_UPLOADED_PRIOR_TO={cutoff}"] if cutoff else []
        # Keyed by the era, not the commit: candidates that declare the same
        # dependencies get the same image. The image is built from the era's
        # representative (newest) commit; each run mounts the candidate's own
        # tree first on PYTHONPATH, so the installed copy never decides which
        # code is under test.
        key = hashlib.sha256(json.dumps([candidate["repo"], era["key"], pinned, install, cutoff, "validator-v3"], sort_keys=True).encode()).hexdigest()[:24]
        tag = f"fleet-validation:{key}"
        runtime = sandbox_runtime()
        result["sandbox_runtime"] = runtime
        with work_directory(out) as tmp:
            work = Path(tmp)
            base = work / "base"
            snapshot(root, candidate["base_sha"], base)
            dockerfile = "\n".join([f"FROM {pinned}", "WORKDIR /workspace", "COPY build/ /workspace/",
                                    "RUN " + json.dumps([*install_prefix, "/bin/sh", "-lc", install]), "ENV HOME=/tmp"]) + "\n"
            (out / "Dockerfile").write_text(dockerfile)
            cached = run(["docker", "image", "inspect", tag], timeout=30)
            result["environment_cache_hit"] = cached.returncode == 0
            if cached.returncode:
                context = work / "context"
                snapshot(root, era["representative"], context / "build")
                (context / "Dockerfile").write_text(dockerfile)
                built = run(["docker", "build", "--tag", tag, str(context)], timeout=900)
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

            def checkout_for(state: str, kinds: tuple[str, ...]) -> Path:
                checkout = work / f"run-{state}"
                shutil.copytree(base, checkout)
                for kind in kinds:
                    try:
                        _apply(checkout, patches[kind])
                    except ValueError:
                        raise ValueError(f"{kind} patch does not apply cleanly to {state}") from None
                return checkout

            # Interleaved, so a slow drift in machine load lands on both sides
            # rather than masquerading as a difference between them.
            states = [("base", ())] if base_run else []
            for index in range(1, repeats + 1):
                states += [(f"before-{index}", ("test",)), (f"after-{index}", ("test", "source"))]
            for state, kinds in states:
                if progress:
                    progress(f"Running {state}")
                result["runs"].append(_execute(state, checkout_for(state, kinds), out / state, built_id, paths, timeout, runtime))
            by_state = {r["state"]: r["tests"] for r in result["runs"]}
            measured = judge(by_state.get("base") if base_run else None,
                             [by_state[f"before-{i}"] for i in range(1, repeats + 1)],
                             [by_state[f"after-{i}"] for i in range(1, repeats + 1)])
            result.update(measured)
            kinds = failure_kinds(out / "before-1" / "junit.xml")
            result["fail_to_pass_kinds"] = {k: kinds.get(k, "exception") for k in measured["fail_to_pass"]}
            for name in ("quarantined", "preexisting_failures", "error_to_pass", "absent_before", "unresolved"):
                if measured[name]:
                    result["labels"].append(name)
            if result["fail_to_pass"] and all(v == "load_error" for v in result["fail_to_pass_kinds"].values()):
                result["labels"].append("fail_to_pass_by_import_only")
            if not result["errors"] and not hunk_probes:
                result["adequacy"] = {"status": "not_run"}
                result["status"] = "verified_checks"
            elif not result["errors"]:
                # Adequacy is only worth paying for on a candidate that passed.
                hunks = split_hunks(patches["source"])

                def execute(hunk: dict) -> dict[str, str]:
                    state = f"hunk-{len(list(work.glob('run-hunk-*'))) + 1}"
                    checkout = checkout_for(state, ("test", "source"))
                    _apply(checkout, hunk["patch"], reverse=True)
                    return _execute(state, checkout, out / state, built_id, paths, timeout, runtime)["tests"]

                if progress and len(hunks) > 1:
                    progress(f"Probing {min(len(hunks), MAX_HUNK_PROBES)} source hunks")
                result["adequacy"] = probe_hunks(hunks, result["fail_to_pass"], execute)
                if result["adequacy"].get("survived"):
                    result["labels"].append("untested_hunks")
                result["status"] = "verified_checks"
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, ET.ParseError, tarfile.TarError) as exc:
        result["errors"].append(str(exc))
    write_json(out / "validation.json", result)
    return result
