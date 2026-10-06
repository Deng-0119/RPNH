"""Opt-in ordinary graph ancestry, immutable analysis and version-three records."""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import json
import hashlib

from ..registration import Registration
from ..registry._event_store.collaboration_descriptors import exact_descriptor
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.object_store import ObjectIntegrityError
from ..registry.resources import PrivateSystemOrigin, PublishResource
from ..registry.resource_service import _publish_private_system
from ..registry.schema_catalog import canonical_json, canonical_text
from .authoring import _exact_object_ref
from .assembly_v2 import _binding_at, _material_ref
from .assemblies import _command as _check_command
from .graph_authoring import (
    GraphModuleAuthor, GRAPH_REVISION_TYPE, GRAPH_REF_FIELDS, GRAPH_DOCUMENT_SCHEMAS, ValidatedGraphRevision,
    _read_graph_revision_at,
)
from .graph_source import GRAPH_SOURCE_SCHEMA
from .materials import _material, _same_json, _validate_at, _validate_materials_at
from .plain_merge import _nearest
from .plain_merge_result import _resource_bytes, _metadata, _schema_authority_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from ._graph_merge_model import ALGORITHM, NORMALIZATION, normalize, compare

MERGE_REVISION_TYPE = "collaboration_net_revision/v3"
MERGE_REVISION_SCHEMA = "registry_v1/" + MERGE_REVISION_TYPE
ANALYSIS_SCHEMA = "rpnh/collaboration/graph_merge_analysis/v1"
ANALYSIS_COMMAND_SCHEMA = "rpnh/collaboration/graph_merge_analysis_command/v1"
ANALYSIS_MARKER = "graph_merge_analysis_v1"
ANALYSIS_COMMAND_MARKER = "graph_merge_analysis_command_v1"
GRAPH_HISTORY_TYPES = (GRAPH_REVISION_TYPE, MERGE_REVISION_TYPE)


def _reference(value, binding):
    if not isinstance(value, SourceQualifiedVersionRef) or value.ref.entity_type not in GRAPH_HISTORY_TYPES:
        raise ValueError("graph merge requires an exact ordinary graph-v2/v3 reference")
    _exact_object_ref(value, entity_type=value.ref.entity_type, logical_kind="resource", version_kind="resource_version")
    if value.source_id != binding["source_id"]:
        raise RegistryConflict("graph history must share the bound local source")
    return value


