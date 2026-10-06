"""Exact donor projection and explicit selective author resolution; no runtime."""
from copy import deepcopy
import hashlib

from ..registry.schema_catalog import canonical_json
from ._plain_merge_model import _same, _state, compare
from ._plain_merge_resolution import UnresolvedPlainMerge, resolve_atoms

ALGORITHM = "rpnh/plain_author_selective_analysis/v1"
RESOLUTION_ALGORITHM = "rpnh/plain_author_selective_resolution/v1"


class UnresolvedPlainTransplant(UnresolvedPlainMerge):
    def __init__(self, reason, subjects=()):
        self.subjects = tuple(sorted(set(subjects)))
        super().__init__(reason + (": " + ", ".join(self.subjects) if self.subjects else ""))


def selection(base, donor, requested):
    if (not isinstance(requested, (list, tuple)) or not requested
            or any(not isinstance(x, str) or not x for x in requested)
            or list(requested) != sorted(set(requested))):
        raise ValueError("transplant requires nonempty sorted unique explicit selected_subjects")
    changed = {key for key in base["atoms"].keys() | donor["atoms"].keys()
               if not _same(_state(base["atoms"], key), _state(donor["atoms"], key))}
    if set(requested) - changed:
        raise ValueError("selection names unknown or unchanged donor subjects: " + ", ".join(sorted(set(requested) - changed)))
    return changed


def project(base, donor, selected):
    """Analysis only: dependencies/carriers are evidence from real B/R proofs.

    This projection is not a compiled declaration. Incomplete identities are
    excluded from helper locator indexes and retained as exact structural gaps.
    """
    atoms, dependencies = deepcopy(base["atoms"]), deepcopy(base["dependencies"])
    for key in selected:
        if key in donor["atoms"]:
            atoms[key] = deepcopy(donor["atoms"][key])
            dependencies[key] = deepcopy(donor["dependencies"].get(key, []))
        else:
            atoms.pop(key, None); dependencies.pop(key, None)
    locators = {identity: locator for identity, locator in {**base["locators"], **donor["locators"]}.items()
                if all(identity + suffix in atoms for suffix in ("/identity", "/name", "/value"))}
    carriers = {canonical_json(row): row for model in (base, donor) for row in model["derived_resource_dependencies"]}
    return {"contract": base["contract"], "atoms": dict(sorted(atoms.items())),
            "dependencies": dict(sorted(dependencies.items())), "locators": dict(sorted(locators.items())),
            "derived_resource_dependencies": [deepcopy(carriers[key]) for key in sorted(carriers)]}


def structural_gaps(model):
    atoms, dependencies = model["atoms"], model["dependencies"]
    identities = {k[:-9]: v for k, v in atoms.items() if k.endswith("/identity")}
    gaps = []
    def gap(reason, subjects, required):
        gaps.append({"reason": reason, "subjects": sorted(set(subjects)), "required_subjects": sorted(set(required))})
    for identity in sorted({k.split("/", 1)[0] for k in atoms if k != "host/selection"}):
        required = [identity + suffix for suffix in ("/identity", "/name", "/value") if identity + suffix not in atoms]
        if required:
            gap("incomplete_identity", [k for k in atoms if k.startswith(identity + "/")], required)
    roots = [identity for identity, row in identities.items() if row == {"kind": "module", "parent": None}]
    if len(roots) != 1:
        gap("module_root", [i + "/identity" for i, row in identities.items() if row["kind"] == "module"],
            [i + "/identity" for i, row in identities.items() if row["kind"] == "module"])
    for identity, row in identities.items():
        if identity in roots: continue
        parent = identities.get(row["parent"])
        expected = "component" if row["kind"] in {"port", "operation"} else "module"
        if parent is None or parent["kind"] != expected:
            gap("parent_kind", [identity + "/identity"], [str(row["parent"]) + "/identity"])
    for root in roots:
        key = root + "/terminal_alternatives_order"
        terminals = {i for i, row in identities.items() if row == {"kind": "terminal", "parent": root}}
        order = atoms.get(key)
        if (not isinstance(order, list) or len(order) != len(set(order))
                or set(order) - terminals or len(terminals - set(order)) != 1):
            gap("terminal_order_primary", [key, *(i + "/identity" for i in terminals)], [key])
    for key, refs in dependencies.items():
        missing = [ref + "/identity" for ref in refs if ref not in identities]
        if missing: gap("missing_dependency", [key], missing)
    fields = {"components_order": "component", "ports_order": "port", "operations_order": "operation", "links_order": "link"}
    for parent, row in sorted(identities.items()):
        names = ("components_order", "links_order") if row["kind"] == "module" else ("ports_order", "operations_order") if row["kind"] == "component" else ()
        for field in names:
            key = parent + "/" + field
            expected = {i for i, v in identities.items() if v == {"kind": fields[field], "parent": parent}}
            order = atoms.get(key)
            if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != expected:
                actual = set(order) if isinstance(order, list) else set()
                gap("order_membership", [key, *(i + "/identity" for i in expected ^ actual)], [key])
    return gaps


