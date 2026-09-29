"""AgentLoop workspace mechanics.

Methods operate on the owning mechanical lifecycle instance; they do not
own Registry state or create a second transaction coordinator.
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


class WorkspaceRecordsMechanicsMixin:
    def settle_monitored_workspace_action(
            self, *, before: AgentLoopSnapshot, after: AgentLoopSnapshot,
            turn_ref: VersionRef, pending_action_ref: VersionRef,
            settled_record: AgentActionRecord,
            idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
            error_documents: Mapping[VersionRef, Mapping[str, Any]] | None = None,
    ) -> tuple[AgentLoopSnapshot, AgentActionRecord, VersionRef]:
        """Settle one previously persisted ACTION_PENDING action atomically."""
        current = self.require_current_revision(
            before, allowed_states=(AgentLoopState.ACTION_PENDING,))
        pending = self.hydrate_action(pending_action_ref)
        if (pending.state != AgentLoopState.ACTION_PENDING
                or pending.action_id != settled_record.action_id
                or self.turn_ref_for(
                    current.loop_id, pending.turn_sequence) != turn_ref
                or settled_record.state == AgentLoopState.ACTION_PENDING
                or settled_record.loop_id != current.loop_id
                or after.loop_id != current.loop_id
                or after.revision != current.revision + 1
                or turn_ref.entity_type != "agent_turn/v1"):
            raise AgentLoopMechanicalLifecycleError(
                "monitored action settlement differs from its pending authority")
        version = _stable_id(
            "agent_action_version", settled_record.action_id,
            idempotency_key)
        settled_ref = VersionRef(
            "agent_action/v2",
            TypedId.parse(settled_record.action_id, expected="agent_action"),
            version)

        def settle(tx: Any) -> None:
            if stage is not None:
                stage(tx)
            document = self._action_document(
                settled_record, action_ref=settled_ref,
                loop_ref=self.loop_ref(after), turn_ref=turn_ref)
            self.prewrite_object(
                tx, settled_ref, document,
                producer_invocation_id=current.invocation_ref.entity_id)
            for error_ref, raw in (error_documents or {}).items():
                error = dict(raw)
                error["agent_loop_ref"] = _ref_payload(self.loop_ref(after))
                self.prewrite_object(
                    tx, error_ref, error,
                    producer_invocation_id=current.invocation_ref.entity_id)

        event = ("agent_action_settled/v1", current.loop_id, "agent_loop", {
            "agent_loop_id": current.loop_id,
            "agent_turn_id": str(turn_ref.entity_id),
            "agent_action_id": settled_record.action_id,
            "tool_call_ordinal": settled_record.tool_call_ordinal,
            "tool_call_id": settled_record.tool_call_id,
            "action_identity_kind": settled_record.action_identity_kind,
            "action_identity_key": settled_record.action_identity_key,
            "tool_name": settled_record.tool_name,
            "raw_arguments": settled_record.raw_arguments,
            "arguments": (dict(settled_record.arguments)
                          if settled_record.arguments is not None else None),
            "expected_revision": settled_record.expected_revision,
            "settlement": settled_record.state.value,
            "result_version_ids": [str(ref.version_id)
                                   for ref in settled_record.result_refs],
            "tool_error_version_id": (
                str(settled_record.tool_error_ref.version_id)
                if settled_record.tool_error_ref is not None else None),
            "revision": after.revision,
        })
        committed = self.commit_monitored_workspace_suffix(
            before=current, after=after, idempotency_key=idempotency_key,
            stage=settle, events=(event,))
        return committed, self.hydrate_action(settled_ref), settled_ref

    def commit_monitored_workspace_suffix(
            self, *, before: AgentLoopSnapshot,
            after: AgentLoopSnapshot, idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
            events: Sequence[tuple[str, str, str, Mapping[str, Any]]] = (),
    ) -> AgentLoopSnapshot:
        if before.state != AgentLoopState.ACTION_PENDING:
            raise AgentLoopMechanicalLifecycleError(
                "monitored workspace suffix requires ACTION_PENDING")
        return self.commit_loop_successor(
            before, after, idempotency_key=idempotency_key, stage=stage,
            events=events)


__all__ = ["WorkspaceRecordsMechanicsMixin"]


from collections.abc import Mapping
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import replace
import errno
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import stat
import uuid

from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.llm_contracts import LLMCallAttempt, LLMInputTarget
from cpn.rpnh.response_protocol import (
    PublishedLLMResponse, canonicalize_llm_response_payload,
    observe_registered_llm_response,
)
from cpn.rpnh.registry.errors import (
    ResourceIntegrityFault, ResourcePayloadSchemaViolation,
    StaleAuthorityHead, UnauthorizedResourceDelivery,
)
from cpn.rpnh.registry.firing_authority import canonical_invocation
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.agent_resource_broker import (
    AgentLoopResourceRequest, prepare_agent_resource_request,
    stage_resumed_agent_resource_lifecycle,
)
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operation_execution import verify_operation_execution
from cpn.rpnh.registry.publication import (
    _append_direct_resource_version_publication, _direct_resource_metadata,
    _provider_request_resource_metadata, _ref_payload, _registry_type_catalog_ref,
    _resource_from_payload, _version_from_payload, _stable_id,
)
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resource_verification import verify_resource
from cpn.rpnh.registry.resources import (
    AcknowledgeResourceDelivery, AddressBindingIntent,
    AgentLoopResourceGrantAuthority, AgentLoopResourceLifecycleAuthority,
    AuthorizeResourceRelease, PetriOutputOrigin, PrepareResourceDelivery,
    PublishResource, ResourceAddress, ResourceVersionRef,
    UnbindResourceAddress, WorkspaceWriteOrigin,
)
from cpn.rpnh.registry.schema_catalog import TypeDefinition, canonical_json

from .compact import build_replacement_history, reduce_tool_messages, should_compact
from .request_envelope_materialization import materialize_agent_request_envelope
from .models import (
    AgentActionRecord, AgentLoopSnapshot, AgentLoopState, AgentTurnRecord,
    LocatedAgentInput,
)
from .service import (
    AgentLengthInterruptionRecord, AgentLoopRegistryPort,
    CompletedAgentContextCompaction, PreparedAgentContextCompaction,
    ParentOwnedDelegatedSubtaskLengthReplay, ParentOwnedDelegatedSubtaskRequest,
    ParentOwnedDelegatedSubtaskResult, ParentOwnedDelegatedSubtaskToolStep,
    PreparedAgentLLMTurn, PreparedAgentTurnContext,
    PreparedParentOwnedDelegatedSubtaskCall, StartAgentLoopCommand,
    _PreparedParentOwnedDelegatedSubtaskContext,
)
from .delegated_history import compact_delegated_subtask_history_after_length
from .tool_catalog import (
    TOOL_ARGUMENT_SCHEMAS, AgentToolCatalog, build_agent_tool_catalog,
    derive_atomic_subtask_tools, parse_agent_tool_catalog,
)
from .tool_validation import (
    AgentToolSyntaxError, ValidatedAgentToolAction,
    validate_agent_tool_observation,
)
from .tool_projection import (
    bounded_agent_action_output_projection, bounded_agent_text_search_projection,
)
from cpn.components.tool_executors import (
    ExecutionEnvironmentIdentity, NumericalToolProfile,
    query_execution_environment_resources,
)
from .action_execution import (
    EXECUTION_PROVENANCE_DOCUMENT, EXECUTION_PROVENANCE_SCHEMA,
    OPTIONAL_TOOL_BINDINGS, OptionalAgentCapabilityUnavailable,
)


_WORKSPACE_STAGING_PREFIX = ".rpnh-write-stage-"


def _semantic_workspace_path(
        requested: PurePosixPath, symbolic_port_name: str,
) -> PurePosixPath:
    """Give intermediate graph products a deterministic port-owned path.

    Parallel graph nodes share one settled workspace lineage.  Their semantic
    products are already distinct Registry resources, but providers commonly
    choose the same presentation path (for example ``outputs/result.json``)
    for every node.  Keep those files in the persistent workspace under their
    exact internal output handle so unrelated parallel products can merge.
    Public egress and non-graph products retain the provider-requested path.
    """

    prefix = "team.output__"
    if not symbolic_port_name.startswith(prefix):
        return requested
    relative_parts = (
        requested.parts[1:]
        if requested.parts[:1] == ("outputs",) else requested.parts)
    if not relative_parts:
        relative_parts = ("result",)
    return PurePosixPath(
        "outputs", "ports", symbolic_port_name.removeprefix("team."),
        *relative_parts)

class WorkspaceExecutionMixin:
    def _execute_file_materialization(
            self, context, identity_key, materialize):
        from cpn.rpnh.file_execution_net import (
            FILE_MATERIALIZATION_NET,
            execute_idempotent_materialization,
        )
        _state, evidence_refs = execute_idempotent_materialization(
            self.core, context=context,
            definition=FILE_MATERIALIZATION_NET,
            transition_id="materialize_file",
            identity_key=identity_key,
            materialize=materialize,
        )
        return evidence_refs

    def _workspace_runtime(self):
        environment_rows = self.core.event_store.canonical_object_rows(
            object_type="execution_environment_identity/v1")
        profile_rows = self.core.event_store.canonical_object_rows(
            object_type="numerical_tool_profile/v1")
        if len(environment_rows) != 1 or len(profile_rows) != 1:
            raise OptionalAgentCapabilityUnavailable(
                "workspace requires one registered environment and profile")
        environment_ref = self._row_ref(
            environment_rows[0], "execution_environment_identity/v1",
            "execution_environment", "execution_environment_version")
        profile_ref = self._row_ref(
            profile_rows[0], "numerical_tool_profile/v1",
            "numerical_tool_profile", "numerical_tool_profile_version")
        environment_data = json.loads(environment_rows[0]["metadata_json"])
        profile_data = json.loads(profile_rows[0]["metadata_json"])
        environment = ExecutionEnvironmentIdentity(
            environment_ref, environment_data["name"],
            environment_data["python_executable"],
            environment_data["python_prefix"])
        profile = NumericalToolProfile(
            profile_ref, _version_from_payload(profile_data["environment_ref"]),
            profile_data["timeout_seconds"], profile_data["memory_bytes"],
            profile_data["process_limit"], profile_data["source_size_bytes"],
            profile_data["input_size_bytes"])
        if profile.environment_ref != environment.environment_ref:
            raise ResourceIntegrityFault(
                "workspace profile differs from its registered environment")
        return environment, profile

    def _workspace_template(self, context):
        binding = self.kernel._exact_object(
            context.operation_binding_ref,
            expected_type="operation_binding/v1").metadata
        raw = binding.get("workspace_binding_ref")
        if raw is None:
            raise OptionalAgentCapabilityUnavailable(
                "operation has no workspace binding")
        ref = _version_from_payload(raw)
        template = self.kernel._exact_object(
            ref, expected_type="workspace_binding/v1").metadata
        return ref, template

    def _workspace_view(self, loop, *, native_resume=False):
        context = self._context(loop, native_resume=native_resume)
        ref = loop.workspace_binding_ref
        if ref is None:
            raise OptionalAgentCapabilityUnavailable(
                "agent loop has no firing workspace binding")
        view = self.kernel._exact_object(
            ref, expected_type="workspace_binding/v1").metadata
        if (view.get("binding_kind") != "firing_view"
                or view.get("invocation_ref")
                != _ref_payload(context.invocation_ref)
                or view.get("transition_firing_ref")
                != _ref_payload(context.own_transition_firing_ref)):
            raise ResourceIntegrityFault(
                "agent loop workspace differs from its firing authority")
        return ref, view

    def _workspace_root(self, loop, *, native_resume=False):
        from cpn.rpnh.registry.resource_service import (
            _resolve_registry_workspace_root,
        )
        _ref, template = self._workspace_view(
            loop, native_resume=native_resume)
        root = _resolve_registry_workspace_root(
            self.core, template["allowed_root"])
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _workspace_write_intent(self, context, template_ref):
        matches = []
        for row in self.core.event_store.canonical_object_rows(
                object_type="workspace_write_intent/v1"):
            metadata = json.loads(row["metadata_json"])
            if (metadata.get("operation_binding_ref")
                    == _ref_payload(context.operation_binding_ref)
                    and metadata.get("source_binding_ref")
                    == _ref_payload(template_ref)):
                matches.append(self._row_ref(
                    row, "workspace_write_intent/v1",
                    "write_intent", "write_intent_version"))
        if len(matches) != 1:
            raise ResourceIntegrityFault(
                "workspace binding lacks one exact write intent")
        return matches[0]

    @staticmethod
    def _strict_relative_path(value):
        path = PurePosixPath(value)
        if (path.is_absolute() or not path.parts
                or any(part in {"", ".", ".."} for part in path.parts)
                or path.parts[0] == "registered_resources"):
            raise ValueError(
                "workspace file path must be relative and outside registered_resources")
        return path

    @staticmethod
    def _write_workspace_bytes(
            root, relative_path, payload, *, allow_registered_resources=False,
            mode=0o600, before_replace=None):
        relative = PurePosixPath(relative_path)
        if (relative.is_absolute() or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
                or (relative.parts[0] == "registered_resources"
                    and not allow_registered_resources)):
            raise ValueError("workspace destination is not a strict relative path")
        directory_flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0))
        file_flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                      | getattr(os, "O_NOFOLLOW", 0)
                      | getattr(os, "O_NONBLOCK", 0))
        descriptors = []
        staging_name = None
        staging_directory = None
        try:
            root_descriptor = os.open(root, directory_flags)
            descriptors.append(root_descriptor)
            try:
                os.mkdir("registered_resources", mode=0o700,
                         dir_fd=root_descriptor)
            except FileExistsError:
                pass
            staging_directory = os.open(
                "registered_resources", directory_flags,
                dir_fd=root_descriptor)
            descriptors.append(staging_directory)
            current = root_descriptor
            for part in relative.parts[:-1]:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
                current = os.open(part, directory_flags, dir_fd=current)
                descriptors.append(current)
            target_name = relative.parts[-1]
            try:
                target_status = os.stat(
                    target_name, dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                target_status = None
            if target_status is not None and stat.S_ISLNK(target_status.st_mode):
                raise OSError(
                    errno.ELOOP, "workspace destination must not be a symlink",
                    target_name)
            if (target_status is not None
                    and not stat.S_ISREG(target_status.st_mode)):
                raise ValueError(
                    "workspace destination must be one regular file")

            staging_name = (
                f"{_WORKSPACE_STAGING_PREFIX}{uuid.uuid4().hex}.tmp")
            target = os.open(
                staging_name, file_flags, mode=0o600,
                dir_fd=staging_directory)
            descriptors.append(target)
            remaining = memoryview(payload)
            while remaining:
                written = os.write(target, remaining)
                if written < 1:
                    raise OSError("workspace write made no progress")
                remaining = remaining[written:]
            os.fchmod(target, mode)
            os.fsync(target)
            os.close(target)
            descriptors.pop()

            result = before_replace() if before_replace is not None else None
            os.replace(
                staging_name, target_name,
                src_dir_fd=staging_directory, dst_dir_fd=current)
            staging_name = None
            # The execution checkpoint may become map-ready immediately after
            # this method returns.  Flush every opened directory in the path so
            # a host/filesystem crash cannot leave Registry evidence ahead of
            # the visible rename or newly created parent directories.
            for descriptor in dict.fromkeys(reversed(descriptors)):
                os.fsync(descriptor)
            return result
        finally:
            if staging_name is not None and staging_directory is not None:
                try:
                    os.unlink(staging_name, dir_fd=staging_directory)
                except FileNotFoundError:
                    pass
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _workspace_files(self, root):
        files = []
        for current, directories, names in os.walk(root, followlinks=False):
            relative_dir = Path(current).relative_to(root)
            if relative_dir == Path("."):
                directories[:] = [
                    name for name in directories
                    if name != "registered_resources"
                    and not (Path(current) / name).is_symlink()]
            else:
                directories[:] = [
                    name for name in directories
                    if not (Path(current) / name).is_symlink()]
            for name in names:
                candidate = Path(current) / name
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                relative = candidate.relative_to(root).as_posix()
                self._strict_relative_path(relative)
                files.append(relative)
        return tuple(sorted(files))

    def _current_workspace_resource(
            self, context, relative_path, *, native_resume=False):
        address = ResourceAddress(context.task_ref, relative_path)
        binding_ref = self.kernel._current_binding(
            address,
            through_ordinal=self.core.event_store.max_ordinal(),
            include_tombstone=True,
            provisional_firing_ref=context.own_transition_firing_ref,
        )
        if binding_ref is None:
            return None, None
        if binding_ref.tombstone:
            return None, binding_ref
        binding = self.kernel._exact_object(
            binding_ref.as_version_ref(),
            expected_type="resource_address_binding/v1")
        resource_ref = _resource_from_payload(
            binding.metadata["resource_ref"])
        self.kernel._authorize_resource(
            context, context.operation_binding_ref,
            resource_ref, metadata_only=True,
            native_resume=native_resume)
        prepared = self.kernel._firing_prepared(
            context, resource_ref)
        metadata = prepared.metadata
        if (metadata.get("origin_kind") != "workspace_write"
                or metadata.get("descriptors", {}).get("workspace_path")
                != relative_path):
            raise ResourceIntegrityFault(
                "workspace address does not resolve to its registered file")
        return resource_ref, binding_ref

    def _register_workspace_file(
            self, loop, relative_path, key, *, summary=None,
            context=None, native_resume=False):
        context = context or self._context(
            loop, native_resume=native_resume)
        template_ref, _template = self._workspace_template(context)
        root = self._workspace_root(loop, native_resume=native_resume)
        relative = self._strict_relative_path(relative_path)
        absolute = root.joinpath(*relative.parts)
        if absolute.is_symlink() or not absolute.is_file():
            raise ValueError("workspace publication requires one regular file")
        environment, profile = self._workspace_runtime()
        del environment
        if absolute.stat().st_size > profile.input_size_bytes:
            raise ValueError(
                "workspace file exceeds the registered publication byte limit")
        prior_ref, prior_binding = self._current_workspace_resource(
            context, relative.as_posix(), native_resume=native_resume)
        payload = absolute.read_bytes()
        # The address head is append-only and can still name a resource from a
        # later execution generation after the owner reopens an older
        # checkpoint.  Content convergence is reusable only when that exact
        # resource belongs to this firing's selected workspace base; otherwise
        # this firing must publish its own accepted path evidence.
        from cpn.rpnh.workspace_settlement import _workspace_resource_state
        base_ref = _workspace_resource_state(
            self.core, loop.workspace_base_revision_ref).get(
                relative.as_posix())
        if (prior_ref is not None
                and prior_ref == base_ref
                and self.kernel._read_firing_registered(context, prior_ref)
                == payload):
            return prior_ref, False
        intent_ref = self._workspace_write_intent(context, template_ref)
        supersedes = None
        if prior_ref is not None:
            prior = self.kernel._firing_prepared(context, prior_ref)
            if (prior.metadata.get("origin", {}).get("primary_ref")
                    == _ref_payload(context.operation_binding_ref)):
                supersedes = prior_ref
        media_type = mimetypes.guess_type(relative.name)[0]
        if media_type is None:
            media_type = "application/octet-stream"
        command = PublishResource(
            origin=WorkspaceWriteOrigin(
                context.operation_binding_ref, intent_ref),
            payload=payload,
            media_type=media_type,
            content_schema_ref=None,
            summary=(summary or f"Workspace file {relative.as_posix()}")[:600],
            lifetime_ref=context.invocation_ref,
            address_bindings=(AddressBindingIntent(
                ResourceAddress(context.task_ref, relative.as_posix()),
                context.operation_binding_ref, prior_binding),),
            supersedes=supersedes,
            descriptors={
                "content_role": "workspace_file",
                "workspace_path": relative.as_posix(),
                "producer_turn": loop.next_turn_sequence,
            },
            idempotency_key=key,
        )
        return self.kernel._publish(
            context, command, native_resume=native_resume), True

    def _sync_workspace(
            self, execution, loop, key, *, native_resume=False):
        context = self._context(loop, native_resume=native_resume)
        if execution.operation.canonical.context != context:
            raise ResourceIntegrityFault(
                "workspace sync differs from its firing invocation")
        from cpn.rpnh.workspace_settlement import _archive_files

        root = self._workspace_root(loop, native_resume=native_resume)
        current_paths = self._workspace_files(root)
        base_files = _archive_files(
            self.core, loop.workspace_base_revision_ref)
        published = []
        for ordinal, relative_path in enumerate(current_paths):
            absolute = root.joinpath(*PurePosixPath(relative_path).parts)
            current_state = (
                absolute.read_bytes(), stat.S_IMODE(absolute.stat().st_mode))
            if base_files.get(relative_path) == current_state:
                continue
            ref, changed = self._register_workspace_file(
                loop, relative_path,
                f"{key}:file:{ordinal}:{relative_path}",
                context=context, native_resume=native_resume)
            if changed:
                published.append(ref)
        for ordinal, relative_path in enumerate(
                sorted(set(base_files) - set(current_paths))):
            _prior_ref, binding_ref = self._current_workspace_resource(
                context, relative_path, native_resume=native_resume)
            if binding_ref is None:
                continue
            self.kernel.unbind_address(context, UnbindResourceAddress(
                ResourceAddress(context.task_ref, relative_path),
                context.operation_binding_ref, binding_ref,
                f"{key}:delete:{ordinal}:{relative_path}"),
                native_resume=native_resume)
        return tuple(published)

    def _run_workspace(self, execution, loop, arguments, _key):
        self._execution(execution, loop)
        if arguments.get("execution_mode", "sync") != "sync":
            raise ValueError("standalone workspace supports sync execution only")
        self._project_workspace(execution, loop)
        root = self._workspace_root(loop)
        environment, profile = self._workspace_runtime()
        requested_timeout = arguments["timeout_seconds"]
        timeout = (
            requested_timeout if profile.timeout_seconds is None else
            min(requested_timeout, profile.timeout_seconds))
        from . import optional_execution as _optional_execution
        result = _optional_execution.execute_bounded_workspace_tool(
            script=arguments["script"], cwd=str(root),
            timeout_seconds=timeout, max_output_bytes=1024 * 1024,
            environment=environment, profile=profile)
        return (), {
            "kind": "workspace_execution/v1", **result}

    def finalize_agent_workspace_v1(
            self, execution, loop, *, idempotency_key):
        execution = self._execution(execution, loop)
        published = []

        def synchronize():
            published.extend(self._sync_workspace(
                execution, loop, idempotency_key))

        from cpn.rpnh.workspace_settlement import (
            finalize_firing_workspace_candidate,
        )
        finalize_firing_workspace_candidate(
            self.core, self.kernel, execution, loop,
            idempotency_key=idempotency_key,
            synchronize=synchronize)
        return tuple(ref.as_version_ref() for ref in published)

    def finalize_recovered_agent_workspace_v1(
            self, execution, loop, *, idempotency_key):
        """Finalize one stale firing at an authorized native-recovery cut."""

        context = self._context(loop, native_resume=True)
        if execution.operation.canonical.context != context:
            raise ResourceIntegrityFault(
                "workspace recovery differs from its firing invocation")
        current = self.mechanical_lifecycle.current_loop(loop)
        published = []

        def synchronize():
            published.extend(self._sync_workspace(
                execution, current, idempotency_key,
                native_resume=True))

        from cpn.rpnh.workspace_settlement import (
            finalize_firing_workspace_candidate,
        )
        finalize_firing_workspace_candidate(
            self.core, self.kernel, execution, current,
            idempotency_key=idempotency_key,
            synchronize=synchronize)
        return tuple(ref.as_version_ref() for ref in published)

    def _settled_workspace_resources(self, context, revision_ref):
        """Resolve exact path resources at one immutable revision head."""
        from cpn.rpnh.workspace_settlement import _workspace_resource_state

        resources = _workspace_resource_state(self.core, revision_ref)

        result = []
        for path, ref in sorted(resources.items()):
            try:
                self._strict_relative_path(path)
                self.kernel._authorize_resource(
                    context, context.operation_binding_ref,
                    ref, metadata_only=True)
            except UnauthorizedResourceDelivery:
                continue
            result.append((path, ref))
        return tuple(result)

    def _project_workspace(self, execution, loop):
        from cpn.rpnh.workspace_settlement import _archive_files

        context = self._context(loop)
        root = self._workspace_root(loop)
        if loop.next_turn_sequence == 0:
            for relative_path, (payload, mode) in _archive_files(
                    self.core,
                    loop.workspace_base_revision_ref).items():
                self._write_workspace_bytes(
                    root, relative_path, payload, mode=mode)
        located = []
        seen = set()
        for item in execution.operation.inputs:
            header = self.kernel._firing_header(
                context, item.resource_ref)
            path = self._input_path(item.resource_ref)
            payload = self.kernel._read_firing_registered(
                context, item.resource_ref)
            self._write_workspace_bytes(
                root, path, payload, allow_registered_resources=True)
            located.append(LocatedAgentInput(
                item.resource_ref, path, semantic_name=item.port_id,
                file_name="content",
                summary=header.display_summary or "Registered input",
                media_type=header.media_type,
                content_schema_ref=header.content_schema_ref))
            seen.add(item.resource_ref)
        for relative_path, ref in self._settled_workspace_resources(
                context, loop.workspace_base_revision_ref):
            if ref in seen:
                continue
            header = self.kernel._firing_header(context, ref)
            payload = self.kernel._read_firing_registered(context, ref)
            registry_path = self._input_path(ref)
            self._write_workspace_bytes(
                root, registry_path, payload,
                allow_registered_resources=True)
            located.append(LocatedAgentInput(
                ref, registry_path, semantic_name="workspace_file",
                file_name=PurePosixPath(relative_path).name,
                source_relative_path=relative_path,
                summary=header.display_summary or (
                    f"Registered workspace file {relative_path}"),
                media_type=header.media_type,
                content_schema_ref=header.content_schema_ref))
            seen.add(ref)
        return tuple(located)

    def prepare_workspace_action_execution_v1(
            self, execution, loop, turn, action):
        execution = self._execution(execution, loop)
        self._current(loop)
        if turn != self.hydrate_current_agent_turn_v1(loop):
            raise ResourceIntegrityFault(
                "workspace execution differs from the current stored turn")
        validation = getattr(action, "validation", None)
        if (not isinstance(validation, ValidatedAgentToolAction)
                or getattr(validation, "tool_name", None) != "workspace"
                or validation.arguments.get("execution_mode", "sync")
                != "sync"):
            raise ValueError(
                "standalone workspace execution requires one sync action")
        self._project_workspace(execution, loop)
        root = self._workspace_root(loop)
        environment, profile = self._workspace_runtime()
        requested_timeout = validation.arguments["timeout_seconds"]
        timeout = (
            requested_timeout if profile.timeout_seconds is None else
            min(requested_timeout, profile.timeout_seconds))
        return {
            "script": validation.arguments["script"],
            "cwd": str(root),
            "timeout_seconds": timeout,
            "max_output_bytes": 1024 * 1024,
            "environment": environment,
            "profile": profile,
        }

    def _decoded_read(self, context, ref, arguments):
        payload = self.kernel._read_firing_registered(context, ref)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "read_file requires registered UTF-8 text") from exc
        prepared = self.kernel._firing_prepared(context, ref)
        metadata = prepared.metadata
        schema_less_workspace = (
            metadata.get("origin_kind") == "workspace_write"
            and metadata.get("content_schema_ref") is None)
        if not schema_less_workspace:
            if metadata.get("media_type") == "application/json":
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ResourceIntegrityFault(
                        "registered JSON read is not valid JSON") from exc
                else:
                    if isinstance(data, str):
                        text = data
            elif not str(metadata.get("media_type", "")).startswith("text/"):
                raise ValueError(
                    "read_file requires a registered text resource")
        offset = arguments.get("offset_chars", 0)
        return text[offset:offset + arguments.get("max_chars", 32768)]

    def _read_input(self, execution, loop, arguments, key):
        context = self._context(loop)
        refs = [item.resource_ref
                for item in self._project_workspace(execution, loop)
                if item.sandbox_path == arguments["path"]]
        if len(refs) != 1:
            raise ValueError("read_file path must be an exact registered input")
        ref = refs[0]
        self._decoded_read(context, ref, arguments)
        terminal, _, _ = self._deliver(context, ref, "tool_result", key)
        delivery_ref = self.mechanical_lifecycle.acknowledged_delivery_ref(
            terminal)
        return (ref.as_version_ref(), delivery_ref), {
            "kind": "registered_file_read/v1", "path": arguments["path"],
            "resource_ref": _resource_payload(ref),
            "use_receipt_ref": _ref_payload(delivery_ref)}

    def _search_text(self, execution, loop, arguments, key):
        context = self._context(loop)
        refs = [
            item.resource_ref
            for item in self._project_workspace(execution, loop)
            if item.sandbox_path == arguments["path"]
        ]
        if len(refs) != 1:
            raise ValueError(
                "search_text path must be one exact registered input")
        ref = refs[0]
        terminal, _receipt, payload = self._deliver(
            context, ref, "tool_result", key)
        delivery_ref = self.mechanical_lifecycle.acknowledged_delivery_ref(
            terminal)
        projection = bounded_agent_text_search_projection(
            payload, arguments)
        return (ref.as_version_ref(), delivery_ref), {
            "kind": "registered_text_search/v1",
            "path": arguments["path"],
            **projection,
            "resource_ref": _resource_payload(ref),
            "use_receipt_ref": _ref_payload(delivery_ref),
        }

    def _read_action_output(
            self, execution, loop, turn, arguments, _key):
        self._execution(execution, loop)
        raw_ref = arguments["agent_action_ref"]
        action_ref = VersionRef(
            "agent_action/v2",
            TypedId.parse(
                raw_ref["logical_id"], expected="agent_action"),
            TypedId.parse(
                raw_ref["version_id"], expected="agent_action_version"),
        )
        source = self.mechanical_lifecycle.hydrate_action(action_ref)
        if (source.loop_id != loop.loop_id
                or source.turn_sequence >= turn.sequence
                or source.tool_name != "workspace"
                or source.state != AgentLoopState.ACTION_APPLIED
                or not isinstance(source.result_metadata, Mapping)):
            raise ValueError(
                "read_action_output requires an earlier settled workspace action")
        result = bounded_agent_action_output_projection(
            source.result_metadata, arguments)
        return (action_ref,), result

    def _query_environment_resources(
            self, execution, loop, arguments, key):
        execution = self._execution(execution, loop)
        context = self._context(loop)
        _inventory_ref, _prepared, payload = self._static(
            context, "execution_environment_inventory")
        environment, _profile = self._workspace_runtime()
        result = query_execution_environment_resources(
            inventory=json.loads(payload),
            environment=environment,
            packages=tuple(arguments.get("packages", ())),
        )
        template_ref, _template = self._workspace_template(context)
        intent_ref = self._workspace_write_intent(context, template_ref)
        result_ref = self.kernel.publish_bytes(context, PublishResource(
            origin=WorkspaceWriteOrigin(
                context.operation_binding_ref, intent_ref),
            payload=canonical_json(result),
            media_type="application/json",
            content_schema_ref=None,
            summary="Execution environment resource query",
            lifetime_ref=context.invocation_ref,
            descriptors={
                "content_role": "execution_environment_query",
                "producer_turn": loop.next_turn_sequence - 1,
            },
            idempotency_key=key,
        ))
        metadata = dict(result)
        metadata["resource_ref"] = _resource_payload(result_ref)
        return (result_ref.as_version_ref(),), metadata

    @staticmethod
    def _optional_exact_ref(value):
        if value is None:
            return None
        if (isinstance(value, Mapping)
                and set(value) == {"entity_type", "logical_id", "version_id"}):
            return dict(value)
        if (isinstance(value, Mapping)
                and set(value) == {"resource_id", "resource_version_id"}):
            return {
                "entity_type": "resource_version/v1",
                "logical_id": value["resource_id"],
                "version_id": value["resource_version_id"],
            }
        raise ResourceIntegrityFault(
            "resource catalog lineage contains a malformed exact ref")

    def _query_registry_resources(
            self, execution, loop, arguments, _key):
        execution = self._execution(execution, loop)
        context = self._context(loop)
        query = arguments.get("query", "")
        view = arguments.get("view", "current")
        offset = arguments.get("offset", 0)
        page_size = arguments.get("page_size", 10)
        if (not isinstance(query, str) or len(query) > 256
                or view not in {"current", "history"}
                or isinstance(offset, bool) or not isinstance(offset, int)
                or offset < 0
                or isinstance(page_size, bool)
                or not isinstance(page_size, int)
                or not 1 <= page_size <= 20):
            raise ValueError("registry resource query page is invalid")

        candidates = []
        for row in self.core.event_store.object_rows_by_type(
                "resource_address_binding/v1"):
            binding = json.loads(row["metadata_json"])
            if (binding.get("scope_ref") != _ref_payload(context.task_ref)
                    or not isinstance(binding.get("opaque_name"), str)):
                continue
            candidates.append((
                binding["opaque_name"],
                int(binding["resulting_stream_sequence"]),
                VersionRef(
                    "resource_address_binding/v1",
                    TypedId.parse(
                        str(row["logical_id"]),
                        expected="resource_address_binding"),
                    TypedId.parse(
                        str(row["version_id"]),
                        expected="resource_address_binding_version"),
                ),
                binding,
            ))
        latest = {}
        for item in candidates:
            prior = latest.get(item[0])
            if prior is None or item[1] > prior[1]:
                latest[item[0]] = item

        rows = []
        for path, _sequence, binding_ref, binding in sorted(
                candidates, key=lambda item: (item[0], item[1])):
            is_current = latest[path][2] == binding_ref
            if (binding["lifecycle_state"] != "bound"
                    or view == "current" and not is_current):
                continue
            ref = _resource_from_payload(binding["resource_ref"])
            try:
                self.kernel._authorize_resource(
                    context, context.operation_binding_ref,
                    ref, metadata_only=True)
                resource = self.kernel._firing_prepared(context, ref)
            except UnauthorizedResourceDelivery:
                continue
            metadata = resource.metadata
            if metadata.get("origin_kind") != "workspace_write":
                continue
            producer_ref = metadata.get("producer_ref")
            firing_ref = node_ref = None
            transition_id = None
            if isinstance(producer_ref, Mapping):
                invocation_ref = _version_from_payload(producer_ref)
                invocation = self.kernel._exact_object(
                    invocation_ref, expected_type="invocation/v1").metadata
                firing_ref = invocation.get("own_transition_firing_ref")
                node_ref = invocation.get("own_node_ref")
                if isinstance(firing_ref, Mapping):
                    firing = self.kernel._exact_object(
                        _version_from_payload(firing_ref),
                        expected_type="transition_firing/v1").metadata
                    transition_id = firing.get("transition_id")
            descriptors = metadata.get("descriptors", {})
            provenance = metadata.get("reference_provenance", {})
            summary = metadata.get("summary")
            if isinstance(summary, str) and len(summary) > 600:
                summary = summary[:600]
                summary_truncated = True
            else:
                summary_truncated = False
            settlement_status = (
                "provisional"
                if producer_ref == _ref_payload(context.invocation_ref)
                else "settled")
            row_value = {
                "resource_id": str(ref.resource_id),
                "resource_version_id": str(ref.resource_version_id),
                "relative_path": path,
                "file_name": PurePosixPath(path).name,
                "description_summary": summary,
                "description_summary_truncated": summary_truncated,
                "content_role": descriptors.get("content_role"),
                "output_port_id": descriptors.get("output_port_id"),
                "producer_ref": (
                    dict(producer_ref)
                    if isinstance(producer_ref, Mapping) else None),
                "producer_transition_id": transition_id,
                "producer_node_ref": (
                    dict(node_ref) if isinstance(node_ref, Mapping) else None),
                "transition_firing_ref": (
                    dict(firing_ref)
                    if isinstance(firing_ref, Mapping) else None),
                "producer_turn": descriptors.get("producer_turn"),
                "settlement_status": settlement_status,
                "document_provenance": (
                    dict(provenance) if provenance else None),
                "source_execution": {
                    "operation_binding_ref": metadata.get(
                        "origin", {}).get("primary_ref"),
                },
                "destination_publication": {
                    "producer_ref": (
                        dict(producer_ref)
                        if isinstance(producer_ref, Mapping) else None),
                    "transition_firing_ref": (
                        dict(firing_ref)
                        if isinstance(firing_ref, Mapping) else None),
                    "settlement_status": settlement_status,
                },
                "lineage": {
                    "input_resource_refs": [],
                    "derived_from_refs": [
                        self._optional_exact_ref(value)
                        for value in provenance.get(
                            "derived_from_refs", ())],
                    "contributor_refs": [
                        self._optional_exact_ref(value)
                        for value in provenance.get(
                            "contributor_refs", ())],
                    "tool_evidence_refs": [
                        self._optional_exact_ref(value)
                        for value in provenance.get(
                            "tool_evidence_refs", ())],
                    "supersedes_ref": self._optional_exact_ref(
                        provenance.get("supersedes_ref")),
                    "address_binding_ref": _ref_payload(binding_ref),
                    "previous_address_binding_ref": (
                        dict(binding["previous_binding_ref"])
                        if isinstance(
                            binding.get("previous_binding_ref"), Mapping)
                        else None),
                },
                "currentness": (
                    "current" if is_current
                    and binding["lifecycle_state"] == "bound"
                    else "superseded"),
            }
            haystack = "\n".join(str(value) for value in (
                path, row_value["file_name"], summary or "",
                row_value["content_role"] or "",
                row_value["resource_id"],
                row_value["resource_version_id"],
            )).casefold()
            if query.casefold() in haystack:
                rows.append(row_value)
        total = len(rows)
        page = rows[offset:offset + page_size]
        next_offset = offset + len(page)
        return (), {
            "kind": "registry_resource_catalog/v1",
            "query": query,
            "view": view,
            "offset": offset,
            "page_size": page_size,
            "result_count": len(page),
            "total_count": total,
            "next_offset": next_offset if next_offset < total else None,
            "rows": page,
        }

    def _write_product(self, execution, loop, arguments, key):
        context = self._context(loop)
        _, compiled, operation = self._declared(context)
        requested_path = PurePosixPath(arguments["path"])
        if requested_path.is_absolute() or ".." in requested_path.parts:
            raise ValueError("write_file product path must be relative")
        requested = arguments.get("output_port_id")
        ports = [item for item in compiled.ports if item.name in operation.declaration.outputs]
        selected = [item for item in ports if requested in (item.name, item.port_id)] if requested else ports
        if len(selected) != 1:
            raise ValueError("write_file requires one exact declared symbolic output port")
        port = selected[0]
        path = self._strict_relative_path(
            _semantic_workspace_path(requested_path, port.name))
        semantic_outcomes = tuple(
            outcome for outcome in operation.declaration.outcomes
            if outcome.name != "interrupted")
        requested_outcome = arguments.get("outcome_id")
        if requested_outcome is None and len(semantic_outcomes) == 1:
            requested_outcome = semantic_outcomes[0].name
        outcome = next((item for item in semantic_outcomes
                        if item.name == requested_outcome), None)
        if outcome is None:
            raise ValueError(
                "write_file requires one exact declared semantic outcome_id")
        if port.name not in {product.port for product in outcome.products}:
            raise ValueError(
                "write_file output port is outside the selected outcome bundle")
        binding, = [item for item in execution.operation.operation_binding.output_port_bindings if item.port_id == port.port_id]
        if any(self.kernel._firing_prepared(context, ref).metadata.get("descriptors", {}).get("output_port_id") == port.port_id
               for ref in loop.written_resource_refs):
            raise ValueError("optional semantic port already has its registered product")
        # Earlier writes in this batch are committed provisional-firing
        # members, not canonical resources until the firing itself settles.
        prior_outcomes = {
            value
            for ref in loop.written_resource_refs
            for descriptor in self.kernel._firing_header(
                context, ref).descriptor_labels
            if descriptor.name == "output_outcome_id"
            for value in descriptor.values
        }
        if prior_outcomes and prior_outcomes != {outcome.name}:
            raise ValueError(
                "write_file cannot mix semantic outcomes in one firing")
        source_ref = None
        if "content" in arguments:
            supplied_content = arguments["content"]
            supplied_payload = supplied_content.encode("utf-8")
            if port.schema == "application/rpnh_agent_text/v1":
                # Text ports accept the direct string exposed by the tool
                # contract. Preserve the formerly documented JSON-string
                # spelling when supplied without requiring a second quoting
                # layer around direct text or a JSON document carried as text.
                try:
                    decoded = json.loads(supplied_payload)
                except json.JSONDecodeError:
                    decoded = None
                text = decoded if isinstance(decoded, str) else supplied_content
                payload = canonical_json(text)
                workspace_payload = text.encode("utf-8")
            else:
                decoded = json.loads(supplied_payload)
                payload = supplied_payload
                workspace_payload = (
                    decoded.encode("utf-8")
                    if isinstance(decoded, str) else supplied_payload)
        else:
            source_ref = _resource_from_payload(
                arguments["source_resource_ref"])
            _terminal, _receipt, workspace_payload = self._deliver(
                context, source_ref, "tool_result", key + ":source")
            if port.schema == "application/rpnh_agent_text/v1":
                try:
                    decoded = json.loads(workspace_payload)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    decoded = workspace_payload.decode("utf-8")
                payload = (
                    workspace_payload if isinstance(decoded, str)
                    and workspace_payload[:1] == b'"' else
                    canonical_json(decoded))
            else:
                payload = workspace_payload
        derived_from = (
            (source_ref,) if source_ref is not None else ())
        command = PublishResource(
            origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref), payload=payload,
            media_type="application/json", content_schema_ref=port.schema, summary=arguments["description"],
            lifetime_ref=context.invocation_ref, derived_from=derived_from,
            descriptors={"output_outcome_id": outcome.name,
                "output_port_id": port.port_id, "place": binding.place}, idempotency_key=key)
        # Reject deterministic path/schema/publication-contract faults before
        # attaching and starting the durable file execution child.  Failures
        # after this boundary may reflect an accepted publication or host
        # interruption and intentionally retain their same-key recovery net.
        self.kernel._preflight_publish(context, command)
        root = self._workspace_root(loop)
        def materialize(_state):
            published = self._write_workspace_bytes(
                root, path, workspace_payload,
                before_replace=lambda: self.kernel.publish_bytes(
                    context, command))
            return (published.as_version_ref(),)

        evidence_refs = self._execute_file_materialization(
            context, f"semantic-write:{key}", materialize)
        evidence_ref, = evidence_refs
        if evidence_ref.entity_type != "resource_version/v1":
            raise ResourceIntegrityFault(
                "file materialization evidence is not its exact resource")
        ref = ResourceVersionRef(
            evidence_ref.entity_id, evidence_ref.version_id)
        verify_resource(self.core, self.kernel, execution.operation.canonical, ref)
        return (ref.as_version_ref(),), {
            "kind": "registered_file_write/v1", "path": path.as_posix(),
            "description": arguments["description"], "output_port_id": port.port_id,
            "resource_ref": _resource_payload(ref), "registered": True}
