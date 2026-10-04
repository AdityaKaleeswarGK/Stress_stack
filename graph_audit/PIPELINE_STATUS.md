# Pipeline status — 2026-10-05

## Implemented

1. Historical PR/commit discovery with reconstructible base/fixed SHA pairs, configurable history/candidate limits, source/test/docs/config classification, diff statistics, and Python AST checks for comment/docstring-only edits. Source-only fixes can qualify; there is no default 400-line ceiling.
2. Deterministic Python selection from declared version pins and packaging/tox constraints. Ambiguous or conflicting metadata requires environment review.
3. Local Docker sandbox validation of base and fixed snapshots, identical regression probes, fail-to-pass and pass-to-pass transitions, regressions, collection failures, and source integrity.
4. Runtime reviewer with historical file/diff inspection, agent-authored pytest probes, repeated paired runs and saved evidence. Pilots use OpenRouter `openai/gpt-6-luna-pro`, with two concurrent reviewers and two containers, bounded calls, experiments and costs.

New tests passing on both versions can provide coverage/preservation evidence. A behavioral bug-fix task needs a meaningful discriminator. Reviewer decisions are provisional; no automatic training-ready certification is implemented.

## Recorded experiments

- Pluggy: 100 historical changes yielded nine initial candidates. A settings-file classification correction reduced this to eight. The first paired review supported #646, #632 and #617; #616 showed a repeated regression. The runtime reviewer subsequently demonstrated a repeated discriminator for the source-only #590 change. The 50-candidate ceiling did not truncate this sample.
- Glom: 21 shortlisted candidates; **15 reviewed and six incomplete**. The batch recorded 99 model calls and $0.23053543 reported API cost. Conservative reservations reached $0.9992208 of a $1 ceiling and stopped the remaining reviews. Reservation reconciliation is not implemented. The 15 reviews still need independent semantic auditing; their classifications are not a final accepted corpus.
- Local execution uses Docker Desktop/runc, not gVisor or Firecracker. Recorded dependency images are controller-managed and are not a full historical lockfile replay.

## Remaining work

- Finish the six Glom reviews and independently audit agent assertions, generated probes and task statements.
- Reconcile completed API-call costs with reserved budgets.
- Verify authoritative closed-issue-to-PR relationships; commit-message hints and closing claims alone do not establish that relationship.
- Generalize environment/image preparation and runtime recipes across repositories, then integrate the experiment stages into a unified CLI workflow.
- Package validated problem statements, reference patches and test oracles into a versioned corpus/export format.

## Repository contents

`fleet/` contains reusable implementation and tests. The experiment directories contain runnable scripts, prompts, compact input/summary records and reports. Scripts currently depend on locally generated snapshots, baseline results, images and clone paths; this is an experimental harness, not a fresh-clone turnkey workflow. Raw logs, copied third-party repositories and per-run snapshots remain local and are excluded from this code commit. Reports describe the completed runs and their limits.
