"""User-owned execution policy carried by one generated model profile."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    """Bounds for the firing-private workspace process and publications."""

    timeout_seconds: int = 120
    memory_bytes: int = 4 * 1024 * 1024 * 1024
    process_limit: int = 64
    source_size_bytes: int = 16 * 1024 * 1024
    input_size_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"runtime.workspace.{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    """Cost, concurrency, compaction, and workspace policy for one profile."""

    max_turns_per_node: int = 12
    max_parallel_nodes: int = 4
    main_history_message_limit: int = 20
    context_pressure_trigger_ratio: float = 0.90
    context_tool_output_byte_limit: int = 10_000
    workspace: WorkspacePolicy = field(default_factory=WorkspacePolicy)

    def __post_init__(self) -> None:
        for name in (
                "max_turns_per_node", "max_parallel_nodes",
                "main_history_message_limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"runtime.{name} must be a positive integer")
        ratio = self.context_pressure_trigger_ratio
        if (isinstance(ratio, bool) or not isinstance(ratio, (int, float))
                or not 0.0 < float(ratio) < 1.0):
            raise ValueError(
                "runtime.context_pressure_trigger_ratio must be between zero and one")
        limit = self.context_tool_output_byte_limit
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 128:
            raise ValueError(
                "runtime.context_tool_output_byte_limit must be at least 128")
        if not isinstance(self.workspace, WorkspacePolicy):
            raise TypeError("runtime.workspace must be a WorkspacePolicy")

    def as_document(self) -> dict[str, object]:
        return {
            "max_turns_per_node": self.max_turns_per_node,
            "max_parallel_nodes": self.max_parallel_nodes,
            "main_history_message_limit": self.main_history_message_limit,
            "context_pressure_trigger_ratio": float(
                self.context_pressure_trigger_ratio),
            "context_tool_output_byte_limit": (
                self.context_tool_output_byte_limit),
            "workspace": asdict(self.workspace),
        }


_RUNTIME_FIELDS = {
    "max_turns_per_node", "max_parallel_nodes",
    "main_history_message_limit", "context_pressure_trigger_ratio",
    "context_tool_output_byte_limit", "workspace",
}
_WORKSPACE_FIELDS = {
    "timeout_seconds", "memory_bytes", "process_limit",
    "source_size_bytes", "input_size_bytes",
}


def runtime_policy_from_document(value: object | None) -> RuntimePolicy:
    """Load the one current runtime-policy shape; omission means defaults."""

    if value is None:
        return RuntimePolicy()
    if not isinstance(value, Mapping) or set(value) != _RUNTIME_FIELDS:
        raise ValueError("runtime policy fields are not current")
    workspace = value.get("workspace")
    if not isinstance(workspace, Mapping) or set(workspace) != _WORKSPACE_FIELDS:
        raise ValueError("runtime.workspace fields are not current")
    return RuntimePolicy(
        max_turns_per_node=value["max_turns_per_node"],
        max_parallel_nodes=value["max_parallel_nodes"],
        main_history_message_limit=value["main_history_message_limit"],
        context_pressure_trigger_ratio=value[
            "context_pressure_trigger_ratio"],
        context_tool_output_byte_limit=value[
            "context_tool_output_byte_limit"],
        workspace=WorkspacePolicy(**workspace),
    )


__all__ = (
    "RuntimePolicy", "WorkspacePolicy", "runtime_policy_from_document",
)
