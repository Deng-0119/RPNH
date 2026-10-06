"""Record grammar and JSON freezing only; no candidate commit/readiness claim."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import re
from pathlib import Path

import pytest

from cpn.rpnh.collaboration import SourceQualifiedVersionRef, candidate_schema_data
from cpn.rpnh.registry.identities import new_id, TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.runtime_binding_contracts import (
    PLAN_TYPE, MANIFEST_TYPE, READINESS_TYPE, PLAN_SCHEMA, MANIFEST_SCHEMA, READINESS_SCHEMA,
    RuntimeBindingManifestDraft, freeze_candidate_document, is_runtime_manifest_type,
)
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json
from cpn.rpnh.registry.strict_contracts import content_schema_ref_payload, ref_payload
from test_module_graph_projection import graph_arguments


def record_ref(kind):
    return VersionRef(kind, new_id("resource"), new_id("resource_version"))


@pytest.fixture(scope="module")
def grammar(tmp_path_factory):
    core, compiled, _, arguments = graph_arguments(tmp_path_factory.mktemp("record-shape"), "basic")
    documents, definitions, paths = candidate_schema_data()
    catalog = SchemaCatalog(schemas=documents, types=definitions, schema_paths=paths)
    plan_ref, manifest_ref = record_ref(PLAN_TYPE), record_ref(MANIFEST_TYPE)
    author = json.loads(core.event_store.object_rows_by_type("collaboration_net_revision/v1")[0]["metadata_json"])
    host = core.object_store.read_registered(core.get_version(author["host_requirements_ref"]["ref"]["resource_version_id"]))
    identity = arguments["identity"]
    host_binding = {"agent_ref": None, "activation_ref": None, "llm_input_target_ref": None,
        "workspace_binding_ref": None, "module_artifact_refs": [], "extra_resource_refs": []}
    plan = {"schema_version": PLAN_SCHEMA, "plan_ref": ref_payload(plan_ref), "manifest_ref": ref_payload(manifest_ref),
        "source_id": "source-a", "command_id": "candidate:shape", "owner_task_ref": ref_payload(identity.task_ref),
        "producer_principal_ref": SourceQualifiedVersionRef("source-a", arguments["principal_ref"]).to_dict(),
        "author_ref": author["revision_ref"], "principal_ref": ref_payload(arguments["principal_ref"]),
        "bootstrap_ref": ref_payload(arguments["bootstrap_ref"]), "task_round_ref": ref_payload(arguments["task_round_ref"]),
        "authority_decision_ref": ref_payload(arguments["authority_decision_ref"]),
        "run_identity": {"run_ref": ref_payload(identity.run_ref), "task_ref": ref_payload(identity.task_ref),
            "task_branch_ref": ref_payload(identity.task_branch_ref), "genesis_manifest_ref": ref_payload(identity.genesis_manifest_ref),
            "branch_id": identity.branch_id, "protocol_versions": list(identity.protocol_versions)},
        "graph_command_key": arguments["idempotency_key"], "operation_command_key": "fixture:operations",
        "schema_refs": {key: content_schema_ref_payload(ref) for key, ref in arguments["schema_refs"].items()},
        "operation_refs": {key: ref_payload(ref) for key, ref in arguments["operation_refs"].items()},
        "compiled": compiled.to_dict(), "host_requirements": json.loads(host), "entry_inputs": {},
        "owner_resource_inputs": {}, "owner_input_resources": [], "preserved_slot_refs": {},
        "host_bindings": {item.name: deepcopy(host_binding) for item in compiled.symbolic.transitions},
        "runtime_dependencies": {item.name: {} for item in compiled.symbolic.transitions},
        "command_context": {"nested": [{"value": True}]}, "host_inventory": {"resource_refs": [], "artifact_refs": []},
        "dependency_evidence": []}
    return catalog, plan_ref, manifest_ref, plan


def test_opt_in_record_grammars_leave_default_catalog_unsupported(grammar, monkeypatch):
    import urllib.request
    def forbidden(*args, **kwargs):
        raise AssertionError("candidate grammar must not request external schema retrieval")
    monkeypatch.setattr(urllib.request, "Request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    catalog, _, _, plan = grammar
    catalog.validate_instance(PLAN_TYPE, category="object", instance=plan)
    default = SchemaCatalog()
    for kind in (PLAN_TYPE, MANIFEST_TYPE, READINESS_TYPE):
        catalog.require(kind, category="object")
        with pytest.raises(SchemaGovernanceError):
            default.require(kind, category="object")


@pytest.mark.parametrize("damage", ["missing_context", "unknown_field", "wrong_manifest_type", "unknown_role"])
def test_complete_plan_shape_is_closed(grammar, damage):
    catalog, _, _, original = grammar
    plan = deepcopy(original)
    if damage == "missing_context":
        plan.pop("command_context")
    elif damage == "unknown_field":
        plan["current_default_profile"] = "never-fallback"
    elif damage == "wrong_manifest_type":
        plan["manifest_ref"]["entity_type"] = "runtime_binding_manifest/v2"
    else:
        next(iter(plan["runtime_dependencies"].values()))["automatic_remote_trust"] = plan["author_ref"]
    with pytest.raises(SchemaGovernanceError):
        catalog.validate_instance(PLAN_TYPE, category="object", instance=plan)


def test_finite_draft_deep_detaches_and_preserves_json_type_distinctions(grammar):
    _, plan_ref, manifest_ref, original = grammar
    document = deepcopy(original)
    draft = RuntimeBindingManifestDraft.from_document(plan_ref, manifest_ref, document)
    document["command_context"]["nested"][0]["value"] = 1
    returned = draft.plan
    returned["command_context"]["nested"].append("mutated")
    assert draft.plan["command_context"] == {"nested": [{"value": True}]}
    assert freeze_candidate_document(document) != draft._plan_json
    assert freeze_candidate_document({"value": 1}) != freeze_candidate_document({"value": 1.0})
    with pytest.raises(FrozenInstanceError):
        draft._plan_json = "{}"
    with pytest.raises(ValueError, match="refs"):
        RuntimeBindingManifestDraft.from_document(plan_ref, record_ref(MANIFEST_TYPE), original)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), b"bytes", (1,), {1}, object()])
def test_strict_request_freeze_rejects_non_json_values(value):
    with pytest.raises((TypeError, ValueError)):
        freeze_candidate_document({"opaque": value})


@pytest.mark.parametrize("key", [1, True, None, ("tuple",)])
def test_request_freeze_rejects_non_string_keys_before_serialization(key):
    with pytest.raises(TypeError, match="keys"):
        freeze_candidate_document({key: "value"})


def test_freeze_never_calls_user_string_coercion_and_handles_cycles():
    class Opaque:
        def __str__(self):
            raise AssertionError("candidate freeze must not invoke default=str")
    with pytest.raises(TypeError):
        freeze_candidate_document({"opaque": Opaque()})
    values = []
    values.append(values)
    with pytest.raises(ValueError, match="cyclic"):
        freeze_candidate_document({"cycle": values})
    shared = [True, 1, 1.0]
    assert json.loads(freeze_candidate_document({"a": shared, "b": shared})) == {"a": shared, "b": shared}


def test_checked_copy_cannot_be_replaced_before_encoding(monkeypatch):
    from cpn.rpnh.registry import runtime_binding_contracts as contracts
    class Opaque:
        def __str__(self):
            raise AssertionError("unchecked concurrent value must not be coerced")
    document = {"nested": [{"value": True}]}
    original_dumps = json.dumps
    def mutate_caller_before_encoding(value, **kwargs):
        document["nested"][0]["value"] = Opaque()
        return original_dumps(value, **kwargs)
    monkeypatch.setattr(contracts.json, "dumps", mutate_caller_before_encoding)
    encoded = freeze_candidate_document(document)
    assert isinstance(document["nested"][0]["value"], Opaque)
    assert json.loads(encoded) == {"nested": [{"value": True}]}


@pytest.mark.parametrize("document", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}', '[]'])
def test_draft_decoder_rejects_lossy_or_nonstandard_input(grammar, document):
    _, plan_ref, manifest_ref, _ = grammar
    with pytest.raises((TypeError, ValueError)):
        RuntimeBindingManifestDraft(plan_ref, manifest_ref, document)


@pytest.mark.parametrize("kind,expected", [(MANIFEST_TYPE, True), ("runtime_binding_manifest/v2", True),
    ("runtime_binding_manifest/v99", True), ("runtime_binding_manifest_notes/v1", False), (None, False)])
def test_reserved_family_classification_is_not_v1_support(kind, expected):
    assert is_runtime_manifest_type(kind) is expected


def test_readiness_grammar_cannot_claim_execution_or_reserved_capacity(grammar):
    catalog, plan_ref, manifest_ref, _ = grammar
    ref = VersionRef("executable_transition_binding/v1", new_id("executable_transition_binding"), new_id("executable_transition_binding_version"))
    document = {"schema_version": READINESS_SCHEMA, "readiness_ref": ref_payload(record_ref(READINESS_TYPE)),
        "plan_ref": ref_payload(plan_ref), "manifest_ref": ref_payload(manifest_ref), "check_kind": "offline_static_preparation",
        "execution_permission_checked": False, "checks": [{"transition_id": "step.run", "executable_ref": ref_payload(ref),
            "registry_resolution": "resolved", "static_scope": "matched", "execution_handle": "not_checked",
            "runtime_capacity": "not_checked", "reservation_state": "not_reserved", "blocking_requirements": []}]}
    catalog.validate_instance(READINESS_TYPE, category="object", instance=document)
    for field, value in (("execution_handle", "created"), ("runtime_capacity", "available"), ("reservation_state", "reserved")):
        changed = deepcopy(document)
        changed["checks"][0][field] = value
        with pytest.raises(SchemaGovernanceError):
            catalog.validate_instance(READINESS_TYPE, category="object", instance=changed)
    document["execution_permission_checked"] = True
    with pytest.raises(SchemaGovernanceError):
        catalog.validate_instance(READINESS_TYPE, category="object", instance=document)


def test_manifest_grammar_requires_explicit_exact_inventory_and_known_version(grammar):
    catalog, plan_ref, manifest_ref, plan = grammar
    def typed(kind, logical, version):
        return ref_payload(VersionRef(kind, new_id(logical), new_id(version)))
    name = next(iter(plan["host_bindings"]))
    document = {"schema_version": MANIFEST_SCHEMA, "manifest_ref": ref_payload(manifest_ref),
        "plan_ref": ref_payload(plan_ref), "source_id": plan["source_id"], "author_ref": plan["author_ref"],
        "task_ref": plan["owner_task_ref"], "run_ref": plan["run_identity"]["run_ref"], "task_round_ref": plan["task_round_ref"],
        "net_ref": typed("net_instance/v1", "net_instance", "net_instance_version"),
        "root_ref": typed("team_design_root/v1", "team_design_root", "team_design_root_version"),
        "declaration_ref": next(iter(plan["schema_refs"].values())), "object_refs": [], "entries": [{
            "transition_id": name,
            "executable_ref": typed("executable_transition_binding/v1", "executable_transition_binding", "executable_transition_binding_version"),
            "operation_binding_ref": typed("operation_binding/v1", "operation_binding", "operation_binding_version"),
            "operation_spec_ref": next(iter(plan["operation_refs"].values())),
            "host_binding": plan["host_bindings"][name], "dependencies": {}}]}
    # This is shape validation only. Registry existence, inventory completeness
    # and source relationships must be established by the future fixed gate.
    catalog.validate_instance(MANIFEST_TYPE, category="object", instance=document)
    for field in ("plan_ref", "object_refs", "entries", "source_id"):
        changed = deepcopy(document)
        changed.pop(field)
        with pytest.raises(SchemaGovernanceError):
            catalog.validate_instance(MANIFEST_TYPE, category="object", instance=changed)
    document["schema_version"] = "registry_v1/runtime_binding_manifest/v99"
    with pytest.raises(SchemaGovernanceError):
        catalog.validate_instance(MANIFEST_TYPE, category="object", instance=document)


def embed_local_schema(document, pointer, *, root=True):
    """Mechanical Draft7 schema relocation; literal instance values are kept."""
    if isinstance(document, bool):
        return document
    result = {}
    maps = {"definitions", "properties", "patternProperties"}
    singles = {"additionalProperties", "additionalItems", "contains", "propertyNames", "not", "if", "then", "else"}
    arrays = {"allOf", "anyOf", "oneOf"}
    for key, value in document.items():
        if key in {"$id", "$schema"}:
            if key == "$id" and not root:
                raise ValueError("nested schema IDs require an explicit relocation contract")
            continue
        if key == "$ref":
            if value == "#":
                result[key] = pointer
            elif value.startswith("#/"):
                result[key] = pointer + value[1:]
            else:
                raise ValueError("only local JSON pointer schema references can be embedded")
        elif key in maps:
            result[key] = {name: embed_local_schema(child, pointer, root=False) for name, child in value.items()}
        elif key in singles:
            result[key] = embed_local_schema(value, pointer, root=False)
        elif key in arrays or (key == "items" and isinstance(value, list)):
            result[key] = [embed_local_schema(child, pointer, root=False) for child in value]
        elif key == "items":
            result[key] = embed_local_schema(value, pointer, root=False)
        elif key == "dependencies":
            result[key] = {name: deepcopy(child) if isinstance(child, list) else embed_local_schema(child, pointer, root=False)
                for name, child in value.items()}
        else:
            result[key] = deepcopy(value)
    return result


def test_embedded_wire_and_host_grammars_match_their_actual_source_contracts(grammar):
    catalog, _, _, _ = grammar
    plan = json.loads(catalog.schema_path(PLAN_SCHEMA).read_text())
    for field, name, schema in (("compiled", "compiled_wire", "rpnh/executable_net/v1"),
            ("host_requirements", "author_host", "rpnh/collaboration/author_host_requirements/v1")):
        pointer = "#/definitions/" + name
        original = json.loads(catalog.schema_path(schema).read_text())
        assert plan["properties"][field] == {"$ref": pointer}
        assert canonical_json(plan["definitions"][name]) == canonical_json(embed_local_schema(original, pointer))


@pytest.mark.parametrize("schema", [PLAN_SCHEMA, MANIFEST_SCHEMA, READINESS_SCHEMA])
def test_every_new_id_digest_and_lexical_pattern_rejects_final_newline(schema):
    from jsonschema import Draft7Validator
    documents, _, _ = candidate_schema_data()
    patterns = []
    def visit(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                if path == ("definitions",) and key in {"compiled_wire", "author_host"}:
                    continue
                if key == "pattern":
                    patterns.append(child)
                else:
                    visit(child, (*path, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, index))
    visit(documents[schema])
    assert patterns
    for pattern in patterns:
        if ":[a-f0-9]{32}" in pattern:
            explicit = re.match(r"^\^([a-z][a-z0-9_]*):", pattern)
            valid = (explicit.group(1) if explicit else "resource") + ":" + "a" * 32
        elif "[a-f0-9]{64}" in pattern:
            valid = "a" * 64
        elif "/v[1-9]" in pattern:
            valid = "resource_version/v1"
        else:
            valid = "source-a"
        validator = Draft7Validator({"type": "string", "pattern": pattern})
        assert validator.is_valid(valid), pattern
        assert not validator.is_valid(valid + "\n"), pattern


@pytest.mark.parametrize("schema", [PLAN_SCHEMA, MANIFEST_SCHEMA, READINESS_SCHEMA])
@pytest.mark.parametrize("entity_type", ["not a type", "resource/v0", "Resource/v1", "resource/v1\n", "resource/version"])
def test_qualified_ref_reuses_original_entity_type_lexical_contract(schema, entity_type):
    from jsonschema import Draft7Validator
    documents, _, _ = candidate_schema_data()
    document = {"schema_version": "rpnh/collaboration/source_version_ref/v1", "source_id": "source-a",
        "ref": {"entity_type": entity_type, "logical_id": "resource:" + "a" * 32,
            "version_id": "resource_version:" + "b" * 32}}
    validator = Draft7Validator({"$ref": "#/definitions/qualified_ref", "definitions": documents[schema]["definitions"]})
    assert not validator.is_valid(document)
    with pytest.raises(TypeError):
        SourceQualifiedVersionRef("source-a", VersionRef(entity_type, new_id("resource"), new_id("resource_version")))


@pytest.mark.parametrize("side", ["plan", "manifest"])
@pytest.mark.parametrize("fault", ["fake_id", "ref_subclass", "id_subclass", "entity_type_subclass",
    "id_kind_subclass", "id_value_subclass", "bad_id_value"])
def test_draft_rejects_impostor_refs_before_any_string_coercion(side, fault):
    class TrapString(str):
        def __str__(self):
            raise AssertionError("impostor ref string coercion must never run")
    class MutableId:
        kind = "resource"
        def __str__(self):
            raise AssertionError("mutable fake ID string coercion must never run")
    class RefSubclass(VersionRef):
        pass
    class IdSubclass(TypedId):
        def __str__(self):
            raise AssertionError("typed ID subclass coercion must never run")
    plan_ref, manifest_ref = record_ref(PLAN_TYPE), record_ref(MANIFEST_TYPE)
    document = {"schema_version": PLAN_SCHEMA, "plan_ref": ref_payload(plan_ref), "manifest_ref": ref_payload(manifest_ref)}
    original = plan_ref if side == "plan" else manifest_ref
    if fault == "fake_id":
        bad = VersionRef(original.entity_type, MutableId(), original.version_id)
    elif fault == "ref_subclass":
        bad = RefSubclass(original.entity_type, original.entity_id, original.version_id)
    elif fault == "id_subclass":
        bad = VersionRef(original.entity_type, IdSubclass("resource", "a" * 32), original.version_id)
    elif fault == "entity_type_subclass":
        bad = VersionRef(TrapString(original.entity_type), original.entity_id, original.version_id)
    else:
        identifier = new_id("resource")
        if fault == "id_kind_subclass":
            object.__setattr__(identifier, "kind", TrapString("resource"))
        elif fault == "id_value_subclass":
            object.__setattr__(identifier, "value", TrapString("a" * 32))
        else:
            object.__setattr__(identifier, "value", "a" * 32 + "\n")
        bad = VersionRef(original.entity_type, identifier, original.version_id)
    with pytest.raises((TypeError, ValueError)):
        RuntimeBindingManifestDraft.from_document(bad if side == "plan" else plan_ref,
            bad if side == "manifest" else manifest_ref, document)


def test_draft_rebuilds_standard_ref_values_instead_of_retaining_caller_ids():
    plan_ref, manifest_ref = record_ref(PLAN_TYPE), record_ref(MANIFEST_TYPE)
    document = {"schema_version": PLAN_SCHEMA, "plan_ref": ref_payload(plan_ref), "manifest_ref": ref_payload(manifest_ref)}
    draft = RuntimeBindingManifestDraft.from_document(plan_ref, manifest_ref, document)
    assert draft.plan_ref == plan_ref and draft.plan_ref is not plan_ref
    assert draft.plan_ref.entity_id is not plan_ref.entity_id
    object.__setattr__(plan_ref.entity_id, "value", "c" * 32)
    assert ref_payload(draft.plan_ref) == document["plan_ref"]
    assert ref_payload(draft.manifest_ref) == document["manifest_ref"]
