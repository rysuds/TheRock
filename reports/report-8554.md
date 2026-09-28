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
    [ROCm/rocm-libraries#2753](https://github.com/ROCm/rocm-libraries/pull/2753)
    and
    [#2681](https://github.com/ROCm/rocm-libraries/pull/2681) are the paired
    fp32 fix: #2753 replaces undersized gfx12 fp32/fp64 catalog tiles, while
    #2681 flattens the gfx12 launch and adds the exact
    `{m=3,n=524289,k=3}` regression shape. Closed
    [rocm-libraries#5706](https://github.com/ROCm/rocm-libraries/issues/5706)
    independently reproduced the silent gfx1201 corruption with
    `hipblaslt-bench` on ROCm 7.2 and confirmed `507afd7d` fixes it.
  - Open
    [ROCm/rocm-libraries#9184](https://github.com/ROCm/rocm-libraries/pull/9184)
    fixes a separate legacy rocBLAS/Tensile grid-Y defect. Its verified
    boundaries are `2^21` for fp64 and `2^22` for fp32, so it is only an
    alternative for issue #8554 if the affected call rejects internal
    hipBLASLt and falls back. It remains unmerged and conflicting.
  - No open TheRock PR matching issue #8554 was found. An unrelated PR matched
    only because it mentioned the number `524288`.

## Root cause

- The demonstrated fp32 failure is effectively identified as the old
  **hipBLASLt/TensileLite gfx12 multidimensional-grid defect** fixed by paired
  [PR #2753](https://github.com/ROCm/rocm-libraries/pull/2753) and
  [PR #2681](https://github.com/ROCm/rocm-libraries/pull/2681), not TheRock
  build glue:
  - PyTorch converts row-major `[N,3] @ [3,3]` into column-major
    `m=3, n=N, k=3`. The row/column swap is documented in
    [`pytorch/pytorch@v2.9.1: aten/src/ATen/native/cuda/Blas.cpp#L108-L192`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/native/cuda/Blas.cpp#L108-L192).
  - The ROCm 7.2 gfx1201 selector maps the oversized-grid edge to
    [solution 0](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/library/src/amd_detail/rocblaslt/src/Tensile/Logic/asm_full/gfx1201/GridBased/gfx1201_Cijk_Ailk_Bljk_SB_Bias_HAS_SAV_UserArgs.yaml#L1238-L1239),
    an
    [`MT8x8` kernel](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/library/src/amd_detail/rocblaslt/src/Tensile/Logic/asm_full/gfx1201/GridBased/gfx1201_Cijk_Ailk_Bljk_SB_Bias_HAS_SAV_UserArgs.yaml#L114-L197).
    Since gfx12 grid Y/Z is 16-bit,
    `524288 = 65536 * 8` is the last representable launch; `N=524289` needs
    workgroup ID 65536.
  - The affected wheel pins rocm-libraries `171b8698`. It computes separate
    logical X/Y counts but skips flattening Y/Z on gfx12 in
    [`ContractionSolution.cpp#L1271-L1347`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/tensilelite/src/ContractionSolution.cpp#L1271-L1347).
    TensileLite uses
    [`hipExtModuleLaunchKernel`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/tensilelite/src/hip/HipSolutionAdapter.cpp#L373-L403),
    and the affected CLR revision omitted the Y/Z bounds check present on the
    ordinary module launch
    ([`hip_module.cpp#L534-L604`](https://github.com/ROCm/rocm-systems/blob/a7cfb4d18e54d55307ca6f11804049e84f517094/projects/clr/hipamd/src/hip_module.cpp#L534-L604)).
    That explains why output can be silently omitted instead of returning an
    API error.
  - PR #2753 merge commit
    [`98aa2c31`](https://github.com/ROCm/rocm-libraries/commit/98aa2c31e8c2d17f60c52047114fbf93e42962e9)
    replaces the problematic small gfx12 catalog tiles with MT64-class
    solutions. PR #2681 merge commit
    [`507afd7d`](https://github.com/ROCm/rocm-libraries/commit/507afd7d815aad804e923629b28147318047407a)
    flattens logical Y/Z into physical X and restores logical IDs in the
    kernel prologue. Its initial commit added the exact
    [`{3,524289,3}` test](https://github.com/ROCm/rocm-libraries/blob/69ca701c824e780ff737b7a22c4593ccdc367eac/projects/hipblaslt/clients/tests/data/matmul_common.yaml#L51-L60).
    ROCm 7.2 deliberately lacks the complete fix, as release PR
    [#5292](https://github.com/ROCm/rocm-libraries/pull/5292) documents; the
    current TheRock pin contains it.
- The reported preference experiment does not isolate backends, and the exact
  fp64 mechanism remains unresolved:
  - In PyTorch 2.9.1,
    [`TORCH_BLAS_PREFER_HIPBLASLT=0` leaves the backend at `Default`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/Context.h#L478-L482);
    `Default` automatically selects hipBLASLt on gfx1201 in
    [`Context.cpp#L464-L524`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/Context.cpp#L464-L524).
  - PyTorch bypasses its own direct Lt helper for fp64 in
    [`CUDABlas.cpp#L1261-L1278`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/cuda/CUDABlas.cpp#L1261-L1278)
    but then calls hipBLAS/rocBLAS. This TheRock build enables rocBLAS's
    internal hipBLASLt delegation, whose routing has no double exclusion and
    maps double to `HIP_R_64F`/`HIPBLAS_COMPUTE_64F`
    ([`hipblaslt_host.cpp#L44-L90`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/rocblas/library/src/hipblaslt_host.cpp#L44-L90)).
    Therefore fp64 can still reach the affected TensileLite path indirectly.
  - However, the double GridBased edge predicts an MT16 solution, whose
    16-bit-Y boundary is `2^20`, not `2^19`. The exact fp64 claim in issue
    #8554 has no posted solution log or per-size output. Plausible explanations
    are a different runtime-selected MT8 solution, a related effective
    `2^15` remapping defect referenced by #2681, or fallback to the separate
    legacy-Tensile bug in open PR #9184. Hardware logging is required to
    distinguish them.
- Evidence for fp32: exact boundary arithmetic, exact release selector and
  tile, the paired catalog/launch fixes, exact regression shape, issue #5706's
  direct fail-before/pass-after confirmation, and release #5292's explicit
  statement that #2681 is absent. Confidence for fp64 is materially lower.
- Ownership: **upstream**, in `ROCm/rocm-libraries`, principally
  `projects/hipblaslt/tensilelite/src/ContractionSolution.cpp`; classic
  `projects/rocblas/library/src/blas3/Tensile/gemm_templates.cpp` remains an
  alternative only if logs show fallback.

## Fix

- What changed and why:

  - `reports/8554/repro_torch_matmul.py` adds a deterministic torch-level
    fp32/fp64 boundary scan, CPU comparison, mismatch location, and chunked-GPU
    control.
  - `reports/8554/library-repro.md` maps the PyTorch operation to BLAS
    arguments and provides direct fp32/fp64 hipBLASLt probes plus fp32/fp64
    rocBLAS commands for both sides of the boundary.
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

  - No TheRock product code is wrong, and its current rocm-libraries pin
    already contains catalog update `98aa2c31` and launch fix `507afd7d`.
  - Backporting component code through TheRock or carrying unmerged PR #9184
    without gfx1201 hardware would be an unvalidated correctness and
    performance risk.
  - A reproducer and version-specific backport report are the appropriate
    deliverables without matching hardware.

- Mitigation:

  - Keep each leading-dimension chunk at or below 262144 rows.
  - Test a current-nightly Windows stack containing `98aa2c31` and
    `507afd7d`. Existing Linux gfx1201 evidence confirms the paired fp32 fix;
    fp64 still needs explicit boundary and solution logging.
  - Use `ROCBLAS_USE_HIPBLASLT=0` plus `ROCBLAS_LAYER=10` to isolate and
    determine whether the separate legacy-Tensile defect participates.

- Drafted upstream issue text (not filed):

  ````markdown
  Title: [hipBLASLt][gfx1201][ROCm 7.2 Windows] Backport #2753/#2681 for N=524289 GEMM corruption; clarify fp64

  Repository: ROCm/rocm-libraries

  On the ROCm 7.2.1 Windows PyTorch wheels, `torch.mm` for contiguous
  `[N,3] @ [3,3]` is correct at N=524288 and silently wrong at N=524289 for
  both fp32 and fp64. Chunking to 262144 rows is correct. PyTorch maps this to
  column-major `m=3, n=N, k=3`; `524288 = 65536 * 8`, exactly the gfx1201
  grid-Y capacity of the fp32 selector's MT8 solution.

  The affected wheel pins rocm-libraries 171b8698. Its
  `projects/hipblaslt/tensilelite/src/ContractionSolution.cpp` keeps a
  multidimensional gfx12 launch, while the selected catalog uses an undersized
  MT8 kernel. Merged PR #2753 / commit 98aa2c31 replaces the small catalog
  tiles; PR #2681 / commit 507afd7d flattens the launch and adds the exact
  `{3,524289,3}` regression. Issue #5706 independently confirms the old-stack
  failure and paired backport on Linux gfx1201. Release PR #5292 confirms the
  complete launch fix is absent from ROCm 7.2.

  PyTorch bypasses its direct Lt helper for fp64, but this package's rocBLAS
  can delegate double GEMM back to hipBLASLt. The default double selector
  predicts MT16, so the asserted fp64 transition at 2^19 is not yet explained;
  please capture its actual backend and solution. If it falls back to classic
  Tensile, open PR #9184 is the adjacent candidate.

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

  Please backport #2753 and #2681 to the maintained ROCm 7.2 Windows line (or
  document the first fixed wheel), add an exact 524288/524289 fp32
  regression, and capture the fp64 backend/solution before assigning its fix.
  ````

## Branch

- Base: `main` @ `20abb1e96819fe8a85935db7e329dd8a69396303`
- Branch name: `cursor/investigate-8554-gfx1201-564d` (pushed only to
  `rysuds/TheRock`)
- Commits:
  - `b4d932893875f27d9639888e0e080b4822381a4c` Add investigation and
    reproducers for issue 8554
  - `749310385d4b98696bac69850f73512cbc46844d` Finalize issue 8554
    validation report
  - `77e31c395979d570de4f5a7aa01295111c39eda2` Correct backend-specific
    root cause for issue 8554
  - `267068f0203e012538f50c45c7eba0b464fbc142` Resolve fp64 uncertainty in
    issue 8554 report
  - The branch-tip report correction incorporates the paired catalog fix. Its
    own SHA cannot be embedded in its contents; it is reported in the handoff.

## Validation performed in the cloud agent environment

Environment: Linux x86_64, no AMD GPU, Python 3.12.3, CMake 3.28.3.

| #   | Command (exact)                                                                                                               | Result                     | Notes                                                            |
| --- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------- | ---------------------------------------------------------------- |
| 1   | `python3 -m py_compile reports/8554/repro_torch_matmul.py`                                                                    | Passed                     | No output                                                        |
| 2   | `python3 reports/8554/repro_torch_matmul.py --help`                                                                           | Passed                     | Printed argparse help without importing torch                    |
| 3   | `~/.local/bin/pre-commit run --files reports/8554/repro_torch_matmul.py reports/8554/library-repro.md reports/report-8554.md` | Reformatted files, exit 1  | First run: Black changed the script and mdformat changed reports |
| 4   | `~/.local/bin/pre-commit run --files reports/8554/repro_torch_matmul.py reports/8554/library-repro.md reports/report-8554.md` | Reformatted report, exit 1 | Second run followed a validation-table edit                      |
| 5   | `~/.local/bin/pre-commit run --files reports/8554/repro_torch_matmul.py reports/8554/library-repro.md reports/report-8554.md` | Passed                     | All applicable hooks passed with pre-commit 4.6.2                |
| 6   | `git diff --check HEAD^ HEAD`                                                                                                 | Passed                     | No output; checked the pre-validation commit                     |
| 7   | `git diff --check`                                                                                                            | Passed                     | No output                                                        |

Pre-fix fail / post-fix pass is N/A in this repository: no product fix was
made, and reproducing the upstream GPU defect requires unavailable hardware.

## What could not be validated and why

- The Windows 11 + RX 9070 XT failure, selected kernel/solution, first bad
  output row, and silent launch behavior could not be measured because this
  VM is Linux-only and has no AMD GPU. A maintainer should run the torch
  reproducer with `HIPBLASLT_LOG_MASK=32` and replay the logged command with
  `hipblaslt-bench --print_kernel_info`.
- The direct fp32/fp64 `hipblaslt-bench` probes and fp32/fp64
  `rocblas-bench` commands are unexecuted because neither ROCm clients nor a
  GPU are present. Run both boundary sizes and both rocBLAS backend settings
  from `reports/8554/library-repro.md` on the affected wheel payload.
- The paired `98aa2c31`/`507afd7d` fix includes the exact fp32 shape, but its
  pass result and the reported fp64 behavior could not be validated here.
  Compare the affected ROCm 7.2.1 build with a current nightly on the same
  Windows host.
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
- Justification: fp32 has exact threshold arithmetic, selector evidence, an
  exact regression in the fix, and independent gfx1201 confirmation.
  Confidence remains low overall because no GPU work ran here and the asserted
  fp64 `2^19` transition conflicts with the default double selector's MT16
  geometry.

## Risks / follow-ups

- Capture the hipBLASLt solution index plus the rocBLAS internal backend,
  kernel name, macro-tile, launch dimensions, and first mismatching row at
  `N=524288` and `N=524289`.
- Test paired `98aa2c31`/`507afd7d` on the reporter's Windows host for both
  dtypes. Test PR #9184 only if `ROCBLAS_LAYER=10` shows fallback to classic
  Tensile.
- Confirm the backend with logs. `TORCH_BLAS_PREFER_HIPBLASLT=0` is not a
  force-off switch in PyTorch 2.9.1 on gfx1201.
- Coordinate with [rocm-libraries#5706](https://github.com/ROCm/rocm-libraries/issues/5706)
  and [#8645](https://github.com/ROCm/rocm-libraries/issues/8645) rather than
  opening a duplicate unless maintainers prefer a release-specific backport
  issue.
- Continue review of
  [rocm-libraries#9184](https://github.com/ROCm/rocm-libraries/pull/9184);
  its broad chunking change may affect performance, and its verified
  thresholds differ from issue #8554.
