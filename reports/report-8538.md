# Report: ROCm/TheRock#8538: [Windows][ROCm 10.0][gfx1200] Unsigned ROCm DLLs blocked by Smart App Control – WinError 4551 on RX 9060 XT

## Issue

- Link: https://github.com/ROCm/TheRock/issues/8538
- Type / skill used: `windows-packaging` (`skills/windows-packaging.md`), on top of
  `common.md`. The `rocm-windows.md` and `rocm-triage-playbook.md` references and the repo's
  own `skills/rocm-pr-quality` and `skills/therock-pr-quality` skills were also applied.
- Summary: On Windows 11 with Smart App Control (SAC) in enforcement mode, `import torch`
  from the ROCm 10.0 Python wheels fails inside `rocm_sdk.initialize_process()` →
  `preload_libraries()` → `ctypes.CDLL()` with
  `OSError: [WinError 4551] An Application Control policy has blocked this file`, because the
  DLLs in the ROCm wheels are not Authenticode-signed. The reporter (RX 9060 XT, gfx1200)
  expects the official Windows runtime to load with SAC on. A maintainer replied that only the
  Windows tarballs are signed today and that Windows signing "has some blocking issues" that
  need internal discussion. Separately from signing, the error does not say which DLL was
  blocked, why, or what the user can do.
