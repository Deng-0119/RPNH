"""Real POSIX lease races with disposable installed-layout context fixtures.

No package is installed here. sys.prefix/__file__ represent an A interpreter;
directory checks, current pointer changes, subprocesses and flock are real.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cpn.rpnh import update_install as manager


_LOCK_CHILD = r'''
import fcntl, json, os, pathlib, sys
root, operation = pathlib.Path(sys.argv[1]), sys.argv[2]
fd = os.open(root / "lifecycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
try:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"lock": "busy"}), flush=True)
        raise SystemExit(0)
    if operation == "switch":
        target = sys.argv[3]
        temporary = root / ".test-switch-current"
        temporary.symlink_to(target)
        os.replace(temporary, root / "current")
        print(json.dumps({"lock": "acquired", "current": os.readlink(root / "current")}), flush=True)
    else:
        print(json.dumps({"lock": "acquired"}), flush=True)
finally:
    os.close(fd)
'''

_INHERITED_CHILD = r'''
import os, sys
fd = int(sys.argv[1])
os.fstat(fd)
print("ready", flush=True)
sys.stdin.read(1)
os.close(fd)
'''


@unittest.skipUnless(os.name == "posix", "real POSIX flock/pass_fds checks")
class LifecycleRaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rpnh-lease-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.root.chmod(0o700)
        (self.root / "versions").mkdir(mode=0o700)
        (self.root / "bin").mkdir(mode=0o700)
        (self.root / "managed.json").write_text(json.dumps({"protocol": manager.PROTOCOL}), encoding="utf-8")
        self.a = "versions/0.1.0rc2-aaaaaaaaaaaa"
        self.b = "versions/0.1.0rc2-bbbbbbbbbbbb"
        for target in (self.a, self.b):
            directory = self.root / target
            (directory / "venv/bin").mkdir(parents=True, mode=0o700)
            for name in ("python", "rpnh"):
                executable = directory / "venv/bin" / name
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o700)
            (directory / "frontend.json").write_text("{}", encoding="utf-8")
            (directory / "installed.json").write_text(json.dumps({
                "protocol": manager.PROTOCOL, "complete": True,
                "entry": "basic", "version": "0.1.0rc2"}), encoding="utf-8")
        (self.root / "current").symlink_to(self.a)

    @contextmanager
    def interpreter_a(self):
        directory = self.root / self.a
        with patch.object(manager.sys, "prefix", str(directory / "venv")), \
                patch.object(manager, "__file__", str(directory / "venv/lib/cpn/rpnh/update_install.py")), \
                patch.dict(os.environ, {
                    "RPNH_MANAGED_ROOT": str(self.root),
                    "RPNH_FRONTEND_CONFIG": str(directory / "frontend.json"),
                }):
            yield

    def child(self, operation="probe"):
        result = subprocess.run(
            [sys.executable, "-c", _LOCK_CHILD, str(self.root), operation, self.b],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=True)
        return json.loads(result.stdout)

    def switch_before_shared_lock(self, selected, exclusive):
        self.assertEqual(selected, self.root)
        self.assertFalse(exclusive)
        self.assertEqual(self.child("switch"), {"lock": "acquired", "current": self.b})
        return self.real_open(selected, exclusive)

    def test_runtime_rejects_current_changed_between_precheck_and_shared_lock(self):
        self.real_open = manager._open_lease
        with self.interpreter_a():
            self.assertEqual(manager.managed_root(), self.root)
            with patch.object(manager, "_open_lease", side_effect=self.switch_before_shared_lock):
                with self.assertRaisesRegex(ValueError, "managed environment"):
                    with manager.lifecycle_lease():
                        self.fail("stale interpreter entered after current changed")
        self.assertEqual(os.readlink(self.root / "current"), self.b)
        self.assertEqual(self.child(), {"lock": "acquired"})

    def test_worker_lease_rejects_same_current_race_and_releases_descriptor(self):
        self.real_open = manager._open_lease
        lease = None
        try:
            with self.interpreter_a(), \
                    patch.object(manager, "_open_lease", side_effect=self.switch_before_shared_lock):
                with self.assertRaisesRegex(ValueError, "managed environment"):
                    lease = manager.worker_lease()
        finally:
            if lease is not None:
                lease.close()
        self.assertEqual(os.readlink(self.root / "current"), self.b)
        self.assertEqual(self.child(), {"lock": "acquired"})

    def test_shared_lifetime_blocks_real_exclusive_updater(self):
        with self.interpreter_a(), manager.lifecycle_lease() as lease:
            self.assertIsNotNone(lease)
            self.assertEqual(self.child(), {"lock": "busy"})
            self.assertEqual(os.readlink(self.root / "current"), self.a)
        self.assertEqual(self.child(), {"lock": "acquired"})

    def test_inherited_worker_lease_survives_parent_close(self):
        with self.interpreter_a():
            lease = manager.worker_lease()
        child = subprocess.Popen(
            [sys.executable, "-c", _INHERITED_CHILD, str(lease.fileno())],
            pass_fds=(lease.fileno(),), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline(), b"ready\n")
            lease.close()
            self.assertEqual(self.child(), {"lock": "busy"})
            child.communicate(b"q", timeout=10)
            self.assertEqual(child.returncode, 0)
            self.assertEqual(self.child(), {"lock": "acquired"})
        finally:
            lease.close()
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()

    def test_explicit_management_root_does_not_require_candidate_to_be_current(self):
        self.assertEqual(self.child("switch")["current"], self.b)
        with self.interpreter_a():
            with self.assertRaises(ValueError):
                manager.managed_root()
            with manager.lifecycle_lease(self.root, exclusive=True):
                self.assertEqual(self.child(), {"lock": "busy"})
        self.assertEqual(self.child(), {"lock": "acquired"})


if __name__ == "__main__":
    unittest.main()
