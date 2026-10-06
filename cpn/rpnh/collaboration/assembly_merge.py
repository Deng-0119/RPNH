"""Explicit conflict analysis over genuine flat Assembly v2/v6 histories."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from ..registration import Registration
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.object_store import ObjectIntegrityError
from ..registry.schema_catalog import canonical_json, canonical_text
from .assemblies import _command as _check_command
from .assembly_v2 import _binding_at, _material_ref, AssemblyAuthorV2
from .materials import _material, _same_json, _validate_at as _closed_at, ValidatedClosedRevision
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from ._assembly_merge_support import (owner_ref, principal_at, select_authorities, authority_pins_at,
    material_spec, check_material_at, publish_spec, canonical_descriptor_at, input_material_at, catalog_ref)

ANALYSIS_SCHEMA = "rpnh/collaboration/assembly_merge_analysis/v1"
COMMAND_SCHEMA = "rpnh/collaboration/assembly_merge_analysis_command/v1"
ANALYSIS_MARKER = "assembly_merge_analysis_v1"
COMMAND_MARKER = "assembly_merge_analysis_command_v1"
ALGORITHM = "flat_assembly_complete_history_conflicts/v1"
HISTORY_TYPES = {"collaboration_assembly_revision/v2", "collaboration_assembly_revision/v6"}
PLAN_ATOMS = ("name", "connections", "completion", "budget_policy", "deployment_intent")


class UnresolvedAssemblyMerge(ValueError):
    """An explicit history/choice/whole-composition obligation remains open."""


def _key(command_id):
    _check_command(command_id)
    return "collaboration-assembly-merge-analysis:" + canonical_text({"command_id": command_id})


def _reference(reference, binding):
    if (not isinstance(reference, SourceQualifiedVersionRef) or reference.source_id != binding["source_id"]
            or reference.ref.entity_type not in HISTORY_TYPES):
        raise RegistryConflict("Assembly merge requires exact local v2/v6 history references")
    return reference


def _parents(record):
    return record.parent_revision_refs if record.revision_ref.ref.entity_type.endswith("/v6") else (
        () if record.parent_revision_ref is None else (record.parent_revision_ref,))


def _baseline_leaf_at(db, core, reference, registration, binding, visiting, memo):
    """Do not let future catalog/reader dispatch silently broaden this protocol."""
    from .plain_merge import _plain
    from .plain_merge_result import ValidatedPlainMergeRevision
    from .graph_authoring import ValidatedGraphRevision
    tag = ("assembly-v6-baseline-leaf", reference)
    if reference in visiting or tag in visiting:
        raise RegistryConflict("Assembly merge leaf history contains a cycle")
    if tag in memo:
        return memo[tag]
    value = _closed_at(db, core, reference, registration, binding, visiting)
    if type(value) not in {ValidatedClosedRevision, ValidatedPlainMergeRevision, ValidatedGraphRevision}:
        raise RegistryConflict("unsupported_contract: Assembly merge requires exact accepted-base leaf proofs")
    if type(value) is ValidatedGraphRevision:
        if reference.ref.entity_type != "collaboration_net_revision/v2":
            raise RegistryConflict("Assembly merge graph leaf requires exact v2 proof")
    else:
        _plain(value, core, binding)
    for parent in value.revision.parent_revision_refs:
        _baseline_leaf_at(db, core, parent, registration, binding, visiting | {reference, tag}, memo)
    memo[tag] = value
    return value


def _validate_revision_at(db, core, reference, registration, binding, visiting, memo):
    _reference(reference, binding)
    if reference in visiting:
        raise RegistryConflict("Assembly merge ancestry contains a cycle")
    if reference in memo:
        return memo[reference]
    if reference.ref.entity_type.endswith("/v6"):
        from .assembly_v6 import _validate_at
        value = _validate_at(db, core, reference, registration, binding, visiting, memo)
    else:
        from .assembly_v2 import _validate_at
        value = _validate_at(db, core, reference, registration, binding, visiting)
        for row in value.plan["members"]:
            _baseline_leaf_at(db, core, SourceQualifiedVersionRef.from_dict(row["revision_ref"], catalog=core.catalog),
                registration, binding, visiting | {reference}, memo)
    memo[reference] = value
    return value


def _history_at(db, core, references, registration, binding, visiting, memo):
    from .assembly_v2 import _read_at as read_v2
    from .assembly_v6 import _read_at as read_v6
    records, active = {}, set(visiting)
    def visit(reference):
        _reference(reference, binding)
        if reference in active:
            raise RegistryConflict("Assembly merge complete history contains a cycle")
        if reference in records:
            return
        active.add(reference)
        record = (read_v6 if reference.ref.entity_type.endswith("/v6") else read_v2)(db, core, reference, binding)
        for parent in _parents(record):
            visit(parent)
        records[reference] = record
        active.remove(reference)
    for reference in references:
        visit(reference)
    values = {reference: _validate_revision_at(db, core, reference, registration, binding, visiting, memo) for reference in records}
    return records, values


def _input_proof_pins_at(db, core, registration, binding, producer, history, visiting, memo, *, members=(), analyses=()):
    """Pin the exact finite proof closure, never incidental entries in a cache.

    Existing v2/plain/graph readers prove their supported semantics. This opt-in
    additionally freezes all their input metadata and canonical authority bytes,
    so later catalog composition or a resumed writer cannot change the cut.
    """
    descriptors, materials, leaves_seen, analyses_seen, active_materials = {}, {}, set(), set(), set()
    def descriptor(reference):
        raw = reference.to_dict()["ref"] if isinstance(reference, SourceQualifiedVersionRef) else reference
        key = canonical_text(raw)
        if key not in descriptors:
            _, descriptors[key] = canonical_descriptor_at(db, core, reference)
    def material(reference):
        key = canonical_text(reference.to_dict())
        if key in active_materials:
            raise RegistryConflict("Assembly merge input schema authority contains a cycle")
        if key in materials:
            return materials[key][0]
        active_materials.add(key)
        document, pin = input_material_at(db, core, binding, reference)
        materials[key] = (document, pin)
        authority = pin["metadata"]["content_schema_authority_ref"]
        if authority is not None:
            if "entity_type" in authority:
                descriptor(authority)
            else:
                material(SourceQualifiedResourceRef.from_dict({"schema_version": "rpnh/collaboration/source_resource_ref/v1",
                    "source_id": binding["source_id"], "ref": authority}, catalog=core.catalog))
        active_materials.remove(key)
        return document
    def revision_materials(value):
        descriptor(value.revision.revision_ref)
        descriptor(value.revision.owner_task_ref)
        descriptor(value.revision.producer_principal_ref)
        for field in ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref",
                      "graph_source_ref", "graph_recipe_ref", "graph_source_mapping_ref"):
            reference = getattr(value.revision, field, None)
            if reference is not None:
                material(reference)
        for row in value.host_requirements["declaration_refs"]:
            material(SourceQualifiedResourceRef.from_dict(row["resource_ref"], catalog=core.catalog))
    def leaf(value):
        reference = value.revision.revision_ref
        if reference in leaves_seen:
            return
        leaves_seen.add(reference)
        revision_materials(value)
        for field in ("command_ref", "resolution_ref"):
            reference = getattr(value, field, None)
            if reference is not None:
                material(reference)
        analysis_ref = getattr(value, "analysis_ref", None)
        if analysis_ref is not None:
            document = material(analysis_ref)
            material(SourceQualifiedResourceRef.from_dict(document["command_ref"], catalog=core.catalog))
        for parent in value.revision.parent_revision_refs:
            leaf(_baseline_leaf_at(db, core, parent, registration, binding, visiting, memo))
    def analysis(value):
        if value.analysis_ref in analyses_seen:
            return
        analyses_seen.add(value.analysis_ref)
        material(value.analysis_ref)
        material(value.command_ref)
    for field in ("binding_ref", "task_ref", "native_run_ref", "bootstrap_command_ref"):
        descriptor(binding[field])
    descriptor(producer)
    descriptor(catalog_ref(db))
    for value in history.values():
        record = value.revision
        descriptor(record.revision_ref)
        descriptor(record.owner_task_ref)
        descriptor(record.producer_principal_ref)
        for field in ("plan_ref", "compiled_inventory_ref", "lowering_mapping_ref"):
            material(getattr(record, field))
        revision_materials(value.generated)
        plan = value.plan["composition"] if record.revision_ref.ref.entity_type.endswith("/v6") else value.plan
        for row in plan["members"]:
            leaf(_baseline_leaf_at(db, core, SourceQualifiedVersionRef.from_dict(row["revision_ref"], catalog=core.catalog),
                registration, binding, visiting, memo))
        if getattr(value, "analysis", None) is not None:
            analysis(value.analysis)
    for value in members:
        leaf(value)
    for value in analyses:
        analysis(value)
    return {"descriptors": [descriptors[key] for key in sorted(descriptors)],
        "materials": [materials[key][1] for key in sorted(materials)]}


def _ancestors(records, reference):
    found, pending = set(), [reference]
    while pending:
        item = pending.pop()
        if item in found:
            continue
        found.add(item)
        pending.extend(_parents(records[item]))
    return found


def _nearest(records, left, right):
    common = _ancestors(records, left) & _ancestors(records, right)
    return sorted((item for item in common if not any(item in _ancestors(records, other)
        for other in common - {item})), key=lambda item: canonical_json(item.to_dict()))


def _author_plan(value):
    plan = value.plan["composition"] if value.revision.revision_ref.ref.entity_type.endswith("/v6") else value.plan
    return {**{key: deepcopy(plan[key]) for key in PLAN_ATOMS}, "members": [
        {key: deepcopy(row[key]) for key in ("member_id", "display_name", "revision_ref")} for row in plan["members"]]}


def _atoms(plan):
    return {**{key: deepcopy(plan[key]) for key in PLAN_ATOMS},
        **{"member/" + row["member_id"]: deepcopy(row) for row in plan["members"]}}


def _compare(base, left, right):
    values = {role: _atoms(plan) for role, plan in (("base", base), ("left", left), ("right", right))}
    subjects = sorted(set().union(*(set(value) for value in values.values())))
    changed = {role: {subject for subject in subjects if not _same_json(values[role].get(subject), values["base"].get(subject))}
        for role in ("left", "right")}
    structural = lambda subject: subject != "name"
    coupled = bool(any(map(structural, changed["left"])) and any(map(structural, changed["right"])))
    coupled_subjects = ({subject for subject in changed["left"] | changed["right"] if structural(subject)} |
        {"connections", "completion", "budget_policy", "deployment_intent"}) if coupled else set()
    atoms = []
    for subject in subjects:
        b, l, r = (values[role].get(subject) for role in ("base", "left", "right"))
        direct = not _same_json(l, r) and not _same_json(l, b) and not _same_json(r, b)
        conflict = direct or subject in coupled_subjects
        reason = "coupled_assembly_contract" if conflict else "unilateral" if subject in changed["left"] | changed["right"] else "equal"
        if direct:
            reason = "author_atom_conflict"
        if (subject.startswith("member/") and isinstance(b, dict) and isinstance(l, dict) and isinstance(r, dict)
                and l["revision_ref"] != b["revision_ref"] and r["revision_ref"] != b["revision_ref"]
                and l["revision_ref"] != r["revision_ref"]):
            reason = "member_version_competition"
        selected = l if _same_json(l, r) or _same_json(r, b) else r
        atoms.append({"subject": subject, "base": b, "left": l, "right": r, "conflict": conflict,
            "reason": reason, "selected": None if conflict else selected})
    return atoms


def resolve(document, choices):
    if document["status"] != "analyzed":
        raise UnresolvedAssemblyMerge("Assembly merge requires one unique nearest common base")
    if not isinstance(choices, (tuple, list)):
        raise TypeError("Assembly merge requires explicit choice rows")
    selected = {}
    for choice in choices:
        if not isinstance(choice, dict) or set(choice) not in ({"subject", "choice", "reason"}, {"subject", "choice", "reason", "value"}):
            raise UnresolvedAssemblyMerge("Assembly merge choices require exact subject, choice and reason fields")
        subject, decision, reason = choice["subject"], choice["choice"], choice["reason"]
        if (not isinstance(subject, str) or subject in selected or not isinstance(reason, str) or not reason.strip()
                or reason != reason.strip() or any(ord(c) < 32 for c in reason)):
            raise UnresolvedAssemblyMerge("Assembly merge choices must be unique and have nonempty canonical reasons")
        if decision not in {"left", "right", "base", "delete", "exact"} or ("value" in choice) != (decision == "exact"):
            raise UnresolvedAssemblyMerge("Assembly merge has an unknown or malformed choice")
        selected[subject] = deepcopy(choice)
    required = {row["subject"] for row in document["atoms"] if row["conflict"]}
    if set(selected) != required:
        raise UnresolvedAssemblyMerge("Assembly merge choices must cover exactly every conflict subject")
    result = {}
    for row in document["atoms"]:
        subject = row["subject"]
        if not row["conflict"]:
            value = deepcopy(row["selected"])
        else:
            choice = selected[subject]
            decision = choice["choice"]
            if decision == "delete":
                if not subject.startswith("member/"):
                    raise UnresolvedAssemblyMerge("only an exact member subject can be deleted")
                value = None
            else:
                value = deepcopy(choice["value"] if decision == "exact" else row[decision])
                if decision == "exact" and subject.startswith("member/") and value is None:
                    raise UnresolvedAssemblyMerge("exact member selection requires a complete member row; use explicit delete")
        if subject.startswith("member/"):
            if value is not None and (not isinstance(value, dict) or set(value) != {"member_id", "display_name", "revision_ref"}
                    or value["member_id"] != subject.removeprefix("member/")):
                raise UnresolvedAssemblyMerge("chosen member must preserve its exact stable subject identity")
        elif value is None:
            raise UnresolvedAssemblyMerge("Assembly plan atoms cannot be deleted")
        result[subject] = value
    plan = {key: result[key] for key in PLAN_ATOMS}
    plan["members"] = [result[key] for key in sorted(result) if key.startswith("member/") and result[key] is not None]
    return plan, [selected[key] for key in sorted(selected)]


def _prepare_at(db, core, registration, binding, producer, command_id, request, authorities, visiting, memo):
    principal_at(db, core, binding, producer)
    if not isinstance(request, dict) or set(request) != {"local_revision_ref", "incoming_revision_ref"}:
        raise ValueError("Assembly analysis has exactly local and incoming references, without base override")
    left, right = (SourceQualifiedVersionRef.from_dict(request[key], catalog=core.catalog)
        for key in ("local_revision_ref", "incoming_revision_ref"))
    if left == right:
        raise ValueError("Assembly merge requires two distinct exact heads")
    records, values = _history_at(db, core, (left, right), registration, binding, visiting, memo)
    candidates = _nearest(records, left, right)
    base = candidates[0] if len(candidates) == 1 else None
    inventory = []
    for reference in sorted(records, key=lambda item: canonical_json(item.to_dict())):
        value, record = values[reference], records[reference]
        inventory.append({"revision_ref": reference.to_dict(), "parent_revision_refs": [r.to_dict() for r in _parents(record)],
            **{key: getattr(record, key).to_dict() for key in ("plan_ref", "generated_revision_ref", "compiled_inventory_ref", "lowering_mapping_ref")},
            "plan_bytes": len(canonical_json(value.plan)), "plan_sha256": hashlib.sha256(canonical_json(value.plan)).hexdigest()})
    roots = sorted((ref for ref, record in records.items() if not _parents(record)), key=lambda item: canonical_json(item.to_dict()))
    if base is not None and (len(roots) != 1 or left.ref.entity_id != right.ref.entity_id or roots[0].ref.entity_id != left.ref.entity_id):
        raise RegistryConflict("Assembly merge common ancestry differs from exact logical root")
    key = _key(command_id)
    command_ref = _material_ref(core, binding, key + ":command")
    document = {"schema_version": ANALYSIS_SCHEMA, "algorithm": ALGORITHM, "source_id": binding["source_id"],
        "owner_task_ref": owner_ref(binding, core).to_dict(), "producer_principal_ref": producer.to_dict(), "command_id": command_id,
        "command_ref": command_ref.to_dict(), "request": deepcopy(request), "status": "analyzed" if base is not None else "unresolved_history",
        "base_revision_ref": None if base is None else base.to_dict(), "nearest_common_bases": [ref.to_dict() for ref in candidates],
        "history_roots": [ref.to_dict() for ref in roots], "ancestry": inventory,
        "input_proof_pins": _input_proof_pins_at(db, core, registration, binding, producer, values, visiting, memo),
        "atoms": [] if base is None else _compare(_author_plan(values[base]), _author_plan(values[left]), _author_plan(values[right]))}
    pins = authority_pins_at(db, core, binding, authorities, (ANALYSIS_SCHEMA, COMMAND_SCHEMA))
    spec = material_spec(core, binding, "analysis", key + ":analysis", ANALYSIS_SCHEMA, authorities[ANALYSIS_SCHEMA], document,
        "Assembly merge analysis", {ANALYSIS_MARKER: canonical_text(command_ref.to_dict())})
    command = {"schema_version": COMMAND_SCHEMA, "analysis": document, "schema_authorities": deepcopy(authorities),
        "schema_authority_pins": pins, "prepared_analysis": spec}
    core.catalog.validate_schema_ref(COMMAND_SCHEMA, command)
    return document, command


def _command_spec(core, binding, command):
    return material_spec(core, binding, "command", _key(command["analysis"]["command_id"]) + ":command", COMMAND_SCHEMA,
        command["schema_authorities"][COMMAND_SCHEMA], command, "Assembly merge complete analysis command", {COMMAND_MARKER: COMMAND_SCHEMA})


@dataclass(frozen=True)
class ValidatedAssemblyMergeAnalysis:
    analysis_ref: SourceQualifiedResourceRef
    command_ref: SourceQualifiedResourceRef
    document: dict


def _read_at(db, core, reference, registration, binding, visiting=None, memo=None):
    visiting, memo = set() if visiting is None else visiting, {} if memo is None else memo
    tag = (ANALYSIS_SCHEMA, reference)
    if tag in visiting:
        raise RegistryConflict("Assembly merge analysis/proof cycle")
    if tag in memo:
        return memo[tag]
    document, _ = _material(db, core, reference, binding, ANALYSIS_SCHEMA)
    command_ref = SourceQualifiedResourceRef.from_dict(document["command_ref"], catalog=core.catalog)
    command, _ = _material(db, core, command_ref, binding, COMMAND_SCHEMA)
    producer = SourceQualifiedVersionRef.from_dict(document["producer_principal_ref"], catalog=core.catalog)
    expected, expected_command = _prepare_at(db, core, registration, binding, producer, document["command_id"], document["request"],
        command["schema_authorities"], visiting | {tag}, memo)
    if (reference != _material_ref(core, binding, _key(document["command_id"]) + ":analysis")
            or command_ref != _material_ref(core, binding, _key(document["command_id"]) + ":command")
            or not _same_json((document, command), (expected, expected_command))):
        raise RegistryConflict("Assembly analysis differs from exact immutable command and full history")
    check_material_at(db, core, binding, _command_spec(core, binding, expected_command))
    check_material_at(db, core, binding, expected_command["prepared_analysis"])
    value = ValidatedAssemblyMergeAnalysis(reference, command_ref, document)
    memo[tag] = value
    return value


def read_assembly_merge_analysis(core, reference, registration):
    if not isinstance(registration, Registration):
        raise TypeError("Assembly analysis requires explicit trusted Registration")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return _read_at(db, core, reference, registration, _binding_at(db, core))


class AssemblyMergeAnalyzer:
    def __init__(self, gateway, registration, producer_principal_ref):
        base = AssemblyAuthorV2(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = base.core, gateway, registration
        self.binding, self.producer = base.binding, base.producer
        self.schemas = {}
        for schema in (ANALYSIS_SCHEMA, COMMAND_SCHEMA):
            body = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": body})

    def _publish_spec(self, spec):
        return publish_spec(self, spec)

    def analyze(self, *, local_ref, incoming_ref, command_id):
        _key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("Assembly merge analyzer uses a stale writer")
        request = {"local_revision_ref": local_ref.to_dict(), "incoming_revision_ref": incoming_ref.to_dict()}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("Assembly merge source binding changed")
            document, command = _prepare_at(db, self.core, self.registration, binding, self.producer,
                command_id, request, select_authorities(db, self.schemas), set(), {})
        try:
            self._publish_spec(_command_spec(self.core, binding, command))
            # The analysis itself is a published proof, so a committed command
            # prefix must still match the current exact input cut before it exists.
            with self.core.event_store.connect() as db:
                db.execute("BEGIN")
                final_binding = _binding_at(db, self.core)
                final_document, final_command = _prepare_at(db, self.core, self.registration, final_binding,
                    self.producer, command_id, request, command["schema_authorities"], set(), {})
                if not _same_json((final_document, final_command), (document, command)):
                    raise RegistryConflict("Assembly analysis input proof changed after its first command")
                check_material_at(db, self.core, final_binding, _command_spec(self.core, final_binding, final_command))
            self._publish_spec(command["prepared_analysis"])
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("Assembly merge analysis conflicts with complete immutable command") from exc
        return read_assembly_merge_analysis(self.core, _material_ref(self.core, binding, _key(command_id) + ":analysis"), self.registration)
