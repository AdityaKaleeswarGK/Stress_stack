# Sandbox and Scale Infrastructure for Untrusted-Code Pipelines (state as of November 2026)

Research note for Fleet. Conventions: "vendor-reported" marks numbers a provider published about itself; "third-party" marks comparison blogs (several are written by competing vendors: Fly.io, Upstash, Northflank, Blaxel). Dates are given for every figure older than 2026. Items that could not be verified are listed under Gaps rather than filled from memory.

## 1. Sandbox isolation technologies and hosted sandbox providers

### Takeaway
Hardware-virtualized microVMs (Firecracker, Kata/Cloud Hypervisor) are the default isolation boundary for hosted agent sandboxes in 2026, with gVisor as the main software-kernel alternative (Modal, GKE Agent Sandbox); plain runc is treated as insufficient for untrusted code, and Wasm cannot run arbitrary repository test suites. Hosted sandbox list prices cluster around $0.04-0.07 per vCPU-hour plus $0.009-0.024 per GiB-hour, and snapshot/fork is now a standard feature, with large differences in whether memory is captured.

### Cited Findings

Isolation primitives
- Firecracker (NSDI '20 paper, Agache et al., 2020): under 5 MB memory overhead per microVM, boot to application code in under 125 ms, up to 150 microVMs created per second per host. These are the authors' (AWS) own figures. — [Firecracker NSDI paper](https://css.csail.mit.edu/6.566/2023/readings/firecracker.pdf)
- Third-party summaries (2026) place Firecracker boot at about 100-200 ms, Kata at about 150-300 ms depending on VMM, and gVisor at "milliseconds" with no VM boot; gVisor is reported 10-30% slower than native containers on I/O-heavy workloads; Kata needs tens of MB extra memory per container. All vendor-blog figures, no common test bench. — [Northflank: Kata vs Firecracker vs gVisor](https://northflank.com/blog/kata-containers-vs-firecracker-vs-gvisor); [Northflank: Firecracker vs gVisor](https://northflank.com/blog/firecracker-vs-gvisor); [Edera](https://edera.dev/stories/kata-vs-firecracker-vs-gvisor-isolation-compared)
- Firecracker and Kata give each sandbox a dedicated guest kernel via KVM; gVisor intercepts syscalls in a user-space kernel (the Sentry) and shares the host kernel behind it. — [Northflank: Kata vs Firecracker vs gVisor](https://northflank.com/blog/kata-containers-vs-firecracker-vs-gvisor)
- gVisor's own performance guide: CPU-bound code runs natively with no penalty; syscall interception cost depends on the platform (KVM platform is low-overhead but runs poorly under nested virtualization; Systrap is recommended in most cases); file I/O crossing the sandbox boundary goes through the Gofer with a fixed per-operation cost; small-operation workloads (Redis small ops, Apache small static files) show large overhead; its network stack is less CPU-efficient than Linux's. The page publishes charts, not numbers in text, and its benchmarks use old hardware and the slow ptrace platform. — [gVisor performance guide](https://gvisor.dev/docs/architecture_guide/performance/)
- Kata integrates as a standard Kubernetes RuntimeClass; the upstream kubernetes-sigs/agent-sandbox project defaults to gVisor with Kata as an option. — [Northflank: Kata vs Firecracker vs gVisor](https://northflank.com/blog/kata-containers-vs-firecracker-vs-gvisor); [Kubernetes blog, 20 Mar 2026](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox)
- WebAssembly: WASI is deny-by-default (capabilities passed explicitly), with CPU limits via fuel/epochs and a memory cap; but a runtime CVE (e.g. in wasmtime) is a same-host escape across tenants. — [agentpatterns.ai](https://agentpatterns.ai/security/wasm-sandbox-agent-code-execution/)
- Pyodide-based "sandboxes" were shown at DEF CON 34 (2026) to be escapable in seven products, because restrictions sat at the Python level and ctypes/FFI remained available. — [Cyera research](https://www.cyera.com/research/sandcastles-not-sandboxes-how-one-architectural-flaw-exposed-seven-products); [eSecurity Planet](https://www.esecurityplanet.com/threats/def-con-34-one-pyodide-flaw-exposed-seven-products/)
- Wasm Python has no PyTorch/CUDA, and plain WASI lacks the dynamic linking that native extensions rely on; Pyodide is not supported for server-side use. — [Simon Willison, 6 Jun 2026](https://simonwillison.net/2026/Jun/6/micropython-in-a-sandbox/); [Morph: WebAssembly sandbox](https://www.morphllm.com/webassembly-sandbox)

Kubernetes-native sandboxing
- kubernetes-sigs/agent-sandbox provides Sandbox, SandboxTemplate, SandboxClaim and SandboxWarmPool CRDs and delegates isolation to gVisor or Kata through RuntimeClass. — [Agent Sandbox docs](https://agent-sandbox.sigs.k8s.io/docs/); [Kubernetes blog, 20 Mar 2026](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox)
- GKE's managed Agent Sandbox is gVisor-only. GKE Pod snapshots save full pod state (memory plus filesystem) to Cloud Storage, require the gVisor runtime, and take "a few seconds" to restore the gVisor kernel; Google's docs say workloads that already start fast generally will not benefit. — [GKE Pod snapshots docs](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/pod-snapshots?authuser=0)
- Secondary reports (unverified against Google primary pages): warm pools up to 300 sandboxes per second per cluster with 90% under 200 ms; pod snapshots cut startup latency by up to 89%. — [InfoQ, Sep 2026](https://infoq.com/news/2026/09/gke-pod-snapshots-benchmarks)

Hosted providers: isolation, cold start, snapshots (third-party table from Upstash, first published 17 Sep 2026, "last checked November 2026"; cold-start numbers are mostly vendor claims measured in inconsistent ways) — [Upstash: 15 providers compared](https://upstash.com/blog/ai-agent-sandbox-providers-compared-2026)

| Provider | Isolation | Cold start (claim) | Snapshot / fork / pause | Max session | Concurrency | BYOC / self-host |
|---|---|---|---|---|---|---|
| E2B | Firecracker microVM | ~150 ms | Memory + fs snapshots; fork up to 20 per call; pause keeps memory, fs, processes | 1 h Hobby, 24 h Pro | 10 Hobby, 100 Pro, up to 1,100 | BYOC (Enterprise); Apache-2.0 self-hosted runtime |
| Daytona | Container by default; VM classes available | under 90 ms | Disk snapshots; VM snapshots with memory; VM fork | none documented | vCPU pools 10-500 by tier; 4 vCPU / 8 GB per sandbox | BYOC; open-source repo archived June 2026 |
| Modal | gVisor (VM option) | sub-second | Filesystem and directory snapshots; memory snapshots (alpha); no pause state | 24 h | 100 Starter, 5,000 Team | Managed only |
| Runloop | VM on custom hypervisor | "a few seconds" | Disk snapshots, fan-out | 1 h default, configurable | 3 on trial | Managed; listed as "winding down" |
| Fly Sprites | Firecracker microVM | 1-2 s create, 100-500 ms warm wake | Disk-only checkpoints, no fork | none | 20-2,000 active by plan | Managed only |
| Vercel Sandbox | Firecracker microVM | "milliseconds" | Disk snapshots, fork, auto-snapshot on stop | 45 min Hobby, 24 h Pro | 10 Hobby, 10,000 Pro | Managed only |
| Cloudflare Sandbox | Firecracker microVM | 1-3 s | Fs snapshots (beta, 20 GB max, 30-day TTL) | none; idle sleep | 15,000 concurrent vCPU per account | Managed only |
| Northflank | Kata + Cloud Hypervisor; gVisor for GPU | under 1 s | Container snapshots; no fork | none | "unlimited" on PAYG | Self-serve BYOC, bring your own Kubernetes |
| Beam | gVisor + runc; microVM opt-in | ~0.5 s | Fs + memory snapshots, fork | none | 30 CPU Developer, 1,000 Team | BYOC; self-host via Beta9 (AGPL-3.0) |
| Freestyle | KVM VM | ~65 ms provision | Memory + disk snapshots; fork a running VM without pausing | none | 10 / 40 / 400 by plan | Managed only |
| Blaxel | microVM | ~25 ms resume from standby | Memory + disk snapshots; fork in private preview | none | 10 up to 100k+ | Bring-your-own-metal |

Hosted providers: list pricing (pay-as-you-go)
- E2B and Daytona: $0.0504 per vCPU-hour and $0.0162 per GiB-hour, wall-clock on reserved resources. E2B Pro adds a $150/month base fee; Daytona has no plan fee and bills reserved disk for stopped sandboxes ($0.000108 per GiB-hour beyond 5 GiB), nothing for archived. — [Fly.io pricing comparison, 9 Sep 2026, rates checked 10 Sep and 5 Oct 2026](https://fly.io/learn/ai-sandbox-pricing/)
- Modal Sandboxes: about $0.1419 per physical core-hour (2 vCPU, so about $0.071 per vCPU-hour), $0.024 per GiB-hour, billed on the higher of requested or actual usage; Starter includes $30/month credit, Team $250/month. — [Fly.io pricing comparison](https://fly.io/learn/ai-sandbox-pricing/); [Upstash](https://upstash.com/blog/ai-agent-sandbox-providers-compared-2026)
- Runloop: $0.108 per CPU-hour, $0.0252 per GB-hour, storage-only billing when suspended (Pro). — [Upstash](https://upstash.com/blog/ai-agent-sandbox-providers-compared-2026)
- Fly Sprites: $0.0385 per CPU-hour and $0.021875 per GB-hour on actual usage; hot storage $0.000683 per GB-hour, cold $0.000027 per GB-hour; pauses about 30 s after idle. Fly Machines shared-cpu-1x with 1 GB from $0.0093/hour, billed per second. — [Fly.io pricing comparison](https://fly.io/learn/ai-sandbox-pricing/)
- Vercel Sandbox: $0.128 per active CPU-hour, $0.0212 per GB-hour provisioned, snapshots $0.08 per GB-month. Cloudflare Sandbox: $0.072 per vCPU-hour active CPU, $0.009 per GiB-hour. Northflank: $0.01667 per vCPU-hour, $0.00833 per GB-hour. Beam: $0.05 (Performance), $0.011 (Shared), $0.0063 (Spot) per vCPU-hour. Freestyle: $0.04032 per vCPU-hour. — [Upstash](https://upstash.com/blog/ai-agent-sandbox-providers-compared-2026)
- Morph Cloud: bills in Morph Compute Units at $0.05 per MCU, where 1 MCU = 1 vCPU-hour, or 4 GB RAM-hours, or 16 GB disk-hours, or 5 TB snapshot-hours. "Infinibranch" is claimed to snapshot, branch and restore in under 250 ms (vendor-reported, no percentile). — [Morph Cloud subscribe page](https://cloud.morph.so/web/subscribe); [Morph Cloud developer docs](https://cloud.morph.so/docs/developers)
- AWS Fargate (us-east-1, Linux/x86): about $0.04048 per vCPU-hour and $0.004445 per GB-hour; Graviton about 20% lower; Fargate Spot about $0.01254 per vCPU-hour with a 2-minute reclaim warning. Lambda: $0.20 per million requests plus $0.0000166667 per GB-second on x86. Figures from third-party calculators, not the AWS pricing page. — [cloudburn.io](https://cloudburn.io/blog/aws-fargate-pricing); [tech-insider.org](https://tech-insider.org/aws-fargate-vs-lambda-2026/)
- Worked example (third-party, 2 vCPU / 4 GB for one hour, CPU-saturated wall-clock): E2B/Daytona come to about $0.166; Modal and Runloop are the most expensive in the comparison. Mostly-idle workloads favour active-CPU billing (Cloudflare about $0.052). — [Upstash](https://upstash.com/blog/ai-agent-sandbox-providers-compared-2026)
- Latency measured by one third party (Aug 2026): Vercel Sandbox median 0.67 s; Modal 0.88 s; Runloop 0.89 s median with P95 3.27 s. — [Superagent benchmark](https://www.superagent.sh/blog/ai-code-sandbox-benchmark-2026)

Modal snapshot and network details
- `snapshot_filesystem()` returns an Image from which new sandboxes can be spawned; memory snapshots capture memory plus filesystem, are experimental, and expire 7 days after creation. Docs conflict on filesystem snapshot retention (indefinite versus a 30-day default TTL). — [Modal snapshots guide](https://modal.com/docs/guide/sandbox-snapshots.md); [Modal memory snapshots](https://modal.com/docs/guide/sandbox-memory-snapshots)
- Modal network controls: `block_network` (default False) and `outbound_cidr_allowlist`; the older `cidr_allowlist` is deprecated. Allowlisting is by CIDR, not by domain. — [modal.Sandbox reference](https://modal.com/docs/reference/modal.Sandbox.md)
- Modal published "directory snapshots" for resumable project state on 24 Feb 2026. — [Modal blog](https://modal.com/blog/directory-snapshots-resumable-project-state-for-sandboxes)

### Inferences
- For Fleet's workload (pip/apt installs and pytest runs, which are syscall- and small-file-heavy), gVisor's overhead profile is the unfavourable case, and microVMs with a real guest kernel will give the best compatibility for arbitrary repositories. This is a reading of gVisor's own qualitative guidance, not a measured result.
- Wasm is not a candidate for the repository-test stage: Fleet needs subprocesses, native extensions and arbitrary toolchains. It could only apply to small pure-Python helper code.
- At list prices, a 2 vCPU / 4 GB sandbox costs roughly $0.17 per hour on E2B/Daytona and roughly $0.10 per hour on Fargate on-demand. 50,000 ten-minute test runs at that shape is about 8,300 sandbox-hours, or roughly $800-1,400; compute for running tests is therefore small next to LLM spend and engineering time. (Arithmetic on the cited list prices.)
- Concurrency caps on self-serve tiers (E2B Pro 100, Modal Starter 100 / Team 5,000) are the binding constraint for a burst of tens of thousands of runs, more than price.
- Provider risk is real: the same table lists Runloop as winding down and Daytona's open-source repository as archived in June 2026, so the sandbox layer should sit behind an interface (as Harbor does with pluggable environments).

### Gaps
- No independent 2026 benchmark measures runc, gVisor, Kata and Firecracker on the same hardware; all startup and overhead numbers above come from different sources and conditions.
- No sourced quantitative gVisor slowdown for build or test-suite workloads (the gVisor guide publishes charts only).
- No sourced Wasm cold-start timings versus containers.
- AWS Lambda SnapStart, Lambda limits (15-minute cap, image size) and a reported June 2026 Fargate microVM launch were not verified from AWS primary pages.
- Fly Machines detailed pricing (stopped-machine rootfs billing) and Morph's official rate card (only visible in its billing console) were not confirmed.
- E2B/Daytona snapshot restore times and snapshot storage pricing were not found in primary docs.

## 2. How comparable projects ran tens of thousands of environments

### Takeaway
The per-instance-image design (SWE-bench, SWE-Gym, R2E-Gym) costs terabytes; two fixes are documented: one image per repository with per-task branches (SWE-smith: 50k tasks in 295 GB) and aggressive layer sharing (Epoch AI: SWE-bench from 684 GiB to 67 GiB). The only detailed account of a build farm at Fleet's target scale is Nebius's SWE-rebench: rootless Buildah builds as map jobs, an internal registry, internal PyPI/Docker Hub/apt mirrors, and up to 8,000 parallel agent pods on Kubernetes.

### Cited Findings

SWE-bench harness
- Three image layers: one base image, about 60 environment images (one per Python/dependency configuration), and one instance image per task. — [SWE-bench Docker setup guide](https://www.swebench.com/SWE-bench/guides/docker_setup/)
- Storage by `cache_level`: `none`/`base` about 120 GB during a run; `env` (default) about 100 GB; `instance` about 2,000 GB (fastest). Recommended workers: fewer than min(0.75 x cores, 24); 16 GB+ RAM; at least 120 GB free disk. — [SWE-bench Docker setup guide](https://www.swebench.com/SWE-bench/guides/docker_setup/)
- Epoch AI (10 Jul 2025): the original 2,290 SWE-bench images sum to 3,129 GiB if counted individually but 684 GiB as unique layers; after reordering layers the registry is 67 GiB (about 10x smaller), and SWE-bench Verified's 500 images are 30 GiB (down from 189 GiB, about 6x). — [Epoch AI blog](https://epoch.ai/blog/swebench-docker)
- Epoch's techniques: move `git clone` into the shared environment layer so the instance layer only does a hard reset to the target commit (Django final layer 330 MB to 40 MB, of which 291 MB had been git history); move version-specific apt dependencies into the environment layer (matplotlib top layer about 1,900 MB to 110 MB); disable the pip cache. Epoch notes that leaving future git history in the image is a reward-hacking risk. — [Epoch AI blog](https://epoch.ai/blog/swebench-docker)
- Epoch runs SWE-bench Verified in 62-73 minutes on a single 32-core / 128 GB GitHub Actions runner (about 8 s per sample amortized), consuming 100-150M tokens per eval (mostly cached) at about 2M tokens per minute; images are public on GHCR; x86_64 only for 2,290 of 2,294, with 1,819 best-effort arm64 images. — [Epoch AI blog](https://epoch.ai/blog/swebench-docker)

SWE-smith (2025)
- 50,137 task instances from 128 Python repositories, with one Docker image per repository rather than per task; total environment storage about 295 GB. The paper estimates SWE-bench-style images for the same task count at 50-150 TB (about 500x). Image count is given as 125 in one summary and 128 in another. — [SWE-smith paper, arXiv 2504.21798v2](https://arxiv.org/html/2504.21798v2); [SWE-smith project page](https://john-b-yang.github.io/swe-smith)
- Storage comparison table in the paper: R2E about 270 GB (137 repos, 0.25k tasks); R2E-Gym subset about 4 TB (4.6k tasks, 10 repos); SWE-Gym about 6 TB (2.4k tasks, 11 repos). — [SWE-smith paper](https://arxiv.org/html/2504.21798v2)
- Total cost about $1,360: about $1,000 bug generation, $160 automatic repository installation with SWE-agent, $200 issue generation for 10k bugs. Average 2.32 cents per candidate with 50.1% overall yield (LM Modify 0.38 cents / 56% yield; LM Rewrite 3.93 cents / 35%; PR Mirror 5.53 cents / 33.8%; procedural and combine methods cost nothing). — [SWE-smith paper](https://arxiv.org/html/2504.21798v2)
- Environment construction: SWE-agent with Claude 3.5 Sonnet on each repository's latest commit, up to 150 calls, averaging $0.72 and 17 steps per repository; manual review 3-20 minutes per install; about 18 human hours for 128 repositories (17 dropped at review); total author effort about 20 hours. — [SWE-smith paper](https://arxiv.org/html/2504.21798v2)
- Validation rule: test runs capped at 2 minutes, candidates exceeding it are discarded; repositories required over 80% of existing tests passing. — [SWE-smith paper](https://arxiv.org/html/2504.21798v2)

SWE-Gym and R2E-Gym
- SWE-Gym (Dec 2024): 2,438 validated task instances from 11 repositories, out of a raw pool across 358 Python repositories (a read of the paper gave 64,689 raw instances; a search summary of the same paper gave 66,894, so the raw count is uncertain). That is under 4% of raw instances ending up with a working environment. — [SWE-Gym paper, arXiv 2412.21139](https://arxiv.org/html/2412.21139v2)
- SWE-Gym environments were configured manually, version by version, from requirements files, CI scripts and docs, then checked with SWE-bench's execution-based validation; this took about 200 human annotation hours and about 10,000 CPU core-hours for setup and validation. — [SWE-Gym paper](https://arxiv.org/html/2412.21139v2)
- SWE-Gym releases one pre-built Docker image per instance, 6 TB in total (about 2.5 GB per image by division). Rollouts and test evaluation were parallelized with the OpenHands remote runtime (worker count not stated); one sampling run lost instances to infrastructure errors. — [SWE-Gym paper](https://arxiv.org/html/2412.21139v2); corroborated by [SWE-MiniSandbox, arXiv 2602.11210](https://arxiv.org/html/2602.11210)
- SWE-smith's author states images across SWE-bench, SWE-Gym and R2E-Gym average 1-3 GB each. — [SWE-smith project page](https://john-b-yang.github.io/swe-smith)
- R2E-Gym: 8.1K problems across 13 repositories with pre-built Docker images, each estimated at 300-500 MB. — [R2E-Gym GitHub](https://github.com/ServiceNow/R2E-Gym); [R2E-Gym site](https://r2e-gym.github.io)
- RepoForge reports a typical SWE-bench-style image at about 1.4 GB and about 700 GB for 500 SWE-bench Verified tasks. — [RepoForge, arXiv 2508.01550](https://arxiv.org/html/2508.01550v2)
- SWE-MiniSandbox (2026) proposes container-free environments and claims about 25% of the environment-preparation time and about 5% of the storage of container-based methods (authors' claim). — [SWE-MiniSandbox, arXiv 2602.11210](https://arxiv.org/html/2602.11210)

SWE-rebench and SWE-rebench V2 (Nebius)
- SWE-rebench V2: 32,000+ tasks, 20 languages, 3,600+ repositories in the arXiv v1 preprint; the ICML 2026 version reports 36,000+ tasks and 3,800+ repositories; pre-built images are provided. Setup synthesis uses an interactive agent to infer and debug install and test instructions on top of language-specific base-image, test-runner and parser templates. — [SWE-rebench V2, arXiv 2602.23866](https://arxiv.org/abs/2602.23866); [ICML 2026 poster](https://icml.cc/virtual/2026/poster/63732)
- Infrastructure (predecessor pipeline, post dated 7 Nov 2025): about 21 TB of GitHub Archive ingested; about 32K repositories cloned with full history (about 1 TB); about 153K task candidates yielded about 21K validated tasks (roughly 14%). — [Nebius: infrastructure behind SWE-rebench](https://nebius.com/blog/posts/infrastructure-behind-swe-rebench)
- Builds run as MapReduce-style map jobs on TractoAI (YTsaurus-based) using Buildah (rootless, daemonless), storing layers on tmpfs with the VFS storage driver because disk I/O was the bottleneck; each job builds the image from an LLM-extracted recipe and runs the tests to validate; images go to the platform's built-in registry. Evaluation uses Podman inside map jobs. — [Nebius](https://nebius.com/blog/posts/infrastructure-behind-swe-rebench)
- Agent runs: up to 8,000 pods in parallel on Kubernetes, driven directly through the Kubernetes Python SDK, placed on GPU nodes with idle CPU; pod requests set to median observed usage, limits about 4 CPU / 16 GB. A full SWE-bench Verified evaluation took about 18 minutes. — [Nebius](https://nebius.com/blog/posts/infrastructure-behind-swe-rebench)
- Internal mirrors of PyPI, Docker Hub and Ubuntu archives were added after public rate limits were hit during builds and validation. — [Nebius](https://nebius.com/blog/posts/infrastructure-behind-swe-rebench)
- Reported failure modes: pods stuck Pending on a missing image deadlocked whole runs (fixed with state-transition timeouts); abruptly exiting runners left zombie pods (fixed with a label-based cleanup cron); vanished pods after node failures required whole-task retries; Kubernetes logs and events disappeared quickly (fixed with Grafana and kube-state-metrics); giant batch jobs exceeded the etcd entry size limit; container-in-container builds were difficult, which drove the choice of Buildah/Podman. — [Nebius](https://nebius.com/blog/posts/infrastructure-behind-swe-rebench)

NVIDIA Nemotron
- Nemotron-SWE-v1 (card created 3 Dec 2025): 59k OpenHands agent trajectories with tasks drawn from SWE-Gym and R2E-Gym-Subset (51,029 from R2E-Gym). NVIDIA reuses third-party environment images rather than building its own. — [Nemotron-SWE-v1 dataset card](https://huggingface.co/datasets/nvidia/Nemotron-SWE-v1)
- NVIDIA's Nemotron RL docs state each SWE task needs an isolated environment with its own filesystem, that R2E-Gym, SWE-Gym and SWE-bench Verified images are available as Docker images, and that they can be converted to Apptainer for HPC clusters. — [NVIDIA Nemotron SWE-RL docs](https://docs.nvidia.com/nemotron/nightly/nemotron/super3/rl/swe.html)

OpenHands remote runtime
- OpenHands blog (4 Oct 2024): the remote runtime runs each agent's Docker sandbox on separate cloud machines; at 32x parallelism a 300-instance SWE-bench Lite evaluation fell from about two days (about 10 minutes per instance serially) to several hours; storage need is described as "hundreds of gigabytes"; cost about $0.30 per issue with Claude 3.5 Sonnet, reduced by prompt caching. — [OpenHands blog](https://openhands.dev/blog/evaluation-of-llms-as-coding-agents-on-swe-bench-at-30x-speed)
- OpenHands docs label the remote runtime as beta and intended only for evaluation through the evaluation harness, not for production applications. — [OpenHands remote runtime docs](https://docs.all-hands.dev/modules/usage/runtimes/remote)

Harbor
- Harbor (Apache-2.0) is the framework from the Terminal-Bench authors for evaluating agents, building benchmarks and generating RL rollouts, and is the official harness for Terminal-Bench 2.0. Runs default to local Docker; `--env` selects a hosted backend. The README (read November 2026) names Daytona, Modal, LangSmith, Blaxel, Novita Sandbox, Tensorlake, Runta and Sail; older package pages also list E2B, Runloop and GKE, so the provider list has changed over time. Examples use `--n-concurrent 4` locally and `--n-concurrent 100` on Daytona, and the README claims experiments "in thousands of environments in parallel" (project's own claim, no measured run described). — [Harbor GitHub](https://github.com/harbor-framework/harbor); [Harbor on PyPI](https://pypi.org/project/harbor/0.1.16/)

### Inferences
- Fleet's "one environment image per commit" is the expensive pattern. For repositories contributing many commits, the SWE-smith and Epoch results both point to putting the clone and the dependency install in a shared per-repository (or per-dependency-set) layer and making the per-commit layer only a `git checkout` plus any dependency delta. Epoch's Django numbers imply roughly 40 MB per commit instead of 330 MB or more.
- Nebius's 153K candidates to 21K validated tasks is a concrete planning ratio: expect to build and test roughly seven environments per accepted task, so build throughput and fast failure matter more than run throughput.
- SWE-smith's 2-minute test cap and 80%-tests-pass repository filter are cheap gates that bound compute before any expensive stage.
- Epoch's caution about future git history applies directly to Fleet: a shared clone layer must be pruned to the target commit (or the remote and later objects removed) so task solutions are not recoverable from the image.
- NVIDIA and OpenHands both consumed existing images; among the projects surveyed, only Nebius and SWE-smith describe building environments at scale, and both use an LLM agent to synthesize install recipes followed by execution-based validation.

### Gaps
- SWE-rebench V2 total image storage, build compute and V2-specific infrastructure were not found; the Nebius post describes the predecessor pipeline.
- R2E-Gym total storage is not officially published; the 4 TB figure is SWE-smith's estimate for a subset.
- No NVIDIA account of building environments at scale was found.
- OpenHands remote runtime internals (image build and storage, backing infrastructure) are not documented in the sources found; the 2024 blog is the most recent primary account located.
- Harbor's README does not cover job resumability, image or snapshot caching, or a measured maximum concurrency; its detailed docs were not read.
- SWE-bench's Modal-based cloud evaluation option was not confirmed from the page fetched.

## 3. Image and build efficiency

### Takeaway
The largest documented gains come from layer ordering and sharing (6-10x storage reduction) and from keeping builder state warm, not from lazy pulling; lazy pulling helps only when a container reads a small fraction of its image, which is not the case for a test suite that imports most of its environment.

### Cited Findings
- Layer sharing: Epoch AI cut SWE-bench image storage about 10x (684 to 67 GiB) and Verified about 6x purely by moving shared, expensive steps into deeper layers and keeping instance-specific steps at the top. (10 Jul 2025) — [Epoch AI blog](https://epoch.ai/blog/swebench-docker)
- One image per repository instead of per task: 295 GB for 50k tasks versus an estimated 50-150 TB. (2025) — [SWE-smith paper](https://arxiv.org/html/2504.21798v2)
- BuildKit cache mounts are not exported by registry-based `cache-to`/`cache-from`; they live only in the builder's local state, so ephemeral CI builders lose them unless a workaround or persistent builder is used. — [Depot: cache mounts in CI](https://depot.dev/blog/how-to-use-buildkit-cache-mounts-in-ci); [buildkit-state project](https://github.com/isac322/buildkit-state)
- Depot reports an apt cache mount made a rebuilt RUN step about twice as fast (vendor example). — [Depot](https://depot.dev/blog/how-to-use-buildkit-cache-mounts-in-ci)
- Earthly's demo: 54 s baseline build fell to about 1.5 s with a warm local BuildKit cache. — [Earthly blog](https://earthly.dev/blog/build-buildkit-cache/)
- Blacksmith claims a persistent remote BuildKit instance with shared cache takes 30-minute builds to under 2 minutes (vendor marketing, Aug 2024). — [Blacksmith blog](https://www.blacksmith.sh/blog/faster-docker-builds-using-a-remote-buildkit-instance)
- RunsOn's PostHog benchmark found that exporting a remote (S3) cache took longer than the entire build job on a persistent "sticky" disk, so remote cache export can erase its own gains. — [RunsOn blog](https://runs-on.com/blog/posthog-buildkit-sticky-disk-benchmark/)
- Amazon ECR supports BuildKit remote cache storage. — [AWS containers blog](https://aws.amazon.com/jp/blogs/containers/announcing-remote-cache-support-in-amazon-ecr-for-buildkit-clients/)
- Rootless builders at scale: Nebius used Buildah with the VFS driver on tmpfs because disk I/O was the bottleneck and overlay filesystems were unavailable inside unprivileged containers. (Nov 2025) — [Nebius](https://nebius.com/blog/posts/infrastructure-behind-swe-rebench)
- Lazy pulling, Grab production (21 Jan 2026): an Airflow container started in 5.0 s on overlayFS, 5.0 s with SOCI and 25.0 s with eStargz; SOCI with tuned parallel download settings cut fresh-node image download from 60 s to 24 s (60%); production P95 startup improved 29-34% (30-40% stated elsewhere in the same post). — [Grab engineering](https://engineering.grab.com/docker-lazy-loading)
- SOCI builds a separate index over unmodified OCI images; eStargz and Nydus require converting images to a new format. — [SOCI paper (Amazon Science)](https://cdn.amazon.science/84/96/51ea2e0d40fca8cfaee9328278b9/soci-paper.pdf); [stargz-snapshotter](https://github.com/containerd/stargz-snapshotter)
- Motivation for lazy pulling: research cited in the Kata/Nydus design doc found pulling accounts for 76% of container start time while only 6.4% of the data is read. — [Kata Nydus design doc](https://github.com/csynineyang/kata-containers/blob/main/docs/design/kata-nydus-design.md)
- Nydus vendor figures: Alibaba serverless pull time from 20 s to 0.8 s; Kuaishou reports over 90% pull-time savings and over 70% registry bandwidth reduction with Dragonfly P2P plus Nydus. (2022-2023, vendor-reported) — [Dragonfly image service site](https://dragonflyoss.github.io/image-service-website); [Alibaba Cloud blog on Kuaishou](https://www.alibabacloud.com/blog/image-pulling-saves-over-90%25-the-large-scale-distribution-practice-of-kuaishou-based-on-dragonfly_599854)
- An unverified 2026 preprint reports SOCI cutting a 1.3 GB Python image cold pull from 20 s to about 2.8 s, and that lazy loading wins below about 80% access density while parallel full pull is faster above it. — [arXiv 2607.06868](https://arxiv.org/abs/2607.06868)
- Snapshot instead of rebuild: Modal's `snapshot_filesystem()` turns a running sandbox into a reusable image; E2B, Freestyle and Beam capture memory as well; Morph claims sub-250 ms branch/restore. — [Modal snapshots guide](https://modal.com/docs/guide/sandbox-snapshots.md); [Upstash](https://upstash.com/blog/ai-agent-sandbox-providers-compared-2026); [Morph Cloud docs](https://cloud.morph.so/docs/developers)

### Inferences
- For Fleet the ordering of wins is likely: (1) per-repository shared layers with thin per-commit layers; (2) persistent builders with local cache and cache mounts for pip/uv/apt download caches, co-located with a package mirror; (3) a registry in the same region as the workers; (4) lazy pulling last, and only SOCI-style (no conversion), since test runs touch most of the environment and Grab saw eStargz make startup 5x slower.
- A filesystem snapshot of a "dependencies installed, tests passing" container is functionally an environment image without a Dockerfile; on hosted sandboxes this replaces the build stage, at the cost of provider lock-in and snapshot retention limits (Modal memory snapshots expire in 7 days, so only filesystem snapshots are suitable as durable artifacts).
- Content-addressing the environment by a hash of (base image digest, dependency manifest files, install recipe) rather than by commit SHA would let many commits of one repository share an environment image; this is the same idea as SWE-bench's roughly 60 environment images serving 2,294 instances.

### Gaps
- No case study with numbers was found for Bazel remote execution/remote cache or Nix binary caches applied to benchmark-environment construction; the search returned nothing usable.
- No reproducible cache hit-rate figures for hosted build services (Depot, Blacksmith); one listicle gave self-contradictory numbers and was discarded.
- No measurement of AWS SOCI on Fargate launch-time improvement from a primary source.
- No sourced comparison of copy-on-write backends (overlayfs versus devicemapper thin pools versus ZFS/btrfs) for sandbox density.

## 4. Network policy for builds versus tests

### Takeaway
The documented practice is default-deny egress with explicit allowlists enforced at both DNS and IP layers, plus internal package and image mirrors; domain allowlists alone are bypassable (DNS tunnelling, domain fronting, tools ignoring proxy variables). Historical-commit installs can now be pinned by upload date natively in uv and pip.

### Cited Findings
- Nebius ran internal mirrors of PyPI, Docker Hub and Ubuntu archives for its build and validation jobs, introduced after hitting public registry rate limits. (Nov 2025) — [Nebius](https://nebius.com/blog/posts/infrastructure-behind-swe-rebench)
- uv's `--exclude-newer` makes the resolver ignore package versions uploaded after a timestamp (RFC 3339 or date); it can also be set through `UV_EXCLUDE_NEWER` or project configuration, and `exclude-newer-package` can exempt individual packages. `uv python install` has no such cutoff. — [pydevtools handbook](https://pydevtools.com/handbook/how-to/how-to-use-exclude-newer-for-reproducible-python-environments/)
- pip 26.0 (January 2026) added `--uploaded-prior-to` for the same purpose. — [pydevtools: dependency cooldown](https://pydevtools.com/handbook/explanation/what-is-a-dependency-cooldown/)
- Both flags need upload-time metadata from the index; JFrog Artifactory (7.132.x+) documents how to expose it so the cutoff works through a proxy. — [JFrog: upload-date filtering for uv and pip](https://jfrog.com/help/r/artifactory-how-to-support-upload-date-based-package-filtering-for-uv-and-pip-artifactory-7-132-x); [JFrog: time-based filtering for PyPI repositories](https://jfrog.com/help/r/artifactory-enabling-time-based-package-filtering-for-pypi-repositories)
- Recommended sandbox network design: default-deny egress, explicit allowlists, DNS controls, private networking, per-tenant policies; IP rules are weak if the sandbox can use another resolver. — [Northflank: networking for agent sandboxes](https://northflank.com/blog/how-to-design-networking-for-secure-ai-agent-sandboxes)
- Alibaba OpenSandbox's egress sidecar uses a DNS proxy that returns NXDOMAIN for denied names plus an nftables layer, which it says is required to enforce IP/CIDR rules and block DoH/DoT. — [OpenSandbox egress docs](https://www.mintlify.com/alibaba/OpenSandbox/components/egress)
- Vercel documents that SNI-based domain allowlisting can be bypassed by domain fronting (allowlisted SNI with a different Host header). — [Vercel Sandbox firewall docs](https://vercel.com/docs/sandbox/concepts/firewall)
- Blaxel warns that proxy-based domain filtering depends on tools honouring the standard proxy environment variables, and recommends injecting credentials at the proxy so untrusted code never holds them. — [Blaxel: egress control](https://blaxel.ai/blog/sandbox-egress-control-outbound-allow-listing)
- DNS is an exfiltration channel even with allowlists (e.g. a post-install script encoding secrets into subdomain lookups). — [lavx.hu write-up](https://news.lavx.hu/article/why-network-allow-lists-alone-can-t-prevent-credential-exfiltration)
- One practitioner test of 24 exfiltration payloads against a deny-by-default sandbox (Tensorlake) reported 21 blocked: 16 at DNS resolution, 5 at the egress proxy. — [Towards AI](https://pub.towardsai.net/i-tested-24-exfiltration-attacks-against-a-locked-down-ai-sandbox-4604052cc582)
- Modal exposes `block_network` and a CIDR-based `outbound_cidr_allowlist` per sandbox. — [modal.Sandbox reference](https://modal.com/docs/reference/modal.Sandbox.md)
- Git history in the image is itself a leak path for task solutions. (Jul 2025) — [Epoch AI blog](https://epoch.ai/blog/swebench-docker)

### Inferences
- A two-phase policy fits Fleet: the build phase gets egress only to an internal registry proxy (PyPI, npm, apt, Docker Hub mirror) with no secrets present in the build sandbox; the test and agent phases run with networking fully off, as today. The mirror also removes rate limits and makes builds faster and repeatable.
- For commits older than their dependencies' latest releases, setting the resolver cutoff to the commit date (`UV_EXCLUDE_NEWER=<commit timestamp>` or pip's `--uploaded-prior-to`) is now a first-class alternative to a dedicated time-travel index, provided the mirror passes through upload-time metadata. Unpinned system packages (apt) and Python interpreter builds are not covered by this mechanism.
- Because install scripts execute arbitrary code at build time, build sandboxes need the same isolation level as test sandboxes; network allowlisting does not substitute for it.

### Gaps
- No source was found describing the build-phase-online / test-phase-offline split as practised by SWE-bench-style pipelines specifically, nor quantitative results for devpi or time-travel PyPI snapshot services (the `pypi-timemachine` search returned nothing).
- No data on how often historical-commit installs fail without a date cutoff versus with one.
- Equivalent date-cutoff mechanisms for npm, cargo, Go modules and apt were not researched.

## 5. Pipeline orchestration

### Takeaway
Sources separate durable-execution engines (Temporal, plus Inngest, Restate, DBOS, Hatchet, Trigger.dev) from data/DAG orchestrators (Airflow, Prefect, Dagster, Argo Workflows). Temporal is the engine most often named for production agent workloads (Replit, OpenAI integrations), but its per-workflow history and payload limits mean large artifacts must live outside it; the one detailed benchmark-construction account (Nebius) used a MapReduce-style batch platform plus Kubernetes with Airflow run IDs, not a durable-execution engine.

### Cited Findings
- A widely repeated 2026 taxonomy puts Temporal, Inngest, Trigger.dev, Hatchet, Restate and DBOS in a durable-execution category, and Airflow, Prefect, Dagster and Argo Workflows in a batch/DAG category described as a different class of tool. (Secondary source; the original page could not be fetched.) — [youngju.dev, May 2026](https://www.youngju.dev/blog/culture/2026-05-15-workflow-engines-2026-temporal-inngest-trigger-hatchet-restate-dbos-airflow-deep-dive.en)
- Temporal Cloud limits: 51,200 events or 50 MB per workflow execution history (warnings at 10,240 events or 10 MB); 2 MB per request payload; 4 MB per history transaction and per gRPC message; 2,000 incomplete activities/child workflows/signals per workflow execution (500 or fewer recommended); default 500 actions per second per namespace; retention 1-90 days (default 30). Temporal recommends external storage for regularly large payloads. — [Temporal Cloud limits](https://docs.temporal.io/cloud/limits)
- Temporal Cloud pricing (page undated, read November 2026): pay-as-you-go has no base fee; actions cost $50 per million for the first 5M per month, stepping down to $25 per million at 100-200M; the Business plan is $500/month minimum with 2.5M actions included; storage overage is $0.042 per GB-hour active and $0.00105 per GB-hour retained. — [Temporal pricing](https://temporal.io/pricing)
- Replit chose Temporal Cloud and the Python SDK to run the Replit Agent control plane, replacing custom infrastructure (Temporal-published case study). — [Temporal: Replit case study](https://temporal.io/resources/case-studies/replit-uses-temporal-to-power-replit-agent-reliably-at-scale)
- Temporal and OpenAI shipped an OpenAI Agents SDK integration (public preview, July 2025) and, in April 2026, a Temporal extension for sandbox agents in OpenAI's `openai-agents-python` repository. Temporal lists OpenAI, Replit and Lovable as agent-building users (vendor-reported). — [Temporal: Agents SDK integration](https://temporal.io/blog/announcing-openai-agents-sdk-integration); [Temporal: agentic sandboxes](https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk); [Temporal AI page](https://temporal.io/ai)
- Temporal published an account of running the Pi coding agent on Temporal (October 2026). — [Temporal blog](https://temporal.io/blog/the-immortal-life-of-pi-running-the-pi-coding-agent-on-temporal)
- Comparison blogs describe Prefect as the Python data-pipeline choice, Dagster as asset-based with lineage and observability, and Argo Workflows as Kubernetes-native, container-per-step DAGs; a competitor (Diagrid) argues Argo is optimized for containerized task workflows rather than dynamic agents. — [automationatlas.io, March 2026](https://automationatlas.io/answers/temporal-vs-prefect-2026/); [Diagrid FAQ](https://www.diagrid.io/faq/workflow-engine-alternatives/how-does-diagrid-catalyst-compare-to-argo-workflows-for-ai-agents)
- Nebius (Nov 2025): data collection, image builds, evaluation and trajectory storage ran as map jobs on TractoAI with fair-share scheduling; agent runs were Kubernetes pods labelled with an `airflow-run-id`; individual operations were capped at 100 tasks, so datasets were split across operations in a thread pool; whole-task retry was the recovery strategy when pods vanished. — [Nebius](https://nebius.com/blog/posts/infrastructure-behind-swe-rebench)
- Harbor's model is a job consisting of independent trials run with bounded concurrency (`-n`) against a pluggable environment backend. — [Harbor on PyPI](https://pypi.org/project/harbor/0.1.16/)
- Epoch AI's approach to SWE-bench Verified is a single large VM with pre-built images and high concurrency, finishing in about an hour with no orchestrator beyond the eval harness. (Jul 2025) — [Epoch AI blog](https://epoch.ai/blog/swebench-docker)

### Inferences
- Fleet's stages (candidate filter, environment build, test validation, agent review) are coarse, idempotent, and keyed by content (repository, commit, recipe hash). That shape matches the batch pattern the benchmark builders actually used: a work queue, bounded-concurrency workers, an artifact store keyed by content hash, and whole-stage retry. A durable-execution engine adds value mainly where a single unit of work is long, stateful and multi-step, such as an agent loop that should survive worker loss mid-run.
- If Temporal is adopted, per-repository or per-commit workflows with activities per stage fit within its limits only if logs, diffs and transcripts are stored externally and referenced by key (2 MB payload cap), and fan-out is batched through child workflows (2,000 pending cap; 51,200-event history cap).
- The Nebius failure list (Pending deadlocks, zombie pods, vanished pods, lost logs) is a checklist for any Kubernetes-jobs design: every state transition needs a timeout, every resource needs an owner label for garbage collection, and logs must be shipped off-cluster at write time.
- Skip-if-output-exists on a content-addressed store gives resumability without an engine; the engine question is then about visibility, rate limiting and retries rather than correctness.

### Gaps
- No primary-source, quantitative comparison of Temporal, Prefect, Dagster, Airflow, Ray and Argo for this workload was found; available comparisons are vendor or aggregator content.
- How many billable Temporal actions a Fleet-style task would generate was not measured; each activity, timer, signal and child workflow start counts, so the per-task cost depends on stage granularity.
- Nothing was found on Ray (Ray Data / Ray jobs) for sandboxed build-and-test fan-out, or on DBOS/Postgres-queue designs with measured results.
- No independent (non-Temporal) account of what Replit, OpenAI or other agent companies run, and no account of orchestration choices from SWE-smith, SWE-Gym or R2E-Gym.
- One aggregator claimed Prefect acquired Dagster; this could not be corroborated and should not be relied on.

## 6. Cost control and observability for LLM calls

### Takeaway
The documented levers are batch APIs (50% off, asynchronous), prompt caching (cache reads at about 10% of input price on Anthropic), hard per-run token/step/dollar caps, and a gateway that enforces budgets per key while exporting traces. Benchmark builders report very low per-task LLM costs when cheap deterministic stages run before LLM stages.

### Cited Findings
- Anthropic's Batch API gives a 50% discount on input and output tokens for asynchronous processing, and the discount stacks with prompt caching. — [Anthropic pricing docs](https://docs.anthropic.com/en/docs/about-claude/pricing)
- Anthropic cache reads are priced at about one tenth of base input (cache writes at 1.25x for the 5-minute tier and 2x for the 1-hour tier), per a third-party pricing table. — [Morph: Anthropic API pricing 2026](https://www.morphllm.com/anthropic-api-pricing)
- OpenAI's Batch API is reported at the same 50% discount with up to a 24-hour completion window and up to 50,000 requests per batch (third-party guide). — [Respan guide](https://www.respan.ai/articles/anthropic-message-batches-api)
- Sources conflict on OpenAI's cached-input discount (one comparison says about 50% versus Anthropic's 90%) and on Anthropic batch turnaround (one outlier says 72 hours; most say 24). — [developersdigest.tech](https://www.developersdigest.tech/blog/how-to-cut-llm-api-bill-2026)
- Per-run caps in practice: SWE-smith capped expert trajectories at 75 steps and $2.00 per run, and environment-setup agents at 150 calls (average $0.72 per repository). Epoch capped SWE-bench samples at 300,000 tokens (1M in its Benchmarking Hub). — [SWE-smith paper](https://arxiv.org/html/2504.21798v2); [Epoch AI blog](https://epoch.ai/blog/swebench-docker)
- SWE-smith's whole 50k-task dataset cost about $1,360 in LLM spend, because two of five generation strategies are procedural (zero LLM cost) and the average candidate costs 2.32 cents. (2025) — [SWE-smith paper](https://arxiv.org/html/2504.21798v2)
- Epoch's evaluations used 100-150M tokens each, "mostly cached", at about 2M tokens per minute, and needed high provider rate limits. (Jul 2025) — [Epoch AI blog](https://epoch.ai/blog/swebench-docker)
- OpenHands reported about $0.30 per SWE-bench issue with Claude 3.5 Sonnet and noted prompt caching greatly reduced agent costs. (Oct 2024) — [OpenHands blog](https://openhands.dev/blog/evaluation-of-llms-as-coding-agents-on-swe-bench-at-30x-speed)
- LiteLLM proxy supports virtual keys with `max_budget` and `budget_duration`, team-level and global budgets, soft-budget alerts, and rejects requests once a key's budget is exceeded; it has built-in callbacks for Langfuse, OpenTelemetry, Datadog, Helicone and MLflow. — [LiteLLM budget alerts docs (mirror)](https://www.mintlify.com/BerriAI/litellm/proxy/budget-alerts); [elest.io write-up](https://blog.elest.io/litellm-stop-burning-money-on-llm-apis-virtual-keys-cost-tracking-and-guardrails/)
- LiteLLM versions 1.82.7 and 1.82.8 were reported as affected by a March 2026 supply-chain incident (secondary source). — [dev.to guide](https://dev.to/jangwook_kim_e31e7291ad98/litellm-one-proxy-for-140-llms-setup-cost-guide-1i71)
- Sandbox prices never include model tokens. — [Upstash](https://upstash.com/blog/ai-agent-sandbox-providers-compared-2026)

### Inferences
- Fleet's non-interactive LLM stages (candidate triage, issue/description generation, rubric review) fit the batch APIs and can take the 50% discount; interactive agent loops inside a sandbox cannot use batch, but benefit most from prompt caching because each turn resends a growing prefix.
- Budget enforcement works best at three levels: a per-call token cap, a per-task step and dollar cap (as SWE-smith and Epoch do), and a per-stage or per-run budget on a gateway key so a runaway stage halts itself rather than the whole pipeline.
- Ordering stages cheapest-first (static filters, build, test) before any LLM call is the main cost control demonstrated by SWE-smith's economics; the 14% candidate-to-task yield at Nebius shows how much spend an LLM-first ordering would waste.
- Provider rate limits (tokens per minute), not sandbox capacity, are likely to set the ceiling on agent-stage concurrency; Epoch needed about 2M tokens per minute for one 500-task run.

### Gaps
- Official OpenAI pricing for batch and cached input was not verified from a primary page.
- Nothing sourced was found on OpenTelemetry GenAI semantic conventions adoption or on Langfuse/Phoenix/Braintrust usage by the benchmark-construction projects.
- No published per-task LLM cost for SWE-rebench V2, R2E-Gym or SWE-Gym environment synthesis was found.
- Current model list prices were not collected; any dollar projections should be recomputed from the provider pricing pages at the time of the run.
