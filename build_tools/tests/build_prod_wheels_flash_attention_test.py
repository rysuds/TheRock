# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Tests for the flash attention (aotriton) decision in build_prod_wheels.py."""

import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(
    0, os.fspath(Path(__file__).parent.parent.parent / "external-builds" / "pytorch")
)

import build_prod_wheels
from build_prod_wheels import get_pytorch_aotriton_commit, resolve_use_flash_attention

# aotriton 0.14.2b, pinned by PyTorch main since pytorch/pytorch#197747.
BROKEN_AOTRITON_COMMIT = "c11b5b886a10a00f925f74133d38b3505e67ccd9"
# aotriton 0.13b, pinned by ROCm/pytorch release/2.13 and release/2.14.
GOOD_AOTRITON_COMMIT = "6e00ef3e335b45dfb49065259533b59c68995bfe"
ISSUE_URL = "https://github.com/ROCm/TheRock/issues/8564"

# The pin section of pytorch/cmake/External/aotriton.cmake.
AOTRITON_CMAKE_TEMPLATE = """\
  set(__AOTRITON_VER "0.14.2b")
  if(DEFINED ENV{{PYTORCH_AOTRITON_COMMIT}})
    set(__AOTRITON_CI_COMMIT "$ENV{{PYTORCH_AOTRITON_COMMIT}}")
  else()
    set(__AOTRITON_CI_COMMIT "{commit}")
  endif()
"""


def write_aotriton_cmake(pytorch_dir: Path, contents: str) -> None:
    aotriton_cmake = pytorch_dir / "cmake" / "External" / "aotriton.cmake"
    aotriton_cmake.parent.mkdir(parents=True, exist_ok=True)
    aotriton_cmake.write_text(contents)


class GetPytorchAotritonCommitTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pytorch_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reads_pinned_commit(self):
        write_aotriton_cmake(
            self.pytorch_dir,
            AOTRITON_CMAKE_TEMPLATE.format(commit=BROKEN_AOTRITON_COMMIT),
        )
        self.assertEqual(
            get_pytorch_aotriton_commit(self.pytorch_dir, {}), BROKEN_AOTRITON_COMMIT
        )

    def test_reads_pin_without_env_override(self):
        write_aotriton_cmake(
            self.pytorch_dir,
            f'  set(__AOTRITON_CI_COMMIT "{GOOD_AOTRITON_COMMIT}")\n',
        )
        self.assertEqual(
            get_pytorch_aotriton_commit(self.pytorch_dir, {}), GOOD_AOTRITON_COMMIT
        )

    def test_env_override_wins_over_pin(self):
        write_aotriton_cmake(
            self.pytorch_dir,
            AOTRITON_CMAKE_TEMPLATE.format(commit=BROKEN_AOTRITON_COMMIT),
        )
        env = {"PYTORCH_AOTRITON_COMMIT": GOOD_AOTRITON_COMMIT}
        self.assertEqual(
            get_pytorch_aotriton_commit(self.pytorch_dir, env), GOOD_AOTRITON_COMMIT
        )

    def test_installed_aotriton_skips_source_build(self):
        write_aotriton_cmake(
            self.pytorch_dir,
            AOTRITON_CMAKE_TEMPLATE.format(commit=BROKEN_AOTRITON_COMMIT),
        )
        env = {"AOTRITON_INSTALLED_PREFIX": "C:/aotriton"}
        self.assertIsNone(get_pytorch_aotriton_commit(self.pytorch_dir, env))

    def test_missing_aotriton_cmake_is_unknown(self):
        self.assertIsNone(get_pytorch_aotriton_commit(self.pytorch_dir, {}))

    def test_multiple_pins_are_unknown(self):
        write_aotriton_cmake(
            self.pytorch_dir,
            f'set(__AOTRITON_CI_COMMIT "{BROKEN_AOTRITON_COMMIT}")\n'
            f'set(__AOTRITON_CI_COMMIT "{GOOD_AOTRITON_COMMIT}")\n',
        )
        self.assertIsNone(get_pytorch_aotriton_commit(self.pytorch_dir, {}))


