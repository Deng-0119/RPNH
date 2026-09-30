from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _property_names(value: object) -> set[str]:
    if isinstance(value, dict):
        names = set(value.get("properties", {}))
        for child in value.values():
            names.update(_property_names(child))
        return names
    if isinstance(value, list):
        result: set[str] = set()
        for child in value:
            result.update(_property_names(child))
        return result
    return set()


def _named_in_code_span(document: str, name: str) -> bool:
    return re.search(
        rf"`[^`]*(?<![A-Za-z0-9_-]){re.escape(name)}"
        rf"(?![A-Za-z0-9_-])[^`]*`",
        document,
    ) is not None


def test_configuration_reference_names_every_catalog_field_in_both_languages(
) -> None:
    schema = json.loads((
        ROOT / "cpn/schemas/runtime/provider_model_catalog.v3.schema.json"
    ).read_text(encoding="utf-8"))
    public_fields = _property_names(schema)
    public_fields.update({
        "entry_point", "version", "config", "environment",
        "max_result_bytes", "max_rework_cycles",
    })

    for relative in (
            "docs/guides/configuration.md",
            "docs/guides/configuration_ZH.md"):
        document = (ROOT / relative).read_text(encoding="utf-8")
        missing = sorted(
            field for field in public_fields
            if not _named_in_code_span(document, field))
        assert missing == [], f"{relative} omits public fields: {missing}"


def test_configuration_reference_names_all_operator_selectors() -> None:
    required = {
        "RPNH_PROVIDER_CATALOG", "RPNH_PROFILE_DIR", "RPNH_CONFIG",
        "RPNH_EXECUTION_CONFIG", "RPNH_PLUGIN_CONFIG", "RPNH_CODEX_BIN",
        "--catalog", "--output-root", "--execution", "--save-default",
        "--effort",
        "--session-dir", "--resume", "--prompt", "--frontend",
        "--show-resources", "--resources-only", "--node", "--output",
        "--host", "--port", "--max-checkpoints", "--max-firings",
    }
    for relative in (
            "docs/guides/configuration.md",
            "docs/guides/configuration_ZH.md"):
        document = (ROOT / relative).read_text(encoding="utf-8")
        missing = sorted(
            item for item in required
            if not _named_in_code_span(document, item))
        assert missing == [], f"{relative} omits operator selectors: {missing}"
