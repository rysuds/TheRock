# Report: ROCm/TheRock#8558: Some ASAN linux native meta packages missing device package dependency dependencies

> Committed as `reports/report-8558.md` in a separate final commit on the pushed fork branch, as
> instructed for this task (the template's "local only" note does not apply here).

## Issue

- Link: https://github.com/ROCm/TheRock/issues/8558
- Type / skill used: `packaging-native-linux` (`skills/packaging-native-linux.md`), on top of the
  repo-wide `common.md` playbook. `rocm-gpu-targets-and-arch.md` (target-ID rules),
  `rocm-packaging-and-install.md` (meta vs device packages) and `rocm-triage-playbook.md` were used
  as background.
- Summary: In full-ASAN native Linux packaging (both DEB and RPM), the versioned meta packages
  `amdrocm-{rand,solver,hiptensor,rocalution}-asan10.x` depend only on their `-host` package and not
  on the `-gfx942`/`-gfx950` device packages that are built right next to them. `apt install amdrocm-rand-asan` therefore installs the host libraries without any GPU kernels. The same drop
  cascades: device packages of dependents lose their same-target dependency (`blas` → `solver`,
  `rocalution` → `rand`), per-target metapackages lose whole libraries (`amdrocm-core-asan…-gfx942`
  loses rand and solver; `amdrocm-hpc-asan…-gfx942` ends up with an empty `Depends:`), and six ASAN
  `-test` meta packages are affected too. Expected behavior, as documented in
  `docs/packaging/nativepackage_dependency_tree.md`: a meta package depends on its host package plus
  every device package that has content, and device/per-target packages depend on the same-target
  device packages of their gfxarch dependencies.
- Related open PRs/issues and how this work relates:
  - [ROCm/TheRock#8560](https://github.com/ROCm/TheRock/pull/8560) (open, by the issue author):
    same core idea (add the `{base}:*` glob to `has_artifact_for_arch()`), no tests (the PR bot
    flags the missing unit test), test plan is a manual CI rebuild. **Relation: alternative that
    subsumes it.** Its dependency output is identical to this branch's (Validation #13: this
    branch's new tests pass against #8560 except one ordering assertion, and real packages built
    with #8560 have identical `Depends` fields and `Requires` sets). This branch adds a single shared helper
    so the two code paths cannot drift again, deterministic variant ordering, and the regression
    tests. If maintainers prefer to land #8560, the tests from `1fc939b4` apply on top of it
    unchanged except the ordering assertion in `test_device_payload_found_for_each_layout` (drop
    it, or add `sorted()` to the glob). Reviewed read-only; nothing was posted.
  - [ROCm/TheRock#6098](https://github.com/ROCm/TheRock/pull/6098) (merged, commit `66b05f26`):
    added the xnack-variant glob to `filter_components_fromartifactory()` only. This is where the
    two lookups diverged; `has_artifact_for_arch()` came from
    [#3561](https://github.com/ROCm/TheRock/pull/3561) and was never updated.
  - [ROCm/TheRock#7290](https://github.com/ROCm/TheRock/issues/7290) (open): colons in ASAN kpack
    names break Bazel consumers. If the `:` separator is ever changed, the new helper is the one
    place to update (see Risks).

## Root cause

The two functions that decide "does package P have device content for target T" disagree
(links at base `20abb1e96819fe8a85935db7e329dd8a69396303`):

- [`filter_components_fromartifactory()` L1063-L1067](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/packaging/linux/packaging_utils.py#L1063-L1067)
  builds the **device package contents** from `{name}_{component}_{target}` **and**
  `{name}_{component}_{target}:*` (e.g. `rand_lib_gfx942:xnack+`).
- [`has_artifact_for_arch()` L1208-L1214](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/packaging/linux/packaging_utils.py#L1208-L1214)
  only checks the plain `{name}_{component}_{target}` directory. It is the single source of truth
  for which device packages get depended on:
  - [`filter_archs_with_artifacts()`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/packaging/linux/packaging_utils.py#L1237-L1281)
    → [`expand_kpack_meta_dependencies()`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/packaging/linux/packaging_utils.py#L507-L538)
    (versioned meta package = host + devices) and `expand_metapackage_to_all_archs()`.
  - [`filter_dependencies_by_artifacts()`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/packaging/linux/packaging_utils.py#L1284-L1327)
    → [`process_main_dependencies_kpack()`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/packaging/linux/packaging_utils.py#L647-L735)
    for per-target metapackages (L675-L683) and device packages (L712-L731).
- The target list itself is fine: `build_package.py`
  [L120-L121](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/packaging/linux/build_package.py#L120-L121)
  strips `gfx942:xnack+` → `gfx942`, so both functions are asked about `gfx942`.

Why some components exist **only** as `:xnack+` directories:

- `cmake/therock_sanitizers.cmake`
  [L95-L97](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/cmake/therock_sanitizers.cmake#L95-L97)
  rewrites `GPU_TARGETS` `gfx942`/`gfx950` → `gfx942:xnack+`/`gfx950:xnack+` for ASAN/TSAN.
- The kpack splitter (`rocm-systems` @ `0ad5f73`, the pinned submodule) keys **fat-binary kernels
  by the full target ID**
  ([artifact_splitter.py L373-L409](https://github.com/ROCm/rocm-systems/blob/0ad5f732536450ea7659aba61226e2d9d94917dd/shared/kpack/python/rocm_kpack/artifact_splitter.py#L373-L409);
  the features are stripped only for the `gpu_targets` filter) and names the output
  `{prefix}_{arch}` ([L462-L464](https://github.com/ROCm/rocm-systems/blob/0ad5f732536450ea7659aba61226e2d9d94917dd/shared/kpack/python/rocm_kpack/artifact_splitter.py#L462-L464)).
  **Kernel database files** (rocBLAS, hipBLASLt, hipSPARSELt, MIOpen, aotriton, hipkernelprovider,
  hotswap cache handlers) are keyed by the bare arch
  ([L113-L122](https://github.com/ROCm/rocm-systems/blob/0ad5f732536450ea7659aba61226e2d9d94917dd/shared/kpack/python/rocm_kpack/artifact_splitter.py#L113-L122)).
  So a component with kernel-database files gets a plain directory (blas and sparse get both
  forms), while components whose device code is only in fat binaries, such as rand, solver,
  hiptensor and rocalution, get only the `:xnack+` directory. I didn't investigate why the fft,
  MIOpen and rccl libraries were plain-only in the run below; it doesn't affect this bug.

Evidence from a real upstream ASAN run, [Multi-Arch Release ASAN (dev) | Linux: gfx94x, run
36142888996](https://github.com/ROCm/TheRock/actions/runs/36142888996) (2026-09-25, success), job
"Linux::asan / Build DEB Packages / Build deb packages":

| gfx942 artifact layout in that run | Artifacts                                                                                                          |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| plain only                         | `fft_lib`, `miopen_lib`, `rccl_lib`                                                                                |
| `:xnack+` only                     | `rand_lib`, `solver_lib`, `hiptensor_lib`, `rocalution_lib`, `{blas,rand,rccl,sparse,hiptensor,prim,rocwmma}_test` |
| plain + `:xnack+`                  | `blas_lib`, `sparse_lib`, `fft_test`                                                                               |

- `Auto-detected GFX architectures: ['gfx942']`, then
  `WORKAROUND: amdrocm-{rand,solver,rocalution,hiptensor} missing artifacts for: ['gfx942']` while
  building their meta packages, and the same for `amdrocm-{sparse,blas,rand,hiptensor,rccl,ccl}-test`.
- Cascade lines, attributed to the package being built: `amdrocm-blas` [device gfx942] excludes
  `amdrocm-solver`; `amdrocm-rocalution` [device gfx942] excludes `amdrocm-rand`; `amdrocm-core`
  [gfx942] excludes `amdrocm-rand` and `amdrocm-solver`; `amdrocm-hpc` [gfx942] excludes
  `amdrocm-rocalution` and `amdrocm-hiptensor`; `amdrocm-hpc-tests` [gfx942] excludes
  `amdrocm-hiptensor-test`.
- The device packages **were** built, e.g.
  `amdrocm-rand-asan10.2-gfx942_10.2.0~dev20260925-36142888996_amd64.deb`, so they exist in the repo
  but nothing depends on them.

The CI "Simulated install Test" did not catch this because it runs `apt install --simulate` with
every built `.deb` on the command line, so an unreachable device package is still "installed".

Local repro on `20abb1e9` (real packaging functions on a fake tree) matched this exactly, and the
same tree with plain directories produced all dependencies, so the `:xnack+`-only layout is the
trigger. **The cause is in TheRock** (`build_tools/packaging/linux/packaging_utils.py`). The kpack
naming is intentional (an `:xnack+` code object is a distinct target ID), so nothing upstream
needs to change.

## Fix

- `build_tools/packaging/linux/packaging_utils.py`
  - New `_artifact_dirs_with_variants(artifacts_dir, base_name)`: returns the plain directory
    followed by `sorted(glob(base_name + ":*"))`. The `:` anchor keeps `gfx1250` from claiming
    `gfx1250-strict` (a separate owner member) and sibling/prefix-sharing artifacts.
  - `filter_components_fromartifactory()` uses the helper: same directories as before, and variant
    directories are now visited in sorted order instead of `os.scandir` order.
  - `has_artifact_for_arch()` uses the helper, so it now returns `True` when only a variant
    directory has a matching manifest entry. I dropped its redundant `source_dir.exists()` check
    (the manifest check implies it), and the docstring now states that it must look at the same
    directories as the content collector.
- `build_tools/packaging/linux/tests/build_package_test.py`: new `TargetFeatureVariantDependencyTest`
  (6 tests / 21 subtests) plus helpers `_stage_artifact_dir`, `_dependency_names` and
  `_generated_dependency_names` (real DEB control / RPM spec generation; only `dpkg-buildpackage`,
  `rpmbuild` and the output move are mocked, as in the existing tests).

Key diff:

```python
def _artifact_dirs_with_variants(artifacts_dir: Path, base_name: str) -> list[Path]:
    artifacts_dir = Path(artifacts_dir)
    return [artifacts_dir / base_name, *sorted(artifacts_dir.glob(f"{base_name}:*"))]


# has_artifact_for_arch(): was a single `source_dir = artifacts_dir / base` lookup
for source_dir in _artifact_dirs_with_variants(
    artifacts_dir, f"{artifact_prefix}_{component}_{artifact_suffix}"
):
    manifest_file = source_dir / "artifact_manifest.txt"
    ...
```

Why this approach rather than alternatives:

- **Inline copy of the glob (upstream #8560):** equivalent output, but it keeps two copies of the
  discovery rule. The bug exists because #6098 updated one copy and not the other.
- **Make `has_artifact_for_arch()` return `bool(filter_components_fromartifactory(...))`:** the
  strongest single source of truth, but it changes semantics for the `gfx_arch=""` and `GFX_HOST`
  callers (e.g. `Artifact_Gfxarch=False` artifacts would start counting for `""`). That entangles
  a separate bug (Risks #1), so I rejected it for this issue.
- **Loosen the glob to `{base}*`:** would make `gfx1250` claim `gfx1250-strict` payloads and break
  owner-member selection (`SharedOwnerPackagingTest`).
- **Rename the directories in the kpack splitter:** out of TheRock's scope and tied to the
  runtime's kpack naming (#7290).

Behavior change: only kpack builds where a gfxarch package's device payload exists solely in
feature-variant directories (full ASAN/TSAN today, or any explicit `:xnack±`/`:sramecc±` target ID)
gain dependencies. Release builds are unchanged: the same package set is produced (32 DEB / 33 RPM
in the end-to-end run, identical before and after), the blas/fft controls keep identical
dependencies, and the full `build_tools` suite is unchanged. No package names or versions change.
Windows is not affected (separate MSI packaging code).

## Branch

- Base: `main` @ `20abb1e96819fe8a85935db7e329dd8a69396303` (`20abb1e9` "bump libhipcxx (#8448)")
- Branch name: `cursor/asan-meta-xnack-device-deps-8331`, pushed to
  https://github.com/rysuds/TheRock only (not to ROCm/TheRock); no pull request opened. The cloud
  agent environment requires `cursor/<name>-8331` branch names, so the repo's
  `users/<name>/<description>` convention was not used.
- Commits:
  - `1fc939b4` test(packaging): cover device payloads that exist only as :xnack+ artifacts
  - `cf27dafc` fix(packaging): count :xnack+ artifact dirs when resolving device deps
  - (this commit) docs: add report for TheRock issue 8558
- Commit messages avoid `#NNNN`/URL references to upstream, so pushing to the public fork creates no
  cross-reference events on ROCm/TheRock issues or PRs. The tests commit was reworded once, before
  its first push, for this reason; its content is unchanged.
- Authored with an AI coding agent (Cursor); per CONTRIBUTING.md the human submitter remains
  accountable.

## Validation performed in the cloud agent environment

Environment: Linux x86_64 (Ubuntu 24.04.4, kernel 6.12), no AMD GPU, Python 3.12.3 (venv),
pytest 9.0.3 (the `requirements-test.txt` pin), pre-commit 4.6.2 (black 25.11.0), CMake 3.28.3 and
ninja 1.11.1 (only for existing CMake-driven unit tests), dpkg-deb 1.22.6, rpmbuild 4.18.2,
apt 2.8.3, bandit 1.9.4. To mirror `multi_arch_build_native_linux_packages.yml` I installed `rpm`,
`debhelper`, `build-essential`, `dpkg-dev`, `patchelf` and `pyelftools`, plus `fakeroot` and
`ninja-build`.

| #   | Command (exact)                                                                                                                                                                                                                                                                                                                                                                                                                                                | Result                                                                                                                                                                                                           | Notes                                                                                                                                                                                                                                                  |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | at `20abb1e9`: `cd build_tools && python -m pytest packaging/linux/tests -q -p no:cacheprovider`                                                                                                                                                                                                                                                                                                                                                               | 277 passed, 3 skipped, 83 subtests passed                                                                                                                                                                        | baseline; the skips are runpath tests needing gcc/readelf/patchelf (installed later)                                                                                                                                                                   |
| 2   | at `20abb1e9`: `python /tmp/repro_8558.py` and `python /tmp/repro_8558_cascade.py` (throwaway scripts calling the real packaging functions on a fake tree)                                                                                                                                                                                                                                                                                                     | rand has device content for gfx942/gfx950 but `has_artifact_for_arch()` is `False`; meta deps `['amdrocm-rand-host-asan10.1']`; blas-gfx942 drops solver, core-gfx942 drops rand and solver, hpc-gfx942 is empty | same tree with plain dirs: every dependency present                                                                                                                                                                                                    |
| 3   | at `1fc939b4` (tests only): `cd build_tools && python -m pytest packaging/linux/tests/build_package_test.py -k TargetFeatureVariantDependencyTest -p no:cacheprovider -q`                                                                                                                                                                                                                                                                                      | **15 failed**, 5 passed, 7 subtests passed                                                                                                                                                                       | red: 14 failing subtests + 1 failing test. The "5 passed" are the look-alike guard plus 4 parents whose subtests failed (pytest 9 subtest reporting)                                                                                                   |
| 4   | at `cf27dafc`: same command as #3                                                                                                                                                                                                                                                                                                                                                                                                                              | **6 passed, 21 subtests passed**                                                                                                                                                                                 | green                                                                                                                                                                                                                                                  |
| 5   | at `cf27dafc`: `cd build_tools && python -m pytest packaging/linux/tests -vv -p no:cacheprovider`                                                                                                                                                                                                                                                                                                                                                              | 286 passed, 104 subtests passed                                                                                                                                                                                  | baseline 277 + 3 previously skipped + 6 new                                                                                                                                                                                                            |
| 6   | at `cf27dafc`: `cd build_tools && python -m pytest -q --durations=20 --cov --cov-report=term -p no:cacheprovider` (the `unit_tests.yml` command without the HTML report)                                                                                                                                                                                                                                                                                       | 1 failed, 2442 passed, 8 skipped, 313 subtests passed                                                                                                                                                            | the failure is `tests/artifact_backend_test.py::TestS3BackendCredentials::test_list_objects_unsigned`, a live S3 listing that this VM's egress blocks (SSL EOF)                                                                                        |
| 7   | at `20abb1e9`: same command as #6                                                                                                                                                                                                                                                                                                                                                                                                                              | 1 failed (same test), 2436 passed, 8 skipped, 292 subtests passed                                                                                                                                                | branch = main + 6 tests / 21 subtests, no new failures. Before `ninja-build` was installed, the CMake-driven `rocm_build_flags_test.py` and `therock_subproject_prebuilt_test.py` also failed ("unable to find Ninja"); they pass with ninja installed |
| 8   | at `cf27dafc`: `cd test_tools && python -m pytest -q --durations=20 -p no:cacheprovider`                                                                                                                                                                                                                                                                                                                                                                       | 74 passed, 19 subtests passed                                                                                                                                                                                    |                                                                                                                                                                                                                                                        |
| 9   | `pre-commit run --files build_tools/packaging/linux/packaging_utils.py build_tools/packaging/linux/tests/build_package_test.py`                                                                                                                                                                                                                                                                                                                                | Passed (all applicable hooks)                                                                                                                                                                                    | black reformatted the test file once, before the tests commit                                                                                                                                                                                          |
| 10  | `bandit --configfile build_tools/scan_tools/bandit.yml --severity-level low build_tools/packaging/linux/packaging_utils.py build_tools/packaging/linux/tests/build_package_test.py`                                                                                                                                                                                                                                                                            | No issues identified                                                                                                                                                                                             |                                                                                                                                                                                                                                                        |
| 11  | real packages at `20abb1e9` vs `cf27dafc`: `python build_tools/packaging/linux/build_package.py --artifacts-dir <tree> --dest-dir <out> --rocm-version 10.2.0 --pkg-type {deb,rpm} --version-suffix 36142888996 --build-variant asan --runpath-pkg -j 1 --pkg-names amdrocm-rand amdrocm-solver amdrocm-hiptensor amdrocm-rocalution amdrocm-blas amdrocm-sparse amdrocm-fft amdrocm-hpc amdrocm-runtime`, then `dpkg-deb -f <deb> Depends` / `rpm -qpR <rpm>` | see the before/after table below                                                                                                                                                                                 | real `dpkg-buildpackage`/`rpmbuild`; same 32 DEB / 33 RPM package set before and after                                                                                                                                                                 |
| 12  | apt resolver: private flat repo (`dpkg-scanpackages`) of real DEBs built at each commit, `apt-get -s install amdrocm-rand-asan` / `amdrocm-hpc-asan` with private `Dir::State`/`Dir::Cache`                                                                                                                                                                                                                                                                    | before: no `-gfx942` package is pulled in and hpc installs only empty metapackages; after: full host + device sets                                                                                               | simulation only, nothing installed                                                                                                                                                                                                                     |
| 13  | upstream PR #8560 applied on `20abb1e9` (`gh pr diff 8560 --repo ROCm/TheRock` + `git apply`) with this branch's test file; then #11 with that checkout                                                                                                                                                                                                                                                                                                        | tests: 1 failed (the gfx90a ordering subtest), 6 passed, 20 subtests passed. Packages: `Depends`/`Requires` identical to `cf27dafc` for all 32 DEB / 33 RPM                                                      | #8560's dependency behavior equals this branch's                                                                                                                                                                                                       |

Fake artifact tree for #11/#12: gfx942 artifact names copied from run 36142888996, i.e.
`rand_{lib,run,doc}_generic` + `rand_lib_gfx942:xnack+`; `solver`, `hiptensor` and `rocalution`
likewise (`:xnack+` only); `blas_lib_gfx942` + `blas_lib_gfx942:xnack+`; `sparse_lib_gfx942` +
`sparse_lib_gfx942:xnack+`; `fft_lib_gfx942` (plain only); `core-hip_lib_generic`; each with an
`artifact_manifest.txt` and a text payload; plus `therock_manifest.json` with
`KPACK_SPLIT_ARTIFACTS`. Targets were auto-detected, as in CI.

Before/after dependencies of the affected packages from #11 (`dpkg-deb -f`; RPM `rpm -qpR` shows the
same sets). Meta-package entries carry `(= 10.2.0-36142888996)`, omitted here:

| Package                                                    | Before (`20abb1e9`)                                                    | After (`cf27dafc`)                                                                |
| ---------------------------------------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `amdrocm-rand-asan10.2`                                    | `amdrocm-rand-host-asan10.2`                                           | `amdrocm-rand-host-asan10.2`, **`amdrocm-rand-asan10.2-gfx942`**                  |
| `amdrocm-solver-asan10.2`                                  | `amdrocm-solver-host-asan10.2`                                         | `amdrocm-solver-host-asan10.2`, **`amdrocm-solver-asan10.2-gfx942`**              |
| `amdrocm-hiptensor-asan10.2`                               | `amdrocm-hiptensor-host-asan10.2`                                      | `amdrocm-hiptensor-host-asan10.2`, **`amdrocm-hiptensor-asan10.2-gfx942`**        |
| `amdrocm-rocalution-asan10.2`                              | `amdrocm-rocalution-host-asan10.2`                                     | `amdrocm-rocalution-host-asan10.2`, **`amdrocm-rocalution-asan10.2-gfx942`**      |
| `amdrocm-blas-asan10.2-gfx942` (device)                    | `amdrocm-blas-host-asan10.2`                                           | `amdrocm-blas-host-asan10.2`, **`amdrocm-solver-asan10.2-gfx942`**                |
| `amdrocm-rocalution-asan10.2-gfx942` (device)              | host, `amdrocm-blas-asan10.2-gfx942`, `amdrocm-sparse-asan10.2-gfx942` | same + **`amdrocm-rand-asan10.2-gfx942`**                                         |
| `amdrocm-hpc-asan10.2-gfx942` (per-target meta)            | *(empty)*                                                              | **`amdrocm-rocalution-asan10.2-gfx942`**, **`amdrocm-hiptensor-asan10.2-gfx942`** |
| `amdrocm-blas-asan10.2`, `amdrocm-fft-asan10.2` (controls) | host + `-gfx942`                                                       | unchanged                                                                         |

The build log's `WORKAROUND: … missing artifacts` / `Excluding …` lines went from 8 to 0.

apt resolution from #12 (the amdrocm packages apt would install):

| `apt-get -s install` | Before                                                                                                 | After                                                                                               |
| -------------------- | ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| `amdrocm-rand-asan`  | `amdrocm-rand-asan`, `amdrocm-rand-asan10.2`, `amdrocm-rand-host-asan10.2`, `amdrocm-runtime-asan10.2` | the same + **`amdrocm-rand-asan10.2-gfx942`**                                                       |
| `amdrocm-hpc-asan`   | `amdrocm-hpc-asan`, `amdrocm-hpc-asan10.2`, `amdrocm-hpc-asan10.2-gfx942` (no libraries at all)        | + rocALUTION and hipTensor host and gfx942 packages, rand host and gfx942 (via rocALUTION), runtime |

Tests added (all in `TargetFeatureVariantDependencyTest`, `build_package_test.py`):

| Test                                                              | Covers                                                                                                                                                                               | Before fix                     |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------ |
| `test_device_payload_found_for_each_layout`                       | plain-only, `:xnack+`-only, plain + `:xnack+`, `:xnack+` + `:xnack-`, and neither; `has_artifact_for_arch()` must equal "device package has content"; variant order is deterministic | fails (gfx942, gfx90a)         |
| `test_meta_depends_on_every_target_with_device_payload`           | versioned meta = host + every target with content, and never the target with none                                                                                                    | fails                          |
| `test_asan_meta_packages_depend_on_device_packages`               | rand/solver/hiptensor/rocalution × DEB control and RPM spec, auto-detected ASAN targets                                                                                              | fails (8/8)                    |
| `test_device_package_depends_on_same_target_variant_only_package` | `amdrocm-blas` gfx942 → `amdrocm-solver` gfx942, DEB and RPM                                                                                                                         | fails (2/2)                    |
| `test_arch_metapackage_depends_on_variant_only_packages`          | `amdrocm-hpc` gfx942 → rocALUTION and hipTensor gfx942, DEB and RPM                                                                                                                  | fails (2/2)                    |
| `test_look_alike_directories_are_not_variants`                    | anchoring: `gfx1250` vs `gfx1250-strict`, sibling component `rand_dev_*`, shared prefix `miopen` vs `miopenprovider`                                                                 | guard; passes before and after |

## What could not be validated and why

- **Real ASAN CI artifacts and CI-built packages.** The `therock-dev-artifacts` S3 bucket is blocked
  by this VM's egress policy (TLS connection reset), so I used a fake tree with the exact artifact
  names from the CI log. To validate: dispatch `multi_arch_release_asan.yml` (`workflow_dispatch`
  with `release_type=dev`, `linux_amdgpu_families=gfx94x,gfx950`, `ref=<branch with this change>`).
  It calls the reusable `multi_arch_build_native_linux_packages.yml` with `build_variant=asan`.
  Then confirm the "Build DEB/RPM Packages" logs contain no
  `WORKAROUND: amdrocm-rand missing artifacts` lines and that `dpkg-deb -f amdrocm-rand-asan10.2_*.deb Depends` / `rpm -qpR` list the `-gfx942` and `-gfx950` packages.
- **Real apt/dnf/zypper installs on CI images were not run.** Only `apt-get -s` against a private
  local repo on this VM was exercised. To validate: `test_native_linux_packages_install.yml` /
  `install_native_linux_packages.yml` on ubuntu2404, rhel8, rhel10 and sles16 against the repo from
  the run above; check that `amdrocm-rand-asan` pulls `amdrocm-rand-asan10.2-gfx942` and
  `amdrocm-hpc-asan` pulls rocALUTION and hipTensor.
- **GPU runtime behavior.** Needs MI300 (gfx942) / MI350 (gfx950) with `HSA_XNACK=1`: rocRAND,
  rocSOLVER, hipTensor and rocALUTION kernels loading from the ASAN device packages. The failure mode
  before the fix (host library without its kpack payload, i.e. `hipErrorNoBinaryForGpu` /
  `hipErrorInvalidImage`) is inferred, not observed.
- **gfx950 on real artifacts.** The CI run I inspected was gfx94x only. The code path is
  target-agnostic, the sanitizer stanza rewrites gfx950 the same way, and the unit tests cover
  gfx950 names.
- **RPM installation.** Only spec text and `rpm -qpR` metadata were checked; `rpm`/`dnf`/`zypper`
  resolution was not run.
- **GitHub-hosted `unit_tests.yml` / `pre-commit.yml`.** Not run, since no PR was opened by request.
  The local runs used the same Python version, pytest pin and hooks.
- **Windows:** N/A (Linux native packaging only).

## Confidence

- **High: 90%**
- Justification: the root cause is confirmed three ways: code reading of both TheRock and the pinned
  kpack splitter, a real upstream ASAN run (artifact names, the exact `WORKAROUND` lines, and device
  packages that were built but never depended on), and local repros with the real functions and
  real `dpkg-buildpackage`/`rpmbuild` builds. The fix is covered by 6 new tests (21 subtests) across
  every dependency path that consumes `has_artifact_for_arch()` (versioned meta, device, per-target
  metapackage) for both DEB and RPM, by an apt resolver simulation, and by an unchanged full
  `build_tools` suite. The remaining 10% covers what could only be inferred: I didn't run this
  against real CI artifacts (S3 blocked) or real installs on CI images, and I didn't run anything on
  GPU hardware.

## Risks / follow-ups

1. **Separate pre-existing bug, not fixed here.** In kpack mode, packages built as plain versioned
   packages with an empty target drop every gfxarch dependency. That covers all non-meta `-devel`
   packages, plus gfxarch packages routed to the simple builder such as `amdrocm-dnn-test` and
   `amdrocm-runtime-test`. For example, `amdrocm-rand-dev-asan10.2` does not depend on
   `amdrocm-rand-asan10.2`. This happens independently of xnack: it reproduces with plain artifact
   names, and it appears for 10 packages in run 36142888996
   (`WORKAROUND: Excluding amdrocm-blas (no artifacts for )`, …). Drafted issue (not filed):

   > **kpack native packages: versioned packages built for an empty target drop gfxarch
   > dependencies (e.g. `amdrocm-rand-dev` does not depend on `amdrocm-rand`)**
   >
   > `process_main_dependencies_kpack()` handles non-gfxarch versioned packages (all non-meta
   > `-devel` packages) by calling `filter_dependencies_by_artifacts(dep_list, artifacts_dir, config.gfx_arch)` with `gfx_arch == ""`. For gfxarch dependencies this calls
   > `has_artifact_for_arch(dep, artifacts_dir, "")`, which looks for `{name}_{component}_`
   > directories that never exist, so the dependency is dropped with
   > `WORKAROUND: Excluding amdrocm-<x> (no artifacts for )`. This shows up in ASAN run
   > 36142888996 for `amdrocm-{blas,sparse,solver,rocalution,hiptensor,rand,rccl,dnn}-devel`,
   > `amdrocm-dnn-test` and `amdrocm-runtime-test`, and it reproduces with release (non-xnack)
   > artifact names. Expected: depend on the versioned meta (e.g. `amdrocm-rand10.2`) or the host
   > package. Which one is a maintainer decision.

1. **CI coverage gap.** The simulated install test passes every built `.deb` explicitly, so it
   cannot detect unreachable device packages. A cheap guard: after packaging, assert that every
   built `<pkg>-gfxNNN` device package appears in the `Depends`/`Requires` of the `<pkg>` versioned
   meta. Alternatively, run `apt-get -s install <meta>` against a local flat repo, as in #12.

1. **Docs vs code.** `docs/packaging/nativepackage_dependency_tree.md` says host packages depend on
   the host variants of gfxarch dependencies (`amdrocm-blas-host8.2` → `amdrocm-solver-host8.2`),
   but the `GFX_HOST` branch in `process_main_dependencies_kpack()` excludes gfxarch dependencies.
   Noticed while reading; not investigated further.

1. **Separator changes.** If #7290 is resolved by renaming the `:` separator in artifact
   directories, update `_artifact_dirs_with_variants()`. Anchoring on the separator is deliberate:
   use an explicit feature pattern (e.g. via `sdk_targets.canonical_target`), not a looser glob,
   or `gfx1250` will start matching `gfx1250-strict`.

1. **Ordering.** Sorting variant directories can reorder RPM spec source lists and copy order for
   packages with two or more variant directories for one target. No content change is expected,
   because kpack file names embed the full target ID.

1. **Merge coordination with #8560.** Land one of the two. If #8560 merges first, this branch's
   helper and tests need a trivial rebase over the same lines of `has_artifact_for_arch()`.
