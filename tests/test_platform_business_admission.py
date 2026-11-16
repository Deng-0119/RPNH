"""Read-only admission on real temporary files/SQLite and real worker claims.

No provider calls or upstream frontends are used. The launch factory in task
inventory fixtures is a declared double; the worker-claim test uses a real
subprocess and real advisory lock, without starting a business run or socket.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cpn.rpnh import main_session, task_control
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
from cpn.rpnh.business_admission import inspect_main_session_write_admission
from cpn.rpnh.session_access import MainSessionOwnerLease
from cpn.rpnh.worker_runtime import (
    WorkerCompatibilityError, observe_worker_runtime, require_worker_interoperability,
)


class TaskInventoryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-task-admission-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.root = self.directory / "control"
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.spawn = Mock(return_value=SimpleNamespace(pid=321, poll=lambda: None))
        self.control = task_control.TaskControl(self.root, popen_factory=self.spawn)

    def pending(self):
        return self.control.start(AgentTaskSpec(
            run_dir=self.directory / "child", prompt="Inspect only this task.",
            stages=(AgentStage("worker", "Return one result."),),
            execution_config_path=self.directory / "execution.json"))

    def manifest(self, handle):
        return self.root / "manifests" / f"{handle.task_id}.json"

    def test_unknown_neighbour_blocks_all_pending_recovery_without_writes(self):
        handle = self.pending()
        good = self.manifest(handle)
        unknown = good.parent / "task-future.json"
        unknown.write_text(json.dumps({"schema_version": "rpnh/task_handle/future",
                                      "task_id": "task-future"}), encoding="utf-8")
        before = {path.name: path.read_bytes() for path in good.parent.iterdir()}
        spawn = Mock()
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "task-future.json"):
            task_control.TaskControl(self.root, popen_factory=spawn)
        spawn.assert_not_called()
        self.assertEqual(before, {path.name: path.read_bytes() for path in good.parent.iterdir()})
        self.assertFalse(handle.run_dir.exists())

    def test_nonstandard_final_json_name_is_not_silently_ignored(self):
        directory = self.root / "manifests"
        directory.mkdir(parents=True)
        (directory / "future-record.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "future-record.json"):
            task_control.inspect_task_inventory(self.root)

    def test_duplicate_json_key_is_rejected_without_spawn(self):
        handle = self.pending()
        path = self.manifest(handle)
        path.write_text('{"task_id":"one","task_id":"two"}', encoding="utf-8")
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "invalid JSON"):
            task_control.TaskControl(self.root, popen_factory=Mock())

    def test_unknown_manifest_field_is_rejected(self):
        handle = self.pending()
        path = self.manifest(handle)
        value = json.loads(path.read_text())
        value["future_meaning"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "unknown task manifest field"):
            task_control.inspect_task_inventory(self.root)

    def test_changed_pending_runtime_source_is_not_rewritten_or_spawned(self):
        handle = self.pending()
        path = self.manifest(handle)
        value = json.loads(path.read_text())
        value["launch_runtime"]["package_root"] = str(self.directory / "another-package")
        path.write_text(json.dumps(value), encoding="utf-8")
        before = path.read_bytes()
        spawn = Mock()
        with self.assertRaisesRegex(WorkerCompatibilityError, "runtime/source changed"):
            task_control.TaskControl(self.root, popen_factory=spawn)
        spawn.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_pending_without_runtime_remains_readable_but_not_recovered(self):
        handle = self.pending()
        path = self.manifest(handle)
        value = json.loads(path.read_text())
        value.pop("launch_runtime")
        value.pop("worker_runtime")
        path.write_text(json.dumps(value), encoding="utf-8")
        restored = task_control.TaskControl(self.root, recover_pending_launches=False)
        self.assertEqual(restored.get(handle.task_id).spec, handle.spec)
        with self.assertRaisesRegex(WorkerCompatibilityError, "missing or malformed"):
            task_control.TaskControl(self.root, popen_factory=Mock())

    def test_inventory_detects_changed_spec_without_hash_or_writer(self):
        handle = self.pending()
        inventory = task_control.inspect_task_inventory(self.root)
        path = self.root / "specs" / f"{handle.task_id}.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "document changed"):
            inventory.assert_unchanged()

    def test_bad_refresh_cannot_keep_a_stale_process_for_control(self):
        handle = self.pending()
        self.manifest(handle).write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "cannot be refreshed"):
            self.control.stop(handle.task_id, startup_safe=True)

    def test_direct_claim_cannot_activate_an_unknown_legacy_pending_intent(self):
        handle = self.pending()
        path = self.manifest(handle)
        value = json.loads(path.read_text())
        value.pop("launch_runtime")
        value.pop("worker_runtime")
        path.write_text(json.dumps(value), encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaisesRegex(WorkerCompatibilityError, "missing or malformed"):
            task_control.claim_task_worker_launch(
                self.root / "specs" / f"{handle.task_id}.json", handle.spec, resume=False)
        self.assertEqual(path.read_bytes(), before)
        lock = task_control._acquire_task_launch_lock(self.root, handle.task_id)
        self.assertIsNotNone(lock)
        lock.close()

    def test_direct_claim_uses_strict_manifest_fields_and_exact_spec(self):
        handle = self.pending()
        path = self.manifest(handle)
        value = json.loads(path.read_text())
        value["unknown_future_meaning"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        before = path.read_bytes()
        spec_path = self.root / "specs" / f"{handle.task_id}.json"
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "unknown task manifest field"):
            task_control.claim_task_worker_launch(spec_path, handle.spec, resume=False)
        self.assertEqual(path.read_bytes(), before)
        value.pop("unknown_future_meaning")
        path.write_text(json.dumps(value), encoding="utf-8")
        spec = json.loads(spec_path.read_text())
        spec["prompt"] = "A changed task cannot claim the earlier loaded spec."
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "spec changed"):
            task_control.claim_task_worker_launch(spec_path, handle.spec, resume=False)
        self.assertEqual(json.loads(path.read_text())["launch_state"], "pending")

    def test_unchanged_status_refresh_does_not_reparse_the_large_spec(self):
        handle = self.pending()
        spec_path = self.root / "specs" / f"{handle.task_id}.json"
        with patch.object(task_control, "_read_task_document", wraps=task_control._read_task_document) as reader:
            self.control.get(handle.task_id)
            self.control.get(handle.task_id)
        self.assertTrue(reader.call_count)
        self.assertFalse(any(call.args[0] == spec_path for call in reader.call_args_list))
        document = json.loads(spec_path.read_text())
        document["prompt"] = "Changed persisted task."
        spec_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(task_control.TaskAdmissionError, "source/spec binding changed"):
            self.control.get(handle.task_id)

    def test_unknown_worker_protocol_blocks_control_before_socket_or_signal(self):
        handle = self.pending()
        handle.worker_runtime = {**observe_worker_runtime(), "control_protocol": "future/v1"}
        self.control._write_manifest(handle, launch_state="active", launch_mode="fresh",
                                     pid=os.getpid(), process_identity=task_control.read_process_identity(os.getpid()))
        with patch.object(task_control, "ControlClient") as client, \
                patch.object(task_control.os, "kill") as signal:
            for operation in (lambda: self.control.message(handle.task_id, "hello"),
                              lambda: self.control.snapshot(handle.task_id),
                              lambda: self.control.stop(handle.task_id)):
                with self.assertRaisesRegex(WorkerCompatibilityError, "interoperability scope"):
                    operation()
        client.assert_not_called()
        signal.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin", "real POSIX process birth observation")
    def test_real_child_claim_records_actual_runtime_and_preserves_lease(self):
        handle = self.pending()
        spec_path = self.root / "specs" / f"{handle.task_id}.json"
        program = (
            "import json,sys; from pathlib import Path; "
            "from cpn.rpnh.agent_tasks import AgentTaskSpec; "
            "from cpn.rpnh.task_control import claim_task_worker_launch; "
            "p=Path(sys.argv[1]); s=AgentTaskSpec.from_worker_document(json.loads(p.read_text()),document_root=p.parent); "
            "lease=claim_task_worker_launch(p,s,resume=False); "
            "print('claimed',flush=True); sys.stdin.read(1); lease.close()")
        child = subprocess.Popen([sys.executable, "-c", program, str(spec_path)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "claimed")
            value = json.loads(self.manifest(handle).read_text())
            self.assertEqual(value["pid"], child.pid)
            self.assertEqual(value["worker_runtime"], observe_worker_runtime())
            require_worker_interoperability(value["worker_runtime"], label="real child")
            self.assertIsNone(task_control._acquire_task_launch_lock(self.root, handle.task_id))
            inventory = task_control.inspect_task_inventory(self.root)
            inventory.require_write_compatible()
            self.assertFalse(handle.run_dir.exists())
        finally:
            child.communicate("x", timeout=10)
        self.assertEqual(child.returncode, 0)
        # An exited historical task without added runtime fields is not denied
        # solely for missing software provenance.
        value.pop("worker_runtime")
        value.pop("launch_runtime")
        self.manifest(handle).write_text(json.dumps(value), encoding="utf-8")
        task_control.inspect_task_inventory(self.root).require_write_compatible()


class MainSessionBusinessAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-main-admission-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.adapter = self.directory / "adapter.json"
        self.adapter.write_text(json.dumps({
            "schema_version": "local_process_adapter_config/v1", "adapter_kind": "local_process",
            "model_condition": "offline-admission", "argv": [sys.executable, "-c", "pass"],
            "probe_argv": [sys.executable, "-c", "pass"], "env": {}, "inherit_env": [],
        }), encoding="utf-8")
        self.execution = self.directory / "execution.json"
        self.execution.write_text(json.dumps({
            "schema_version": "llm_execution_selection/v1", "adapter_kind": "local_process",
            "model_condition": "offline-admission", "adapter_config_path": str(self.adapter),
            "timeout_seconds": 30, "max_output_tokens": 1024, "max_response_bytes": 65536,
        }), encoding="utf-8")
        self.root = self.directory / "session"
        self.owner_lease = MainSessionOwnerLease.reserve_for_creation(self.root)
        self.addCleanup(self.owner_lease.close)
        self.session = main_session.MainSession(self.root, self.execution, task_control=object(),
            owner_root_reserved=True, owner_lease=self.owner_lease)
        self.database = self.root / "main" / ".registry_v1" / "registry.sqlite3"

    def facts(self):
        with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True) as db:
            return (db.execute("SELECT key,value FROM registry_meta ORDER BY key").fetchall(),
                    db.execute("SELECT count(*) FROM events").fetchone(),
                    db.execute("SELECT count(*) FROM objects").fetchone())

    def reject_before_writer(self, pattern):
        before = self.facts()
        with patch.object(main_session, "_RegistryCore", wraps=main_session._RegistryCore) as writer, \
                patch.object(main_session, "TaskControl") as control, \
                self.assertRaisesRegex(ValueError, pattern):
            main_session.MainSession.resume(self.root, owner_lease=self.owner_lease)
        writer.assert_not_called()
        control.assert_not_called()
        self.assertEqual(self.facts(), before)

    def test_unknown_task_record_is_rejected_before_writer_and_pending_recovery(self):
        manifest = self.session.child_path_root / "tasks" / "manifests" / "task-future.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"schema_version": "rpnh/task_handle/future", "task_id": "task-future"}))
        self.reject_before_writer("task-future.json")

    def test_unknown_main_path_contract_is_rejected_before_writer(self):
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE registry_meta SET value=? WHERE key=?", ("future/v1", "main_thread_child_path_base"))
        self.reject_before_writer("child path base is unknown")

    def test_live_legacy_worker_without_protocol_is_rejected_before_writer(self):
        control = task_control.TaskControl(self.session.child_path_root / "tasks", popen_factory=Mock())
        handle = control.start(AgentTaskSpec(
            run_dir=self.directory / "child", prompt="A selected existing task.",
            stages=(AgentStage("worker", "Return one result."),), execution_config_path=self.execution))
        control._write_manifest(handle, launch_state="active", launch_mode="fresh", pid=os.getpid(),
                                process_identity=task_control.read_process_identity(os.getpid()))
        self.reject_before_writer("worker runtime observation is missing")

    def test_admission_uses_existing_cut_without_creating_facts(self):
        before = self.facts()
        admission = inspect_main_session_write_admission(self.root)
        admission.assert_unchanged()
        self.assertEqual(self.facts(), before)
        self.assertEqual(admission.tasks.handles, ())

    def test_normal_resume_still_opens_exactly_one_writer(self):
        before = dict(self.facts()[0])["writer_epoch"]
        with patch.object(main_session, "_RegistryCore", wraps=main_session._RegistryCore) as writer:
            resumed = main_session.MainSession.resume(self.root, task_control=object(),
                owner_lease=self.owner_lease)
        writer.assert_called_once()
        self.assertEqual(int(dict(self.facts()[0])["writer_epoch"]), int(before) + 1)
        self.assertEqual(resumed.history, [])


if __name__ == "__main__":
    unittest.main()
