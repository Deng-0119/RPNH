"""Internal first-plan preparation for the opt-in exact candidate producer.

This recovery slice persists only a complete inert plan for plain default
operation Modules with explicit business-resource selections. Graph publication, runtime manifests, optional HOST choices,
and readiness are separate boundaries and are not provided by this module.
"""
from __future__ import annotations

import json

from ..executable_net import _load_compiled_net_offline
from ..registration import Registration
from ..registry._candidate_plan_reads import PlanReadClosure, same
from ..registry._candidate_read_context import _CandidateReadContext
from ..registry._event_store.collaboration_descriptors import exact_prepared, readable_descriptor
from ..registry._event_store.source_identity import _canonical_commit_event
from ..registry.bootstrap import NativeRunIdentity
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.resources import ResourceVersionRef
from ..registry.publication import _version_from_payload
from ..registry.registration_gateway import RegistryRegistrationGateway
from ..registry.runtime_binding_contracts import (
    PLAN_TYPE, PLAN_SCHEMA, MANIFEST_TYPE, RuntimeBindingManifestDraft, freeze_candidate_document,
)
from ..registry.schema_catalog import canonical_json
from ..registry.strict_contracts import _stable_id, ref_payload
from .materials import (
    AuthorMaterialReadContext, _HostDeclarationClosure, _match_author_compiled, _requires_graph_source_rebuild,
    _validate_source_at, validate_closed_revision,
)
from .references import SourceQualifiedVersionRef


def _copy_ref(value, expected_type):
    # Inspect concrete data before any str(), custom to_dict(), or ref_payload().
    if type(value) is not VersionRef or type(value.entity_type) is not str or value.entity_type != expected_type:
        raise TypeError("candidate input requires a standard exact VersionRef")
    ids = []
    for identifier in (value.entity_id, value.version_id):
        if (type(identifier) is not TypedId or type(identifier.kind) is not str
                or type(identifier.value) is not str):
            raise TypeError("candidate input requires standard exact TypedId fields")
        ids.append(TypedId(identifier.kind, identifier.value))
    return VersionRef(expected_type, *ids)


def _resource_pair(value):
    if type(value) is not ResourceVersionRef:
        raise TypeError("candidate business input requires a standard ResourceVersionRef")
    result = {}
    for field, kind in (("resource_id", "resource"), ("resource_version_id", "resource_version")):
        identifier = getattr(value, field)
        if (type(identifier) is not TypedId or type(identifier.kind) is not str
                or type(identifier.value) is not str or identifier.kind != kind):
            raise TypeError("candidate business input requires standard resource TypedId fields")
        result[field] = str(TypedId(kind, identifier.value))
    return result


def _resource_selections(entry_inputs, owner_resource_inputs, owner_input_resources):
    def mapping(value):
        if value is None:
            return {}
        if type(value) is not dict or any(type(key) is not str for key in value):
            raise TypeError("candidate resource selections require a standard string-key mapping")
        return value
    def bundle(value):
        if type(value) is ResourceVersionRef:
            return [_resource_pair(value)]
        if type(value) is not list and type(value) is not tuple:
            raise TypeError("candidate resource bundle requires a standard list/tuple of exact refs")
        return [_resource_pair(item) for item in value]
    if owner_input_resources is not None and type(owner_input_resources) is not list and type(owner_input_resources) is not tuple:
        raise TypeError("candidate owner marking inputs require a standard list/tuple of exact refs")
    return {"entry_inputs": {key: bundle(value) for key, value in mapping(entry_inputs).items()},
        "owner_resource_inputs": {key: _resource_pair(value) for key, value in mapping(owner_resource_inputs).items()},
        "owner_input_resources": [] if owner_input_resources is None else bundle(owner_input_resources)}


def _copy_qualified(value, expected_type):
    if type(value) is not SourceQualifiedVersionRef or type(value.source_id) is not str:
        raise TypeError("candidate input requires one standard source-qualified exact ref")
    return SourceQualifiedVersionRef(value.source_id, _copy_ref(value.ref, expected_type))


def _command_key(task, source, command):
    if (type(command) is not str or not command or command != command.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in command)):
        raise ValueError("candidate command_id must be canonical")
    return "collaboration-candidate:" + freeze_candidate_document({
        "task": str(task), "source": source, "command_id": command})


def _record_ref(kind, key):
    return VersionRef(kind, _stable_id("resource", key), _stable_id("resource_version", key))


