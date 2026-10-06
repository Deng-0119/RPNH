"""Explicit immutable plain merge decisions/results; no Branch or runtime action."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import uuid

from ..compiler import compile_module
from ..registry._event_store.collaboration_descriptors import exact_descriptor, exact_prepared, readable_payload
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.publication import _fresh_bootstrap_reference_resource_metadata, _version_from_payload
from ..registry.resource_service import _publish_private_system
from ..registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS, canonical_json, canonical_text
from .authoring import NetRevision, NET_REVISION_SCHEMA, NET_REVISION_TYPE
from .assembly_v2 import _binding_at, _material_ref, _host_at
from .materials import (
    ValidatedClosedRevision, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA,
    _material, _private_document, _same_json, _element_map, _boundaries,
)
from .plain_merge import PlainModuleMergeAnalyzer, _read_at as _analysis_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from ._plain_merge_model import normalize
from ._plain_merge_resolution import RESOLUTION_ALGORITHM, UnresolvedPlainMerge, resolve_atoms, rebuild_module
from .assemblies import _command as _check_command

RESOLUTION_SCHEMA = "rpnh/collaboration/plain_merge_resolution/v1"
RESULT_COMMAND_SCHEMA = "rpnh/collaboration/plain_merge_author_command/v1"
RESULT_MARKER = "plain_merge_author_command_v1"
SCHEMAS = (RESOLUTION_SCHEMA, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA)
ROLES = ("resolution", "definition", "element_mapping", "boundary_mapping", "host_requirements")


def _key(command_id):
    _check_command(command_id)
    return "collaboration-plain-merge-result:" + canonical_text({"command_id": command_id})


def _result_ref(core, binding, command_id, left, right):
    if left == right:
        raise UnresolvedPlainMerge("identical exact heads cannot form ordered unique merge parents")
    if left.ref.entity_id != right.ref.entity_id:
        raise UnresolvedPlainMerge("common-lineage result requires the same logical revision identity")
    version = TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": _key(command_id), "task": str(core.task_id), "source": binding["source_id"],
        "kind": "resource_version"})).hex)
    return SourceQualifiedVersionRef(binding["source_id"], VersionRef(NET_REVISION_TYPE, left.ref.entity_id, version))


def _resource_bytes(db, core, reference):
    prepared = exact_prepared(db, core.object_store, core.task_id, {
        "entity_type": "resource_version/v1", "logical_id": str(reference.ref.resource_id),
        "version_id": str(reference.ref.resource_version_id)})
    return readable_payload(core.object_store, prepared, media_type="application/json")


def _schema_authority_at(db, core, binding, schema, authority):
    """Preflight the same exact schema bytes as _material, before a resource exists."""
    if schema in PROTECTED_SCHEMA_REFS:
        if set(authority) != {"entity_type", "logical_id", "version_id"} or authority["entity_type"] != "registry_type_catalog/v1":
            raise RegistryConflict("protected result schema needs its exact frozen catalog")
        bundle = exact_descriptor(db, core.object_store, core.task_id, authority)
        entry = bundle["schemas"].get(schema)
        if not isinstance(entry, dict) or entry.get("schema_id") != schema:
            raise RegistryConflict("result schema is absent from exact catalog")
        actual = json.loads(entry["source"])
        prepared = exact_prepared(db, core.object_store, core.task_id, authority)
        schema_bytes = readable_payload(core.object_store, prepared, media_type="application/json")
    else:
        if set(authority) != {"resource_id", "resource_version_id"}:
            raise RegistryConflict("result schema needs an exact local schema resource")
        ref = SourceQualifiedResourceRef.from_dict({"schema_version": "rpnh/collaboration/source_resource_ref/v1",
            "source_id": binding["source_id"], "ref": authority}, catalog=core.catalog)
        actual, _ = _private_document(db, core, ref, binding, media_type="application/schema+json")
        prepared = exact_prepared(db, core.object_store, core.task_id, {"entity_type": "resource_version/v1",
            "logical_id": authority["resource_id"], "version_id": authority["resource_version_id"]})
        schema_bytes = readable_payload(core.object_store, prepared, media_type="application/schema+json")
    expected = json.loads(core.catalog.schema_path(schema).read_text(encoding="utf-8"))
    if not _same_json(actual, expected):
        raise RegistryConflict("result schema authority bytes differ from supported contract")
    return hashlib.sha256(schema_bytes).hexdigest()


def _metadata(core, binding, reference, schema, authority, body, summary, descriptors):
    task, bootstrap = (_version_from_payload(binding[field]) for field in ("task_ref", "bootstrap_command_ref"))
    return _fresh_bootstrap_reference_resource_metadata(core, ref=reference.ref, task_ref=task,
        bootstrap_ref=bootstrap, lifetime_ref=bootstrap, payload_size=len(canonical_json(body)),
        media_type="application/json", content_schema_ref=schema, content_schema_authority_ref=authority,
        summary=summary, descriptors=descriptors, extensions={}, derived_from=())


def _prepare_at(db, core, registration, binding, producer, command_id, analysis_ref, choices, authorities, visiting):
    key = _key(command_id)
    if producer.source_id != binding["source_id"] or producer.ref.entity_type != "principal/v1":
        raise RegistryConflict("merge result producer must be an exact local principal")
    principal = exact_descriptor(db, core.object_store, core.task_id, producer.to_dict()["ref"])
    if principal["principal_id"] != str(producer.ref.entity_id) or principal["principal_version_id"] != str(producer.ref.version_id):
        raise RegistryConflict("merge result producer self-identity differs")
    analysis = _analysis_at(db, core, analysis_ref, registration, binding, visiting)
    if analysis.document["status"] != "analyzed":
        raise UnresolvedPlainMerge("merge result requires one unique common base")
    ref = lambda value: SourceQualifiedVersionRef.from_dict(value, catalog=core.catalog)
    left, right = (ref(analysis.document["request"][field]) for field in ("local_revision_ref", "incoming_revision_ref"))
    reference = _result_ref(core, binding, command_id, left, right)
    atoms, canonical_choices, deleted = resolve_atoms(analysis.document, choices)
    module, ids = rebuild_module(atoms)
    if module.designer_constraints or any(c.key in {f"rpnh/agent-workflow-graph/v{n}" for n in range(1, 5)} for c in module.components):
        raise UnresolvedPlainMerge("result must remain a plain closed Module")
    compiled = compile_module(module, registration)
    host = _host_at(db, core, binding, compiled)
    chosen_host = atoms.get("host/selection")
    if not isinstance(chosen_host, dict): raise UnresolvedPlainMerge("result requires explicit HOST selection")
    chosen_refs = {(row["kind"], row["key"]): row["resource_ref"] for row in chosen_host["declaration_refs"]}
    for row in host["declaration_refs"]:
        kind, name = row["kind"], row["key"]
        if (not _same_json(row["resource_ref"], chosen_refs.get((kind, name)))
                or not _same_json(host["registrations"][kind][name], chosen_host["registrations"].get(kind, {}).get(name))):
            raise UnresolvedPlainMerge("result compile needs a HOST declaration outside the selected exact contract")
    elements = _element_map(module, ids, None, {})
    boundary = _boundaries(module, elements)
    reconstructed = normalize(ValidatedClosedRevision(None, module, compiled, elements, boundary, host))["atoms"]
    expected_atoms = {**atoms, "host/selection": host}
    if not _same_json(reconstructed, expected_atoms):
        raise UnresolvedPlainMerge("result does not exactly round-trip the resolved author atoms")
    command_ref = _material_ref(core, binding, key + ":command")
    resolution = {"schema_version": RESOLUTION_SCHEMA, "algorithm": RESOLUTION_ALGORITHM,
        "analysis_ref": analysis_ref.to_dict(), "choices": canonical_choices, "deleted_element_ids": deleted}
    documents = (resolution, module.to_dict(), elements, boundary, host)
    specs = []
    if set(authorities) != set((*SCHEMAS, RESULT_COMMAND_SCHEMA)):
        raise RegistryConflict("result command must lock every exact schema authority")
    authority_digests = {schema: _schema_authority_at(db, core, binding, schema, authority) for schema, authority in authorities.items()}
    for role, schema, body in zip(ROLES, SCHEMAS, documents, strict=True):
        core.catalog.validate_schema_ref(schema, body)
        selected = _material_ref(core, binding, key + ":" + role)
        descriptors = {RESULT_MARKER: canonical_text(command_ref.to_dict())}
        metadata = _metadata(core, binding, selected, schema, authorities[schema], body, "Plain merge " + role, descriptors)
        specs.append({"role": role, "schema": schema, "resource_ref": selected.to_dict(),
            "document": body, "metadata": metadata, "sha256": hashlib.sha256(canonical_json(body)).hexdigest()})
    owner = SourceQualifiedVersionRef.from_dict({"schema_version": "rpnh/collaboration/source_version_ref/v1",
        "source_id": binding["source_id"], "ref": binding["task_ref"]}, catalog=core.catalog)
    command = {"schema_version": RESULT_COMMAND_SCHEMA, "algorithm": RESOLUTION_ALGORITHM,
        "source_id": binding["source_id"], "owner_task_ref": owner.to_dict(), "producer_principal_ref": producer.to_dict(),
        "command_id": command_id, "analysis_ref": analysis_ref.to_dict(), "analysis_command_ref": analysis.command_ref.to_dict(),
        "requested_choices": list(deepcopy(choices)), "schema_authorities": deepcopy(authorities), "schema_authority_sha256": authority_digests,
        "result_revision_ref": reference.to_dict(), "parent_revision_refs": [left.to_dict(), right.to_dict()],
        "selected_change_refs": [], "prepared_materials": specs}
    json.dumps(command, allow_nan=False)
    core.catalog.validate_schema_ref(RESULT_COMMAND_SCHEMA, command)
    refs = [SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog) for spec in specs]
    revision = NetRevision(reference, owner, producer, command_id, "closed_module", refs[1], (left, right), (), refs[2], refs[3], refs[4], None)
    return command, revision, module, compiled, elements, boundary, host


@dataclass(frozen=True)
class ValidatedPlainMergeRevision(ValidatedClosedRevision):
    command_ref: SourceQualifiedResourceRef
    analysis_ref: SourceQualifiedResourceRef
    resolution_ref: SourceQualifiedResourceRef


def _validate_merge_at(db, core, revision, registration, binding, visiting):
    # The legacy public closed reader starts with a weaker binding helper.
    # Every explicit merge entry upgrades only its local proof context here.
    binding = _binding_at(db, core)
    if len(revision.parent_revision_refs) != 2 or revision.selected_change_refs:
        raise RegistryConflict("merge result requires two ordered parents and empty selected changes")
    definition, metadata = _material(db, core, revision.definition_ref, binding, MODULE_SCHEMA)
    try:
        marker = metadata["descriptors"]
        if set(marker) != {RESULT_MARKER}: raise ValueError("ambiguous merge marker")
        command_ref = SourceQualifiedResourceRef.from_dict(json.loads(marker[RESULT_MARKER]), catalog=core.catalog)
    except (TypeError, ValueError, KeyError) as exc:
        raise RegistryConflict("merge result requires its unique exact command marker") from exc
    tag = (RESULT_COMMAND_SCHEMA, command_ref)
    if tag in visiting: raise RegistryConflict("merge command/proof cycle")
    visiting = visiting | {tag}
    command, command_meta = _material(db, core, command_ref, binding, RESULT_COMMAND_SCHEMA)
    analysis_ref = SourceQualifiedResourceRef.from_dict(command["analysis_ref"], catalog=core.catalog)
    producer = SourceQualifiedVersionRef.from_dict(command["producer_principal_ref"], catalog=core.catalog)
    expected, record, module, compiled, elements, boundary, host = _prepare_at(db, core, registration, binding,
        producer, command["command_id"], analysis_ref, command["requested_choices"], command["schema_authorities"], visiting)
    expected_ref = _material_ref(core, binding, _key(command["command_id"]) + ":command")
    expected_meta = _metadata(core, binding, expected_ref, RESULT_COMMAND_SCHEMA,
        command["schema_authorities"][RESULT_COMMAND_SCHEMA], expected, "Plain merge complete command", {RESULT_MARKER: RESULT_COMMAND_SCHEMA})
    if (command_ref != expected_ref or _resource_bytes(db, core, command_ref) != canonical_json(expected) or not _same_json(command, expected) or not _same_json(command_meta, expected_meta)
            or not _same_json(revision.to_dict(), record.to_dict())):
        raise RegistryConflict("merge result differs from its exact immutable complete command")
    for spec in expected["prepared_materials"]:
        reference = SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog)
        document, actual_metadata = _material(db, core, reference, binding, spec["schema"])
        if (_resource_bytes(db, core, reference) != canonical_json(spec["document"])
                or not _same_json(document, spec["document"]) or not _same_json(actual_metadata, spec["metadata"])):
            raise RegistryConflict("merge material payload or selected metadata differs from complete command")
    return ValidatedPlainMergeRevision(revision, module, compiled, elements, boundary, host,
        command_ref, analysis_ref, SourceQualifiedResourceRef.from_dict(expected["prepared_materials"][0]["resource_ref"], catalog=core.catalog))


class PlainModuleMergeAuthor:
    def __init__(self, gateway, registration, producer_principal_ref):
        self.analyzer = PlainModuleMergeAnalyzer(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = self.analyzer.core, gateway, registration
        self.binding, self.producer = self.analyzer.binding, self.analyzer.producer
        self.schemas = dict(self.analyzer.author.schemas)
        for schema in (RESOLUTION_SCHEMA, RESULT_COMMAND_SCHEMA):
            body = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": body})

    def publish(self, *, analysis_ref, choices, command_id):
        from .materials import validate_closed_revision
        key = _key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("merge result author uses a stale writer")
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding): raise RegistryConflict("merge result binding changed")
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
                raise RegistryConflict("merge result conflicts with original complete command")
            return prior
        command_ref = _material_ref(self.core, binding, key + ":command")
        command_metadata = _metadata(self.core, binding, command_ref, RESULT_COMMAND_SCHEMA, authorities[RESULT_COMMAND_SCHEMA],
            command, "Plain merge complete command", {RESULT_MARKER: RESULT_COMMAND_SCHEMA})
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
                    raise RegistryConflict("merge prepared identity differs")
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                _validate_merge_at(db, self.core, revision, self.registration, binding, {revision.revision_ref})
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=NET_REVISION_TYPE, logical_id=revision.revision_ref.ref.entity_id,
                version_id=revision.revision_ref.ref.version_id, payload=canonical_json(revision.to_dict()),
                metadata=revision.to_dict(), media_type="application/json", schema_ref=NET_REVISION_SCHEMA)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("merge result conflicts with immutable complete command") from exc
        return validate_closed_revision(self.core, revision.revision_ref, self.registration)
