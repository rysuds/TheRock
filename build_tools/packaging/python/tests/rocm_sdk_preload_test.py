# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for rocm_sdk.preload_libraries() load failure reporting.

The rocm_sdk template package is imported from source, so no built or installed
ROCm Python packages are needed. Windows loader failures are simulated by
making ctypes.CDLL raise the OSError that CPython produces on Windows.
"""

import errno
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BUILD_TOOLS_DIR = Path(__file__).resolve().parents[3]
REPO_ROOT = BUILD_TOOLS_DIR.parent
ROCM_SDK_SRC_DIR = (
    BUILD_TOOLS_DIR / "packaging" / "python" / "templates" / "rocm" / "src"
)
# _therock_utils (imported by the unrendered rocm_sdk._dist_info) lives in build_tools.
sys.path.insert(0, os.fspath(BUILD_TOOLS_DIR))
sys.path.insert(0, os.fspath(ROCM_SDK_SRC_DIR))

import rocm_sdk

ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION = 4551
ERROR_BAD_EXE_FORMAT = 193


def make_windows_error(winerror: int, strerror: str) -> OSError:
    """Builds the OSError that ctypes raises on Windows for a failed LoadLibrary."""
    error = OSError(errno.EINVAL, strerror, None, winerror)
    # The constructor only sets winerror on Windows.
    error.winerror = winerror
    return error


def make_app_control_error() -> OSError:
    return make_windows_error(
        ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION,
        "An Application Control policy has blocked this file",
    )


def github_heading_anchor(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


class PreloadLibrariesLoadErrorTest(unittest.TestCase):
    def setUp(self):
        rocm_sdk._ALL_CDLLS.clear()
        self.addCleanup(rocm_sdk._ALL_CDLLS.clear)
        self.dll_path = Path(
            "site-packages", "_rocm_sdk_libraries", "bin", "hiprand.dll"
        )
        find_libraries = mock.patch.object(
            rocm_sdk, "find_libraries", return_value=[self.dll_path]
        )
        find_libraries.start()
        self.addCleanup(find_libraries.stop)

    def test_app_control_block_names_library_and_links_docs(self):
        blocked = make_app_control_error()
        with mock.patch("platform.system", return_value="Windows"), mock.patch(
            "ctypes.CDLL", side_effect=blocked
        ):
            with self.assertRaises(OSError) as cm:
                rocm_sdk.initialize_process(preload_shortnames=["hiprand"])

        message = str(cm.exception)
        self.assertIn("'hiprand'", message)
        self.assertIn(str(self.dll_path), message)
        self.assertIn("or a DLL that it depends on", message)
        self.assertIn("Smart App Control", message)
        self.assertIn("App Control for Business", message)
        self.assertIn(rocm_sdk._APP_CONTROL_TROUBLESHOOTING_URL, message)
        self.assertIs(cm.exception.__cause__, blocked)
        self.assertEqual(cm.exception.errno, blocked.errno)
        self.assertNotIn("hiprand", rocm_sdk._ALL_CDLLS)

    @unittest.skipUnless(sys.platform == "win32", "OSError.winerror is Windows-only")
    def test_app_control_block_keeps_winerror(self):
        with mock.patch("ctypes.CDLL", side_effect=make_app_control_error()):
            with self.assertRaises(OSError) as cm:
                rocm_sdk.preload_libraries("hiprand")

        self.assertEqual(cm.exception.winerror, ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION)
        self.assertIn(rocm_sdk._APP_CONTROL_TROUBLESHOOTING_URL, str(cm.exception))

    def test_other_windows_load_errors_are_unchanged(self):
        bad_format = make_windows_error(
            ERROR_BAD_EXE_FORMAT, "%1 is not a valid Win32 application"
        )
        with mock.patch("platform.system", return_value="Windows"), mock.patch(
            "ctypes.CDLL", side_effect=bad_format
        ):
            with self.assertRaises(OSError) as cm:
                rocm_sdk.preload_libraries("hiprand")

        self.assertIs(cm.exception, bad_format)

    def test_app_control_handling_is_windows_only(self):
        blocked = make_app_control_error()
        with mock.patch("platform.system", return_value="Linux"), mock.patch(
            "ctypes.CDLL", side_effect=blocked
        ):
            with self.assertRaises(OSError) as cm:
                rocm_sdk.preload_libraries("hiprand")

        self.assertIs(cm.exception, blocked)

    def test_real_loader_error_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_a_library = Path(tmp) / "hiprand.dll"
            not_a_library.write_text("not a shared library")
            with mock.patch.object(
                rocm_sdk, "find_libraries", return_value=[not_a_library]
            ):
                with self.assertRaises(OSError) as cm:
                    rocm_sdk.preload_libraries("hiprand")

        self.assertIsNone(cm.exception.__cause__)
        self.assertNotIn("Application Control", str(cm.exception))

    def test_successful_load_is_cached(self):
        handle = object()
        with mock.patch("ctypes.CDLL", return_value=handle) as cdll:
            rocm_sdk.preload_libraries("hiprand")
            rocm_sdk.preload_libraries("hiprand")

        cdll.assert_called_once()
        self.assertIs(rocm_sdk._ALL_CDLLS["hiprand"], handle)


class TroubleshootingUrlTest(unittest.TestCase):
    def test_url_points_at_releases_md_heading(self):
        url, fragment = rocm_sdk._APP_CONTROL_TROUBLESHOOTING_URL.split("#", 1)
        self.assertTrue(url.endswith("/RELEASES.md"), msg=url)
        releases_md = (REPO_ROOT / "RELEASES.md").read_text(encoding="utf-8")
        headings = re.findall(r"^#+ (.+)$", releases_md, flags=re.MULTILINE)
        self.assertIn(fragment, [github_heading_anchor(h) for h in headings])


if __name__ == "__main__":
    unittest.main()