def _operation_refs(compiled, key):
    return {item.declaration.name: ref_payload(VersionRef("operation_spec/v1",
        _stable_id("operation_spec", f"{key}:operation-spec:{item.declaration.name}"),
        _stable_id("operation_spec_version", f"{key}:operation-spec:{item.declaration.name}")))
        for item in compiled.operations}


def _empty_hosts(compiled):
    return {item.name: {"agent_ref": None, "activation_ref": None, "llm_input_target_ref": None,
        "workspace_binding_ref": None, "module_artifact_refs": [], "extra_resource_refs": []}
        for item in compiled.symbolic.transitions}


def _supported_plan(plan, compiled):
    if _requires_graph_source_rebuild(compiled.source):
        raise RegistryConflict("graph-authoritative preparation requires a single-source rebuild")
    if any(item.key != "operation" for item in compiled.source.components):
        raise RegistryConflict("this plan slice supports only default operation Modules")
    empty = {"preserved_slot_refs": {}, "host_bindings": _empty_hosts(compiled),
        "runtime_dependencies": {item.name: {} for item in compiled.symbolic.transitions},
        "host_inventory": {"resource_refs": [], "artifact_refs": []}}
    if any(not same(plan[key], value) for key, value in empty.items()):
        raise RegistryConflict("additional candidate selections are unsupported in this plan slice")


def _pure_author_preflight(db, core, closure, author):
    if author.source_id != closure.binding["source_id"] or author.ref.entity_type != "collaboration_net_revision/v1":
        raise RegistryConflict("candidate plan requires its exact local closed Module author")
    context = AuthorMaterialReadContext(closure.store, core.catalog, core.task_id, core.branch_id)
    declarations = _HostDeclarationClosure()
    material = _validate_source_at(db, context, author, closure.binding, set(), _host_closure=declarations)
    # Every actual material/schema authority byte read is checked and recorded
    # before any wire or HOST instance validation can consume its schema.
    closure.finish_resources()
    return material, declarations


def _collect_plan_dependencies(db, core, plan):
    """Fixed pure data read at the caller's cut, with no compiler/HOST calls."""
    closure = PlanReadClosure(db, core)
    closure.owner(plan)
    author = SourceQualifiedVersionRef.from_dict(plan["author_ref"], catalog=core.catalog)
    material, _ = _pure_author_preflight(db, core, closure, author)
    # Compare inert wire data before any fragment/config validation. A foreign
    # schema cannot be evaluated merely because it appears in a shaped plan.
    if (not same(plan["compiled"]["source"], material.module.to_dict())
            or not same(plan["compiled"]["registrations"], material.host_requirements["registrations"])):
        raise RegistryConflict("candidate raw wire differs from exact author source/HOST materials")
    compiled = _load_compiled_net_offline(plan["compiled"])
    _supported_plan(plan, compiled)
    material = _match_author_compiled(material, compiled)
    if not same(material.host_requirements, plan["host_requirements"]):
        raise RegistryConflict("candidate plan differs from complete author HOST materials")
    schemas = {item["key"]: item["resource_ref"]["ref"] for item in material.host_requirements["declaration_refs"]
        if item["kind"] == "schema" and item["key"] in compiled.source.required_schemas}
    if set(schemas) != set(compiled.source.required_schemas) or not same(schemas, plan["schema_refs"]):
        raise RegistryConflict("candidate schema selection differs from exact author declarations")
    key = _command_key(core.task_id, closure.binding["source_id"], plan["command_id"])
    if (not same(plan["plan_ref"], ref_payload(_record_ref(PLAN_TYPE, key + ":plan")))
            or not same(plan["manifest_ref"], ref_payload(_record_ref(MANIFEST_TYPE, key + ":manifest")))
            or plan["graph_command_key"] != key + ":graph" or plan["operation_command_key"] != key + ":operations"
            or not same(plan["operation_refs"], _operation_refs(compiled, key + ":operations"))):
        raise RegistryConflict("candidate plan differs from its complete command's allocated identities")
    closure.business_inputs(plan, compiled)
    return closure.finish_resources()


def read_candidate_plan(core, plan_ref):
    """Exact committed inert plan with independent typed dependency reread.

    The plan is read before constructing the recording closure, so it cannot
    become evidence for itself. This does not prove graph publication/readiness.
    """
    reference = _copy_ref(plan_ref, PLAN_TYPE)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return _read_candidate_plan_at(_CandidateReadContext.from_core(core, db), reference)


