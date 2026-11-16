"""Skill publication and active-firing reads on original Registry/PN authorities.

No parser or pytest substitute is used. Literal index documents keep these
publication/read tests independent of parsing. Actual SKILL.md parsing is covered
by test_skill_strictyaml_active_delivery. Every world contains real Registration,
Module, Owner, Start and Success.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.agent_workflows import TEXT_SCHEMA
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef
from cpn.rpnh.collaboration.schema_catalog import source_identity_schema_data
from cpn.rpnh.material_types import encode_agent_text
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.errors import ResourceIntegrityFault, ResourceServiceError
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.resources import SettledPetriInputArtifact
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from cpn.rpnh import skill_materials as skill

SOURCE = "skill-native-test-source"
RAW = "---\nname: read-guide\ndescription: Read this exact guide.\n---\n# Guide\n"
EXECUTOR = "tests/skill-material-reader/v1"
TERMINAL = "tests/skill-material-terminal/v1"


def unused_executor(**_kwargs):
    raise AssertionError("focused tests drive original Owner methods")


def world(root, *, request=None):
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT_SCHEMA, {
        "$id": TEXT_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "string",
    })
    skill.register_skill_schema(registration)
    registration.register_executor(EXECUTOR, unused_executor,
        identity={"implementation_id": "tests.skill_material_reader", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "tests.skill_material_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})

    def component(name, input_schema, output_schema):
        return {"name": name, "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
            "config": {}, "ports": [
                {"name": "request", "direction": "input", "schema": input_schema},
                {"name": "result", "direction": "output", "schema": output_schema}],
            "operations": [{"name": "run", "executor": EXECUTOR,
                "inputs": ["request"], "outputs": ["result"], "request_port": None,
                "tools": [], "config": {},
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
                "budget_binding": {"bucket_id": name, "budget_scope": name,
                                   "finalization_scope": None}}]}

    doc = {"schema_version": "rpnh/module_declaration/v1", "name": "SkillMaterials",
        "components": [component("publisher", TEXT_SCHEMA, skill.SKILL_INDEX_SCHEMA),
                       component("reader", skill.SKILL_INDEX_SCHEMA, TEXT_SCHEMA)],
        "links": [{"source": {"component": "publisher", "port": "result"},
                   "target": {"component": "reader", "port": "request"}}],
        "entry": {"request": {"component": "publisher", "port": "request"}},
        "exit": {"result": {"component": "reader", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "reader", "port": "result"},
                     "operation": "run", "outcome": "complete",
                     "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT_SCHEMA, skill.SKILL_INDEX_SCHEMA],
        "budgets": {},
        "budget_buckets": [
            {"bucket_id": name, "budget_scope": name, "finalization_scope": None,
             "max_attempts": 1} for name in ("publisher", "reader")]}
    module = ModuleDeclaration.from_dict(doc)
    schemas, types, paths = source_identity_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    if request is None:
        request = OwnerInput(TEXT_SCHEMA, encode_agent_text(RAW), "Skill source text")
    owner = start_run(module, registration, run_dir=root / "run",
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 2, 0, 2, 0),
        model_condition="no-model", owner_statement="Skill integration fixture",
        command_id="skill:run", catalog=catalog)
    owner.schema_gateway.bind_source_identity(source_id=SOURCE, command_id="skill:source")
    admitted = owner.admit("publisher.run", logical_tau=0, command_id="skill:publisher:admit")
    execution = owner.start(admitted, command_id="skill:publisher:start")
    member_ref = SourceQualifiedResourceRef(SOURCE, execution.operation.inputs[0].resource_ref)
    index = {"schema_version": skill.SKILL_INDEX_SCHEMA,
             "metadata": {"name": "read-guide", "description": "Read this exact guide."},
             "members": {"SKILL.md": member_ref.to_dict(),
                         "references/details.md": member_ref.to_dict(),
                         "scripts/reference.py": member_ref.to_dict()}}
    return owner, execution, member_ref, index


def publish(owner, execution, index):
    binding, = execution.operation.operation_binding.output_port_bindings
    return skill.publish_skill_index(owner, execution, index,
        output_port_id=binding.port_id, outcome_id="complete",
        command_id="skill:publish")


def consumer(owner, outputs):
    owner.succeed(outputs, command_id="skill:publisher:success")
    admitted = owner.admit("reader.run", logical_tau=1, command_id="skill:reader:admit")
    return owner.start(admitted, command_id="skill:reader:start")


def grant_member(owner, execution, member_ref, key="skill:member"):
    context = execution.operation.canonical.context
    logical = owner._core.capabilities.issue(
        target_invocation_id=context.invocation_ref.entity_id,
        operation_binding_ref=context.operation_binding_ref,
        resource_version_ids=(member_ref.ref.resource_version_id,),
        revocation_condition="invocation closes", idempotency_key=key + ":issue")
    row = owner._core._logical_object(logical, "capability_grant/v1")
    ref = VersionRef("capability_grant/v1", logical,
                     TypedId.parse(row["version_id"], expected="grant"))
    owner._core.capabilities.transition(logical, "activate", payload={},
                                       idempotency_key=key + ":activate")
    owner._core.capabilities.transition(logical, "allow", payload={},
                                       idempotency_key=key + ":allow")
    return logical, ref


def counts(owner):
    with owner._core.event_store.connect() as db:
        return tuple(db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                     for table in ("objects", "events", "transactions", "relations"))


class SkillNativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def world(self):
        return world(Path(self.temporary.name))

    def test_exact_original_product_is_private_until_success_then_reused_as_input(self):
        owner, execution, member_ref, index = self.world()
        outputs = publish(owner, execution, index)
        self.assertEqual(len(outputs.outputs), 1)
        before_replay = counts(owner)
        replayed = publish(owner, execution, index)
        self.assertEqual(replayed.outputs, outputs.outputs)
        self.assertEqual(counts(owner), before_replay)
        index_ref = outputs.outputs[0].resource_ref
        core = owner._core
        canonical = core.event_store.canonical_view()
        self.assertIsNone(core.event_store.object_row_for_view(canonical, index_ref.resource_version_id))
        active = consumer(owner, outputs)
        canonical = core.event_store.canonical_view()
        self.assertIsNotNone(core.event_store.object_row_for_view(canonical, index_ref.resource_version_id))
        self.assertEqual(active.operation.inputs[0].resource_ref, index_ref)
        before = counts(owner)
        read = skill.read_firing_skill_index(owner, active,
            SourceQualifiedResourceRef(SOURCE, index_ref), command_id="skill:index:read")
        self.assertEqual(read["index"], index)
        self.assertEqual(read["receipt"], active.operation.inputs[0].artifact.receipt)
        self.assertEqual(counts(owner), before)

    def test_index_is_not_member_authority_and_original_grant_can_be_revoked(self):
        owner, execution, member_ref, index = self.world()
        outputs = publish(owner, execution, index)
        active = consumer(owner, outputs)
        index_ref = SourceQualifiedResourceRef(SOURCE, outputs.outputs[0].resource_ref)
        before = counts(owner)
        with self.assertRaises(ResourceServiceError):
            skill.read_firing_skill_member(owner, active, index_ref,
                "references/details.md", command_id="skill:denied")
        self.assertEqual(counts(owner), before)
        logical, grant = grant_member(owner, active, member_ref)
        read = skill.read_firing_skill_member(owner, active, index_ref,
            "references/details.md", member_authorization_ref=grant, command_id="skill:allowed")
        self.assertEqual(read["text"], RAW)
        delivery = owner._core.get_version(read["receipt"].delivery_ref.version_id)
        self.assertEqual(delivery.metadata["resource_ref"], member_ref.to_dict()["ref"])
        self.assertEqual(read["receipt"].outcome, "acknowledged")
        self.assertIsNotNone(read["receipt"].observed_read_event_id)
        owner._core.capabilities.transition(logical, "revoke", payload={},
                                            idempotency_key="skill:member:revoke")
        before = counts(owner)
        with self.assertRaises(ResourceServiceError):
            skill.read_firing_skill_member(owner, active, index_ref,
                "references/details.md", member_authorization_ref=grant,
                command_id="skill:after-revoke")
        self.assertEqual(counts(owner), before)

    def test_publication_rejects_unadmitted_member_and_different_source(self):
        owner, execution, member_ref, index = self.world()
        binding, = execution.operation.operation_binding.output_port_bindings
        for change in ("foreign-source", "unadmitted"):
            candidate = json.loads(json.dumps(index))
            if change == "foreign-source":
                for value in candidate["members"].values():
                    value["source_id"] = "other-source"
            else:
                candidate["members"]["references/details.md"]["ref"] = {
                    "resource_id": str(new_id("resource")),
                    "resource_version_id": str(new_id("resource_version"))}
            before = counts(owner)
            with self.assertRaises(skill.SkillMaterialError):
                skill.prepare_index_publication(owner, execution, candidate,
                    output_port_id=binding.port_id, outcome_id="complete",
                    idempotency_key="skill:reject:" + change)
            self.assertEqual(counts(owner), before)

    def test_wrong_start_and_foreign_source_cannot_read_active_skill_index(self):
        owner, execution, _member_ref, index = self.world()
        outputs = publish(owner, execution, index)
        active = consumer(owner, outputs)
        before = counts(owner)
        forged = replace(active, start_event_id=new_id("event"))
        with self.assertRaises(ResourceIntegrityFault):
            skill.read_firing_skill_index(owner, forged,
                SourceQualifiedResourceRef(SOURCE, outputs.outputs[0].resource_ref),
                command_id="skill:wrong-start")
        with self.assertRaises(skill.SkillMaterialError):
            skill.read_firing_skill_index(owner, active,
                SourceQualifiedResourceRef("other-source", outputs.outputs[0].resource_ref),
                command_id="skill:wrong-source")
        self.assertEqual(counts(owner), before)

    def test_forged_consumer_projection_does_not_replace_original_input_bytes(self):
        owner, execution, _member_ref, index = self.world()
        outputs = publish(owner, execution, index)
        active = consumer(owner, outputs)
        changed = json.loads(json.dumps(index))
        changed["metadata"]["description"] = "forged description"
        projected = replace(active.operation.inputs[0],
                            projected_payload=json.dumps(changed).encode())
        forged = replace(active, operation=replace(active.operation, inputs=(projected,)))
        read = skill.read_firing_skill_index(owner, forged,
            SourceQualifiedResourceRef(SOURCE, outputs.outputs[0].resource_ref),
            command_id="skill:projection")
        self.assertEqual(read["index"], index)

    def test_same_length_artifact_payload_cannot_replace_registered_index_or_member(self):
        owner, execution, _member_ref, index = self.world()
        outputs = publish(owner, execution, index)
        index_ref = SourceQualifiedResourceRef(SOURCE, outputs.outputs[0].resource_ref)
        item, = execution.operation.inputs
        changed_payload = encode_agent_text(RAW.replace(
            "Read this exact guide.", "Fake this exact guide."))
        self.assertEqual(len(changed_payload), len(item.artifact.payload))
        changed = replace(item, artifact=replace(item.artifact, payload=changed_payload))
        supplied = replace(execution,
            operation=replace(execution.operation, inputs=(changed,)))
        # Both paths retain real original Start/resource/receipt facts. Only
        # caller-held payload bytes are changed, without a parser substitute.
        with self.subTest(material="member"):
            read = skill.read_firing_skill_member(owner, supplied, index_ref,
                "scripts/reference.py", command_id="skill:actual-member")
            self.assertEqual(read["text"], RAW)
            self.assertEqual(read["receipt"], item.artifact.receipt)

        active = consumer(owner, outputs)
        item, = active.operation.inputs
        changed_index = json.loads(item.artifact.payload)
        changed_index["metadata"]["description"] = "Fake this exact guide."
        changed_payload = canonical_json(changed_index)
        self.assertEqual(len(changed_payload), len(item.artifact.payload))
        changed = replace(item, artifact=replace(item.artifact, payload=changed_payload))
        supplied = replace(active,
            operation=replace(active.operation, inputs=(changed,)))
        before = counts(owner)
        with self.subTest(material="index"):
            read = skill.read_firing_skill_index(owner, supplied, index_ref,
                command_id="skill:actual-index")
            self.assertEqual(read["index"], index)
            self.assertEqual(read["receipt"], item.artifact.receipt)
            self.assertEqual(counts(owner), before)

    def test_settled_carrier_requires_original_full_receipt_header_and_start(self):
        owner, execution, _member_ref, index = self.world()

        def supplied(current, item, payload, *, forged_header=False, wrong_start=False):
            resource = item.artifact.resource
            if forged_header:
                resource = replace(resource,
                    header=replace(resource.header, byte_size=len(payload)))
            # Caller-built counterexample over real Start and acknowledged
            # inputs; this is not a native settlement/recovery fixture.
            artifact = SettledPetriInputArtifact(
                payload=payload, resource=resource,
                receipt=replace(item.artifact.receipt, positive_byte_count=len(payload)),
                source_invocation_ref=current.operation.canonical.context.invocation_ref,
                source_firing_ref=current.operation.firing.transition_firing_ref,
                operation_start_event_id=(new_id("event") if wrong_start
                                          else current.start_event_id))
            changed = replace(item, artifact=artifact)
            return replace(current, operation=replace(current.operation,
                inputs=tuple(changed if candidate is item else candidate
                             for candidate in current.operation.inputs)))

        def rejected_before_body(current, item, cases, consume):
            original_read = _ResourceServiceKernel._read_firing_registered
            for label, payload, forged_header, wrong_start in cases:
                actual_reads = []

                def observe_registered_read(kernel, context, ref, *args, **kwargs):
                    if ref == item.resource_ref:
                        actual_reads.append(ref)
                    return original_read(kernel, context, ref, *args, **kwargs)

                before = counts(owner)
                with self.subTest(schema=item.artifact.resource.header.content_schema_ref,
                                  case=label):
                    changed = supplied(current, item, payload,
                        forged_header=forged_header, wrong_start=wrong_start)
                    with patch.object(_ResourceServiceKernel, "_read_firing_registered",
                                      observe_registered_read):
                        with self.assertRaisesRegex(skill.SkillMaterialError,
                                                    "exact full input"):
                            consume(changed)
                    self.assertEqual(actual_reads, [])
                    self.assertEqual(counts(owner), before)

        def cases(item):
            return (
                ("zero", b"", False, False),
                ("one", item.artifact.payload[:1], False, False),
                ("one-and-header", item.artifact.payload[:1], True, False),
                ("wrong-start", item.artifact.payload, False, True),
            )

        item, = execution.operation.inputs
        binding, = execution.operation.operation_binding.output_port_bindings

        def prepare(current):
            return skill.prepare_index_publication(owner, current, index,
                output_port_id=binding.port_id, outcome_id="complete",
                idempotency_key="skill:settled:publication")

        rejected_before_body(execution, item, cases(item), prepare)
        full = supplied(execution, item, item.artifact.payload)
        self.assertEqual(prepare(full).payload, canonical_json(index))

        outputs = publish(owner, execution, index)
        active = consumer(owner, outputs)
        index_ref = SourceQualifiedResourceRef(SOURCE, outputs.outputs[0].resource_ref)
        item, = active.operation.inputs
        shortened = json.loads(item.artifact.payload)
        shortened["metadata"]["description"] = "x"
        shorter_valid_index = canonical_json(shortened)
        self.assertLess(len(shorter_valid_index), len(item.artifact.payload))
        read_cases = cases(item) + (
            ("short-valid-index-and-header", shorter_valid_index, True, False),)
        rejected_before_body(active, item, read_cases,
            lambda current: skill.read_firing_skill_index(owner, current, index_ref,
                command_id="skill:settled:index"))
        full = supplied(active, item, item.artifact.payload)
        before = counts(owner)
        read = skill.read_firing_skill_index(owner, full, index_ref,
            command_id="skill:settled:full")
        self.assertEqual(read["index"], index)
        self.assertEqual(read["receipt"], item.artifact.receipt)
        self.assertEqual(counts(owner), before)

    def test_member_outside_selection_and_byte_limit_do_not_read_member(self):
        owner, execution, _member_ref, index = self.world()
        outputs = publish(owner, execution, index)
        active = consumer(owner, outputs)
        index_ref = SourceQualifiedResourceRef(SOURCE, outputs.outputs[0].resource_ref)
        before = counts(owner)
        with self.assertRaises(skill.SkillMaterialError):
            skill.read_firing_skill_member(owner, active, index_ref, "scripts/missing.py",
                                          command_id="skill:missing")
        with self.assertRaises(skill.SkillMaterialError):
            skill.read_firing_skill_index(owner, active, index_ref,
                                         command_id="skill:bounded", max_bytes=0)
        self.assertEqual(counts(owner), before)

    def test_full_material_helper_rejects_projected_or_substituted_authorities(self):
        # This tests the typed adapter guard after hydration, not a declared
        # projection flowing through Module. Public rehydration is tested above.
        owner, execution, member_ref, index = self.world()
        kernel, _repository, current = skill._owner_execution(owner, execution)
        item, = current.operation.inputs
        binding, = current.operation.operation_binding.output_port_bindings
        changed_items = (
            replace(item, projected_payload=b'"only an allowed projection"'),
            replace(item, source_resource_ref=replace(member_ref.ref,
                resource_id=new_id("resource"), resource_version_id=new_id("resource_version")),
                substituted_content_schema_id=TEXT_SCHEMA),
        )
        before = counts(owner)
        for changed in changed_items:
            with self.subTest(projection=changed.projected_payload is not None):
                plan = current.operation.input_plan
                if changed.source_resource_ref is not None:
                    plan = replace(plan, claims=tuple(replace(claim,
                        source_resource_ref=changed.source_resource_ref,
                        substituted_content_schema_id=TEXT_SCHEMA)
                        for claim in plan.claims))
                changed_execution = replace(current,
                    operation=replace(current.operation, input_plan=plan,
                                      inputs=(changed,)))
                with self.assertRaisesRegex(skill.SkillMaterialError, "exact full input"):
                    skill._firing_material(kernel, changed_execution, member_ref,
                        core=owner._core, source_id=SOURCE, authorization_ref=None,
                        command_id="skill:helper-reject", schema=TEXT_SCHEMA,
                        max_bytes=None)
                with self.assertRaisesRegex(skill.SkillMaterialError, "exact admitted inputs"):
                    skill._index_publication(owner, changed_execution, index,
                        output_port_id=binding.port_id, outcome_id="complete",
                        idempotency_key="skill:helper-publish-reject")
        self.assertEqual(counts(owner), before)

    def test_original_member_input_is_reused_as_text_and_never_executed(self):
        owner, execution, member_ref, index = self.world()
        outputs = publish(owner, execution, index)
        index_ref = SourceQualifiedResourceRef(SOURCE, outputs.outputs[0].resource_ref)
        read = skill.read_firing_skill_member(owner, execution, index_ref,
            "scripts/reference.py", command_id="skill:inert-script")
        self.assertEqual(read["text"], RAW)
        self.assertEqual(read["receipt"], execution.operation.inputs[0].artifact.receipt)
        self.assertEqual(read["receipt"].exact_resource_ref, member_ref.ref)

    def test_settled_firing_cannot_open_new_skill_delivery(self):
        owner, execution, _member_ref, index = self.world()
        outputs = publish(owner, execution, index)
        owner.succeed(outputs, command_id="skill:publisher:success")
        before = counts(owner)
        with self.assertRaises(ResourceServiceError):
            skill.read_firing_skill_index(owner, execution,
                SourceQualifiedResourceRef(SOURCE, outputs.outputs[0].resource_ref),
                command_id="skill:closed-firing")
        self.assertEqual(counts(owner), before)


if __name__ == "__main__":
    unittest.main()