def _conflict(reason, subjects, models):
    subjects = sorted(set(subjects))
    payload = {"reason": reason, "subjects": subjects, **{
        role: [{"subject": key, **_state(model["atoms"], key)} for key in subjects]
        for role, model in models.items()}}
    return {"conflict_id": "conflict:" + hashlib.sha256(canonical_json(payload)).hexdigest(), **payload}


def analyze_models(models, requested):
    base, local, donor = (models[role] for role in ("base", "local", "incoming"))
    changed = selection(base, donor, requested)
    selected = set(requested)
    projected = project(base, donor, selected)
    omitted = project(base, donor, changed - selected)
    gaps = structural_gaps(projected)
    resolved_models = {"base": base, "local": local, "incoming": projected}
    differences, conflicts = compare(base, local, projected)
    _, donor_conflicts = compare(base, projected, omitted)
    for row in donor_conflicts:
        chosen, missing = selected & set(row["subjects"]), (changed - selected) & set(row["subjects"])
        if chosen and missing:
            gaps.append({"reason": "donor_" + row["reason"], "subjects": sorted(chosen), "required_subjects": sorted(missing)})
    # Every gap is explicit and declineable. A choice cannot waive a gap while
    # importing its donor atom. Structural missing subjects remain diagnostic.
    for gap in gaps:
        affected = selected & set(gap["subjects"])
        if not affected:
            affected = {k for k in selected if k.split("/", 1)[0] in {s.split("/", 1)[0] for s in gap["subjects"] + gap["required_subjects"]}}
        gap["selected_subjects"] = sorted(affected)
        if affected: conflicts.append(_conflict("selection_gap:" + gap["reason"], affected, resolved_models))
    conflicts = {row["conflict_id"]: row for row in conflicts}
    return projected, differences, [conflicts[k] for k in sorted(conflicts)], sorted(gaps, key=canonical_json)


def resolve_selection(analysis, choices):
    selected = set(analysis["request"]["selected_subjects"])
    projected = {**analysis, "normalized": {**analysis["normalized"], "incoming": analysis["projected_incoming"]}}
    atoms, canonical_choices, deleted = resolve_atoms(projected, choices)
    local = analysis["normalized"]["local"]["atoms"]
    donor = analysis["normalized"]["incoming"]["atoms"]
    base = analysis["normalized"]["base"]["atoms"]
    changed_unselected = {k for k in atoms.keys() | local.keys() if k not in selected and not _same(_state(atoms, k), _state(local, k))}
    if changed_unselected:
        raise UnresolvedPlainTransplant("resolution would change unselected local subjects", changed_unselected)
    dispositions = []
    for key in sorted(selected):
        state, l, r, b = (_state(a, key) for a in (atoms, local, donor, base))
        if key.split("/", 1)[0] in deleted: kind = "deleted_by_choice"
        elif _same(state, l): kind = "already_local" if _same(l, r) else "kept_local"
        elif _same(state, r): kind = "imported"
        elif _same(state, b): kind = "selected_base"
        else: raise UnresolvedPlainTransplant("selection is outside the exact caller choices", [key])
        dispositions.append({"subject": key, "disposition": kind, "result": state})
    effective = {row["subject"] for row in dispositions if row["disposition"] == "imported"}
    for gap in analysis["selection_gaps"]:
        if effective & set(gap["selected_subjects"]):
            raise UnresolvedPlainTransplant("selected import requires an explicit revised selection (" + gap["reason"] + ")",
                gap["selected_subjects"] + gap["required_subjects"])
    # Use exact B/L/P dependency evidence for the actual chosen atom states.
    dependencies = {}
    for key in atoms:
        dependencies[key] = sorted({ref for model in projected["normalized"].values()
            if _same(_state(model["atoms"], key), _state(atoms, key)) for ref in model["dependencies"].get(key, [])})
    gaps = structural_gaps({"atoms": atoms, "dependencies": dependencies})
    if gaps:
        raise UnresolvedPlainTransplant("resolved structure has missing dependency/order subjects",
            [key for gap in gaps for key in gap["subjects"] + gap["required_subjects"]])
    return atoms, canonical_choices, deleted, dispositions
