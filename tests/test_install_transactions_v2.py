"""Real filesystem/flock activation tests, with explicit software metadata fixtures."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from cpn.rpnh import install_v2 as manager
from cpn.rpnh.install_contract import build_install_plan, installation_target
from cpn.rpnh.update_install import _open_lease, lifecycle_lease
from tests.test_install_contract_v2 import (
    installed_document, platform_document, runtime_bindings, source_document,
)


class InstallTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rpnh-transaction-v2-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.environment = mock.patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root = manager.create_root(self.base / "a-root")
        self.ids = ["i-" + character * 32 for character in "1234"]
        self.targets = [installation_target(identity) for identity in self.ids]

    def complete(self, ordinal):
        record = installed_document(self.ids[ordinal])
        directory = self.root / self.targets[ordinal]
        directory.mkdir(mode=0o700)
        manager._write_json(directory / "installed.json", record)
        return directory, record

    def activate(self, ordinal, *, operation="bootstrap", expected=None, event=None, probe=None):
        with manager.management_lease(self.root):
            return manager.activate_locked(self.root, self.targets[ordinal], operation=operation,
                expected_from=expected, probe=probe or (lambda _directory, _record: None), event=event)

    def initial(self):
        self.complete(0)
        self.activate(0)

    def test_bootstrap_commits_one_pointer_and_refuses_root_adoption(self):
        self.complete(0)
        result = self.activate(0)
        self.assertEqual((result.current, result.previous, result.committed), (self.targets[0], None, True))
        self.assertIsNone(manager.marker(self.root)["pending_activation"])
        self.assertFalse((self.root / self.targets[0] / "FAILED").exists())
        with self.assertRaises(ValueError):
            manager.create_root(self.root)
        self.assertEqual((self.root / "bin/rpnh").stat().st_mode & 0o777, 0o700)

    def test_failure_at_each_commit_boundary_has_correct_recovery(self):
        self.initial()
        for ordinal, stage in enumerate(("intent", "current", "previous"), 1):
            with self.subTest(stage=stage):
                old = manager.read_target(self.root)[0]
                old_previous = manager._pointer(self.root, "previous", allow_missing=True)
                self.complete(ordinal)
                def stop(observed):
                    if observed == stage:
                        raise OSError("injected interruption after " + stage)
                with self.assertRaises(manager.ActivationError) as caught:
                    self.activate(ordinal, operation="reconfigure", expected=old, event=stop)
                committed = stage != "intent"
                self.assertIs(caught.exception.committed, committed)
                self.assertEqual(caught.exception.current, self.targets[ordinal] if committed else old)
                result = manager.recover(self.root)
                self.assertIs(result.committed, committed)
                self.assertTrue(result.recovered)
                self.assertEqual(result.current, self.targets[ordinal] if committed else old)
                self.assertEqual(result.previous, old if committed else old_previous)
                self.assertIsNone(manager.marker(self.root)["pending_activation"])
                self.assertFalse((self.root / self.targets[ordinal] / "FAILED").exists())

    def test_postcommit_fsync_failure_reports_commit_and_keeps_intent(self):
        self.initial()
        self.complete(1)
        original = manager._sync_directory
        calls = 0
        def fail_after_replace(path):
            nonlocal calls
            if path == self.root:
                calls += 1
                if calls == 2:
                    raise OSError("directory fsync unavailable after current replace")
            return original(path)
        with mock.patch.object(manager, "_sync_directory", side_effect=fail_after_replace):
            with self.assertRaises(manager.ActivationError) as caught:
                self.activate(1, operation="upgrade", expected=self.targets[0])
        self.assertTrue(caught.exception.committed)
        self.assertEqual(os.readlink(self.root / "current"), self.targets[1])
        self.assertIsNotNone(manager.marker(self.root)["pending_activation"])
        result = manager.recover(self.root)
        self.assertEqual((result.current, result.previous), (self.targets[1], self.targets[0]))

    def test_recovery_uses_metadata_even_when_external_python_is_absent(self):
        self.initial()
        _, record = self.complete(1)
        self.assertFalse(Path(record["runtime_bindings"]["python"]["path"]).exists())
        def stop(stage):
            if stage == "current":
                raise KeyboardInterrupt()
        with self.assertRaises(manager.ActivationError):
            self.activate(1, operation="upgrade", expected=self.targets[0], event=stop)
        with mock.patch.object(manager, "probe_candidate", side_effect=AssertionError("must not probe")):
            result = manager.recover(self.root)
        self.assertTrue(result.committed)
        self.assertEqual(result.previous, self.targets[0])

    def test_conflicting_current_is_retained_without_guessing_latest(self):
        self.initial()
        self.complete(1)
        self.complete(2)
        def stop(stage):
            if stage == "intent":
                raise RuntimeError("stop")
        with self.assertRaises(manager.ActivationError):
            self.activate(1, operation="reconfigure", expected=self.targets[0], event=stop)
        manager._set_pointer(self.root, "current", self.targets[2])
        before = (self.root / "managed.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "conflicts"):
            manager.recover(self.root)
        self.assertEqual((self.root / "managed.json").read_bytes(), before)
        self.assertEqual(os.readlink(self.root / "current"), self.targets[2])

    def test_rollback_probes_only_destination_and_is_idempotent(self):
        self.initial()
        self.complete(1)
        self.activate(1, operation="reconfigure", expected=self.targets[0])
        observed = []
        probe = lambda directory, record: observed.append(record["installation_id"])
        result = manager.rollback(self.root, probe=probe, compatible=lambda _from, _to: True)
        self.assertEqual((result.current, result.previous), (self.targets[0], self.targets[0]))
        again = manager.rollback(self.root, probe=probe, compatible=lambda _from, _to: False)
        self.assertEqual(again.current, self.targets[0])
        self.assertEqual(observed, [self.ids[0]])

    def test_unqualified_or_unhealthy_rollback_preserves_both_pointers(self):
        self.initial()
        self.complete(1)
        self.activate(1, operation="reconfigure", expected=self.targets[0])
        before = [os.readlink(self.root / name) for name in ("current", "previous")]
        probe = mock.Mock()
        with self.assertRaises(ValueError):
            manager.rollback(self.root, probe=probe, compatible=lambda _from, _to: False)
        probe.assert_not_called()
        with self.assertRaises(RuntimeError):
            manager.rollback(self.root, probe=mock.Mock(side_effect=RuntimeError("target Node is missing")),
                             compatible=lambda _from, _to: True)
        self.assertEqual([os.readlink(self.root / name) for name in ("current", "previous")], before)
        self.assertIsNone(manager.marker(self.root)["pending_activation"])

    def test_dangling_or_invalid_current_is_not_treated_as_empty_bootstrap(self):
        self.complete(0)
        for target in (self.targets[1], "../outside", "versions/not-an-installation"):
            with self.subTest(target=target):
                (self.root / "current").symlink_to(target)
                with self.assertRaises((OSError, ValueError)):
                    self.activate(0)
                self.assertEqual(os.readlink(self.root / "current"), target)
                (self.root / "current").unlink()
        (self.root / "current").write_text("not a pointer")
        with self.assertRaises(ValueError):
            self.activate(0)
        self.assertEqual((self.root / "current").read_text(), "not a pointer")

    def test_expected_from_change_rejects_before_probe_and_intent(self):
        self.initial()
        self.complete(1)
        probe = mock.Mock()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.activate(1, operation="reconfigure", expected=None, probe=probe)
        probe.assert_not_called()
        self.assertIsNone(manager.marker(self.root)["pending_activation"])

    def test_new_stage_identity_is_never_reused_after_aborted_bootstrap(self):
        plan = build_install_plan(source_document(), platform_document(), runtime_bindings(),
                                  installation_id=self.ids[0])
        with manager.management_lease(self.root):
            directory = manager.create_stage_locked(self.root, plan)
            (directory / "FAILED").write_text("retained failed stage")
            with self.assertRaises(FileExistsError):
                manager.create_stage_locked(self.root, plan)
        next_plan = build_install_plan(source_document(), platform_document(), runtime_bindings(),
                                       installation_id=self.ids[1])
        with manager.management_lease(self.root):
            next_directory = manager.create_stage_locked(self.root, next_plan)
        self.assertNotEqual(directory, next_directory)
        self.assertTrue((directory / "FAILED").exists())
        self.assertFalse((self.root / "current").exists())

    def test_real_shared_runtime_lease_blocks_management_without_unlocking_it(self):
        with lifecycle_lease(self.root):
            with self.assertRaises(RuntimeError):
                with manager.management_lease(self.root):
                    self.fail("EX must not pass an existing SH")
        with manager.management_lease(self.root):
            pass

    def test_cross_root_order_and_busy_source_release_partial_target_lock(self):
        import cpn
        from cpn import update_context
        from cpn.rpnh import install_contract
        source = manager.create_root(self.base / "z-source")
        directory = source / self.targets[0]
        directory.mkdir(mode=0o700)
        prefix = directory / "venv"
        prefix.mkdir(mode=0o700)
        manager._write_json(directory / "installed.json",
                            installed_document(self.ids[0], version=cpn.__version__))
        held = _open_lease(source, True)
        self.addCleanup(held.close)
        order = []
        from cpn.rpnh import update_install
        original = update_install._open_lease
        def traced(root, exclusive):
            order.append((root, exclusive))
            return original(root, exclusive)
        with mock.patch.dict(os.environ, {"RPNH_MANAGED_ROOT": str(source)}), \
                mock.patch.object(sys, "prefix", str(prefix)), \
                mock.patch.object(cpn, "__file__", str(prefix / "lib/cpn/__init__.py")), \
                mock.patch.object(update_context, "__file__", str(prefix / "lib/cpn/update_context.py")), \
                mock.patch.object(install_contract, "__file__", str(prefix / "lib/cpn/rpnh/install_contract.py")), \
                mock.patch.object(update_install, "__file__", str(prefix / "lib/cpn/rpnh/update_install.py")), \
                mock.patch.object(manager, "__file__", str(prefix / "lib/cpn/rpnh/install_v2.py")), \
                mock.patch.object(update_install, "_open_lease", side_effect=traced):
            with self.assertRaises(RuntimeError):
                with manager.management_lease(self.root):
                    self.fail("busy source must stop a cross-root operation")
        self.assertEqual(order, [(self.root, True), (source, False)])
        with _open_lease(self.root, True):
            pass
        with self.assertRaises(RuntimeError):
            _open_lease(source, False)

    def test_successful_commit_uses_known_result_without_a_fallible_final_read(self):
        self.complete(0)
        original = manager._state
        calls = []
        def only_initial_state(*args, **kwargs):
            calls.append(None)
            if len(calls) > 1:
                raise OSError("postcommit state reread failed")
            return original(*args, **kwargs)
        with mock.patch.object(manager, "_state", side_effect=only_initial_state):
            result = self.activate(0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result.committed)
        self.assertEqual(result.current, self.targets[0])

    def test_mutated_plan_is_rejected_before_creating_any_stage(self):
        plan = build_install_plan(source_document(), platform_document(), runtime_bindings(),
                                  installation_id=self.ids[0])
        document = plan.to_document()
        document["components"].pop("basic")
        arbitrary = mock.Mock(to_document=lambda: document)
        with manager.management_lease(self.root):
            with self.assertRaises(ValueError):
                manager.create_stage_locked(self.root, arbitrary)
        self.assertEqual(list((self.root / "versions").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
