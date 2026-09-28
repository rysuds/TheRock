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
    fixes the hipBLASLt/TensileLite side of this bug family. It is titled
    “gfx12 Fix ttmp init workaround,” changes gfx12 launches from
    multidimensional to flattened 1D, and records a closely related
    `m=3, n=2073600, k=3` reproducer. It was not backported to the ROCm 7.2
    source line used by the Windows wheel.
  - Open
    [ROCm/rocm-libraries#9184](https://github.com/ROCm/rocm-libraries/pull/9184)
    is the clear candidate for the regular rocBLAS/Tensile side. It chunks the
    large free dimension around `rocblas_call_tensile`; its newer selected
    tiles yield `2^21`/`2^22` test boundaries, while an 8-wide tile would
    yield this issue's `2^19` boundary. The PR remains unmerged, conflicting,
    and unvalidated on the exact Windows configuration.
  - No open TheRock PR matching issue #8554 was found. An unrelated PR matched
    only because it mentioned the number `524288`.

## Root cause

- The most likely cause is an upstream **16-bit grid-Y/workgroup launch limit**
  reached by the large free dimension. ROCm 7.2 has backend-specific forms of
  the same defect in hipBLASLt/TensileLite and legacy rocBLAS/Tensile; neither
  is TheRock build glue:
  - PyTorch converts row-major `[N,3] @ [3,3]` into column-major
    `m=3, n=N, k=3`. The row/column swap is documented in
    [`pytorch/pytorch@v2.9.1: aten/src/ATen/native/cuda/Blas.cpp#L108-L192`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/native/cuda/Blas.cpp#L108-L192).
  - gfx1201 grid Y/Z is 16-bit. The observed transition has the exact
    geometry `524288 = 65536 * 8`: an 8-wide free-dimension tile fits through
    `N=524288`, while `N=524289` needs one more Y workgroup. The matching
    hipBLASLt
    [`fp32` catalog](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/library/src/amd_detail/rocblaslt/src/Tensile/Logic/asm_full/gfx1201/GridBased/gfx1201_Cijk_Ailk_Bljk_SB_Bias_HAS_SAV_UserArgs.yaml#L150-L160)
    contains `MacroTile1: 8`. The exact legacy-Tensile fp64 tile could not be
    established statically; its identical transition implies an 8-wide tile
    if this grid-Y mechanism is the cause. Hardware logging must prove both
    selected solutions.
- The reported preference experiment does not force two cleanly isolated
  backends:
  - In PyTorch 2.9.1,
    [`TORCH_BLAS_PREFER_HIPBLASLT=0` leaves the backend at `Default`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/Context.h#L478-L482);
    `Default` automatically selects hipBLASLt on gfx1201 in
    [`Context.cpp#L464-L524`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/Context.cpp#L464-L524).
  - fp32 normally uses direct hipBLASLt. The matching ROCm 7.2.1 Windows wheel
    branch
    [`ROCm/TheRock@ba21f57`](https://github.com/ROCm/TheRock/commit/ba21f57e0275d8c2b64cf4b52966db996b918090)
    pins rocm-libraries `171b8698`, whose gfx12 host path leaves the launch
    multidimensional in
    [`ContractionSolution.cpp#L1271-L1346`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/tensilelite/src/ContractionSolution.cpp#L1271-L1346)
    and reconstructs workgroup IDs later in
    [`StreamK.py#L1704-L1718`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/tensilelite/Tensile/Components/StreamK.py#L1704-L1718).
    Merged
    [`507afd7d` / PR #2681](https://github.com/ROCm/rocm-libraries/commit/507afd7d815aad804e923629b28147318047407a)
    flattens the launch into grid X and restores logical IDs at kernel entry.
    The ROCm 7.2 wheel pin lacks that fix; the current TheRock pin contains it.
  - fp64 does **not** have a supported direct hipBLASLt GEMM in this release,
    as both
    [`CUDABlas.cpp#L1261-L1278`](https://github.com/pytorch/pytorch/blob/v2.9.1/aten/src/ATen/cuda/CUDABlas.cpp#L1261-L1278)
    and the
    [ROCm 7.2.1 hipBLASLt support table](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/hipblaslt/docs/reference/api-reference.rst#L79-L92)
    show. It goes through hipBLAS/rocBLAS and is expected to fall back to
    legacy Tensile. In the wheel source,
    [`gemm_templates.cpp#L76-L140`](https://github.com/ROCm/rocm-libraries/blob/171b86988decb713c842236cee4898dfce2e3bf5/projects/rocblas/library/src/blas3/Tensile/gemm_templates.cpp#L76-L140)
    passes full `N` to `rocblas_call_tensile`; only the source fallback chunks
    `N`, and that fallback is skipped when Tensile reports success. Open
    [PR #9184](https://github.com/ROCm/rocm-libraries/pull/9184) adds the
    missing chunk loop.
- Thus the common explanation for fp32 and fp64 is launch geometry, not one
  shared kernel implementation: both can cross the same 16-bit Y limit if
  their selected solutions use an 8-wide free-dimension tile. PR #2681 is the
  likely hipBLASLt-side fix; PR #9184 is the still-unmerged rocBLAS/Tensile
  fix.
- Evidence: the exact `65536 * 8` transition; the fp32 release catalog; the
  old unflattened and unchunked launch sources; issue #8645's Linux hardware
  results; #2681's near-identical `m=3, k=3` case; and #9184's
  fail-before/pass-after gfx1201 evidence. This remains source-based
  localization, not a reproduction of issue #8554.
- Ownership: **upstream**, in `ROCm/rocm-libraries`, principally
  `projects/hipblaslt/tensilelite/src/ContractionSolution.cpp` for the Lt path
  and `projects/rocblas/library/src/blas3/Tensile/gemm_templates.cpp` for the
  regular path.

## Fix

- What changed and why:

  - `reports/8554/repro_torch_matmul.py` adds a deterministic torch-level
    fp32/fp64 boundary scan, CPU comparison, mismatch location, and chunked-GPU
    control.
  - `reports/8554/library-repro.md` maps the PyTorch operation to BLAS
    arguments and provides a direct fp32 hipBLASLt command plus fp32/fp64
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

  - No TheRock product code is wrong. Its current rocm-libraries pin contains
    the hipBLASLt fix but not unmerged rocBLAS PR #9184.
  - Advancing the submodule cannot pick up an unmerged fix, while carrying
    #9184 as a patch without gfx1201 hardware would be an unvalidated
    correctness and performance risk.
  - A reproducer and version-specific backport report are the appropriate
    deliverables without matching hardware.

- Mitigation:

  - Keep each leading-dimension chunk at or below 262144 rows.
  - Test a current-nightly Windows stack. It contains hipBLASLt fix
    `507afd7d`, but the fp64/legacy-Tensile result must still be checked because
    PR #9184 remains unmerged.
  - Use `ROCBLAS_USE_HIPBLASLT=0` plus `ROCBLAS_LAYER=10` to isolate and
    confirm the legacy Tensile path.

- Drafted upstream issue text (not filed):

  ````markdown
  Title: [gfx1201][ROCm 7.2 Windows] GEMM grid-Y overflow at N=2^19; correlate hipBLASLt #2681 and rocBLAS #9184

  Repository: ROCm/rocm-libraries

  On the ROCm 7.2.1 Windows PyTorch wheels, `torch.mm` for contiguous
  `[N,3] @ [3,3]` is correct at N=524288 and silently wrong at N=524289 for
  both fp32 and fp64. Chunking to 262144 rows is correct. PyTorch maps this to
  column-major `m=3, n=N, k=3`; `524288 = 65536 * 8`, exactly the gfx1201
  grid-Y capacity for a MacroTileN=8 solution. The ROCm 7.2 gfx1201 fp32
  hipBLASLt catalog contains that tile; the fp64 Tensile tile needs to be
  confirmed from runtime logging.

  The affected wheel branch pins rocm-libraries 171b8698. Its fp32
  hipBLASLt/TensileLite path keeps a multidimensional gfx12 launch; merged
  PR #2681 / commit 507afd7d later flattened that launch. fp64 is not a
  supported direct hipBLASLt combination in this release and routes through
  rocBLAS/Tensile, where
  `projects/rocblas/library/src/blas3/Tensile/gemm_templates.cpp` passes the
  full free dimension to `rocblas_call_tensile`. Open PR #9184 adds the
  missing chunking there. Related issue #8645 reports both old-stack failures
  on Linux and says current-nightly hipBLASLt is correct.

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

  Please capture both the hipBLASLt solution and rocBLAS internal backend on
  gfx1201, validate #2681 and #9184 independently, and add an exact
  524288/524289 fp32+fp64 regression case. Backport the applicable fixes to
  the maintained ROCm 7.2 Windows line, or document the first fixed wheel.
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
  - The branch-tip report correction incorporates final source-trace results.
    Its own SHA cannot be embedded in its contents; it is reported in the
    handoff.

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
- The direct fp32 `hipblaslt-bench` and fp32/fp64 `rocblas-bench` commands are
  unexecuted because neither ROCm clients nor a GPU are present. Run both
  boundary sizes and both rocBLAS backend settings from
  `reports/8554/library-repro.md` on the affected wheel payload.
- Neither commit `507afd7d` nor PR #9184 could be proven to fix this exact
  shape. Validate the affected ROCm 7.2.1 build, a current nightly, and a
  #9184 build on the same gfx1201 Windows host with identical inputs.
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
- Justification: the exact `65536 * 8` threshold, dtype routing, fp32 release
  catalog, unchunked source, and related hardware report form a coherent
  mechanism. Confidence remains low because the selected solutions and
  before/after behavior were not observed on gfx1201, Windows, or any GPU in
  this environment, and the two dtypes likely traverse different backend
  implementations.

## Risks / follow-ups

- Capture the hipBLASLt solution index plus the rocBLAS internal backend,
  kernel name, macro-tile, launch dimensions, and first mismatching row at
  `N=524288` and `N=524289`.
- Test `507afd7d` and PR #9184 independently on the reporter's Windows host;
  do not treat source ancestry or another shape's boundary as proof.
- Confirm the backend with logs. `TORCH_BLAS_PREFER_HIPBLASLT=0` is not a
  force-off switch in PyTorch 2.9.1 on gfx1201.
- Coordinate with [rocm-libraries#8645](https://github.com/ROCm/rocm-libraries/issues/8645)
  rather than opening a duplicate unless maintainers prefer a release-specific
  backport issue.
- Continue review of
  [rocm-libraries#9184](https://github.com/ROCm/rocm-libraries/pull/9184);
  fp64 naturally reaches the regular BLAS path in PyTorch 2.9.1, and the
  PR's broad chunking change may affect performance.
