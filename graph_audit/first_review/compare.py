"""Score review runs against the hand reference labels.

Usage: python compare.py reference.json RUN_DIR [RUN_DIR ...]
Each RUN_DIR holds summary.json and <sha>.json files from run.py.
"""
import json
import sys
from pathlib import Path


def matches(expected, actual) -> bool:
    return actual in expected if isinstance(expected, list) else actual == expected


reference = json.loads(Path(sys.argv[1]).read_text())["labels"]
table = {}
for run_dir in map(Path, sys.argv[2:]):
    summary = json.loads((run_dir / "summary.json").read_text())
    scores = {"decision": 0, "kind": 0, "failure_cause": 0, "required_names": 0}
    rows, total = [], 0
    for row in summary["rows"]:
        label = reference.get(row["sha"][:10])
        if not label or row["route"]["route"] != "review":
            continue
        total += 1
        record = json.loads((run_dir / f"{row['sha'][:12]}.json").read_text())
        verdict = record.get("verdict") or {}
        got_required = sorted(i["name"].split(".")[-1].lstrip("-") for i in verdict.get("required_interface", []))
        result = {
            "decision": matches(label["decision"], verdict.get("decision")),
            "kind": matches(label["kind"], verdict.get("kind")),
            "failure_cause": matches(label["failure_cause"], verdict.get("failure_cause")),
            # Names the reference requires must all be present; extra names are allowed.
            "required_names": set(label["required"]) <= set(got_required),
        }
        for key, ok in result.items():
            scores[key] += ok
        rows.append({"sha": row["sha"][:10], **{k: ("ok" if v else "MISS") for k, v in result.items()},
                     "got": {k: verdict.get(k) for k in ("decision", "kind", "failure_cause")}, "required": got_required,
                     "status": record.get("status"), "check_failures": len(record.get("check_failures", [])),
                     "calls": (record.get("usage") or {}).get("calls"), "cost": (record.get("usage") or {}).get("cost"),
                     "seconds": record.get("seconds")})
    table[run_dir.name] = {"candidates": total, "agreement": {k: f"{v}/{total}" for k, v in scores.items()},
                           "totals": summary["totals"], "per_candidate": summary["per_reviewed_candidate"], "rows": rows}
print(json.dumps(table, indent=1))
