#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Reproduce ROCm/TheRock#8554 and compare every GPU result with CPU."""

import argparse
import os
import platform
import sys
from dataclasses import dataclass
from types import ModuleType


DEFAULT_SIZES = [262144, 524288, 524289, 600000, 1048576, 2097152]
ENV_OVERRIDE_CHOICES = ["inherit", "unset", "0", "1"]


@dataclass(frozen=True)
class Comparison:
    max_abs_error: float
    max_rel_error: float
    mismatch_count: int
    first_mismatch: tuple[int, int, float, float] | None
    passed: bool


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare torch matmul [N,3]@[3,3] on a ROCm GPU with a CPU "
            "reference around the N=2^19 boundary."
        )
    )
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=DEFAULT_SIZES,
        help="Leading dimensions to test (default: %(default)s)",
    )
    parser.add_argument(
        "--dtypes",
        choices=["float32", "float64"],
        nargs="+",
        default=["float32", "float64"],
        help="Data types to test (default: %(default)s)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=262144,
        help="Rows per GPU chunk for the workaround comparison (default: %(default)s)",
    )
    parser.add_argument(
        "--skip-chunked",
        action="store_true",
        help="Skip the chunked-GPU workaround comparison",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Base seed for deterministic CPU inputs (default: %(default)s)",
    )
    parser.add_argument(
        "--torch-blas-prefer-hipblaslt",
        choices=ENV_OVERRIDE_CHOICES,
        default="inherit",
        help=(
            "Set TORCH_BLAS_PREFER_HIPBLASLT before importing torch. "
            "'0' does not force rocBLAS on gfx1201 in torch 2.9.1; it leaves "
            "automatic backend selection in effect (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--rocblas-use-hipblaslt",
        choices=ENV_OVERRIDE_CHOICES,
        default="inherit",
        help=(
            "Set ROCBLAS_USE_HIPBLASLT before importing torch. This only "
            "affects calls that reach rocBLAS (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--disable-addmm-lt",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Set DISABLE_ADDMM_CUDA_LT=1 before importing torch. This is "
            "diagnostic and does not by itself force rocBLAS on Windows."
        ),
    )
    return parser


def apply_environment_override(name: str, value: str) -> None:
    if value == "inherit":
        return
    if value == "unset":
        os.environ.pop(name, None)
        return
    os.environ[name] = value


def compare_tensors(
    torch_module: ModuleType,
    reference: object,
    actual: object,
    *,
    rtol: float,
    atol: float,
) -> Comparison:
    mismatch_mask = ~torch_module.isclose(actual, reference, rtol=rtol, atol=atol)
    mismatch_count = int(mismatch_mask.sum().item())

    difference = (actual - reference).abs()
    finite_difference = torch_module.nan_to_num(
        difference, nan=float("inf"), posinf=float("inf"), neginf=float("inf")
    )
    max_abs_error = float(finite_difference.max().item())
    denominator = reference.abs().clamp_min(torch_module.finfo(reference.dtype).tiny)
    relative_error = torch_module.nan_to_num(
        difference / denominator,
        nan=float("inf"),
        posinf=float("inf"),
        neginf=float("inf"),
    )
    max_rel_error = float(relative_error.max().item())

    first_mismatch = None
    if mismatch_count:
        flat_index = int(
            mismatch_mask.reshape(-1).nonzero(as_tuple=False)[0].item()
        )
        row, column = divmod(flat_index, int(reference.shape[1]))
        first_mismatch = (
            row,
            column,
            float(reference[row, column].item()),
            float(actual[row, column].item()),
        )

    return Comparison(
        max_abs_error=max_abs_error,
        max_rel_error=max_rel_error,
        mismatch_count=mismatch_count,
        first_mismatch=first_mismatch,
        passed=mismatch_count == 0,
    )


def print_comparison(label: str, comparison: Comparison) -> None:
    status = "PASS" if comparison.passed else "FAIL"
    print(
        f"  {label:<8} {status}: mismatches={comparison.mismatch_count}, "
        f"max_abs={comparison.max_abs_error:.8g}, "
        f"max_rel={comparison.max_rel_error:.8g}"
    )
    if comparison.first_mismatch is not None:
        row, column, reference, actual = comparison.first_mismatch
        print(
            f"           first mismatch at [{row}, {column}]: "
            f"cpu={reference:.17g}, gpu={actual:.17g}"
        )


def run_case(
    torch_module: ModuleType,
    *,
    dtype_name: str,
    n: int,
    seed: int,
    chunk_size: int,
    run_chunked: bool,
) -> bool:
    dtype = getattr(torch_module, dtype_name)
    rtol, atol = (1.0e-5, 1.0e-6) if dtype_name == "float32" else (1.0e-12, 1.0e-12)

    generator = torch_module.Generator(device="cpu")
    generator.manual_seed(seed)
    cpu_b = torch_module.randn((3, 3), dtype=dtype, generator=generator)
    cpu_a = torch_module.randn((n, 3), dtype=dtype, generator=generator)
    cpu_reference = cpu_a @ cpu_b

    gpu_a = cpu_a.cuda()
    gpu_b = cpu_b.cuda()
    direct = (gpu_a @ gpu_b).cpu()
    direct_comparison = compare_tensors(
        torch_module, cpu_reference, direct, rtol=rtol, atol=atol
    )

    print(f"dtype={dtype_name}, N={n}, rtol={rtol:g}, atol={atol:g}")
    print_comparison("direct", direct_comparison)

    chunked_passed = True
    if run_chunked and n > chunk_size:
        chunks = [
            (gpu_a[start : start + chunk_size] @ gpu_b).cpu()
            for start in range(0, n, chunk_size)
        ]
        chunked = torch_module.cat(chunks)
        chunked_comparison = compare_tensors(
            torch_module, cpu_reference, chunked, rtol=rtol, atol=atol
        )
        print_comparison("chunked", chunked_comparison)
        chunked_passed = chunked_comparison.passed

    del gpu_a, gpu_b, direct
    torch_module.cuda.empty_cache()
    return direct_comparison.passed and chunked_passed


def main(argv: list[str]) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if any(size <= 0 for size in args.sizes):
        parser.error("--sizes values must all be positive")
    if args.chunk_size <= 0:
        parser.error("--chunk-size must be positive")

    apply_environment_override(
        "TORCH_BLAS_PREFER_HIPBLASLT", args.torch_blas_prefer_hipblaslt
    )
    apply_environment_override(
        "ROCBLAS_USE_HIPBLASLT", args.rocblas_use_hipblaslt
    )
    if args.disable_addmm_lt:
        os.environ["DISABLE_ADDMM_CUDA_LT"] = "1"

    # torch is intentionally imported after backend environment variables are set.
    try:
        import torch
    except ModuleNotFoundError:
        print("ERROR: this reproducer requires PyTorch", file=sys.stderr)
        return 2

    print(f"python={platform.python_version()} platform={platform.platform()}")
    print(f"torch={torch.__version__} hip={torch.version.hip}")
    for name in [
        "TORCH_BLAS_PREFER_HIPBLASLT",
        "ROCBLAS_USE_HIPBLASLT",
        "DISABLE_ADDMM_CUDA_LT",
    ]:
        print(f"{name}={os.environ.get(name, '<unset>')}")

    if torch.version.hip is None:
        print("ERROR: this is not a ROCm-enabled PyTorch build", file=sys.stderr)
        return 2
    if not torch.cuda.is_available():
        print("ERROR: no ROCm GPU is available", file=sys.stderr)
        return 2

    device_index = torch.cuda.current_device()
    properties = torch.cuda.get_device_properties(device_index)
    print(
        f"device={device_index} name={properties.name} "
        f"arch={getattr(properties, 'gcnArchName', '<unknown>')}"
    )
    print(f"compiled_arches={torch.cuda.get_arch_list()}")
    print(f"reported_blas_preference={torch.backends.cuda.preferred_blas_library()}")

    all_passed = True
    for dtype_offset, dtype_name in enumerate(args.dtypes):
        for n in args.sizes:
            case_seed = args.seed + dtype_offset * 10_000_000
            all_passed = (
                run_case(
                    torch,
                    dtype_name=dtype_name,
                    n=n,
                    seed=case_seed,
                    chunk_size=args.chunk_size,
                    run_chunked=not args.skip_chunked,
                )
                and all_passed
            )

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
