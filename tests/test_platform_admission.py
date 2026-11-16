"""Main-session profile checks before opening a writer or recovering tasks.

The fixtures use actual local profile files and a temporary SQLite Registry.
They do not execute an adapter, bind IPC, or start a task worker. These checks
cover the profile ordering slice only, not complete cross-release admission.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from cpn.rpnh import main_session
from cpn.rpnh.session_access import MainSessionOwnerLease


class MainSessionProfileAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-profile-admission-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        environment = patch.dict(os.environ, {
            "RPNH_PROFILE_DIR": str(self.directory / "unconfigured-profiles"),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.adapter = self.directory / "adapter.json"
        self.adapter.write_text(json.dumps({
            "schema_version": "local_process_adapter_config/v1",
            "adapter_kind": "local_process",
            "model_condition": "offline-profile-admission",
            "argv": [sys.executable, "-c", "pass"],
            "probe_argv": [sys.executable, "-c", "pass"],
            "env": {},
            "inherit_env": [],
        }), encoding="utf-8")
        self.execution = self.directory / "execution.json"
        self.execution.write_text(json.dumps({
            "schema_version": "llm_execution_selection/v1",
            "adapter_kind": "local_process",
            "model_condition": "offline-profile-admission",
            "adapter_config_path": str(self.adapter),
            "timeout_seconds": 30,
            "max_output_tokens": 1024,
            "max_response_bytes": 65536,
        }), encoding="utf-8")
        self.root = self.directory / "session"
        # Establish actual Registry/profile authority without a TaskControl.
        self.owner_lease = MainSessionOwnerLease.reserve_for_creation(self.root)
        self.addCleanup(self.owner_lease.close)
        main_session.MainSession(self.root, self.execution, task_control=object(),
            owner_root_reserved=True, owner_lease=self.owner_lease)

    def registry_facts(self):
        database = self.root / "main" / ".registry_v1" / "registry.sqlite3"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            return {
                "meta": db.execute(
                    "SELECT key,value FROM registry_meta ORDER BY key").fetchall(),
                "events": db.execute("SELECT count(*) FROM events").fetchone()[0],
                "objects": db.execute("SELECT count(*) FROM objects").fetchone()[0],
            }

    def writer_epoch(self):
        return int(dict(self.registry_facts()["meta"])["writer_epoch"])

    def assert_rejected_before_writer(self, callback, message):
        before = self.registry_facts()
        task_control = Mock(return_value=object())
        with patch.object(main_session, "_RegistryCore", wraps=main_session._RegistryCore) as writer, \
                patch.object(main_session, "TaskControl", task_control), \
                self.assertRaisesRegex(ValueError, message):
            callback()
        writer.assert_not_called()
        task_control.assert_not_called()
        self.assertEqual(self.registry_facts(), before)

    def change_adapter_identity(self):
        document = json.loads(self.adapter.read_text(encoding="utf-8"))
        document["argv"][0] = str(self.directory / "different-local-adapter")
        self.adapter.write_text(json.dumps(document), encoding="utf-8")

    def test_missing_profile_authority_is_rejected_before_writer(self):
        (self.root / main_session.MainSession.PROFILE_FILE).unlink()
        (self.root / main_session.MainSession.STATE_FILE).unlink()
        self.assert_rejected_before_writer(
            lambda: main_session.MainSession(self.root, self.execution, resume=True, owner_lease=self.owner_lease),
            "no persisted execution profile authority")

    def test_different_execution_path_is_rejected_before_writer(self):
        self.assert_rejected_before_writer(
            lambda: main_session.MainSession.resume(
                self.root, self.directory / "other-execution.json", owner_lease=self.owner_lease),
            "cannot change the execution profile")

    def test_changed_adapter_identity_is_rejected_before_writer(self):
        self.change_adapter_identity()
        self.assert_rejected_before_writer(
            lambda: main_session.MainSession.resume(self.root, owner_lease=self.owner_lease),
            "execution profile identity changed")

    def test_unknown_persisted_profile_schema_is_rejected_before_writer(self):
        profile = self.root / main_session.MainSession.PROFILE_FILE
        document = json.loads(profile.read_text(encoding="utf-8"))
        document["schema_version"] = "rpnh/main_session_profile/future"
        profile.write_text(json.dumps(document), encoding="utf-8")
        self.assert_rejected_before_writer(
            lambda: main_session.MainSession.resume(self.root, owner_lease=self.owner_lease),
            "invalid execution profile projection")

    def test_unchanged_profile_enters_normal_resume(self):
        before = self.writer_epoch()
        with patch.object(main_session, "TaskControl", wraps=main_session.TaskControl) as control:
            resumed = main_session.MainSession.resume(self.root, owner_lease=self.owner_lease)
        control.assert_called_once()
        self.assertEqual(resumed.execution_config_path, self.execution)
        self.assertEqual(self.writer_epoch(), before + 1)
        self.assertEqual(resumed.history, [])

    def test_existing_post_writer_identity_check_is_retained(self):
        original_core = main_session._RegistryCore

        def open_then_change(*args, **kwargs):
            core = original_core(*args, **kwargs)
            self.change_adapter_identity()
            return core

        with patch.object(main_session, "_RegistryCore", side_effect=open_then_change) as writer, \
                patch.object(main_session, "TaskControl", return_value=object()), \
                self.assertRaisesRegex(ValueError, "execution profile identity changed"):
            main_session.MainSession.resume(self.root, owner_lease=self.owner_lease)
        writer.assert_called_once()

    def test_legacy_v1_profile_keeps_existing_read_and_upgrade_behavior(self):
        profile = self.root / main_session.MainSession.PROFILE_FILE
        profile.write_text(json.dumps({
            "schema_version": "rpnh/main_session_profile/v1",
            "execution_config_path": str(self.execution),
        }), encoding="utf-8")
        resumed = main_session.MainSession.resume(self.root, task_control=object(), owner_lease=self.owner_lease)
        self.assertEqual(resumed.execution_config_path, self.execution)
        self.assertEqual(json.loads(profile.read_text(encoding="utf-8"))["schema_version"],
                         "rpnh/main_session_profile/v3")


if __name__ == "__main__":
    unittest.main()
