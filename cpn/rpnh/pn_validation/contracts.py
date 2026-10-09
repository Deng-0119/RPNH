"""Immutable finite-analysis contracts; proposed identities grant no authority."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
import json
import math
from typing import Literal

from ..registry.models import VersionRef
from ..registry.resources import (ExecutableNetAuthority, ResourceVersionRef,
                                  TypedMarkingSnapshot)

SCHEMA_VERSION = "rpnh/pn_validation/v1"
SEMANTICS_VERSION = "exact-start-settle/v1"
Verdict = Literal["HOLDS", "VIOLATED", "UNKNOWN", "NOT_APPLICABLE"]


def canonical_data(value):
    """Closed deterministic data, preserving bool/string and rejecting bad keys."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is bytes:
        return {"$bytes_hex": value.hex()}
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite analysis value")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: canonical_data(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("analysis mapping keys must be strings")
        return {key: canonical_data(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical_data(item) for item in value]
    raise TypeError(f"unsupported analysis data: {type(value).__name__}")


def canonical_json(value) -> str:
    return json.dumps(canonical_data(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, slots=True)
class ProducedSpec:
    place: str
    verdict: bool | str | None = None
    resource_ref: ResourceVersionRef | None = None
    work_resource_ref: ResourceVersionRef | None = None
    kind: str | None = None
    output_port_id: str | None = None
    output_place_ref: VersionRef | None = None
    output_binding_ref: VersionRef | None = None


@dataclass(frozen=True, slots=True)
class OperationCase:
    case_id: str
    outcome_id: str
    produced: tuple[ProducedSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationModel:
    operation_name: str
    revision: str
    cases: tuple[OperationCase, ...]
    fidelity_assumption: str = "HOST implementation obeys this finite model"


@dataclass(frozen=True, slots=True)
class TerminalContract:
    revision: str = "v1"
    success_places: tuple[str, ...] = ()
    permitted_failure_places: tuple[str, ...] = ()
    unfinished_places: tuple[str, ...] = ()
    released_lease_places: tuple[str, ...] = ()
    persistent_places: tuple[str, ...] = ()
    stop_on_terminal: bool = False


@dataclass(frozen=True, slots=True)
class EnvironmentContract:
    revision: str = "v1"
    closed: bool = True
    external_wait: bool = False


@dataclass(frozen=True, slots=True)
class SchedulerContract:
    revision: str = "v1"
    selection: str = "any-exact-binding"
    fairness: str = "none"


@dataclass(frozen=True, slots=True)
class AnalysisPolicy:
    max_states: int = 1000
    max_edges: int = 5000
    max_depth: int = 100
    max_seconds: float = 10.0
    max_bindings: int = 1000
    properties: tuple[str, ...] = ("safety", "enabledness", "terminal_classification", "proper_completion", "possible_successful_completion", "allowed_completion_from_every_state", "dead_transition", "inevitable_completion_without_fairness", "local_progress")
    mode: str = "advisory"
    required_properties: tuple[str, ...] = ("safety",)


@dataclass(frozen=True, slots=True)
class ActiveOccurrence:
    firing_ref: VersionRef
    claim_id: int
    transition_id: str
    claim_epoch: int
    attempt_index: int
    consumed_refs: tuple[VersionRef, ...]
    reference_refs: tuple[VersionRef, ...] = ()
    lease_accesses: tuple[tuple[VersionRef, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisState:
    base: TypedMarkingSnapshot
    active: tuple[ActiveOccurrence, ...] = ()
    next_claim_id: int = 1

    @property
    def key(self) -> str:
        return canonical_json(self)


def state_key(state: AnalysisState) -> str:
    return state.key


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    # Canonical declarations prevent caller-owned mutable mappings changing proof inputs.
    compiled_json: str
    net_ref: VersionRef
    resource_plan: object
    state: AnalysisState
    operation_models: tuple[OperationModel, ...]
    terminal_contract: TerminalContract
    environment_contract: EnvironmentContract
    scheduler_contract: SchedulerContract
    policy: AnalysisPolicy
    source_context_json: str = "{}"
    executable: ExecutableNetAuthority | None = None
    ordinary_token_ref_scheme: str | None = None
    schema_version: str = SCHEMA_VERSION
    semantics_version: str = SEMANTICS_VERSION

    @property
    def compiled(self):
        from ..executable_net import load_compiled_net
        return load_compiled_net(self.compiled_json)

    @property
    def initial_state(self):
        return self.state


@dataclass(frozen=True, slots=True)
class SupportResult:
    supported: bool
    reasons: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    supported_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionBinding:
    kind: Literal["START", "SETTLE"]
    transition_id: str
    token_refs: tuple[VersionRef, ...] = ()
    firing_ref: VersionRef | None = None
    case_id: str | None = None


@dataclass(frozen=True, slots=True)
class BindingEnumeration:
    bindings: tuple[ActionBinding, ...]
    complete: bool = True
    reasons: tuple[str, ...] = ()
    candidates_examined: int = 0


@dataclass(frozen=True, slots=True)
class ActionEvidence:
    binding: ActionBinding
    before_refs: tuple[VersionRef, ...]
    after_refs: tuple[VersionRef, ...]
    occurrence: ActiveOccurrence
    outcome_id: str | None = None
    proposed_identities: bool = True


@dataclass(frozen=True, slots=True)
class Successor:
    state: AnalysisState | None
    action: ActionEvidence
    safety_violations: tuple[str, ...] = ()
    unknown_reasons: tuple[str, ...] = ()
    candidate_json: str | None = None


@dataclass(frozen=True, slots=True)
class PropertyResult:
    property_id: str
    verdict: Verdict
    reason: str
    quantifier: str = ""
    scope: str = "finite exact identity graph"
    assumptions: tuple[str, ...] = ()
    witness: tuple[ActionBinding, ...] = ()
