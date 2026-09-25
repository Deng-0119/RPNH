"""Optional snapshot observations; no kernel import, Registry access or proof."""
from __future__ import annotations

from typing import Any, Mapping

MARKING_ANALYZER = "local/marking/v1"
RESOURCE_WAIT_ANALYZER = "local/resource_wait/v1"


def marking_warnings(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    warnings = []
    if "enabled_transitions" not in snapshot or "active_firings" not in snapshot:
        reason = "Enabled-transition or active-firing data unavailable."
    elif not snapshot["enabled_transitions"] and not snapshot["active_firings"]:
        warnings.append("No enabled transition or active firing in this snapshot; an interactive marking may await owner input.")
        reason = "This observation is not a deadlock or terminal decision."
    else:
        reason = "A single marking cannot establish global liveness."
    if snapshot.get("admission_paused"):
        warnings.append("Owner reports new firing admission paused.")
    return {"warnings": warnings, "reason": reason}


def resource_wait_warnings(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    facts = snapshot.get("resource_wait")
    if isinstance(facts, Mapping) and facts.get("status") == "AVAILABLE":
        from cpn.rpnh.registry.resource_wait_snapshot import resource_wait_cycle
        cycle = resource_wait_cycle(facts["live_documents"])
        warnings = []
        if cycle:
            warnings.append("Current resource wait cycle observed: " + "; ".join(
                f'{edge["waiting_firing_ref"]["version_id"]} waits for '
                f'{edge["holder_firing_ref"]["version_id"]} on {edge["resource_ref"]["version_id"]}'
                for edge in cycle))
        return {"warnings": warnings,
            "reason": "Exact current lifecycle graph observation only; absence of a cycle is not a safety proof, and global Petri-net liveness remains UNKNOWN."}
    # Token references alone never establish live waits or a cycle.
    warnings = []
    if any(token.get("resource_ref") is not None for token in snapshot.get("marking", ())):
        warnings.append("Marking contains resource references; these do not establish live waits, grants or a resource cycle.")
    return {"warnings": warnings,
            "reason": "Exact live resource lifecycle/request/queue/grant/lease graph is not supplied by this snapshot; resource-cycle diagnosis UNKNOWN."}


def register_diagnostics(registration) -> None:
    """Explicit HOST opt-in, not an automatic core dependency."""
    for key, analyzer in ((MARKING_ANALYZER, marking_warnings),
                          (RESOURCE_WAIT_ANALYZER, resource_wait_warnings)):
        registration.register_analyzer(key, analyzer,
            identity={"name": key, "revision": "v1"},
            contracts={"input": "read_only_snapshot", "output": "warnings_unknown"})
