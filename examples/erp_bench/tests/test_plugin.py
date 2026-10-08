"""Plugin schema and direct transport checks, not native owner acceptance."""
import os
from pathlib import Path
import tempfile
import socket
import threading
import time
import unittest
from unittest.mock import patch

from cpn.plugins.api import PluginContext, PluginError, validate
from rpnh_erp_bench.bridge import Bridge, send_frame
from rpnh_erp_bench.plugin import (INPUT_SCHEMA, bindings, configuration, erp_python_handler,
                                   factory, validate_plan_handler)


def context(endpoint, *, cancelled=False):
    event = threading.Event()
    if cancelled:
        event.set()
    return PluginContext(config={"endpoint": str(endpoint), "trial_id": "trial"}, resources=(),
                         operation_id="operation", invocation_id="invocation", firing_id="firing",
                         call_id="call", cancel=event, deadline=time.monotonic() + 2)


class PluginTests(unittest.TestCase):
    def test_importable_factory_effects_and_bindings(self):
        definition = factory()
        self.assertEqual((definition.name, definition.version), ("erp_bench", "0.1.0"))
        operations = {op.name: op for op in definition.operations}
        self.assertEqual(operations["erp_python"].effect, "external_write")
        self.assertEqual(operations["validate_plan"].effect, "pure")
        self.assertIs(operations["erp_python"].handler, erp_python_handler)
        self.assertEqual(set(bindings()["executor"]["tools"]), {"erp_python", "validate_plan"})
        self.assertEqual(bindings()["executor"]["admitted_effects"], ["pure", "external_write"])
        self.assertEqual(configuration("/trusted/socket", "trial")["plugins"][0]["entry_point"], "erp_bench")

    def test_closed_schema_and_timeout(self):
        validate(INPUT_SCHEMA, {"source": "pass", "timeout_seconds": 3600})
        for arguments in ({"source": "pass", "filename": "../../host"}, {"source": "pass", "endpoint": "/other"},
                          {"source": "pass", "timeout_seconds": 3601}, {"source": "pass", "timeout_seconds": True},
                          {"source": "pass", "docker": "other"}, {"source": "pass", "import_path": "host.code"}):
            with self.assertRaises(PluginError):
                validate(INPUT_SCHEMA, arguments)

    def test_cancellation_before_dispatch(self):
        with self.assertRaises(PluginError):
            erp_python_handler(context("/unused", cancelled=True), {"source": "pass"})

    def test_pure_planning_operation_needs_no_bridge(self):
        plan = {"orders": [{"order_ref": "O", "quantity": 2, "list_price": 10, "budget": 20, "due_days": 2}],
                "routes": [{"route_ref": "R", "kind": "buy", "capacity": 2, "minimum_quantity": 1,
                            "unit_cost": 5, "lead_days": 1}],
                "allocations": [{"order_ref": "O", "route_ref": "R", "quantity": 2}], "minimum_margin": 0.2}
        answer = validate_plan_handler(context("/unused"), plan)
        self.assertEqual(answer["status"], "completed")
        self.assertEqual(answer["new_spend"], "10")

    def test_handler_routes_actual_native_context_identity(self):
        class Backend:
            def execute_python(self, source, timeout_seconds, cancellation_requested, identity):
                self.identity = identity
                return {"status": "completed", "stdout": "fixture", "stderr": "", "exit_code": 0,
                        "execution_id": "fixture-execution"}
        backend = Backend()
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"], prefix="p-") as temporary:
            endpoint = Path(temporary) / "b.sock"
            with Bridge(endpoint, "trial", backend):
                answer = erp_python_handler(context(endpoint), {"source": "print('fixture')"})
        self.assertEqual(answer["stdout"], "fixture")
        self.assertEqual(backend.identity, {"trial_id": "trial", "operation_id": "operation",
                                          "invocation_id": "invocation", "firing_id": "firing", "call_id": "call"})

    def test_lost_completed_reply_is_unknown_and_blocks_next_effect(self):
        class Backend:
            calls = 0
            def execute_python(self, source, timeout_seconds, cancellation_requested, identity):
                self.calls += 1
                return {"status": "completed", "stdout": "mutated", "stderr": "", "exit_code": 0,
                        "execution_id": "original-effect"}
        backend = Backend()
        dropped = False
        def drop_first_reply(connection, answer):
            nonlocal dropped
            if answer.get("status") == "completed" and not dropped:
                dropped = True
                connection.shutdown(socket.SHUT_RDWR)
            return send_frame(connection, answer)
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"], prefix="p-") as temporary:
            endpoint = Path(temporary) / "b.sock"
            with Bridge(endpoint, "trial", backend), patch("rpnh_erp_bench.bridge.send_frame", side_effect=drop_first_reply):
                with self.assertRaisesRegex(RuntimeError, "ERP execution outcome unknown; do not replay"):
                    erp_python_handler(context(endpoint), {"source": "mutate()"})
                following = context(endpoint)
                following.call_id = "next-call"
                self.assertEqual(erp_python_handler(following, {"source": "dependent_mutation()"})["status"], "interrupted")
                cached = erp_python_handler(context(endpoint), {"source": "mutate()"})
                self.assertEqual(cached["execution_id"], "original-effect")
                self.assertEqual(backend.calls, 1)


if __name__ == "__main__":
    unittest.main()
