"""Explicit immutable plain transplant decisions/results; no Branch or runtime action."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import uuid

from ..compiler import compile_module
from ..registry._event_store.collaboration_descriptors import exact_descriptor
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.publication import _version_from_payload
from ..registry.resource_service import _publish_private_system
from ..registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS, canonical_json, canonical_text
from .authoring import NetRevision, NET_REVISION_SCHEMA, NET_REVISION_TYPE
from .assembly_v2 import _binding_at, _material_ref, _host_at
from .materials import (
    ValidatedClosedRevision, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA,
    _material, _same_json, _element_map, _boundaries,
)
from .plain_transplant import PlainModuleTransplantAnalyzer, _read_at as _analysis_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from ._plain_merge_model import normalize, _state
from ._plain_transplant_resolution import RESOLUTION_ALGORITHM, UnresolvedPlainTransplant, resolve_selection
from ._plain_merge_resolution import rebuild_module
from .assemblies import _command as _check_command

RESOLUTION_SCHEMA = "rpnh/collaboration/plain_transplant_resolution/v1"
RESULT_COMMAND_SCHEMA = "rpnh/collaboration/plain_transplant_author_command/v1"
RESULT_MARKER = "plain_transplant_author_command_v1"
SELECTED_SCHEMA = "rpnh/collaboration/plain_selected_change/v1"
SCHEMAS = (RESOLUTION_SCHEMA, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA)
ROLES = ("resolution", "definition", "element_mapping", "boundary_mapping", "host_requirements")


def _key(command_id):
    _check_command(command_id)
    return "collaboration-plain-transplant-result:" + canonical_text({"command_id": command_id})


def _result_ref(core, binding, command_id, left):
    version = TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": _key(command_id), "task": str(core.task_id), "source": binding["source_id"],
        "kind": "resource_version"})).hex)
    return SourceQualifiedVersionRef(binding["source_id"], VersionRef(NET_REVISION_TYPE, left.ref.entity_id, version))


from .plain_merge_result import _resource_bytes, _schema_authority_at, _metadata


def _prepare_at(db, core, registration, binding, producer, command_id, analysis_ref, choices, authorities, visiting):
    key = _key(command_id)
    if producer.source_id != binding["source_id"] or producer.ref.entity_type != "principal/v1":
        raise RegistryConflict("transplant result producer must be an exact local principal")
    principal = exact_descriptor(db, core.object_store, core.task_id, producer.to_dict()["ref"])
    if principal["principal_id"] != str(producer.ref.entity_id) or principal["principal_version_id"] != str(producer.ref.version_id):
        raise RegistryConflict("transplant result producer self-identity differs")
    analysis = _analysis_at(db, core, analysis_ref, registration, binding, visiting)
    if analysis.document["status"] != "analyzed":
        raise UnresolvedPlainTransplant("transplant result requires one unique common base")
    ref = lambda value: SourceQualifiedVersionRef.from_dict(value, catalog=core.catalog)
    left, right = (ref(analysis.document["request"][field]) for field in ("local_revision_ref", "incoming_revision_ref"))
    reference = _result_ref(core, binding, command_id, left)
    atoms, canonical_choices, deleted, dispositions = resolve_selection(analysis.document, choices)
    module, ids = rebuild_module(atoms)
    if module.designer_constraints or any(c.key in {f"rpnh/agent-workflow-graph/v{n}" for n in range(1, 5)} for c in module.components):
        raise UnresolvedPlainTransplant("result must remain a plain closed Module")
    compiled = compile_module(module, registration)
    host = _host_at(db, core, binding, compiled)
    chosen_host = atoms.get("host/selection")
    if not isinstance(chosen_host, dict): raise UnresolvedPlainTransplant("result requires explicit HOST selection")
    chosen_refs = {(row["kind"], row["key"]): row["resource_ref"] for row in chosen_host["declaration_refs"]}
    for row in host["declaration_refs"]:
        kind, name = row["kind"], row["key"]
        if (not _same_json(row["resource_ref"], chosen_refs.get((kind, name)))
                or not _same_json(host["registrations"][kind][name], chosen_host["registrations"].get(kind, {}).get(name))):
            raise UnresolvedPlainTransplant("result compile needs a HOST declaration outside the selected exact contract")
    elements = _element_map(module, ids, None, {})
    boundary = _boundaries(module, elements)
    reconstructed = normalize(ValidatedClosedRevision(None, module, compiled, elements, boundary, host))["atoms"]
    if not _same_json(reconstructed, atoms):
        raise UnresolvedPlainTransplant("result does not exactly round-trip the resolved author atoms")
    command_ref = _material_ref(core, binding, key + ":command")
    resolution = {"schema_version": RESOLUTION_SCHEMA, "algorithm": RESOLUTION_ALGORITHM,
        "analysis_ref": analysis_ref.to_dict(), "choices": canonical_choices, "deleted_element_ids": deleted,
        "selected_subjects": analysis.document["request"]["selected_subjects"], "dispositions": dispositions}
    documents = (resolution, module.to_dict(), elements, boundary, host)
    rows = list(zip(ROLES, SCHEMAS, documents, strict=True))
    element_by_id = {row["element_id"]: row for row in elements["elements"]}
    selected_refs = []
    for disposition in dispositions:
        if disposition["disposition"] != "imported": continue
        subject = disposition["subject"]
        role = "selected_change:" + hashlib.sha256(canonical_json(subject)).hexdigest()
        selected_refs.append(_material_ref(core, binding, key + ":" + role))
        identity = subject.split("/", 1)[0]
        mapping = element_by_id.get(identity)
        body = {"schema_version": SELECTED_SCHEMA, "analysis_ref": analysis_ref.to_dict(),
            "base_revision_ref": analysis.document["base_revision_ref"], "local_revision_ref": left.to_dict(),
            "incoming_revision_ref": right.to_dict(), "result_revision_ref": reference.to_dict(),
            "subject": subject, "base": _state(analysis.document["normalized"]["base"]["atoms"], subject),
            "incoming": _state(analysis.document["normalized"]["incoming"]["atoms"], subject),
            "result": disposition["result"], "result_element": None if mapping is None else {
                field: mapping[field] for field in ("element_id", "kind", "locator")}}
        rows.append((role, SELECTED_SCHEMA, body))
    specs = []
    if set(authorities) != set((*SCHEMAS, SELECTED_SCHEMA, RESULT_COMMAND_SCHEMA)):
        raise RegistryConflict("result command must lock every exact schema authority")
    authority_digests = {schema: _schema_authority_at(db, core, binding, schema, authority) for schema, authority in authorities.items()}
    for role, schema, body in rows:
        core.catalog.validate_schema_ref(schema, body)
        selected = _material_ref(core, binding, key + ":" + role)
        descriptors = {RESULT_MARKER: canonical_text(command_ref.to_dict())}
        metadata = _metadata(core, binding, selected, schema, authorities[schema], body, "Plain transplant " + role, descriptors)
        specs.append({"role": role, "schema": schema, "resource_ref": selected.to_dict(),
            "document": body, "metadata": metadata, "sha256": hashlib.sha256(canonical_json(body)).hexdigest()})
    owner = SourceQualifiedVersionRef.from_dict({"schema_version": "rpnh/collaboration/source_version_ref/v1",
        "source_id": binding["source_id"], "ref": binding["task_ref"]}, catalog=core.catalog)
    command = {"schema_version": RESULT_COMMAND_SCHEMA, "algorithm": RESOLUTION_ALGORITHM,
        "source_id": binding["source_id"], "owner_task_ref": owner.to_dict(), "producer_principal_ref": producer.to_dict(),
        "command_id": command_id, "analysis_ref": analysis_ref.to_dict(), "analysis_command_ref": analysis.command_ref.to_dict(),
        "requested_choices": list(deepcopy(choices)), "schema_authorities": deepcopy(authorities), "schema_authority_sha256": authority_digests,
        "result_revision_ref": reference.to_dict(), "parent_revision_refs": [left.to_dict()],
        "base_revision_ref": analysis.document["base_revision_ref"], "incoming_revision_ref": right.to_dict(),
        "selected_subjects": analysis.document["request"]["selected_subjects"], "dispositions": dispositions,
        "selected_change_refs": [ref.to_dict() for ref in selected_refs], "prepared_materials": specs}
    json.dumps(command, allow_nan=False)
    core.catalog.validate_schema_ref(RESULT_COMMAND_SCHEMA, command)
    refs = [SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog) for spec in specs]
    revision = NetRevision(reference, owner, producer, command_id, "closed_module", refs[1], (left,), tuple(selected_refs), refs[2], refs[3], refs[4], None)
    return command, revision, module, compiled, elements, boundary, host


@dataclass(frozen=True)
class ValidatedPlainTransplantRevision(ValidatedClosedRevision):
    transplant_proof = True
    command_ref: SourceQualifiedResourceRef
    analysis_ref: SourceQualifiedResourceRef
    resolution_ref: SourceQualifiedResourceRef


def _validate_transplant_at(db, core, revision, registration, binding, visiting):
    # The legacy public closed reader starts with a weaker binding helper.
    # Every explicit transplant entry upgrades only its local proof context here.
    binding = _binding_at(db, core)
    if len(revision.parent_revision_refs) != 1:
        raise RegistryConflict("transplant result requires exactly the local parent")
    definition, metadata = _material(db, core, revision.definition_ref, binding, MODULE_SCHEMA)
    try:
        marker = metadata["descriptors"]
        if set(marker) != {RESULT_MARKER}: raise ValueError("ambiguous transplant marker")
        command_ref = SourceQualifiedResourceRef.from_dict(json.loads(marker[RESULT_MARKER]), catalog=core.catalog)
    except (TypeError, ValueError, KeyError) as exc:
        raise RegistryConflict("transplant result requires its unique exact command marker") from exc
    tag = (RESULT_COMMAND_SCHEMA, command_ref)
    if tag in visiting: raise RegistryConflict("transplant command/proof cycle")
    visiting = visiting | {tag}
    command, command_meta = _material(db, core, command_ref, binding, RESULT_COMMAND_SCHEMA)
    analysis_ref = SourceQualifiedResourceRef.from_dict(command["analysis_ref"], catalog=core.catalog)
    producer = SourceQualifiedVersionRef.from_dict(command["producer_principal_ref"], catalog=core.catalog)
    expected, record, module, compiled, elements, boundary, host = _prepare_at(db, core, registration, binding,
        producer, command["command_id"], analysis_ref, command["requested_choices"], command["schema_authorities"], visiting)
    expected_ref = _material_ref(core, binding, _key(command["command_id"]) + ":command")
    expected_meta = _metadata(core, binding, expected_ref, RESULT_COMMAND_SCHEMA,
        command["schema_authorities"][RESULT_COMMAND_SCHEMA], expected, "Plain transplant complete command", {RESULT_MARKER: RESULT_COMMAND_SCHEMA})
    if (command_ref != expected_ref or _resource_bytes(db, core, command_ref) != canonical_json(expected) or not _same_json(command, expected) or not _same_json(command_meta, expected_meta)
            or not _same_json(revision.to_dict(), record.to_dict())):
        raise RegistryConflict("transplant result differs from its exact immutable complete command")
    for spec in expected["prepared_materials"]:
        reference = SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog)
        document, actual_metadata = _material(db, core, reference, binding, spec["schema"])
        if (_resource_bytes(db, core, reference) != canonical_json(spec["document"])
                or not _same_json(document, spec["document"]) or not _same_json(actual_metadata, spec["metadata"])):
            raise RegistryConflict("transplant material payload or selected metadata differs from complete command")
    return ValidatedPlainTransplantRevision(revision, module, compiled, elements, boundary, host,
        command_ref, analysis_ref, SourceQualifiedResourceRef.from_dict(expected["prepared_materials"][0]["resource_ref"], catalog=core.catalog))


class PlainModuleTransplantAuthor:
    def __init__(self, gateway, registration, producer_principal_ref):
        self.analyzer = PlainModuleTransplantAnalyzer(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = self.analyzer.core, gateway, registration
        self.binding, self.producer = self.analyzer.binding, self.analyzer.producer
        self.schemas = dict(self.analyzer.author.schemas)
        for schema in (RESOLUTION_SCHEMA, SELECTED_SCHEMA, RESULT_COMMAND_SCHEMA):
            body = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": body})

    def publish(self, *, analysis_ref, choices, command_id):
        from .materials import validate_closed_revision
        key = _key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("transplant result author uses a stale writer")
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding): raise RegistryConflict("transplant result binding changed")
            catalogs = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='registry_type_catalog/v1'").fetchall()
            if len(catalogs) != 1: raise RegistryConflict("result schema requires one frozen catalog")
            protected = {"entity_type": "registry_type_catalog/v1", "logical_id": catalogs[0]["logical_id"], "version_id": catalogs[0]["version_id"]}
            authorities = {schema: protected if ref is None else {"resource_id": str(ref.resource_id), "resource_version_id": str(ref.resource_version_id)}
                           for schema, ref in self.schemas.items()}
            command, revision, *_ = _prepare_at(db, self.core, self.registration, binding, self.producer,
                command_id, analysis_ref, choices, authorities, set())
        if self.core.event_store.object_row(revision.revision_ref.ref.version_id) is not None:
            prior = validate_closed_revision(self.core, revision.revision_ref, self.registration)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                prior_command, _ = _material(db, self.core, prior.command_ref, _binding_at(db, self.core), RESULT_COMMAND_SCHEMA)
            if not _same_json(command, prior_command) or not _same_json(prior.revision.to_dict(), revision.to_dict()):
                raise RegistryConflict("transplant result conflicts with original complete command")
            return prior
        command_ref = _material_ref(self.core, binding, key + ":command")
        command_metadata = _metadata(self.core, binding, command_ref, RESULT_COMMAND_SCHEMA, authorities[RESULT_COMMAND_SCHEMA],
            command, "Plain transplant complete command", {RESULT_MARKER: RESULT_COMMAND_SCHEMA})
        specs = [{"role": "command", "schema": RESULT_COMMAND_SCHEMA, "document": command,
                  "resource_ref": command_ref.to_dict(), "metadata": command_metadata}, *command["prepared_materials"]]
        try:
            for spec in specs:
                meta = spec["metadata"]
                authority = meta["content_schema_authority_ref"]
                selected = (_version_from_payload(authority) if "entity_type" in authority else ResourceVersionRef(
                    TypedId.parse(authority["resource_id"], expected="resource"), TypedId.parse(authority["resource_version_id"], expected="resource_version")))
                resource = _publish_private_system(self.core, self.gateway._task_ref, PublishResource(
                    origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(spec["document"]),
                    media_type=meta["media_type"], content_schema_ref=spec["schema"], content_schema_authority_ref=selected,
                    summary=meta["summary"], descriptors=meta["descriptors"], extensions=meta["extensions"],
                    lifetime_ref=self.gateway._bootstrap_ref, idempotency_key=key + ":" + spec["role"]))
                if SourceQualifiedResourceRef(binding["source_id"], resource).to_dict() != spec["resource_ref"]:
                    raise RegistryConflict("transplant prepared identity differs")
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                _validate_transplant_at(db, self.core, revision, self.registration, binding, {revision.revision_ref})
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=NET_REVISION_TYPE, logical_id=revision.revision_ref.ref.entity_id,
                version_id=revision.revision_ref.ref.version_id, payload=canonical_json(revision.to_dict()),
                metadata=revision.to_dict(), media_type="application/json", schema_ref=NET_REVISION_SCHEMA)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("transplant result conflicts with immutable complete command") from exc
        return validate_closed_revision(self.core, revision.revision_ref, self.registration)
