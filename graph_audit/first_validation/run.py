"""First end-to-end sandbox runs: before and after the fix, under gVisor.

Usage:
  python run.py --repo OWNER/NAME --clone DIR --analysis ANALYSIS_JSON --out DIR
                --install 'pip install ...' ERA_KEY:COUNT [ERA_KEY:COUNT ...]

Candidates come from the filter audit (`../initial_filter_pytest/run.py`);
only kept changes whose tests changed are run. Commit-only candidates are
allowed (no linked issue required). The Python image follows the era's
declared Python (`--default-python` when it declares none). Results go to
OUT/<sha>/validation.json and a compact OUT/summary.json.
"""
import argparse
import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from fleet.history import eras as era_module
from fleet.history.patch import PathKind, classify_path
from fleet.validation import validate

parser = argparse.ArgumentParser()
parser.add_argument("--repo", required=True)
parser.add_argument("--clone", type=Path, required=True)
parser.add_argument("--analysis", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--install", required=True)
parser.add_argument("--default-python", default="3.7")
parser.add_argument("selection", nargs="+", help="ERA_KEY:COUNT")
args = parser.parse_args()
root, out = args.clone, args.out


def git(*argv: str) -> str:
    return subprocess.run(["git", "-C", str(root), *argv], check=True, capture_output=True, text=True).stdout


rows = json.loads(args.analysis.read_text())["kept"]
by_era = defaultdict(list)
for row in rows:
    if row["test_changed"]:
        by_era[row["era"]].append(row)
chosen = []
for item in args.selection:
    era_key, count = item.split(":")
    chosen += by_era[era_key][: int(count)]

assigned = era_module.assign(root, sorted({r["base_sha"] for r in rows}))
out.mkdir(parents=True, exist_ok=True)
summary_path = out / "summary.json"
summary = json.loads(summary_path.read_text()) if summary_path.is_file() else []
done = {r["sha"] for r in summary}
for row in chosen:
    sha, base = row["sha"], row["base_sha"]
    if sha in done:
        continue
    era = assigned[base]
    python = era.get("python") or args.default_python
    names = git("diff", "--name-only", base, sha).split()
    tests = [p for p in names if classify_path(p) is PathKind.TEST and p.endswith(".py")]
    existing = [p for p in tests if git("ls-tree", "--name-only", base, "--", p).strip()]
    candidate = {"id": sha, "repo": args.repo, "base_sha": base, "fixed_sha": sha,
                 "patch": {"files": [{"path": p} for p in names]}, "status": "commit_only", "issues": []}
    record = {"sha": sha, "subject": row["subject"], "kind": row["kind"], "kind_basis": row["kind_basis"],
              "era": era["key"], "python": python, "test_paths": existing}
    if not existing:
        record.update(status="skipped", reason="only new test files; no existing file to run at base")
    else:
        recipe = {"image": f"python:{python}-slim", "install_command": args.install,
                  "test_paths": existing, "pin_index_date": True}
        started = time.monotonic()
        result = validate(candidate, root, out / sha[:12], recipe, era=era, require_issue=False,
                          progress=lambda m, s=sha: print(f"[{s[:10]}] {m}", flush=True))
        result["seconds"] = round(time.monotonic() - started, 1)
        (out / sha[:12] / "validation.json").write_text(json.dumps(result, indent=2))
        record.update(status=result["status"], errors=result["errors"], seconds=result["seconds"],
                      cache_hit=result.get("environment_cache_hit"),
                      fail_to_pass=len(result.get("fail_to_pass", [])), pass_to_pass=len(result.get("pass_to_pass", [])),
                      pass_to_fail=len(result.get("pass_to_fail", [])), labels=result["labels"],
                      fail_to_pass_kinds=result.get("fail_to_pass_kinds", {}))
    summary.append(record)
    print(json.dumps({k: record.get(k) for k in ("sha", "status", "fail_to_pass", "pass_to_pass", "seconds", "errors")}), flush=True)
    summary_path.write_text(json.dumps(summary, indent=2))