def _read_candidate_plan_at(core, plan_ref):
    """Original v1 reader using only an already-owned fixed SQLite cut."""
    if type(core) is not _CandidateReadContext:
        raise TypeError("candidate reading requires its fixed read context")
    core.require_cut()
    reference = _copy_ref(plan_ref, PLAN_TYPE)
    db = core.db
    prepared = exact_prepared(db, core.object_store, core.task_id, ref_payload(reference))
    document = readable_descriptor(core.object_store, prepared)
    row = db.execute("SELECT o.published_event_id,o.transaction_id,t.idempotency_key FROM objects o "
        "JOIN transactions t ON o.transaction_id=t.transaction_id WHERE o.version_id=?", (str(reference.version_id),)).fetchone()
    key = _command_key(core.task_id, document["source_id"], document["command_id"])
    if (prepared.producer_invocation_id is not None or row["idempotency_key"] != key + ":plan"
            or _canonical_commit_event(db, row["transaction_id"], core.task_id) is None
            or db.execute("SELECT 1 FROM firing_temporary_members WHERE "
                "(member_kind='object' AND member_identity=?) OR (member_kind='event' AND member_identity=?)",
                (str(reference.version_id), row["published_event_id"])).fetchone() is not None
            or core.object_store.read_registered(prepared) != freeze_candidate_document(document).encode("utf-8")
            or not same(document["plan_ref"], ref_payload(reference))):
        raise RegistryConflict("candidate plan lacks exact static canonical bytes/identity")
    evidence = _collect_plan_dependencies(db, core, document)
    if not same(evidence, document["dependency_evidence"]):
        raise RegistryConflict("candidate dependency bytes differ from the frozen exact evidence")
    return RuntimeBindingManifestDraft.from_document(reference,
        _version_from_payload(document["manifest_ref"]), document)


def _existing_plan_ref_at(context, key):
    """Discover the stored format; the returned ref still needs its exact reader.

    This performs no publication or version upgrade. Incomplete/conflicting
    command occupancy is not absence. A matching location is not proof of the
    object's canonical bytes, dependency closure or supported plan semantics.
    """
    from ..registry.preserved_binding_contracts import PLAN_V2_TYPE
    if type(context) is not _CandidateReadContext or type(key) is not str or not key:
        raise TypeError("candidate discovery requires a fixed context and command key")
    context.require_cut()
    expected = _record_ref(PLAN_TYPE, key + ":plan")
    row = context.db.execute("SELECT object_type,logical_id,version_id,transaction_id FROM objects WHERE version_id=?",
        (str(expected.version_id),)).fetchone()
    transaction = context.db.execute("SELECT transaction_id,status FROM transactions WHERE task_id=? AND idempotency_key=?",
        (str(context.task_id), key + ":plan")).fetchone()
    if row is None and transaction is None:
        return None
    if (row is None or transaction is None or transaction["status"] != "committed"
            or row["transaction_id"] != transaction["transaction_id"]
            or row["logical_id"] != str(expected.entity_id)
            or row["object_type"] not in (PLAN_TYPE, PLAN_V2_TYPE)):
        raise RegistryConflict("candidate command has incomplete or conflicting plan occupancy")
    return VersionRef(row["object_type"], expected.entity_id, expected.version_id)


