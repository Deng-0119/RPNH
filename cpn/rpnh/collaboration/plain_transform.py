"""Caller-explicit ordinary author identity history, never business equivalence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import uuid

from ..compiler import compile_module
from ..module import ModuleDeclaration
from ..registry._event_store.collaboration_descriptors import exact_descriptor, exact_prepared, readable_payload
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.publication import _version_from_payload
from ..registry.resource_service import _publish_private_system
from ..registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from ..registry.schema_catalog import canonical_json, canonical_text
from .assemblies import _command as _check_command
from .assembly_v2 import _binding_at, _material_ref, _host_at
from .authoring import NetRevision, NET_REVISION_TYPE, NET_REVISION_SCHEMA, _revision_ref
from .materials import (
    ClosedModuleAuthor, ValidatedClosedRevision, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA,
    _ELEMENT_ID, _element_map, _boundaries, _material, _same_json,
)
from .plain_merge_result import _resource_bytes, _schema_authority_at, _metadata
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef

MAP_SCHEMA = "rpnh/collaboration/plain_author_transform_map/v1"
COMMAND_SCHEMA = "rpnh/collaboration/plain_author_transform_command/v1"
MARKER = "plain_author_transform_command_v1"
CONTRACT = "rpnh/plain_author_identity_transform/v1"
SCHEMAS = (MAP_SCHEMA, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA)
ROLES = ("transformation", "definition", "element_mapping", "boundary_mapping", "host_requirements")
_FIELDS = ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")


def _key(command_id):
    _check_command(command_id)
    return "collaboration-plain-transform:" + canonical_text({"command_id": command_id})


def _plain_module(module):
    if module.designer_constraints or any(
            c.key in {f"rpnh/agent-workflow-graph/v{n}" for n in range(1, 5)} for c in module.components):
        raise RegistryConflict("unsupported_contract: transform requires an ordinary unconstrained plain Module")


def _ids(values, label):
    if (not isinstance(values, list) or any(not isinstance(i, str) or _ELEMENT_ID.fullmatch(i) is None for i in values)
            or values != sorted(set(values))):
        raise ValueError(label + " must be a sorted unique list of canonical element IDs")
    return set(values)


def _mapping(module, ids, parent, historical_ids, request):
    copies = request["copy_sources"]
    elements = _element_map(module, ids, parent, copies)
    old = {row["element_id"]: row for row in parent.element_map["elements"]}
    current = {row["element_id"]: row for row in elements["elements"]}
    retained = set(old) & set(current)
    fresh = set(current) - retained
    if fresh & historical_ids:
        raise ValueError("new element identities cannot reuse any verified historical identity")
    groups = request["transform_groups"]
    if not isinstance(groups, list) or not groups or groups != sorted(groups, key=canonical_text):
        raise ValueError("transform groups must be a nonempty canonically sorted list")
    sources, targets = set(), set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"kind", "source_revision_ref", "source_element_ids", "target_element_ids"}:
            raise ValueError("transform group requires its complete exact contract")
        if group["source_revision_ref"] != parent.revision.revision_ref.to_dict():
            raise ValueError("transform group must name the exact selected source revision")
        left = _ids(group["source_element_ids"], "transform sources")
        right = _ids(group["target_element_ids"], "transform targets")
        if not ((group["kind"] == "split" and len(left) == 1 and len(right) >= 2)
                or (group["kind"] == "fusion" and len(left) >= 2 and len(right) == 1)):
            raise ValueError("transform kind/cardinality must be one-to-many split or many-to-one fusion")
        if not left <= set(old) or not right <= fresh or left & retained:
            raise ValueError("transform needs exact retired parent sources and fresh current targets")
        if left & sources or right & targets:
            raise ValueError("transform groups cannot overlap source or target identities")
        if len({old[i]["kind"] for i in left} | {current[i]["kind"] for i in right}) != 1:
            raise ValueError("transform source and target elements must have the same kind")
        sources.update(left)
        targets.update(right)
    created = _ids(request["created_element_ids"], "created identities")
    removed = _ids(request["removed_element_ids"], "removed identities")
    copied = set(copies)
    if (targets & copied or targets & created or copied & created
            or (targets | copied | created) != fresh):
        raise ValueError("current identity partition must be total, disjoint retained/transformed/copied/created")
    if sources & removed or (retained | sources | removed) != set(old) or removed & retained:
        raise ValueError("source identity partition must be total, disjoint retained/transformed/removed")
    result = {"schema_version": MAP_SCHEMA, "contract": CONTRACT,
        "source_revision_ref": parent.revision.revision_ref.to_dict(),
        "retained_element_ids": sorted(retained), "transform_groups": deepcopy(groups),
        "copy_sources": deepcopy(copies), "created_element_ids": sorted(created),
        "removed_element_ids": sorted(removed)}
    return result, elements


def _input_pins_at(db, core, binding, history, producer):
    """Lock exact history, HOST and material schema dependencies, without copying their payloads."""
    refs = {}
    def add(ref):
        refs[canonical_text(ref)] = ref
    def obj(qualified):
        add(qualified.to_dict()["ref"])
    def resource(qualified):
        add({"entity_type": "resource_version/v1", "logical_id": str(qualified.ref.resource_id),
             "version_id": str(qualified.ref.resource_version_id)})
    for field in ("task_ref", "bootstrap_command_ref"):
        add(binding[field])
    obj(producer)
    for value in history:
        obj(value.revision.revision_ref)
        obj(value.revision.owner_task_ref)
        obj(value.revision.producer_principal_ref)
        for field in _FIELDS:
            resource(getattr(value.revision, field))
        for item in value.host_requirements["declaration_refs"]:
            resource(SourceQualifiedResourceRef.from_dict(item["resource_ref"], catalog=core.catalog))
        if type(value) is ValidatedPlainTransformRevision:
            resource(value.command_ref)
            resource(value.transform_map_ref)
    pins, done = [], set()
    while set(refs) - done:
        key = min(set(refs) - done)
        done.add(key)
        ref = refs[key]
        prepared = exact_prepared(db, core.object_store, core.task_id, ref)
        media = prepared.metadata.get("media_type", "application/json") if ref["entity_type"] == "resource_version/v1" else "application/json"
        raw = readable_payload(core.object_store, prepared, media_type=media)
        pins.append({"ref": deepcopy(ref), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                     "metadata_sha256": hashlib.sha256(canonical_json(prepared.metadata)).hexdigest()})
        authority = prepared.metadata.get("content_schema_authority_ref") if ref["entity_type"] == "resource_version/v1" else None
        if authority:
            add(authority if "entity_type" in authority else {"entity_type": "resource_version/v1",
                "logical_id": authority["resource_id"], "version_id": authority["resource_version_id"]})
    return sorted(pins, key=lambda pin: canonical_text(pin["ref"]))


def _history_at(db, core, parent_ref, registration, binding, visiting):
    from .materials import _validate_at
    from .plain_merge import _plain
    history, seen = [], set()
    current = parent_ref
    while current is not None:
        if current in seen or current in visiting:
            raise RegistryConflict("transform history contains an active ancestry cycle")
        seen.add(current)
        value = _validate_at(db, core, current, registration, binding, visiting)
        if type(value) not in (ValidatedClosedRevision, ValidatedPlainTransformRevision):
            raise RegistryConflict("unsupported_contract: transform history excludes other author proof families")
        _plain_module(value.module)
        if type(value) is ValidatedClosedRevision:
            _plain(value, core, binding)  # Genuine canonical ordinary producer, not descriptor-shaped input.
        revision = value.revision
        if (revision.owner_task_ref.to_dict()["ref"] != binding["task_ref"]
                or revision.revision_ref.source_id != binding["source_id"]
                or revision.selected_change_refs or len(revision.parent_revision_refs) > 1):
            raise RegistryConflict("unsupported_contract: transform needs same-owner plain single-parent history")
        if revision.revision_ref.ref.entity_id != parent_ref.ref.entity_id:
            raise RegistryConflict("transform history cannot change its author lineage identity")
        history.append(value)
        current = revision.parent_revision_refs[0] if revision.parent_revision_refs else None
    return history


def _prepare_at(db, core, registration, binding, producer, command_id, request, authorities, visiting):
    key = _key(command_id)
    if set(request) != {"parent_ref", "module", "element_ids", "transform_groups", "copy_sources",
                       "created_element_ids", "removed_element_ids"}:
        raise ValueError("transform request requires all explicit identity dispositions")
    parent_ref = SourceQualifiedVersionRef.from_dict(request["parent_ref"], catalog=core.catalog)
    _revision_ref(parent_ref)
    history = _history_at(db, core, parent_ref, registration, binding, visiting)
    parent = history[0]
    if producer.source_id != binding["source_id"] or producer.ref.entity_type != "principal/v1":
        raise RegistryConflict("transform producer must be one exact local principal")
    principal = exact_descriptor(db, core.object_store, core.task_id, producer.to_dict()["ref"])
    if principal["principal_id"] != str(producer.ref.entity_id) or principal["principal_version_id"] != str(producer.ref.version_id):
        raise RegistryConflict("transform producer self-identity differs")
    module = ModuleDeclaration.from_dict(request["module"])
    _plain_module(module)
    historical_ids = {row["element_id"] for value in history for row in value.element_map["elements"]}
    mapping, elements = _mapping(module, request["element_ids"], parent, historical_ids, request)
    compiled = compile_module(module, registration)
    host = _host_at(db, core, binding, compiled)
    boundary = _boundaries(module, elements)
    version = TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": binding["source_id"], "kind": "resource_version"})).hex)
    reference = SourceQualifiedVersionRef(binding["source_id"], VersionRef(NET_REVISION_TYPE, parent_ref.ref.entity_id, version))
    if reference in {value.revision.revision_ref for value in history}:
        raise RegistryConflict("transform command cannot select itself as historical input")
    command_ref = _material_ref(core, binding, key + ":command")
    if set(authorities) != set((*SCHEMAS, COMMAND_SCHEMA)):
        raise RegistryConflict("transform command must lock every exact schema authority")
    digests = {schema: _schema_authority_at(db, core, binding, schema, authority) for schema, authority in authorities.items()}
    documents = (mapping, module.to_dict(), elements, boundary, host)
    specs = []
    for role, schema, body in zip(ROLES, SCHEMAS, documents, strict=True):
        core.catalog.validate_schema_ref(schema, body)
        selected = _material_ref(core, binding, key + ":" + role)
        metadata = _metadata(core, binding, selected, schema, authorities[schema], body,
            "Plain transform " + role, {MARKER: canonical_text(command_ref.to_dict())})
        specs.append({"role": role, "schema": schema, "resource_ref": selected.to_dict(), "document": body,
                      "metadata": metadata, "sha256": hashlib.sha256(canonical_json(body)).hexdigest()})
    owner = SourceQualifiedVersionRef(binding["source_id"], _version_from_payload(binding["task_ref"]))
    command = {"schema_version": COMMAND_SCHEMA, "contract": CONTRACT, "source_id": binding["source_id"],
        "source_binding": deepcopy(binding), "owner_task_ref": owner.to_dict(), "producer_principal_ref": producer.to_dict(),
        "command_id": command_id, "request": deepcopy(request), "schema_authorities": deepcopy(authorities),
        "schema_authority_sha256": digests, "input_pins": _input_pins_at(db, core, binding, history, producer),
        "result_revision_ref": reference.to_dict(), "parent_revision_refs": [parent_ref.to_dict()],
        "selected_change_refs": [], "prepared_materials": specs}
    core.catalog.validate_schema_ref(COMMAND_SCHEMA, command)
    refs = [SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog) for spec in specs]
    revision = NetRevision(reference, owner, producer, command_id, "closed_module", refs[1], (parent_ref,), (), refs[2], refs[3], refs[4], None)
    return command, revision, module, compiled, elements, boundary, host, mapping


@dataclass(frozen=True)
class ValidatedPlainTransformRevision(ValidatedClosedRevision):
    command_ref: SourceQualifiedResourceRef
    transform_map_ref: SourceQualifiedResourceRef
    transformation: dict


def _validate_transform_at(db, core, revision, registration, binding, visiting):
    binding = _binding_at(db, core)
    if len(revision.parent_revision_refs) != 1 or revision.selected_change_refs:
        raise RegistryConflict("transform requires exactly one parent and no selected changes")
    _, metadata = _material(db, core, revision.definition_ref, binding, MODULE_SCHEMA)
    try:
        if set(metadata["descriptors"]) != {MARKER}:
            raise ValueError("ambiguous transform marker")
        command_ref = SourceQualifiedResourceRef.from_dict(json.loads(metadata["descriptors"][MARKER]), catalog=core.catalog)
    except (ValueError, KeyError, TypeError) as exc:
        raise RegistryConflict("transform needs its unique exact command marker") from exc
    tag = (COMMAND_SCHEMA, command_ref)
    if tag in visiting:
        raise RegistryConflict("transform command has an active proof cycle")
    command, command_meta = _material(db, core, command_ref, binding, COMMAND_SCHEMA)
    producer = SourceQualifiedVersionRef.from_dict(command["producer_principal_ref"], catalog=core.catalog)
    expected, record, module, compiled, elements, boundary, host, mapping = _prepare_at(
        db, core, registration, binding, producer, command["command_id"], command["request"],
        command["schema_authorities"], visiting | {tag})
    expected_ref = _material_ref(core, binding, _key(command["command_id"]) + ":command")
    expected_meta = _metadata(core, binding, expected_ref, COMMAND_SCHEMA, command["schema_authorities"][COMMAND_SCHEMA],
        expected, "Plain transform complete command", {MARKER: COMMAND_SCHEMA})
    if (command_ref != expected_ref or _resource_bytes(db, core, command_ref) != canonical_json(expected)
            or not _same_json(command_meta, expected_meta) or not _same_json(revision.to_dict(), record.to_dict())):
        raise RegistryConflict("transform differs from its exact immutable complete command")
    for spec in expected["prepared_materials"]:
        ref = SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog)
        document, meta = _material(db, core, ref, binding, spec["schema"])
        if (_resource_bytes(db, core, ref) != canonical_json(spec["document"])
                or not _same_json(document, spec["document"]) or not _same_json(meta, spec["metadata"])):
            raise RegistryConflict("transform material differs from its complete command")
    return ValidatedPlainTransformRevision(revision, module, compiled, elements, boundary, host,
        command_ref, SourceQualifiedResourceRef.from_dict(expected["prepared_materials"][0]["resource_ref"], catalog=core.catalog), mapping)


class PlainModuleTransformAuthor:
    """Explicit complete-definition transforms; no inferred equivalence or runtime adoption."""
    def __init__(self, gateway, registration, producer_principal_ref):
        self.author = ClosedModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = self.author.core, gateway, registration
        self.binding, self.producer = self.author.binding, self.author.producer
        self.schemas = dict(self.author.schemas)
        for schema in (MAP_SCHEMA, COMMAND_SCHEMA):
            body = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": body})

    def publish(self, *, parent_ref, module, element_ids, transform_groups, copy_sources,
                created_element_ids, removed_element_ids, command_id):
        from .materials import validate_closed_revision
        key = _key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("transform author uses a stale owner writer")
        if not isinstance(module, ModuleDeclaration):
            raise TypeError("transform requires a complete closed ModuleDeclaration")
        _revision_ref(parent_ref)
        request = {"parent_ref": parent_ref.to_dict(), "module": module.to_dict(), "element_ids": dict(element_ids),
            "transform_groups": deepcopy(transform_groups), "copy_sources": dict(copy_sources),
            "created_element_ids": deepcopy(created_element_ids), "removed_element_ids": deepcopy(removed_element_ids)}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("transform owner source binding changed")
            rows = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='registry_type_catalog/v1'").fetchall()
            if len(rows) != 1:
                raise RegistryConflict("transform requires one exact frozen catalog")
            protected = {"entity_type": "registry_type_catalog/v1", "logical_id": rows[0]["logical_id"], "version_id": rows[0]["version_id"]}
            authorities = {schema: protected if ref is None else {"resource_id": str(ref.resource_id), "resource_version_id": str(ref.resource_version_id)}
                           for schema, ref in self.schemas.items()}
            command, revision, *_ = _prepare_at(db, self.core, self.registration, binding, self.producer,
                command_id, request, authorities, set())
        if self.core.event_store.object_row(revision.revision_ref.ref.version_id) is not None:
            prior = validate_closed_revision(self.core, revision.revision_ref, self.registration)
            if type(prior) is not ValidatedPlainTransformRevision:
                raise RegistryConflict("transform command conflicts with original proof family")
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                old, _ = _material(db, self.core, prior.command_ref, _binding_at(db, self.core), COMMAND_SCHEMA)
            if not _same_json(command, old) or revision != prior.revision:
                raise RegistryConflict("transform command conflicts with original complete request")
            return prior
        command_ref = _material_ref(self.core, binding, key + ":command")
        command_meta = _metadata(self.core, binding, command_ref, COMMAND_SCHEMA, authorities[COMMAND_SCHEMA], command,
            "Plain transform complete command", {MARKER: COMMAND_SCHEMA})
        specs = [{"role": "command", "schema": COMMAND_SCHEMA, "document": command,
                  "resource_ref": command_ref.to_dict(), "metadata": command_meta}, *command["prepared_materials"]]
        try:
            for spec in specs:
                meta = spec["metadata"]
                authority = meta["content_schema_authority_ref"]
                selected = (_version_from_payload(authority) if "entity_type" in authority else ResourceVersionRef(
                    TypedId.parse(authority["resource_id"], expected="resource"), TypedId.parse(authority["resource_version_id"], expected="resource_version")))
                ref = _publish_private_system(self.core, self.gateway._task_ref, PublishResource(
                    origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(spec["document"]),
                    media_type=meta["media_type"], content_schema_ref=spec["schema"], content_schema_authority_ref=selected,
                    summary=meta["summary"], descriptors=meta["descriptors"], extensions=meta["extensions"],
                    lifetime_ref=self.gateway._bootstrap_ref, idempotency_key=key + ":" + spec["role"]))
                if SourceQualifiedResourceRef(binding["source_id"], ref).to_dict() != spec["resource_ref"]:
                    raise RegistryConflict("transform prepared identity differs")
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                _validate_transform_at(db, self.core, revision, self.registration, _binding_at(db, self.core), {revision.revision_ref})
            from .public_projections import publish_public_projection, PRODUCER_CONTRACTS
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                projected = _validate_transform_at(db, self.core, revision, self.registration,
                    _binding_at(db, self.core), {revision.revision_ref})
            parent = validate_closed_revision(self.core, parent_ref, self.registration)
            publish_public_projection(self.author, projected, parent=parent,
                producer_contract=PRODUCER_CONTRACTS[1])
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=NET_REVISION_TYPE, logical_id=revision.revision_ref.ref.entity_id,
                version_id=revision.revision_ref.ref.version_id, payload=canonical_json(revision.to_dict()),
                metadata=revision.to_dict(), media_type="application/json", schema_ref=NET_REVISION_SCHEMA)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("transform conflicts with immutable complete command") from exc
        return validate_closed_revision(self.core, revision.revision_ref, self.registration)
