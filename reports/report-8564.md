# Report: ROCm/TheRock#8564: Windows Multi Arch PyTorch nightly: build fails — `dllimport cannot be applied to non-inline function definition` in `aotriton/util.h`

> Written by an AI agent (Cursor cloud agent). A human contributor should review
> and own it before anything here goes upstream. Nothing was filed, commented, or
> opened as a PR; the branch exists only on the `rysuds/TheRock` fork.

## Issue

- Link: https://github.com/ROCm/TheRock/issues/8564
- Type / skill used: `external-builds-pytorch` (`skills/external-builds-pytorch.md`),
  with `common.md`, `rocm-triage-playbook.md` and `rocm-windows.md`.
- Summary: All five Windows PyTorch **nightly** wheel builds (Python 3.10–3.14) in
  [rockrel run 36289615270](https://github.com/ROCm/rockrel/actions/runs/36289615270)
  (2026-09-27) fail about an hour in, compiling `torch_hip`'s
  `aten/src/ATen/native/transformers/hip/sdp_utils.cpp` with clang-cl:
  `torch/include\aotriton\util.h(41,1): error: dllimport cannot be applied to non-inline function definition`.
  No Windows nightly torch, torchaudio or torchvision wheels are produced. The
  `release/2.12`, `release/2.13` and `release/2.14` builds in the same run pass.
  Expected: nightly Windows wheels build as they did through the 2026-09-25 PyTorch nightly.
- Related issues and PRs:
  - [pytorch/pytorch#197747](https://github.com/pytorch/pytorch/pull/197747)
    "[ROCm] Bump AOTriton to 0.14.2b" is the trigger. It landed as
    [`9b99789`](https://github.com/pytorch/pytorch/commit/9b9978943e4030e97eeee36a9968db27a3b21163)
    on 2026-09-26 after two reverts.
  - [ROCm/aotriton#244](https://github.com/ROCm/aotriton/pull/244) is the change that
    exposed the bug ("[win32] Correctly set `AOTRITON_API` to `dllimport`/`dllexport`").
  - [#8413](https://github.com/ROCm/TheRock/issues/8413) (closed) is a sibling Windows
    regression (`import fcntl`) from an earlier landing of the same PyTorch PR. It was
    resolved by reverting the PyTorch PR. Its fix, [ROCm/aotriton#242](https://github.com/ROCm/aotriton/pull/242),
    is still open. This report is complementary: a different defect with the same
    trigger.
  - I found no existing report or fix for this error. I searched ROCm/aotriton issues
    and PRs for `dllimport` and `cdiv`, pytorch/pytorch for `dllimport cannot be applied aotriton`
    and `aotriton util.h cdiv`, and ROCm/TheRock for `dllimport`, `sdp_utils` and `aotriton`.
    No open TheRock PR addresses #8564.

## Root cause

The bug is **upstream in ROCm/aotriton**. A PyTorch pin bump exposed it. TheRock's
only involvement is that it builds Windows torch with flash attention by default
(since #7131).

1. aotriton's public header applies the export macro to a header-defined,
   non-inline function template.
   [`include/aotriton/util.h:39-43`](https://github.com/ROCm/aotriton/blob/c11b5b886a10a00f925f74133d38b3505e67ccd9/include/aotriton/util.h#L39-L43)
   at 0.14.2b:

   ```cpp
   template<typename T>
   T AOTRITON_API
   cdiv(T numerator, T denominator) {
     return (numerator + (denominator - 1)) / denominator;
   }
   ```

   These lines are identical in 0.13b.

1. [ROCm/aotriton#244](https://github.com/ROCm/aotriton/pull/244) changed what
   `AOTRITON_API` means for consumers on MSVC-ABI targets. In 0.13b,
   [`config.h.in:24-28`](https://github.com/ROCm/aotriton/blob/6e00ef3e335b45dfb49065259533b59c68995bfe/include/aotriton/config.h.in#L24-L28)
   was unconditionally `__declspec(dllexport)`. In 0.14.2b,
   [`config.h.in:24-34`](https://github.com/ROCm/aotriton/blob/c11b5b886a10a00f925f74133d38b3505e67ccd9/include/aotriton/config.h.in#L24-L34)
   is `dllexport` only when `AOTRITON_BUILDING_LIBRARY` is defined, and
   `dllimport` otherwise. Only aotriton's own targets define that macro
   ([`v3src/common/CMakeLists.txt:39`](https://github.com/ROCm/aotriton/blob/c11b5b886a10a00f925f74133d38b3505e67ccd9/v3src/common/CMakeLists.txt#L39),
   [`v3src/hip-dependent/CMakeLists.txt:27`](https://github.com/ROCm/aotriton/blob/c11b5b886a10a00f925f74133d38b3505e67ccd9/v3src/hip-dependent/CMakeLists.txt#L27)).
   clang accepts `dllexport` on a function definition but rejects `dllimport` on a
   non-inline one. A template pattern is not an instantiation, so the template
   exemption does not apply, and the error fires as soon as the header is parsed.
   The call site doesn't matter. aotriton's own build, and therefore its CI, only
   sees `dllexport`, which is why it didn't catch this.

   - #244 is in the aotriton tags **0.14.1b** (`eb7b78ba`) and **0.14.2b**
     (`c11b5b88`, the head of `release/0.14`), and on `main` as `01dc9166`.
     0.14b (`1c7c973c`) predates it. aotriton `main` still has the bug.

1. PyTorch pulled it in.
   [pytorch/pytorch#197747](https://github.com/pytorch/pytorch/pull/197747) moved
   `__AOTRITON_CI_COMMIT` from `6e00ef3e` (0.13b) to `c11b5b88` (0.14.2b) in
   [`cmake/External/aotriton.cmake:36-40`](https://github.com/pytorch/pytorch/blob/ef166fb295ff511bdefd628b2d2fefea9570aa61/cmake/External/aotriton.cmake#L36-L40).
   On Windows, PyTorch always builds the aotriton runtime from source at that commit
   ([`aotriton.cmake:274-278`](https://github.com/pytorch/pytorch/blob/ef166fb295ff511bdefd628b2d2fefea9570aa61/cmake/External/aotriton.cmake#L274-L278))
   and installs its headers into `torch/include`
   ([`aotriton.cmake:5`](https://github.com/pytorch/pytorch/blob/ef166fb295ff511bdefd628b2d2fefea9570aa61/cmake/External/aotriton.cmake#L5)).
   `sdp_utils.cpp` includes `<aotriton/flash.h>` whenever flash or mem-efficient
   attention is enabled
   ([`sdp_utils.cpp:33-36`](https://github.com/pytorch/pytorch/blob/ef166fb295ff511bdefd628b2d2fefea9570aa61/aten/src/ATen/native/transformers/cuda/sdp_utils.cpp#L33-L36);
   the hipified copy has it on line 37). As a consumer, it sees `dllimport`.

1. TheRock builds Windows torch with `USE_FLASH_ATTENTION=ON` and
   `USE_MEM_EFF_ATTENTION=ON` whenever at least one target is aotriton-capable
   ([`build_prod_wheels.py:1121-1163`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/external-builds/pytorch/build_prod_wheels.py#L1121-L1163)).
   The release family list includes gfx110X, gfx1151, gfx120X and gfx90a, so it is on.

Evidence:

- Failing log ([py3.14 job](https://github.com/ROCm/rockrel/actions/runs/36289615270/job/108537124757),
  fetched with `gh api repos/ROCm/rockrel/actions/jobs/108537124757/logs`):

  - `[AOTriton] Use SHA1 c11b5b886a10a00f925f74133d38b3505e67ccd9`, and
    `Cannot find AOTriton runtime for ROCM 7.17. Build runtime from source`.
  - aotriton's own TUs compile with `config.h(28,33): ... #define AOTRITON_API __declspec(dllexport)`
    and produce only `-Wignored-attributes` warnings. `aotriton_v2.dll` is installed at 03:30:04.
  - At 03:30:24, `sdp_utils.cpp.obj` fails with exactly one error: `util.h(41,1): error: dllimport cannot be applied to non-inline function definition`.
    Apart from CMake's expected OpenMP feature-probe failures during configure, that
    is the only compile error in the 100 MB log. Ninja then drains in-flight jobs and
    stops at 04:14.

- Last good and first bad:

  |           | rockrel run                                                                                 | PyTorch nightly (main)      | aotriton pin        | nightly jobs                                                   |
  | --------- | ------------------------------------------------------------------------------------------- | --------------------------- | ------------------- | -------------------------------------------------------------- |
  | last good | [36216540588](https://github.com/ROCm/rockrel/actions/runs/36216540588) (2026-09-26 04:00Z) | `2fca68c9e0` (`47b776c01f`) | `6e00ef3` (0.13b)   | 5/5 pass; `sdp_utils.cpp.obj` compiled with flash attention ON |
  | first bad | [36289615270](https://github.com/ROCm/rockrel/actions/runs/36289615270) (2026-09-27 02:49Z) | `6aa9e2fc40` (`ef166fb295`) | `c11b5b8` (0.14.2b) | 5/5 fail at `sdp_utils.cpp.obj`                                |

  `47b776c0..ef166fb2` is 68 commits and contains `9b99789` (#197747). The only other
  change there touching `sdp_utils.cpp`, [#184983](https://github.com/pytorch/pytorch/pull/184983),
  changes symbolic-shape guards and not the includes. The TheRock commit used by CI,
  `36292c8a`, has the same `external-builds/pytorch` contents as my base `20abb1e9`.
  PyTorch's `nightly` branch as of 2026-09-28 (`68e0ae4967`) still pins `c11b5b88`,
  so the breakage persists.

- Partial repro with Linux clang, detailed under Validation: the real aotriton
  public headers, with `config.h` generated by CMake `configure_file` from each
  tag's `config.h.in`, compiled in clang-cl mode with the language flags from the
  failing command line. The 0.14.2b consumer TU produces the byte-identical error
  (`util.h(41,1)`, `1 error generated.`). The 0.13b consumer TU compiles, and so do
  the 0.14.2b headers when compiled as aotriton itself (`-DAOTRITON_BUILDING_LIBRARY`).
  The 0.13b consumer build prints 4× `-Wdllexport-explicit-instantiation-decl`
  on the `extern template class TensorView<N>` lines, the contradiction #244 set out
  to fix.

## Fix

The right fix is upstream in aotriton (text drafted below). The TheRock change is a
narrowly gated, self-removing mitigation.

- `external-builds/pytorch/build_prod_wheels.py`:

  - New `WINDOWS_BROKEN_AOTRITON_COMMITS` holds the aotriton tags that contain #244
    without a fix: 0.14.1b `eb7b78ba…` and 0.14.2b `c11b5b88…`. It carries
    `TODO(https://github.com/ROCm/TheRock/issues/8564)`.
  - New `get_pytorch_aotriton_commit(pytorch_dir, env)` returns the aotriton commit
    PyTorch will build from source. It mirrors `aotriton.cmake`:
    `AOTRITON_INSTALLED_PREFIX` means no source build (returns `None`), a
    `PYTORCH_AOTRITON_COMMIT` override wins, and otherwise it parses the single
    40-hex `set(__AOTRITON_CI_COMMIT "…")` literal. A missing file or an ambiguous
    pin returns `None`, which keeps today's behavior.
  - The existing flash-attention decision moves verbatim into
    `resolve_use_flash_attention(...)` so it can be unit tested. It now takes
    `pytorch_rocm_arch` as a parameter instead of reading `env`. One new rule is
    appended: on Windows only, if the build would enable flash attention and the
    effective aotriton commit is known-broken, it prints a `::warning::` annotation.
    It then disables flash attention when that was the default, or keeps it with an
    "expect this build to fail" warning when `--enable-pytorch-flash-attention` was
    passed explicitly.
  - `do_build_pytorch` calls it. The aotriton commit is only looked up
    `if is_windows`, so Linux never reads the file.

  ```python
  if (
      use_flash_attention
      and is_windows
      and aotriton_commit is not None
      and aotriton_commit.lower() in WINDOWS_BROKEN_AOTRITON_COMMITS
  ):
      ...  # explicit flag: warn and keep; default: ::warning:: and disable
  ```

- `build_tools/tests/build_prod_wheels_flash_attention_test.py` (new, 17 tests):

  - Commit discovery: pin parsing, the env override, the installed prefix, a missing
    file, and ambiguous pins.
  - Decision matrix: Windows with a broken pin (default, uppercase SHA, explicit
    enable, explicit disable), Windows with a good or unknown pin, a Windows target
    list aotriton can't build for (no warning), Linux with a broken pin (unchanged),
    and Linux without triton.
  - Two wiring tests run `do_build_pytorch` with `is_windows=True` and mocked
    subprocesses. They assert the torch `python -m build` env has
    `USE_FLASH_ATTENTION`/`USE_MEM_EFF_ATTENTION=OFF` and no
    `aotriton_v2.dll` force-include for the broken pin, and the opposite for a good pin.

Effect: Windows nightly (PyTorch `main`) builds skip aotriton entirely. PyTorch only
includes `aotriton.cmake` when either flag is on
([`CMakeLists.txt:1172-1176`](https://github.com/pytorch/pytorch/blob/ef166fb295ff511bdefd628b2d2fefea9570aa61/CMakeLists.txt#L1172-L1176)),
and every aotriton `#include` in always-built ROCm sources is behind those flags
(`attention.cu:87-100`, `attention_backward.cu:48-61`, `sdp_utils.cpp:33-36`;
`flash_attn/aot/*.hip` is only compiled with `USE_FLASH_ATTENTION`). The wheel then
has no `aotriton_v2.dll`/`liblzma.dll`, and SDPA uses the non-aotriton backends.
That matches the pre-#7131 Windows default and today's gfx103X-only builds. It
lifts automatically once PyTorch pins another commit, or once CI sets
`PYTORCH_AOTRITON_COMMIT` to a fixed one. Linux, and the ROCm/pytorch `release/2.12`,
`2.13` and `2.14` branches (pins `c073da80` and `6e00ef3e`), are unchanged; see
validation #6.

Alternatives considered and not taken:

- **Define `AOTRITON_BUILDING_LIBRARY` for torch TUs** (e.g. via `CXXFLAGS`). This
  keeps aotriton on Windows, but it misdeclares torch as aotriton, reintroduces the
  `dllexport` + `extern template` contradiction that #244 removed, and drops the
  `extern template TensorView<N>` declarations. The link and ABI effects can't be
  checked without a Windows build.
- **`PYTORCH_AOTRITON_COMMIT` pointing at a pre-#244 commit** (0.14b `1c7c973c`).
  That commit has the #8413 `fcntl` break, and pairing it with the 0.14.2b kernel
  images and PyTorch's 0.14 API changes (#247 changed kernels) risks silent mismatches.
  Pointing it at a *fixed* commit is the preferred interim step once one exists, and
  the new code honors it.
- **Pinning PyTorch nightly** to a pre-bump commit. The playbook advises against
  this, and it freezes nightly.
- **Passing `--no-enable-pytorch-flash-attention` in the workflow.** It works, but it
  isn't self-removing and someone has to remember to revert it.
- **Patching aotriton headers mid-build.** PyTorch's `ExternalProject` has no patch
  hook, and the build is a single ninja invocation.
- **Reverting pytorch/pytorch#197747 again**, as was done for #8413. That's a valid
  maintainer choice, and it would make this mitigation a no-op.

### Drafted upstream issue: ROCm/aotriton (not filed)

```markdown
**Title:** [Windows] `cdiv` in util.h marked AOTRITON_API breaks all consumers since #244
("dllimport cannot be applied to non-inline function definition")

Since #244, `AOTRITON_API` expands to `__declspec(dllimport)` in MSVC-ABI TUs that don't
define `AOTRITON_BUILDING_LIBRARY`. `include/aotriton/util.h` (0.14.2b, c11b5b8, L39-43)
still applies it to a header-defined, non-inline function template:

    template<typename T>
    T AOTRITON_API
    cdiv(T numerator, T denominator) {
      return (numerator + (denominator - 1)) / denominator;
    }

clang-cl rejects dllimport on a non-inline function definition, so every consumer that
includes `<aotriton/util.h>` (directly or via `<aotriton/flash.h>`) fails to compile on
Windows, whether or not it calls `cdiv`. aotriton's own targets define
`AOTRITON_BUILDING_LIBRARY` (dllexport), so aotriton's build/CI doesn't see it. Before #244
consumers also saw dllexport, which is allowed on a definition.

Affected: tags 0.14.1b (eb7b78ba) and 0.14.2b (c11b5b88, release/0.14 head); main since
01dc9166. PyTorch main pins 0.14.2b (pytorch/pytorch#197747), so ROCm Windows PyTorch
builds fail in aten/src/ATen/native/transformers/hip/sdp_utils.cpp (ROCm/TheRock#8564):

    B:/src/pytorch/torch/include\aotriton\util.h(41,1): error: dllimport cannot be applied to non-inline function definition
       41 | cdiv(T numerator, T denominator) {
          | ^
    1 error generated.

Minimal repro (any clang; no Windows needed):

    printf '%s\n' '#define AOTRITON_API __declspec(dllimport)' \
      'template<typename T>' 'T AOTRITON_API' \
      'cdiv(T numerator, T denominator) { return (numerator + (denominator - 1)) / denominator; }' > t.cpp
    clang --driver-mode=cl --target=x86_64-pc-windows-msvc /nologo -TP -std:c++20 /Zs t.cpp
    # t.cpp(4,1): error: dllimport cannot be applied to non-inline function definition

Suggested fix: `cdiv` is header-only, so drop the export macro (optionally make it
`constexpr` like CAT64/CAT32/TRICAT next to it):

    -T AOTRITON_API
    +constexpr T
     cdiv(T numerator, T denominator) {

No ABI impact: consumers instantiate it locally, and aotriton's own `cdiv<uint32_t>` uses in
modules/flash/csrc/*.cc still compile (checked in clang-cl mode for consumer and
AOTRITON_BUILDING_LIBRARY TUs). Please consider a release/0.14 commit or 0.14.x tag with
the fix that PyTorch can pin, and a Windows CI step that compiles a consumer TU
(`#include <aotriton/flash.h>` without AOTRITON_BUILDING_LIBRARY) to catch export-macro
regressions.
```

### Drafted note for pytorch/pytorch#197747 (not posted)

```markdown
Heads-up: with the 0.14.2b pin, ROCm Windows builds fail in sdp_utils.cpp with
"dllimport cannot be applied to non-inline function definition" from aotriton/util.h
(ROCm/TheRock#8564). Root cause is in aotriton (`cdiv` carries AOTRITON_API, which
ROCm/aotriton#244 made dllimport for consumers). Once aotriton has a fixed 0.14.x commit,
bumping `__AOTRITON_CI_COMMIT` is enough for Windows, since Windows always builds the
runtime from source at that commit; the image/runtime SHA256 lists only change if the
release tag changes.
```

## Branch

- Base: `main` @ `20abb1e96819fe8a85935db7e329dd8a69396303` ("bump libhipcxx (#8448)")
- Branch name: `cursor/windows-aotriton-dllimport-mitigation-70fa`, pushed to
  https://github.com/rysuds/TheRock only, with no PR. The `cursor/…-70fa` prefix is
  required by the agent environment in place of the usual `users/<name>/…` convention.
- Commits:
  - `de15553c` [torch][windows] Skip flash attention for aotriton pins that fail with clang-cl
  - (this commit) Add report for ROCm/TheRock#8564

## Validation performed in the cloud agent environment

Environment: Linux x86_64 (Ubuntu 24.04.4), no AMD GPU. Python 3.12.3. For CI parity,
tests ran in a venv with only `requirements-test.txt` installed (pytest 9.0.3), as
`unit_tests.yml` does; the root `requirements.txt` pins `pytest-cmake`, which caps
pytest below 9. pre-commit 4.6.2, CMake 3.28.3, Ubuntu clang 18.1.3, and ninja 1.13.2
(pip; added to `PATH` because the VM has no system ninja).

| #   | Command (exact)                                                                                                                                                                                                                | Result                                                                                                                                                                                                                                                                                                                                                       | Notes                                                                                                                                                                                                                                    |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `cd build_tools && python -m pytest tests/build_prod_wheels_flash_attention_test.py -vv -p no:cacheprovider` (**pre-fix**)                                                                                                     | 1 error at collection                                                                                                                                                                                                                                                                                                                                        | `ImportError: cannot import name 'get_pytorch_aotriton_commit'`                                                                                                                                                                          |
| 2   | `cd build_tools && python -m pytest tests/build_prod_wheels_flash_attention_test.py tests/build_prod_wheels_version_test.py -vv -p no:cacheprovider` (post-fix)                                                                | 24 passed                                                                                                                                                                                                                                                                                                                                                    | 17 new tests plus 7 existing                                                                                                                                                                                                             |
| 3   | Mutation check: temporarily (M1) disable the known-bad check, (M2) drop the `is_windows` gate, (M3) stop passing the commit at the call site; rerun #2's new file; restore (`cmp` identical)                                   | M1: 4 failed; M2: 1 failed (`test_linux_ignores_windows_broken_aotriton`); M3: 1 failed (`test_broken_aotriton_builds_without_flash_attention`); restored: 17 passed                                                                                                                                                                                         | Confirms the tests guard the behavior                                                                                                                                                                                                    |
| 4   | `cd build_tools && python -m pytest -q -p no:cacheprovider` on a `main` worktree (**pre-change**, ninja on `PATH`)                                                                                                             | 2433 passed, 1 failed, 11 skipped, 292 subtests passed                                                                                                                                                                                                                                                                                                       | Failure: `artifact_backend_test.py::TestS3BackendCredentials::test_list_objects_unsigned` (`SSLError`, blocked egress to S3). Without ninja, 13 more CMake-based tests fail with "unable to find a build program corresponding to Ninja" |
| 5   | Same as #4 on the branch (**post-change**)                                                                                                                                                                                     | 2450 passed, 1 failed, 11 skipped, 292 subtests passed                                                                                                                                                                                                                                                                                                       | Same single pre-existing S3 failure; +17 new tests                                                                                                                                                                                       |
| 6   | Ad-hoc: `get_pytorch_aotriton_commit` and `resolve_use_flash_attention` against the real `aotriton.cmake` from pytorch `ef166fb2` (first bad), pytorch `47b776c0` (last good), and ROCm/pytorch `release/2.12`, `2.13`, `2.14` | Pins parsed as `c11b5b88`, `6e00ef3e`, `c073da80`, `6e00ef3e`, `6e00ef3e`. Windows default flash attention is `False` only for `ef166fb2`; Linux is `True` for all                                                                                                                                                                                           | Windows target list is a subset of the release families (gfx110X, gfx1151, gfx120X, gfx90a, gfx900, gfx1030)                                                                                                                             |
| 7   | Ad-hoc mocked dry run of `do_build_pytorch` with `is_windows=True` and the real `ef166fb2` `aotriton.cmake`                                                                                                                    | Prints `::warning::Disabling Flash Attention: aotriton c11b5b88… does not compile on Windows (…/issues/8564)…`; build env has `USE_FLASH_ATTENTION=OFF` and `USE_MEM_EFF_ATTENTION=OFF`; no aotriton or liblzma force-includes. With `PYTORCH_AOTRITON_COMMIT=<other sha>`, or the `release/2.14` file, both are `ON` with the DLL force-includes            | Subprocesses mocked; no real build                                                                                                                                                                                                       |
| 8   | `python external-builds/pytorch/build_prod_wheels.py --help` and `... build --help`                                                                                                                                            | rc 0 for both                                                                                                                                                                                                                                                                                                                                                | argparse intact; `--enable-pytorch-flash-attention` unchanged                                                                                                                                                                            |
| 9   | `pre-commit run --files external-builds/pytorch/build_prod_wheels.py build_tools/tests/build_prod_wheels_flash_attention_test.py` (and later with `reports/report-8564.md`)                                                    | Passed                                                                                                                                                                                                                                                                                                                                                       | black, whitespace, EOF, tabs, mdformat                                                                                                                                                                                                   |
| 10  | `bandit --configfile build_tools/scan_tools/bandit.yml --severity-level low <both files>`, compared with `main`                                                                                                                | No new findings                                                                                                                                                                                                                                                                                                                                              | Both findings (B310 URL open, B101 assert) are pre-existing on `main`                                                                                                                                                                    |
| 11  | Minimal repro: `clang --driver-mode=cl --target=x86_64-pc-windows-msvc /nologo -TP /permissive- -std:c++20 -fms-extensions -Wno-ignored-attributes /Zs minimal.cpp` (snippet of `util.h:39-43` + `config.h.in` macro)          | Consumer: `minimal.cpp(14,1): error: dllimport cannot be applied to non-inline function definition`; with `-DAOTRITON_BUILDING_LIBRARY`: rc 0; Linux (`clang++ -fsyntax-only`): rc 0                                                                                                                                                                         | Same result with `clang++ -target x86_64-pc-windows-msvc -fms-extensions -fsyntax-only`                                                                                                                                                  |
| 12  | Header-level repro (`run_repro.sh`, below): real headers from 0.13b `6e00ef3` and 0.14.2b `c11b5b8`; `config.h` via `cmake -P` `configure_file`; flags from the failing command line; stub STL and `hip/hip_runtime.h`         | 0.13b consumer: rc 0 (4 `-Wdllexport-explicit-instantiation-decl` warnings). **0.14.2b consumer: `util.h(41,1): error: dllimport cannot be applied to non-inline function definition`, `1 error generated.`** 0.14.2b library TU: rc 0. 0.14.2b with `AOTRITON_API` dropped from `cdiv`, or with `constexpr` instead: rc 0 for both consumer and library TUs | Partial repro only (see below)                                                                                                                                                                                                           |
| 13  | Log forensics: `gh api --allow-escape-sequences repos/ROCm/rockrel/actions/jobs/<id>/logs` for 108537124757 (first bad) and 108333598883 (last good); `gh api repos/pytorch/pytorch/compare/47b776c0…ef166fb2`                 | See the Root cause table                                                                                                                                                                                                                                                                                                                                     |                                                                                                                                                                                                                                          |

<details>
<summary><code>run_repro.sh</code> used for #12 (lives outside the repo)</summary>

```bash
# Inputs: inst-<sha>/include/aotriton/{flash,util,runtime,dtypes,cpp_tune}.h fetched at the tag,
# plus config.h from `cmake -DIN=config.h.in -DOUT=... -P gen_config.cmake`
# (configure_file with aotriton's defaults: no name suffix, version 0.<minor>.<patch>).
# stubs/: array, functional, memory, vector, stdexcept, string_view, cstdint, cstddef,
# hip/hip_runtime.h (hipStream_t, hipError_t only).
CL=(clang --driver-mode=cl --target=x86_64-pc-windows-msvc /nologo -TP
    -std:c++20 /permissive- /EHsc /Zc:__cplusplus /bigobj /utf-8
    -fms-extensions -Wno-ignored-attributes -D__HIP_PLATFORM_AMD__=1
    -DUSE_ROCM -DNOMINMAX -DWIN32_LEAN_AND_MEAN /DWIN32 /D_WINDOWS -MD
    /Istubs /Zs)
# consumer_tu.cpp: #include <aotriton/{dtypes,util,config,flash}.h>; calls check_gpu()
# library_tu.cpp:  #include <aotriton/flash.h>; calls AOTRITON_NS::cdiv<uint32_t>()
"${CL[@]}" /Iinst-6e00ef3/include consumer_tu.cpp                              # rc=0
"${CL[@]}" /Iinst-c11b5b8/include consumer_tu.cpp                              # util.h(41,1): error
"${CL[@]}" /Iinst-c11b5b8/include -DAOTRITON_BUILDING_LIBRARY library_tu.cpp   # rc=0
"${CL[@]}" /Iinst-c11b5b8-drop_api/include consumer_tu.cpp                     # rc=0
"${CL[@]}" /Iinst-c11b5b8-drop_api/include -DAOTRITON_BUILDING_LIBRARY library_tu.cpp  # rc=0
```

</details>

## What could not be validated and why

- **The Windows clang-cl PyTorch build itself.** It needs a Windows runner with MSVC and
  about 1.5–2.5 h. I have not shown that the nightly wheel builds end-to-end with the
  mitigation. The failing build stopped at step 2444/2946, and PyTorch `main` has 68
  new commits since the last good nightly, so a different Windows-only failure later in
  the build can't be ruled out. To validate, dispatch
  `multi_arch_build_windows_pytorch_wheels.yml` with:

  - `pytorch_git_ref: nightly`, `amdgpu_families: gfx110X-all;gfx1151;gfx120X-all`,
    `python_version: 3.12`
  - `rocm_version: 10.2.0a20260927` and
    `rocm_package_find_links_url: https://therock-nightly-artifacts.s3.amazonaws.com/36281205442-windows/python/index.html`
    (the ROCm build from the failing run)
  - `release_type: dev`, `test_level: none`, `repository`/`ref` pointing at this branch

  Expect the `::warning::Disabling Flash Attention…` annotation on "Build PyTorch
  wheels", the step to finish, and "Sanity check wheel" to pass.

- **The real toolchain.** The CI compiler is TheRock's amd-llvm clang-cl with the real
  MSVC STL and HIP headers. My repro used Ubuntu clang 18.1.3 with stub STL and HIP
  headers, so it covers the front-end diagnostic, not the full TU or linking. The
  diagnostic text and location match CI exactly.

- **The proposed aotriton fix in a real Windows aotriton + PyTorch build.** I only
  checked it at header level; the full build and link are untested.

- **Runtime behavior of the resulting wheels on AMD GPUs.** On Windows gfx110X,
  gfx1151 or gfx120X hardware, SDPA should fall back to the non-aotriton backends, and
  aotriton SDPA tests should skip because `PLATFORM_SUPPORTS_FLASH_ATTENTION` is false.
  Validate with `test_pytorch_wheels.yml` on a Windows GPU runner.

- **rockrel release orchestration.** That includes annotation visibility, and the
  kpack split and upload steps with a wheel that has no `aotriton.images`. The release
  workflows need `ROCm/rockrel` credentials, which I don't have.

- **Linux.** It is unaffected by construction and unit-tested with `is_windows=False`,
  but I did not build it.

## Confidence

- **Medium: 80%**
- Root cause: about 95%. The exact diagnostic reproduces from the real headers, and
  the last-good/first-bad pins bracket the aotriton #244 change. Only the TU-level
  context (real STL/HIP, amd-llvm) is unverified.
- Mitigation logic: about 95%. It is covered by 17 unit tests with mutation checks,
  real-file parsing of five `aotriton.cmake` versions, and a mocked Windows dry run.
- Windows nightly actually building green with the mitigation: about 75%. I verified
  from source that no aotriton header or target is used with both flags off, and this
  configuration was the Windows default until #7131. The known unknowns are later
  Windows failures masked by this one, and whether maintainers prefer an upstream
  revert over shipping nightlies without aotriton for a while.

## Risks / follow-ups

- **Behavior change.** While active, Windows nightly wheels drop aotriton SDPA (flash
  and mem-efficient attention), so attention is slower and there is no
  `aotriton_v2.dll`. That diverges from the README's "aotriton ✅ Supported on Windows"
  for nightly builds only. The `::warning::` makes it visible in CI. Maintainers may
  prefer to fail loudly and revert pytorch/pytorch#197747 again instead (the #8413
  precedent). If so, drop commit `de15553c`; nothing else depends on it. aotriton SDPA
  test coverage on Windows nightlies also pauses, because the tests skip rather than run.
- **Known-bad list coverage.** It matches only full SHAs (case-insensitive). If PyTorch
  moves to another still-broken commit, or someone sets `PYTORCH_AOTRITON_COMMIT` to a
  tag or short SHA, the check misses it and the build fails loudly as it does today.
  If PyTorch restructures the pin (multiple literals), the helper returns `None` and
  also falls back to today's behavior.
- **Upstream actions**, drafts above, for a human to file:
  - An aotriton issue and fix: drop `AOTRITON_API` from `cdiv`, cut a release/0.14 fix
    commit or tag, and add a Windows consumer-compile CI check.
  - A PyTorch pin bump to that commit.
  - Once a fixed commit exists, TheRock can set `PYTORCH_AOTRITON_COMMIT=<fixed sha>`
    for Windows nightly jobs to restore aotriton before PyTorch bumps.
- **Cleanup.** Remove `WINDOWS_BROKEN_AOTRITON_COMMITS` and the check (the
  `TODO(#8564)`) once PyTorch pins a fixed aotriton. The tests referencing it go with it.
- **Related open work.** [ROCm/aotriton#242](https://github.com/ROCm/aotriton/pull/242)
  (the `fcntl` fix for #8413) is still open. 0.14.2b already contains #243, which
  removed codegen's dependency on the tuner, so the `fcntl` failure did not recur in
  this run.
