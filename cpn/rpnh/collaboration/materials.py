"""Explicit closed-Module author material publication; never runtime adoption."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
import uuid

from ..compiler import compile_module
from ..module import ModuleDeclaration
from ..registration import Registration
from ..registry._event_store.collaboration_descriptors import (
    _canonical_closure, exact_descriptor, exact_prepared, readable_payload,
)
from ..registry._event_store.source_identity import read_source_binding
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.identities import TypedId, fresh_bootstrap_resource_id
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.publication import (_fresh_bootstrap_reference_resource_metadata, _version_from_payload,
                                    _stable_id as _resource_stable_id)
from ..registry.resource_service import _publish_private_system
from ..registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS, canonical_json, canonical_text
from .authoring import NET_REVISION_SCHEMA, NET_REVISION_TYPE, NetRevision, _read_net_revision_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef


ELEMENT_SCHEMA = "rpnh/collaboration/author_element_map/v1"
BOUNDARY_SCHEMA = "rpnh/collaboration/author_boundary_map/v1"
HOST_SCHEMA = "rpnh/collaboration/author_host_requirements/v1"
MODULE_SCHEMA = "rpnh/module_declaration/v1"
_ELEMENT_ID = re.compile(r"element:[a-f0-9]{32}")


def _same_json(left, right):
    return canonical_json(left) == canonical_json(right)


def _elements(module):
    result = {"/": "module", "/terminal": "terminal"}
    for component in module.components:
        base = f"/components/{component.name}"
        result[base] = "component"
        result.update({f"{base}/ports/{port.name}": "port" for port in component.ports})
        result.update({f"{base}/operations/{operation.name}": "operation" for operation in component.operations})
    result.update({f"/links/{index}": "link" for index, _ in enumerate(module.links)})
    result.update({f"/entry/{name}": "entry" for name in module.entry})
    result.update({f"/exit/{name}": "exit" for name in module.exit})
    result.update({f"/terminal_alternatives/{index}": "terminal" for index, _ in enumerate(module.terminal_alternatives)})
    return result


def _element_map(module, element_ids, parent, copy_sources):
    expected = _elements(module)
    if set(element_ids) != set(expected):
        raise ValueError("element IDs must cover exactly the current Module declaration locators")
    ids = tuple(element_ids.values())
    if (any(not isinstance(value, str) or _ELEMENT_ID.fullmatch(value) is None for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("element IDs must be unique canonical stable identities")
    old = {} if parent is None else {item["element_id"]: item for item in parent.element_map["elements"]}
    if set(copy_sources) - set(ids):
        raise ValueError("copy sources must name a new current element ID")
    elements = []
    for locator, kind in sorted(expected.items()):
        identity = element_ids[locator]
        copied = copy_sources.get(identity)
        if identity in old and old[identity]["kind"] != kind:
            raise ValueError("retained element identity cannot change its declared kind")
        if copied is not None:
            if parent is None or identity in old or copied not in old or old[copied]["kind"] != kind:
                raise ValueError("copy needs a new identity and an exact same-kind parent element")
        elements.append({"element_id": identity, "kind": kind, "locator": locator,
            "copied_from": None if copied is None else {"revision_ref": parent.revision.revision_ref.to_dict(), "element_id": copied}})
    return {"schema_version": ELEMENT_SCHEMA, "scope": "module_declaration/v1", "elements": elements}


def _boundaries(module, element_map):
    ids = {item["locator"]: item["element_id"] for item in element_map["elements"]}
    def port(endpoint):
        return ids[f"/components/{endpoint.component}/ports/{endpoint.port}"]
    def terminal(locator, value):
        if f"/components/{value.source.component}/operations/{value.operation}" not in ids:
            raise ValueError("closed author material requires an explicitly declared terminal operation")
        return {"element_id": ids[locator], "port_element_id": port(value.source),
                "operation_element_id": ids[f"/components/{value.source.component}/operations/{value.operation}"]}
    return {"schema_version": BOUNDARY_SCHEMA,
        "entries": [{"element_id": ids[f"/entry/{name}"], "port_element_id": port(value)} for name, value in sorted(module.entry.items())],
        "exits": [{"element_id": ids[f"/exit/{name}"], "port_element_id": port(value)} for name, value in sorted(module.exit.items())],
        "terminals": [terminal("/terminal", module.terminal), *[
            terminal(f"/terminal_alternatives/{index}", value) for index, value in enumerate(module.terminal_alternatives)]]}


def _binding(db, core):
    row = read_source_binding(db, core.catalog, core.task_id)
    if row is None:
        raise RegistryConflict("closed author material requires an explicitly bound local source")
    return json.loads(row["binding_metadata_json"])


def _private_document(db, core, qualified, binding, *, media_type="application/json"):
    """Narrow bootstrap-origin resource reader, using the shared exact closure."""
    if not isinstance(qualified, SourceQualifiedResourceRef) or qualified.source_id != binding["source_id"]:
        raise RegistryConflict("author material must be one exact local-source resource")
    ref = qualified.ref
    prepared = exact_prepared(db, core.object_store, core.task_id, {
        "entity_type": "resource_version/v1", "logical_id": str(ref.resource_id), "version_id": str(ref.resource_version_id)})
    body = prepared.metadata
    task, bootstrap = (_version_from_payload(binding[key]) for key in ("task_ref", "bootstrap_command_ref"))
    for authority in (binding["task_ref"], binding["bootstrap_command_ref"]):
        exact_descriptor(db, core.object_store, core.task_id, authority)
    expected = _fresh_bootstrap_reference_resource_metadata(core, ref=ref, task_ref=task,
        bootstrap_ref=bootstrap, lifetime_ref=bootstrap, payload_size=prepared.size,
        media_type=media_type, content_schema_ref=body["content_schema_ref"],
        content_schema_authority_ref=body["content_schema_authority_ref"], summary=body["summary"],
        descriptors=body["descriptors"], extensions=body["extensions"], derived_from=())
    if not _same_json(body, expected) or prepared.producer_invocation_id is not None:
        raise RegistryConflict("author material bootstrap identity/provenance differs")
    # The writer already records the strong producer relation. Verify its
    # canonical fact as well, without opening another read cut.
    rows = db.execute("SELECT r.*,e.event_type,e.transaction_id AS event_transaction,e.payload_json "
        " ,e.criticality AS event_criticality,e.task_id AS event_task,e.payload_schema_ref AS event_schema, "
        "e.stream_id AS event_stream,e.aggregate_id AS event_aggregate,e.aggregate_type AS event_aggregate_type, "
        "e.producer_invocation_id AS event_invocation,e.producer_principal AS event_principal "
        "FROM relations r JOIN events e ON e.event_id=r.published_event_id "
        "WHERE json_extract(r.source_json,'$.version_id')=?", (str(ref.resource_version_id),)).fetchall()
    if len(rows) != 1:
        raise RegistryConflict("author material requires its one exact producer relation")
    relation = rows[0]
    endpoint = lambda value: {"entity_type": value.entity_type, "entity_id": str(value.entity_id), "version_id": str(value.version_id)}
    object_tx = db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (str(ref.resource_version_id),)).fetchone()[0]
    if (relation["relation_type"] != "produced_by" or relation["strength"] != "strong"
            or relation["transaction_id"] != object_tx or relation["event_criticality"] != "authoritative"
            or relation["event_stream"] != f"relation:{relation['relation_id']}"
            or relation["event_aggregate"] != relation["relation_id"]
            or relation["event_aggregate_type"] != "typed_relation/v1" or relation["event_invocation"] is not None
            or relation["event_principal"] != "framework"
            or relation["event_task"] != str(core.task_id) or relation["event_schema"] != "registry_v1/relation_published/v1"
            or not _same_json(json.loads(relation["source_json"]), endpoint(ref.as_version_ref()))
            or not _same_json(json.loads(relation["target_json"]), endpoint(bootstrap))
            or relation["event_type"] != "relation_published/v1" or relation["event_transaction"] != relation["transaction_id"]
            or not _same_json(json.loads(relation["payload_json"]), {"relation_id": relation["relation_id"], "relation_type": "produced_by",
                "source": endpoint(ref.as_version_ref()), "target": endpoint(bootstrap), "strength": "strong",
                "metadata": json.loads(relation["metadata_json"])})
            or not _canonical_closure(db, core.task_id, transaction_id=relation["transaction_id"],
                members=(("relation", relation["relation_id"]), ("event", relation["published_event_id"])))):
        raise RegistryConflict("author material producer relation lacks exact canonical authority")
    try:
        payload = readable_payload(core.object_store, prepared, media_type=media_type)
        document = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise RegistryConflict("author material payload must be JSON") from exc
    if isinstance(core, AuthorMaterialReadContext) and payload != canonical_json(document):
        raise RegistryConflict("author material requires canonical JSON bytes")
    return document, body


def _material(db, core, qualified, binding, schema_id):
    document, metadata = _private_document(db, core, qualified, binding)
    authority = metadata["content_schema_authority_ref"]
    if metadata["content_schema_ref"] != schema_id or not isinstance(authority, dict):
        raise RegistryConflict("author material requires its exact selected content schema authority")
    if schema_id in PROTECTED_SCHEMA_REFS:
        if set(authority) != {"entity_type", "logical_id", "version_id"} or authority["entity_type"] != "registry_type_catalog/v1":
            raise RegistryConflict("protected material schema requires its exact frozen catalog")
        bundle = exact_descriptor(db, core.object_store, core.task_id, authority)
        entry = bundle["schemas"].get(schema_id)
        if not isinstance(entry, dict) or entry.get("schema_id") != schema_id:
            raise RegistryConflict("protected material schema is absent from its catalog")
        schema = json.loads(entry["source"])
    else:
        if set(authority) != {"resource_id", "resource_version_id"}:
            raise RegistryConflict("application author material requires an exact schema resource")
        schema_ref = SourceQualifiedResourceRef.from_dict({"schema_version": "rpnh/collaboration/source_resource_ref/v1",
            "source_id": binding["source_id"], "ref": authority}, catalog=core.catalog)
        schema, _ = _private_document(db, core, schema_ref, binding, media_type="application/schema+json")
    expected = json.loads(core.catalog.schema_path(schema_id).read_text(encoding="utf-8"))
    if not _same_json(schema, expected):
        raise RegistryConflict("author material exact schema bytes differ from the supported contract")
    core.catalog.validate_schema_ref(schema_id, document)
    return document, metadata


def _command_material(*, source_id, owner, producer, command_id, parents, documents):
    return canonical_text({"schema_version": "rpnh/collaboration/closed_author_command/v1",
        "source_id": source_id, "owner_task_ref": owner.to_dict(), "producer_principal_ref": producer.to_dict(),
        "command_id": command_id, "parent_revision_refs": [ref.to_dict() for ref in parents],
        "documents": list(documents)})


@dataclass(frozen=True)
class ValidatedClosedRevision:
    revision: NetRevision
    module: ModuleDeclaration
    compiled: object
    element_map: dict
    boundary_map: dict
    host_requirements: dict


def _validate_at(db, core, reference, registration, binding, visiting, *, _assembly_v9_role=None):
    if reference in visiting:
        raise RegistryConflict("author material parent ancestry contains a cycle")
    visiting = visiting | {reference}
    if _assembly_v9_role is not None:
        from ._assembly_v9_lowering import require_history_role
        # Check every actual ancestor before any derived consumer or HOST lower.
        require_history_role(db, core, reference, binding, _assembly_v9_role)
    if reference.ref.entity_type == "collaboration_net_revision/v3":
        from .graph_merge import _read_revision_at
        from .graph_merge_author import _validate_graph_merge_at
        revision = _read_revision_at(db, core, reference, binding)
        return _validate_graph_merge_at(db, core, revision, registration, binding, visiting)
    if reference.ref.entity_type == "collaboration_net_revision/v2":
        from .graph_authoring import _read_graph_revision_at
        revision = _read_graph_revision_at(db, core, reference, local_source_id=binding["source_id"])
    else:
        revision = _read_net_revision_at(db, core, reference, local_source_id=binding["source_id"])
    if revision.definition_kind != "closed_module" or revision.open_region_contract_ref is not None:
        raise ValueError("this material consumer supports only already closed_module definitions")
    if reference.ref.entity_type == NET_REVISION_TYPE:
        # Dispatch on canonical prepared metadata only. The selected branch
        # still consumes actual definition bytes/schema through _material.
        prepared = exact_prepared(db, core.object_store, core.task_id, {
            "entity_type": "resource_version/v1", "logical_id": str(revision.definition_ref.ref.resource_id),
            "version_id": str(revision.definition_ref.ref.resource_version_id)})
        markers = {key for key in prepared.metadata["descriptors"]
                   if "_author_command_" in key or "_author_transform_command_" in key}
        from .plain_merge_result import RESULT_MARKER, _validate_merge_at
        if markers == {RESULT_MARKER}:
            return _validate_merge_at(db, core, revision, registration, binding, visiting)
        from .plain_transplant_result import RESULT_MARKER as TRANSPLANT_MARKER, _validate_transplant_at
        if markers == {TRANSPLANT_MARKER}:
            return _validate_transplant_at(db, core, revision, registration, binding, visiting)
        from .open_region_closure import CLOSURE_MARKER, _closure_at
        if markers == {CLOSURE_MARKER}:
            return _closure_at(db, core, revision, registration, binding, visiting)
        from .open_region_derived import MARKER as DERIVED_MARKER, _derived_at
        if markers == {DERIVED_MARKER}:
            return _derived_at(db, core, revision, registration, binding, visiting)
        from .plain_transform import MARKER as TRANSFORM_MARKER, _validate_transform_at
        if markers == {TRANSFORM_MARKER}:
            return _validate_transform_at(db, core, revision, registration, binding, visiting)
        from .plain_transplant_derived import MARKER as TRANSPLANT_DERIVED_MARKER, _derived_at as transplant_derived_at
        if markers == {TRANSPLANT_DERIVED_MARKER}:
            return transplant_derived_at(db, core, revision, registration, binding, visiting)
        if markers != {"closed_author_command_v1"}:
            raise RegistryConflict("unknown or dual closed author command markers")
    if len(revision.parent_revision_refs) > 1 or revision.selected_change_refs:
        raise ValueError("this material consumer supports root/single-parent history, without selected changes")
    if _assembly_v9_role is None:
        parent = None if not revision.parent_revision_refs else _validate_at(
            db, core, revision.parent_revision_refs[0], registration, binding, visiting)
        return _validate_materials_at(db, core, revision, registration, binding, parent)
    parent = None if not revision.parent_revision_refs else _validate_at(
        db, core, revision.parent_revision_refs[0], registration, binding, visiting,
        _assembly_v9_role=_assembly_v9_role)
    return _validate_materials_at(db, core, revision, registration, binding, parent,
        _assembly_v9_role=_assembly_v9_role)


def _validate_materials_at(db, core, revision, registration, binding, parent, *, _assembly_v9_role=None):
    from .plain_transform import ValidatedPlainTransformRevision
    if isinstance(parent, ValidatedPlainTransformRevision):
        raise RegistryConflict("unsupported_contract: legacy author cannot erase a transform parent's proof")
    if parent is not None and getattr(parent, "transplant_proof", False):
        raise RegistryConflict("unsupported_contract: legacy author cannot consume a transplant parent")
    if parent is not None and (getattr(parent, "adaptation_claim", None) is not None
            or getattr(parent, "historical_origins_ref", None) is not None):
        raise RegistryConflict("unsupported_contract: legacy author cannot erase an adapted parent's proof")
    if revision.owner_task_ref.to_dict()["ref"] != binding["task_ref"]:
        raise RegistryConflict("author material revision owner differs from the bound owner version")
    for qualified, kind in ((revision.owner_task_ref, "task"), (revision.producer_principal_ref, "principal")):
        ref = qualified.to_dict()["ref"]
        authority = exact_descriptor(db, core.object_store, core.task_id, ref)
        if authority[f"{kind}_id"] != ref["logical_id"] or authority[f"{kind}_version_id"] != ref["version_id"]:
            raise RegistryConflict("author producer/owner self-identity differs")
    proof = None
    if revision.revision_ref.ref.entity_type == "collaboration_net_revision/v2":
        from .graph_authoring import _read_graph_proof
        proof = _read_graph_proof(db, core, revision, binding, parent)
    document, definition_metadata = _material(db, core, revision.definition_ref, binding, MODULE_SCHEMA)
    if proof is not None and not _same_json(document, proof[3].to_dict()):
        raise RegistryConflict("graph definition differs from the complete source/recipe reconstruction")
    module = ModuleDeclaration.from_dict(document)
    if _assembly_v9_role is not None:
        from ._assembly_v9_lowering import require_module_role
        require_module_role(module, _assembly_v9_role)
    if proof is not None:
        from .graph_authoring import _ordinary_host_selection
        _ordinary_host_selection(module, registration)
    if _assembly_v9_role is None:
        compiled = compile_module(module, registration)
    else:
        from ..compiler import _compile_module_offline
        compiled = _compile_module_offline(module, registration)
    element_map, _ = _material(db, core, revision.element_mapping_ref, binding, ELEMENT_SCHEMA)
    boundary_map, _ = _material(db, core, revision.boundary_mapping_ref, binding, BOUNDARY_SCHEMA)
    host, _ = _material(db, core, revision.host_requirements_ref, binding, HOST_SCHEMA)
    ids = {item["locator"]: item["element_id"] for item in element_map["elements"]}
    copies = {}
    for item in element_map["elements"]:
        copied = item["copied_from"]
        if copied is not None:
            if parent is None or copied["revision_ref"] != parent.revision.revision_ref.to_dict():
                raise RegistryConflict("copy provenance must pin the exact selected parent revision")
            copies[item["element_id"]] = copied["element_id"]
    if (not _same_json(element_map, _element_map(module, ids, parent, copies))
            or not _same_json(boundary_map, _boundaries(module, element_map))):
        raise RegistryConflict("author element or boundary map differs from the actual Module")
    if not _same_json(host["registrations"], compiled.to_dict()["registrations"]):
        raise RegistryConflict("author HOST requirements differ from actual compile-consumed declarations")
    expected_keys = {(kind, key) for kind, entries in host["registrations"].items() for key in entries}
    actual_keys = [(item["kind"], item["key"]) for item in host["declaration_refs"]]
    if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != expected_keys:
        raise RegistryConflict("author HOST exact declaration refs are incomplete or duplicated")
    for item in host["declaration_refs"]:
        kind, key = item["kind"], item["key"]
        qualified = SourceQualifiedResourceRef.from_dict(item["resource_ref"], catalog=core.catalog)
        declared, _ = _private_document(db, core, qualified, binding,
            media_type="application/schema+json" if kind == "schema" else "application/json")
        expected = host["registrations"][kind][key]
        if not _same_json(declared, expected["schema"] if kind == "schema" else expected):
            raise RegistryConflict("author HOST exact declaration bytes differ")
    if proof is not None:
        from .graph_authoring import _finish_graph_validation
        return _finish_graph_validation(core, revision, parent, binding, proof, module, compiled, element_map, boundary_map, host)
    command_material = _command_material(source_id=binding["source_id"], owner=revision.owner_task_ref,
        producer=revision.producer_principal_ref, command_id=revision.command_id, parents=revision.parent_revision_refs,
        documents=(module.to_dict(), element_map, boundary_map, host))
    if definition_metadata["descriptors"].get("closed_author_command_v1") != command_material:
        raise RegistryConflict("closed author material differs from its immutable complete command")
    return ValidatedClosedRevision(revision, module, compiled, element_map, boundary_map, host)


def validate_closed_revision(core, reference, registration):
    """Explicit trusted HOST material consumer, not descriptor type decoding."""
    if not isinstance(registration, Registration):
        raise TypeError("material validation requires an explicit trusted HOST Registration")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return _validate_at(db, core, reference, registration, _binding(db, core), set())


class ClosedModuleAuthor:
    """Configured by the trusted owner; input principal data grants no access."""

    def __init__(self, gateway, registration, producer_principal_ref):
        from ..registry.registration_gateway import RegistryRegistrationGateway
        if not isinstance(gateway, RegistryRegistrationGateway) or not isinstance(registration, Registration):
            raise TypeError("closed author requires the existing trusted owner gateway and Registration")
        self.core, self.gateway, self.registration = gateway._core, gateway, registration
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            self.binding = _binding(db, self.core)
            if (gateway._task_ref != _version_from_payload(self.binding["task_ref"])
                    or gateway._bootstrap_ref != _version_from_payload(self.binding["bootstrap_command_ref"])):
                raise RegistryConflict("author gateway differs from the bound exact owner registration")
            if (not isinstance(producer_principal_ref, SourceQualifiedVersionRef)
                    or producer_principal_ref.source_id != self.binding["source_id"]
                    or producer_principal_ref.ref.entity_type != "principal/v1"):
                raise TypeError("HOST author configuration requires one local exact producer principal")
            principal = exact_descriptor(db, self.core.object_store, self.core.task_id, producer_principal_ref.to_dict()["ref"])
            if (principal["principal_id"] != str(producer_principal_ref.ref.entity_id)
                    or principal["principal_version_id"] != str(producer_principal_ref.ref.version_id)):
                raise RegistryConflict("configured author producer principal self-identity differs")
        self.producer = producer_principal_ref
        registration.bind_schema_catalog(self.core.catalog)
        registration.bind_gateway(gateway)
        self.schemas = {}
        for schema_id in (MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA):
            document = json.loads(self.core.catalog.schema_path(schema_id).read_text(encoding="utf-8"))
            self.schemas[schema_id] = (None if schema_id in PROTECTED_SCHEMA_REFS
                else gateway("schema", schema_id, {"kind": "schema", "key": schema_id, "schema": document}))

    def _compile(self, module):
        return compile_module(module, self.registration)

    def _validate(self, reference):
        return validate_closed_revision(self.core, reference, self.registration)

    def _validate_materials(self, db, revision, binding, parent):
        return _validate_materials_at(db, self.core, revision, self.registration, binding, parent)

    def publish(self, *, module, element_ids, command_id, parent_ref=None, copy_sources=None):
        if not isinstance(module, ModuleDeclaration):
            raise TypeError("author publication requires an already closed ModuleDeclaration")
        if (not isinstance(command_id, str) or not command_id or command_id != command_id.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in command_id)):
            raise ValueError("author command_id must be canonical")
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("author publisher uses a stale owner writer")
        # Validation and compilation happen before preparing any revision bytes.
        parent = None if parent_ref is None else self._validate(parent_ref)
        from .plain_transform import ValidatedPlainTransformRevision
        if isinstance(parent, ValidatedPlainTransformRevision):
            raise RegistryConflict("unsupported_contract: legacy author cannot erase a transform parent's proof")
        if parent is not None and getattr(parent, "transplant_proof", False):
            raise RegistryConflict("unsupported_contract: legacy author cannot consume a transplant parent")
        if parent is not None and (getattr(parent, "adaptation_claim", None) is not None
            or getattr(parent, "historical_origins_ref", None) is not None):
            raise RegistryConflict("unsupported_contract: legacy author cannot erase an adapted parent's proof")
        if parent is not None and parent.revision.revision_ref.ref.entity_type != NET_REVISION_TYPE:
            raise ValueError("legacy author v1 cannot downgrade a graph source-authoritative parent")
        element_map = _element_map(module, dict(element_ids), parent, dict(copy_sources or {}))
        boundary_map = _boundaries(module, element_map)
        compiled = self._compile(module)
        registrations = compiled.to_dict()["registrations"]
        declaration_refs = []
        for kind, entries in sorted(registrations.items()):
            for key, declaration in sorted(entries.items()):
                ref = self.gateway.declaration_refs.get((kind, key))
                if ref is None:
                    ref = self.gateway.bind_builtin_schema(key) if kind == "schema" and key in PROTECTED_SCHEMA_REFS else self.gateway(kind, key, declaration)
                declaration_refs.append({"kind": kind, "key": key,
                    "resource_ref": SourceQualifiedResourceRef(self.binding["source_id"], ref).to_dict()})
        host = {"schema_version": HOST_SCHEMA, "registrations": registrations, "declaration_refs": declaration_refs}
        documents = (module.to_dict(), element_map, boundary_map, host)
        schemas = (MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA)
        for schema, document in zip(schemas, documents):
            self.core.catalog.validate_schema_ref(schema, document)
        key = "collaboration-author:" + canonical_text({"command_id": command_id})
        stable = lambda kind: TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
            "scope": key, "task": str(self.core.task_id), "source": self.binding["source_id"], "kind": kind})).hex)
        reference = SourceQualifiedVersionRef(self.binding["source_id"], VersionRef(NET_REVISION_TYPE,
            stable("resource") if parent is None else parent.revision.revision_ref.ref.entity_id, stable("resource_version")))
        existing = self.core.event_store.object_row(reference.ref.version_id)
        if existing is not None:
            if existing["object_type"] != NET_REVISION_TYPE or existing["logical_id"] != str(reference.ref.entity_id):
                raise RegistryConflict("author command conflicts with original revision identity")
            prior = self._validate(reference)
            if (prior.revision.command_id != command_id or prior.revision.producer_principal_ref != self.producer
                    or prior.revision.parent_revision_refs != (() if parent is None else (parent_ref,))
                    or not _same_json((prior.module.to_dict(), prior.element_map, prior.boundary_map, prior.host_requirements), documents)):
                raise RegistryConflict("author command conflicts with original immutable material")
            return prior
        command_material = _command_material(source_id=self.binding["source_id"],
            owner=SourceQualifiedVersionRef(self.binding["source_id"], self.gateway._task_ref), producer=self.producer,
            command_id=command_id, parents=() if parent is None else (parent_ref,), documents=documents)
        refs = []
        try:
            for index, (schema, document) in enumerate(zip(schemas, documents)):
                ref = _publish_private_system(self.core, self.gateway._task_ref,
                    PublishResource(origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(document),
                        media_type="application/json", content_schema_ref=schema, content_schema_authority_ref=self.schemas[schema],
                        summary=f"Closed author material {index}", lifetime_ref=self.gateway._bootstrap_ref,
                        descriptors={"closed_author_command_v1": command_material} if index == 0 else {},
                        idempotency_key=f"{key}:material:{index}"))
                refs.append(SourceQualifiedResourceRef(self.binding["source_id"], ref))
            revision = NetRevision(reference, SourceQualifiedVersionRef(self.binding["source_id"], self.gateway._task_ref),
                self.producer, command_id, "closed_module", refs[0], () if parent is None else (parent_ref,), (),
                refs[1], refs[2], refs[3], None)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                self._validate_materials(db, revision, _binding(db, self.core), parent)
            from .public_projections import publish_public_projection
            publish_public_projection(self, ValidatedClosedRevision(revision, module, compiled,
                element_map, boundary_map, host), parent=parent)
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=NET_REVISION_TYPE, logical_id=reference.ref.entity_id, version_id=reference.ref.version_id,
                payload=canonical_json(revision.to_dict()), metadata=revision.to_dict(), media_type="application/json", schema_ref=NET_REVISION_SCHEMA)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("author command conflicts with immutable prepared material") from exc
        return self._validate(reference)


@dataclass(frozen=True, slots=True)
class AuthorMaterialReadContext:
    """The fixed pure consumer's read-only dependencies; no writer or HOST API."""
    object_store: object
    catalog: object
    task_id: TypedId
    branch_id: str


