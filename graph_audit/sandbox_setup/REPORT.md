# Local sandbox: Colima + gVisor (2026-11-21)

**Machine:** Apple-silicon Mac, macOS 26.0.1, 8 GB RAM, 8 cores, about 28 GB free disk.
**Chosen:** Colima (open source, Docker-compatible) running a Linux VM, with gVisor (`runsc`) as the container runtime for test runs.

## Why this combination

| Layer | What it isolates | Why |
|---|---|---|
| Colima VM (macOS Virtualization.framework, arm64) | everything from macOS | Free, CLI-only, VM size is configurable (needed with 8 GB of RAM), and Fleet's `docker` calls work unchanged. Native arm64, so no amd64 emulation |
| gVisor `runsc` | each test container from the VM's kernel | Plain runc shares the kernel with the code under test. Hosted agent sandboxes treat a user-space kernel (gVisor) or a microVM as the minimum for untrusted code |

Docker Desktop is heavier on memory, and OrbStack needs a paid licence for business use. Both would also work: Fleet only calls the `docker` CLI.

## Setup, reproducible

```bash
brew install colima docker docker-buildx
```

```bash
printf '{\n  "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]\n}\n' > ~/.docker/config.json
```

```bash
colima start --cpu 4 --memory 4 --disk 20 --vm-type vz --arch aarch64
```

Install gVisor inside the VM (from gVisor's apt repository):

```bash
colima ssh -- bash -lc 'curl -fsSL https://gvisor.dev/archive.key | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | sudo tee /etc/apt/sources.list.d/gvisor.list && sudo apt-get update && sudo apt-get install -y runsc'
```

Register it through **Colima's** config, not Docker's `daemon.json`, which Colima rewrites on restart. In `~/.colima/default/colima.yaml`, replace `docker: {}` with:

```yaml
docker:
  runtimes:
    runsc:
      path: /usr/bin/runsc
```

Then restart Colima and check with Fleet:

```bash
colima restart
```

```bash
fleet doctor
```

It should print `Sandbox runtime: runsc`. Installed versions: Colima 0.10.3, Docker CLI 29.10.0, Docker engine 29.5.2 (in the VM), buildx 0.39.1, gVisor `release-20261116.0`.

## Measured: runc against gVisor

Each suite ran three times per runtime with exactly the validator's flags: `--network=none`, `--read-only`, `--cap-drop=ALL`, `no-new-privileges`, `--memory=2g --memory-swap=2g`, `--cpus=2`, `--pids-limit=256`, non-root user, read-only code mount, `--init`.

| Suite | Runtime | Result (all 3 runs) | pytest time | Wall time per container |
|---|---|---|---|---|
| pluggy | runc | 144 passed | 0.26-0.28 s | 0.7-1.0 s |
| pluggy | gVisor | 144 passed | 0.36 s | 0.9-1.0 s |
| glom | runc | 201 passed, 1 failed | 0.74-0.75 s | 1.2-1.5 s |
| glom | gVisor | 201 passed, 1 failed | 0.92-0.96 s | 1.6 s |

Per-test outcomes were **identical across all six runs** of each suite, including glom's one failing test (it fails under both runtimes, so it reflects the dependency versions, not the runtime). Cost of gVisor: about 25-35% more test time and 0.3-0.4 s more per container. All validator flags are accepted.

## What Fleet now does

- `validation.sandbox_runtime()` picks `runsc` whenever Docker lists it, else `runc`. `FLEET_SANDBOX_RUNTIME=runc|runsc|auto` overrides. An explicit `runsc` that Docker lacks fails at launch rather than falling back. Each validation records its `sandbox_runtime`; the runtime reviewer uses the same choice.
- Checkouts are created inside the evidence folder, not `$TMPDIR`. **Colima shares only the home folder with its VM**, so mounts from `/var/folders` (macOS's temp folder) fail before a test starts. Docker Desktop shares more, which is why this never showed earlier.
- `fleet doctor` reports the runtime and warns when the Colima context is in use.

## Limits

- Two small suites on one laptop. gVisor's worst case is syscall-heavy work (many small files, subprocesses), so heavier suites may slow down more.
- Image builds (`docker build`) still use runc and have network access to install packages. Only test runs use gVisor and have no network.
- Two image builds failed once with pip exit code 2 and succeeded on retry with no change. Most likely a transient network error, but unconfirmed.
- With 4 GB in the VM and 2 GB per test container, at most one or two validations run at a time.
