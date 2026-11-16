"""Real business state transitions with explicitly substituted test transport."""
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from platform_business_support import (
    main_session_chain, continuation_chain, run_acceptance, transport_scope,
)


class PlatformBusinessChainTests(unittest.TestCase):
    def test_actual_main_session_pause_handoff_and_resume(self):
        with tempfile.TemporaryDirectory(prefix="rpnh-platform-main-") as directory:
            with transport_scope("session-pipe"):
                result = main_session_chain(Path(directory) / "main")
            self.assertEqual(result["status"], "PASS")

    def test_actual_parallel_checkpoint_continuation(self):
        with tempfile.TemporaryDirectory(prefix="rpnh-platform-continuation-") as directory:
            with transport_scope("session-pipe"):
                result = continuation_chain(Path(directory) / "continuation")
            self.assertEqual(result["status"], "PASS")

    def test_native_preflight_failure_never_uses_pipe_fallback(self):
        with tempfile.TemporaryDirectory(prefix="rpnh-platform-blocked-") as directory:
            with patch.object(socket, "socket", side_effect=PermissionError(1, "fixture EPERM")):
                with patch("platform_business_support.main_session_chain") as business:
                    result = run_acceptance(Path(directory) / "blocked", transport="native",
                                            source_reference="test-fixture")
            business.assert_not_called()
            self.assertEqual(result["status"], "BLOCKED_NATIVE_IPC")
            self.assertEqual(result["scenarios"], {})


if __name__ == "__main__":
    unittest.main()