@dataclass(frozen=True, slots=True)
class GraphMergeNetRevision:
    revision_ref: SourceQualifiedVersionRef
    owner_task_ref: SourceQualifiedVersionRef
    producer_principal_ref: SourceQualifiedVersionRef
    command_id: str
    author_kind: str
    parent_revision_refs: tuple[SourceQualifiedVersionRef, ...]
    graph_source_ref: SourceQualifiedResourceRef
    graph_recipe_ref: SourceQualifiedResourceRef
    graph_source_mapping_ref: SourceQualifiedResourceRef
    definition_ref: SourceQualifiedResourceRef
    element_mapping_ref: SourceQualifiedResourceRef
    boundary_mapping_ref: SourceQualifiedResourceRef
    host_requirements_ref: SourceQualifiedResourceRef
    author_command_ref: SourceQualifiedResourceRef
    resolution_ref: SourceQualifiedResourceRef

    definition_kind = "closed_module"
    selected_change_refs = ()
    open_region_contract_ref = None

    def __post_init__(self):
        _exact_object_ref(self.revision_ref, entity_type=MERGE_REVISION_TYPE, logical_kind="resource", version_kind="resource_version")
        _exact_object_ref(self.owner_task_ref, entity_type="task/v1", logical_kind="task", version_kind="task_version")
        _exact_object_ref(self.producer_principal_ref, entity_type="principal/v1", logical_kind="principal", version_kind="principal_version")
        _check_command(self.command_id)
        if self.author_kind not in {"merge", "edit"} or not isinstance(self.parent_revision_refs, tuple):
            raise ValueError("graph-v3 requires an explicit merge or descendant edit")
        if len(self.parent_revision_refs) != (2 if self.author_kind == "merge" else 1):
            raise ValueError("graph merge needs two exact parents; edit needs one")
        if len(set(self.parent_revision_refs)) != len(self.parent_revision_refs):
            raise ValueError("graph merge parents must be distinct and ordered")
        for ref in self.parent_revision_refs:
            _reference(ref, {"source_id": self.revision_ref.source_id})
            if ref == self.revision_ref or ref.ref.entity_id != self.revision_ref.ref.entity_id:
                raise ValueError("graph history must retain its exact logical lineage")
            if self.author_kind == "edit" and ref.ref.entity_type != MERGE_REVISION_TYPE:
                raise ValueError("graph-v3 edit requires its complete v3 parent")
        resources = tuple(getattr(self, key) for key in (*GRAPH_REF_FIELDS, "author_command_ref", "resolution_ref"))
        if any(not isinstance(ref, SourceQualifiedResourceRef) for ref in resources) or len(set(resources)) != len(resources):
            raise ValueError("graph-v3 materials require distinct exact resource references")
        if any(ref.source_id != self.revision_ref.source_id for ref in (*resources, self.owner_task_ref, self.producer_principal_ref)):
            raise ValueError("graph-v3 authority and materials must share the exact source")

    def to_dict(self):
        return {"schema_version": MERGE_REVISION_SCHEMA, "definition_kind": "closed_module",
            "source_contract": GRAPH_SOURCE_SCHEMA, "author_kind": self.author_kind,
            "command_id": self.command_id, "parent_revision_refs": [ref.to_dict() for ref in self.parent_revision_refs],
            **{key: getattr(self, key).to_dict() for key in ("revision_ref", "owner_task_ref", "producer_principal_ref",
                *GRAPH_REF_FIELDS, "author_command_ref", "resolution_ref")}}

    @classmethod
    def from_dict(cls, document, *, catalog):
        catalog.validate_instance(MERGE_REVISION_TYPE, category="object", instance=document)
        obj = lambda value: SourceQualifiedVersionRef.from_dict(value, catalog=catalog)
        res = lambda value: SourceQualifiedResourceRef.from_dict(value, catalog=catalog)
        return cls(obj(document["revision_ref"]), obj(document["owner_task_ref"]), obj(document["producer_principal_ref"]),
            document["command_id"], document["author_kind"], tuple(obj(ref) for ref in document["parent_revision_refs"]),
            *(res(document[key]) for key in (*GRAPH_REF_FIELDS, "author_command_ref", "resolution_ref")))


@dataclass(frozen=True)
class ValidatedGraphMergeRevision(ValidatedGraphRevision):
    identity_origins: dict
    command_ref: SourceQualifiedResourceRef
    resolution_ref: SourceQualifiedResourceRef


def _read_revision_at(db, core, reference, binding):
    _reference(reference, binding)
    if reference.ref.entity_type == GRAPH_REVISION_TYPE:
        return _read_graph_revision_at(db, core, reference, local_source_id=binding["source_id"])
    record = GraphMergeNetRevision.from_dict(exact_descriptor(db, core.object_store, core.task_id,
        reference.to_dict()["ref"]), catalog=core.catalog)
    if record.revision_ref != reference or record.owner_task_ref.to_dict()["ref"] != binding["task_ref"]:
        raise RegistryConflict("graph-v3 descriptor exact self/owner differs")
    return record


def _lineage_at(db, core, references, binding):
    records, active = {}, set()
    def visit(reference):
        if reference in active: raise RegistryConflict("graph author ancestry contains a cycle")
        if reference in records: return
        active.add(reference)
        record = _read_revision_at(db, core, reference, binding)
        if record.owner_task_ref.to_dict()["ref"] != binding["task_ref"]:
            raise RegistryConflict("graph ancestry owner differs from exact bound owner")
        for parent in record.parent_revision_refs: visit(parent)
        records[reference] = record
        active.remove(reference)
    for reference in references: visit(reference)
    return records


def _origins(reference, records, values, cache):
    if reference in cache: return cache[reference]
    value = values[reference]
    if isinstance(value, ValidatedGraphMergeRevision):
        cache[reference] = deepcopy(value.identity_origins)
        return cache[reference]
    parents = records[reference].parent_revision_refs
    previous = {} if not parents else _origins(parents[0], records, values, cache)
    result = {}
    for row in value.source_map["elements"]:
        identity = row["element_id"]
        result[identity] = deepcopy(previous[identity]) if identity in previous else {
            "revision_ref": reference.to_dict(), "element_id": identity, "copied_from": deepcopy(row["copied_from"])}
    cache[reference] = result
    return result