class ResolveUseFlashAttentionTest(unittest.TestCase):
    def resolve(self, **overrides) -> tuple[bool, str]:
        kwargs = {
            "enable_pytorch_flash_attention": None,
            "is_windows": True,
            "triton_requirement": None,
            "pytorch_rocm_arch": "gfx1100;gfx1151;gfx1201;gfx1030",
            "aotriton_commit": BROKEN_AOTRITON_COMMIT,
        }
        kwargs.update(overrides)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = resolve_use_flash_attention(**kwargs)
        return result, stdout.getvalue()

    def test_windows_broken_aotriton_disables_by_default(self):
        result, output = self.resolve()
        self.assertFalse(result)
        self.assertIn("::warning::Disabling Flash Attention", output)
        self.assertIn(ISSUE_URL, output)

    def test_windows_broken_aotriton_commit_matches_case_insensitively(self):
        result, _ = self.resolve(aotriton_commit=BROKEN_AOTRITON_COMMIT.upper())
        self.assertFalse(result)

    def test_windows_explicit_enable_is_honored_with_warning(self):
        result, output = self.resolve(enable_pytorch_flash_attention=True)
        self.assertTrue(result)
        self.assertIn("expect this build to fail", output)
        self.assertIn(ISSUE_URL, output)

    def test_windows_explicit_disable(self):
        result, output = self.resolve(enable_pytorch_flash_attention=False)
        self.assertFalse(result)
        self.assertNotIn(ISSUE_URL, output)

    def test_windows_good_aotriton_keeps_default(self):
        result, output = self.resolve(aotriton_commit=GOOD_AOTRITON_COMMIT)
        self.assertTrue(result)
        self.assertNotIn(ISSUE_URL, output)

    def test_windows_unknown_aotriton_keeps_default(self):
        result, output = self.resolve(aotriton_commit=None)
        self.assertTrue(result)
        self.assertNotIn(ISSUE_URL, output)

    def test_windows_without_supported_arch_does_not_warn(self):
        result, output = self.resolve(pytorch_rocm_arch="gfx1030;gfx1031")
        self.assertFalse(result)
        self.assertNotIn(ISSUE_URL, output)

    def test_linux_ignores_windows_broken_aotriton(self):
        result, output = self.resolve(
            is_windows=False, triton_requirement="triton==3.8.0"
        )
        self.assertTrue(result)
        self.assertNotIn(ISSUE_URL, output)

    def test_linux_without_triton_disables(self):
        result, _ = self.resolve(is_windows=False, aotriton_commit=None)
        self.assertFalse(result)


class DoBuildPytorchFlashAttentionTest(unittest.TestCase):
    """Checks the resolved setting reaches the Windows torch build command."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pytorch_dir = Path(self.temp_dir.name) / "pytorch"
        (self.pytorch_dir / "torch").mkdir(parents=True)
        (self.pytorch_dir / "version.txt").write_text("2.13.0a0\n")
        (self.pytorch_dir / "pyproject.toml").write_text(
            '[build-system]\nbuild-backend = "scikit_build_core.build"\n'
        )
        self.args = argparse.Namespace(
            version_suffix="+rocm10.2.0a20260927",
            release_type="nightly",
            pytorch_build_number="1",
            enable_pytorch_flash_attention=None,
            pip_cache_dir=None,
            clean=False,
            output_dir=Path(self.temp_dir.name) / "out",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def build_windows(self, aotriton_commit: str) -> tuple[list[str], dict[str, str]]:
        write_aotriton_cmake(
            self.pytorch_dir, AOTRITON_CMAKE_TEMPLATE.format(commit=aotriton_commit)
        )
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ))
            os.environ.pop("PYTORCH_AOTRITON_COMMIT", None)
            os.environ.pop("AOTRITON_INSTALLED_PREFIX", None)
            stack.enter_context(
                mock.patch.object(build_prod_wheels, "is_windows", True)
            )
            run_command = stack.enter_context(
                mock.patch.object(build_prod_wheels, "run_command")
            )
            for name, return_value in {
                "capture": "False",
                "get_rocm_sdk_version": "10.2.0a20260927",
                "copy_msvc_libomp_to_torch_lib": None,
                "copy_libuv_to_torch_lib": None,
                "find_built_wheel": Path("torch-2.13.0a0-cp312-cp312-win_amd64.whl"),
                "copy_to_output": None,
            }.items():
                stack.enter_context(
                    mock.patch.object(
                        build_prod_wheels, name, return_value=return_value
                    )
                )
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            build_prod_wheels.do_build_pytorch(
                self.args,
                self.pytorch_dir,
                {"PYTORCH_ROCM_ARCH": "gfx1100;gfx1201"},
                triton_requirement=None,
            )
        build_calls = [c for c in run_command.call_args_list if "--wheel" in c.args[0]]
        self.assertEqual(len(build_calls), 1)
        return [str(a) for a in build_calls[0].args[0]], build_calls[0].kwargs["env"]

    def test_broken_aotriton_builds_without_flash_attention(self):
        build_command, env = self.build_windows(BROKEN_AOTRITON_COMMIT)
        self.assertEqual(env["USE_FLASH_ATTENTION"], "OFF")
        self.assertEqual(env["USE_MEM_EFF_ATTENTION"], "OFF")
        self.assertFalse(any("aotriton_v2.dll" in a for a in build_command))

    def test_good_aotriton_builds_with_flash_attention(self):
        build_command, env = self.build_windows(GOOD_AOTRITON_COMMIT)
        self.assertEqual(env["USE_FLASH_ATTENTION"], "ON")
        self.assertEqual(env["USE_MEM_EFF_ATTENTION"], "ON")
        self.assertTrue(any("aotriton_v2.dll" in a for a in build_command))


if __name__ == "__main__":
    unittest.main()
