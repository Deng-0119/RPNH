"""Installer cleanup classification with mocked children and signal syscalls.

The legacy lane exercises the non-Linux branch only through mocks; it is not
macOS certification or native process supervision evidence. The Linux lane
checks shared-supervisor result classification and cleanup dispatch. Neither
lane creates an installation, runs a package manager, or signals a process.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cpn.process_supervision import ProcessCleanupError, SupervisedProcess
from cpn.rpnh import install_executor as executor
from cpn.rpnh import install_materials as materials


class FakeProcess:
    pid = 987660

    def __init__(self, events, wait_results=(0,), *, reaped=False):
        self.events = events
        self.wait_results = iter(wait_results)
        self.returncode = 0 if reaped else None

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))
        result = next(self.wait_results, 0)
        if isinstance(result, BaseException):
            raise result
        self.returncode = result
        return result

    def kill(self):
        self.events.append(("popen_kill",))


class LegacyInstallerProcessCleanupMockTests(unittest.TestCase):
    """Preserve the original legacy failure and late-signal refusal contracts."""

    def setUp(self):
        self.enterContext(patch.object(executor.sys, "platform", "legacy-posix"))
        for module in (executor, materials):
            self.enterContext(patch.object(module, "start_process",
                side_effect=AssertionError("legacy mock lane must not start a Linux supervisor")))

    def candidate(self, *, group_error=ProcessLookupError(), wait_results=(0,),
                  observe_error=None, reaped=False):
        events = []
        process = FakeProcess(events, wait_results, reaped=reaped)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / ("i-" + "1" * 32)
            directory.mkdir()
            def launch(argv, **kwargs):
                events.append(("launch", tuple(argv), kwargs["pass_fds"], kwargs["start_new_session"]))
                kwargs["stdout"].write(json.dumps({"operation": "probe", "status": "verified",
                    "installation_id": directory.name}).encode())
                kwargs["stdout"].flush()
                return process
            def group(pid, signum):
                events.append(("killpg", pid, signum))
                if group_error is not None:
                    raise group_error
            def observe(*args):
                events.append(("waitid",))
                if observe_error is not None:
                    raise observe_error
                return None
            with patch.object(executor.subprocess, "Popen", side_effect=launch), \
                    patch.object(executor, "_candidate_environment", return_value={}), \
                    patch.object(executor.os, "waitid", side_effect=observe), \
                    patch.object(executor.os, "killpg", side_effect=group), \
                    patch.object(executor.os, "kill", side_effect=lambda p,s: events.append(("kill",p,s))):
                try:
                    result = executor.run_candidate_child(Path(temporary), directory,
                        SimpleNamespace(fileno=lambda: 123), "probe", timeout=7)
                except BaseException as error:
                    result = error
        return result, events

    def material(self, *, group_error=ProcessLookupError(), wait_results=(0,),
                 observe_error=None, shared=False, reaped=False):
        events = []
        process = FakeProcess(events, wait_results, reaped=reaped)
        def launch(argv, **kwargs):
            events.append(("launch", tuple(argv), kwargs["start_new_session"]))
            kwargs["stdout"].write(b"material output")
            kwargs["stdout"].flush()
            return process
        def group(pid, signum):
            events.append(("killpg", pid, signum))
            if group_error is not None:
                raise group_error
        def observe(*args):
            events.append(("waitid",))
            if observe_error is not None:
                raise observe_error
            return None
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"RPNH_CANDIDATE_ID": "fixture"} if shared else {}, clear=True), \
                patch.object(materials.subprocess, "Popen", side_effect=launch), \
                patch.object(materials.os, "waitid", side_effect=observe), \
                patch.object(materials.os, "killpg", side_effect=group), \
                patch.object(materials.os, "kill", side_effect=lambda p,s: events.append(("kill",p,s))):
            try:
                result = materials._run(["never-executed"], directory=Path(temporary), timeout=7)
            except BaseException as error:
                result = error
        return result, events

    def signs(self, events):
        return [event[2] for event in events if event[0] == "killpg"]

    def test_candidate_normal_absent_group_only_observes_after_reap(self):
        result, events = self.candidate()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.signs(events), [0])
        self.assertLess(events.index(("wait", 7)), events.index(("killpg", FakeProcess.pid, 0)))
        self.assertEqual(events[0][2:], ((123,), True))

    def test_candidate_visible_group_rejects_without_destructive_signal(self):
        result, events = self.candidate(group_error=None)
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(self.signs(events), [0])
        self.assertEqual([event for event in events if event[0] == "wait"], [("wait", 7)])

    def test_material_visible_group_rejects_without_destructive_signal(self):
        result, events = self.material(group_error=None)
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(self.signs(events), [0])
        self.assertEqual([event for event in events if event[0] == "wait"], [("wait", 7)])

    def test_normal_group_permission_or_unknown_never_enters_signal_cleanup(self):
        for caller in (self.candidate, self.material):
            for error in (PermissionError(errno.EPERM, "fixture"), OSError(errno.EIO, "fixture")):
                with self.subTest(caller=caller.__name__, error=type(error).__name__):
                    result, events = caller(group_error=error)
                    self.assertIsInstance(result, RuntimeError)
                    self.assertIs(result.__cause__, error)
                    self.assertEqual(self.signs(events), [0])

    def test_material_normal_absent_group_returns_original_output(self):
        result, events = self.material()
        self.assertEqual(result, "material output")
        self.assertEqual(self.signs(events), [0])

    def test_timeout_observes_then_signals_then_bounded_wait(self):
        for caller in (self.candidate, self.material):
            with self.subTest(caller=caller.__name__):
                timeout = subprocess.TimeoutExpired("fixture", 7)
                result, events = caller(group_error=None, wait_results=(timeout, -9))
                self.assertIs(result, timeout)
                self.assertEqual(self.signs(events), [signal.SIGKILL])
                self.assertLess(events.index(("waitid",)), events.index(("killpg", FakeProcess.pid, signal.SIGKILL)))
                self.assertEqual(events[-1], ("wait", 1))

    def test_interrupt_uses_same_owned_bounded_cleanup_and_preserves_interrupt(self):
        interruption = KeyboardInterrupt("fixture")
        result, events = self.candidate(group_error=None, wait_results=(interruption, -9))
        self.assertIs(result, interruption)
        self.assertEqual(self.signs(events), [signal.SIGKILL])
        self.assertEqual(events[-1], ("wait", 1))

    def test_timeout_with_already_reaped_child_never_signals(self):
        for caller in (self.candidate, self.material):
            with self.subTest(caller=caller.__name__):
                result, events = caller(wait_results=(subprocess.TimeoutExpired("fixture", 7),), reaped=True)
                self.assertIsInstance(result, RuntimeError)
                self.assertEqual(self.signs(events), [])
                self.assertNotIn(("waitid",), events)
                self.assertEqual([event for event in events if event[0] == "wait"], [("wait", 7)])

    def test_timeout_with_echild_or_missing_api_never_signals(self):
        for error in (ChildProcessError("fixture"), AttributeError("waitid")):
            for caller in (self.candidate, self.material):
                with self.subTest(caller=caller.__name__, error=type(error).__name__):
                    result, events = caller(wait_results=(subprocess.TimeoutExpired("fixture", 7),), observe_error=error)
                    self.assertIsInstance(result, RuntimeError)
                    self.assertIs(result.__cause__, error)
                    self.assertEqual(self.signs(events), [])
                    self.assertEqual([event for event in events if event[0] == "wait"], [("wait", 7)])

    def test_timeout_cleanup_permission_error_is_not_hidden_by_final_wait(self):
        denial = PermissionError(errno.EPERM, "fixture")
        original = subprocess.TimeoutExpired("fixture", 7)
        result, events = self.candidate(group_error=denial, wait_results=(original, 0))
        self.assertIsInstance(result, RuntimeError)
        self.assertIs(result.__cause__, denial)
        self.assertIs(denial.__context__, original)
        self.assertEqual(self.signs(events), [signal.SIGKILL])
        self.assertEqual(events[-1], ("wait", 1))

    def test_timeout_cleanup_wait_failure_is_bounded_and_typed(self):
        cleanup_timeout = subprocess.TimeoutExpired("fixture cleanup", 1)
        result, events = self.material(group_error=None,
            wait_results=(subprocess.TimeoutExpired("fixture", 7), cleanup_timeout))
        self.assertIsInstance(result, RuntimeError)
        self.assertIs(result.__cause__, cleanup_timeout)
        self.assertEqual(events[-1], ("wait", 1))

    def test_timeout_group_absent_still_waits_owned_direct_child(self):
        original = subprocess.TimeoutExpired("fixture", 7)
        result, events = self.candidate(wait_results=(original, 0))
        self.assertIs(result, original)
        self.assertEqual(events[-1], ("wait", 1))

    def test_shared_material_normal_path_does_not_observe_or_signal_group(self):
        result, events = self.material(shared=True)
        self.assertEqual(result, "material output")
        self.assertFalse(events[0][-1])
        self.assertEqual(self.signs(events), [])
        self.assertNotIn(("waitid",), events)

    def test_shared_material_timeout_only_signals_owned_direct_child(self):
        original = subprocess.TimeoutExpired("fixture", 7)
        result, events = self.material(shared=True, wait_results=(original, -9))
        self.assertIs(result, original)
        self.assertEqual(self.signs(events), [])
        self.assertIn(("kill", FakeProcess.pid, signal.SIGKILL), events)
        self.assertEqual(events[-1], ("wait", 1))
        self.assertNotIn(("popen_kill",), events)

    def test_candidate_nonzero_exit_remains_failure_when_group_absent(self):
        result, events = self.candidate(wait_results=(3,))
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("failed (exit 3)", str(result))
        self.assertEqual(self.signs(events), [0])



class LinuxInstallerProcessCleanupMockTests(unittest.TestCase):
    """Current Linux dispatch/report checks; native lifetime checks live separately."""

    def invoke(self, caller, *, wait_result=0, cleanup_complete=True,
               had_live_descendants=False, cleanup_error=None):
        events = []
        raw = FakeProcess(events, (wait_result,))
        process = SupervisedProcess(raw, None)
        process.started = True
        process.cleanup_complete = cleanup_complete
        process.had_live_descendants = had_live_descendants
        module = executor if caller == "candidate" else materials
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / ("i-" + "1" * 32)
            directory.mkdir()
            def launch(argv, **options):
                events.append(("launch", tuple(argv), options))
                output = (json.dumps({"operation": "probe", "status": "verified",
                    "installation_id": directory.name}).encode()
                    if caller == "candidate" else b"material output")
                options["stdout"].write(output)
                options["stdout"].flush()
                return process
            def stop(target):
                self.assertIs(target, process)
                events.append(("stop",))
                if cleanup_error is not None:
                    raise cleanup_error
            with patch.object(executor.sys, "platform", "linux"), \
                    patch.object(executor.time, "monotonic", return_value=100), \
                    patch.dict(os.environ, {}, clear=True), \
                    patch.object(module, "start_process", side_effect=launch) as start, \
                    patch.object(executor, "stop_process", side_effect=stop), \
                    patch.object(executor, "_candidate_environment", return_value={}), \
                    patch.object(executor.subprocess, "Popen") as popen, \
                    patch.object(executor.os, "waitid") as waitid, \
                    patch.object(executor.os, "killpg") as killpg, \
                    patch.object(executor.os, "kill") as kill:
                try:
                    if caller == "candidate":
                        result = executor.run_candidate_child(Path(temporary), directory,
                            SimpleNamespace(fileno=lambda: 123), "probe", timeout=7)
                    else:
                        result = materials._run(["never-executed"], directory=directory,
                                                environment={}, timeout=7)
                except BaseException as error:
                    result = error
                start.assert_called_once()
                for forbidden in (popen, waitid, killpg, kill):
                    forbidden.assert_not_called()
        self.assertEqual(events[0][2]["deadline"], 107)
        self.assertNotIn("start_new_session", events[0][2])
        self.assertEqual(events[1], ("wait", 7))
        return result, events

    def test_clean_report_accepts_original_output_and_exact_candidate_launch(self):
        for caller in ("candidate", "material"):
            with self.subTest(caller=caller):
                result, events = self.invoke(caller)
                self.assertEqual(len(events), 2)
                if caller == "candidate":
                    self.assertIsInstance(result, dict)
                    self.assertEqual(result["status"], "verified")
                    argv, options = events[0][1:]
                    self.assertEqual(argv, (str(options["cwd"] / "venv/bin/python"),
                        "-I", "-m", "cpn.rpnh.install_candidate", "probe"))
                    self.assertEqual(options["pass_fds"], (123,))
                    self.assertEqual(options["stdin"], subprocess.DEVNULL)
                else:
                    self.assertEqual(result, "material output")

    def test_incomplete_or_leftover_report_refuses_without_late_cleanup(self):
        for caller in ("candidate", "material"):
            for complete, leftovers in ((False, False), (False, True), (True, True)):
                with self.subTest(caller=caller, complete=complete, leftovers=leftovers):
                    result, events = self.invoke(caller, cleanup_complete=complete,
                                                had_live_descendants=leftovers)
                    self.assertIsInstance(result, RuntimeError)
                    self.assertIn("unfinished descendants" if complete else "cleanup is unconfirmed", str(result))
                    self.assertEqual(len(events), 2)  # Reaped completion cannot authorize later cleanup.

    def test_unconfirmed_target_exit_refuses_without_late_cleanup(self):
        for caller in ("candidate", "material"):
            with self.subTest(caller=caller):
                result, events = self.invoke(caller, wait_result=None)
                self.assertIsInstance(result, RuntimeError)
                self.assertIn("direct child exit is unconfirmed", str(result))
                self.assertEqual(len(events), 2)

    def test_nonzero_exit_is_not_hidden_by_clean_report(self):
        for caller in ("candidate", "material"):
            with self.subTest(caller=caller):
                result, events = self.invoke(caller, wait_result=3)
                self.assertIsInstance(result, RuntimeError)
                self.assertIn("failed (exit 3)", str(result))
                self.assertEqual(len(events), 2)

    def test_timeout_or_interrupt_dispatches_owned_stop_and_preserves_original(self):
        for caller in ("candidate", "material"):
            for failure in (subprocess.TimeoutExpired("fixture", 7), KeyboardInterrupt("fixture")):
                with self.subTest(caller=caller, failure=type(failure).__name__):
                    result, events = self.invoke(caller, wait_result=failure)
                    self.assertIs(result, failure)
                    self.assertEqual(events[2:], [("stop",)])

    def test_cleanup_failure_remains_failure_with_original_timeout_context(self):
        for caller in ("candidate", "material"):
            with self.subTest(caller=caller):
                original = subprocess.TimeoutExpired("fixture", 7)
                failure = ProcessCleanupError("owned process cleanup was not confirmed")
                result, events = self.invoke(caller, wait_result=original, cleanup_error=failure)
                self.assertIs(result, failure)
                self.assertIs(result.__context__, original)
                self.assertEqual(events[2:], [("stop",)])


if __name__ == "__main__":
    unittest.main()
