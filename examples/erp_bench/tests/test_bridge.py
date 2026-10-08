"""Offline transport and strict fake-Harbor contracts; no real ERP or model."""
import ast
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import socket
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rpnh_erp_bench.bridge import Bridge, MAX_FRAME, MAX_OUTPUT, receive_frame, send_frame
from rpnh_erp_bench.sandbox import (HarborBackend, IsolationError, PreparedSandbox,
                                  _PROBE, _RUNNER, install_firewall_packages,
                                  prepare_sandbox, validate_container_inspect)


def invocation(call="call-1", **arguments):
    return {"identity": {"trial_id": "trial-1", "operation_id": "operation-1",
                         "invocation_id": "invocation-1", "firing_id": "firing-1", "call_id": call},
            "arguments": arguments or {"source": "print('fixture')", "timeout_seconds": 1},
            "remaining_seconds": 1}


def request(endpoint, frame):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(str(endpoint))
        send_frame(connection, frame)
        return receive_frame(connection)


class FakeBackend:
    def __init__(self, status="completed", delay=0):
        self.calls = []
        self.status, self.delay = status, delay
        self.active = self.maximum = 0
        self.entered = threading.Event()
        self.stopped = threading.Event()

    def execute_python(self, source, timeout_seconds, cancellation_requested, identity):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.calls.append((source, timeout_seconds, identity))
        self.entered.set()
        try:
            until = time.monotonic() + self.delay
            while time.monotonic() < until:
                if cancellation_requested():
                    self.stopped.set()
                    return {"status": "unknown", "stdout": "", "stderr": "cancelled",
                            "exit_code": None, "execution_id": "original-execution"}
                time.sleep(0.005)
            if self.status == "lost":
                raise OSError("private host details must not leak")
            return {"status": self.status, "stdout": "x" * (MAX_OUTPUT + 1), "stderr": "fixture",
                    "exit_code": 0, "execution_id": "original-execution"}
        finally:
            self.active -= 1


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"], prefix="b-")
        self.addCleanup(self.temporary.cleanup)
        self.endpoint = Path(self.temporary.name) / "b.sock"

    def test_identity_bounded_response_and_duplicate_known_result(self):
        backend = FakeBackend()
        with Bridge(self.endpoint, "trial-1", backend):
            first = request(self.endpoint, invocation())
            second = request(self.endpoint, invocation())
        self.assertEqual(first, second)
        self.assertEqual(first["identity"], invocation()["identity"])
        self.assertEqual(first["execution_id"], "original-execution")
        self.assertEqual(first["admission_granularity"], "script")
        self.assertEqual(len(first["stdout"]), MAX_OUTPUT)
        self.assertEqual(len(backend.calls), 1)
        self.assertFalse(self.endpoint.exists())

    def test_unknown_is_not_replayed_and_blocks_later_writes(self):
        backend = FakeBackend("lost")
        with Bridge(self.endpoint, "trial-1", backend):
            first = request(self.endpoint, invocation())
            repeated = request(self.endpoint, invocation())
            next_call = request(self.endpoint, invocation("call-2"))
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(first, repeated)
        self.assertEqual(next_call["status"], "interrupted")
        self.assertEqual(len(backend.calls), 1)
        self.assertNotIn("private host", first["stderr"])

    def test_duplicate_changed_identity_or_source_is_rejected(self):
        backend = FakeBackend()
        with Bridge(self.endpoint, "trial-1", backend):
            request(self.endpoint, invocation())
            for frame in (invocation(source="different"), invocation()):
                frame["identity"]["invocation_id"] = "different"
                self.assertIn("error", request(self.endpoint, frame))
        self.assertEqual(len(backend.calls), 1)

    def test_wrong_trial_and_model_transport_fields_are_rejected(self):
        backend = FakeBackend()
        with Bridge(self.endpoint, "trial-1", backend):
            frames = [invocation(source="pass", endpoint="/tmp/other"), invocation(source="pass", filename="../../host"),
                      invocation(source="pass", timeout_seconds=True), invocation(source="pass", timeout_seconds=3601)]
            other = invocation()
            other["identity"]["trial_id"] = "other"
            frames.append(other)
            for frame in frames:
                self.assertIn("error", request(self.endpoint, frame))
        self.assertEqual(backend.calls, [])

    def test_python_is_not_keyword_filtered(self):
        # This asserts routing only, not real container isolation. The source is
        # never evaluated on the host; real access depends on prepared container.
        source = "import pathlib\nprint(pathlib.Path('../../etc/passwd').read_text())"
        backend = FakeBackend()
        with Bridge(self.endpoint, "trial-1", backend):
            self.assertEqual(request(self.endpoint, invocation(source=source))["status"], "completed")
        self.assertEqual(backend.calls[0][0], source)

    def test_concurrent_writes_are_serialized(self):
        backend = FakeBackend(delay=0.03)
        with Bridge(self.endpoint, "trial-1", backend), ThreadPoolExecutor(4) as pool:
            values = list(pool.map(lambda i: request(self.endpoint, invocation(str(i))), range(4)))
        self.assertTrue(all(row["status"] == "completed" for row in values))
        self.assertEqual(backend.maximum, 1)

    def test_deadline_propagates_cooperative_cancellation(self):
        backend = FakeBackend(delay=1)
        frame = invocation()
        frame["remaining_seconds"] = 0.05
        with Bridge(self.endpoint, "trial-1", backend):
            answer = request(self.endpoint, frame)
        self.assertEqual(answer["status"], "unknown")
        self.assertTrue(backend.stopped.is_set())
        self.assertLessEqual(backend.calls[0][1], 0.05)

    def test_disconnect_does_not_replay_inflight_mutation(self):
        backend = FakeBackend(delay=1)
        with Bridge(self.endpoint, "trial-1", backend):
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(str(self.endpoint))
            send_frame(connection, invocation())
            self.assertTrue(backend.entered.wait(1))
            connection.close()
            self.assertTrue(backend.stopped.wait(1))
            answer = request(self.endpoint, invocation())
        self.assertEqual(answer["status"], "unknown")
        self.assertEqual(len(backend.calls), 1)

    def test_close_admission_cancels_and_rejects_new_calls(self):
        backend = FakeBackend()
        with Bridge(self.endpoint, "trial-1", backend) as bridge:
            bridge.close_admission()
            self.assertEqual(request(self.endpoint, invocation())["status"], "interrupted")
        self.assertEqual(backend.calls, [])

    def test_oversized_frame_rejected_without_reading_body(self):
        backend = FakeBackend()
        with Bridge(self.endpoint, "trial-1", backend):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(str(self.endpoint))
                connection.sendall(struct.pack("!I", MAX_FRAME + 1))
                self.assertIn("error", receive_frame(connection))
        self.assertEqual(backend.calls, [])

    def test_domain_infeasible_is_preserved(self):
        with Bridge(self.endpoint, "trial-1", FakeBackend("domain_infeasible")):
            self.assertEqual(request(self.endpoint, invocation())["status"], "domain_infeasible")

    def test_interrupted_backend_also_closes_world_to_new_writes(self):
        backend = FakeBackend("interrupted")
        with Bridge(self.endpoint, "trial-1", backend):
            first = request(self.endpoint, invocation())
            self.assertEqual(request(self.endpoint, invocation()), first)
            following = request(self.endpoint, invocation("next-call"))
        self.assertEqual(first["status"], "interrupted")
        self.assertIn("reconciliation", following["stderr"])
        self.assertEqual(len(backend.calls), 1)

    def test_completed_response_loss_blocks_next_call_before_delivery_lock_releases(self):
        backend = FakeBackend()
        sending = threading.Event()
        release = threading.Event()
        second_arrived = threading.Event()
        failed = False
        class ObservedBridge(Bridge):
            def _dispatch(self, frame, connection):
                if frame["identity"]["call_id"] == "call-2":
                    second_arrived.set()
                return super()._dispatch(frame, connection)
        def blocked_delivery(connection, answer):
            nonlocal failed
            if answer.get("identity", {}).get("call_id") == "call-1" and not failed:
                failed = True
                sending.set()
                release.wait(2)
                connection.shutdown(socket.SHUT_RDWR)
            return send_frame(connection, answer)
        with patch("rpnh_erp_bench.bridge.send_frame", side_effect=blocked_delivery):
            with ObservedBridge(self.endpoint, "trial-1", backend), ThreadPoolExecutor(2) as pool:
                first = pool.submit(request, self.endpoint, invocation())
                self.assertTrue(sending.wait(1))
                second = pool.submit(request, self.endpoint, invocation("call-2"))
                try:
                    self.assertTrue(second_arrived.wait(1))
                    time.sleep(0.05)
                    self.assertEqual(len(backend.calls), 1, "a second write overtook response delivery")
                finally:
                    release.set()
                with self.assertRaises((EOFError, OSError)):
                    first.result(2)
                self.assertEqual(second.result(2)["status"], "interrupted")
                repeated = request(self.endpoint, invocation())
                self.assertEqual(repeated["execution_id"], "original-execution")
                self.assertEqual(repeated["identity"], invocation()["identity"])
                self.assertEqual(len(backend.calls), 1)