def _pins(value):
    fields = GRAPH_REF_FIELDS
    if isinstance(value, ValidatedGraphMergeRevision): fields = (*fields, "author_command_ref", "resolution_ref")
    return {"revision_ref": value.revision.revision_ref.to_dict(),
        **{key: getattr(value.revision, key).to_dict() for key in fields}}


def _key(command_id):
    _check_command(command_id)
    return "collaboration-graph-merge-analysis:" + canonical_text({"command_id": command_id})


def _prepare_at(db, core, registration, binding, producer, command_id, request, authorities, visiting=None):
    visiting = set() if visiting is None else visiting
    if set(request) != {"local_revision_ref", "incoming_revision_ref", "asserted_base_revision_ref"}:
        raise RegistryConflict("graph analysis request shape differs")
    key = _key(command_id)
    if producer.source_id != binding["source_id"] or producer.ref.entity_type != "principal/v1":
        raise RegistryConflict("graph analysis producer must be an exact local principal")
    principal = exact_descriptor(db, core.object_store, core.task_id, producer.to_dict()["ref"])
    if principal["principal_id"] != str(producer.ref.entity_id) or principal["principal_version_id"] != str(producer.ref.version_id):
        raise RegistryConflict("graph analysis producer exact self differs")
    parsed = {key: None if body is None else SourceQualifiedVersionRef.from_dict(body, catalog=core.catalog) for key, body in request.items()}
    left, right = parsed["local_revision_ref"], parsed["incoming_revision_ref"]
    records = _lineage_at(db, core, [ref for ref in (left, right, parsed["asserted_base_revision_ref"]) if ref is not None], binding)
    values = {}
    for ref in records:
        if ref in visiting: raise RegistryConflict("graph analysis reaches an active author proof")
        record = records[ref]
        if ref.ref.entity_type == GRAPH_REVISION_TYPE:
            # records is parent-first, so each old single-parent graph is proved
            # once within this read cut, against its already proved exact parent.
            parent = None if not record.parent_revision_refs else values[record.parent_revision_refs[0]]
            value = _validate_materials_at(db, core, record, registration, binding, parent)
            for index, (field, schema) in enumerate(zip(GRAPH_REF_FIELDS, GRAPH_DOCUMENT_SCHEMAS, strict=True)):
                _, metadata = _material(db, core, getattr(record, field), binding, schema)
                expected_markers = {"graph_author_command_v1"} if index == 0 else set()
                if set(metadata["descriptors"]) != expected_markers:
                    raise RegistryConflict("graph merge input has unknown or dual author markers")
        else:
            value = _validate_at(db, core, ref, registration, binding, visiting)
        if not isinstance(value, ValidatedGraphRevision):
            raise RegistryConflict("graph history requires full source-authoritative proof")
        values[ref] = value
    candidates = _nearest(records, left, right)
    base = candidates[0] if len(candidates) == 1 else None
    if parsed["asserted_base_revision_ref"] is not None and parsed["asserted_base_revision_ref"] != base:
        raise ValueError("asserted base differs from unique nearest common graph ancestor")
    models, differences, conflicts = {}, [], []
    if base is not None:
        origins = {}
        models = {role: normalize(values[ref], _origins(ref, records, values, origins))
            for role, ref in (("base", base), ("local", left), ("incoming", right))}
        differences, conflicts = compare(models["base"], models["local"], models["incoming"])
    owner = SourceQualifiedVersionRef.from_dict({"schema_version": "rpnh/collaboration/source_version_ref/v1",
        "source_id": binding["source_id"], "ref": binding["task_ref"]}, catalog=core.catalog)
    document = {"schema_version": ANALYSIS_SCHEMA, "algorithm": ALGORITHM, "normalization": NORMALIZATION,
        "source_id": binding["source_id"], "owner_task_ref": owner.to_dict(), "producer_principal_ref": producer.to_dict(),
        "command_id": command_id, "command_ref": _material_ref(core, binding, key + ":command").to_dict(),
        "request": deepcopy(request), "status": "unrelated_histories" if not candidates else "multiple_bases" if len(candidates) > 1 else "analyzed",
        "base_revision_ref": None if base is None else base.to_dict(), "nearest_common_bases": [ref.to_dict() for ref in candidates],
        "inputs": {"base": None if base is None else _pins(values[base]), "local": _pins(values[left]), "incoming": _pins(values[right])},
        "ancestry": [_pins(values[ref]) for ref in sorted(records, key=lambda r: canonical_json(r.to_dict()))],
        "normalized": models, "differences": differences, "conflicts": conflicts}
    if set(authorities) != {ANALYSIS_SCHEMA, ANALYSIS_COMMAND_SCHEMA}:
        raise RegistryConflict("graph analysis must freeze both exact schema authorities")
    authority_digests = {schema: _schema_authority_at(db, core, binding, schema, selected)
        for schema, selected in authorities.items()}
    analysis_ref = _material_ref(core, binding, key + ":analysis")
    payload = canonical_json(document)
    metadata = _metadata(core, binding, analysis_ref, ANALYSIS_SCHEMA, authorities[ANALYSIS_SCHEMA],
        document, "Graph merge analysis", {ANALYSIS_MARKER: canonical_text(document["command_ref"])})
    command = {"schema_version": ANALYSIS_COMMAND_SCHEMA, "analysis": document,
        "schema_authorities": deepcopy(authorities), "schema_authority_sha256": authority_digests,
        "analysis_material": {"resource_ref": analysis_ref.to_dict(), "metadata": metadata,
            "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}
    for schema, body in ((ANALYSIS_SCHEMA, document), (ANALYSIS_COMMAND_SCHEMA, command)):
        json.dumps(body, allow_nan=False)
        core.catalog.validate_schema_ref(schema, body)
    return document, command


@dataclass(frozen=True)
class ValidatedGraphMergeAnalysis:
    analysis_ref: SourceQualifiedResourceRef
    command_ref: SourceQualifiedResourceRef
    document: dict


def _analysis_at(db, core, reference, registration, binding, visiting=None):
    visiting = set() if visiting is None else visiting
    tag = (ANALYSIS_SCHEMA, reference)
    if tag in visiting: raise RegistryConflict("graph analysis/proof cycle")
    visiting = visiting | {tag}
    document, metadata = _material(db, core, reference, binding, ANALYSIS_SCHEMA)
    command_ref = SourceQualifiedResourceRef.from_dict(document["command_ref"], catalog=core.catalog)
    command, command_metadata = _material(db, core, command_ref, binding, ANALYSIS_COMMAND_SCHEMA)
    producer = SourceQualifiedVersionRef.from_dict(document["producer_principal_ref"], catalog=core.catalog)
    expected, expected_command = _prepare_at(db, core, registration, binding, producer, document["command_id"],
        document["request"], command["schema_authorities"], visiting)
    key = _key(document["command_id"])
    expected_command_metadata = _metadata(core, binding, command_ref, ANALYSIS_COMMAND_SCHEMA,
        command["schema_authorities"][ANALYSIS_COMMAND_SCHEMA], expected_command,
        "Graph merge command", {ANALYSIS_COMMAND_MARKER: ANALYSIS_COMMAND_SCHEMA})
    if (reference != _material_ref(core, binding, key + ":analysis")
            or command_ref != _material_ref(core, binding, key + ":command")
            or not _same_json(document, expected) or not _same_json(command, expected_command)
            or _resource_bytes(db, core, reference) != canonical_json(expected)
            or _resource_bytes(db, core, command_ref) != canonical_json(expected_command)
            or not _same_json(metadata, expected_command["analysis_material"]["metadata"])
            or not _same_json(command_metadata, expected_command_metadata)):
        raise RegistryConflict("graph analysis differs from complete immutable command and source reconstruction")
    return ValidatedGraphMergeAnalysis(reference, command_ref, document)


def read_graph_merge_analysis(core, reference, registration):
    if not isinstance(registration, Registration): raise TypeError("graph analysis needs explicit trusted Registration")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return _analysis_at(db, core, reference, registration, _binding_at(db, core))


class GraphMergeAnalyzer:
    def __init__(self, gateway, registration, producer_principal_ref):
        with gateway._core.event_store.connect() as db:
            db.execute("BEGIN")
            _binding_at(db, gateway._core)
        self.author = GraphModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = self.author.core, gateway, registration
        self.producer, self.binding = self.author.producer, self.author.binding
        self.schemas = {}
        for schema in (ANALYSIS_SCHEMA, ANALYSIS_COMMAND_SCHEMA):
            body = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": body})

    def analyze(self, *, local_ref, incoming_ref, command_id, base_ref=None):
        key = _key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("graph analyzer uses a stale owner writer")
        request = {"local_revision_ref": _reference(local_ref, self.binding).to_dict(),
            "incoming_revision_ref": _reference(incoming_ref, self.binding).to_dict(),
            "asserted_base_revision_ref": None if base_ref is None else _reference(base_ref, self.binding).to_dict()}
        selected_schemas = dict(self.schemas)
        authorities = {schema: {"resource_id": str(selected.resource_id), "resource_version_id": str(selected.resource_version_id)}
            for schema, selected in selected_schemas.items()}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding): raise RegistryConflict("graph analyzer binding changed")
            document, command = _prepare_at(db, self.core, self.registration, binding, self.producer, command_id, request, authorities)
        command_ref = _material_ref(self.core, binding, key + ":command")
        try:
            for role, schema, body, descriptors in (
                ("command", ANALYSIS_COMMAND_SCHEMA, command, {ANALYSIS_COMMAND_MARKER: ANALYSIS_COMMAND_SCHEMA}),
                ("analysis", ANALYSIS_SCHEMA, document, {ANALYSIS_MARKER: canonical_text(command_ref.to_dict())})):
                def final_proof(_objects):
                    expected_resource = _material_ref(self.core, binding, key + ":analysis").ref
                    if (len(_objects) != 1 or _objects[0].object_type != "resource_version/v1"
                            or _objects[0].logical_id != expected_resource.resource_id
                            or _objects[0].version_id != expected_resource.resource_version_id
                            or not _same_json(_objects[0].metadata, command["analysis_material"]["metadata"])
                            or self.core.object_store.read_registered(_objects[0]) != canonical_json(document)):
                        raise RegistryConflict("prepared graph analysis differs from frozen actual material")
                    with self.core.event_store.connect() as db:
                        db.execute("BEGIN")
                        selected_binding = _binding_at(db, self.core)
                        latest = _prepare_at(db, self.core, self.registration, selected_binding, self.producer, command_id, request, authorities)
                        stored, metadata = _material(db, self.core, command_ref, selected_binding, ANALYSIS_COMMAND_SCHEMA)
                        expected_metadata = _metadata(self.core, selected_binding, command_ref, ANALYSIS_COMMAND_SCHEMA,
                            authorities[ANALYSIS_COMMAND_SCHEMA], command, "Graph merge command",
                            {ANALYSIS_COMMAND_MARKER: ANALYSIS_COMMAND_SCHEMA})
                        if (not _same_json(latest, (document, command)) or not _same_json(stored, command)
                                or not _same_json(metadata, expected_metadata)
                                or _resource_bytes(db, self.core, command_ref) != canonical_json(command)):
                            raise RegistryConflict("graph analysis dependencies changed after command freeze")
                spec = PublishResource(
                    origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(body), media_type="application/json",
                    content_schema_ref=schema, content_schema_authority_ref=selected_schemas[schema],
                    summary="Graph merge " + role, descriptors=descriptors, lifetime_ref=self.gateway._bootstrap_ref,
                    idempotency_key=key + ":" + role)
                expected_ref = _material_ref(self.core, binding, key + ":" + role)
                if role == "analysis" and self.core.event_store.object_row(expected_ref.ref.resource_version_id) is None:
                    tx = self.core.begin(idempotency_key=key + ":analysis")
                    actual = _publish_private_system(self.core, self.gateway._task_ref, spec, transaction=tx)
                    if tx._objects:
                        tx.validate_before_commit(final_proof)
                        tx.commit()
                else:
                    actual = _publish_private_system(self.core, self.gateway._task_ref, spec)
                if SourceQualifiedResourceRef(binding["source_id"], actual) != expected_ref:
                    raise RegistryConflict("graph analysis material exact reference differs")
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("graph analysis conflicts with immutable complete command") from exc
        return read_graph_merge_analysis(self.core, _material_ref(self.core, binding, key + ":analysis"), self.registration)
