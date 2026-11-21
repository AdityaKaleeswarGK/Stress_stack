"""Run fleet's hard gates and signals over a repository's recent mainline."""
import json, sys, time
from collections import Counter
from pathlib import Path
from fleet.history.filtering import filter_history
from fleet.history.window import GitRepo
from fleet.history import signals

repo, root, out, window = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
clone = GitRepo.at(root)
started = time.monotonic()
report = filter_history(repo, clone, ref="HEAD", max_commits=window, max_changes=window, limit=window)
gated = time.monotonic() - started
links = signals.history_links(root)
kept = []
for c in report["candidates"]:
    row = {"sha": c["sha"], "base_sha": c["base_sha"], "subject": c["subject"], "pr": c.get("pr_number"),
           **signals.classify(c, c["message"], root, links)}
    row["source_analysis"] = [f["kind"] for f in c.get("source_analysis", [])]
    kept.append(row)
rejected = []
for r in report["rejected"]:
    row = {"sha": r["sha"], "subject": r["subject"], "pr": r.get("pr_number"), "reason": r["reason"],
           "source_analysis": [f["kind"] for f in r.get("source_analysis") or []]}
    if r.get("patch"):
        record = {"sha": r["sha"], "base_sha": r["base_sha"],
                  "files": [{**f, "deleted": f.get("removed", 0)} for f in r["patch"]["files"]]}
        row.update(signals.classify(record, clone.commit_message(r["sha"]), root, links))
        row["paths"] = [f["path"] for f in r["patch"]["files"]]
    rejected.append(row)
# How well does the commit message alone predict the project's own label?
# The project's own label: a typed changelog fragment or a versioned release note.
LABELLED = {"changelog_fragment", "patch_release_notes", "minor_release_notes"}
truth = [r for r in kept + rejected if r.get("kind_basis") in LABELLED]
agreement = Counter((r["kind"], r["message"]["kind"]) for r in truth)
# Which environment image each kept change's base needs.
from fleet.history import eras as era_module
started_eras = time.monotonic()
assigned = era_module.assign(root, [row["base_sha"] for row in kept])
for row in kept:
    row["era"] = assigned[row["base_sha"]]["key"]
distinct = {era["key"]: era for era in assigned.values()}
era_report = {"summary": era_module.summarise(assigned), "seconds": round(time.monotonic() - started_eras, 1),
              "eras": sorted(distinct.values(), key=lambda e: e["last_date"])}
result = {"repo": repo, "summary": report["summary"], "rejection_reasons": report["rejection_reasons"],
          "eras": era_report,
          "seconds": {"gates": round(gated, 1), "total": round(time.monotonic() - started, 1)},
          "kept_bins": signals.bins(kept),
          "message_vs_changelog": {f"{a} <- msg {b}": n for (a, b), n in agreement.most_common()},
          "kept": kept, "rejected": rejected}
out.write_text(json.dumps(result, indent=2))
print(json.dumps({k: result[k] for k in ("summary", "rejection_reasons", "seconds", "kept_bins", "message_vs_changelog")}
                 | {"eras": era_report["summary"]}, indent=2))
