# Library-level reproduction for TheRock#8554

These commands were derived from source and were **not executed** in the cloud
agent environment, which has neither Windows nor an AMD GPU.

## Argument mapping

PyTorch stores the inputs row-major. Its BLAS adapter uses
`(A @ B).T = B.T @ A.T`, so contiguous `[N, 3] @ [3, 3]` maps to a
column-major GEMM with:

```text
m=3, n=N, k=3, transA=N, transB=N, lda=3, ldb=3, ldc=3, ldd=3
```

Backend logging is the safest way to confirm the exact arguments and selected
solution for a particular wheel:

```powershell
$env:HIPBLASLT_LOG_MASK = "32"
$env:ROCBLAS_LAYER = "10"
python .\reports\8554\repro_torch_matmul.py --sizes 524288 524289
```

The fp32 log should contain replayable `hipblaslt-bench` lines. The rocBLAS
internal trace identifies whether each regular BLAS call used
`rocblas_gemm_hipblaslt_backend` or `rocblas_gemm_tensile_backend`.

## Direct hipBLASLt

Run both sides of the boundary for fp32:

```powershell
foreach ($n in 524288, 524289) {
  hipblaslt-bench --api_method c --function matmul `
    -m 3 -n $n -k 3 --transA N --transB N `
    --lda 3 --ldb 3 --ldc 3 --ldd 3 `
    --alpha 1 --beta 0 `
    --a_type f32_r --b_type f32_r --c_type f32_r --d_type f32_r `
    --scale_type f32_r --compute_type f32_r --c_equal_d `
    --algo_method heuristic --requested_solution 1 `
    --workspace 79691776 --initialization norm_dist --verify `
    --cold_iters 0 --iters 1 --print_kernel_info
}
```

PyTorch's direct Lt helper bypasses hipBLASLt for fp64, but the resulting
rocBLAS call can delegate back to hipBLASLt in this packaged build. Probe the
same TensileLite catalog directly with:

```powershell
foreach ($n in 524288, 524289) {
  hipblaslt-bench --api_method c --function matmul `
    -m 3 -n $n -k 3 --transA N --transB N `
    --lda 3 --ldb 3 --ldc 3 --ldd 3 `
    --alpha 1 --beta 0 `
    --a_type f64_r --b_type f64_r --c_type f64_r --d_type f64_r `
    --scale_type f64_r --compute_type f64_r --c_equal_d `
    --algo_method heuristic --requested_solution 1 `
    --workspace 79691776 --initialization norm_dist --verify `
    --cold_iters 0 --iters 1 --print_kernel_info
}
```

If this reports no supported solution, the rocBLAS trace determines whether
the Torch path instead fell back to classic Tensile.

ROCm/rocm-libraries
[PR #2753](https://github.com/ROCm/rocm-libraries/pull/2753) was motivated by
this closely related failing command and replaced the small gfx12 fp32/fp64
catalog tiles with MT64-class solutions:

```text
hipblaslt-bench -transA T -transB N -m 3 -n 2073600 -k 3 --a_type f32
```

Paired [PR #2681](https://github.com/ROCm/rocm-libraries/pull/2681) fixes the
gfx12 multidimensional launch itself. Use the option spellings printed by the
installed `hipblaslt-bench --help` if the ROCm 7.2.1 client rejects an option
above.

## Through rocBLAS

Run the equivalent regular BLAS calls for fp32 and fp64. Setting
`ROCBLAS_USE_HIPBLASLT=1` requests rocBLAS's internal hipBLASLt delegation;
the ROCm 7.2.1 adapter maps double to `HIP_R_64F` and does not exclude it.
Unsupported selected solutions still fall back to legacy Tensile:

```powershell
$env:ROCBLAS_USE_HIPBLASLT = "1"
foreach ($precision in "s", "d") {
  foreach ($n in 524288, 524289) {
    rocblas-bench -f gemm -r $precision `
      -m 3 -n $n -k 3 --transposeA N --transposeB N `
      --lda 3 --ldb 3 --ldc 3 --alpha 1 --beta 0 `
      --initialization rand_int -v 1 -t 1 -j 0 -i 1
  }
}
```

Then isolate legacy Tensile:

```powershell
$env:ROCBLAS_USE_HIPBLASLT = "0"
foreach ($precision in "s", "d") {
  foreach ($n in 524288, 524289) {
    rocblas-bench -f gemm -r $precision `
      -m 3 -n $n -k 3 --transposeA N --transposeB N `
      --lda 3 --ldb 3 --ldc 3 --alpha 1 --beta 0 `
      --initialization rand_int -v 1 -t 1 -j 0 -i 1
  }
}
```

`TORCH_BLAS_PREFER_HIPBLASLT=0` is not equivalent to the second experiment:
in PyTorch 2.9.1 it leaves the backend at `Default`, and `Default`
automatically selects hipBLASLt on gfx1201. For fp64, PyTorch enters rocBLAS,
which may delegate back to hipBLASLt despite PyTorch bypassing its own direct
Lt helper.

For backend confirmation, `ROCBLAS_LAYER=10` enables bench logging plus the
internal trace that identifies `rocblas_gemm_hipblaslt_backend` versus
`rocblas_gemm_tensile_backend`.

The exact fp32 boundary is diagnostic for the old TensileLite path:
`524288 = 65536 * 8`; its selected MT8 solution reaches the gfx1201 16-bit
grid-Y limit at `N=524288`, and `N=524289` requires one additional workgroup.
Classic Tensile has a separate large-grid defect tracked by
[ROCm/rocm-libraries#9184](https://github.com/ROCm/rocm-libraries/pull/9184)
with different verified boundaries. The `ROCBLAS_USE_HIPBLASLT=0` experiment
determines whether it participates in this exact shape.