def inspect_fixture():
    return {"Id": "container-fixture", "HostConfig": {"NetworkMode": "none", "Privileged": False}, "Mounts": []}


class StrictHarbor:
    """Exact Harbor 0.24 signature; examines bytes, never executes actor source."""
    def __init__(self, *, lost=False, code=0):
        self.calls = []
        self.source = bytearray()
        self.executions = 0
        self.lost, self.code = lost, code

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.calls.append((command, cwd, timeout_sec, user, threading.get_ident()))
        if user == "root":
            assert cwd == "/root"
            assert "python3 -I -c " in command
            return SimpleNamespace(return_code=0, stdout="1", stderr="")
        assert user == "agent" and cwd == "/workspace"
        words = shlex.split(command)
        assert words[:5] == ["setpriv", "--no-new-privs", "python3", "-I", "-c"]
        if words[5] == _RUNNER:
            assert words[6].startswith("/workspace/rpnh-") and words[6].endswith(".py")
            self.executions += 1
            if self.lost:
                raise TimeoutError("compose lost response after container mutation")
            return SimpleNamespace(return_code=0, stdout=json.dumps({"status": "failed" if self.code else "completed",
                                   "stdout": "fixture stdout", "stderr": "fixture stderr", "exit_code": self.code}), stderr="")
        stage = ast.parse(words[5])
        call = stage.body[1].value
        mode = ast.literal_eval(call.func.value.args[1])
        assert mode == ("ab" if self.source else "xb")
        self.source.extend(base64.b64decode(ast.literal_eval(call.args[0].args[0])))
        return SimpleNamespace(return_code=0, stdout="", stderr="")


