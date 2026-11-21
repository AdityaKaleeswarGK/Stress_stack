# Cross-domain "generate many, filter cheaply, verify expensively" pipelines: transferable techniques for Fleet (as of November 2026)

Reading notes for the report writer:
- Research date 2026-11-15. Most primary results in these fields date from 2016-2025; each finding carries its date. I found very little genuinely new 2026 primary material on these topics; that is flagged in Gaps.
- Most numbers come from search-result excerpts of the cited primary papers, not full-text reads. Where a number was only seen in a secondary source it is marked "(secondary)". Where a claim is from my background knowledge and was not confirmed this session it is placed under Gaps or Inferences, not Cited Findings.
- "Fleet mapping" bullets under Inferences are my own proposals, not sourced claims.

## 1. Software testing and CI at scale (Google, Meta, Microsoft): test selection, flakiness, quarantine, bisection

### Takeaway
The big CI systems all converge on the same shape: a cheap learned or historical prior decides which expensive executions to run, reruns are used to separate flaky from real signal (with a fixed N, usually 5), flaky items are quarantined rather than deleted, and culprit search is treated as a noisy (Bayesian) search rather than plain bisection. The single most transferable statistic is how skewed outcomes are: at Google only about 63K of 5.5M affected tests ever failed, so uniform scheduling wastes almost all compute.

### Cited Findings

