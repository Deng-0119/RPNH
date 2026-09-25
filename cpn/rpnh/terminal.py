"""Registered terminal product contracts, never terminal authority on their own.

Registry must still prove the exact output binding, settled producing firing and
current checkpoint. Semantic interpretation supplies indexes, not permissions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class TerminalProductContract:
    key: str
    outcome_ports: Mapping[str, str]
    content_schema_id: str
    index_authority: str
    producer_label: str
    index_from_interpretation: bool = False

    def __post_init__(self):
        if (any(not isinstance(value, str) or not value.strip()
                for value in (self.key, self.content_schema_id,
                              self.index_authority, self.producer_label))
                or not self.outcome_ports
                or set(self.outcome_ports) - {"complete", "failed"}
                or any(not isinstance(value, str) or not value
                       for value in self.outcome_ports.values())
                or len(set(self.outcome_ports.values())) != len(self.outcome_ports)
                or type(self.index_from_interpretation) is not bool):
            raise ValueError("invalid registered terminal product contract")

    def to_dict(self):
        return {
            "key": self.key, "outcome_ports": dict(self.outcome_ports),
            "content_schema_id": self.content_schema_id,
            "index_authority": self.index_authority,
            "producer_label": self.producer_label,
            "index_from_interpretation": self.index_from_interpretation,
        }

    def require_product(self, *, binding_key, outcome, port_id, schema_id):
        if (binding_key != self.key or outcome not in self.outcome_ports
                or self.outcome_ports[outcome] != port_id
                or schema_id != self.content_schema_id):
            raise ValueError("terminal product differs from exact registered binding")
