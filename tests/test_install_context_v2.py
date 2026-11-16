"""Real POSIX lease checks and explicit fixtures for managed-v2 contexts.

No frontend is installed and no Registry, socket, cloud job or model API is
used. Interpreter paths in context tests are stated fixtures; descriptor
identity, subprocess inheritance and nonblocking flock are real OS behavior.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import cpn
from cpn import update_context
from cpn.rpnh import install_candidate as candidate
from cpn.rpnh import update_install as manager
from cpn.rpnh import install_contract as contract
from cpn.rpnh import install_v2


def installed_fixture(identity):
    """Structurally complete metadata only; no installation health is claimed."""
    version = cpn.__version__
    return {
        "schema_version": contract.INSTALLED_SCHEMA, "protocol": contract.V2_PROTOCOL,
        "installation_id": identity, "rpnh_version": version, "complete": True,
        "source": {"kind": "local_candidate", "rpnh_version": version,
                   "release": None, "channel": None,
                   "artifact": {"filename": f"rpnh_harness-{version}-py3-none-any.whl",
                                "location": f"/fixtures/rpnh_harness-{version}-py3-none-any.whl"}},
        "platform": {"os": "linux", "architecture": "x86_64", "os_release": "fixture",
                     "libc": None, "emulated": False},
        "runtime_bindings": {"python": {"path": "/missing/python", "version": "3.12.14",
                                        "implementation": "cpython", "architecture": "x86_64"}},
        "components": {"basic": {"profile": "builtin", "binding": {"kind": "managed"},
                                 "launch": {"kind": "builtin", "scope": "installation",
                                            "path": "venv/bin/rpnh"},
                                 "artifact": {"kind": "wheel", "name": "rpnh-harness", "version": version,
                                              "variant": "py3-none-any", "architecture": "any"}}},
    }


class DeliveredLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-candidate-fd-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.descriptors = []
        self.fd = self.open_lock()
        self.addCleanup(self.close_descriptors)

    def close_descriptors(self):
        for descriptor in self.descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def open_lock(self, name="lifecycle.lock"):
        descriptor = os.open(self.root / name, os.O_RDWR | os.O_CREAT, 0o600)
        self.descriptors.append(descriptor)
        return descriptor

    def test_unlocked_descriptor_is_rejected_without_acquiring_exclusive(self):
        with self.assertRaisesRegex(candidate.CandidateContextError, "already held exclusive"):
            candidate.inherited_exclusive_lease(self.root, self.fd)
        other = self.open_lock()
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_shared_descriptor_is_rejected_without_upgrading_or_releasing_it(self):
        fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with self.assertRaisesRegex(candidate.CandidateContextError, "already held exclusive"):
            candidate.inherited_exclusive_lease(self.root, self.fd)
        other = self.open_lock()
        fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with self.assertRaises(BlockingIOError):
            fcntl.flock(self.open_lock(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_somebody_elses_exclusive_lock_does_not_validate_an_unlocked_fd(self):
        owner = self.open_lock()
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(candidate.CandidateContextError, "does not own"):
            candidate.inherited_exclusive_lease(self.root, self.fd)
        with self.assertRaises(BlockingIOError):
            fcntl.flock(self.open_lock(), fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_wrong_file_and_symlink_lock_are_rejected(self):
        wrong = self.open_lock("wrong.lock")
        fcntl.flock(wrong, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(candidate.CandidateContextError, "does not identify"):
            candidate.inherited_exclusive_lease(self.root, wrong)
        (self.root / "lifecycle.lock").unlink()
        (self.root / "lifecycle.lock").symlink_to(self.root / "wrong.lock")
        with self.assertRaisesRegex(candidate.CandidateContextError, "does not identify"):
            candidate.inherited_exclusive_lease(self.root, wrong)

    def test_validation_duplicate_closes_without_unlocking_parent(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        duplicate = candidate.inherited_exclusive_lease(self.root, self.fd)
        self.assertGreaterEqual(duplicate, 3)
        self.assertFalse(os.get_inheritable(duplicate))
        os.close(duplicate)
        with self.assertRaises(BlockingIOError):
            fcntl.flock(self.open_lock(), fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_real_child_inherits_and_preserves_parent_exclusive_description(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code = """