class _HostDeclarationClosure:
    """One material-read invocation's exact shared declarations, never global."""
    __slots__ = ("declarations",)

    def __init__(self):
        self.declarations = {}

    def observe(self, inventory):
        for category, declarations in inventory.items():
            for key, declaration in declarations.items():
                identity, value = (category, key), canonical_json(declaration)
                if identity in self.declarations and self.declarations[identity] != value:
                    raise RegistryConflict("shared HOST declaration differs across exact material closure")
                self.declarations[identity] = value


def _requires_graph_source_rebuild(module):
    """Finite known graph-authoritative versions, not a general graph detector."""
    return any(component.key in {f"rpnh/agent-workflow-graph/v{version}" for version in range(1, 5)}
               for component in module.components)


def _author_command_key(command_id):
    if (not isinstance(command_id, str) or not command_id or command_id != command_id.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in command_id)):
        raise ValueError("author command_id must be canonical")
    return "collaboration-author:" + canonical_text({"command_id": command_id})


def _author_revision_ref(core, source_id, command_id, parent):
    key = _author_command_key(command_id)
    def stable(kind):
        return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
            "scope": key, "task": str(core.task_id), "source": source_id, "kind": kind})).hex)
    return SourceQualifiedVersionRef(source_id, VersionRef(NET_REVISION_TYPE,
        stable("resource") if parent is None else parent.revision.revision_ref.ref.entity_id,
        stable("resource_version")))


