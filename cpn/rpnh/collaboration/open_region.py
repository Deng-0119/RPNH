"""Explicit open author roots and their exact immutable command consumer.

An open region is neither a Module nor a runtime candidate. Its source is
dedicated extraction provenance, never a fabricated history parent.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import uuid

from ..registration import Registration
from ..registry._event_store.collaboration_descriptors import exact_descriptor
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.publication import _version_from_payload
from ..registry.resource_service import _publish_private_system
from ..registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from ..registry.schema_catalog import canonical_json, canonical_text
from .authoring import NetRevision, NET_REVISION_SCHEMA, NET_REVISION_TYPE, _read_net_revision_at
from .assembly_v2 import _binding_at, _host_at, _material_ref
from .assemblies import _command as _check_command
from .materials import (ClosedModuleAuthor, ValidatedClosedRevision, MODULE_SCHEMA, ELEMENT_SCHEMA,
    BOUNDARY_SCHEMA, HOST_SCHEMA, _material, _same_json, _validate_at as _closed_at)
from .plain_merge_result import _metadata, _resource_bytes, _schema_authority_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from ._open_region_inventory import (INVENTORY_SCHEMA, element_identity, required_choices,
                                     scan, selected_components, signature)

PREFIX = "rpnh/collaboration/"
PROVENANCE_SCHEMA = PREFIX + "open_region_extraction_provenance/v1"
DEFINITION_SCHEMA = PREFIX + "open_region_definition/v1"
OPEN_ELEMENT_SCHEMA = PREFIX + "open_region_element_map/v1"
OPEN_HOST_SCHEMA = PREFIX + "open_region_host_requirements/v1"
CONTRACT_SCHEMA = PREFIX + "open_region_contract/v1"
COMMAND_SCHEMA = PREFIX + "open_region_author_command/v1"
MARKER = "open_region_author_command_v1"
ALGORITHM = PREFIX + "whole_operation_components/v1"
OPEN_MATERIALS = (("provenance", PROVENANCE_SCHEMA), ("definition", DEFINITION_SCHEMA),
    ("element_mapping", OPEN_ELEMENT_SCHEMA), ("boundary_mapping", INVENTORY_SCHEMA),
    ("host_requirements", OPEN_HOST_SCHEMA), ("open_contract", CONTRACT_SCHEMA))
SOURCE_MATERIALS = (("definition", MODULE_SCHEMA), ("element_mapping", ELEMENT_SCHEMA),
                    ("boundary_mapping", BOUNDARY_SCHEMA), ("host_requirements", HOST_SCHEMA))


def _key(command_id, *, closure=False):
    _check_command(command_id)
    prefix = "collaboration-open-region-closure:" if closure else "collaboration-open-region:"
    return prefix + canonical_text({"command_id": command_id})


def _result_ref(core, binding, command_id, *, closure=False):
    def stable(kind):
        return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
            "scope": _key(command_id, closure=closure), "task": str(core.task_id),
            "source": binding["source_id"], "kind": kind})).hex)
    return SourceQualifiedVersionRef(binding["source_id"], VersionRef(
        NET_REVISION_TYPE, stable("resource"), stable("resource_version")))


def _obj(core, value):
    return SourceQualifiedVersionRef.from_dict(value, catalog=core.catalog)


def _resource(core, value):
    return SourceQualifiedResourceRef.from_dict(value, catalog=core.catalog)


def _owner(core, binding):
    return SourceQualifiedVersionRef(binding["source_id"], _version_from_payload(binding["task_ref"]))


def _producer_at(db, core, binding, producer):
    if (not isinstance(producer, SourceQualifiedVersionRef) or producer.source_id != binding["source_id"]
            or producer.ref.entity_type != "principal/v1"):
        raise RegistryConflict("authority_mismatch: open author needs one exact local principal")
    ref = producer.to_dict()["ref"]
    body = exact_descriptor(db, core.object_store, core.task_id, ref)
    if body["principal_id"] != ref["logical_id"] or body["principal_version_id"] != ref["version_id"]:
        raise RegistryConflict("authority_mismatch: producer self identity differs")


def _pins_at(db, core, binding, value):
    pins = {}
    for role, schema in SOURCE_MATERIALS:
        reference = getattr(value.revision, role + "_ref")
        document, metadata = _material(db, core, reference, binding, schema)
        payload = _resource_bytes(db, core, reference)
        if payload != canonical_json(document):
            raise RegistryConflict("material_integrity_mismatch: source bytes must be canonical")
        _schema_authority_at(db, core, binding, schema, metadata["content_schema_authority_ref"])
        pins[role] = {"resource_ref": reference.to_dict(), "schema": schema,
            "schema_authority_ref": metadata["content_schema_authority_ref"], **signature(document)}
    return pins


def _source_at(db, core, registration, binding, reference, visiting):
    source = _closed_at(db, core, reference, registration, binding, visiting)
    if type(source) is not ValidatedClosedRevision:
        raise RegistryConflict("unsupported_contract: extraction requires an ordinary closed-v1 source")
    if source.revision.owner_task_ref != _owner(core, binding):
        raise RegistryConflict("authority_mismatch: source owner differs")
    # Upgrade the old ordinary material boundary to exact HOST metadata/bytes
    # in this new proof. Lowering only observes already registered declarations.
    if not _same_json(source.host_requirements, _host_at(db, core, binding, source.compiled)):
        raise RegistryConflict("authority_mismatch: source HOST selection differs")
    return source


def _prepare_specs(core, binding, key, marker, documents, authorities):
    command_ref = _material_ref(core, binding, key + ":command")
    specs = []
    for role, schema, body in documents:
        core.catalog.validate_schema_ref(schema, body)
        reference = _material_ref(core, binding, key + ":" + role)
        metadata = _metadata(core, binding, reference, schema, authorities[schema], body,
            "Open region " + role, {marker: canonical_text(command_ref.to_dict())})
        specs.append({"role": role, "schema": schema, "resource_ref": reference.to_dict(),
            "document": body, "metadata": metadata, **signature(body)})
    return specs


def _command_document(db, core, binding, producer, command_id, request, authorities,
                      reference, specs, schema, algorithm, expected_schemas):
    if set(authorities) != set(expected_schemas):
        raise RegistryConflict("authority_mismatch: command must lock the exact schema set")
    digests = {key: _schema_authority_at(db, core, binding, key, value) for key, value in authorities.items()}
    command = {"schema_version": schema, "algorithm": algorithm, "source_id": binding["source_id"],
        "owner_task_ref": _owner(core, binding).to_dict(), "producer_principal_ref": producer.to_dict(),
        "command_id": command_id, "request": deepcopy(request), "schema_authorities": deepcopy(authorities),
        "schema_authority_sha256": digests, "result_revision_ref": reference.to_dict(),
        "parent_revision_refs": [], "selected_change_refs": [], "prepared_materials": specs}
    core.catalog.validate_schema_ref(schema, command)
    return command


def _prepare_open_at(db, core, registration, binding, producer, command_id, request, authorities, visiting):
    _producer_at(db, core, binding, producer)
    if set(request) != {"source_revision_ref", "selection", "lineage_mode"} or request["lineage_mode"] != "new_lineage":
        raise ValueError("invalid_selection: explicit new_lineage and exact source/selection are required")
    source = _source_at(db, core, registration, binding, _obj(core, request["source_revision_ref"]), visiting)
    selection = request["selection"]
    names = selected_components(source, selection)
    key, reference = _key(command_id), _result_ref(core, binding, command_id)
    provenance_ref = _material_ref(core, binding, key + ":provenance")
    provenance = {"schema_version": PROVENANCE_SCHEMA, "lineage_mode": "new_lineage",
        "source_revision_ref": source.revision.revision_ref.to_dict(), "source_materials": _pins_at(db, core, binding, source),
        "selection": deepcopy(selection), "source_compile_signature": signature(source.compiled.to_dict()),
        "projection_recipe": "whole_operation_components/v1"}
    document = source.module.to_dict()
    retained_links = [(index, row) for index, row in enumerate(document["links"])
                      if row["source"]["component"] in names and row["target"]["component"] in names]
    definition = {"schema_version": DEFINITION_SCHEMA, "provenance_ref": provenance_ref.to_dict(),
        "components": [row for row in document["components"] if row["name"] in names],
        "internal_links": [row for _, row in retained_links],
        "source_public_boundaries": {field: {name: row for name, row in document[field].items()
            if row["component"] in names} for field in ("entry", "exit")}, "completion": None}
    elements = []
    link_indices = {index for index, _ in retained_links}
    for row in source.element_map["elements"]:
        locator = row["locator"]
        selected = (locator.startswith("/components/") and locator.split("/")[2] in names)
        selected |= locator.startswith("/links/") and int(locator.split("/")[2]) in link_indices
        if not selected:
            continue
        origin = {"source_revision_ref": source.revision.revision_ref.to_dict(), "source_element_id": row["element_id"]}
        elements.append({"element_id": element_identity(reference.ref.entity_id, row["kind"], origin),
            "kind": row["kind"], "locator": locator, **origin})
    elements.sort(key=lambda row: row["locator"])
    element_map = {"schema_version": OPEN_ELEMENT_SCHEMA, "provenance_ref": provenance_ref.to_dict(), "elements": elements}
    inventory = scan(source, selection, provenance_ref)
    host = {"schema_version": OPEN_HOST_SCHEMA, "provenance_ref": provenance_ref.to_dict(),
        "source_host_requirements_ref": source.revision.host_requirements_ref.to_dict(),
        "declaration_pins": deepcopy(source.host_requirements), "scope": "conservative_source_support"}
    choices = required_choices(inventory)
    contract = {"schema_version": CONTRACT_SCHEMA, "provenance_ref": provenance_ref.to_dict(),
        "boundary_inventory_ref": _material_ref(core, binding, key + ":boundary_mapping").to_dict(),
        "unresolved_item_ids": [row["item_id"] for row in choices], "required_choices": choices, "claim": "open_only"}
    bodies = (provenance, definition, element_map, inventory, host, contract)
    specs = _prepare_specs(core, binding, key, MARKER,
        [(role, schema, body) for (role, schema), body in zip(OPEN_MATERIALS, bodies, strict=True)], authorities)
    command = _command_document(db, core, binding, producer, command_id, request, authorities, reference, specs,
        COMMAND_SCHEMA, ALGORITHM, [COMMAND_SCHEMA, *[schema for _, schema in OPEN_MATERIALS],
                                   *[schema for _, schema in SOURCE_MATERIALS]])
    refs = {spec["role"]: _resource(core, spec["resource_ref"]) for spec in specs}
    revision = NetRevision(reference, _owner(core, binding), producer, command_id, "open_region", refs["definition"],
        (), (), refs["element_mapping"], refs["boundary_mapping"], refs["host_requirements"], refs["open_contract"])
    return command, revision, source, bodies


@dataclass(frozen=True)
class ValidatedOpenRegion:
    revision: NetRevision
    command_ref: SourceQualifiedResourceRef
    provenance_ref: SourceQualifiedResourceRef
    provenance: dict
    definition: dict
    element_map: dict
    boundary_inventory: dict
    host_requirements: dict
    contract: dict


def _read_command_at(db, core, revision, binding, *, definition_schema, command_schema, marker, visiting):
    _, metadata = _material(db, core, revision.definition_ref, binding, definition_schema)
    try:
        descriptors = metadata["descriptors"]
        if set(descriptors) != {marker}:
            raise ValueError("unknown or dual author command markers")
        reference = _resource(core, json.loads(descriptors[marker]))
        if descriptors[marker] != canonical_text(reference.to_dict()):
            raise ValueError("noncanonical marker")
    except (ValueError, KeyError, TypeError) as exc:
        raise RegistryConflict("claim_not_proved: unique exact command marker required") from exc
    tag = (command_schema, reference)
    if tag in visiting:
        raise RegistryConflict("proof_cycle: open command dependency cycle")
    command, metadata = _material(db, core, reference, binding, command_schema)
    return reference, command, metadata, visiting | {tag}


def _check_prepared_at(db, core, binding, revision, expected, record, command_ref, command_meta, *, key, marker, schema):
    expected_ref = _material_ref(core, binding, key + ":command")
    expected_meta = _metadata(core, binding, expected_ref, schema, expected["schema_authorities"][schema],
        expected, "Open region complete command", {marker: schema})
    if (command_ref != expected_ref or not _same_json(revision.to_dict(), record.to_dict())
            or _resource_bytes(db, core, command_ref) != canonical_json(expected)
            or not _same_json(command_meta, expected_meta)):
        raise RegistryConflict("command_conflict: immutable complete open command differs")
    for spec in expected["prepared_materials"]:
        reference = _resource(core, spec["resource_ref"])
        actual, metadata = _material(db, core, reference, binding, spec["schema"])
        if (_resource_bytes(db, core, reference) != canonical_json(spec["document"])
                or not _same_json(actual, spec["document"]) or not _same_json(metadata, spec["metadata"])):
            raise RegistryConflict("material_integrity_mismatch: exact command material differs")


def _open_at(db, core, reference, registration, binding, visiting, *, prepared_revision=None):
    if reference in visiting:
        raise RegistryConflict("proof_cycle: open revision dependency cycle")
    visiting = visiting | {reference}
    binding = _binding_at(db, core)
    revision = prepared_revision or _read_net_revision_at(db, core, reference, local_source_id=binding["source_id"])
    if revision.definition_kind != "open_region" or revision.owner_task_ref != _owner(core, binding):
        raise RegistryConflict("authority_mismatch: full open reader needs exact owner open root")
    command_ref, command, metadata, visiting = _read_command_at(db, core, revision, binding,
        definition_schema=DEFINITION_SCHEMA, command_schema=COMMAND_SCHEMA, marker=MARKER, visiting=visiting)
    expected, record, source, bodies = _prepare_open_at(db, core, registration, binding,
        _obj(core, command["producer_principal_ref"]), command["command_id"], command["request"],
        command["schema_authorities"], visiting)
    _check_prepared_at(db, core, binding, revision, expected, record, command_ref, metadata,
                      key=_key(command["command_id"]), marker=MARKER, schema=COMMAND_SCHEMA)
    return ValidatedOpenRegion(revision, command_ref, _resource(core, expected["prepared_materials"][0]["resource_ref"]), *bodies)


def validate_open_revision(core, reference, registration):
    """Fully reopen an unresolved region, without compiling it as a Module."""
    if not isinstance(registration, Registration):
        raise TypeError("full open reader requires the caller's exact Registration")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return _open_at(db, core, reference, registration, _binding_at(db, core), set())


def _authorities_at(db, core, schemas):
    rows = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='registry_type_catalog/v1'").fetchall()
    if len(rows) != 1:
        raise RegistryConflict("authority_mismatch: one frozen catalog required")
    protected = {"entity_type": "registry_type_catalog/v1", "logical_id": rows[0]["logical_id"], "version_id": rows[0]["version_id"]}
    return {schema: protected if reference is None else {
        "resource_id": str(reference.resource_id), "resource_version_id": str(reference.resource_version_id)}
        for schema, reference in schemas.items()}


def _publish_command(author, command, revision, *, key, marker, schema, final_validate):
    """Freeze the entire request first; immutable prefixes can only resume it."""
    core, binding = author.core, author.binding
    command_ref = _material_ref(core, binding, key + ":command")
    meta = _metadata(core, binding, command_ref, schema, command["schema_authorities"][schema], command,
                     "Open region complete command", {marker: schema})
    specs = [{"role": "command", "schema": schema, "document": command,
              "resource_ref": command_ref.to_dict(), "metadata": meta}, *command["prepared_materials"]]
    try:
        for spec in specs:
            metadata = spec["metadata"]
            authority = metadata["content_schema_authority_ref"]
            selected = (_version_from_payload(authority) if "entity_type" in authority else ResourceVersionRef(
                TypedId.parse(authority["resource_id"], expected="resource"),
                TypedId.parse(authority["resource_version_id"], expected="resource_version")))
            resource = _publish_private_system(core, author.gateway._task_ref, PublishResource(
                origin=PrivateSystemOrigin(author.gateway._bootstrap_ref), payload=canonical_json(spec["document"]),
                media_type="application/json", content_schema_ref=spec["schema"], content_schema_authority_ref=selected,
                summary=metadata["summary"], descriptors=metadata["descriptors"], extensions=metadata["extensions"],
                lifetime_ref=author.gateway._bootstrap_ref, idempotency_key=key + ":" + spec["role"]))
            if SourceQualifiedResourceRef(binding["source_id"], resource).to_dict() != spec["resource_ref"]:
                raise RegistryConflict("command_conflict: prepared identity differs")
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            final_validate(db, revision, _binding_at(db, core))
        tx = core.begin(idempotency_key=key)
        tx.prewrite(object_type=NET_REVISION_TYPE, logical_id=revision.revision_ref.ref.entity_id,
            version_id=revision.revision_ref.ref.version_id, payload=canonical_json(revision.to_dict()),
            metadata=revision.to_dict(), media_type="application/json", schema_ref=NET_REVISION_SCHEMA)
        tx.commit()
    except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
        raise RegistryConflict("command_conflict: immutable open command cannot be changed") from exc


class OpenRegionAuthor:
    """Trusted HOST author for explicit source-component extraction roots."""

    def __init__(self, gateway, registration, producer_principal_ref):
        author = ClosedModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = author.core, gateway, registration
        self.binding, self.producer, self.schemas = author.binding, author.producer, dict(author.schemas)
        for schema in (COMMAND_SCHEMA, *[schema for _, schema in OPEN_MATERIALS]):
            document = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def publish(self, *, source_revision_ref, component_element_ids, lineage_mode, command_id):
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("open author uses a stale writer")
        # Do not coerce selection identities or infer a lineage mode.
        request = {"source_revision_ref": source_revision_ref.to_dict(),
            "selection": {"kind": "component_element_ids", "element_ids": list(component_element_ids)},
            "lineage_mode": lineage_mode}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("authority_mismatch: source binding changed")
            authorities = _authorities_at(db, self.core, self.schemas)
            command, revision, *_ = _prepare_open_at(db, self.core, self.registration, binding,
                self.producer, command_id, request, authorities, set())
            if self.core.event_store.object_row(revision.revision_ref.ref.version_id) is not None:
                prior = _open_at(db, self.core, revision.revision_ref, self.registration, binding, set())
                prior_command, _ = _material(db, self.core, prior.command_ref, binding, COMMAND_SCHEMA)
                if not _same_json(command, prior_command):
                    raise RegistryConflict("command_conflict: replay differs from complete request")
                return prior
        _publish_command(self, command, revision, key=_key(command_id), marker=MARKER, schema=COMMAND_SCHEMA,
            final_validate=lambda db, record, binding: _open_at(db, self.core, record.revision_ref,
                self.registration, binding, set(), prepared_revision=record))
        return validate_open_revision(self.core, revision.revision_ref, self.registration)
