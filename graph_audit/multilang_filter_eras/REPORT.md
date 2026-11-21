# Filter and eras across C++, Python and Go (2026-11-21)

Three new repositories chosen to differ from each other and from pytest and Django, each run through the whole offline pipeline (hard gates → signals → eras) over its last 400 mainline changes. **No Docker, no LLM, no API calls**: nothing here installs or runs a repository's code. The harness is `../initial_filter_pytest/run.py`; per-repository totals and every era are in `summary.json`.

| Repository | Language | Why |
|---|---|---|
| [fmtlib/fmt](https://github.com/fmtlib/fmt) | C++ | CMake, no package manager, tests in a singular `test/` |
| [pallets/flask](https://github.com/pallets/flask) | Python | pinned `requirements/*.txt`, later `uv.lock`; merges from `stable` |
| [gin-gonic/gin](https://github.com/gin-gonic/gin) | Go | `go.mod`/`go.sum`, frequent dependabot bumps |

## What had to be built first

The filter and era logic were Python-only in three places:

1. **The comment-only check compared Python syntax trees only**, so a comment-only edit in Go or C++ always counted as code. It now compares tree-sitter token streams with comments removed, for Go, C, C++, Rust, Java, JavaScript and TypeScript (`history/content.py`). This can only over-keep: a misparse turns comments into tokens and makes the files differ.
2. **C/C++ build files were misfiled.** `CMakeLists.txt` counted as documentation (it ends in `.txt`), and `conanfile.py` and `.cmake` modules counted as source. They are now build configuration. Go's `testdata/` folder now counts as tests.
3. **Eras knew only Python declarations.** They now also read `go.mod` (comments stripped) and `go.sum`; the dependency calls in the root `CMakeLists.txt` and `meson.build` (not their source lists); Conan and vcpkg manifests; Meson wraps; and git submodules at their pinned commits. Each era also records its toolchain: Go's `go` directive, CMake's minimum version and C++ standard, or Python via the existing resolver.

## Filter results

| | fmt | flask | gin |
|---|---|---|---|
| Kept | 235 | 175 | 175 |
| Rejected: no source change | 145 | 195 | 199 |
| Rejected: comment/docstring-only source edit | 17 | 26 | 25 |
| Rejected: other | 3 renames with no line changes | 4 empty merge diffs | 1 empty diff |

**Wrong drops found: one, now fixed.** gin's `7a1b65507` "fix: sonic on arm64" changed only `//go:build` lines. To the Go grammar these are comments, but they decide which files are compiled. Directive comments are now kept as code: `//go:`, `// +build`, `//line`, `//export`, every comment in a cgo file, TypeScript's `@ts-` and `/// <reference>`, and `#__PURE__`. gin kept went from 174 to 175.

Every other reject category was checked:
- all "no source change" rejects that touched code-looking files are test-only commits (`test/*.cc`, `*_test.go`, `tests/*.py`) or docs configuration;
- the comment-only rejects that were read by hand are comment, docstring or blank-line edits in source, with any substance in tests.

**Leaks and mislabels on the kept side:**

- **Messages contradicted by the diff.** gin labels real behaviour changes `chore(...)`, for example "chore(context): always trust xff headers from unix socket" and "chore(response): prevent Flush() panic". A `chore`/`ci`/`docs`/`test` message on a change whose source code changed is now `unknown` (`message_contradicted_by_diff`), not `other`. gin's "other" fell from 47 to 7.
- **Maintenance-branch merges.** 56 of flask's kept changes are merges from `stable` or `X.Y.x`. Each brings several fixes forward in one diff, so as tasks they are bundled. They are now labelled `maintenance_merge`. The better candidates are the individual commits on the maintenance branch itself, which a mainline walk never visits.
- **Unlabelled fixes.** 83 fmt changes have no recognisable wording, for example "Don't lose data on short writes in fmt::ostream". fmt has no changelog fragments or versioned release notes. These stay `unknown` and are kept, as agreed.

## Era results

| Repository | Kept changes | Eras (images) | Resolution | Toolchain read |
|---|---|---|---|---|
| fmt | 235 | **1** | system packages | CMake `3.8...3.28` |
| flask | 175 | 67 (was 74) | 57 date-pinned, 10 locked | Python 3.9 → 3.13, then "needs review" |
| gin | 175 | 65 | all locked (`go.sum`) | Go 1.18 → 1.26.0 |
| pytest | 189 | 7 (was 10) | 3 date-pinned, 4 locked | Python 3.14 |
| Django | 199 | 6 | all date-pinned | needs review (`>= 3.12` only) |

**Is the splitting correct?** For every era boundary, the dependency file that changed was identified:

- **gin:** all 64 boundaries are real `go.mod`/`go.sum` changes, 61 of them dependabot version bumps. Each era really needs a different exact module set.
- **fmt:** one era. It declares no dependencies beyond the compiler.
- **flask** was over-split by files the test run never installs: `docs.txt`, `typing.txt`, `dev.txt`, `build.txt`, and `tox.ini` command edits. Two fixes:
  - when a repository has a test-specific requirement list, side lists (docs, typing, lint, build, dev) are ignored;
  - only `tox.ini`'s install settings count.

  The remaining 66 boundaries are real: `requirements/tests.txt` is pinned exactly and bumped about monthly (24), plus `uv.lock`, `setup.py` and `pyproject.toml` dependency edits.

**What would cut images further.** Genuinely different version sets can still often share an image: a test suite usually runs fine one minor bump away. Two options, neither built yet, and both need Docker:

1. **Measured merging.** Run an older commit in the next era's image; if its base run passes, merge the two eras.
2. **Shared module caches for Go.** Go's module cache holds many versions side by side, so one image per Go toolchain version can pre-download every era's modules.

## Limits

- Five repositories, 400 recent changes each, history read offline only.
- No image was built and no test was run. "Locked", "date-pinned" and the toolchain versions are what the declarations say, not what an install produced.
- Requirement lists deeper than the root or `requirements/` are not tracked, nor are `CMakeLists.txt` files below the root (fmt's `test/CMakeLists.txt` declares its test dependencies).
- The toolchain is recorded, not yet used to pick the base image.
