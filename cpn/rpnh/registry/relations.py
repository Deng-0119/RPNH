"""Typed forward/reverse relation queries."""

from __future__ import annotations

import json
from typing import Any

from .event_store import EventStore


class RelationStore:
    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    def forward(self, reference: str, *, relation_type: str | None = None) -> list[dict[str, Any]]:
        return self._query("source_json", reference, relation_type)

    def reverse(self, reference: str, *, relation_type: str | None = None) -> list[dict[str, Any]]:
        return self._query("target_json", reference, relation_type)

    def _query(self, column: str, reference: str,
               relation_type: str | None) -> list[dict[str, Any]]:
        if column not in {"source_json", "target_json"}:
            raise ValueError("invalid relation direction")
        output: list[dict[str, Any]] = []
        for row in self.event_store.canonical_relation_rows():
            endpoint = json.loads(row[column])
            if reference not in {endpoint.get("entity_id"), endpoint.get("version_id")}:
                continue
            if relation_type is not None and row["relation_type"] != relation_type:
                continue
            output.append({
                "relation_id": row["relation_id"], "relation_type": row["relation_type"],
                "source": json.loads(row["source_json"]),
                "target": json.loads(row["target_json"]), "strength": row["strength"],
                "metadata": json.loads(row["metadata_json"]),
            })
        return output
