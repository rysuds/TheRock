# Report: ROCm/TheRock#8554: gfx1201 Windows tall-skinny matmul silently returns wrong results

## Issue

- Link: https://github.com/ROCm/TheRock/issues/8554
- Type / skill used: `gpu-runtime-bug` (`gpu-runtime-bug.md`)
- Summary (2–4 sentences): On an RX 9070 XT (`gfx1201`) with the Windows
  PyTorch 2.9.1 + ROCm 7.2.1 wheels, contiguous `[N,3] @ [3,3]` silently
  becomes incorrect at `N=524289`; `N=524288` is correct. The report covers
  fp32 and fp64 and several PyTorch entry points, while chunking the leading
  dimension avoids the defect. The expected result is a finite GPU result
  within dtype-appropriate tolerance of the CPU result for every valid size.
- Related open PRs/issues found (if any) and how this work relates to them
  (duplicate, complement, review):
  - [ROCm/rocm-libraries#8645](https://github.com/ROCm/rocm-libraries/issues/8645)
    is the same bug family on Linux gfx1201 with ROCm 7.1/7.2. Its coarse scan
    found the default hipBLASLt fp32 path correct at 500k rows and corrupt at
    700k rows, which brackets this issue's exact `2^19` boundary. A maintainer
    reports that the hipBLASLt path works on a current nightly.
  - Merged
    [ROCm/rocm-libraries#2681](https://github.com/ROCm/rocm-libraries/pull/2681)
    is the most likely existing fix. It is titled “gfx12 Fix ttmp init
    workaround,” explicitly fixes workgroup IDs above `2^15`, changes gfx12
    launches from multidimensional to flattened 1D, adds fp32/fp64 large-grid
    tests, and records a closely related `m=3, n=2073600, k=3` reproducer.
  - Open
    [ROCm/rocm-libraries#9184](https://github.com/ROCm/rocm-libraries/pull/9184)
    complements #2681 for the forced legacy rocBLAS/Tensile backend. Its
    measured boundaries are `2^21` for fp64 and `2^22` for fp32, so it does
    not explain this issue's shared `2^19` boundary. It is currently
    conflicting and has outstanding review discussion.
  - No open TheRock PR matching issue #8554 was found. An unrelated PR matched
    only because it mentioned the number `524288`.

## Root cause

- The most likely cause is the old **hipBLASLt/TensileLite gfx12
  multidimensional launch/workgroup-ID path**, not TheRock build glue:
  - PyTorch converts row-major `[N,3] @ [3,3]` into column-major
    `m=3, n=N, k=3`. The row/column swap is documented in
    [`pytorch/pytorch@v2.9.1: aten/src/ATen/native/cuda/Blas.cpp#L108-L192`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/native/cuda/Blas.cpp#L108-L192).
  - The matching ROCm 7.2.1 Windows wheel branch
    [`ROCm/TheRock@ba21f57`](https://github.com/ROCm/TheRock/commit/ba21f57e0275d8c2b64cf4b52966db996b918090)
    pins rocm-libraries `171b8698`. That revision leaves gfx12 as a 2D/3D
    launch while flattening other architectures in
    [`ContractionSolution.cpp#L1271-L1346`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/tensilelite/src/ContractionSolution.cpp#L1271-L1346),
    then restores gfx12 workgroup IDs from temporary registers in
    [`StreamK.py#L1704-L1718`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/tensilelite/Tensile/Components/StreamK.py#L1704-L1718).
  - The release's gfx1201 grid-based fp32 and fp64 catalogs both contain
    `MacroTile1: 16` solutions:
    [`fp32#L320-L330`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/library/src/amd_detail/rocblaslt/src/Tensile/Logic/asm_full/gfx1201/GridBased/gfx1201_Cijk_Ailk_Bljk_SB_Bias_HAS_SAV_UserArgs.yaml#L320-L330)
    and
    [`fp64#L320-L330`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/library/src/amd_detail/rocblaslt/src/Tensile/Logic/asm_full/gfx1201/GridBased/gfx1201_Cijk_Ailk_Bljk_DB_UserArgs.yaml#L320-L330).
    If that solution is selected, `N=524288` needs exactly
    `524288 / 16 = 32768 = 2^15` workgroups in the large free dimension;
    `N=524289` needs workgroup ID 32768, the first ID beyond that limit. This
    exactly matches both the observed transition and #2681's stated defect.
    Capturing `--print_kernel_info` on the affected machine is still required
    to prove the selected solution.
  - The fix in
    [`507afd7d`](https://github.com/ROCm/rocm-libraries/commit/507afd7d815aad804e923629b28147318047407a)
    removes the gfx12 exception, flattens the launch into grid X, and restores
    the logical 3D workgroup IDs at kernel entry. The ROCm 7.2.1 tag and wheel
    pin retain the old code, while TheRock 7.11 and later pins contain
    `507afd7d`. The current TheRock base pins rocm-libraries `6054c511`, which
    also contains the fix.
- The reported preference experiment does not establish two independent
  backend failures:
  - In PyTorch 2.9.1,
    [`TORCH_BLAS_PREFER_HIPBLASLT=0` leaves the backend at `Default`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/Context.h#L478-L482);
    `Default` automatically selects hipBLASLt on gfx1201 in
    [`Context.cpp#L464-L524`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/Context.cpp#L464-L524).
  - PyTorch sends fp32 directly to hipBLASLt but sends fp64 to rocBLAS because
    that PyTorch revision says direct hipBLASLt double GEMM is unsupported
    ([`CUDABlas.cpp#L1261-L1298`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/cuda/CUDABlas.cpp#L1261-L1298)).
    The Windows TheRock build enables rocBLAS's hipBLASLt backend
    ([`math-libs/BLAS/CMakeLists.txt#L172-L203`](https://github.com/ROCm/TheRock/blob/ba21f57e0275d8c2b64cf4b52966db996b918090/math-libs/BLAS/CMakeLists.txt#L172-L203)),
    and rocBLAS defaults gfx1201 to hipBLASLt
    ([`handle.hpp#L284-L321`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/rocblas/library/src/include/handle.hpp#L284-L321)).
    Therefore both reported dtypes plausibly reach the same faulty
    hipBLASLt/TensileLite launch.
- Evidence: the exact power-of-two transition; the matching old source and
  catalog geometry; the existing fix's title, implementation, regression
  tests, and near-identical `m=3, k=3` reproducer; the related Linux report;
  and version ancestry. This remains source-based localization, not a hardware
  diagnosis.
- Ownership: **upstream**, in `ROCm/rocm-libraries`, principally
  `projects/hipblaslt/tensilelite/src/ContractionSolution.cpp` and
  `projects/hipblaslt/tensilelite/Tensile/KernelWriterAssembly.py` (the old
  reconstruction was in `Tensile/Components/StreamK.py`).

## Fix

- What changed and why:
  - `reports/8554/repro_torch_matmul.py` adds a deterministic torch-level
    fp32/fp64 boundary scan, CPU comparison, mismatch location, and chunked-GPU
    control.
  - `reports/8554/library-repro.md` maps the PyTorch operation to BLAS
    arguments and provides direct hipBLASLt plus rocBLAS commands for both
    sides of the boundary and both backend modes.
  - `reports/report-8554.md` records the source localization, related upstream
    work, limitations, and an unfiled upstream draft.
- Key diffs summarized:

  ```python
  cpu_reference = cpu_a @ cpu_b
  direct = (gpu_a @ gpu_b).cpu()
  chunks = [
      (gpu_a[start : start + chunk_size] @ gpu_b).cpu()
      for start in range(0, n, chunk_size)
  ]
  ```

- Why this approach rather than alternatives:
  - No TheRock product code is wrong at the current base, and its current
    rocm-libraries pin already contains the likely upstream fix.
  - Changing a submodule pin or carrying a patch would be an unvalidated
    regression risk and would duplicate merged upstream work.
  - A reproducer and version-specific backport report are the appropriate
    deliverables without matching hardware.
- Mitigation:
  - Keep each leading-dimension chunk at or below 262144 rows.
  - Test a TheRock 7.11-or-newer/current-nightly Windows stack, whose
    rocm-libraries pin contains `507afd7d`. This is a source-derived
    recommendation and is not hardware-validated here.
  - To isolate the separate legacy Tensile path, set
    `ROCBLAS_USE_HIPBLASLT=0` on a call known to reach rocBLAS and compare with
    open PR #9184.
- Drafted upstream issue text (not filed):

  ````markdown
  Title: [hipBLASLt][gfx1201][ROCm 7.2 Windows] Backport gfx12 workgroup-ID fix for [N,3]@[3,3] above N=2^19

  Repository: ROCm/rocm-libraries

  On the ROCm 7.2.1 Windows PyTorch wheels, `torch.mm` for contiguous
  `[N,3] @ [3,3]` is correct at N=524288 and silently wrong at N=524289 for
  both fp32 and fp64. Chunking to 262144 rows is correct. The affected wheel
  branch pins rocm-libraries 171b8698, whose gfx12 path keeps a
  multidimensional launch in
  `projects/hipblaslt/tensilelite/src/ContractionSolution.cpp` and restores
  workgroup IDs later from TTMP registers.

  This appears to be the release-branch form of merged PR #2681 / commit
  507afd7d ("gfx12 Fix ttmp init workaround"). With a MacroTile1=16
  solution, N=524288 is exactly 32768 (2^15) workgroups and N=524289
  requires the first workgroup beyond the limit. The 7.2 tag does not contain
  507afd7d; TheRock 7.11+ does. Related issue #8645 reports the same old-stack
  failure family and says current-nightly hipBLASLt is correct. Open PR #9184
  addresses a separate forced rocBLAS/Tensile grid.y limit at larger,
  dtype-dependent boundaries.

  Reproducer:

  ```python
  import torch

  torch.manual_seed(0)
  for dtype in (torch.float32, torch.float64):
      for n in (524288, 524289):
          a = torch.randn(n, 3, dtype=dtype)
          b = torch.randn(3, 3, dtype=dtype)
          ref = a @ b
          got = (a.cuda() @ b.cuda()).cpu()
          print(dtype, n, (got - ref).abs().max().item())
  ```

  Please confirm the selected solution with hipBLASLt logging on gfx1201,
  backport 507afd7d (or its current equivalent) to the maintained ROCm 7.2
  Windows line, and add an exact 524288/524289 fp32+fp64 regression case.
  If 7.2 will not receive the fix, please document the first fixed Windows
  wheel version.
  ````

## Branch

- Base: `main` @ `20abb1e96819fe8a85935db7e329dd8a69396303`
- Branch name: `cursor/investigate-8554-gfx1201-564d` (pushed only to
  `rysuds/TheRock`)
- Commits: N/A in this pre-validation report revision; the final report will
  record the resulting commit SHAs.

## Validation performed in the cloud agent environment

Environment: Linux x86_64, no AMD GPU, Python 3.12.3, CMake 3.28.3.

| # | Command (exact) | Result | Notes |
|---|---|---|---|
| 1 | `python3 -m py_compile reports/8554/repro_torch_matmul.py` | Pending | Run after the required pre-validation commit |
| 2 | `python3 reports/8554/repro_torch_matmul.py --help` | Pending | Exercises CLI parsing without importing torch |
| 3 | `pre-commit run --files reports/8554/repro_torch_matmul.py reports/8554/library-repro.md reports/report-8554.md` | Pending | `pre-commit` must be installed first |
| 4 | `git diff --check HEAD^ HEAD` | Pending | Whitespace validation |

Pre-fix fail / post-fix pass is N/A in this repository: no product fix was
made, and reproducing the upstream GPU defect requires unavailable hardware.

## What could not be validated and why

- The Windows 11 + RX 9070 XT failure, selected kernel/solution, first bad
  output row, and silent launch behavior could not be measured because this
  VM is Linux-only and has no AMD GPU. A maintainer should run the torch
  reproducer with `HIPBLASLT_LOG_MASK=32` and replay the logged command with
  `hipblaslt-bench --print_kernel_info`.
- The direct `hipblaslt-bench` and `rocblas-bench` commands are unexecuted
  because neither ROCm clients nor a GPU are present. Run all four boundary
  combinations from `reports/8554/library-repro.md` on the affected wheel
  payload.
- Commit `507afd7d` could not be proven to fix this exact shape. Validate the
  affected ROCm 7.2.1 build and a current TheRock 7.11+/nightly build on the
  same gfx1201 Windows host with identical inputs.
- The pure legacy rocBLAS/Tensile path could not be separated. Set
  `ROCBLAS_USE_HIPBLASLT=0`, confirm the internal backend with
  `ROCBLAS_LAYER=10`, and test both this `2^19` boundary and PR #9184's
  `2^21`/`2^22` boundaries.
- The exact installed wheel manifest was not attached to issue #8554. The
  source revision mapping uses the matching public
  `pytorch2.9.1-rocm7.2.1_windows` branch; inspect the reporter's wheel
  manifest to confirm the same submodule SHA.

## Confidence

- **Low: 65%**
- Justification: the exact threshold, dtype routing, release source, related
  hardware report, and already-merged fix form a coherent mechanism with no
  contradictory source evidence. Confidence remains low because the affected
  kernel selection and before/after behavior were not observed on gfx1201,
  Windows, or any GPU in this environment, and a separate forced-Tensile
  large-N defect is still open.

## Risks / follow-ups

- Capture the exact hipBLASLt solution index, kernel name, macro-tile, launch
  dimensions, and first mismatching row at `N=524288` and `N=524289`.
- Test `507afd7d` or a release containing it on the reporter's Windows host;
  do not treat source ancestry alone as proof of a fix.
- Confirm the backend with logs. `TORCH_BLAS_PREFER_HIPBLASLT=0` is not a
  force-off switch in PyTorch 2.9.1 on gfx1201.
- Coordinate with [rocm-libraries#8645](https://github.com/ROCm/rocm-libraries/issues/8645)
  rather than opening a duplicate unless maintainers prefer a release-specific
  backport issue.
- Continue review of
  [rocm-libraries#9184](https://github.com/ROCm/rocm-libraries/pull/9184) for
  users who explicitly force `ROCBLAS_USE_HIPBLASLT=0`; its broad chunking
  change may affect performance and remains under discussion.