import os, pathlib, sys
from cpn.rpnh.install_candidate import inherited_exclusive_lease
duplicate = inherited_exclusive_lease(pathlib.Path(sys.argv[1]), int(sys.argv[2]))
print('validated', flush=True)
sys.stdin.read(1)
os.close(duplicate)
"""
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(self.root), str(self.fd)],
            pass_fds=(self.fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        try:
            ready, _, _ = select.select([child.stdout], [], [], 5)
            self.assertTrue(ready, "candidate helper did not report validation")
            self.assertEqual(child.stdout.readline(), b"validated\n")
            os.close(self.fd)
            self.descriptors.remove(self.fd)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(self.open_lock(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            _, error = child.communicate(b"q", timeout=10)
            self.assertEqual(child.returncode, 0, error.decode())
            fcntl.flock(self.open_lock(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()


class ContextHintTests(unittest.TestCase):
    def test_v1_and_v2_prefixes_request_validation_without_environment(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "managed.json").write_text("{}", encoding="utf-8")
            for target in ("0.1.0rc2-aaaaaaaaaaaa", "i-" + "a" * 32):
                with self.subTest(target=target), patch.dict(os.environ, {}, clear=True), \
                        patch.object(sys, "prefix", str(root / "versions" / target / "venv")):
                    self.assertEqual(update_context.managed_context_hint(), root)

    def test_v2_reserved_prefix_cannot_become_manual_by_losing_its_marker(self):
        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {}, clear=True):
            root = Path(raw).resolve()
            with patch.object(sys, "prefix", str(root / "versions" / ("i-" + "a" * 32) / "venv")):
                self.assertEqual(update_context.managed_context_hint(), root)
                with self.assertRaisesRegex(ValueError, "installation marker"):
                    manager.managed_root()
            with patch.object(sys, "prefix", str(root / "versions/0.1.0rc2-aaaaaaaaaaaa/venv")):
                self.assertIsNone(update_context.managed_context_hint())

    def test_any_candidate_hint_refuses_normal_runtime_including_empty(self):
        for name in update_context.CANDIDATE_CONTEXT_KEYS:
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}):
                with self.assertRaisesRegex(ValueError, "fixed prepare/probe"):
                    manager.managed_root()
                with self.assertRaisesRegex(ValueError, "fixed prepare/probe"):
                    with manager.lifecycle_lease(Path("/unrelated"), exclusive=True):
                        self.fail("candidate entered ordinary management lease")
                with self.assertRaisesRegex(ValueError, "fixed prepare/probe"):
                    manager.worker_lease()


class ManagedV2ContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-v2-context-")
        self.addCleanup(self.temporary.cleanup)
        self.root = install_v2.create_root(Path(self.temporary.name).resolve() / "managed")
        self.a = "i-" + "a" * 32
        self.b = "i-" + "b" * 32
        self.adir = self.make_installation(self.a)
        self.bdir = self.make_installation(self.b)
        (self.root / "current").symlink_to(contract.installation_target(self.a))

    @staticmethod
    def write(path, document):
        path.write_text(json.dumps(document), encoding="utf-8")
        path.chmod(0o600)

    def make_installation(self, identity):
        directory = self.root / contract.installation_target(identity)
        directory.mkdir(mode=0o700)
        (directory / "venv").mkdir(mode=0o700)
        self.write(directory / "installed.json", installed_fixture(identity))
        self.write(directory / "frontend.json", {
            "schema_version": contract.FRONTEND_SCHEMA, "installation_id": identity, "frontend": "basic"})
        return directory

    @contextmanager
    def interpreter(self, directory, *, extra=None):
        environment = {
            "RPNH_MANAGED_ROOT": str(self.root),
            "RPNH_FRONTEND_CONFIG": str(directory / "frontend.json"),
        }
        environment.update(extra or {})
        with patch.dict(os.environ, environment), \
                patch.object(sys, "prefix", str(directory / "venv")), \
                patch.object(update_context, "__file__", str(directory / "venv/lib/cpn/update_context.py")), \
                patch.object(contract, "__file__", str(directory / "venv/lib/cpn/rpnh/install_contract.py")), \
                patch.object(install_v2, "__file__", str(directory / "venv/lib/cpn/rpnh/install_v2.py")), \
                patch.object(manager, "__file__", str(directory / "venv/lib/cpn/rpnh/update_install.py")), \
                patch.object(candidate, "__file__", str(directory / "venv/lib/cpn/rpnh/install_candidate.py")), \
                patch.object(cpn, "__file__", str(directory / "venv/lib/cpn/__init__.py")):
            yield

    def assert_lock_free(self):
        with manager._open_lease(self.root, True):
            pass

    def make_plan(self):
        (self.bdir / "installed.json").unlink()
        record = installed_fixture(self.b)
        document = {
            "schema_version": contract.PLAN_SCHEMA, "protocol": contract.V2_PROTOCOL,
            "installation_id": self.b, "rpnh_version": cpn.__version__,
            "operation": "reconfigure", "expected_from": contract.installation_target(self.a),
            "source": record["source"], "platform": record["platform"],
            "runtime_bindings": record["runtime_bindings"],
            "components": {"basic": {"profile": "builtin", "binding": {"kind": "managed"}}},
            "conflicts": [],
        }
        self.write(self.bdir / "plan.json", contract.validate_plan(document))
        return document

    def pending(self):
        document = install_v2.marker(self.root)
        document["pending_activation"] = {
            "operation": "reconfigure", "from": contract.installation_target(self.a),
            "to": contract.installation_target(self.b), "old_previous": None,
            "previous_after": contract.installation_target(self.a),
        }
        self.write(self.root / "managed.json", document)

    def test_current_binding_version_and_frontend_view_are_checked(self):
        with self.interpreter(self.adir):
            self.assertEqual(manager.managed_root(), self.root)
            with manager.lifecycle_lease():
                with self.assertRaises(RuntimeError):
                    with manager._open_lease(self.root, True):
                        self.fail("runtime SH did not protect its installation")
            with patch.object(cpn, "__version__", "99.0.0"):
                with self.assertRaisesRegex(ValueError, "version"):
                    manager.managed_root()
            self.write(self.adir / "frontend.json", {
                "schema_version": contract.FRONTEND_SCHEMA, "installation_id": self.b, "frontend": "basic"})
            with self.assertRaisesRegex(ValueError, "another installation"):
                manager.managed_root()
        self.assert_lock_free()

    def test_current_switch_between_precheck_and_sh_rejects_stale_v2_runtime_and_worker(self):
        for kind in ("runtime", "worker"):
            with self.subTest(kind=kind):
                (self.root / "current").unlink()
                (self.root / "current").symlink_to(contract.installation_target(self.a))
                original = manager._open_lease
                def change_then_lock(root, exclusive):
                    with original(root, True):
                        replacement = root / "replacement-current"
                        replacement.symlink_to(contract.installation_target(self.b))
                        os.replace(replacement, root / "current")
                    return original(root, exclusive)
                with self.interpreter(self.adir), patch.object(manager, "_open_lease", side_effect=change_then_lock):
                    with self.assertRaisesRegex(ValueError, "managed environment"):
                        if kind == "runtime":
                            with manager.lifecycle_lease():
                                self.fail("stale runtime entered")
                        else:
                            manager.worker_lease()
                self.assert_lock_free()

    def test_pending_recovery_happens_before_sh_and_managed_root_stays_readonly(self):
        self.pending()
        with self.interpreter(self.adir):
            with self.assertRaisesRegex(ValueError, "pending activation"):
                manager.managed_root()
            self.assertIsNotNone(install_v2.marker(self.root)["pending_activation"])
            with patch.object(install_v2, "recover", wraps=install_v2.recover) as recovery:
                with manager.lifecycle_lease():
                    self.assertIsNone(install_v2.marker(self.root)["pending_activation"])
            recovery.assert_called_once_with(self.root)
        self.assert_lock_free()

    def test_uncommitted_candidate_uses_fixed_handler_and_preserves_parent_lease(self):
        self.make_plan()
        with manager._open_lease(self.root, True) as lease, self.interpreter(self.bdir, extra={
                "RPNH_CANDIDATE_ID": self.b, "RPNH_CANDIDATE_FD": str(lease.fileno())}):
            prepare, probe = Mock(return_value={"fixture": "prepared"}), Mock()
            self.assertEqual(candidate.run_candidate("prepare", prepare=prepare, probe=probe), {"fixture": "prepared"})
            context = prepare.call_args.args[0]
            self.assertEqual(context.directory, self.bdir)
            self.assertEqual(context.rpnh_version, cpn.__version__)
            self.assertEqual(context.record["components"], {"basic": {"profile": "builtin", "binding": {"kind": "managed"}}})
            probe.assert_not_called()
            with self.assertRaises(OSError):
                os.fstat(context.lease_fd)
            with self.assertRaises(RuntimeError):
                with manager._open_lease(self.root, False):
                    self.fail("child cleanup released parent's EX")
        self.assert_lock_free()

    def test_complete_noncurrent_target_allows_only_probe(self):
        with manager._open_lease(self.root, True) as lease, self.interpreter(self.bdir, extra={
                "RPNH_CANDIDATE_ID": self.b, "RPNH_CANDIDATE_FD": str(lease.fileno())}):
            prepare, probe = Mock(), Mock(return_value={"fixture": "probed"})
            with self.assertRaisesRegex(candidate.CandidateContextError, "cannot be prepared"):
                candidate.run_candidate("prepare", prepare=prepare, probe=probe)
            self.assertEqual(candidate.run_candidate("probe", prepare=prepare, probe=probe), {"fixture": "probed"})
            prepare.assert_not_called()
            probe.assert_called_once()

    def test_alias_to_another_installations_venv_is_not_candidate_or_runtime_ownership(self):
        (self.bdir / "venv").rmdir()
        (self.bdir / "venv").symlink_to(self.adir / "venv", target_is_directory=True)
        with manager._open_lease(self.root, True) as lease, self.interpreter(self.bdir, extra={
                "RPNH_CANDIDATE_ID": self.b, "RPNH_CANDIDATE_FD": str(lease.fileno())}):
            with self.assertRaisesRegex(candidate.CandidateContextError, "share one installation"):
                with candidate.candidate_context("probe"):
                    self.fail("candidate borrowed another installation's venv")
        (self.root / "current").unlink()
        (self.root / "current").symlink_to(contract.installation_target(self.b))
        with self.interpreter(self.bdir):
            with self.assertRaisesRegex(ValueError, "own real venv"):
                manager.managed_root()

    def test_bad_candidate_operation_version_config_and_current_never_dispatch(self):
        self.make_plan()
        with manager._open_lease(self.root, True) as lease, self.interpreter(self.bdir, extra={
                "RPNH_CANDIDATE_ID": self.b, "RPNH_CANDIDATE_FD": str(lease.fileno())}):
            prepare, probe = Mock(), Mock()
            with self.assertRaisesRegex(candidate.CandidateContextError, "only prepare or probe"):
                candidate.run_candidate("update", prepare=prepare, probe=probe)
            with patch.object(cpn, "__version__", "99.0.0"):
                with self.assertRaisesRegex(candidate.CandidateContextError, "version"):
                    candidate.run_candidate("prepare", prepare=prepare, probe=probe)
            with patch.dict(os.environ, {"RPNH_FRONTEND_CONFIG": str(self.adir / "frontend.json")}):
                with self.assertRaisesRegex(candidate.CandidateContextError, "share one installation"):
                    candidate.run_candidate("prepare", prepare=prepare, probe=probe)
            (self.root / "current").unlink()
            (self.root / "current").symlink_to(contract.installation_target(self.b))
            with self.assertRaisesRegex(candidate.CandidateContextError, "current installation"):
                candidate.run_candidate("prepare", prepare=prepare, probe=probe)
            prepare.assert_not_called()
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
