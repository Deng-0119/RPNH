"""Explicit, task-independent API visibility comparison; baseline is untouched.

No task prompt, world, rubric, search corpus, or endpoint implementation is read
by the overlay or preflight validator. This is not a historical-result repair.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .constants import UPSTREAM_COMMIT
from .io import file_sha, sha

BASELINE = "baseline"
CONDITION = "api-contract-visibility-v1"
READBACK_CONDITION = "api-contract-visibility-readback-v2"
CHOICES = (BASELINE, CONDITION, READBACK_CONDITION)

# Only public interface facts verified against the pinned schema and mutators.
# These strings also define the independently hashed overlay payload.
OVERLAY = {
    "salesforce.sobjects.account.update": {
        "method": "PATCH",
        "request_suffix": (
            " Also supported: HealthStatus (health_status), Tier (tier), Priority (priority). "
            "Each underlying field is Optional[str], with no enforced enum. "
            "The pinned updater ignores null and empty-string values; they do not clear a field."
        ),
    },
    "sheets.spreadsheets.get": {
        "method": "GET",
        "description": (
            "Retrieve spreadsheet metadata and the list of sheets. The pinned implementation "
            "returns no grid cell data, even when includeGridData is true. To read cells, "
            "choose each needed sheet explicitly and call sheets.spreadsheets.values.get: "
            "GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range} "
            "with a sheet-qualified range such as 'Sheet Title'!A1:Z100. "
            "A bare A1 range addresses only the first sheet. No automatic all-sheet read is performed."
        ),
        "parameters": {
            "includeGridData": "Accepted but ignored by this pinned implementation; no cell data is returned.",
            "ranges": "Accepted but ignored by this pinned implementation; does not filter sheets or retrieve cells.",
        },
        "response": (
            "Spreadsheet metadata only: {spreadsheetId, properties: {title}, "
            "sheets: [{properties: {sheetId, title, index, sheetType}}], spreadsheetUrl}"
        ),
    },
    "sheets.spreadsheets.values.get": {
        "method": "GET",
        "description_suffix": (
            " Select each needed sheet explicitly in the URL range, e.g. 'Sheet Title'!A1:Z100. "
            "A bare A1 range reads only the first sheet; this call does not read all sheets."
        ),
    },
}


def selected(upstream) -> dict | None:
    """Absent means baseline, including old fixtures and retained work roots."""
    return copy.deepcopy(getattr(upstream, "configuration_condition", None))


def configure(upstream, name: str = BASELINE) -> dict | None:
    if name not in CHOICES:
        raise ValueError("unknown AutomationBench configuration condition")
    if name == BASELINE:
        upstream.configuration_condition = None
        return None
    if upstream.identity.get("commit") != UPSTREAM_COMMIT or upstream.identity.get("tracked_changes"):
        raise ValueError("comparison condition requires the unchanged pinned upstream")
    root = Path(upstream.root)
    catalog = {p.name: file_sha(p) for p in sorted(
        (root / "automationbench/tools/api/schemas").glob("*.jsonc"))}
    if not {"salesforce.jsonc", "google_sheets.jsonc"}.issubset(catalog):
        raise ValueError("comparison condition requires the pinned endpoint catalog")
    implementation = {name: file_sha(Path(__file__).parent / name) for name in (
        "configuration_condition.py", "broker.py", "native.py", "plugin.py",
        "drivers/native.py", "evidence.py")}
    payload = {
        "schema": "rpnh-ab/configuration-condition/v1", "id": name,
        "upstream_commit": UPSTREAM_COMMIT,
        "catalog_sha256": sha(catalog), "overlay_sha256": sha(OVERLAY),
        "implementation_sha256": sha(implementation),
        "search_ranking": "upstream-unchanged-post-search-overlay-only",
        "parameter_feedback": "sanitized-pre-dispatch-only-v1",
        "task_prompt_world_rubric": "unchanged",
    }
    payload["sha256"] = sha(payload)
    upstream.configuration_condition = payload
    return copy.deepcopy(payload)


def freeze_plan(plan: dict, condition: dict | None) -> dict:
    if condition is None:
        return plan
    result = copy.deepcopy(plan)
    result["condition_id"] += "+" + condition["id"]
    result["configuration_condition"] = copy.deepcopy(condition)
    return result


def restore_frozen(upstream, conditions: dict) -> dict | None:
    frozen = conditions.get("configuration_condition")
    benchmark = conditions.get("benchmark_spec", {})
    if benchmark.get("configuration_condition") != frozen:
        raise ValueError("benchmark configuration condition differs from frozen conditions")
    if frozen is None:
        if selected(upstream) is not None:
            raise ValueError("configured API comparison differs from frozen baseline")
        return None
    if not isinstance(frozen, dict):
        raise ValueError("invalid frozen configuration condition")
    if conditions.get("execution_spec", {}).get("executor_host") != "native":
        raise ValueError("API comparison condition currently supports only the native host")
    actual = configure(upstream, frozen.get("id"))
    if actual != frozen:
        raise ValueError("configuration condition/catalog/overlay identity changed")
    return actual


def overlay_search(raw: str) -> tuple[str, list[str]]:
    """Edit only recognized returned endpoints; no search/ranking or extra fetch."""
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw, []
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        return raw, []
    changed = []
    for endpoint in result["results"]:
        if not isinstance(endpoint, dict):
            continue
        rule = OVERLAY.get(endpoint.get("id"))
        if rule is None or endpoint.get("method") != rule["method"]:
            continue
        original = copy.deepcopy(endpoint)
        for field in ("description", "response"):
            if field in rule:
                endpoint[field] = rule[field]
        for field in ("request", "description"):
            if field + "_suffix" in rule and isinstance(endpoint.get(field), str):
                endpoint[field] += rule[field + "_suffix"]
        params = endpoint.get("parameters")
        for name, description in rule.get("parameters", {}).items():
            if isinstance(params, dict) and isinstance(params.get(name), dict):
                params[name]["description"] = description
        if endpoint != original:
            changed.append(endpoint["id"])
    return (json.dumps(result, ensure_ascii=False), changed) if changed else (raw, [])


def _error(field: str, code: str, message: str) -> dict:
    # Never echo submitted data, URL, exception text, world state or credentials.
    return {"error": {"code": code, "field": field, "message": message,
                      "stage": "pre_dispatch", "upstream_dispatched": False,
                      "effect_status": "not_started"}}


def pre_dispatch_error(tool: str, arguments: dict) -> dict | None:
    """A small preflight, not a catch/reclassification of router exceptions.

    Avoid endpoint-wide body schema validation: some endpoints accept scalar,
    array or text bodies. Unknown URLs and semantic errors stay upstream-owned.
    """
    if tool != "api_fetch":
        return None
    if any(key not in {"method", "url", "params", "body"} for key in arguments):
        return _error("arguments", "unexpected_argument", "Use only method, url, params and body; world is host-owned.")
    for field in ("method", "url"):
        if not isinstance(arguments.get(field), str) or not arguments[field].strip():
            return _error(field, "required_string", "Supply a non-empty string using the endpoint metadata from api_search.")
    try:
        parsed = urlsplit(arguments["url"])
    except ValueError:
        return _error("url", "invalid_url", "Use a valid endpoint URL from api_search.")
    # Preserve established normalization (including the Trello scalar mapping).
    from .upstream import normalize_api_fetch_arguments
    normalized, _, _ = normalize_api_fetch_arguments(arguments)
    for field in ("params", "body"):
        value = normalized.get(field)
        if value is None or value == {} or value == "":
            continue
        if isinstance(value, dict):
            decoded = value
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                if field == "body":
                    # QuickBooks text-query compatibility is upstream-owned;
                    # do not classify any malformed body as an exception here.
                    continue
                return _error(field, "invalid_json", "Encode query parameters as a JSON object string, or omit params.")
        else:
            return _error(field, "invalid_argument_type", "Supply a JSON string or object, or omit this optional argument.")
        if field == "params" and not isinstance(decoded, dict):
            return _error(field, "object_required", "Encode query parameters as a JSON object string, or omit params.")
        if (field == "body" and arguments["method"].upper() == "PATCH"
                and parsed.hostname and parsed.hostname.endswith(".salesforce.com")
                and re.fullmatch(r"/services/data/v\d+\.\d+/sobjects/Account/[^/]+", parsed.path)
                and not isinstance(decoded, dict)):
            return _error(field, "object_required", "For Account PATCH, encode the fields to update as a JSON object.")
    return None