Google TAP and test scheduling
- (2017, ICSE-SEIP) Memon et al., "Taming Google-Scale Continuous Testing": over the studied period, of 5.5 million affected tests only 63K ever failed; the rest never failed once. TAP treated all 5.5M identically in execution frequency, and the authors argue resources could be saved by running rarely-failing tests less often. — [Google Research](https://research.google/pubs/taming-google-scale-continuous-testing/); [full PDF](https://web.eecs.umich.edu/~xwangsd/courses/w23/readings/googletest.pdf)
- (2017) Same paper: tests that fail tend to sit "closer" (in the dependency graph) to the code changed; frequently modified code, certain users/tools, and code recently modified by more than three developers break more often. On an average day TAP integrated 13K+ projects, with 800K builds and 150M test runs. — [full PDF](https://web.eecs.umich.edu/~xwangsd/courses/w23/readings/googletest.pdf)

Google flakiness
- (April 2017) Jeff Listfield, Google Testing Blog: over one week 0.5% of small tests, 1.6% of medium tests and 14% of large tests were flaky; overall about 1.65%. Larger (more resource-heavy, more integrated) tests are much more flaky. Size labels were engineer-selected. — [Google Testing Blog, Listfield posts](https://testing.googleblog.com/search/label/Jeff%20Listfield?m=0)
- (May 2016) John Micco, Google Testing Blog: commonly cited as about 1.5% of all test runs flaky and almost 16% of tests showing some flakiness. I saw these only via secondary summaries this session, not the original text. — [Google Testing Blog, Micco posts](https://testing.googleblog.com/search/label/John%20Micco) (secondary confirmation only)

Google culprit finding (bisection under flakiness)
- (2023, ICST Industry) Henderson et al., "Flake Aware Culprit Finding": replaces deflaked bisection with Bayesian inference plus noisy binary search, using prior information on which change is most likely the culprit. Evaluated on 13,000+ real test breakages at Google. Two stated weaknesses of the baseline: plain bisection ignores flakiness, and with k-reruns for deflaking, accuracy plateaus while build cost keeps rising linearly. With good priors it can identify the culprit in a logarithmic number of builds in the best case. — [Google Research](https://research.google/pubs/flake-aware-culprit-finding/); [PDF](https://hackthology.com/pdfs/icst-2023.pdf)

Google mutation testing at code-review time
- (2021, IEEE TSE) Petrović, Ivanković, Fraser, Just, "Practical Mutation Testing at Scale": context is a ~2 billion LOC monorepo with 500M+ test executions per day gating ~60,000 changes. Mutation is triggered when a changelist is sent for review; the service first computes coverage for the changelist and only mutates changed, covered code. Almost 17 million mutants were generated, of which about 2 million were surfaced to reviewers (i.e. roughly 88% of generated mutants are suppressed or never shown). — [arXiv 2102.11378](https://arxiv.org/pdf/2102.11378v1); [Google Research](https://research.google/pubs/practical-mutation-testing-at-scale-a-view-from-google/)

Meta predictive test selection
- (2019, ICSE-SEIP; arXiv Oct 2018) Machalica et al., "Predictive Test Selection": a learned model over (change, test) history selects tests to run; it catches over 95% of individual test failures and over 99.9% of faulty code changes; had been in production for several months. — [arXiv 1810.05286](https://arxiv.org/pdf/1810.05286.pdf)
- The frequently quoted "runs only a third of the tests / halves infrastructure cost" figure appeared only in an AI-generated secondary summary this session; treat as unverified. A 2025 TU Wien thesis replicates the study outside Meta's environment (results not read). — [TU Wien replication thesis](https://repositum.tuwien.at/bitstream/20.500.12708/215633/1/Aichmann%20Stefan%20-%202025%20-%20Predictive%20Test%20Selection%20A%20Replication%20Study.pdf)

Meta TestGen-LLM (Assured LLM-based software engineering)
- (Feb 2024, FSE '24) Alshahwan et al.: LLM-generated test candidates pass through "progressively demanding semantic filters": must build, must pass repeatedly (5 runs, to exclude flaky tests), must increase coverage. Only survivors are shown to engineers, so the tool gives a guarantee of non-regression. — [arXiv 2402.09171](https://arxiv.org/abs/2402.09171)
- Funnel numbers as relayed by a secondary source: 75% of candidate test cases built, 57% passed reliably, 25% increased coverage; 11.5% of classes it was applied to were improved; 73% of recommendations accepted by engineers in Instagram/Facebook test-a-thons. — [freeCodeCamp summary](https://www.freecodecamp.org/news/automated-unit-testing-with-testgen-llm-and-cover-agent/) (secondary; paper is [arXiv 2402.09171](https://arxiv.org/abs/2402.09171))

Meta ACH (mutation-guided LLM test generation)
- (Jan 2025, FSE Companion '25) Foster et al.: LLM generates realistic faults (mutants) for a concern (privacy), then generates tests that kill them. Across 7 platforms and 10,795 classes, ACH generated 9,095 mutants and 571 privacy-hardening tests (arXiv v1 abstract reported 4,660 mutants; counts differ between versions). Engineers accepted 73% of tests; 36% judged privacy-relevant. — [arXiv 2501.12862](https://arxiv.org/pdf/2501.12862)
- 277 of the 571 tests would have been discarded under a line-coverage-only adequacy criterion (they add no new line coverage but do kill a mutant). — [arXiv 2501.12862](https://arxiv.org/pdf/2501.12862)
- LLM-based equivalent-mutant detector: precision 0.79 / recall 0.47 alone; 0.95 / 0.96 after simple static preprocessing. A cheap deterministic normaliser in front of the LLM judge more than doubled recall. — [arXiv 2501.12862](https://arxiv.org/pdf/2501.12862)

Microsoft flaky test management
- (Feb 2022) Microsoft engineering blog: the "Flakes" system in CloudBuild/CloudTest had identified ~49K flaky tests, turned 160K sessions that would have failed into passes, and was used by 100+ product teams. Detection is retry-based (CloudBuild retries a failed test once by default). — [Engineering@Microsoft](https://devblogs.microsoft.com/engineering-at-microsoft/improving-developer-productivity-via-flaky-test-management/)
- Quarantine semantics: quarantined tests are still always run, only their result is suppressed; a bug is filed and when the bug is closed the test leaves quarantine automatically and failures block again. Teams can add policy, e.g. block PRs for developers with more than 10 open flaky-test bugs. — [Engineering@Microsoft](https://devblogs.microsoft.com/engineering-at-microsoft/improving-developer-productivity-via-flaky-test-management/)
- (2020, ISSTA) Lam et al., "A Study on the Lifecycle of Flaky Tests": six large proprietary Microsoft projects; asynchronous calls were the leading root cause. — [Lam et al. PDF](https://cs.gmu.edu/~winglam/publications/2020/LamETAL20FaTB.pdf)

### Inferences (Fleet mapping)
- Skew-aware scheduling (Memon 2017): expect most candidate commits and most repositories to yield nothing. Track per-repository and per-rule yield, and stop treating all candidates equally. A repo whose first N validated candidates all fail environment build should be demoted, exactly as TAP wanted to demote never-failing tests.
- Predictive selection (Meta PTS): train a small gradient-boosted model on Fleet's own history, features = commit metadata (files touched, test files touched, diff size, repo, language, CI config present, author history), label = "survived fail-before/pass-after validation". Use it to order the container queue. PTS shows such a model can keep >95% of the signal; the right Fleet metric is "fraction of eventually-valid tasks retained at X% of container budget". Keep a small random exploration slice so the model's blind spots stay measurable.
- Proximity prior (Memon 2017): tests that are close to the changed code are the ones that fail. For a candidate commit, run only tests in or near the files touched (or the tests modified in the commit) for the cheap fail-before check, and the whole suite only for survivors.
- Flake classification by fixed-N reruns: Meta TestGen-LLM, BugSwarm and GitBug-Java all use 5 runs. Fleet should rerun both the pre-fix (fail) and post-fix (pass) side, and label candidates reproducible / flaky / unreproducible rather than pass/fail.
- Resource-heaviness predicts flakiness (Listfield 2017: 0.5% small vs 14% large). Use test duration, network use, and container memory as features in a flakiness prior; give "large" tests more reruns and "small" tests fewer (adaptive rerun count instead of a constant 5).
- Quarantine instead of delete (Microsoft): keep flaky candidates in the catalogue, flagged, and re-run them periodically; auto-release when they stabilise (e.g. after an environment pin fix). This preserves yield data and avoids silently losing repos.
- Noisy bisection with priors (Google FACF): if Fleet ever needs to locate the fixing or introducing commit across a range (e.g. squashed PRs, or finding the earliest commit where the test fails), use a Bayesian posterior over commits with priors from diff/test overlap, and choose the next build to maximise information, rather than k-rerun bisection whose cost grows linearly while accuracy plateaus.
- Coverage-first then mutate (Google mutation testing): do the cheap check (does any test cover the changed lines?) before the expensive one. For Fleet: reject candidates where the fix diff is not covered by the fail-to-pass tests before spending LLM review on them.
- Mutation as a test-strength check (ACH): 277/571 useful tests added no line coverage. Coverage alone under-measures test value. Fleet can validate a task's tests by injecting a few LLM- or rule-generated mutants of the fix and checking the task's tests kill them; tasks whose tests kill nothing but the original bug are weakly specified.
- Deterministic pre-processing before an LLM judge (ACH equivalent-mutant result): strip comments/whitespace/renames and apply syntactic identity checks before asking an LLM "is this diff behaviour-changing / is this test meaningful". The ACH numbers suggest this is where most judge recall comes from.
- Staged semantic filters with a stated guarantee (TestGen-LLM): publish Fleet's funnel in the same form (built %, passed reliably %, added signal %) and define the guarantee each stage gives; it makes regressions in any stage visible.

### Gaps
- Sapienz (Meta's search-based mobile testing): I ran no successful search on it this session, so no numbers are reported. From background knowledge only: it is multi-objective search-based test generation for Android deployed at Facebook around 2018, with a reported majority of its crash reports resulting in fixes; this needs verification before use.
- Original text of the 2016 Micco post (16% / 1.5%) and exact accuracy numbers from the FACF paper were not read in full.
- The PTS "select fewer than a third of tests / halve infrastructure cost" figure is unverified here.
- No 2026-dated primary publications from Google/Meta/Microsoft on these topics were surfaced; the newest primary item is ACH (Jan 2025).
- No Microsoft-published numbers on flaky share of failures were verified (third-party blogs cite 13% and 4.6%, which conflict).

## 2. Fuzzing and program analysis: prioritising and deduplicating candidates

### Takeaway
Fuzzing treats "which input to spend time on next" as an explicit explore/exploit problem and has measured large gains from doing so (EcoFuzz: 214% of AFL's path coverage with 32% fewer executions). It also has the most mature post-discovery pipeline: signature-based deduplication, automatic minimisation, and automatic bisection to a regression range, all run before a human sees anything.

### Cited Findings

Seed scheduling as a bandit
- (2016, CCS) AFLFast models seed-to-path transitions as a Markov chain and assigns more energy to seeds exercising low-frequency paths; AFL's default constant schedule over-invests in high-frequency paths. Author-reported: exposed 6 vulnerabilities and 2 bugs about 7x faster than AFL in 24h runs, plus 3 vulnerabilities and 1 bug AFL did not find. — [AFL++ power schedules doc](https://aflplus.plus/docs/power_schedules/); [author post on afl-users](https://groups.google.com/d/msg/afl-users/fOPeb62FZUg/LYxgPYheDwAJ)
- (2020, USENIX Security) EcoFuzz models power scheduling as a variant of the adversarial multi-armed bandit with an adaptive average-cost-based schedule: 214% of AFL's path coverage with 32% fewer generated test cases; 12 vulnerabilities found; compared with six AFL-family tools on 14 subjects over 490 CPU-days. Self-reported, no independent replication found. — [USENIX Security 2020](https://www.usenix.org/conference/usenixsecurity20/presentation/yue)
- Practical caveat: a user reported AFLFast performing poorly in parallel mode because path-probability estimates are wrong for imported seeds, i.e. bandit statistics break when shared across workers naively. — [afl-users thread](https://groups.google.com/d/msg/afl-users/fOPeb62FZUg/LYxgPYheDwAJ)

ClusterFuzz / OSS-Fuzz post-processing
- ClusterFuzz deduplicates by "crash state", a signature generated from the stack trace; a crash is "reliably reproducible" only if the target consistently crashes with the same crash state on the given input. Testcase minimisation and regression-range finding by bisection are core automated tasks; the regression range is reported as x:y (inclusive start, exclusive end revision) on the crash report. — [ClusterFuzz glossary](https://google.github.io/clusterfuzz/reference/glossary); [OSS-Fuzz: ClusterFuzz](https://google.github.io/oss-fuzz/further-reading/clusterfuzz)

LLM-assisted fuzzing
- (Nov 2024) Google OSS-Fuzz: AI-generated and AI-enhanced fuzz targets found 26 new vulnerabilities, including CVE-2024-9143 in OpenSSL (present for roughly two decades per press coverage), and added 370,000+ lines of new coverage across 272 C/C++ projects. — [Google Security Blog](https://security.googleblog.com/2024/11/leveling-up-fuzzing-finding-more.html)
- oss-fuzz-gen README lists 30 bugs reachable only through newly generated targets (snapshot; may be higher now). The framework is an LLM-generates / compile / run / fix-errors / measure-coverage loop. — [google/oss-fuzz-gen](https://github.com/google/oss-fuzz-gen)

Compiler testing
- (2011, PLDI; later thesis) Csmith, a random C program generator that avoids undefined behaviour by construction and uses differential testing across compilers: over 4.25 years of testing it found over 450 bugs in GCC and LLVM. — [Utah thesis record](https://collections.lib.utah.edu/ark:/87278/s6tj1vzr); [Csmith PLDI'11 preprint](https://www-old.cs.utah.edu/~regehr/papers/pldi11-preprint.pdf)
- (2014, PLDI) EMI / Orion: profile a seed program on an input, randomly delete statements that were not executed, and require the compiled variant to behave identically on that input. Orion: 195 bugs reported (111 GCC, 84 LLVM), 147 confirmed, 110 fixed, mostly miscompilations; all but four came from EMI variants of Csmith-generated seeds. The wider EMI project reports 1,602 GCC/LLVM bugs, 1,007 fixed. — [EMI project page](https://web.cs.ucdavis.edu/~su/emi-project/); [EMI paper](https://www.cs.ucdavis.edu/~su/publications/emi.pdf); [Regehr blog](https://blog.regehr.org/?p=1161)
- (2015, OOPSLA) Athena (guided EMI mutation via MCMC): 72 new GCC/LLVM bugs, all confirmed, 68 fixed. — [Athena paper](https://www.cs.ucdavis.edu/~su/publications/oopsla15-compiler.pdf)

### Inferences (Fleet mapping)
- Repository and rule scheduling as a bandit: treat each repository (or repo x filter-rule bucket) as an arm, reward = validated tasks per container-minute, and allocate the next batch of container runs by Thompson sampling or an EcoFuzz-style adaptive schedule. This directly addresses scaling to thousands of repositories where yield is unknown up front. Heed the AFLFast parallel caveat: keep arm statistics in a single shared store, not per worker.
- Favour rare "paths": AFLFast's insight is that over-sampling the common path is the waste. For Fleet, down-weight candidates that look like the already-abundant task type (e.g. one-line fix plus one test in a top-10 repo) and up-weight under-represented categories; define "coverage" as cells in a (repo, language, task-type, difficulty) grid and reward candidates that fill empty cells.
- Corpus minimisation analogue: among near-duplicate candidates (same failing tests, overlapping diffs, cherry-picks/backports across branches), keep only the cheapest representative before spending container time.
- Crash-state analogue for dedup and reproducibility: define a "failure signature" per candidate = sorted set of fail-to-pass test IDs plus normalised top frames or assertion message. Use it (a) to deduplicate candidates across forks/backports, and (b) as the reproducibility criterion: a candidate is reliable only if the same signature recurs across reruns, not merely "something failed".
- Automatic minimisation before review: delta-debug the task to its minimal form (smallest set of tests that flip, smallest fix hunk set that makes them pass). This detects tangled commits mechanically: hunks removable without changing the fail-to-pass set are unrelated changes.
- Differential/metamorphic oracle (Csmith, EMI): validate environments and tasks without a ground truth. Examples: run fail-before/pass-after in two independent container builds (or two Python minor versions) and require agreement; EMI-style, delete code the fail-to-pass tests never execute from the fix and require the tests still pass, which measures how much of the "gold patch" the tests actually constrain.
- LLM-in-the-loop harness generation (oss-fuzz-gen): the generate / build / read error / repair loop is the same loop Fleet needs for environment setup (Dockerfile and test command synthesis). The OSS-Fuzz results show value concentrated in targets no human had written, which argues for pointing Fleet's environment-synthesis effort at repos without working CI configs, where competition and contamination are lowest.

### Gaps
- No C-Reduce or delta-debugging statistics (reduction ratios, time) were retrieved this session. From background knowledge only (unverified): C-Reduce (Regehr et al., PLDI 2012) and ddmin (Zeller and Hildebrandt, 2002) are the standard references; the key transferable caveat is that a reducer needs a validity check so the reduced case still fails for the same reason, not a different one.
- No OSS-Fuzz totals for 2025-2026 were found; the 26/30 figures are from 2024-era reporting.
- No retrieved source on MOpt, Entropic, or libFuzzer's corpus-merge behaviour; not covered.
- EcoFuzz and AFLFast gains are self-reported; no independent head-to-head replication found.

## 3. Defects mining research: Defects4J, BugsInPy, BugSwarm, GitBug-Java, SZZ

### Takeaway
Every credible bug dataset uses the same validation predicate Fleet uses (a test fails before and passes after the fix), plus two additions Fleet should copy: an "isolated fix" requirement and N-times repeated execution. The documented pitfalls are quantified: prior benchmarks reproduce at only 26.6%-96.9%, 62.6% of BugSwarm artifacts broke at least once over time, only 3.6% of BugSwarm was judged usable for repair research, and SZZ and issue labels are both noisy enough that they cannot be used as ground truth.

### Cited Findings

Defects4J and BugsInPy
- Defects4J (Just et al., ISSTA 2014): every bug ships with a test suite containing at least one test that exposes it; initial release 357 bugs from 5 projects, current release cited as 854 bugs from 17 projects (other papers cite 835/845 for subsets). — [ISSTA 2014 record](https://www.springerprofessional.de/doi/10.1145/2610384.2628055); counts as cited in [arXiv 2506.03283](https://arxiv.org/pdf/2506.03283)
- BugsInPy (Widyasari et al., ESEC/FSE 2020): 493 bugs from 17 Python projects (one source says 501). Inclusion criteria: (1) at least one test fails on the faulty version and passes on the fixed one; (2) isolated fix, i.e. the commit contains only the changes needed for the fix; (3) the fix changes source code, not only config/build scripts. Each bug stores buggy and fixed revisions, the patch and the exposing tests. Estimated 831 person-hours to build. — [ESEC/FSE 2020 page](https://2020.esec-fse.org/details/esecfse-2020-tool-demos/26/BugsInPy-A-Database-of-Existing-Bugs-in-Python-Programs-to-Enable-Controlled-Testing); summary in [arXiv 2305.19834](https://arxiv.org/pdf/2305.19834)

BugSwarm
- (2019, ICSE) BugSwarm mines fail-pass build pairs from Travis CI automatically and packages each as a Docker image; a pair is labelled "reproducible" if it reproduces 5 out of 5 times, "flaky" if only sometimes, otherwise unreproducible. — [BugSwarm paper](https://arxiv.org/pdf/1903.06725); [authors' note](https://arxiv.org/pdf/1910.13058)
- (2019) Durieux and Abreu critical review: only 112 of 3,091 artifacts (3.6%) were suitable for evaluating fault localisation or program repair (the rest fail for non-code reasons, have huge or non-source diffs, are duplicates, etc.). — [Critical Review of BugSwarm](https://arxiv.org/pdf/1905.09375)
- (2019) Authors' rebuttal: at that version 2,949 artifacts were fully reproducible and 142 flaky; flakiness mainly due to test flakiness. The rebuttal defends counting, not the suitability criticism. — [A Note About the Critical Review](https://arxiv.org/pdf/1910.13058)
- (later UC Davis dissertation) 62.6% of BugSwarm artifacts break at least once over time (dependency rot); dependency caching and artifact isolation preserve reproducibility at about 95%, matching hand-curated datasets. — [UC Davis dissertation](https://escholarship.org/uc/item/3sv6k634)

GitBug-Java / GitBug-Actions
- (2024, MSR) GitBug-Java: 199 bugs from the 2023 commit history (Jan-Oct 2023) of 55 notable open-source repositories, chosen to post-date LLM training cut-offs (most Defects4J bugs are 10-15 years old). Built with GitBug-Actions, which replays the repository's own GitHub Actions workflows locally. A fix is retained only if it produces exactly the same test results across all five executions. All environments run fully offline. Cites a prior study finding existing bug-fix benchmarks reproduce at between 26.6% and 96.9%. — [arXiv 2402.02961](https://arxiv.org/pdf/2402.02961)

SZZ and label noise
- (2021, ICSE) Rosa et al., "Evaluating SZZ Implementations Through a Developer-Informed Oracle": prior SZZ evaluations rely on researcher judgement; they build an oracle from fix commits whose messages explicitly name the bug-introducing commit, and compare SZZ variants against it. — [arXiv 2102.03300](https://arxiv.org/abs/2102.03300)
- (2019, IEEE TSE) Re-evaluation of SZZ on Defects4J: a median of 44% of the lines flagged by an improved SZZ are very likely bug-introducing; refactorings (31.17%) and equivalent changes (13.64%) are still misidentified; SZZ is roughly 40% more accurate if only valid bug-fix lines are given as input. — [IEEE Xplore](https://ieeexplore.ieee.org/document/8870178/metrics)
- (2021) Herbold et al. confirm Herzig et al. (2013): for every issue correctly labelled as a bug there are 0.74 mislabelled bug issues. — [Göttingen research online](https://publications.goettingen-research-online.de/bitstream/2/98985/1/document.pdf)

### Inferences (Fleet mapping)
- Adopt "isolated fix" as an explicit, measured property, not a binary gate: record what fraction of fix hunks is necessary for the fail-to-pass flip (via hunk-level delta debugging). The BugSwarm critique (3.6% usable) shows that "fail then pass" alone admits build-config fixes, dependency bumps and mass refactors.
- Exclude non-source fixes at the static stage (BugsInPy criterion 3): a commit whose fail-to-pass flip is explained by CI config, lockfile or build-script changes is not a coding task.
- Five identical executions on both sides (BugSwarm, GitBug-Java) is the de facto standard; require identical per-test results, not just an identical exit code.
- Offline, cached environments are the main defence against rot: 62.6% of BugSwarm artifacts broke at least once, and caching restored about 95%. Fleet should vendor dependencies into the image at validation time, validate with the network disabled, and re-validate the catalogue on a schedule to measure decay.
- Reuse the repository's own CI definition (GitBug-Actions) as the environment recipe before falling back to LLM-synthesised Dockerfiles; it is cheaper and reflects what maintainers actually run.
- Recency filtering by commit date (GitBug-Java) is a cheap static rule that also limits training-data contamination.
- Do not use issue labels or SZZ output as ground truth for "this is a bug fix" (0.74 mislabelled per correct label; 44% median SZZ line precision). Use them only as weak features in the prioritisation model. The 40% SZZ gain from cleaning its input is another instance of "clean the input deterministically before the expensive step".
- A developer-informed oracle (Rosa et al.) is a cheap way to get a labelled calibration set: mine commits whose messages explicitly say "fixes regression from <sha>" or link a PR with a reproducer, and use them to calibrate filters and LLM judges.
- Budget expectation: BugsInPy needed 831 person-hours for 493 bugs (about 1.7 h per bug). This is the human-curation baseline that an automated pipeline must beat on cost per validated task.

### Gaps
- Defects4J's exact inclusion criteria (fix in a single commit, bug-related issue link, manually minimised patch with refactorings/features removed) are from background knowledge; no source retrieved this session states them verbatim.
- The Herzig and Zeller tangled-changes number (commonly cited as up to about 15% of bug fixes being tangled, MSR 2013) and Herzig, Just, Zeller "It's not a bug, it's a feature" (ICSE 2013, about a third of bug reports misclassified) were not retrieved; verify before quoting.
- No 2025-2026 successor to GitBug-Java with different counts was found.
- No documented end-to-end funnel yield (candidates mined to bugs kept) was retrieved for Defects4J or GitBug-Java.

## 4. Resource allocation: successive halving, Hyperband, bandits, active learning, cascades

### Takeaway
The AutoML and detection literature gives directly reusable allocation rules: give every candidate a small budget, keep the top 1/eta, repeat (successive halving / Hyperband, 6x-70x faster than random search); reject negatives with the cheapest test first while keeping stage recall near 100% (Viola-Jones cascade, under 2 stages evaluated on average); and escalate to an expensive model only when a cheap one is unsure (FrugalGPT, up to 98% cost reduction). Active learning shows a surrogate trained on a small evaluated sample can recover 70-90% of the best items after evaluating 2-10% of the pool.

### Cited Findings
- (2016/2018, JMLR) Hyperband (Li et al.): wraps successive halving in several brackets that trade off number of configurations against budget per configuration. For the eta and R used, the theoretical maximum speedup over random search is about 50x with linear training time; observed speedups ranged from 6x to 70x. — [arXiv 1603.06560](https://arxiv.org/pdf/1603.06560)
- Commentary on Hyperband: plain successive halving often beats Hyperband in practice, and prior knowledge can be used to restrict which brackets are explored. — [intsystems blog on Hyperband](https://intsystems.github.io/materials/blog/hyperband)
- (2001/2004) Viola-Jones attentional cascade: stages ordered cheap to expensive; a sub-window failing any stage is rejected immediately; each stage is tuned for a near-zero false-negative rate and only a modest false-positive rate; on a large test set the average number of stages evaluated per window was under 2. — [Viola and Jones paper](https://www.cs.jhu.edu/~ayuille/courses/Stat238-Winter12/ViolaJones.pdf)
- (2023) FrugalGPT (Chen, Zaharia, Zou): an LLM cascade that queries cheaper models first and escalates based on a learned scorer of answer reliability; can match the best single LLM (GPT-4 at the time) with up to 98% cost reduction, or improve accuracy by 4% at equal cost. Best-case figure on the paper's datasets. — [arXiv 2305.05176](https://arxiv.org/pdf/2305.05176)
- (2023, JCIM) Marin et al., regression-based active learning for ultra-large docking: simple linear-regression surrogates retrieve up to 90% of the top-1% docking hits after docking 10% of ligands; on a 100M-compound library, 70% of the top-0.05% after docking 2%. — [PMC11005039](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11005039/)
- (2021, JCTC) Yang et al.: active learning recovered more than 80% of experimentally confirmed hits with a 14-fold reduction in compute, and more than 90% of hit scaffolds in the top 5% of model predictions. — [ChemRxiv preprint](https://chemrxiv.org/engage/chemrxiv/article-details/60c755bf842e65adc6db4393)
- Bandits applied to an evaluation pipeline: EcoFuzz (see section 2) is a deployed example of adversarial-MAB budget allocation with measured gains. — [USENIX Security 2020](https://www.usenix.org/conference/usenixsecurity20/presentation/yue)
- Learned cheap prior in front of expensive execution: Meta's predictive test selection (see section 1) is an industrial example on a CI pipeline, >95% of failures retained. — [arXiv 1810.05286](https://arxiv.org/pdf/1810.05286.pdf)

### Inferences (Fleet mapping)
- Successive halving over repositories: rung 0 = clone and static rules on a sample of commits (seconds); rung 1 = environment build attempt for the top 1/eta of repos (minutes); rung 2 = validate a handful of candidates; rung 3 = full mining only for repos with demonstrated yield. "Budget" is container-minutes per repo, "score" is validated tasks per minute. This fits scaling to thousands of repos because most of the cost of a bad repo is discovered at low rungs.
- Successive halving over candidates within a repo: budget rungs = targeted failing test only, then touched test files, then full suite x1, then full suite x5 for flakiness. Only survivors pay for the 5x reruns and LLM review.
- Hyperband's lesson about brackets: some repos have slow-to-build environments but very high yield once built. A pure aggressive-halving schedule would kill them; keep one conservative bracket (few repos, large budget) alongside the aggressive one.
- Cascade design rule (Viola-Jones): order filters by cost, and tune each early filter for recall near 100% with whatever precision it gets, rather than tuning each for accuracy. Measure each static rule's false-negative rate by sending a random sample of its rejects through full validation ("reject audit"). A rule that rejects valid tasks is far more costly than one that lets junk through to a cheap next stage.
- Order stages by (rejection rate / cost), not by intuition: a stage that removes 50% at 1 second belongs before one that removes 80% at 5 minutes. Log per-stage cost and pass rate so the ordering can be recomputed.
- LLM cascade (FrugalGPT): use a small model for task-quality screening and escalate to a large model only when the small model's confidence is low or when it disagrees with a static heuristic. Train the escalation scorer on Fleet's own human-audited sample.
- Active learning for the prioritiser: after each batch of container runs, retrain the yield model and choose the next batch as a mix of top-predicted (exploit) and high-uncertainty (explore) candidates. The docking results suggest a simple linear or tree model is enough to concentrate most of the hits in the first 2-10% evaluated, provided the cheap features carry signal.
- Guard against surrogate bias: docking-guided selection can overlap poorly with the better scorer (see section 6). Fleet's yield model optimises "passes validation", which is not the same as "is a good task"; keep a random-sample audit stream so quality drift is detectable.

### Gaps
- I found no published example of Hyperband or successive halving applied specifically to a data-mining or dataset-construction pipeline. The mapping above is my inference; the closest sourced analogues are predictive test selection (CI), EcoFuzz (fuzzing) and active-learning docking.
- No source retrieved on asynchronous successive halving (ASHA) or on Thompson sampling with delayed feedback; both are relevant (container runs finish asynchronously) but uncited here.
- FrugalGPT's 98% is a best-case 2023 number; I found no 2026 replication with current models and prices.

## 5. Generate-and-filter in ML: AlphaCode, synthetic data, data quality, judge reliability

### Takeaway
Large-scale generate-and-filter works when the filter is an executable check and the survivors are deduplicated by behaviour: AlphaCode discarded about 99% of samples on example tests and then clustered by output. Automated quality flags are only about half right (confident learning: 51-54% of flagged items were real errors), so flags should route items to verification rather than delete them. LLM judges need chance-corrected agreement against humans and bias checks before being trusted as a gate.

### Cited Findings
- (2022, Science / arXiv) AlphaCode: generate very large numbers of candidate programs per problem; filtering on the public example tests in the problem statement removes about 99% of samples; survivors (still thousands to tens of thousands for many problems) are clustered by their outputs on model-generated test inputs, and one sample is submitted from each of the largest clusters (10-submission limit). — [arXiv 2203.07814](https://arxiv.org/pdf/2203.07814)
- (Dec 2023) AlphaCode 2 tech report: up to about a million samples per problem; filtering removes approximately 95% of samples; clustering runs on about 50 thousand remaining candidates per problem on average and keeps the 10 largest clusters; a separate fine-tuned Gemini Pro scoring model then picks the best candidate from each cluster. — [The Register coverage](https://www.theregister.com/2023/12/07/alphacode_2_software/); [tech report copy](https://www.scribd.com/document/696172370/AlphaCode2-Tech-Report)
- (2021, NeurIPS Datasets and Benchmarks) Northcutt, Athalye, Mueller, "Pervasive Label Errors in Test Sets": confident learning flags candidate label errors, humans verify. Estimated at least 3.3% label errors on average across 10 widely used test sets (3.4% in earlier versions); at least 6% of the ImageNet validation set (2,916 errors). Only 51% of algorithmically flagged candidates were actually mislabelled (54% in earlier versions). Correcting labels can change model rankings: ResNet-18 outperforms ResNet-50 if the prevalence of originally mislabelled test examples rises by just 6%. — [arXiv 2103.14749](https://arxiv.org/abs/2103.14749)
- (2024-2025) Gu et al., "A Survey on LLM-as-a-Judge" (arXiv v6 Oct 2025): reliability is framed as consistency, bias mitigation and scenario adaptation; meta-evaluation uses percentage agreement, Cohen's kappa, Spearman correlation, or precision/recall against human labels; documents position and length bias among others; cites CALM, a framework quantifying 12 distinct bias types in LLM evaluators; calls for more rigorous human-alignment checks when judges are used for large-scale annotation. — [arXiv 2411.15594](https://arxiv.org/pdf/2411.15594)
- Meta's assured-LLM funnels are generate-and-filter with executable filters and reported acceptance: TestGen-LLM 75% built / 57% passed reliably / 25% increased coverage (secondary), 73% human acceptance; ACH 73% acceptance. — [arXiv 2402.09171](https://arxiv.org/abs/2402.09171); [arXiv 2501.12862](https://arxiv.org/pdf/2501.12862)
- ACH's LLM equivalence judge went from precision 0.79 / recall 0.47 to 0.95 / 0.96 with simple preprocessing, a measured example of judge reliability depending more on input normalisation than on the model. — [arXiv 2501.12862](https://arxiv.org/pdf/2501.12862)

### Inferences (Fleet mapping)
- Behavioural clustering for dedup and diversity (AlphaCode): cluster candidate tasks by their behavioural signature (which tests flip, failure messages, files touched) and take a bounded number per cluster. This prevents a single noisy repo or a repeated refactor pattern from dominating the catalogue, and it is cheap once validation logs exist.
- Cheap executable filter first, learned scorer last (AlphaCode 2): the order was execute, then cluster, then spend the expensive scoring model on only about 10 cluster representatives. Fleet's LLM reviewer should see cluster representatives of validated candidates, not raw candidates.
- Filters that remove 95-99% are normal. A low Fleet yield (single-digit percent of mined commits surviving) is consistent with the best-known pipelines; the right response is cheaper early stages, not looser gates.
- Flags are leads, not verdicts (confident learning 51-54% precision): any automated "this task looks bad" detector, including an LLM reviewer, should route to a second check (rerun, different model, or human sample) rather than delete. Report the measured precision of each detector.
- Confident-learning analogue for Fleet: where two signals disagree with high confidence (e.g. static rule says "test-only change" but validation shows a source-dependent fail-to-pass flip; or LLM judge says "well-specified" while a solver model with the task statement alone fails for reasons unrelated to difficulty), treat the item as a likely label error and audit it.
- Judge calibration protocol: build a human-labelled gold set of a few hundred validated tasks; report Cohen's kappa between judge and humans and between two humans (the human-human kappa is the ceiling); test order/position and length sensitivity by permuting prompt sections; re-run the calibration whenever the judge model or prompt changes. Gate on judge output only for criteria where kappa is acceptable; elsewhere use the judge for ranking.
- Small benchmark errors move rankings (ResNet-18 vs ResNet-50 result): a few percent of invalid tasks is enough to distort model comparisons, which justifies spending expensive verification on the final catalogue rather than accepting residual noise.

### Gaps
- Self-Instruct (Wang et al., 2022) numbers were not retrieved. From background knowledge only (unverified): new instructions were kept only if ROUGE-L similarity to existing ones was below 0.7, and a manual audit of 200 samples found roughly half of generated instances fully valid.
- No retrieved source on distillation-with-verification pipelines (e.g. execution-verified synthetic code data) with funnel numbers.
- Specific judge-human agreement figures (e.g. the widely quoted roughly 80-85% GPT-4/human agreement on MT-Bench) appeared only in a practitioner blog and were not confirmed against the primary paper.
- No retrieved inter-annotator agreement numbers for coding-task annotation specifically.
- AlphaCode 2 numbers come from press coverage and a re-hosted copy of the tech report, not DeepMind's own hosting.

## 6. Screening funnels elsewhere: drug discovery, hardware verification, formal methods, bug bounty triage

### Takeaway
Virtual screening provides the clearest quantitative evidence that a cheap surrogate can stand in for an expensive scorer on most of a library, and also the clearest warning that the cheap score and the expensive score can rank differently. Bug bounty triage in 2025-2026 is a live case study of what happens to a verify-expensively funnel when candidate generation becomes free: curl's valid-report rate collapsed and the programme was shut down.

### Cited Findings
- (2023, JCIM) Active learning with linear-regression surrogates retrieved up to 90% of top-1% docking hits after docking 10% of the library; on 100M compounds, 70% of the top-0.05% after docking 2%. — [PMC11005039](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11005039/)
- (2021, JCTC) More than 80% of experimentally confirmed hits recovered with a 14-fold compute reduction; more than 90% of hit scaffolds in the top 5% of predictions. — [ChemRxiv](https://chemrxiv.org/engage/chemrxiv/article-details/60c755bf842e65adc6db4393)
- (2026, RSC Digital Discovery) Active learning recovered 79.9% of the top-1% MMGBSA-ranked binders while screening a fraction of the dataset, and docking-guided selection overlapped poorly with MMGBSA-ranked binders, i.e. the cheap tier's ranking is not a reliable proxy for the expensive tier's ranking. — [RSC Digital Discovery 2026](https://pubs.rsc.org/en/content/articlepdf/2026/dd/d5dd00522a)
- (2025) curl bug bounty: Daniel Stenberg reported that by mid-2025 about 20% of submissions were AI slop and described the load as comparable to a DDoS; the project began instantly banning reporters judged to submit slop. — [Stenberg, "Death by a thousand slops"](https://daniel.haxx.se/blog/2025/07/14/); [The Register, May 2025](https://www.theregister.com/2025/05/07/curl_ai_bug_reports/)
- (Jan 2026) curl ended its bug bounty; from February 2026 reports go through GitHub with no monetary reward. — [The New Stack](https://thenewstack.io/drowning-in-ai-slop-reports-curl-ends-bug-bounties/); [Gigazine](https://gigazine.net/gsc_news/en/20260123-curl-bug-bounty)
- HackerOne's reputation penalty for invalid reports was criticised as weak because newcomers can simply create a new account. — [CyberInsider](https://cyberinsider.com/bug-bounty-platforms-are-getting-overwhelmed-by-ai-generated-submissions/)
- ClusterFuzz-style triage (stack-signature dedup, automatic minimisation, automatic regression range) is the security-side model of fully automated triage before human attention. — [ClusterFuzz glossary](https://google.github.io/clusterfuzz/reference/glossary)

### Inferences (Fleet mapping)
- Multi-fidelity funnel with an explicit rank-correlation check: before trusting a cheap score (static rules, yield model) to order the queue for an expensive one (container validation, LLM quality review), measure on a random sample how well the cheap ranking predicts the expensive outcome. The 2026 docking/MMGBSA result shows the cheap tier can be systematically misaligned.
- Enrichment-factor reporting: borrow the virtual-screening metric "fraction of all true hits found in the top x% of the ranked list" as Fleet's headline measure for any prioritiser, instead of classifier accuracy.
- Scaffold diversity: screening counts distinct scaffolds, not just hits. Fleet should count distinct (repo, subsystem, bug-pattern) clusters among validated tasks, not just task count.
- Source reputation (bug bounty): maintain a per-repository and per-author trust score from past validation outcomes, and throttle or ban sources whose candidates repeatedly fail expensive checks. Unlike HackerOne accounts, repositories cannot cheaply re-register, so the penalty actually binds.
- Require a cheap proof-of-work artifact before expensive review: bounty programmes increasingly demand a working reproducer. The Fleet equivalent is that no candidate reaches an LLM reviewer or human without a machine-verified fail-to-pass log.
- Plan for generation becoming free: if LLM-synthesised or agent-authored commits enter the mined population, the valid fraction can drop sharply, as curl experienced. Track valid rate over commit date as a drift signal and consider filtering on evidence of human review (merged PR with review, CI history).

### Gaps
- Hardware verification (coverage-directed test generation, regression ranking, simulation-then-formal funnels): no source retrieved; the one combined search returned nothing on it. Not covered.
- Formal methods triage (e.g. ranking static-analysis or model-checker warnings, proof-obligation scheduling): no source retrieved. Not covered.
- No platform-wide HackerOne/Bugcrowd duplicate or invalid-report rate was found; "less than 1 in 20 reports real" and "15% falling to under 5%" for curl come from a single low-authority blog (pinggy.io) and are unverified, as are that blog's claims about July 2026 curl changes and a March 2026 Internet Bug Bounty pause.
- No experimental hit-rate numbers for industrial virtual-screening funnels (e.g. Schrödinger's workflow white paper surfaced but was not read).