def _validate_source_materials_at(db, core, revision, binding, parent, *, _host_closure=None):
    if revision.owner_task_ref.to_dict()["ref"] != binding["task_ref"]:
        raise RegistryConflict("author material revision owner differs from the bound owner version")
    for qualified, kind in ((revision.owner_task_ref, "task"), (revision.producer_principal_ref, "principal")):
        ref = qualified.to_dict()["ref"]
        authority = exact_descriptor(db, core.object_store, core.task_id, ref)
        if authority[f"{kind}_id"] != ref["logical_id"] or authority[f"{kind}_version_id"] != ref["version_id"]:
            raise RegistryConflict("author producer/owner self-identity differs")
    document, definition_metadata = _material(db, core, revision.definition_ref, binding, MODULE_SCHEMA)
    module = ModuleDeclaration.from_dict(document)
    element_map, _ = _material(db, core, revision.element_mapping_ref, binding, ELEMENT_SCHEMA)
    boundary_map, _ = _material(db, core, revision.boundary_mapping_ref, binding, BOUNDARY_SCHEMA)
    host, _ = _material(db, core, revision.host_requirements_ref, binding, HOST_SCHEMA)
    ids = {item["locator"]: item["element_id"] for item in element_map["elements"]}
    copies = {}
    for item in element_map["elements"]:
        copied = item["copied_from"]
        if copied is not None:
            if parent is None or copied["revision_ref"] != parent.revision.revision_ref.to_dict():
                raise RegistryConflict("copy provenance must pin the exact selected parent revision")
            copies[item["element_id"]] = copied["element_id"]
    if (not _same_json(element_map, _element_map(module, ids, parent, copies))
            or not _same_json(boundary_map, _boundaries(module, element_map))):
        raise RegistryConflict("author element or boundary map differs from the actual Module")
    expected_keys = {(kind, key) for kind, entries in host["registrations"].items() for key in entries}
    actual_keys = [(item["kind"], item["key"]) for item in host["declaration_refs"]]
    if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != expected_keys:
        raise RegistryConflict("author HOST exact declaration refs are incomplete or duplicated")
    for item in host["declaration_refs"]:
        kind, key = item["kind"], item["key"]
        qualified = SourceQualifiedResourceRef.from_dict(item["resource_ref"], catalog=core.catalog)
        declared, _ = _private_document(db, core, qualified, binding,
            media_type="application/schema+json" if kind == "schema" else "application/json")
        expected = host["registrations"][kind][key]
        if not _same_json(declared, expected["schema"] if kind == "schema" else expected):
            raise RegistryConflict("author HOST exact declaration bytes differ")
    command_material = _command_material(source_id=binding["source_id"], owner=revision.owner_task_ref,
        producer=revision.producer_principal_ref, command_id=revision.command_id, parents=revision.parent_revision_refs,
        documents=(module.to_dict(), element_map, boundary_map, host))
    if definition_metadata["descriptors"].get("closed_author_command_v1") != command_material:
        raise RegistryConflict("closed author material differs from its immutable complete command")
    if revision.revision_ref != _author_revision_ref(core, binding["source_id"], revision.command_id, parent):
        raise RegistryConflict("author result identity differs from its immutable complete command")
    bootstrap = _version_from_payload(binding["bootstrap_command_ref"])
    key = _author_command_key(revision.command_id)
    for index, qualified in enumerate((revision.definition_ref, revision.element_mapping_ref,
            revision.boundary_mapping_ref, revision.host_requirements_ref)):
        material_key = f"{key}:material:{index}"
        expected = ResourceVersionRef(
            fresh_bootstrap_resource_id(core.task_id, bootstrap.version_id, material_key),
            _resource_stable_id("resource_version", core.task_id, material_key, "fresh_bootstrap_reference",
                bootstrap.version_id, bootstrap.version_id))
        if qualified.ref != expected:
            raise RegistryConflict("author material is not the original immutable command resource")
    if _host_closure is not None:
        _host_closure.observe(host["registrations"])
    return ValidatedClosedRevision(revision, module, None, element_map, boundary_map, host)


