"""Project missing acceptance references, never grant release qualification.

The case IDs refer to the frozen 2026-11-13 v1.1 acceptance specification.
Inputs have already passed install_contract's structural/evidence readers and
the caller's exact selection match. Presence of a case ID is still only a
maintainer's metadata reference, not proof of execution or semantic coverage.
"""
from __future__ import annotations


CASE_TITLES = {
    "T01": "installation outside the source checkout", "T01a": "fixed npm root materials",
    "T02": "actual startup for each selected frontend/profile", "T02a": "required scripts and component variants",
    "T03": "same-release component/profile/binding reconfiguration", "T04": "complete four-frontend handoff cycle",
    "T05": "real offline task and committed checkpoint", "T06": "upgrade preserves the complete selected set",
    "T07": "retained configuration after a newer profile", "T07a": "isolated writable frontend state",
    "T08": "lifecycle, worker, viewer and cross-root lock competition", "T09": "bounded candidate context",
    "T10": "all activation interruption boundaries", "T11": "idempotent rollback and first bootstrap",
    "T12": "hard termination and unfinished build descendants", "T13": "changed dependencies and damaged outgoing rollback",
    "T13a": "runtime identity changes at an unchanged path", "T14": "wrapper and PATH binding",
    "T15": "cross-root and unknown active worker admission", "T15a": "actual persisted-format admission",
    "T15b": "historical roots without invented writer-version fields", "T16": "bidirectional continued business writes",
    "T17": "paused tasks retain exact old environments", "T17a": "new host with the registered old tool environment",
    "T17b": "non-current entry and missing old environment", "T18": "actual target OS interface boundaries",
    "T18a": "actual execution architecture and emulation", "T19": "offline use and damaged update cache",
    "T20": "frozen package contents and source identity", "T20a": "public/private install entry correspondence",
    "T21": "unsupported tool capabilities rejected", "T22": "display handoff versus installation changes",
}
_COMMON_INSTALL = frozenset({
    "T01", "T02", "T05", "T08", "T09", "T10", "T11", "T12", "T13a", "T15", "T15a", "T15b",
    "T17", "T17a", "T17b", "T18", "T18a", "T19", "T20", "T20a", "T21",
})
_UI_INSTALL = frozenset({"T02a", "T07a", "T14", "T22"})
_UPGRADE = frozenset({"T06", "T08", "T10", "T15", "T15a", "T15b", "T16", "T17a", "T17b"})
_ROLLBACK = frozenset({"T08", "T10", "T11", "T13", "T13a", "T15", "T15a", "T15b", "T16", "T17a", "T17b"})


def _coverage(required, available):
    available = set(available)
    return {"required_case_ids": sorted(required), "referenced_case_ids": sorted(set(required) & available),
            "missing_case_ids": sorted(set(required) - available),
            "unmapped_scope_labels": sorted(available - CASE_TITLES.keys())}


