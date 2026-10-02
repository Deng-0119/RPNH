"""Native driver contracts for the Office benchmark example.

The bundled LocalNativeDriver uses RPNH TaskControl, AgentLoop and Registry.
Contract doubles and scripted probes are never live benchmark results.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from .task_view import PublicTask
from .projection import ObservationCollector


@dataclass(frozen=True)
class LiveLimits:
    max_model_calls: int | None
    max_tool_calls: int | None
    max_seconds: int | None

    def __post_init__(self):
        for value in (self.max_model_calls, self.max_tool_calls, self.max_seconds):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError("run limits must be null or positive integers")


def live_limits_from_config(config: dict) -> LiveLimits:
    value = config.get("limits_per_run")
    if not isinstance(value, dict):
        raise ValueError("limits_per_run must be an object")
    return LiveLimits(
        max_model_calls=value.get("max_model_calls"),
        max_tool_calls=value.get("max_tool_calls"),
        max_seconds=value.get("max_seconds"),
    )


@dataclass(frozen=True)
class DriverRequest:
    task: PublicTask
    initial_input: str
    run_dir: Path
    execution_profile: Path
    plugin_configuration: dict
    limits: LiveLimits


@dataclass(frozen=True)
class DriverResult:
    execution_mode: str
    final_output: str
    terminal_evidence_ref: dict | None
    stop_reason: str
    actual_model_calls: int
    registry_records: list[dict]
    roles_observed: tuple[str, ...]
    context_capture_complete: bool
    model_identity: dict

    def __post_init__(self):
        if self.execution_mode not in {"native_live", "native_scripted", "contract_double"}:
            raise ValueError("explicit execution mode required")
        if type(self.actual_model_calls) is not int or self.actual_model_calls < 0:
            raise ValueError("physical model-call count must be a nonnegative integer")
        if type(self.context_capture_complete) is not bool:
            raise ValueError("context completeness must be explicit")


class NativeDriver(Protocol):
    """Must use native owner/admission/settlement and Registry-backed model calls.

    Failures and denied requests must reach observations. The driver may not see
    TaskConfig.access_rules, bank state, SQL checkpoints or any gold tool path.
    Enforcement of physical-call limits belongs before native submission, not
    in this data contract or as a post-hoc token estimate.
    """
    async def execute(self, request: DriverRequest, observations: ObservationCollector) -> DriverResult: ...
