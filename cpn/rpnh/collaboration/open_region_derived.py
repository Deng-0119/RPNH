"""Explicit ordinary definitions with fully reconstructed open-author history.

Historical adaptation is provenance, never a current target claim. Independent
copies have no history parents; their exact cross-lineage map is separate from
the ordinary element map's parent-only copied_from relation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import uuid

from ..compiler import compile_module
from ..module import ModuleDeclaration
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.schema_catalog import canonical_text
from .authoring import NET_REVISION_TYPE, NetRevision
from .assemblies import _command as _check_command
from .assembly_v2 import _binding_at, _host_at, _material_ref
from .materials import (ClosedModuleAuthor, ValidatedClosedRevision, MODULE_SCHEMA,
    _element_map, _boundaries, _material, _same_json, _validate_at, validate_closed_revision)
from .open_region import (PREFIX, SOURCE_MATERIALS, _obj, _resource, _owner, _producer_at,
    _pins_at, _prepare_specs, _read_command_at, _check_prepared_at, _authorities_at, _publish_command)
from .open_region_closure import ValidatedAdaptedRevision
from .plain_merge_result import _schema_authority_at
from .references import SourceQualifiedVersionRef

COMMAND_SCHEMA = PREFIX + "open_region_derived_author_command/v1"
HISTORY_SCHEMA = PREFIX + "open_region_historical_origins/v1"
MARKER = "open_region_derived_author_command_v1"
ALGORITHM = PREFIX + "ordinary_new_definition/v1"
MATERIALS = (*SOURCE_MATERIALS, ("historical_origins", HISTORY_SCHEMA))
REQUEST_FIELDS = {"operation", "authority_mode", "parent_revision_ref", "copy_source_ref",
                  "module", "element_ids", "copy_sources"}


def _key(command_id):
    _check_command(command_id)
    return "collaboration-open-region-derived:" + canonical_text({"command_id": command_id})


def _result_ref(core, binding, command_id, parent):
    def stable(kind):
        return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
            "scope": _key(command_id), "task": str(core.task_id),
            "source": binding["source_id"], "kind": kind})).hex)
    return SourceQualifiedVersionRef(binding["source_id"], VersionRef(NET_REVISION_TYPE,
        stable("resource") if parent is None else parent.revision.revision_ref.ref.entity_id,
        stable("resource_version")))


@dataclass(frozen=True)
class ValidatedOpenDerivedRevision(ValidatedClosedRevision):
    command_ref: object
    historical_origins_ref: object
    historical_origins: dict
    authority_mode: str = "ordinary_new_definition"
    derivation: str = "historical_only"
    current_adaptation: None = None


def _source_proof_at(db, core, binding, source):
    adapted = type(source) is ValidatedAdaptedRevision
    return {"revision_ref": source.revision.revision_ref.to_dict(),
        "proof_kind": "adapted_result_current" if adapted else "ordinary_new_definition",
        "command_ref": source.command_ref.to_dict(),
        "adaptation_ref": source.adaptation_ref.to_dict() if adapted else None,
        "historical_origins_ref": None if adapted else source.historical_origins_ref.to_dict(),
        "materials": _pins_at(db, core, binding, source)}


def _history(source, reference, operation, elements, copies):
    adapted = type(source) is ValidatedAdaptedRevision
    relation = "ancestor" if operation == "edit" else "copied_from"
    origins = [] if adapted else deepcopy(source.historical_origins["origins"])
    if operation == "copy_root":
        for row in origins:
            row["relationship"] = "copied_from"
    origins.append({"revision_ref": source.revision.revision_ref.to_dict(),
        "command_ref": source.command_ref.to_dict(),
        "adaptation_ref": source.adaptation_ref.to_dict() if adapted else None,
        "historical_origins_ref": None if adapted else source.historical_origins_ref.to_dict(),
        "relationship": relation})
    origins = {canonical_text(row): row for row in origins}
    copy_origins = [] if operation == "edit" else [{
        "result_element_id": row["element_id"], "result_locator": row["locator"], "kind": row["kind"],
        "source_revision_ref": source.revision.revision_ref.to_dict(),
        "source_element_id": copies[row["element_id"]]} for row in elements["elements"]]
    return {"schema_version": HISTORY_SCHEMA, "result_revision_ref": reference.to_dict(),
        "authority_mode": "ordinary_new_definition", "derivation": "historical_only", "current_adaptation": None,
        "origins": [origins[key] for key in sorted(origins)], "copy_origins": copy_origins}


def _prepare_at(db, core, registration, binding, producer, command_id, request, authorities, visiting):
    _producer_at(db, core, binding, producer)
    if (not isinstance(request, dict) or set(request) != REQUEST_FIELDS
            or request["authority_mode"] != "ordinary_new_definition"
            or request["operation"] not in {"edit", "copy_root"}):
        raise ValueError("unsupported_contract: exact request and explicit ordinary_new_definition required")
    edit = request["operation"] == "edit"
    if ((request["parent_revision_ref"] is None) == edit
            or (request["copy_source_ref"] is not None) == edit):
        raise ValueError("invalid_selection: edit requires only parent; copy_root requires only copy source")
    selected = _obj(core, request["parent_revision_ref"] if edit else request["copy_source_ref"])
    source = _validate_at(db, core, selected, registration, binding, visiting)
    if type(source) not in {ValidatedAdaptedRevision, ValidatedOpenDerivedRevision}:
        raise RegistryConflict("unsupported_contract: derived source must be a genuine adapted D or derived E")
    if source.revision.owner_task_ref != _owner(core, binding):
        raise RegistryConflict("authority_mismatch: derived source must belong to the exact owner")
    if not isinstance(request["element_ids"], dict) or not isinstance(request["copy_sources"], dict):
        raise ValueError("invalid_selection: total IDs and explicit copy_sources required")
    module = ModuleDeclaration.from_dict(request["module"])
    parent = source if edit else None
    reference = _result_ref(core, binding, command_id, parent)
    if not edit:
        if (reference.ref.entity_id == selected.ref.entity_id
                or not _same_json(module.to_dict(), source.module.to_dict())):
            raise RegistryConflict("invalid_selection: independent first copy needs a new root and exact source Module")
        old = {row["locator"]: row for row in source.element_map["elements"]}
        ids, copies = request["element_ids"], request["copy_sources"]
        if (set(ids) != set(old) or set(ids.values()) & {row["element_id"] for row in old.values()}
                or copies != {ids[path]: row["element_id"] for path, row in old.items()}):
            raise ValueError("invalid_selection: copy_root requires all fresh IDs and total exact same-locator copy map")
    elements = _element_map(module, request["element_ids"], parent, request["copy_sources"] if edit else {})
    boundaries = _boundaries(module, elements)
    compiled = compile_module(module, registration)
    host = _host_at(db, core, binding, compiled)
    history = _history(source, reference, request["operation"], elements, request["copy_sources"])
    source_proof = _source_proof_at(db, core, binding, source)
    key = _key(command_id)
    bodies = (module.to_dict(), elements, boundaries, host, history)
    specs = _prepare_specs(core, binding, key, MARKER,
        [(role, schema, body) for (role, schema), body in zip(MATERIALS, bodies, strict=True)], authorities)
    if set(authorities) != {COMMAND_SCHEMA, *[schema for _, schema in MATERIALS]}:
        raise RegistryConflict("authority_mismatch: derived command requires exact schema set")
    parents = (selected,) if edit else ()
    command = {"schema_version": COMMAND_SCHEMA, "algorithm": ALGORITHM,
        "source_id": binding["source_id"], "owner_task_ref": _owner(core, binding).to_dict(),
        "producer_principal_ref": producer.to_dict(), "command_id": command_id, "request": deepcopy(request),
        "schema_authorities": deepcopy(authorities), "schema_authority_sha256": {
            schema: _schema_authority_at(db, core, binding, schema, authority)
            for schema, authority in authorities.items()}, "source_proof": source_proof,
        "result_revision_ref": reference.to_dict(), "parent_revision_refs": [ref.to_dict() for ref in parents],
        "selected_change_refs": [], "prepared_materials": specs}
    core.catalog.validate_schema_ref(COMMAND_SCHEMA, command)
    refs = {spec["role"]: _resource(core, spec["resource_ref"]) for spec in specs}
    revision = NetRevision(reference, _owner(core, binding), producer, command_id, "closed_module",
        refs["definition"], parents, (), refs["element_mapping"], refs["boundary_mapping"], refs["host_requirements"], None)
    return command, revision, module, compiled, elements, boundaries, host, history


def _derived_at(db, core, revision, registration, binding, visiting):
    binding = _binding_at(db, core)
    if revision.selected_change_refs or revision.open_region_contract_ref is not None:
        raise RegistryConflict("claim_not_proved: derived ordinary definitions have no selected changes or open contract")
    command_ref, command, metadata, visiting = _read_command_at(db, core, revision, binding,
        definition_schema=MODULE_SCHEMA, command_schema=COMMAND_SCHEMA, marker=MARKER, visiting=visiting)
    expected, record, module, compiled, elements, boundaries, host, history = _prepare_at(
        db, core, registration, binding, _obj(core, command["producer_principal_ref"]), command["command_id"],
        command["request"], command["schema_authorities"], visiting)
    _check_prepared_at(db, core, binding, revision, expected, record, command_ref, metadata,
        key=_key(command["command_id"]), marker=MARKER, schema=COMMAND_SCHEMA)
    history_ref = _material_ref(core, binding, _key(command["command_id"]) + ":historical_origins")
    return ValidatedOpenDerivedRevision(revision, module, compiled, elements, boundaries, host,
        command_ref, history_ref, history)


class OpenRegionDerivedAuthor:
    """A separate, explicit conversion; never an implicit legacy-author edit."""
    def __init__(self, gateway, registration, producer_principal_ref):
        author = ClosedModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = author.core, gateway, registration
        self.binding, self.producer, self.schemas = author.binding, author.producer, dict(author.schemas)
        for schema in (COMMAND_SCHEMA, HISTORY_SCHEMA):
            document = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def publish(self, *, request, command_id):
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("derived author uses a stale writer")
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("authority_mismatch: derived binding changed")
            command, revision, *_ = _prepare_at(db, self.core, self.registration, binding, self.producer,
                command_id, deepcopy(request), _authorities_at(db, self.core, self.schemas), set())
            if self.core.event_store.object_row(revision.revision_ref.ref.version_id) is not None:
                prior = _validate_at(db, self.core, revision.revision_ref, self.registration, binding, set())
                if type(prior) is not ValidatedOpenDerivedRevision:
                    raise RegistryConflict("command_conflict: derived replay selected another proof family")
                prior_command, _ = _material(db, self.core, prior.command_ref, binding, COMMAND_SCHEMA)
                if not _same_json(command, prior_command):
                    raise RegistryConflict("command_conflict: derived replay differs from complete request")
                return prior
        _publish_command(self, command, revision, key=_key(command_id), marker=MARKER,
            schema=COMMAND_SCHEMA, final_validate=lambda db, record, binding:
                _derived_at(db, self.core, record, self.registration, binding, {record.revision_ref}))
        return validate_closed_revision(self.core, revision.revision_ref, self.registration)
