"""Tool-execution facts and proved communications for original regrading.

A Registry-confirmed tool return is an execution observation, not proof of a
later model input. External backend-only or outcome-unknown values must never
be promoted to returned results. Communications still require actual delivery
proof; source-scope metadata keeps these boundaries distinct.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .jsonio import dumps, loads, write_new


@dataclass(frozen=True)
class Observation:
    surface: str
    agent_id: str
    agent_role: str
    timestamp: str
    data: dict
    raw_event: dict

    def validate(self, roles: set[str], tools: set[str]) -> None:
        from datetime import datetime
        if self.surface not in {"tool_call", "communication"}:
            raise ValueError("unknown observation surface")
        if self.agent_role not in roles or not self.agent_id:
            raise ValueError("observation must have a host-bound role")
        if datetime.fromisoformat(self.timestamp.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("observation must carry a timezone-aware source timestamp")
        if not isinstance(self.raw_event, dict): raise ValueError("raw evidence must be an object")
        if self.surface == "tool_call":
            if set(self.data) != {"tool_name", "tool_args", "tool_result"}:
                raise ValueError("tool observation requires name, args and observed result")
            # Unknown requested tools are retained as rejected requests, not erased.
            if self.data["tool_name"] not in tools and self.raw_event.get("rpnh_phase") != "rejected":
                raise ValueError("unknown tool needs explicit rejected phase")
            if not isinstance(self.data["tool_args"], dict) or not isinstance(self.data["tool_result"], str):
                raise ValueError("invalid tool observation data")
        else:
            if set(self.data) != {"target_agent", "target_role", "content"}:
                raise ValueError("communication requires exact recipient and visible text")
            if self.data["target_role"] not in roles | {"user"}:
                raise ValueError("unknown recipient")
            if not all(isinstance(v, str) for v in self.data.values()):
                raise ValueError("invalid communication data")
            if "handoff_context_raw" not in self.raw_event:
                raise ValueError("communication must declare actual delivered structured context, even if null")
        dumps({"data": self.data, "raw_event": self.raw_event})


class ObservationCollector:
    def __init__(self, *, roles: set[str], tools: set[str]):
        self.roles, self.tools = set(roles), set(tools)
        self._observations: list[Observation] = []

    def emit(self, observation: Observation):
        observation.validate(self.roles, self.tools)
        # Copy so caller mutation cannot change earlier evidence.
        self._observations.append(Observation(
            observation.surface, observation.agent_id, observation.agent_role, observation.timestamp,
            loads(dumps(observation.data)), loads(dumps(observation.raw_event))))

    def export_to(self, sink):
        for item in self._observations:
            kwargs = {"agent_id": item.agent_id, "agent_role": item.agent_role,
                      "timestamp": item.timestamp, "raw_event": item.raw_event, **item.data}
            if item.surface == "tool_call": sink.emit_tool_call(**kwargs)
            else: sink.emit_communication(**kwargs)
        sink.finalize()

    def save(self, path: Path):
        write_new(path, [{"surface": i.surface, "agent_id": i.agent_id,
                          "agent_role": i.agent_role, "timestamp": i.timestamp,
                          "data": i.data, "raw_event": i.raw_event} for i in self._observations])

    def __len__(self): return len(self._observations)


def save_normalized_actions(path: Path, sink) -> None:
    """Persist raw_event too; upstream communication JSONL can omit it."""
    records = []
    for action in sink.actions:
        if callable(getattr(action, "model_dump", None)):
            records.append(action.model_dump(mode="json"))
        else:
            raise TypeError("expected upstream ObservableAction, not a fabricated dict")
    write_new(path, records)
