"""Implementation-scope diagnostics; OS/API fixtures are not native acceptance.

Load the standard-library-only preflight directly so these checks also run before
RPNH or its optional runtime dependencies have been installed.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


_SOURCE = Path(__file__).resolve().parents[1] / "cpn/rpnh/platform_support.py"
_SPEC = importlib.util.spec_from_file_location("platform_scope_under_test", _SOURCE)
support = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(support)


@contextmanager
def reported_platform(name, version=(3, 13, 0), *, missing_waitid=False):
    """Exercise the real report over available-but-never-called API fixtures."""
    forbidden = Mock(side_effect=AssertionError("platform reporting must not probe runtime operations"))
    os_api = SimpleNamespace(**{
        key: forbidden for key in (
            "waitid", "getpgid", "killpg", "setsid", "getuid", "fstat", "fsync", "replace",
            "open", "fork", "posix_spawn",
        )
    })
    for key in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED",
                "O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
        setattr(os_api, key, 1)
    if missing_waitid:
        os_api.waitid = None
    with patch.multiple(
        support,
        sys=SimpleNamespace(platform=name, version_info=version,
                            implementation=SimpleNamespace(name="cpython")),
        os=os_api,
        socket=SimpleNamespace(AF_UNIX=1, SOCK_STREAM=1, socket=forbidden),
        fcntl=SimpleNamespace(flock=forbidden, fcntl=forbidden, LOCK_EX=1, LOCK_SH=1,
                              LOCK_NB=1, F_DUPFD_CLOEXEC=1),
        resource=SimpleNamespace(setrlimit=forbidden, RLIMIT_AS=1, RLIMIT_NPROC=1),
        platform=SimpleNamespace(release=lambda: "fixture", machine=lambda: "fixture-cpu",
                                 libc_ver=lambda: ("fixture-libc", "1")),
    ):
        yield
    forbidden.assert_not_called()


class PlatformCapabilityScopeTests(unittest.TestCase):
    def test_linux_implementation_does_not_claim_native_validation(self):
        with reported_platform("linux"):
            report = support.platform_report()
        scope = report["implementation_scope"]
        self.assertEqual(scope["basis"], "source_code_paths")
        self.assertEqual(scope["invocation_owned_supervision"], "linux_only")
        self.assertEqual(scope["local_process_supervision"], "invocation_owned")
        self.assertEqual(scope["plugin_worker_supervision"], "invocation_owned")
        self.assertEqual(scope["controlled_script"]["profile"], "linux_isolated_python/v1")
        self.assertEqual(scope["controlled_script"]["implementation_on_current_platform"], "present")
        self.assertEqual(report["native_validation"], "not_performed")
        self.assertEqual(report["eligible"], all(report["required_checks"].values()))

    def test_darwin_missing_backend_does_not_add_a_prerequisite_gate(self):
        with reported_platform("darwin"):
            report = support.require_platform_support()
        self.assertTrue(report["eligible"])
        self.assertEqual(report["support_level"], "candidate")
        self.assertEqual(report["implementation_scope"]["local_process_supervision"], "legacy_process_group")
        self.assertEqual(report["implementation_scope"]["plugin_worker_supervision"], "legacy_process_group")
        self.assertEqual(report["implementation_scope"]["invocation_owned_supervision"], "linux_only")
        self.assertEqual(report["implementation_scope"]["controlled_script"]["platform_scope"], "linux_only")
        self.assertEqual(report["implementation_scope"]["controlled_script"]["implementation_on_current_platform"], "not_implemented")
        self.assertEqual(report["native_validation"], "not_performed")

    def test_implemented_linux_backend_does_not_override_missing_api(self):
        with reported_platform("linux", missing_waitid=True):
            report = support.platform_report()
            with self.assertRaisesRegex(RuntimeError, "waitid"):
                support.require_platform_support()
        self.assertFalse(report["eligible"])
        self.assertFalse(report["required_checks"]["waitid_api"])
        self.assertEqual(report["implementation_scope"]["local_process_supervision"], "invocation_owned")
        self.assertEqual(report["native_validation"], "not_performed")

    def test_darwin_python_minimum_is_preserved(self):
        with reported_platform("darwin", version=(3, 12, 14)):
            report = support.platform_report()
            with self.assertRaisesRegex(RuntimeError, "Python 3.13"):
                support.require_platform_support()
        self.assertFalse(report["eligible"])
        self.assertEqual(report["implementation_scope"]["plugin_worker_supervision"], "legacy_process_group")

    def test_unsupported_platform_does_not_acquire_a_backend_label(self):
        with reported_platform("win32"):
            report = support.platform_report()
        self.assertFalse(report["eligible"])
        self.assertEqual(report["support_level"], "unavailable")
        self.assertEqual(report["implementation_scope"]["local_process_supervision"], "unsupported_platform")
        self.assertEqual(report["implementation_scope"]["plugin_worker_supervision"], "unsupported_platform")
        self.assertEqual(report["implementation_scope"]["controlled_script"]["implementation_on_current_platform"], "not_implemented")

    def test_cli_formats_expose_scope_and_keep_prerequisite_exit_codes(self):
        for target, missing_api, expected_code in (("linux", False, 0), ("darwin", False, 0),
                                                   ("linux", True, 2)):
            with self.subTest(target=target, missing_api=missing_api):
                with reported_platform(target, missing_waitid=missing_api):
                    encoded = io.StringIO()
                    with redirect_stdout(encoded):
                        self.assertEqual(support.main(["--json"]), expected_code)
                    report = json.loads(encoded.getvalue())
                    plain = io.StringIO()
                    with redirect_stdout(plain):
                        self.assertEqual(support.main([]), expected_code)
                self.assertEqual(report["native_validation"], "not_performed")
                self.assertIn("linux_isolated_python/v1", plain.getvalue())
                self.assertIn("does not perform native runtime", plain.getvalue())
                if target == "darwin":
                    self.assertIn("Darwin invocation-owned supervisor is not implemented", plain.getvalue())
                else:
                    self.assertIn("Linux invocation-owned backend", plain.getvalue())


if __name__ == "__main__":
    unittest.main()
