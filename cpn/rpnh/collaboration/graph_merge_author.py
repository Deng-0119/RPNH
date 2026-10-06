"""Complete ordinary graph merge/edit material production and independent proof."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
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
from .assembly_v2 import _binding_at, _material_ref, _host_at
from .assemblies import _command as _check_command
from .graph_authoring import GRAPH_DOCUMENT_SCHEMAS, GRAPH_REF_FIELDS, _ordinary_host_selection
from .graph_source import (
    GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, _validate, rebuild_graph_module,
    make_graph_source_map, graph_element_map,
)
from .graph_merge import (
    GraphMergeAnalyzer, GraphMergeNetRevision, ValidatedGraphMergeRevision,
    MERGE_REVISION_TYPE, MERGE_REVISION_SCHEMA, _analysis_at,
)
from .materials import _material, _same_json, _boundaries, _validate_at
from .plain_merge_result import _metadata, _schema_authority_at, _resource_bytes
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from ._graph_merge_model import RESOLUTION, UnresolvedGraphMerge, normalize, reconstruct, resolve

RESOLUTION_SCHEMA = "rpnh/collaboration/graph_merge_resolution/v1"
COMMAND_SCHEMA = "rpnh/collaboration/graph_merge_author_command/v1"
RESULT_MARKER = "graph_merge_author_command_v1"
SCHEMAS = (RESOLUTION_SCHEMA, *GRAPH_DOCUMENT_SCHEMAS)
ROLES = ("resolution", "source", "recipe", "source_mapping", "definition", "element_mapping", "boundary_mapping", "host_requirements")


def _key(command_id):
    _check_command(command_id)
    return "collaboration-graph-merge-author:" + canonical_text({"command_id": command_id})


def _result_ref(core, binding, command_id, parents):
    if not parents or len(set(parents)) != len(parents) or len({ref.ref.entity_id for ref in parents}) != 1:
        raise UnresolvedGraphMerge("result requires distinct same-lineage exact graph parents")
    version = TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": _key(command_id), "task": str(core.task_id), "source": binding["source_id"], "kind": "resource_version"})).hex)
    return SourceQualifiedVersionRef(binding["source_id"], VersionRef(MERGE_REVISION_TYPE, parents[0].ref.entity_id, version))


def _prepare_at(db, core, registration, binding, producer, command_id, request, authorities, visiting):
    key = _key(command_id)
    if producer.source_id != binding["source_id"] or producer.ref.entity_type != "principal/v1":
        raise RegistryConflict("graph author producer must be an exact local principal")
    principal = exact_descriptor(db, core.object_store, core.task_id, producer.to_dict()["ref"])
    if principal["principal_id"] != str(producer.ref.entity_id) or principal["principal_version_id"] != str(producer.ref.version_id):
        raise RegistryConflict("graph author producer self differs")
    request = deepcopy(request)
    ref = lambda body: SourceQualifiedVersionRef.from_dict(body, catalog=core.catalog)
    kind = request.get("kind")
    if kind == "merge":
        if set(request) != {"kind", "analysis_ref", "choices"}: raise RegistryConflict("unknown graph merge request")
        analysis_ref = SourceQualifiedResourceRef.from_dict(request["analysis_ref"], catalog=core.catalog)
        analysis = _analysis_at(db, core, analysis_ref, registration, binding, visiting)
        atoms, choices = resolve(analysis.document, request["choices"])
        parents = tuple(ref(analysis.document["request"][name]) for name in ("local_revision_ref", "incoming_revision_ref"))
        reference = _result_ref(core, binding, command_id, parents)
        source, recipe, ids, origins = reconstruct(atoms)
        for identity, origin in origins.items():
            source_kind = atoms[identity + "/identity"]["kind"]
            candidates = [analysis.document["normalized"][side]["atoms"] for side in ("local", "incoming")]
            if (origin.get("element_id") != identity or not any(
                    _same_json(model.get(identity + "/origin"), origin)
                    and model.get(identity + "/identity", {}).get("kind") == source_kind for model in candidates)):
                raise UnresolvedGraphMerge("selected origin does not prove this exact source identity kind")
        source_map = make_graph_source_map(source, ids)
        parent = None
    elif kind == "edit":
        if set(request) != {"kind", "parent_ref", "source", "recipe", "source_ids", "copy_sources"}:
            raise RegistryConflict("unknown graph descendant edit request")
        parents = (ref(request["parent_ref"]),)
        if parents[0].ref.entity_type != MERGE_REVISION_TYPE:
            raise UnresolvedGraphMerge("graph-v3 edit cannot fabricate an upgraded v2 parent")
        parent = _validate_at(db, core, parents[0], registration, binding, visiting)
        if not isinstance(parent, ValidatedGraphMergeRevision): raise RegistryConflict("edit needs complete graph-v3 parent proof")
        reference = _result_ref(core, binding, command_id, parents)
        source, recipe = _validate(GRAPH_SOURCE_SCHEMA, request["source"]), _validate(GRAPH_RECIPE_SCHEMA, request["recipe"])
        source_map = make_graph_source_map(source, request["source_ids"], parent, request["copy_sources"])
        origins = {row["element_id"]: deepcopy(parent.identity_origins[row["element_id"]]) if row["element_id"] in parent.identity_origins else {
            "revision_ref": reference.to_dict(), "element_id": row["element_id"], "copied_from": deepcopy(row["copied_from"])}
            for row in source_map["elements"]}
        choices, analysis_ref = [], None
    else:
        raise RegistryConflict("unknown graph-v3 author kind")
    module = rebuild_graph_module(source, recipe)
    _ordinary_host_selection(module, registration)
    compiled = compile_module(module, registration)
    host = _host_at(db, core, binding, compiled)
    if recipe["declaration_refs"] and not _same_json(recipe["declaration_refs"], host["declaration_refs"]):
        raise UnresolvedGraphMerge("selected complete recipe differs from actual exact HOST consumption")
    if kind == "merge" and not recipe["declaration_refs"]:
        raise UnresolvedGraphMerge("merged recipe must retain a complete validated exact HOST selection")
    recipe["declaration_refs"] = deepcopy(host["declaration_refs"])
    if kind == "edit": request["recipe"] = deepcopy(recipe)
    element_map = graph_element_map(module, source, source_map, parent)
    boundary = _boundaries(module, element_map)
    rebuilt_atoms = normalize(SimpleNamespace(source=source, recipe=recipe, source_map=source_map), origins)["atoms"]
    if kind == "merge" and not _same_json(rebuilt_atoms, atoms):
        raise UnresolvedGraphMerge("merged graph/recipe does not losslessly round-trip all selected atoms")
    resolution = {"schema_version": RESOLUTION_SCHEMA, "algorithm": RESOLUTION, "kind": kind,
        "analysis_ref": None if analysis_ref is None else analysis_ref.to_dict(), "choices": choices,
        "identity_origins": origins, "selected_atoms": rebuilt_atoms}
    documents = (resolution, source, recipe, source_map, module.to_dict(), element_map, boundary, host)
    if set(authorities) != set((*SCHEMAS, COMMAND_SCHEMA)):
        raise RegistryConflict("graph command must freeze every exact schema authority")
    digests = {schema: _schema_authority_at(db, core, binding, schema, authority) for schema, authority in authorities.items()}
    command_ref = _material_ref(core, binding, key + ":command")
    specs = []
    for role, schema, document in zip(ROLES, SCHEMAS, documents, strict=True):
        core.catalog.validate_schema_ref(schema, document)
        selected = _material_ref(core, binding, key + ":" + role)
        payload = canonical_json(document)
        metadata = _metadata(core, binding, selected, schema, authorities[schema], document,
            "Graph merge " + role, {RESULT_MARKER: canonical_text(command_ref.to_dict())})
        specs.append({"role": role, "schema": schema, "resource_ref": selected.to_dict(), "document": document,
            "metadata": metadata, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    owner = SourceQualifiedVersionRef.from_dict({"schema_version": "rpnh/collaboration/source_version_ref/v1",
        "source_id": binding["source_id"], "ref": binding["task_ref"]}, catalog=core.catalog)
    command = {"schema_version": COMMAND_SCHEMA, "algorithm": RESOLUTION, "source_id": binding["source_id"],
        "owner_task_ref": owner.to_dict(), "producer_principal_ref": producer.to_dict(), "command_id": command_id,
        "request": request, "result_revision_ref": reference.to_dict(), "parent_revision_refs": [p.to_dict() for p in parents],
        "schema_authorities": deepcopy(authorities), "schema_authority_sha256": digests, "prepared_materials": specs}
    json.dumps(command, allow_nan=False)
    core.catalog.validate_schema_ref(COMMAND_SCHEMA, command)
    refs = [SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog) for spec in specs]
    record = GraphMergeNetRevision(reference, owner, producer, command_id, kind, parents, *refs[1:], command_ref, refs[0])
    return command, record, module, compiled, element_map, boundary, host, source, recipe, source_map, origins


def _validate_graph_merge_at(db, core, revision, registration, binding, visiting):
    binding = _binding_at(db, core)
    command_ref = revision.author_command_ref
    tag = (COMMAND_SCHEMA, command_ref)
    if tag in visiting: raise RegistryConflict("graph author command/proof cycle")
    visiting = visiting | {tag}
    command, command_metadata = _material(db, core, command_ref, binding, COMMAND_SCHEMA)
    producer = SourceQualifiedVersionRef.from_dict(command["producer_principal_ref"], catalog=core.catalog)
    result = _prepare_at(db, core, registration, binding, producer, command["command_id"], command["request"], command["schema_authorities"], visiting)
    expected, record, module, compiled, element_map, boundary, host, source, recipe, source_map, origins = result
    expected_ref = _material_ref(core, binding, _key(command["command_id"]) + ":command")
    metadata = _metadata(core, binding, expected_ref, COMMAND_SCHEMA, command["schema_authorities"][COMMAND_SCHEMA],
        expected, "Graph merge complete command", {RESULT_MARKER: COMMAND_SCHEMA})
    if (command_ref != expected_ref or not _same_json(revision.to_dict(), record.to_dict())
            or not _same_json(command, expected) or not _same_json(command_metadata, metadata)
            or _resource_bytes(db, core, command_ref) != canonical_json(expected)):
        raise RegistryConflict("graph-v3 differs from its complete immutable source/recipe command")
    for spec in expected["prepared_materials"]:
        reference = SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog)
        body, actual_metadata = _material(db, core, reference, binding, spec["schema"])
        payload = _resource_bytes(db, core, reference)
        if (payload != canonical_json(spec["document"]) or len(payload) != spec["bytes"]
                or hashlib.sha256(payload).hexdigest() != spec["sha256"]
                or not _same_json(body, spec["document"]) or not _same_json(actual_metadata, spec["metadata"])):
            raise RegistryConflict("graph-v3 material differs from exact frozen bytes/metadata")
    return ValidatedGraphMergeRevision(revision, module, compiled, element_map, boundary, host,
        source, recipe, source_map, origins, command_ref, revision.resolution_ref)


class GraphMergeAuthor:
    def __init__(self, gateway, registration, producer_principal_ref):
        self.analyzer = GraphMergeAnalyzer(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = self.analyzer.core, gateway, registration
        self.binding, self.producer = self.analyzer.binding, self.analyzer.producer
        self.core.catalog.require(MERGE_REVISION_TYPE, category="object")
        self.schemas = dict(self.analyzer.author.schemas)
        for schema in (RESOLUTION_SCHEMA, COMMAND_SCHEMA):
            document = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def publish(self, *, analysis_ref, choices, command_id):
        if not isinstance(choices, (list, tuple)):
            raise TypeError("graph merge choices must be an explicit sequence")
        return self._publish({"kind": "merge", "analysis_ref": analysis_ref.to_dict(), "choices": list(deepcopy(choices))}, command_id)

    def publish_edit(self, *, source, recipe, source_ids, command_id, parent_ref, copy_sources=None):
        # HOST declarations may be registered as setup, before the first command
        # lock. Their actual exact resource selections are frozen in that command.
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("graph edit publisher uses a stale owner writer")
        module = rebuild_graph_module(source, recipe)
        _ordinary_host_selection(module, self.registration)
        compiled = compile_module(module, self.registration)
        for kind, entries in sorted(compiled.to_dict()["registrations"].items()):
            for key, declaration in sorted(entries.items()):
                if (kind, key) not in self.gateway.declaration_refs:
                    if kind == "schema" and key in PROTECTED_SCHEMA_REFS: self.gateway.bind_builtin_schema(key)
                    else: self.gateway(kind, key, declaration)
        return self._publish({"kind": "edit", "parent_ref": parent_ref.to_dict(), "source": deepcopy(source),
            "recipe": deepcopy(recipe), "source_ids": dict(source_ids), "copy_sources": dict(copy_sources or {})}, command_id)

    def _publish(self, request, command_id):
        from .materials import validate_closed_revision
        key = _key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("graph author uses a stale owner writer")
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding): raise RegistryConflict("graph publisher binding changed")
            catalogs = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='registry_type_catalog/v1'").fetchall()
            if len(catalogs) != 1: raise RegistryConflict("graph author needs one exact frozen catalog")
            protected = {"entity_type": "registry_type_catalog/v1", "logical_id": catalogs[0]["logical_id"], "version_id": catalogs[0]["version_id"]}
            authorities = {schema: protected if self.schemas[schema] is None else {
                "resource_id": str(self.schemas[schema].resource_id), "resource_version_id": str(self.schemas[schema].resource_version_id)}
                for schema in (*SCHEMAS, COMMAND_SCHEMA)}
            command, revision, *_ = _prepare_at(db, self.core, self.registration, binding, self.producer,
                command_id, request, authorities, set())
        if self.core.event_store.object_row(revision.revision_ref.ref.version_id) is not None:
            prior = validate_closed_revision(self.core, revision.revision_ref, self.registration)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                stored, _ = _material(db, self.core, prior.command_ref, _binding_at(db, self.core), COMMAND_SCHEMA)
            if not _same_json(command, stored) or prior.revision != revision:
                raise RegistryConflict("graph author command conflicts with original complete result")
            return prior
        command_ref = revision.author_command_ref
        metadata = _metadata(self.core, binding, command_ref, COMMAND_SCHEMA, authorities[COMMAND_SCHEMA],
            command, "Graph merge complete command", {RESULT_MARKER: COMMAND_SCHEMA})
        specs = [{"role": "command", "schema": COMMAND_SCHEMA, "resource_ref": command_ref.to_dict(),
                  "document": command, "metadata": metadata}, *command["prepared_materials"]]
        try:
            for spec in specs:
                meta = spec["metadata"]
                authority = meta["content_schema_authority_ref"]
                selected = _version_from_payload(authority) if "entity_type" in authority else ResourceVersionRef(
                    TypedId.parse(authority["resource_id"], expected="resource"), TypedId.parse(authority["resource_version_id"], expected="resource_version"))
                resource = _publish_private_system(self.core, self.gateway._task_ref, PublishResource(
                    origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(spec["document"]),
                    media_type=meta["media_type"], content_schema_ref=spec["schema"], content_schema_authority_ref=selected,
                    summary=meta["summary"], descriptors=meta["descriptors"], extensions=meta["extensions"],
                    lifetime_ref=self.gateway._bootstrap_ref, idempotency_key=key + ":" + spec["role"]))
                if SourceQualifiedResourceRef(binding["source_id"], resource).to_dict() != spec["resource_ref"]:
                    raise RegistryConflict("graph material identity differs from prepared exact command")
            tx = self.core.begin(idempotency_key=key)
            tx.prewrite(object_type=MERGE_REVISION_TYPE, logical_id=revision.revision_ref.ref.entity_id,
                version_id=revision.revision_ref.ref.version_id, payload=canonical_json(revision.to_dict()),
                metadata=revision.to_dict(), media_type="application/json", schema_ref=MERGE_REVISION_SCHEMA)
            def final_proof(_objects):
                with self.core.event_store.connect() as db:
                    db.execute("BEGIN")
                    _validate_graph_merge_at(db, self.core, revision, self.registration, _binding_at(db, self.core), {revision.revision_ref})
            tx.validate_before_commit(final_proof)
            tx.commit()
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("graph author conflicts with immutable complete command") from exc
        return validate_closed_revision(self.core, revision.revision_ref, self.registration)