class CandidatePlanPublisher:
    """Internal trusted producer of one complete inert first-plan record."""
    def __init__(self, gateway, registration, producer_principal_ref):
        if type(gateway) is not RegistryRegistrationGateway or not isinstance(registration, Registration):
            raise TypeError("candidate plans require the existing owner gateway and trusted Registration")
        self.core, self.gateway, self.registration = gateway._core, gateway, registration
        self.producer = _copy_qualified(producer_principal_ref, "principal/v1")

    def publish(self, *, author_ref, identity, task_round_ref, authority_decision_ref, command_id,
                command_context=None, entry_inputs=None, owner_resource_inputs=None, owner_input_resources=None,
                preserved_slot_refs=None, host_bindings=None, runtime_dependencies=None,
                host_resource_refs=None, host_artifact_refs=None):
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("candidate plan publisher uses a stale owner writer")
        # Normalize fixed typed fields and freeze request-only JSON before
        # compiling or storing anything. Remaining selection roles stay closed.
        context = json.loads(freeze_candidate_document({} if command_context is None else command_context))
        selections = _resource_selections(entry_inputs, owner_resource_inputs, owner_input_resources)
        for value in (preserved_slot_refs, host_bindings, runtime_dependencies):
            if value is not None and (type(value) is not dict or value):
                raise ValueError("nonempty additional candidate selections are not supported yet")
        for value in (host_resource_refs, host_artifact_refs):
            if value is not None and ((type(value) is not list and type(value) is not tuple) or value):
                raise ValueError("nonempty additional candidate selections are not supported yet")
        author = _copy_qualified(author_ref, "collaboration_net_revision/v1")
        if type(identity) is not NativeRunIdentity or type(identity.branch_id) is not str or type(identity.protocol_versions) is not tuple:
            raise TypeError("candidate plans require a standard exact NativeRunIdentity")
        native = {key: ref_payload(_copy_ref(getattr(identity, key), kind)) for key, kind in (
            ("task_ref", "task/v1"), ("run_ref", "native_run_identity/v1"),
            ("task_branch_ref", "task_branch/v1"), ("genesis_manifest_ref", "native_genesis_manifest/v1"))}
        native.update(branch_id=identity.branch_id, protocol_versions=list(identity.protocol_versions))
        principal = _copy_qualified(self.producer, "principal/v1")
        key = _command_key(self.core.task_id, principal.source_id, command_id)
        plan_ref, manifest_ref = _record_ref(PLAN_TYPE, key + ":plan"), _record_ref(MANIFEST_TYPE, key + ":manifest")
        request = {"run_identity": native, "author_ref": author.to_dict(),
            "task_round_ref": ref_payload(_copy_ref(task_round_ref, "task_round/v1")),
            "authority_decision_ref": ref_payload(_copy_ref(authority_decision_ref, "user_authority_decision/v1")),
            "command_context": context, **selections}
        request = json.loads(freeze_candidate_document(request))
        # Preflight the complete pure ancestry before the original trusted
        # consumer can compile. Matching declared schemas is not a sandbox for
        # trusted HOST code; it prevents implicit untrusted-schema retrieval.
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            _, declarations = _pure_author_preflight(db, self.core, PlanReadClosure(db, self.core), author)
            for (category, name), expected in declarations.declarations.items():
                if canonical_json(self.registration.declaration(category, name)) != expected:
                    raise RegistryConflict("candidate HOST Registration differs from exact author material closure")
        # The explicit Registration performs real compilation only outside SQL.
        material = validate_closed_revision(self.core, author, self.registration)
        compiled = material.compiled
        document = {"schema_version": PLAN_SCHEMA, "plan_ref": ref_payload(plan_ref), "manifest_ref": ref_payload(manifest_ref),
            "source_id": principal.source_id, "command_id": command_id,
            "owner_task_ref": ref_payload(_copy_ref(self.gateway._task_ref, "task/v1")),
            "producer_principal_ref": principal.to_dict(), "principal_ref": ref_payload(principal.ref),
            "bootstrap_ref": ref_payload(_copy_ref(self.gateway._bootstrap_ref, "bootstrap_command/v1")),
            "graph_command_key": key + ":graph", "operation_command_key": key + ":operations",
            "schema_refs": {item["key"]: item["resource_ref"]["ref"] for item in material.host_requirements["declaration_refs"]
                if item["kind"] == "schema" and item["key"] in compiled.source.required_schemas},
            "operation_refs": _operation_refs(compiled, key + ":operations"),
            "compiled": compiled.to_dict(), "host_requirements": material.host_requirements,
            "entry_inputs": {}, "owner_resource_inputs": {}, "owner_input_resources": [], "preserved_slot_refs": {},
            "host_bindings": _empty_hosts(compiled), "runtime_dependencies": {item.name: {} for item in compiled.symbolic.transitions},
            "host_inventory": {"resource_refs": [], "artifact_refs": []}, "dependency_evidence": [], **request}
        document = json.loads(freeze_candidate_document(document))
        self.core.catalog.validate_instance(PLAN_TYPE, category="object", instance=document)
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            document["dependency_evidence"] = _collect_plan_dependencies(db, self.core, document)
        frozen = freeze_candidate_document(document)
        self.core.catalog.validate_instance(PLAN_TYPE, category="object", instance=document)
        try:
            self.core.publish_bytes(object_type=PLAN_TYPE, logical_id=plan_ref.entity_id, version_id=plan_ref.version_id,
                payload=frozen.encode("utf-8"), metadata=document, media_type="application/json", schema_ref=PLAN_SCHEMA,
                idempotency_key=key + ":plan")
        except ObjectIntegrityError as exc:
            raise RegistryConflict("candidate command conflicts with its first immutable complete plan") from exc
        result = read_candidate_plan(self.core, plan_ref)
        if result._plan_json != frozen:
            raise RegistryConflict("candidate command conflicts with its first immutable complete plan")
        return result
