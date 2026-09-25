"""Shared Registry mechanics for every supported AgentLoop composition.

Policy adapters prepare prompts, tools, workspace effects and review decisions.
This component alone commits the common Loop/turn/action lifecycle and reads
immutable action authority.  It never chooses Current policy or opens a writer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from typing import Any, Callable, Mapping, Sequence

from cpn.rpnh.llm_contracts import LLMCallAttempt
from cpn.rpnh.registry.event_store import PendingEvent
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.models import TypedRelation, VersionRef
from cpn.rpnh.registry.operations import OperationExecutionBlockAuthority
from cpn.rpnh.registry.provider_calls import LLMCallV2, ProviderAttemptV2
from cpn.rpnh.registry.publication import (
    _ref_payload, _resource_from_payload, _stable_id, _version_from_payload,
)
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.response_protocol import (
    PublishedLLMResponse, observe_registered_llm_response,
)

from .models import (
    AgentActionRecord, AgentContextOverlay, AgentLoopSnapshot,
    AgentLoopState, AgentToolCallFact, AgentTurnRecord,
    require_state_transition,
)
from .mechanical_contracts import (
    AgentLoopMechanicalLifecycleError,
    _LLM_FAILURE_DISPOSITIONS,
    _LLM_FAILURES_REQUIRING_TRANSPORT_DETAIL,
    _LLM_SUBMISSION_STATES,
    _OWNER_INTERRUPTION_SUBMISSION_STATES,
    _RETRYABLE_LLM_FAILURE_DISPOSITIONS,
)


from .loop_state import LoopStateMechanicsMixin
from .turn_records import TurnRecordsMechanicsMixin
from .action_records import (
    ActionRecordsMechanicsMixin,
    AgentActionSettlementPlan,
)
from .compaction import CompactionRecordsMechanicsMixin
from .delegation import DelegationRecordsMechanicsMixin
from .workspace import WorkspaceRecordsMechanicsMixin
from .resource_wait import ResourceWaitRecordsMechanicsMixin


class AgentLoopMechanicalLifecycle(LoopStateMechanicsMixin, TurnRecordsMechanicsMixin, ActionRecordsMechanicsMixin, CompactionRecordsMechanicsMixin, DelegationRecordsMechanicsMixin, WorkspaceRecordsMechanicsMixin, ResourceWaitRecordsMechanicsMixin):
    """Compatibility façade over the sole mechanical lifecycle owner."""

    def __init__(self, core: Any, kernel: Any) -> None:
        if getattr(kernel, "core", core) is not core:
            raise TypeError("AgentLoop mechanics require one Registry Core/kernel")
        self.core = core
        self.kernel = kernel