class DelayedDispatchHarbor(StrictHarbor):
    """Compose dispatch survives cancellation of its Python awaiter, as an RPC can."""
    def __init__(self):
        super().__init__()
        self.pending = threading.Event()
        self.release = threading.Event()
        self.killed = threading.Event()
        self.wrapper_cancelled = False
        self.kills = 0
        self.events = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        if user == "root":
            self.kills += 1
            self.events.append("kill")
            self.killed.set()
        if user == "agent" and _RUNNER in shlex.split(command):
            async def underlying_dispatch():
                self.pending.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.005)
                self.events.append("dispatch")
                return await super(DelayedDispatchHarbor, self).exec(command, cwd, env, timeout_sec, user)
            self.underlying = asyncio.create_task(underlying_dispatch())
            try:
                return await asyncio.shield(self.underlying)
            except asyncio.CancelledError:
                self.wrapper_cancelled = True
                raise
        return await super().exec(command, cwd, env, timeout_sec, user)


class HarborContractTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever)
        self.thread.start()
        self.addCleanup(self.cleanup_loop)

    def cleanup_loop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()

    def backend(self, environment):
        return HarborBackend(environment, self.loop, PreparedSandbox(environment, "container-fixture", {}))

    def test_exact_script_upload_generated_name_one_exec_loop_and_cleanup(self):
        environment = StrictHarbor()
        backend = self.backend(environment)
        source = "# Unicode 路径\n" + "#" * 70000 + "\nprint(open('../../etc/passwd').read())"
        answer = backend.execute_python(source, 2, lambda: False, invocation()["identity"])
        self.assertEqual(answer["status"], "completed")
        self.assertEqual(environment.source.decode(), source)
        self.assertEqual(environment.executions, 1)
        self.assertTrue(all(row[4] == self.thread.ident for row in environment.calls))
        self.assertEqual(environment.calls[-1][3], "root")
        self.assertIn("pkill -KILL -u agent", environment.calls[-1][0])
        self.assertIn("/proc", environment.calls[-1][0])
        self.assertTrue(all(len(row[0]) < 131072 for row in environment.calls))

    def test_nonzero_script_result_is_failed(self):
        answer = self.backend(StrictHarbor(code=7)).execute_python("raise Exception()", 2, lambda: False, {})
        self.assertEqual((answer["status"], answer["exit_code"]), ("failed", 7))

    def test_lost_harbor_response_poison_and_no_new_exec(self):
        environment = StrictHarbor(lost=True)
        backend = self.backend(environment)
        self.assertEqual(backend.execute_python("mutate()", 1, lambda: False, {})["status"], "unknown")
        self.assertEqual(backend.execute_python("mutate()", 1, lambda: False, {})["status"], "interrupted")
        self.assertEqual(environment.executions, 1)
        self.assertFalse(backend.sandbox.ready)

    def test_lingering_background_process_is_unknown(self):
        class BackgroundHarbor(StrictHarbor):
            async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
                response = await super().exec(command, cwd, env, timeout_sec, user)
                if user == "root":
                    response.stdout = "0"
                return response
        environment = BackgroundHarbor()
        backend = self.backend(environment)
        self.assertEqual(backend.execute_python("spawn_background()", 2, lambda: False, {})["status"], "unknown")
        self.assertFalse(backend.sandbox.ready)

    def test_quiesce_during_upload_cannot_launch_script_after_stop(self):
        entered = threading.Event()
        release = threading.Event()
        class SlowUpload(StrictHarbor):
            async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
                if user == "agent":
                    entered.set()
                    while not release.is_set():
                        await asyncio.sleep(0.005)
                return await super().exec(command, cwd, env, timeout_sec, user)
        environment = SlowUpload()
        backend = self.backend(environment)
        with ThreadPoolExecutor(1) as pool:
            response = pool.submit(backend.execute_python, "mutate()", 4, lambda: False, {})
            self.assertTrue(entered.wait(1))
            settling = asyncio.run_coroutine_threadsafe(backend.quiesce(), self.loop)
            try:
                time.sleep(0.05)
                self.assertFalse(settling.done(), "quiescence overtook the upload RPC")
            finally:
                release.set()
            stopped = settling.result(2)
            self.assertEqual(stopped["status"], "quiescent")
            self.assertEqual(response.result(2)["status"], "unknown")
        self.assertEqual(environment.executions, 0)

    def test_delayed_compose_dispatch_is_killed_again_before_quiescence(self):
        environment = DelayedDispatchHarbor()
        backend = self.backend(environment)
        cancelled = threading.Event()
        with ThreadPoolExecutor(1) as pool:
            response = pool.submit(backend.execute_python, "mutate()", 2, cancelled.is_set, {})
            self.assertTrue(environment.pending.wait(1))
            cancelled.set()
            self.assertEqual(response.result(1)["status"], "unknown")
            settling = asyncio.run_coroutine_threadsafe(backend.quiesce(), self.loop)
            try:
                self.assertTrue(environment.killed.wait(1))
                self.assertFalse(settling.done())
                self.assertGreaterEqual(environment.kills, 1)
                self.assertFalse(environment.wrapper_cancelled)
            finally:
                environment.release.set()
            self.assertEqual(settling.result(2)["status"], "quiescent")
        self.assertEqual(environment.executions, 1)
        self.assertEqual(environment.events[0], "kill")
        self.assertEqual(environment.events[-1], "kill")
        self.assertGreater(environment.events.index("dispatch"), 0)

    def test_unsettled_compose_rpc_blocks_freeze_without_cancelling_it(self):
        environment = DelayedDispatchHarbor()
        backend = self.backend(environment)
        with patch("rpnh_erp_bench.sandbox.RPC_CLEANUP_SECONDS", 0.1):
            with ThreadPoolExecutor(1) as pool:
                response = pool.submit(backend.execute_python, "mutate()", 0.1, lambda: False, {})
                self.assertTrue(environment.pending.wait(1))
                self.assertEqual(response.result(1)["status"], "unknown")
                try:
                    settling = asyncio.run_coroutine_threadsafe(backend.quiesce(), self.loop)
                    with self.assertRaisesRegex(IsolationError, "pending"):
                        settling.result(1)
                    self.assertGreaterEqual(environment.kills, 1)
                    self.assertFalse(environment.wrapper_cancelled)
                    self.assertTrue(any(not task.done() for task in backend._rpc_tasks))
                finally:
                    environment.release.set()
                # Cleanup the synthetic delayed RPC, never abandon a test task.
                for future in backend._submissions:
                    future.result(2)

    def test_early_stop_long_script_kills_promptly_then_fails_bounded_without_rpc_cancel(self):
        environment = DelayedDispatchHarbor()
        backend = self.backend(environment)
        with patch("rpnh_erp_bench.sandbox.RPC_CLEANUP_SECONDS", 0.15):
            with ThreadPoolExecutor(1) as pool:
                response = pool.submit(backend.execute_python, "mutate()", 3600, lambda: False, {})
                self.assertTrue(environment.pending.wait(1))
                settling = asyncio.run_coroutine_threadsafe(backend.quiesce(), self.loop)
                try:
                    self.assertTrue(environment.killed.wait(0.1), "early stop waited for the script budget")
                    with self.assertRaisesRegex(IsolationError, "pending"):
                        settling.result(1)
                    self.assertFalse(environment.wrapper_cancelled)
                    self.assertFalse(response.done())
                finally:
                    environment.release.set()
                response.result(2)

    def test_failed_rpc_is_not_quiescence_even_if_wrapper_finished(self):
        environment = StrictHarbor(lost=True)
        backend = self.backend(environment)
        self.assertEqual(backend.execute_python("mutate()", 1, lambda: False, {})["status"], "unknown")
        with self.assertRaisesRegex(IsolationError, "uncertain"):
            asyncio.run_coroutine_threadsafe(backend.quiesce(), self.loop).result(1)

    def test_rpc_timeout_error_is_not_mistaken_for_polling_a_long_script(self):
        backend = self.backend(StrictHarbor(lost=True))
        cancelled = threading.Event()
        with ThreadPoolExecutor(1) as pool:
            response = pool.submit(backend.execute_python, "mutate()", 3600, cancelled.is_set, {})
            try:
                self.assertEqual(response.result(1)["status"], "unknown")
                self.assertTrue(backend._rpc_fault)
            finally:
                cancelled.set()

    def test_root_python_cannot_import_agent_workspace_shadow_modules(self):
        # Execute only the fixed benign diagnostic, never an actor script. A
        # fake exec maps the container workspace/root to task-local directories.
        import subprocess
        import sys
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"], prefix="root-helper-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "pathlib.py").write_text("raise RuntimeError('agent workspace import')\n")
            (workspace / "sitecustomize.py").write_text("raise RuntimeError('agent startup import')\n")
            environment = StrictHarbor()
            backend = self.backend(environment)
            asyncio.run_coroutine_threadsafe(backend.quiesce(), self.loop).result(1)
            command, cwd, *_ = environment.calls[-1]
            self.assertEqual(cwd, "/root")
            words = shlex.split(command)
            python_index = words.index("python3")
            options = words[python_index + 1:words.index("-c") + 1]
            probe = subprocess.run([sys.executable, *options, "import pathlib; print(pathlib.__file__)"],
                cwd=workspace, env=dict(os.environ, PYTHONPATH=str(workspace)), text=True, capture_output=True, timeout=2)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertNotIn(str(workspace), probe.stdout)

    def test_quiesce_closes_backend_and_confirms_processes(self):
        environment = StrictHarbor()
        backend = self.backend(environment)
        evidence = asyncio.run_coroutine_threadsafe(backend.quiesce(), self.loop).result(2)
        self.assertEqual(evidence["solver_uid_processes"], 0)
        self.assertEqual(backend.execute_python("pass", 1, lambda: False, {})["status"], "interrupted")
        self.assertEqual(environment.executions, 0)

    def test_unprepared_environment_is_rejected(self):
        with self.assertRaises(IsolationError):
            HarborBackend(StrictHarbor(), self.loop, PreparedSandbox(object(), "wrong", {}))

    def test_container_programs_compile_without_running_actor_code(self):
        compile(_RUNNER, "container-runner", "exec")
        compile(_PROBE, "container-probe", "exec")

    def test_actual_probe_failure_blocks_preparation(self):
        class FailingProbe:
            async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
                return SimpleNamespace(return_code=1 if user == "agent" else 0, stdout="", stderr="")
        with self.assertRaises(IsolationError):
            asyncio.run_coroutine_threadsafe(prepare_sandbox(FailingProbe(), inspect_fixture()), self.loop).result(2)

    def test_literal_docker_none_needs_no_harbor_phase_policy(self):
        class PreparedEnvironment:
            async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
                if user == "root":
                    assert cwd == "/root"
                self.last_user = user
                return SimpleNamespace(return_code=0, stdout=json.dumps(
                    {"uid": 1000, "network": "none", "odoo_loopback": True}) if user == "agent" else "", stderr="")
        environment = PreparedEnvironment()  # Deliberately has no set_network_policy.
        sandbox = asyncio.run_coroutine_threadsafe(prepare_sandbox(environment, inspect_fixture()), self.loop).result(2)
        self.assertTrue(sandbox.ready)
        self.assertEqual(environment.last_user, "agent")

    def test_offline_package_staging_uses_generated_container_paths(self):
        class PackageEnvironment:
            def __init__(self):
                self.uploads = []
                self.commands = []
            async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
                assert cwd == "/root"
                self.commands.append((command, user))
                return SimpleNamespace(return_code=0, stdout="", stderr="")
            async def upload_file(self, source_path, target_path):
                self.uploads.append((source_path, target_path))
        environment = PackageEnvironment()
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"], prefix="deb-") as temporary:
            package = Path(temporary) / "fixture;not-a-command.deb"
            package.write_bytes(b"synthetic package: not actually installed")
            response = asyncio.run_coroutine_threadsafe(install_firewall_packages(
                environment, [package], package_root=temporary), self.loop).result(2)
            self.assertEqual(response["status"], "installed")
            self.assertTrue(environment.uploads[0][1].startswith("/root/rpnh-firewall-"))
            self.assertTrue(all(user == "root" for _, user in environment.commands))
            self.assertNotIn(package.name, environment.commands[-1][0])
            outside_root = Path(temporary) / "subdir"
            outside_root.mkdir()
            with self.assertRaises(ValueError):
                asyncio.run_coroutine_threadsafe(install_firewall_packages(
                    environment, [package], package_root=outside_root), self.loop).result(2)

    def test_unsafe_mount_hostnetwork_and_privilege_rejected(self):
        for field, value in (("NetworkMode", "host"), ("Privileged", True), ("PidMode", "host"),
                             ("CapAdd", ["SYS_ADMIN"])):
            document = inspect_fixture()
            document["HostConfig"][field] = value
            with self.assertRaises(IsolationError):
                validate_container_inspect(document)
        document = inspect_fixture()
        document["Mounts"] = [{"Destination": "/workspace", "Source": "/home/developer/repo", "Type": "bind"}]
        with self.assertRaises(IsolationError):
            validate_container_inspect(document)

    def test_exact_fresh_declared_odoo_anonymous_volumes_are_accepted(self):
        document = inspect_fixture()
        volumes = {"/var/lib/odoo": "a" * 64, "/mnt/extra-addons": "b" * 64}
        document["Config"] = {"Volumes": dict.fromkeys(volumes, {})}
        document["Mounts"] = [{"Type": "volume", "Destination": target, "Name": name, "Driver": "local"}
                              for target, name in volumes.items()]
        self.assertEqual(validate_container_inspect(document, allowed_anonymous_volumes=volumes), "container-fixture")
        class Environment:
            async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
                return SimpleNamespace(return_code=0, stderr="", stdout=json.dumps(
                    {"uid": 1000, "network": "none", "odoo_loopback": True}) if user == "agent" else "")
        prepared = asyncio.run_coroutine_threadsafe(prepare_sandbox(Environment(), document,
            allowed_anonymous_volumes=volumes), self.loop).result(1)
        self.assertTrue(prepared.ready)

    def test_old_other_world_named_and_undeclared_volumes_are_rejected(self):
        allowed = {"/var/lib/odoo": "a" * 64}
        for name, kind, declared in (("c" * 64, "volume", True), ("previous-world", "volume", True),
                                     ("a" * 64, "bind", True), ("a" * 64, "volume", False)):
            document = inspect_fixture()
            document["Config"] = {"Volumes": {"/var/lib/odoo": {}} if declared else {}}
            document["Mounts"] = [{"Type": kind, "Destination": "/var/lib/odoo", "Name": name,
                                   "Source": "/host/shared", "Driver": "local"}]
            with self.subTest(name=name, kind=kind, declared=declared), self.assertRaises(IsolationError):
                validate_container_inspect(document, allowed_anonymous_volumes=allowed)
        with self.assertRaises(IsolationError):
            validate_container_inspect(inspect_fixture(), allowed_anonymous_volumes={"/var/lib/odoo": "named-volume"})
        with self.assertRaises(IsolationError):
            validate_container_inspect(inspect_fixture(), allowed_anonymous_volumes=allowed)


if __name__ == "__main__":
    unittest.main()