- Related open PRs/issues found:
  - [#2607](https://github.com/ROCm/TheRock/issues/2607) (closed): the same WinError 4551 on a
    Windows CI runner, caused by that runner's AppLocker/App Control setup and fixed by changing
    the runner's policy. Same symptom, different cause, no code overlap.
  - [#3973](https://github.com/ROCm/TheRock/pull/3973) (open, Windows packaging RFC): requires
    AMD-signed MSI installers. It does not cover wheel DLLs. Complementary.
  - [#1983](https://github.com/ROCm/TheRock/pull/1983) (open, last updated 2026-06-10): changes
    `preload_libraries()` for recursive dependency preloading. Same function, so a textual merge
    conflict is possible. No functional overlap.
  - [#8341](https://github.com/ROCm/TheRock/pull/8341) (open): ASAN preload for rocm-sdk wheel
    tests (CI only). No overlap.
  - No open PR addresses #8538. I searched open PRs for `4551`, `Smart App Control`,
    `App Control`, `WinError`, `signing`, `signed dll`, `preload_libraries`, and `rocm_sdk`.

## Root cause

There are two separate problems. Only the second one can be fixed in this repo.

1. **The DLLs are unsigned (AMD-internal, not fixable here).** The DLLs in the Windows Python
   wheels (`rocm-sdk-core`, `rocm-sdk-libraries`) are not Authenticode-signed. SAC first asks
   Microsoft's cloud reputation service about a file. If that service can't make a confident
   prediction, SAC only allows validly signed files and blocks the rest. `LoadLibraryExW` then
   fails with `ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION` (4551, defined for example in
   [Wine's `winerror.h`](https://github.com/wine-mirror/wine/blob/c54f1fcb2773ff8a1c3863586e0d6e172e996d20/include/winerror.h#L1753)).
   Evidence: the reporter's `Get-AuthenticodeSignature` audit (all 16 DLLs `NotSigned`) and the
   maintainer's comment on the issue. TheRock's own tree has no signing tooling (no `signtool`,
   `Authenticode`, `AzureSignTool`, or Trusted Signing references outside the submodules), which
   fits with signing being AMD-internal release infrastructure.
1. **The error is not actionable (in TheRock).** `rocm_sdk.preload_libraries()` calls
   `ctypes.CDLL()` with no error context
   ([`rocm_sdk/__init__.py#L114` @ `20abb1e9`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/build_tools/packaging/python/templates/rocm/src/rocm_sdk/__init__.py#L114)).
   On Windows, CPython's ctypes loader adds the module name only for `ERROR_MOD_NOT_FOUND`
   (126). Every other error is raised through `PyErr_SetFromWindowsErr(err)` without a filename
   ([CPython v3.11.9 `Modules/_ctypes/callproc.c#L1371-L1384`](https://github.com/python/cpython/blob/v3.11.9/Modules/_ctypes/callproc.c#L1371-L1384)).
   So users only see `[WinError 4551] An Application Control policy has blocked this file`.
   - The blocked file can also be a dependency of the DLL being loaded, because the loader
     resolves imports from the same directory. TheRock's Windows PyTorch preload list
     ([`build_prod_wheels.py#L202-L216`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/external-builds/pytorch/build_prod_wheels.py#L202-L216))
     has `hiprand` but not `rocrand`, and hipRAND wraps rocRAND. That explains why the reporter's
     "first observed blocked library" was `rocrand.dll`: it is a load-time dependency of
     `hiprand.dll`.
   - The exception reaches `import torch` unchanged. TheRock's documented idiom
     ([`python_packaging.md#L369-L375`](https://github.com/ROCm/TheRock/blob/20abb1e96819fe8a85935db7e329dd8a69396303/docs/packaging/python_packaging.md#L369-L375))
     and upstream PyTorch
     ([`torch/__init__.py#L181-L187` @ `dd9e8f78`](https://github.com/pytorch/pytorch/blob/dd9e8f785b19805a1da4a81c1958fbb9b0da82d4/torch/__init__.py#L181-L187))
     both call `_rocm_init.initialize()` outside their `try`, which only catches
     `ModuleNotFoundError` / `ImportError`.

- Minimal repro without Windows: an ad-hoc script (not committed) builds the multi-arch wheel
  layout (`_rocm_sdk_core/bin`, `_rocm_sdk_libraries/bin`) and a stand-in `torch` package that
  uses upstream's `_rocm_init` idiom with TheRock's Windows preload list. It pretends to be
  Windows and makes `ctypes.CDLL` raise winerror 4551 for `hiprand.dll` only. On the base commit
  the last line of the traceback is `OSError: [Errno 22] An Application Control policy has blocked this file` (`[WinError 4551]` on real Windows), with no DLL name.

## Fix

- `build_tools/packaging/python/templates/rocm/src/rocm_sdk/__init__.py`: `preload_libraries()`
  now catches `OSError` from `ctypes.CDLL()`. When `platform.system() == "Windows"` and
  `winerror == 4551`, it raises a new `OSError` with the same `errno` and `winerror`. The new
  message names the library and the resolved DLL path, says that a DLL it depends on may be the
  one that was blocked, names Smart App Control and App Control for Business as the likely cause,
  and links to the new `RELEASES.md` section. The original exception is kept as `__cause__`. Any
  other error, and every error on other platforms, is re-raised unchanged, and failed loads are
  not cached.
- `build_tools/packaging/python/tests/rocm_sdk_preload_test.py` (new, 7 tests): imports the
  `rocm_sdk` template from source and simulates Windows loader errors by making `ctypes.CDLL`
  raise the `OSError` CPython produces (with `winerror` set explicitly, because the constructor
  only sets it on Windows). It covers:
  - the 4551 message (library name, path, dependency note, cause, docs URL), `__cause__`, and
    `errno`, and that the failed load is not cached;
  - that `winerror` is kept on the translated error (Windows-only, `skipUnless win32`);
  - that other Windows errors (193) pass through unchanged;
  - that 4551 handling is Windows-only;
  - that a real loader failure (a non-library file, no mocking of `ctypes`) passes through
    unchanged;
  - that successful loads are still cached;
  - that the URL fragment in the message matches a real `RELEASES.md` heading, so docs and
    message can't drift apart.
- `RELEASES.md`: a new "Windows: ROCm DLLs blocked by Smart App Control (WinError 4551)"
  section under "Additional installation troubleshooting". It shows both the old and the new
  error text, says the wheel DLLs are unsigned and only the Windows tarballs are signed (quoting
  the maintainer, linking #8538), explains how to find the blocked file (Event Viewer,
  CodeIntegrity/Operational, event 3077), and lists options: signed tarballs where a workflow can
  use them (noting that `rocm_sdk` and PyTorch load DLLs from the wheels, not from tarballs), an
  App Control allow rule from an administrator, and turning SAC off, with its cost stated. A short
  `[!NOTE]` in "Installing multi-arch Python packages" links to the section.

Key diff (`preload_libraries()`, condensed):

```python
try:
    cdll = ctypes.CDLL(str(paths[0]), mode=mode)
except OSError as e:
    if (
        platform.system() == "Windows"
        and getattr(e, "winerror", None) == _ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION
    ):
        raise OSError(
            e.errno,
            f"An Application Control policy blocked loading the ROCm library "
            f"'{shortname}' from '{paths[0]}' (or a DLL that it depends on). ... "
            f"See {_APP_CONTROL_TROUBLESHOOTING_URL} ...",
            None,
            e.winerror,
        ) from e
    raise
```

On Windows, the last line users see becomes:

```text
OSError: [WinError 4551] An Application Control policy blocked loading the ROCm library 'hiprand' from '...\_rocm_sdk_libraries\bin\hiprand.dll' (or a DLL that it depends on). This usually means that Windows Smart App Control or App Control for Business is enforcing a policy that does not trust the DLL, for example because it is not code signed. See https://github.com/ROCm/TheRock/blob/main/RELEASES.md#windows-rocm-dlls-blocked-by-smart-app-control-winerror-4551 for how to find the blocked file and for workarounds.
```

Why this approach rather than the alternatives:

- **Signing** (Authenticode, or the AMD-signed catalog the reporter asked for) is the real fix,
  but it needs AMD certificates and release infrastructure. It is out of scope and a credential
  risk, so it was not attempted.
- **Same exception type.** The error stays an `OSError` with the same `errno` and `winerror`, so
  anything that catches or inspects it behaves the same. A custom subclass or a `RuntimeError`
  would change what callers see.
- **`raise ... from e` instead of `add_note()`.** `add_note()` needs Python 3.11+, and the
  `rocm` package declares no `python_requires`.
- **No retry and no skipping.** Skipping a blocked DLL would leave PyTorch half-initialized and
  hide the real problem.
- **In `rocm_sdk`, not PyTorch's generated `_rocm_init.py`.** `rocm_sdk` is shared by every
  framework and by `ROCM_SDK_PRELOAD_LIBRARIES`. That path already wraps errors in a
  `RuntimeError` with `from e`, so the new message shows up in its cause chain too.
- **Only 4551.** That is the code the issue (and #2607) report. AppLocker's code (1260) and the
  SAC reputation codes (4556–4559) are left as follow-ups because nothing here shows
  `LoadLibrary` returning them.
- **Wording that won't go stale.** The message says "for example because it is not code signed"
  instead of stating the current signing status. The status lives in the docs, which are easier
  to update than messages inside shipped wheels.
- **Behavior on other platforms and configs:** no change on Linux or macOS. On Windows, only the
  message of 4551 load errors changes; the type, `errno`, and `winerror` stay the same.

## Branch

- Base: `main` @ `20abb1e96819fe8a85935db7e329dd8a69396303`
- Branch name: `cursor/windows-app-control-dll-error-eb28`, pushed to the fork `rysuds/TheRock`
  (not pushed to ROCm/TheRock, no PR opened). The name follows this agent environment's required
  `cursor/...` pattern instead of TheRock's `users/<username>/...` convention.
- Commits:
  - `4f5d0e89` docs: explain WinError 4551 (Smart App Control) for Windows Python packages
  - `5191e080` fix(rocm_sdk): name the DLL when App Control blocks a preload
  - this report, committed separately as the final commit (its SHA is given in the hand-off
    message, since a commit can't contain its own hash)

## Validation performed in the cloud agent environment

Environment: Linux x86_64 (kernel 6.12.94+), no AMD GPU (no `/dev/kfd` or `/dev/dri`), Python
3.12.3 in a venv with `requirements-test.txt` (pytest 9.0.3, same as `unit_tests.yml`) plus
pre-commit 4.6.2. CMake 3.28.3 and ninja 1.11.1 were used only by existing CMake-based unit
tests; there was no TheRock configure or build.

| #   | Command (exact)                                                                                                                                                                                               | Result                                                                                      | Notes                                                                                                                                                                                                                                                                                                    |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `cd build_tools && python -m pytest packaging/python/tests/rocm_sdk_preload_test.py -vv` (new tests, before editing `__init__.py` and `RELEASES.md`)                                                          | 2 failed, 4 passed, 1 skipped                                                               | Failed: `test_app_control_block_names_library_and_links_docs` (`"'hiprand'" not found in '[Errno 22] An Application Control policy has blocked this file'`) and `test_url_points_at_releases_md_heading` (no `_APP_CONTROL_TROUBLESHOOTING_URL`). The 4 unchanged-behavior tests pass before and after.  |
| 2   | Same as 1, after the fix                                                                                                                                                                                      | 6 passed, 1 skipped                                                                         | Skipped: `test_app_control_block_keeps_winerror` (Windows-only).                                                                                                                                                                                                                                         |
| 3   | Block A below (restores the base files over the committed tests)                                                                                                                                              | 2 failed, 4 passed, 1 skipped                                                               | Same failures as row 1. Tree restored clean afterwards.                                                                                                                                                                                                                                                  |
| 4   | `cd build_tools && python -m pytest -vv --durations=20 --cov --cov-report=term-missing --cov-report=html` (branch)                                                                                            | 1 failed, 2439 passed, 12 skipped, 292 subtests passed                                      | Only failure: `tests/artifact_backend_test.py::TestS3BackendCredentials::test_list_objects_unsigned` (SSL error reaching S3; network egress is blocked here).                                                                                                                                            |
| 5   | Same as 4, in a worktree of the base commit (`git worktree add --detach /tmp/base-wt 20abb1e96819fe8a85935db7e329dd8a69396303`)                                                                               | 1 failed, 2433 passed, 11 skipped, 292 subtests passed                                      | Same single failure. The difference from row 4 is exactly the 7 new tests (6 pass, 1 skip). An earlier branch run without `ninja` had 14 failures (8 tests and 5 subtests that need Ninja, plus S3). Installing `ninja-build`, which GitHub-hosted runners have, cleared them; rows 4 and 5 both use it. |
| 6   | `cd build_tools && python -m pytest packaging/windows/tests packaging/python/tests -q -p no:cacheprovider`                                                                                                    | 238 passed, 1 skipped                                                                       | The playbook's targeted packaging suites.                                                                                                                                                                                                                                                                |
| 7   | `cd build_tools && python -m pytest tests/py_packaging_test.py tests/sdk_targets_test.py -q -p no:cacheprovider`                                                                                              | 87 passed, 17 subtests passed                                                               | Template packaging and `_dist_info.py` rendering.                                                                                                                                                                                                                                                        |
| 8   | `python /tmp/sim8538/mutate.py` (ad-hoc, not committed)                                                                                                                                                       | 4 of 4 mutations caught                                                                     | Dropping the Windows gate, translating every `winerror`, dropping `from e`, and dropping handle caching each fail exactly the intended test.                                                                                                                                                             |
| 9   | `python /tmp/sim8538/simulate_8538.py /workspace` and `python /tmp/sim8538/simulate_8538.py /tmp/base-wt` (ad-hoc, not committed)                                                                             | After: the last line names `hiprand`, its path, and the docs URL. Before: generic text only | The repro described under Root cause. `find_libraries()` is not mocked. Loaded before the failure: `amd_comgr_3.dll`, `amdhip64_7.dll`, `hiprtc0700.dll`, `hipblas.dll`, `hipfft.dll`, which matches the Windows preload order.                                                                          |
| 10  | `pre-commit run --files RELEASES.md build_tools/packaging/python/templates/rocm/src/rocm_sdk/__init__.py build_tools/packaging/python/tests/rocm_sdk_preload_test.py`                                         | Passed                                                                                      | black (hook rev 25.11.0), mdformat (gfm), and the whitespace, EOF, tab, and large-file hooks. The new file has to be tracked (`git add -N`) for the hooks to check it.                                                                                                                                   |
| 11  | `pre-commit run --hook-stage manual lychee --files RELEASES.md`                                                                                                                                               | Passed                                                                                      | lychee 0.24.2 with `--offline`, so remote links are skipped.                                                                                                                                                                                                                                             |
| 12  | Block B below (lychee with `--include-fragments`, plus a negative control)                                                                                                                                    | 31 OK, 0 errors; the negative control reports `Cannot find fragment`                        | Confirms the new `#windows-rocm-dlls-blocked-by-smart-app-control-winerror-4551` and `#installing-multi-arch-tarballs` anchors resolve.                                                                                                                                                                  |
| 13  | Block C below (GitHub-rendered `RELEASES.md` on the pushed branch)                                                                                                                                            | `id="user-content-windows-rocm-dlls-blocked-by-smart-app-control-winerror-4551"` present    | GitHub's own renderer produces the anchor that the error message and the note link to.                                                                                                                                                                                                                   |
| 14  | `bandit --configfile build_tools/scan_tools/bandit.yml --severity-level low build_tools/packaging/python/templates/rocm/src/rocm_sdk/__init__.py build_tools/packaging/python/tests/rocm_sdk_preload_test.py` | No issues identified                                                                        | bandit 1.9.4.                                                                                                                                                                                                                                                                                            |
| 15  | `pre-commit run --files reports/report-8538.md`                                                                                                                                                               | Passed                                                                                      | This report.                                                                                                                                                                                                                                                                                             |

Block A (pre-fix state over the committed tests, row 3):

```bash
git checkout 20abb1e96819fe8a85935db7e329dd8a69396303 -- \
  build_tools/packaging/python/templates/rocm/src/rocm_sdk/__init__.py RELEASES.md
(cd build_tools && python -m pytest packaging/python/tests/rocm_sdk_preload_test.py -v)
git checkout HEAD -- \
  build_tools/packaging/python/templates/rocm/src/rocm_sdk/__init__.py RELEASES.md
```

Block B (row 12; the binary is the one the lychee pre-commit hook installs):

```bash
L=~/.cache/pre-commit/repo9j_ulb19/.cargo/bin/lychee-0.24.2
$L --no-progress --offline --root-dir "$PWD" --include-fragments RELEASES.md
# Negative control: t.md has the new heading plus one good and one bad same-page anchor.
cd /tmp/lychee-neg && $L --no-progress --offline --root-dir "$PWD" --include-fragments --verbose t.md
```

Block C (row 13):

```bash
gh api -H "Accept: application/vnd.github.html" \
  "repos/rysuds/TheRock/contents/RELEASES.md?ref=cursor/windows-app-control-dll-error-eb28" \
  | grep -o 'id="user-content-windows[^"]*"'
```

## What could not be validated and why

- **Real Windows behavior with SAC or App Control enforcing.** Not checked: that `LoadLibraryExW`
  returns 4551 for the blocked ROCm DLLs and that users then see the new message. This needs
  Windows 11 and a supported AMD GPU. To validate, build Windows wheels from this branch (for
  example with `.github/workflows/build_windows_python_packages.yml`, or the Windows multi-arch
  CI on a PR). Then install `rocm[libraries,device-gfx1200]` and a matching `torch` into a venv
  on a Windows 11 machine with SAC in enforcement mode, and run `python -c "import torch"`. A test
  VM with an enforced App Control for Business policy that denies `hiprand.dll` works too. Expect
  the new message naming `hiprand` and a matching event 3077 in CodeIntegrity/Operational.
- **The Windows-only unit test** (`test_app_control_block_keeps_winerror`) and the rest of the
  new tests on Windows. They run on the `windows-2022` job of
  `.github/workflows/unit_tests.yml` (on `pull_request`, pushes to `main`, or
  `workflow_dispatch`). They did not run because no PR was opened, and the fork's workflows only
  trigger on `main` and on PRs.
- **The exact Windows formatting** (`[WinError 4551] ...`) of the translated error. This follows
  documented CPython behavior (on Windows, the 4-argument `OSError` constructor sets `winerror`
  and derives `errno` from it), checked here only by reading the CPython source. The Windows unit
  test above asserts `winerror == 4551`.
- **Other block codes:** AppLocker (1260) and SAC reputation codes (4556–4559) are not handled,
  and I found no evidence of which of them `LoadLibrary` returns for these DLLs.
- **Building and installing the real `rocm` wheels** from the template (this needs build
  artifacts), `rocm-sdk test` on installed wheels (`test_rocm_wheels.yml`, GPU runners), and the
  Windows PyTorch release workflows in `ROCm/rockrel`.
- **External links** (Microsoft's SAC FAQ, `stable.repo.amd.com`) could not be fetched from this
  VM because egress is blocked. The Microsoft facts in the docs were checked against Microsoft
  Learn and Support content returned by web search, not by opening those pages: event 3077 means
  an enforced block, SAC has no per-file exceptions, and whether SAC can be turned back on
  depends on the Windows version.
- **The docs URL in the message** points at `ROCm/TheRock` `main`, so it only resolves after
  merge. The anchor was verified on the fork's rendered branch (row 13).
- **PyTorch's own DLLs.** Whether they are signed is unknown. If they are not, allowing the ROCm
  DLLs may just move the block to a torch DLL. Upstream PyTorch's loader already names the DLL in
  that case
  ([`torch/__init__.py#L290-L315`](https://github.com/pytorch/pytorch/blob/dd9e8f785b19805a1da4a81c1958fbb9b0da82d4/torch/__init__.py#L290-L315)).

## Confidence

- **High: 85%**
- Justification: every branch of the change is covered by unit tests that ran here: the 4551
  path, the non-4551 path, the non-Windows gate, exception chaining, and caching. Four injected
  bugs were each caught, an end-to-end simulation resolved real paths through `find_libraries()`,
  and the full `build_tools` suite shows no regressions against the base commit. The remaining
  uncertainty: nothing ran on real Windows (the Windows `OSError` behavior is from reading
  CPython's source, and the Windows-only test has not run yet); maintainers may prefer different
  doc wording or placement; and the change does not fix the underlying signing gap, so #8538
  stays open for that.

## Risks / follow-ups

- Regression risk is low. Code that string-matches the old message on the top-level exception
  would stop matching. The original text is still available on `__cause__`.
- Possible textual merge conflict with the stale
  [#1983](https://github.com/ROCm/TheRock/pull/1983), which edits the same function.
- The "not currently code signed" and "only the Windows tarballs are currently signed" statements
  in `RELEASES.md` must be updated when signing lands. They quote maintainers and give no ETA.
  `TroubleshootingUrlTest` keeps the anchor and the message in sync.
- Follow-ups:
  1. (AMD-internal) Sign the DLLs in the Windows wheels, or publish an AMD-signed catalog as
     the reporter proposed. Check whether the PyTorch wheel DLLs need the same.
  1. Optionally, add a CI check on a Windows runner that reports the Authenticode status of the
     shipped DLLs. This is a proposal only.
  1. Handle AppLocker (1260) and the SAC reputation codes if real reports show them.
  1. When opening the upstream PR, reference the issue with `ISSUE ID :` rather than `Fixes`,
     because signing is still open. The PR bot accepts that form, and the new test file matches
     its `*_test.*` pattern.
- Draft PR description (not filed), following `.github/pull_request_template.md`:

```markdown
## Motivation

On Windows with Smart App Control or App Control for Business enforcing, `import torch` from the
ROCm Python packages fails with `OSError: [WinError 4551] An Application Control policy has
blocked this file` and does not say which DLL was blocked. Signing the wheel DLLs is
AMD-internal and tracked separately; this PR makes the failure actionable.

ISSUE ID : https://github.com/ROCm/TheRock/issues/8538

## Technical Details

- `rocm_sdk.preload_libraries()`: on Windows, translate winerror 4551 from `ctypes.CDLL()`
  into an `OSError` (same errno/winerror, original kept as `__cause__`) that names the library
  and its path, explains the likely cause, and links to the troubleshooting docs. All other
  errors are re-raised unchanged.
- `RELEASES.md`: Windows troubleshooting section for WinError 4551 and a note in the Python
  package install section.

## Test Plan

New `build_tools/packaging/python/tests/rocm_sdk_preload_test.py` (Linux and Windows unit test
jobs), full `build_tools` pytest suite, pre-commit (black, mdformat), lychee.

## Test Result

New tests fail before the fix and pass after; no regressions in the `build_tools` suite. Not
validated on a real Windows machine with Smart App Control enforcing.

This change was prepared with an AI coding agent and reviewed by the submitter.

## Submission Checklist

- [x] Look over the contributing guidelines at https://github.com/ROCm/TheRock/blob/main/CONTRIBUTING.md.
```
