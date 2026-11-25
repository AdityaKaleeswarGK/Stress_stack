# First sandbox-to-review run: glom (2026-11-23)

The first end-to-end pass from Fleet's filter to a reviewed task: glom's last 400 mainline changes → hard gates and eras → before/after runs under gVisor → free triage in code → a review agent on the survivors. Five cheap OpenRouter models were compared against hand labels.

Harnesses: `../first_validation/run.py` (sandbox) and `run.py` (review), with scoring in `compare.py`. Labels are in `reference.json`, and each model's compact verdicts in `results/`.

## Sandbox

22 tested candidates from six eras (Python 3.7 to 3.14), one before run and one after run each, under gVisor.

| Result | Count |
|---|---|
| Verified (some test fails before and passes after; nothing breaks) | 11 |
| Every test absent before (the test file imports a name the fix adds) | 5 |
| Refused: the patch changes dependency files | 3 |
| No fail-to-pass test (all three checked by hand; genuine) | 3 |

- **Wall time:** 4.4 minutes in all. Five era-image builds took 3.6 minutes, and the 14 runs on already-built images took 30 seconds together, about 2 s each.
- **Two validator bugs found and fixed.** Either one invalidated every gVisor result:
  1. Colima shows shared folders as root-owned, and gVisor enforces that, so tests could not write their JUnit report. Each run's results folder is now opened for writing; the code mount stays read-only.
  2. The checkouts sat inside the stress_stack repository, and from there `git apply` silently skipped every `diff --git` path, still exiting 0. Before and after both ran the base code. Repository discovery is now stopped at the checkout, and a reverse `--check` proves the patch landed. There is a regression test.
- **Found, not fixed:**
  - "Handle Python 3.11" ran on its era's declared Python 3.10, where the bug cannot occur.
  - The five "absent before" candidates are exactly the missing-interface feature tasks. The judge does not yet count them as fail-to-pass.
  - pytest's own repository cannot run yet: its generated `_version.py` is missing from the checkout.

## Review agent

`fleet/src/fleet/review.py`. The agent's inputs:
- the repository orientation, written once;
- a packet built by code: the commit message, the diffs, only the fail-to-pass tests with their `E` error lines, the names the fix creates and which of them the tests use, and the layout at the base commit;
- capped read-only tools over both commits.

Its verdict is checked by code (leaked test names or files, pasted fix code, required names that are not new or not used by the tests). A failed check goes back to the agent, at most twice.

### Agreement with the hand labels

The labels were written by Claude from the diff and the sandbox errors, and have not yet been checked by a person. A list in `reference.json` means either answer counts.

| Model (setting) | Reviewed | Keep/drop | Kind | Failure cause | Required names | Cost per candidate | Calls per candidate |
|---|---|---|---|---|---|---|---|
| **openai/gpt-6-luna** (reasoning low, cache) | 11 | 11/11 | 10/11 | 11/11 | 11/11 | **$0.0005** | 1.3 |
| qwen/qwen3.7-flash (reasoning off) | 5 | 5/5 | 4/5 | 4/5 | 5/5 | $0.0007 | 4.4 |
| google/gemini-2.5-flash-lite (low, cache) | 11 | 11/11 | 7/11 | 10/11 | 11/11 | $0.0016 | 2.1 |
| google/gemini-2.5-flash-lite (low) | 7 | 7/7 | 5/7 | 6/7 | 7/7 | $0.0020 | 2.7 |
| deepseek/deepseek-v4.1-flash (low) | 6 | 6/6 | 6/6 | 5/6 | 6/6 | $0.0086 | 4.7 |

The Qwen, DeepSeek and Gemini (no cache) runs were stopped early. Each also reviewed the rename candidate `efddd66371` separately.

### What the comparison shows

- **The hard case decided it.** In `efddd66371` "rename Literal to Val", `Literal` and `Val` were two classes, and the path evaluator unwrapped only `Literal`, so `Val(T)` inside an `S[...]` path raised `PathAccessError`. The reference first labelled this a mechanical rename (drop); reading the base code corrected that to a real bug (keep, if the statement describes the failure without dictating the merge).
  - GPT-6 Luna found the bug from the packet alone, in one call.
  - DeepSeek found it after 11 tool calls.
  - Gemini and Qwen went straight to `finish` without exploring. Their statements list the edits ("an alias `Literal = Val` should be added at the end of core.py"), and Qwen's includes a false claim. Both ended `checks_failed`, so neither would be accepted automatically.
- **Exploration helps only a weaker model.** The packet already holds enough to understand these changes. Luna used it; Gemini and Qwen did not, and they did not choose to explore either.
- **Gemini 2.5 Flash Lite mislabels features as bug fixes** (4 of 11).
- **Hidden reasoning is most of DeepSeek's cost:** about 2,900 of its 4,250 output tokens per candidate.
- **Explicit cache markers made little difference** for Gemini at 2–3 calls per conversation. Cost and input per call were about the same, because the cache write is charged too. OpenAI and DeepSeek cache repeated prompt starts on their own.

### Limits

- 11 candidates from one small repository, with one hand labeller. Every verified candidate ended up keepable, so drop decisions are barely tested.
- Statements have been read, not tested. A statement that an engineer could solve from has not been confirmed by running a solver.
- **About 80 seconds of every call is spent inside Fleet's own HTTP client**: a tiny request takes 1.3 s with `curl` and over two minutes through `fleet.agent.OpenRouter`. So the run times here (1–11 minutes per candidate) are not the models' speed.

## Fixes this run points to

1. **The name check compares names across a whole file,** so a new `Partial.__repr__` looks old because other classes have one. Compare per class. Also strip leading dashes, so `--scalar` matches `scalar`.
2. **The leak check sees only test names, test files and pasted code lines.** Add:
   - changed source file names;
   - edit language ("should be added", "renamed to", "the fix");
   - mentions of tests ("tests are failing");
   - literal test inputs and expected outputs copied into the statement.
3. **Do not accept a retried verdict from an agent that never looked:** after a failed check, require at least one tool call.
4. **Find and remove the client's fixed ~80 s delay per call.**
5. **Default model:** GPT-6 Luna. Consider escalating to a second model when a verdict fails its checks.
