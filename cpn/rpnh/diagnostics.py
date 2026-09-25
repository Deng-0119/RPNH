"""Open registered HOST analyzers over detached, recursively read-only JSON."""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .control_client import json_data


def freeze_snapshot(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot must be a JSON object")

    def freeze(value):
        if isinstance(value, dict):
            return MappingProxyType({key: freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        return value

    return freeze(json_data(snapshot))


def diagnose(snapshot: Mapping[str, Any], registration, *,
             analyzer_keys: Iterable[str] | None = None) -> dict[str, Any]:
    """Selected keys override declaration keys; [] disables all analyzers.

    Analyzer ABI: callable(frozen_snapshot) -> {warnings: [text], reason?: text}.
    Only warnings/reason are accepted as observations. No output can supply a
    veto, score, settlement, terminal status or global liveness proof.
    """
    frozen = freeze_snapshot(snapshot)
    keys = (frozen.get("declaration", {}).get("analyzers", ())
            if analyzer_keys is None else analyzer_keys)
    if isinstance(keys, str):
        raise ValueError("analyzer_keys must be a sequence of registered keys")
    reports = []
    for key in keys:
        if not isinstance(key, str) or not key:
            raise ValueError("analyzer keys must be nonempty strings")
        report = {"key": key, "status": "UNKNOWN", "warnings": [], "reason": ""}
        try:
            analyzer = registration.resolve("analyzer", key)
            result = analyzer(frozen)
            if not isinstance(result, Mapping):
                raise ValueError("analyzer result must be an object")
            warnings = result.get("warnings", ())
            if not isinstance(warnings, (list, tuple)) or any(not isinstance(w, str) for w in warnings):
                raise ValueError("analyzer warnings must be text array")
            reason = result.get("reason", "No global liveness proof supplied.")
            if not isinstance(reason, str):
                raise ValueError("analyzer reason must be text")
            report.update(status="WARNING" if warnings else "UNKNOWN",
                          warnings=list(warnings), reason=reason)
        except Exception as exc:
            report["reason"] = f"Analyzer unavailable or failed: {type(exc).__name__}: {exc}"
        reports.append(report)
    return {"status": "WARNING" if any(r["warnings"] for r in reports) else "UNKNOWN",
            "global_liveness": "UNKNOWN", "analyzers": reports}
