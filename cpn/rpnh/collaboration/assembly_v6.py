"""Full-history flat Assembly merge results and explicit one-parent successors.

Assembly owns both merge parents. Its generated ordinary revision independently
continues the left generated declaration; it is never a substitute merge proof.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import uuid

from ..registry._event_store.collaboration_descriptors import exact_descriptor
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.object_store import ObjectIntegrityError
from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS, canonical_json, canonical_text
from .assemblies import AssemblyConnection, AssemblyCompletion, _command as _check_command
from .authoring import NetRevision, NET_REVISION_TYPE, NET_REVISION_SCHEMA, _exact_object_ref
from .assembly_v2 import (AssemblyAuthorV2, AssemblyMemberV2, _binding_at, _material_ref, _resolution,
    _host_at, resolver_recipe as composition_recipe)
from .materials import (MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA, ValidatedClosedRevision,
    _material, _same_json, _element_map, _boundaries, _command_material, _validate_at as _closed_at,
    _validate_materials_at as _closed_materials_at)
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .assembly_merge import (AssemblyMergeAnalyzer, UnresolvedAssemblyMerge, _read_at as _analysis_at,
    _history_at, _parents, _baseline_leaf_at, _reference, _input_proof_pins_at, resolve, PLAN_ATOMS)
from ._assembly_merge_support import (owner_ref, principal_at, select_authorities, authority_pins_at,
    object_spec_at, material_spec, check_material_at, publish_spec, canonical_descriptor_at)

ASSEMBLY_V6_TYPE = "collaboration_assembly_revision/v6"
ASSEMBLY_V6_SCHEMA = "registry_v1/collaboration_assembly_revision/v6"
PLAN_V6_SCHEMA = "rpnh/collaboration/assembly_plan/v6"
LOWERING_V6_SCHEMA = "rpnh/collaboration/assembly_lowering_map/v6"
RESOLVER_CONTRACT = "rpnh/collaboration/flat_full_history_assembly_resolver/v1"
COMMAND_MARKER = "assembly_author_command_v6"
MATERIAL_SCHEMAS = (MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA, "rpnh/executable_net/v1", LOWERING_V6_SCHEMA)
ROLES = ("definition", "element_mapping", "boundary_mapping", "host_requirements", "compiled_inventory", "lowering_mapping")


def resolver_recipe():
    return {"contract": RESOLVER_CONTRACT, "history": "actual_v2_v6_complete_parents/v1",
        "leaf_proofs": ["closed_author_v1", "plain_merge_author_v1", "ordinary_graph_author_v2"],
        "composition": composition_recipe(), "generated_history": "explicit_left_ordinary_continuity/v1",
        "material_lock": "exact_schema_authority_bytes_and_complete_metadata/v1"}


def _key(command_id):
    _check_command(command_id)
    return "collaboration-assembly-v6:" + canonical_text({"command_id": command_id})


def _result_ref(core, binding, command_id, parent):
    version = TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": _key(command_id), "task": str(core.task_id), "source": binding["source_id"], "kind": "resource_version"})).hex)
    return SourceQualifiedVersionRef(binding["source_id"], VersionRef(ASSEMBLY_V6_TYPE, parent.ref.entity_id, version))


def _generated_ref(core, binding, command_id, parent):
    command = _key(command_id) + ":generated"
    key = "collaboration-author:" + canonical_text({"command_id": command})
    version = TypedId("resource_version", uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": binding["source_id"], "kind": "resource_version"})).hex)
    reference = SourceQualifiedVersionRef(binding["source_id"], VersionRef(NET_REVISION_TYPE,
        parent.revision.generated_revision_ref.ref.entity_id, version))
    return command, key, reference


@dataclass(frozen=True, slots=True)
class AssemblyRevisionV6:
    revision_ref: SourceQualifiedVersionRef
    owner_task_ref: SourceQualifiedVersionRef
    producer_principal_ref: SourceQualifiedVersionRef
    command_id: str
    operation: str
    parent_revision_refs: tuple[SourceQualifiedVersionRef, ...]
    lineage_root_ref: SourceQualifiedVersionRef
    analysis_ref: SourceQualifiedResourceRef | None
    plan_ref: SourceQualifiedResourceRef
    generated_revision_ref: SourceQualifiedVersionRef
    compiled_inventory_ref: SourceQualifiedResourceRef
    lowering_mapping_ref: SourceQualifiedResourceRef

    def __post_init__(self):
        _exact_object_ref(self.revision_ref, entity_type=ASSEMBLY_V6_TYPE, logical_kind="resource", version_kind="resource_version")
        _exact_object_ref(self.owner_task_ref, entity_type="task/v1", logical_kind="task", version_kind="task_version")
        _exact_object_ref(self.producer_principal_ref, entity_type="principal/v1", logical_kind="principal", version_kind="principal_version")
        _exact_object_ref(self.generated_revision_ref, entity_type=NET_REVISION_TYPE, logical_kind="resource", version_kind="resource_version")
        _exact_object_ref(self.lineage_root_ref, entity_type="collaboration_assembly_revision/v2", logical_kind="resource", version_kind="resource_version")
        _key(self.command_id)
        if self.operation not in {"merge", "edit"} or not isinstance(self.parent_revision_refs, tuple):
            raise ValueError("Assembly v6 requires explicit merge/edit and exact actual parents")
        if len(self.parent_revision_refs) != (2 if self.operation == "merge" else 1) or len(set(self.parent_revision_refs)) != len(self.parent_revision_refs):
            raise ValueError("Assembly v6 requires distinct ordered full merge parents or one edit parent")
        if (self.analysis_ref is not None) != (self.operation == "merge"):
            raise ValueError("Assembly v6 merge requires an exact analysis; edit has none")
        refs = (self.owner_task_ref, self.producer_principal_ref, self.lineage_root_ref, self.generated_revision_ref,
            self.plan_ref, self.compiled_inventory_ref, self.lowering_mapping_ref, *self.parent_revision_refs)
        if self.analysis_ref is not None:
            refs += (self.analysis_ref,)
        if any(ref.source_id != self.revision_ref.source_id for ref in refs):
            raise ValueError("Assembly v6 requires one exact local source")
        for ref in (*self.parent_revision_refs, self.lineage_root_ref):
            _reference(ref, {"source_id": self.revision_ref.source_id})
            if ref == self.revision_ref or ref.ref.entity_id != self.revision_ref.ref.entity_id:
                raise ValueError("Assembly v6 parent/root must precede it in the exact logical history")
        if self.operation == "edit" and self.parent_revision_refs[0].ref.entity_type != ASSEMBLY_V6_TYPE:
            raise ValueError("Assembly v6 successor requires an actual v6 parent")
        materials = (self.plan_ref, self.compiled_inventory_ref, self.lowering_mapping_ref)
        if any(not isinstance(ref, SourceQualifiedResourceRef) for ref in materials) or len(set(materials)) != 3:
            raise ValueError("Assembly v6 requires distinct exact material references")

    def to_dict(self):
        return {"schema_version": ASSEMBLY_V6_SCHEMA, "command_id": self.command_id, "operation": self.operation,
            "parent_revision_refs": [ref.to_dict() for ref in self.parent_revision_refs],
            "analysis_ref": None if self.analysis_ref is None else self.analysis_ref.to_dict(),
            **{key: getattr(self, key).to_dict() for key in ("revision_ref", "owner_task_ref", "producer_principal_ref",
                "lineage_root_ref", "plan_ref", "generated_revision_ref", "compiled_inventory_ref", "lowering_mapping_ref")}}

    @classmethod
    def from_dict(cls, document, *, catalog):
        catalog.validate_instance(ASSEMBLY_V6_TYPE, category="object", instance=document)
        obj = lambda value: SourceQualifiedVersionRef.from_dict(value, catalog=catalog)
        resource = lambda value: SourceQualifiedResourceRef.from_dict(value, catalog=catalog)
        return cls(obj(document["revision_ref"]), obj(document["owner_task_ref"]), obj(document["producer_principal_ref"]),
            document["command_id"], document["operation"], tuple(obj(ref) for ref in document["parent_revision_refs"]),
            obj(document["lineage_root_ref"]), None if document["analysis_ref"] is None else resource(document["analysis_ref"]),
            resource(document["plan_ref"]), obj(document["generated_revision_ref"]), resource(document["compiled_inventory_ref"]),
            resource(document["lowering_mapping_ref"]))


@dataclass(frozen=True)
class ValidatedAssemblyRevisionV6:
    revision: AssemblyRevisionV6
    plan: dict
    generated: ValidatedClosedRevision
    compiled: object
    lowering_map: dict
    members: dict
    analysis: object | None


def _read_at(db, core, reference, binding):
    _exact_object_ref(reference, entity_type=ASSEMBLY_V6_TYPE, logical_kind="resource", version_kind="resource_version")
    if reference.source_id != binding["source_id"]:
        raise RegistryConflict("Assembly v6 source differs from binding")
    record = AssemblyRevisionV6.from_dict(canonical_descriptor_at(db, core, reference)[0], catalog=core.catalog)
    if record.revision_ref != reference or record.owner_task_ref != owner_ref(binding, core):
        raise RegistryConflict("Assembly v6 self identity or exact owner differs")
    principal_at(db, core, binding, record.producer_principal_ref)
    return record


def _composition_at(db, core, author_plan, registration, binding, visiting, memo):
    if not isinstance(author_plan, dict) or set(author_plan) != {*PLAN_ATOMS, "members"}:
        raise UnresolvedAssemblyMerge("Assembly v6 requires the complete exact author plan")
    if author_plan["budget_policy"] != "shared_exact" or author_plan["deployment_intent"] != "same_run_candidate":
        raise UnresolvedAssemblyMerge("Assembly v6 requires explicit shared_exact and same_run_candidate")
    rows, links = author_plan["members"], author_plan["connections"]
    if not isinstance(rows, list) or not rows or not isinstance(links, list):
        raise UnresolvedAssemblyMerge("Assembly v6 requires actual members and exact connections")
    members, locked = {}, []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"member_id", "display_name", "revision_ref"}:
            raise UnresolvedAssemblyMerge("Assembly v6 member row has unknown or missing fields")
        reference = SourceQualifiedVersionRef.from_dict(row["revision_ref"], catalog=core.catalog)
        member = AssemblyMemberV2(row["member_id"], row["display_name"], reference)
        if member.member_id in members:
            raise UnresolvedAssemblyMerge("Assembly v6 member IDs must be unique")
        value = _baseline_leaf_at(db, core, reference, registration, binding, visiting, memo)
        members[member.member_id] = value
        locked.append({**member.to_dict(), "resolution": _resolution(value)})
    if list(members) != sorted(members):
        raise UnresolvedAssemblyMerge("Assembly v6 membership order must be canonical and inert")
    for row in links:
        if not isinstance(row, dict):
            raise UnresolvedAssemblyMerge("Assembly connection must be a complete exact row")
        AssemblyConnection(**row)
    link_keys = [canonical_text(row) for row in links]
    if link_keys != sorted(set(link_keys)):
        raise UnresolvedAssemblyMerge("Assembly connections must be unique and canonically ordered")
    AssemblyCompletion(**author_plan["completion"])
    projection = {**deepcopy(author_plan), "members": locked, "resolver_recipe": composition_recipe()}
    return projection, members


def _provenance(author_plan, analysis, choices, parent):
    atoms = {} if analysis is None else {row["subject"]: row for row in analysis.document["atoms"]}
    selected = {row["subject"]: row for row in choices}
    result = []
    # Include removed members as dispositions, not invented retained origins.
    rows = {"member/" + row["member_id"]: row for row in author_plan["members"]}
    subjects = sorted(set(rows) | {key for key in atoms if key.startswith("member/")})
    for subject in subjects:
        atom, choice, row = atoms.get(subject), selected.get(subject), rows.get(subject)
        result.append({"member_id": subject.removeprefix("member/"), "selected_member": deepcopy(row),
            "base": None if atom is None else deepcopy(atom["base"]), "left": None if atom is None else deepcopy(atom["left"]),
            "right": None if atom is None else deepcopy(atom["right"]), "choice": deepcopy(choice),
            "disposition": "explicit_edit" if analysis is None else "explicit_choice" if choice is not None else "unchanged_or_unilateral",
            "edit_parent_ref": parent.to_dict() if analysis is None else None})
    return result


def _prepare_at(db, core, registration, binding, producer, command_id, request, authorities, visiting, memo):
    from ._assembly_v2_lowering import compose_plan_v2, lowering_map_v2
    principal_at(db, core, binding, producer)
    if not isinstance(request, dict) or request.get("operation") not in {"merge", "edit"}:
        raise ValueError("Assembly v6 requires explicit merge or edit request")
    operation, analysis, choices = request["operation"], None, []
    if operation == "merge":
        if set(request) != {"operation", "analysis_ref", "choices", "generated_continuity"} or request["generated_continuity"] != "left":
            raise ValueError("Assembly merge requires exact analysis, choices and explicit generated_continuity=left")
        analysis_ref = SourceQualifiedResourceRef.from_dict(request["analysis_ref"], catalog=core.catalog)
        analysis = _analysis_at(db, core, analysis_ref, registration, binding, visiting, memo)
        author_plan, choices = resolve(analysis.document, request["choices"])
        parents = tuple(SourceQualifiedVersionRef.from_dict(analysis.document["request"][key], catalog=core.catalog)
            for key in ("local_revision_ref", "incoming_revision_ref"))
    else:
        if set(request) != {"operation", "parent_revision_ref", "author_plan"}:
            raise ValueError("Assembly edit requires one actual parent and complete author plan")
        parent = SourceQualifiedVersionRef.from_dict(request["parent_revision_ref"], catalog=core.catalog)
        if parent.ref.entity_type != ASSEMBLY_V6_TYPE:
            raise ValueError("Assembly v6 ordinary next author step requires an actual v6 parent")
        parents, analysis_ref, author_plan = (parent,), None, deepcopy(request["author_plan"])
    records, history = _history_at(db, core, parents, registration, binding, visiting, memo)
    roots = [ref for ref, record in records.items() if not _parents(record)]
    if len(roots) != 1 or any(ref.ref.entity_id != roots[0].ref.entity_id for ref in records):
        raise RegistryConflict("Assembly v6 requires one exact genuine logical root")
    parent = history[parents[0]]
    reference = _result_ref(core, binding, command_id, parents[0])
    if reference in records:
        raise RegistryConflict("Assembly v6 result cannot be its own ancestor")
    projection, members = _composition_at(db, core, author_plan, registration, binding, visiting | {reference}, memo)
    module, compiled = compose_plan_v2(projection, members, registration)
    lowered, ids = lowering_map_v2(projection, members, module, compiled, str(reference.ref.entity_id))
    elements = _element_map(module, ids, parent.generated, {})
    boundary, host = _boundaries(module, elements), _host_at(db, core, binding, compiled)
    provenance = _provenance(author_plan, analysis, choices, parents[0])
    mapping = {"schema_version": LOWERING_V6_SCHEMA, "assembly_parent_revision_refs": [ref.to_dict() for ref in parents],
        "generated_continuity_parent_ref": parent.revision.generated_revision_ref.to_dict(),
        "member_provenance": provenance, "projection": lowered}
    documents = (module.to_dict(), elements, boundary, host, compiled.to_dict(), mapping)
    generated_command, generated_key, generated_ref = _generated_ref(core, binding, command_id, parent)
    key = _key(command_id)
    keys = (*[f"{generated_key}:material:{index}" for index in range(4)], key + ":compiled", key + ":lowering")
    pins = authority_pins_at(db, core, binding, authorities, (*MATERIAL_SCHEMAS, PLAN_V6_SCHEMA))
    command_material = _command_material(source_id=binding["source_id"], owner=owner_ref(binding, core), producer=producer,
        command_id=generated_command, parents=(parent.revision.generated_revision_ref,), documents=documents[:4])
    specs = []
    for index, (role, material_key, schema, document) in enumerate(zip(ROLES, keys, MATERIAL_SCHEMAS, documents, strict=True)):
        summary = f"Closed author material {index}" if index < 4 else "Assembly v6 " + role
        descriptors = {"closed_author_command_v1": command_material} if index == 0 else {}
        specs.append(material_spec(core, binding, role, material_key, schema, authorities[schema], document, summary, descriptors))
    refs = [SourceQualifiedResourceRef.from_dict(spec["resource_ref"], catalog=core.catalog) for spec in specs]
    generated = NetRevision(generated_ref, owner_ref(binding, core), producer, generated_command, "closed_module", refs[0],
        (parent.revision.generated_revision_ref,), (), refs[1], refs[2], refs[3], None)
    record = AssemblyRevisionV6(reference, owner_ref(binding, core), producer, command_id, operation, parents, roots[0],
        analysis_ref, _material_ref(core, binding, key + ":plan"), generated_ref, refs[4], refs[5])
    plan = {"schema_version": PLAN_V6_SCHEMA, "resolver_recipe": resolver_recipe(), "source_id": binding["source_id"],
        "owner_task_ref": owner_ref(binding, core).to_dict(), "producer_principal_ref": producer.to_dict(), "command_id": command_id,
        "request": deepcopy(request), "parent_revision_refs": [ref.to_dict() for ref in parents], "lineage_root_ref": roots[0].to_dict(),
        "analysis_command_ref": None if analysis is None else analysis.command_ref.to_dict(),
        "base_revision_ref": None if analysis is None else analysis.document["base_revision_ref"], "resolved_choices": choices,
        "generated_continuity_parent_ref": parent.revision.generated_revision_ref.to_dict(), "composition": projection,
        "member_provenance": provenance, "schema_authorities": deepcopy(authorities), "schema_authority_pins": pins,
        "input_proof_pins": _input_proof_pins_at(db, core, registration, binding, producer, history, visiting, memo,
            members=members.values(), analyses=() if analysis is None else (analysis,)),
        "prepared_materials": specs, "prepared_objects": [
            object_spec_at(db, core, "generated", NET_REVISION_TYPE, NET_REVISION_SCHEMA, generated_ref, generated.to_dict()),
            object_spec_at(db, core, "assembly", ASSEMBLY_V6_TYPE, ASSEMBLY_V6_SCHEMA, reference, record.to_dict())]}
    core.catalog.validate_schema_ref(PLAN_V6_SCHEMA, plan)
    return plan, record, generated, parent, members, module, compiled, mapping, analysis


def _plan_spec(core, binding, plan):
    return material_spec(core, binding, "plan", _key(plan["command_id"]) + ":plan", PLAN_V6_SCHEMA,
        plan["schema_authorities"][PLAN_V6_SCHEMA], plan, "Assembly v6 complete command plan", {COMMAND_MARKER: PLAN_V6_SCHEMA})


def _validate_materials_at(db, core, record, registration, binding, visiting, memo):
    plan, _ = _material(db, core, record.plan_ref, binding, PLAN_V6_SCHEMA)
    expected, reconstructed, generated_record, parent, members, module, compiled, mapping, analysis = _prepare_at(
        db, core, registration, binding, record.producer_principal_ref, record.command_id, plan["request"],
        plan["schema_authorities"], visiting, memo)
    if not _same_json((record.to_dict(), plan), (reconstructed.to_dict(), expected)):
        raise RegistryConflict("Assembly v6 differs from complete immutable command and actual resolved history")
    check_material_at(db, core, binding, _plan_spec(core, binding, expected))
    for spec in expected["prepared_materials"]:
        check_material_at(db, core, binding, spec)
    generated = _closed_at(db, core, generated_record.revision_ref, registration, binding, visiting)
    generated_descriptor, generated_pin = canonical_descriptor_at(db, core, generated_record.revision_ref)
    if (type(generated) is not ValidatedClosedRevision or not _same_json(generated.revision.to_dict(), generated_record.to_dict())
            or not _same_json(generated_descriptor, expected["prepared_objects"][0]["document"])
            or not _same_json(generated_pin["metadata"], expected["prepared_objects"][0]["metadata"])
            or not _same_json((generated.module.to_dict(), generated.element_map, generated.boundary_map, generated.host_requirements),
                tuple(spec["document"] for spec in expected["prepared_materials"][:4]))
            or not _same_json(generated.compiled.to_dict(), compiled.to_dict())):
        raise RegistryConflict("Assembly v6 generated strict ordinary G or complete actual composition differs")
    return ValidatedAssemblyRevisionV6(record, plan, generated, compiled, mapping, members, analysis)


def _validate_at(db, core, reference, registration, binding, visiting, memo=None):
    memo = {} if memo is None else memo
    if reference in visiting:
        raise RegistryConflict("Assembly v6 complete parent history contains a cycle")
    if reference in memo:
        return memo[reference]
    record = _read_at(db, core, reference, binding)
    value = _validate_materials_at(db, core, record, registration, binding, visiting | {reference}, memo)
    memo[reference] = value
    return value


class AssemblyAuthorV6:
    """Actual one-parent consumer of full-history v6 results; no runtime action."""
    def __init__(self, gateway, registration, producer_principal_ref):
        base = AssemblyAuthorV2(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = base.core, gateway, registration
        self.binding, self.producer = base.binding, base.producer
        self.core.catalog.require(ASSEMBLY_V6_TYPE, category="object")
        self.schemas = dict(base.author.schemas)
        for schema in (PLAN_V6_SCHEMA, LOWERING_V6_SCHEMA, "rpnh/executable_net/v1"):
            document = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = None if schema in PROTECTED_SCHEMA_REFS else gateway(
                "schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def _publish_spec(self, spec):
        return publish_spec(self, spec)

    def _publish_object(self, spec, key):
        reference = SourceQualifiedVersionRef.from_dict(spec["revision_ref"], catalog=self.core.catalog)
        tx = self.core.begin(idempotency_key=key)
        tx.prewrite(object_type=spec["object_type"], logical_id=reference.ref.entity_id, version_id=reference.ref.version_id,
            payload=canonical_json(spec["document"]), metadata=spec["metadata"], media_type=spec["media_type"], schema_ref=spec["schema"])
        tx.commit()

    def _publish(self, request, command_id):
        from .assemblies import validate_assembly_revision
        _key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("Assembly v6 author uses a stale owner writer")
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("Assembly v6 exact source binding changed")
            prepared = _prepare_at(db, self.core, self.registration, binding, self.producer, command_id, request,
                select_authorities(db, self.schemas), set(), {})
        plan, record, generated, parent, *_ = prepared
        try:
            # This first immutable write includes all metadata and both object descriptors.
            self._publish_spec(_plan_spec(self.core, binding, plan))
            if self.core.event_store.object_row(record.revision_ref.ref.version_id) is not None:
                return validate_assembly_revision(self.core, record.revision_ref, self.registration)
            for spec in plan["prepared_materials"][:4]:
                self._publish_spec(spec)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                fresh = _binding_at(db, self.core)
                # Same-cut strong parent plus first-prefix proof, not saved preparation objects.
                checked = _prepare_at(db, self.core, self.registration, fresh, self.producer, command_id,
                    request, plan["schema_authorities"], {record.revision_ref}, {})
                if not _same_json(checked[0], plan):
                    raise RegistryConflict("Assembly v6 complete preparation changed before generated publication")
                check_material_at(db, self.core, fresh, _plan_spec(self.core, fresh, plan))
                for spec in plan["prepared_materials"][:4]:
                    check_material_at(db, self.core, fresh, spec)
                _closed_materials_at(db, self.core, generated, self.registration, fresh, checked[3].generated)
            generated_key = "collaboration-author:" + canonical_text({"command_id": generated.command_id})
            self._publish_object(plan["prepared_objects"][0], generated_key)
            for spec in plan["prepared_materials"][4:]:
                self._publish_spec(spec)
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                _validate_materials_at(db, self.core, record, self.registration, _binding_at(db, self.core), {record.revision_ref}, {})
            self._publish_object(plan["prepared_objects"][1], _key(command_id))
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("Assembly v6 conflicts with immutable complete first command") from exc
        return validate_assembly_revision(self.core, record.revision_ref, self.registration)

    def publish(self, *, parent_ref, name, members, connections, completion, budget_policy, deployment_intent, command_id):
        if (not isinstance(members, (tuple, list)) or any(type(member) is not AssemblyMemberV2 for member in members)
                or not isinstance(connections, (tuple, list)) or any(type(link) is not AssemblyConnection for link in connections)
                or type(completion) is not AssemblyCompletion):
            raise TypeError("Assembly v6 edit requires explicit accepted typed members, connections and completion")
        plan = {"name": name, "members": sorted((row.to_dict() for row in members), key=lambda row: row["member_id"]),
            "connections": sorted((row.to_dict() for row in connections), key=canonical_text), "completion": completion.to_dict(),
            "budget_policy": budget_policy, "deployment_intent": deployment_intent}
        return self._publish({"operation": "edit", "parent_revision_ref": parent_ref.to_dict(), "author_plan": plan}, command_id)


class AssemblyMergeAuthor(AssemblyAuthorV6):
    def publish(self, *, analysis_ref, choices, generated_continuity, command_id):
        if not isinstance(choices, (tuple, list)):
            raise TypeError("Assembly merge requires an explicit finite choice list")
        return self._publish({"operation": "merge", "analysis_ref": analysis_ref.to_dict(), "choices": deepcopy(list(choices)),
            "generated_continuity": generated_continuity}, command_id)