def _match_author_compiled(material, compiled):
    """Bind complete pure materials to an independently verified compiled wire."""
    if (not _same_json(material.module.to_dict(), compiled.source.to_dict())
            or not _same_json(material.host_requirements["registrations"], compiled.to_dict()["registrations"])):
        raise RegistryConflict("author source/HOST requirements differ from actual compile-consumed declarations")
    return replace(material, compiled=compiled)


def _validate_source_at(db, context, reference, binding, visiting, *, _host_closure=None):
    """Exact closed-v1 static closure; ordinary/typed author dispatch stays separate."""
    if _host_closure is None:
        _host_closure = _HostDeclarationClosure()
    if reference in visiting:
        raise RegistryConflict("author material parent ancestry contains a cycle")
    visiting = visiting | {reference}
    revision = _read_net_revision_at(db, context, reference, local_source_id=binding["source_id"])
    if revision.definition_kind != "closed_module" or revision.open_region_contract_ref is not None:
        raise ValueError("this material consumer supports only already closed_module definitions")
    if len(revision.parent_revision_refs) > 1 or revision.selected_change_refs:
        raise ValueError("this material consumer supports root/single-parent history, without selected changes")
    parent = None if not revision.parent_revision_refs else _validate_source_at(
        db, context, revision.parent_revision_refs[0], binding, visiting, _host_closure=_host_closure)
    return _validate_source_materials_at(db, context, revision, binding, parent, _host_closure=_host_closure)
