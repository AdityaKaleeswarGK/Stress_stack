"""Triage sandbox results, then run the review agent on the survivors.

Usage: python run.py --clone DIR --runs VALIDATION_DIR --out DIR --model MODEL [--summary-model MODEL]

Reads OUT/<sha>/validation.json files written by ../first_validation/run.py.
Writes OUT/<model>/<sha>.json per candidate and OUT/<model>/summary.json.
The key comes from OPENROUTER_API_KEY and is never written anywhere.
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from fleet.agent import OpenRouter
from fleet.review import Knowledge, review_candidate, triage
from fleet.workspace import git

parser = argparse.ArgumentParser()
parser.add_argument("--clone", type=Path, required=True)
parser.add_argument("--runs", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--summary-model", help="Model that writes the one-off repository orientation; default --model")
parser.add_argument("--reasoning", default="low", help="low, medium, high, none, or default")
parser.add_argument("--cache", action="store_true", help="Add explicit cache_control breakpoints (Gemini, Anthropic)")
parser.add_argument("--only", nargs="*", help="Candidate SHA prefixes to review")
args = parser.parse_args()

client = OpenRouter(args.model)
client.reasoning = None if args.reasoning == "default" else args.reasoning
knowledge = Knowledge(args.clone, args.out / "knowledge")
head = git(args.clone, "rev-parse", "HEAD").strip()
summary_client = OpenRouter(args.summary_model or args.model)
summary_client.reasoning = client.reasoning
summary = knowledge.summary(head, summary_client)
target = args.out / (args.model.replace("/", "__") + f"--{args.reasoning}" + ("--cache" if args.cache else ""))
target.mkdir(parents=True, exist_ok=True)

rows, routes = [], Counter()
for path in sorted(args.runs.glob("*/validation.json")):
    validation = json.loads(path.read_text())
    sha = validation["candidate_id"]
    if args.only and not any(sha.startswith(p) for p in args.only):
        continue
    route = triage(validation)
    routes[route["route"]] += 1
    row = {"sha": sha, "route": route}
    if route["route"] == "review":
        candidate = {"id": sha, "base_sha": validation["base_sha"], "fixed_sha": validation["fixed_sha"]}
        started = time.monotonic()
        try:
            record = review_candidate(candidate, validation, args.clone, path.parent, client, knowledge, summary, cache=args.cache,
                                      progress=lambda m, s=sha: print(f"  [{s[:10]}] {m}", flush=True))
        except RuntimeError as exc:
            record = {"status": "error", "reason": str(exc)}
        (target / f"{sha[:12]}.json").write_text(json.dumps(record, indent=2))
        verdict = record.get("verdict") or {}
        row.update(status=record["status"], decision=verdict.get("decision"), kind=verdict.get("kind"),
                   failure_cause=verdict.get("failure_cause"), tests_check_whole_fix=verdict.get("tests_check_whole_fix"),
                   required=[i["name"] for i in verdict.get("required_interface", [])],
                   check_failures=len(record.get("check_failures", [])), usage=record.get("usage"),
                   tool_calls=sum(len(s["tools"]) for s in record.get("steps", [])),
                   seconds=round(time.monotonic() - started, 1))
        print(json.dumps({k: row.get(k) for k in ("sha", "status", "decision", "kind", "failure_cause", "tool_calls")}), flush=True)
    rows.append(row)

reviewed = [r for r in rows if r.get("usage")]
totals = {k: sum(r["usage"].get(k, 0) for r in reviewed) for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "cost", "calls")}
report = {"model": args.model, "reasoning": args.reasoning, "cache": args.cache, "routes": dict(routes), "decisions": dict(Counter(r.get("decision") for r in reviewed)),
          "totals": totals, "per_reviewed_candidate": {k: round(v / len(reviewed), 5) for k, v in totals.items()} if reviewed else {},
          "summary_usage": summary.get("usage"), "rows": rows}
(target / "summary.json").write_text(json.dumps(report, indent=2))
print(json.dumps({k: report[k] for k in ("routes", "decisions", "totals", "per_reviewed_candidate")}, indent=2))
