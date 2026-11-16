"""Actual Registry/Owner checks for selected-terminal result delivery.

The tiny source is a registered one-step Module with an unused real tool
binding. It copies the application's actual executor identities/contracts
and exporter HOST declaration. The executor callable is an explicit unused
test stub: the fixture drives original Owner publication and Success itself.
Its empty delivery exports no invented business receipt. The parent helper,
run reader, terminal publication and input bytes are real. This verifies the
parent boundary, not the application's exporter execution or business tools.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpn.plugins.managed_output import (
    MANAGED_OPERATION_RESULTS_SCHEMA, MANAGED_OPERATION_RESULTS_DOCUMENT,
    MANAGED_OPERATION_RESULTS_EXPORT_TOOL,
)
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    PNFragment, PlaceDeclaration, TransitionDeclaration, ArcDeclaration,
    PortBinding, PortDeclaration,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry, _parse_ref
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.run_authority import read_run_execution, read_run_terminal_bytes
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run


_INPUT = "application/test_dsh_result_delivery_input/v1"
_OUTPUT = "application/test_dsh_result_delivery_output/v1"
_CONFIG = "application/test_dsh_result_delivery_config/v1"
_COMPONENT = "tests/dsh-result-delivery-component/v1"
_TERMINAL_EXECUTOR = "rpnh/dsh-managed-terminal/v1"
_TOOL_EXECUTOR = "rpnh/dsh-managed-results/v2"
_LEGACY_EXECUTOR = "tests/dsh-result-delivery-legacy/v1"
_TERMINAL = "tests/dsh-result-delivery-terminal/v1"


def _unused_executor(**kwargs):
    raise AssertionError("the fixture drives the original Owner, without an executor substitute")


def _lower(config, context):
    places = [PlaceDeclaration("request", _INPUT),
              PlaceDeclaration("tool_input", _INPUT),
              PlaceDeclaration("tool_result", MANAGED_OPERATION_RESULTS_SCHEMA),
              PlaceDeclaration("done", config["terminal_schema"])]
    transitions = [TransitionDeclaration("finalize", "finalize"),
                   TransitionDeclaration("tool", "tool")]
    arcs = [ArcDeclaration("request", "finalize", "input"),
            ArcDeclaration("done", "finalize", "output", mode="produce", outcome="complete"),
            ArcDeclaration("tool_input", "tool", "input"),
            ArcDeclaration("tool_result", "tool", "output", mode="produce", outcome="complete")]
    internal_ports = [PortDeclaration("tool_decision_input", "input", _INPUT),
                      PortDeclaration("managed_results_output", "output", MANAGED_OPERATION_RESULTS_SCHEMA)]
    internal_bindings = [PortBinding("tool_decision_input", "tool_input"),
                         PortBinding("managed_results_output", "tool_result")]
    if config["governed"]:
        from cpn.rpnh.material_availability import EFFECT_SCHEMA
        places.append(PlaceDeclaration("availability_effect", EFFECT_SCHEMA))
        transitions.append(TransitionDeclaration("disable", "disable"))
        arcs.append(ArcDeclaration("availability_effect", "disable", "output",
                                   mode="produce", outcome="complete"))
        internal_ports.append(PortDeclaration("availability_output", "output", EFFECT_SCHEMA))
        internal_bindings.append(PortBinding("availability_output", "availability_effect"))
    return PNFragment(places=tuple(places), transitions=tuple(transitions), arcs=tuple(arcs),
        operations=context.operations,
        ports=(PortBinding("request", "request"), PortBinding("done", "done")),
        internal_ports=tuple(internal_ports), internal_bindings=tuple(internal_bindings))


def _registration(*, governed=False, terminal_text=False, wrong_identity=None):
    from cpn.dsh.backend import (
        registration, CONFIG_RESULTS_V2, CONFIG_MANAGED_TERMINAL,
    )
    original = registration(executor=_unused_executor,
        managed_result_reader=True, managed_result_delivery=True)
    reg = Registration()
    for schema in (_INPUT, _OUTPUT, _CONFIG):
        reg.register_schema(schema, {"$id": schema,
            "$schema": "http://json-schema.org/draft-07/schema#", "type": "object"})
    for schema in (MANAGED_OPERATION_RESULTS_SCHEMA, CONFIG_RESULTS_V2,
                   CONFIG_MANAGED_TERMINAL):
        reg.register_schema(schema, original.declaration("schema", schema)["schema"])
    if terminal_text:
        from cpn.rpnh.agent_tasks import TEXT_SCHEMA
        reg.register_schema(TEXT_SCHEMA, {"$id": TEXT_SCHEMA,
            "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    reg.register_component(_COMPONENT, _lower,
        identity={"implementation_id": "tests.dsh_delivery_component", "revision": "v1"},
        contracts={"config_schema": _CONFIG})
    for scope, key in (("terminal", _TERMINAL_EXECUTOR), ("source", _TOOL_EXECUTOR)):
        declaration = original.declaration("executor", key)
        identity = declaration["identity"]
        if wrong_identity == scope:
            identity = {**identity, "implementation_id": "tests.wrong_dsh_implementation"}
        reg.register_executor(key, _unused_executor, identity=identity,
            contracts=declaration["contracts"])
    reg.register_executor(_LEGACY_EXECUTOR, _unused_executor,
        identity={"implementation_id": "tests.dsh_delivery_legacy", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None})
    exporter = original.declaration("tool", MANAGED_OPERATION_RESULTS_EXPORT_TOOL)
    reg.register_tool(MANAGED_OPERATION_RESULTS_EXPORT_TOOL,
        original.resolve("tool", MANAGED_OPERATION_RESULTS_EXPORT_TOOL),
        identity=exporter["identity"], contracts=exporter["contracts"])
    reg.register_tool(_TERMINAL, dict,
        identity={"implementation_id": "tests.dsh_delivery_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    if governed:
        from cpn.rpnh.material_availability import register_material_availability
        register_material_availability(reg)
    return reg


def _module(*, governed=False, legacy=False, terminal_text=False, missing_exporter=False):
    from cpn.rpnh.agent_tasks import TEXT_SCHEMA
    from cpn.dsh.backend import (
        CONFIG_RESULTS_V2, CONFIG_MANAGED_TERMINAL, MANAGED_RESULT_IMPORT_SYMBOL,
    )
    terminal_schema = TEXT_SCHEMA if terminal_text else _OUTPUT
    bucket = {"bucket_id": "delivery", "budget_scope": "module", "finalization_scope": None}
    operations = [
        {"name": "finalize", "executor": _LEGACY_EXECUTOR if legacy else _TERMINAL_EXECUTOR,
         "inputs": ["request"], "outputs": ["done"], "request_port": None,
         "tools": [] if legacy or missing_exporter else [MANAGED_OPERATION_RESULTS_EXPORT_TOOL],
         "config": {} if legacy else {"managed_result_export_source": "dsh.tool"},
         "budget_binding": bucket,
         "outcomes": [{"name": "complete", "products": [{"port": "done"}]}]},
        {"name": "tool", "executor": _TOOL_EXECUTOR,
         "inputs": ["tool_decision_input"], "outputs": ["managed_results_output"],
         "request_port": None, "tools": [], "config": {
             "managed_result_reader": True, "context_tool_output_byte_limit": 1024,
             "managed_result_import_symbol": MANAGED_RESULT_IMPORT_SYMBOL}, "budget_binding": bucket,
         "outcomes": [{"name": "complete", "products": [{"port": "managed_results_output"}]}]},
    ]
    schemas = [_INPUT, _OUTPUT, _CONFIG, MANAGED_OPERATION_RESULTS_SCHEMA,
               CONFIG_RESULTS_V2, CONFIG_MANAGED_TERMINAL]
    if terminal_text:
        schemas.append(TEXT_SCHEMA)
    if governed:
        from cpn.rpnh.material_availability import EXECUTOR, CONFIG_SCHEMA, PLAN_SCHEMA, EFFECT_SCHEMA
        schemas.extend((CONFIG_SCHEMA, PLAN_SCHEMA, EFFECT_SCHEMA))
        operations.append({"name": "disable", "executor": EXECUTOR, "inputs": [],
            "outputs": ["availability_output"], "request_port": None, "tools": [],
            "config": {"outcome_id": "complete", "output_port_id": "dsh.availability_output"},
            "budget_binding": bucket,
            "outcomes": [{"name": "complete", "products": [{"port": "availability_output"}]}]})
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "DshResultDeliveryProof",
        "components": [{"name": "dsh", "key": _COMPONENT, "config_schema": _CONFIG,
            "config": {"governed": governed, "terminal_schema": terminal_schema},
            "ports": [{"name": "request", "direction": "input", "schema": _INPUT},
                      {"name": "done", "direction": "output", "schema": terminal_schema}],
            "operations": operations}], "links": [],
        "entry": {"request": {"component": "dsh", "port": "request"}},
        "exit": {"done": {"component": "dsh", "port": "done"}},
        "terminal": {"key": _TERMINAL, "source": {"component": "dsh", "port": "done"},
            "operation": "finalize", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": schemas, "budgets": {},
        "budget_buckets": [{**bucket, "max_attempts": 5}],
    })


@dataclass
class _Source:
    owner: object
    execution: object
    attachment: object
    product: object
    delivery: dict
    output: object
    receipt: object = None
    committed: object = None


class _ParentFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        self.core = _RegistryCore(self.root / "main", create=True)
        self.thread = MainThreadRegistry(self.core, session_root=self.root,
            initialize_path_base=MainThreadRegistry.REGISTRY_ROOT_PATH_BASE)
        self.thread.create_thread(idempotency_key="delivery-parent")
        self.sources = []

    def source(self, *, governed=False, legacy=False, terminal_text=False,
               mutate_delivery=None, answer=None, commit=True,
               wrong_identity=None, missing_exporter=False):
        ordinal = len(self.sources)
        state = self.thread.project_current_thread()
        advance = self.thread.accept_turn(thread_ref=_parse_ref(state["thread_ref"]),
            user_input={"request_id": f"source-{ordinal}"}, idempotency_key=f"accept-{ordinal}")
        attachment = self.thread.attach_attempt(thread_ref=advance.thread_ref,
            turn_ref=advance.turn_ref, idempotency_key=f"attach-{ordinal}")
        module = _module(governed=governed, legacy=legacy, terminal_text=terminal_text,
                         missing_exporter=missing_exporter)
        inp = OwnerInput(_INPUT, canonical_json({"seed": ordinal}), "actual selected-terminal proof")
        extra = {}
        if governed:
            from cpn.rpnh.material_availability import availability_host_bindings
            extra["host_execution_bindings"] = availability_host_bindings
        if terminal_text:
            from cpn.rpnh.collaboration.project_native import catalog
            extra["catalog"] = catalog()
        owner = start_run(module, _registration(governed=governed, terminal_text=terminal_text,
                                               wrong_identity=wrong_identity),
            run_dir=attachment.attempt_path, task_input=inp, entry_inputs={"request": inp},
            budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
                ("rpnh/module_declaration/v1",), 5, 0, 5, 0),
            model_condition="no-model", owner_statement="offline selected terminal fixture",
            command_id="delivery-source:fresh", **extra)
        execution = owner.start(owner.admit("dsh.finalize", logical_tau=0,
            command_id="delivery-source:admit"), command_id="delivery-source:start")
        delivery = {
            "schema_version": "managed_operation_result_delivery/v1",
            "source_task_ref": _ref_payload(owner.identity.task_ref),
            "source_run_ref": json.loads(owner._core.event_store.get_meta("native_run_ref")),
            "producer_execution_ref": _ref_payload(execution.operation_execution_lease_ref),
            "producer_firing_ref": _ref_payload(execution.operation.firing.transition_firing_ref),
            "source_operation_binding_ref": _ref_payload(owner.publication.binding_refs["dsh.tool"]),
            "products": [],
        }
        if mutate_delivery is not None:
            mutate_delivery(delivery, owner, execution)
        output = ("ordinary task text for exact disable-policy proof" if terminal_text else
                  {"managed_result_delivery": delivery, "selected_marker": "original"})
        outputs = owner.products(execution, outcome_id="complete",
            products={"dsh.done": (canonical_json(output),)}, command_id="delivery-source:products")
        product = outputs.outputs[0].resource_ref
        owner.succeed(outputs, command_id="delivery-source:success")
        if not terminal_text and owner.terminal() is None:
            raise AssertionError("actual single-step source did not reach original terminal")
        source = _Source(owner, execution, attachment, product, delivery, output)
        self.sources.append(source)
        if commit:
            self.commit(source, answer=output if answer is None else answer)
        return source

    def commit(self, source, *, answer):
        source.receipt = self.thread.record_execution_receipt(
            turn_ref=source.attachment.turn_ref, idempotency_key="source-receipt")
        source.committed = self.thread.commit_terminal_answer(
            thread_ref=source.attachment.thread_ref, turn_ref=source.attachment.turn_ref,
            receipt_ref=source.receipt, answer=answer, idempotency_key="source-answer")


class DshResultDeliveryOfflineTests(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("RPNH_RESULT_DELIVERY_EVIDENCE")
        self.temporary = None if base else tempfile.TemporaryDirectory(prefix="rpnh-delivery-")
        self.addCleanup(self.temporary.cleanup) if self.temporary else None
        self.root = Path(base or self.temporary.name) / self._testMethodName
        self.root.mkdir(parents=True)

    def _collect(self, parent, **kwargs):
        from cpn.dsh.result_delivery import collect_result_deliveries
        return collect_result_deliveries(parent.thread, **kwargs)

    def test_selected_product_is_authority_instead_of_parent_answer(self):
        from cpn.dsh.result_delivery import (
            is_managed_result_terminal, ManagedResultDeliveryError,
        )
        parent = _ParentFixture(self.root / "parent")
        source = parent.source(answer={"managed_result_delivery": {"forged": True},
                                       "selected_marker": "forged-parent-answer"})
        child_before = source.owner._core.event_store.max_ordinal()
        parent_before = parent.core.event_store.max_ordinal()
        result = self._collect(parent)
        self.assertEqual(result["schema_version"], "managed_operation_result_import/v1")
        self.assertEqual(len(result["deliveries"]), 1)
        row, = result["deliveries"]
        self.assertEqual(row["delivery"], source.delivery)
        self.assertEqual(row["main_turn_ref"], _ref_payload(source.committed.turn_ref))
        self.assertEqual(row["execution_receipt_ref"], _ref_payload(source.receipt))
        self.assertTrue(is_managed_result_terminal(parent.thread,
            turn_ref=source.attachment.turn_ref, receipt_ref=source.receipt))
        with self.assertRaisesRegex(ManagedResultDeliveryError, "running turn and receipt"):
            is_managed_result_terminal(parent.thread,
                turn_ref=source.committed.turn_ref, receipt_ref=source.receipt)
        with self.assertRaisesRegex(ManagedResultDeliveryError,
                                    "requires exact main_turn_execution_receipt/v1"):
            is_managed_result_terminal(parent.thread,
                turn_ref=source.attachment.turn_ref, receipt_ref=source.committed.turn_ref)
        self.assertEqual(source.owner._core.event_store.max_ordinal(), child_before)
        self.assertEqual(parent.core.event_store.max_ordinal(), parent_before)
        (self.root / "proof.json").write_text(json.dumps({
            "source": "actual selected terminal bytes", "parent_answer_forged": True,
            "original_selected_delivery_retained": True, "source_and_parent_heads_unchanged": True,
            "selector": {"original_running_turn_true": True,
                         "committed_instead_of_running_turn_rejected": True,
                         "wrong_receipt_type_rejected": True},
            "business_receipts": 0, "model_requests": 0,
        }, indent=2))

    def test_parent_scope_and_source_head_are_exact(self):
        from cpn.dsh.result_delivery import ManagedResultDeliveryError
        parent = _ParentFixture(self.root / "parent")
        source = parent.source()
        foreign = _ParentFixture(self.root / "foreign")
        foreign_ref = _parse_ref(foreign.thread.project_current_thread()["thread_ref"])
        with self.assertRaisesRegex(ManagedResultDeliveryError, "expected thread version"):
            self._collect(parent, expected_thread_ref=foreign_ref)
        with self.assertRaisesRegex(ManagedResultDeliveryError, "expected thread version"):
            self._collect(parent, expected_thread_ref=source.attachment.thread_ref)
        state = foreign.thread.project_current_thread()
        foreign.thread.accept_turn(thread_ref=_parse_ref(state["thread_ref"]),
            user_input={"request_id": "active"}, idempotency_key="active-turn")
        with self.assertRaisesRegex(ManagedResultDeliveryError, "precede accepting"):
            self._collect(foreign)
        source.owner.control.publish_input({"schema_id": _INPUT, "value": {"seed": 99},
            "summary": "actual later source fact"}, command_id="later-source-fact")
        with self.assertRaisesRegex(ManagedResultDeliveryError, "recorded terminal receipt"):
            self._collect(parent)
        (self.root / "proof.json").write_text(json.dumps({
            "rejected": ["another actual parent thread", "old active parent version",
                         "accepted uncommitted parent turn", "original source advanced after receipt"],
            "source_mutation": "actual OwnerControl.publish_input, without rewriting old facts",
            "business_receipts": 0, "model_requests": 0,
        }, indent=2))

    def test_governed_and_missing_selected_body_do_not_fall_back(self):
        from cpn.rpnh.material_availability import source_governs_materials
        from cpn.rpnh.registry.errors import ResourceIntegrityFault
        from cpn.rpnh.registry.object_store import ObjectIntegrityError
        governed = _ParentFixture(self.root / "governed")
        source = governed.source(governed=True)
        self.assertTrue(source_governs_materials(source.owner._core))
        with self.assertRaisesRegex(ResourceIntegrityFault, "GOVERNED_DELIVERY_REQUIRED"):
            self._collect(governed)
        parent = _ParentFixture(self.root / "missing")
        source = parent.source()
        path = source.owner._core.object_store.path_for_version(source.product.resource_version_id)
        original = path.read_bytes()
        path.unlink()
        try:
            with self.assertRaises((ObjectIntegrityError, ResourceIntegrityFault)) as denied:
                self._collect(parent)
            missing_error = type(denied.exception).__name__
        finally:
            path.write_bytes(original)
        (self.root / "proof.json").write_text(json.dumps({
            "governed_source": "actual adopted registered availability executor",
            "governed_rejection": "GOVERNED_DELIVERY_REQUIRED",
            "missing_body_rejection": missing_error,
            "missing_body_fault": "only the isolated test source payload was removed, then restored",
            "business_receipts": 0, "model_requests": 0,
        }, indent=2))

    def test_legacy_selected_bytes_are_not_retroactive_delivery(self):
        from cpn.dsh.result_delivery import is_managed_result_terminal
        parent = _ParentFixture(self.root / "parent")
        source = parent.source(legacy=True)
        path = source.owner._core.object_store.path_for_version(source.product.resource_version_id)
        original = path.read_bytes()
        path.unlink()
        try:
            result = self._collect(parent)
            self.assertFalse(is_managed_result_terminal(parent.thread,
                turn_ref=source.attachment.turn_ref, receipt_ref=source.receipt))
        finally:
            path.write_bytes(original)
        self.assertEqual(result["deliveries"], [])
        (self.root / "proof.json").write_text(json.dumps({
            "legacy_parent_answer_and_selected_json_contain_export_field": True,
            "legacy_executor_not_upgraded": True,
            "selector_legacy_false_without_body_read": True,
            "legacy_selected_body_was_physically_unavailable_during_collection": True,
            "deliveries": 0, "body_restored": True,
        }, indent=2))

    def test_original_host_identity_and_declared_exporter_are_required(self):
        from cpn.dsh.result_delivery import (
            ManagedResultDeliveryError, is_managed_result_terminal,
        )
        failures = {}
        for name, options in (
            ("terminal_identity", {"wrong_identity": "terminal"}),
            ("source_identity", {"wrong_identity": "source"}),
            ("missing_exporter", {"missing_exporter": True}),
        ):
            with self.subTest(declaration=name):
                parent = _ParentFixture(self.root / name)
                source = parent.source(**options)
                with self.assertRaisesRegex(ManagedResultDeliveryError,
                                            "original HOST") as denied:
                    self._collect(parent)
                failures[name] = str(denied.exception)
                if name != "source_identity":
                    with self.assertRaisesRegex(ManagedResultDeliveryError,
                                                "original HOST"):
                        is_managed_result_terminal(parent.thread,
                            turn_ref=source.attachment.turn_ref, receipt_ref=source.receipt)
        (self.root / "proof.json").write_text(json.dumps({
            "declaration_counterexamples": failures,
            "declaration_source": "actual backend.registration identities, contracts and exporter HOST declaration",
            "executor_callable": "explicit unused test stub; original Owner drives publication and Success",
            "source_execution": "empty delivery; no business tools or application exporter executed",
        }, indent=2))

    def test_real_source_and_parent_cut_changes_reject_after_read(self):
        from cpn.dsh import result_delivery
        from cpn.rpnh.registry.errors import ResourceIntegrityFault
        failures = {}
        original_read = result_delivery.read_run_terminal_bytes
        for kind in ("source", "parent"):
            with self.subTest(changed_cut=kind):
                parent = _ParentFixture(self.root / kind)
                source = parent.source()
                calls = []

                def change_after_exact_read(core, read, **kwargs):
                    value = original_read(core, read, **kwargs)
                    calls.append("actual original selected-body reader completed")
                    if kind == "source":
                        source.owner.control.publish_input({"schema_id": _INPUT,
                            "value": {"seed": 7}, "summary": "source read-cut counterexample"},
                            command_id="after-source-read")
                    else:
                        state = parent.thread.project_current_thread()
                        parent.thread.accept_turn(thread_ref=_parse_ref(state["thread_ref"]),
                            user_input={"request_id": "after-read"}, idempotency_key="after-parent-read")
                    return value

                with patch.object(result_delivery, "read_run_terminal_bytes", change_after_exact_read):
                    with self.assertRaises((ResourceIntegrityFault,
                                            result_delivery.ManagedResultDeliveryError)) as denied:
                        self._collect(parent)
                self.assertEqual(len(calls), 1)
                failures[kind] = {"error_type": type(denied.exception).__name__,
                                  "error": str(denied.exception), "original_body_reads": len(calls)}
        (self.root / "proof.json").write_text(json.dumps({
            "cut_changes": failures,
            "injection": "wrapper delegates the real reader, then appends an actual original Registry fact",
            "business_receipts": 0, "model_requests": 0,
        }, indent=2))

    def test_delivery_headers_match_actual_selected_producer(self):
        from cpn.dsh.result_delivery import ManagedResultDeliveryError
        failures = {}
        def foreign_task(document, owner, execution):
            document["source_task_ref"] = {**document["source_task_ref"],
                "logical_id": "task:" + "0" * 32}
        def foreign_execution(document, owner, execution):
            document["producer_execution_ref"] = {**document["producer_execution_ref"],
                "version_id": "operation_execution_lease_version:" + "0" * 32}
        def wrong_binding(document, owner, execution):
            document["source_operation_binding_ref"] = _ref_payload(owner.publication.binding_refs["dsh.finalize"])
        for name, mutate in (("task", foreign_task), ("execution", foreign_execution),
                             ("non_tool_binding", wrong_binding)):
            with self.subTest(header=name):
                parent = _ParentFixture(self.root / name)
                parent.source(mutate_delivery=mutate)
                with self.assertRaises(ManagedResultDeliveryError) as denied:
                    self._collect(parent)
                failures[name] = str(denied.exception)
        (self.root / "proof.json").write_text(json.dumps({
            "rejected_headers": failures,
            "fault": "incorrect delivery header published by the test HOST through actual selected terminal Success",
            "business_receipts": 0, "model_requests": 0,
        }, indent=2))

    def test_original_disabled_terminal_body_is_rejected(self):
        """Use the original text-only disable contract, not a forged status.

        DSH JSON envelopes are not permitted targets of that first contract.
        This negative uses a real ordinary text terminal under the export
        executor so the original body gate must run before export parsing.
        """
        from cpn.rpnh.collaboration.user_collaboration import UserCollaborationHost
        from cpn.rpnh.material_availability import (
            PLAN_PROTOCOL, run_availability_operation,
        )
        from cpn.rpnh.material_body_policy import resolve_material_body_availability
        from cpn.rpnh.registry._event_store.material_control import COMMAND_PROTOCOL
        from cpn.rpnh.registry.event_store import RegistryConflict

        parent = _ParentFixture(self.root / "parent")
        source = parent.source(governed=True, terminal_text=True, commit=False)
        owner = source.owner
        owner.schema_gateway.bind_source_identity(
            source_id="delivery-disable-source", command_id="disable-source-identity")
        host = UserCollaborationHost(owner._core, owner.schema_gateway)
        session = host.create_session(origin_core=owner._core,
            principal_ref=owner.principal_ref, command_id="disable-owner-session")
        params = {"protocol": PLAN_PROTOCOL,
                  "target_resource_refs": [_ref_payload(source.product.as_version_ref())]}
        plan = owner.command("prepare_material_disable_plan", params,
            command_id="disable-plan", collaboration_action=host.material_action(session,
                owner_command="prepare_material_disable_plan", command_id="disable-plan", params=params))
        params = {"protocol": COMMAND_PROTOCOL, "operation": "disable_versions",
            "target": "dsh.disable", "execution": {
                "task_ref": _ref_payload(owner.identity.task_ref),
                "task_round_ref": _ref_payload(owner.task_round_ref),
                "net_instance_ref": _ref_payload(owner.publication.net_ref)},
            "confirmed_plan_ref": plan["plan_ref"]}
        submitted = owner.command("material_submit", params,
            command_id="disable-command", collaboration_action=host.material_action(session,
                owner_command="material_submit", command_id="disable-command", params=params))
        execution = owner.start(owner.admit("dsh.disable", logical_tau=1,
            command_id="disable-admit", owner_command_ref=_parse_ref(submitted["command_ref"])),
            command_id="disable-start")
        outputs = run_availability_operation(owner, execution)
        owner.succeed(outputs, command_id="disable-success")
        availability = resolve_material_body_availability(owner._core, source.product)
        self.assertEqual(availability["availability"], "disabled")
        self.assertIsNotNone(owner.terminal())
        parent.commit(source, answer={"not_authority": "parent does not expose disabled bytes"})
        source_read = read_run_execution(owner._core, _ResourceServiceKernel(owner._core))
        with self.assertRaisesRegex(RegistryConflict, "MATERIAL_DISABLED") as original_denied:
            read_run_terminal_bytes(owner._core, source_read)
        # The parent uses the original base read-only Core. Optional source
        # catalog evidence can be unresolved there, which must also fail
        # closed. Do not relabel that result as a resolved DISABLED decision.
        with self.assertRaisesRegex(RegistryConflict, "MATERIAL_(DISABLED|UNKNOWN)") as denied:
            self._collect(parent)
        (self.root / "proof.json").write_text(json.dumps({
            "availability": availability,
            "original_source_reader_error": str(original_denied.exception),
            "helper_error": str(denied.exception),
            "helper_base_catalog": "unknown evidence is rejected; no optional-catalog substitution or raw-body fallback",
            "original_operations": ["create_session", "prepare_material_disable_plan",
                "material_submit", "original registered availability operation", "Owner Success"],
            "scope": "original ordinary-text terminal; DSH envelope disable is unsupported by the existing target contract",
            "business_receipts": 0, "model_requests": 0,
        }, indent=2))


if __name__ == "__main__":
    unittest.main()

