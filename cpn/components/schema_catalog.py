"""Explicit optional default-agent component schemas; no executor imports."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from cpn.rpnh.registry.schema_catalog import TypeDefinition


COMPONENT_OBJECT_TYPES = (
    "agent_action/v2",
    "agent_tool_error/v1",
    "agent_context_compaction/v3",
    "agent_loop/v1",
    "agent_tool_readiness/v1",
    "execution_evidence/v1",
    "numerical_tool_profile/v1",
)
COMPONENT_EVENT_TYPES = (
    "agent_action_settled/v1",
    "agent_loop_started/v1",
    "agent_loop_terminal/v1",
)
COMPONENT_CONTENT_SCHEMA_REFS = (
    "registry_v1/agent_document/v2",
    "registry_v1/agent_tool_catalog/v1",
    "registry_v1/generic_critic_prompt/v1",
    "registry_v1/generic_critic_prompt_template/v1",
    "registry_v1/logical_provider_request_recipe/v1",
)
COMPONENT_SCHEMA_REFS = tuple(sorted({
    *(f"registry_v1/{name}" for name in COMPONENT_OBJECT_TYPES),
    *(f"registry_v1/{name}" for name in COMPONENT_EVENT_TYPES),
    *COMPONENT_CONTENT_SCHEMA_REFS,
}))


def schema_data(
    schema_refs: Iterable[str], object_types: Iterable[str],
    event_types: Iterable[str], *, owner: str,
) -> tuple[dict[str, dict[str, Any]], tuple[TypeDefinition, ...], dict[str, Path]]:
    """Load only the caller's explicit inventory as documents and provenance."""
    root = Path(__file__).resolve().parents[1] / "schemas"
    paths = {}
    documents = {}
    for schema_id in schema_refs:
        namespace, _, relative = schema_id.partition("/")
        path = root / namespace / f"{relative.replace('/', '.')}.schema.json"
        paths[schema_id] = path
        documents[schema_id] = json.loads(path.read_text(encoding="utf-8"))
    definitions = tuple(
        TypeDefinition(
            name=name, category=category, owner=owner,
            schema_ref=f"registry_v1/{name}",
            criticality="authoritative" if category == "event" else None,
            permission="writer-only" if category == "event" else "task-scoped",
            retention="permanent" if category == "event" else "run-fact",
            recovery_rule="fail-closed" if category == "event" else "registry-reference-replay",
            integrity_rule="exact-schema-and-reference-validation",
        )
        for category, names in (("object", object_types), ("event", event_types))
        for name in names
    )
    return documents, definitions, paths


def component_schema_data():
    """Optional data for HOST composition, not automatic Core defaults."""
    return schema_data(
        COMPONENT_SCHEMA_REFS, COMPONENT_OBJECT_TYPES, COMPONENT_EVENT_TYPES,
        owner="components",
    )