def project_qualification_gaps(support, validation, assessment, *, components,
                               matching_combinations, current_version=None, root_extras=None,
                               material_selection=None, entry_reconfiguration=False) -> dict:
    """Explain reference gaps for this S and exact predecessor, without writes.

    A bootstrap has no predecessor requirement. A version-pair declaration's
    free-form capability strings are displayed but cannot authorize persistence
    semantics; no universal capability name is invented or recognized here.
    """
    if root_extras is not None and (not isinstance(root_extras, (list, tuple))
                                   or not all(isinstance(item, str) for item in root_extras)):
        raise TypeError("root extras must be a known selection or None")
    selected = set(components)
    ui = bool(selected - {"basic"})
    npm = any(item["artifact"]["kind"] == "npm" for item in components.values())
    complete_cycle = selected == {"basic", "codex", "opencode", "dsh-web"}
    target_version = support["source"]["rpnh_version"]
    same_release = current_version is not None and current_version == target_version
    pair_required = current_version is not None and not same_release
    installation = set(_COMMON_INSTALL)
    if ui:
        installation.update(_UI_INSTALL)
    if npm:
        installation.add("T01a")
    if complete_cycle:
        installation.add("T04")
    cases = {row["id"]: row for row in assessment["evidence_backed_combinations"]}
    from .install_contract import UPDATE_SUPPORT_V3_SCHEMA, UPDATE_SUPPORT_V4_SCHEMA, validate_material_selection
    bound_materials = support["schema_version"] in {UPDATE_SUPPORT_V3_SCHEMA, UPDATE_SUPPORT_V4_SCHEMA}
    bound_entries = support["schema_version"] == UPDATE_SUPPORT_V4_SCHEMA
    material_selection = (None if material_selection is None else
                          validate_material_selection(material_selection, source=support["source"]))
    results = {row["id"]: row for row in validation["results"]}
    matched = {row["id"] for row in matching_combinations}
    records = []

    def direction(kind, identity, required):
        if not pair_required:
            return {"status": "not_applicable", "from_version": None, "to_version": None,
                    "evidence": [], "declared_capabilities": [], **_coverage(set(), set())}
        outgoing, incoming = ((current_version, target_version) if kind == "upgrade_from"
                              else (target_version, current_version))
        pairs = [row for row in assessment[kind]
                 if row["combination"] == identity and row["version"] == current_version]
        # assess_update_support already bound these references to this exact
        # native/pass combination, operation and release direction.
        references = sorted({key for row in pairs for key in row["evidence"]})
        scopes = {item for key in references for item in results[key]["scope"]}
        return {"status": "matched_pair_metadata" if pairs else "missing_pair_metadata",
                "from_version": outgoing, "to_version": incoming, "evidence": references,
                "declared_capabilities": sorted({item for row in pairs for item in row["capabilities"]}),
                **_coverage(required, scopes)}

    for identity in sorted(matched & cases.keys()):
        available = cases[identity]["scope"]
        upgrade = direction("upgrade_from", identity, _UPGRADE)
        rollback = direction("rollback_to", identity, _ROLLBACK)
        rollback_scopes = {item for key in rollback["evidence"] for item in results[key]["scope"]}
        ui_rollback_required = ui and current_version is not None
        ui_rollback_scopes = available if same_release else rollback_scopes
        records.append({"combination": identity, "installation": _coverage(installation, available),
            "same_release_reconfiguration": {"applicable": same_release,
                **_coverage({"T03"} if same_release else set(), available if same_release else set())},
            "upgrade": upgrade, "rollback": rollback,
            "ui_profile_rollback": {"applicable": ui_rollback_required,
                "scope_source": "same_release_installation" if same_release else "exact_rollback_pair" if pair_required else "not_applicable",
                **_coverage({"T07"} if ui_rollback_required else set(), ui_rollback_scopes if ui_rollback_required else set())}})
        if bound_materials:
            selected_materials = cases[identity]["materials"]
            records[-1]["materials"] = selected_materials
            records[-1]["material_selection_matches"] = (None if material_selection is None else
                                                         selected_materials == material_selection)
            records[-1]["root_extras_match"] = (None if root_extras is None else
                                                sorted(root_extras) == selected_materials["root_extras"])
        if bound_entries:
            entries = {}
            for entry in sorted(selected):
                required = {"T02"}
                if entry != "basic":
                    required.update(_UI_INSTALL)
                    if same_release:
                        required.add("T07")
                if cases[identity]["materials"]["frontends"].get(entry, {}).get("kind") == "node_tree":
                    required.add("T01a")
                entries[entry] = _coverage(required, cases[identity]["entry_scope"][entry])
            records[-1]["entry_installation"] = entries
            if entry_reconfiguration and same_release:
                records[-1]["entry_reconfiguration"] = {
                    entry: _coverage({"T03"} | ({"T07"} if entry != "basic" else set()),
                                     cases[identity]["entry_scope"][entry])
                    for entry in sorted(selected)}
    extras_state = ("selection_unknown" if root_extras is None else
                    "missing_schema_dimension" if root_extras else "no_optional_selection")
    dependency_state = "not_bound_by_current_sidecars"
    dependency_detail = "The tested fixed dependency closure is not identified by R/platform/runtime/S or case labels, even for Basic with empty root extras."
    if bound_materials:
        extras_state = ("selection_unknown" if root_extras is None else
                        "matches_exact_backed_selection" if any(row["root_extras_match"] for row in records)
                        else "no_exact_backed_selection")
        dependency_state = ("declared_exact_sets" if material_selection is None else
                            "exact_selection_matches_backed_metadata" if any(
                                row["material_selection_matches"] for row in records) else "selection_mismatch")
        dependency_detail = ("The v4 declarations also bind every selected frontend recipe, entry, artifact and release asset; "
                             "actual acquisition, dependency closure and installed probes remain separate checks."
                             if bound_entries else
                             "The v3 declarations bind exact wheel filenames, versions, asset identities and root extras; "
                             "frontend assets, actual acquisition, dependency closure and installed probes remain separate checks.")
    return {
        "schema_version": "rpnh/release_case_coverage/v1",
        "specification": {"document": "03_Acceptance_Specification.json", "version": "1.1", "date": "2026-11-13"},
        "selected_entries": sorted(selected),
        "context": "bootstrap" if current_version is None else "same_release_reconfiguration" if same_release else "release_pair",
        "applicability": {"common_installation": sorted(_COMMON_INSTALL),
                          "selected_ui": sorted(_UI_INSTALL) if ui else [],
                          "npm_materials": ["T01a"] if npm else [],
                          "full_four_frontend_cycle": ["T04"] if complete_cycle else []},
        "case_titles": dict(CASE_TITLES), "combinations": records,
        "selection_status": "matched_metadata" if records else "no_exact_backed_combination",
        "unmatched_installation_requirements": [] if records else sorted(installation),
        "root_extras": {"selected": None if root_extras is None else list(root_extras), "status": extras_state},
        "dependency_materials": {
            "status": dependency_state, "qualification_granted": False,
            "detail": dependency_detail,
        },
        "frontend_materials": {
            "status": "exact_entries_declared" if bound_entries else "not_applicable" if not ui else "missing_schema_dimension",
            "entry_scope_bound": bound_entries, "qualification_granted": False,
        },
        "persistent_capabilities": {
            "status": "semantic_evidence_review_required" if pair_required else "no_release_pair_requested",
            "vocabulary": "not_defined_by_current_sidecar_contract",
            "mechanically_authorized": False,
            "review_requirements": [
                "Actual DB/meta/object schemas and module/route/plugin contracts must be identified.",
                "Exact selected environment requirements and active worker protocols must be understood.",
                "T15a/T15b and T16 require scoped read/write and continued-checkpoint evidence in each direction.",
                "no-migration, readonly or all-formats strings do not authorize business writes.",
            ]},
        "limitations": [
            "Case references are metadata, not verified execution or reviewed evidence logs.",
            ("v4 entry_scope binds case references to each exact entry/profile/artifact; it does not verify the referenced execution."
             if bound_entries else "T02 must cover each selected entry/profile; this older schema cannot mechanically prove that granularity."),
            "T18/T18a apply to the exact observed target; another OS, architecture or emulation is not inferred.",
            "T03 is a same-release context requirement; T07 is a separate UI rollback requirement.",
            "A bootstrap requires no invented predecessor or public upgrade/rollback pair.",
            "Even every case reference leaves payload provenance, native acceptance and persistent semantics ungranted.",
        ],
        "qualification_granted": False, "installable": False,
    }


__all__ = ("project_qualification_gaps",)
