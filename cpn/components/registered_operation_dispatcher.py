"""Fail-closed consumer for Registry-admitted Petri operation producers.

The dispatcher consumes only the current registered-operation surface. Its
caller supplies immutable Registry authorities and
acknowledged ``petri_input`` artifacts.  Operation implementation selection is
made from the execution owner's HOST inventory after Registry hydration;
JSON callers cannot submit a
callable, module name, import path, argument dictionary, or live agent object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import TYPE_CHECKING

from .operation_gateway import (
    RegisteredOperationGateway, RegisteredOperationResources, has_port_methods,
)

from cpn.rpnh.registry.operations import (
    ClaimedPetriInputAuthority,
    canonical_claimed_petri_inputs,
)


_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cpn.rpnh.llm_contracts import LLMInputPort
    from .generic_critic import (
        GenericCriticInvocationAuthority,
        GenericCriticSelectionAuthority,
    )
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.operations import (
        OperationPortAuthority,
        OperationExecutionResult,
        OperationExecutionAuthority,
        RegisteredOperationAuthority,
        RegisteredOperationOutputsAuthority,
    )
    from cpn.rpnh.registry.resources import (
        CanonicalInvocationAuthority,
        ExecutableNetAuthority,
        ExecutableTransitionAuthority,
        PetriInputArtifact,
        PetriContinuation,
        ResourceVersionRef,
        TransitionFiringAuthority,
    )


class RegisteredOperationAuthorityRequiredError(RuntimeError):
    """The caller did not present one exact, firing-local authority closure."""


class RegisteredOperationContractUnavailableError(RuntimeError):
    """The Registry has not exposed the complete typed operation producer ABI."""


@dataclass(frozen=True, slots=True)
class RegisteredOperationImplementation:
    """HOST-registered semantic implementation identity, never executable code."""

    operation_id: str
    executor_key: str


@dataclass(frozen=True, slots=True)
class RegisteredOperationOutputRecord:
    """Executor-neutral view of one Registry-closed operation output."""

    place: str
    output_resource_ref: ResourceVersionRef
    work_resource_ref: ResourceVersionRef | None = None
    kind: str | None = None
    verdict: bool | str | None = None
    continuation: PetriContinuation | None = None

    def __post_init__(self) -> None:
        from cpn.rpnh.registry.resources import ResourceVersionRef

        if not isinstance(self.place, str) or not self.place.strip():
            raise RegisteredOperationAuthorityRequiredError(
                "registered output place must not be empty")
        if not isinstance(self.output_resource_ref, ResourceVersionRef):
            raise RegisteredOperationAuthorityRequiredError(
                "registered output requires an exact ResourceVersionRef")
        if (self.work_resource_ref is not None
                and not isinstance(self.work_resource_ref, ResourceVersionRef)):
            raise RegisteredOperationAuthorityRequiredError(
                "registered work output must be an exact ResourceVersionRef")
        if self.kind is not None and not isinstance(self.kind, str):
            raise RegisteredOperationAuthorityRequiredError(
                "registered output kind must be a string or None")
        if self.continuation is not None:
            from cpn.rpnh.registry.resources import PetriContinuation
            if not isinstance(self.continuation, PetriContinuation):
                raise RegisteredOperationAuthorityRequiredError(
                    "registered output continuation must be typed")


@dataclass(frozen=True, slots=True)
class RegisteredOperationProducts:
    """Only Registry-published output records may leave the dispatcher."""

    authority: RegisteredOperationOutputsAuthority
    produced: tuple[RegisteredOperationOutputRecord, ...]
    generic_critic_selection: GenericCriticSelectionAuthority | None = None
    timing_observation: object | None = field(
        default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        try:
            from cpn.rpnh.registry.operations import RegisteredOperationOutputsAuthority
        except ImportError as exc:
            raise RegisteredOperationContractUnavailableError(
                "Registry operation output authority ABI is unavailable") from exc
        if not isinstance(self.authority, RegisteredOperationOutputsAuthority):
            raise RegisteredOperationAuthorityRequiredError(
                "products require RegisteredOperationOutputsAuthority")
        produced = tuple(self.produced)
        if any(not isinstance(item, RegisteredOperationOutputRecord)
               for item in produced):
            raise RegisteredOperationAuthorityRequiredError(
                "products require typed registered output records")
        if len(produced) != len(self.authority.outputs):
            raise RegisteredOperationAuthorityRequiredError(
                "product records differ from Registry output cardinality")
        for record, closed in zip(
                produced, self.authority.outputs, strict=True):
            if (record.place != closed.place
                    or record.output_resource_ref != closed.resource_ref
                    or record.work_resource_ref != closed.work_resource_ref
                    or record.kind != closed.kind
                    or record.verdict != closed.verdict
                    or record.continuation != closed.continuation):
                raise RegisteredOperationAuthorityRequiredError(
                    "product record differs from Registry output authority")
        object.__setattr__(self, "produced", produced)
        if self.generic_critic_selection is not None:
            from .generic_critic import (
                GenericCriticSelectionAuthority,
            )
            if not isinstance(
                    self.generic_critic_selection,
                    GenericCriticSelectionAuthority):
                raise RegisteredOperationAuthorityRequiredError(
                    "products carry an untyped generic critic selection")


@dataclass(frozen=True, slots=True)
class RegisteredOperationCompletion:
    """Ordinary typed products plus process-local execution observations.

    This is not output, settlement or terminal authority. Registry publication
    remains the dispatcher's responsibility, including for external executors.
    """

    execution: OperationExecutionAuthority
    result: OperationExecutionResult
    generic_critic_selection: GenericCriticSelectionAuthority | None = None
    timing_observation: object | None = field(
        default=None, compare=False, repr=False)
    selected_outcome_id: str | None = None

    def __post_init__(self) -> None:
        from cpn.rpnh.registry.operations import (
            OperationExecutionAuthority, OperationExecutionResult,
        )
        if (not isinstance(self.execution, OperationExecutionAuthority)
                or not isinstance(self.result, OperationExecutionResult)):
            raise RegisteredOperationAuthorityRequiredError(
                "completion requires exact execution and ordinary typed result")
        if self.selected_outcome_id is not None and (
                not isinstance(self.selected_outcome_id, str)
                or not self.selected_outcome_id):
            raise RegisteredOperationAuthorityRequiredError(
                "completion outcome must be an explicit registered identity")
        if self.generic_critic_selection is not None:
            from .generic_critic import GenericCriticSelectionAuthority
            if not isinstance(
                    self.generic_critic_selection, GenericCriticSelectionAuthority):
                raise RegisteredOperationAuthorityRequiredError(
                    "completion carries an untyped generic critic selection")


@dataclass(frozen=True, slots=True)
class RegisteredOperationContext:
    """Neutral exact identities, not component-state or settlement authority."""

    transition_firing_ref: VersionRef
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    operation_execution_lease_ref: VersionRef
    writer_fencing_epoch: int

    def __post_init__(self) -> None:
        from cpn.rpnh.registry.models import VersionRef

        for ref, expected in (
            (self.transition_firing_ref, "transition_firing/v1"),
            (self.invocation_ref, "invocation/v1"),
            (self.operation_binding_ref, "operation_binding/v1"),
            (self.operation_execution_lease_ref, "operation_execution_lease/v1"),
        ):
            if not isinstance(ref, VersionRef) or ref.entity_type != expected:
                raise RegisteredOperationAuthorityRequiredError(
                    "operation context requires exact typed references")
        if (isinstance(self.writer_fencing_epoch, bool)
                or not isinstance(self.writer_fencing_epoch, int)
                or self.writer_fencing_epoch < 0):
            raise RegisteredOperationAuthorityRequiredError(
                "operation context requires an exact writer epoch")

    def verify_execution(self, execution: OperationExecutionAuthority) -> None:
        from cpn.rpnh.registry.operations import OperationExecutionAuthority

        if not isinstance(execution, OperationExecutionAuthority):
            raise RegisteredOperationAuthorityRequiredError(
                "operation context requires exact execution authority")
        operation = execution.operation
        if (self.transition_firing_ref != operation.firing.transition_firing_ref
                or self.invocation_ref != operation.canonical.context.invocation_ref
                or self.operation_binding_ref
                != operation.operation_binding.operation_binding_ref
                or self.operation_execution_lease_ref
                != execution.operation_execution_lease_ref
                or self.writer_fencing_epoch
                != execution.admission_head.writer_fencing_epoch):
            raise RegisteredOperationAuthorityRequiredError(
                "operation context differs from its exact execution")


@dataclass(frozen=True, slots=True)
class RegisteredOperationResourceWait:
    """HOST-verified wait payload bound to one exact live execution context."""

    execution: OperationExecutionAuthority
    wait: object = field(compare=False, repr=False)
    context: RegisteredOperationContext

    def __post_init__(self) -> None:
        if not isinstance(self.context, RegisteredOperationContext):
            raise RegisteredOperationAuthorityRequiredError(
                "resource wait requires exact neutral execution context")
        self.context.verify_execution(self.execution)


@dataclass(frozen=True, slots=True)
class RegisteredOperationTerminalHandoff:
    """HOST-verified Registry closure payload, distinct from operation success."""

    execution: OperationExecutionAuthority
    completion: object = field(compare=False, repr=False)
    context: RegisteredOperationContext

    def __post_init__(self) -> None:
        if not isinstance(self.context, RegisteredOperationContext):
            raise RegisteredOperationAuthorityRequiredError(
                "terminal handoff requires exact neutral execution context")
        self.context.verify_execution(self.execution)


@dataclass(frozen=True, slots=True)
class RegisteredOperationExecutionBlock:
    """Process-local disposition for one incomplete provisional firing."""

    execution: OperationExecutionAuthority
    authority: object
    agent_loop_block: object | None = field(
        default=None, compare=False, repr=False)
    timing_observation: object | None = field(
        default=None, compare=False, repr=False)
    context: RegisteredOperationContext | None = None

    def __post_init__(self) -> None:
        from cpn.rpnh.registry.operations import (
            OperationExecutionAuthority,
            OperationExecutionBlockAuthority,
        )
        if (not isinstance(self.execution, OperationExecutionAuthority)
                or not isinstance(
                    self.authority, OperationExecutionBlockAuthority)
                or self.authority.execution is not self.execution):
            raise RegisteredOperationAuthorityRequiredError(
                "execution block differs from its provisional operation firing")
        if self.agent_loop_block is not None:
            if not isinstance(self.context, RegisteredOperationContext):
                raise RegisteredOperationAuthorityRequiredError(
                    "component block payload requires exact neutral context")
        if self.context is not None:
            if not isinstance(self.context, RegisteredOperationContext):
                raise RegisteredOperationAuthorityRequiredError(
                    "execution block context is not typed")
            self.context.verify_execution(self.execution)


def supported_registered_operation_ids() -> tuple[str, ...]:
    """Return the execution owner's explicitly registered implementations."""

    from cpn.rpnh.registry.operations import registered_operation_ids

    return registered_operation_ids()


def _registered_implementation(
    operation_id: str, executor_key: str,
) -> RegisteredOperationImplementation:
    """Select one closed semantic implementation without loading executable code."""

    from cpn.rpnh.registry.operations import (
        OperationAuthorityError, registered_operation_contract,
    )

    try:
        registered_operation_contract(executor_key)
    except OperationAuthorityError as exc:
        raise RegisteredOperationAuthorityRequiredError(
            "Registry executor_key has no HOST-registered implementation") from exc
    return RegisteredOperationImplementation(operation_id, executor_key)


def _validate_petri_input_artifact(
    artifact: PetriInputArtifact, *, label: str,
) -> None:
    from cpn.rpnh.registry.resources import (
        PetriInputArtifact, HistoricalPetriInputArtifact, SettledPetriInputArtifact,
    )

    if not isinstance(artifact, (
            PetriInputArtifact, HistoricalPetriInputArtifact, SettledPetriInputArtifact)):
        raise RegisteredOperationAuthorityRequiredError(
            f"{label} requires a Registry-verified Petri input artifact")
    exact_ref = artifact.resource.header.ref
    if ((isinstance(artifact, PetriInputArtifact)
            and artifact.release.exact_resource_ref != exact_ref)
            or artifact.receipt.exact_resource_ref != exact_ref
            or artifact.receipt.positive_byte_count != len(artifact.payload)):
        raise RegisteredOperationAuthorityRequiredError(
            f"{label} lacks one exact petri_input receipt")


class _PermittedRegisteredOperationDispatch:
    """One-use-looking typed surface; Registry replay rules remain authoritative."""

    __slots__ = ("_dispatcher", "_permit")

    def __init__(self, dispatcher: "RegisteredOperationDispatcher", permit: object) -> None:
        self._dispatcher = dispatcher
        self._permit = permit

    def dispatch(
        self,
    ) -> (RegisteredOperationProducts | RegisteredOperationResourceWait
          | RegisteredOperationExecutionBlock
          | RegisteredOperationTerminalHandoff):
        return self._dispatcher._dispatch_permitted(self._permit)


class _RegisteredLLMHostContext:
    """Opted-in context view; ordinary executors never receive this member."""

    __slots__ = ("_dispatcher", "registered_llm")

    def __init__(self, dispatcher, registered_llm) -> None:
        self._dispatcher = dispatcher
        self.registered_llm = registered_llm

    def __getattr__(self, name):
        return getattr(self._dispatcher, name)


class RegisteredOperationDispatcher:
    """Execute one verified Petri operation through a closed exact shell.

    The fixed execution authority is admitted by Registry start and is consumed
    mechanically here.  Registry success is the sole firing-success boundary.
    """

    __slots__ = (
        "_registry", "_resources", "_executable_net", "_execution",
        "_llm_input_port", "_registered_llm",
        "_dispatch_permit", "_resource_resume",
    )

    def __init__(
        self, *, claimed_inputs: tuple[ClaimedPetriInputAuthority, ...],
        execution: OperationExecutionAuthority,
        executable: ExecutableNetAuthority,
        registry: RegisteredOperationGateway,
        resources: RegisteredOperationResources,
        llm_input_port: LLMInputPort | None = None,
        registered_llm: object | None = None,
    ) -> None:
        from cpn.rpnh.registry.resources import (
            CanonicalInvocationAuthority,
            ExecutableNetAuthority,
            ExecutableTransitionAuthority,
            TransitionFiringAuthority,
        )
        from cpn.rpnh.llm_contracts import LLMInputPort
        from cpn.rpnh.registry.operations import OperationExecutionAuthority

        if not isinstance(execution, OperationExecutionAuthority):
            raise RegisteredOperationAuthorityRequiredError(
                "dispatcher requires OperationExecutionAuthority")
        operation = execution.operation
        canonical = operation.canonical
        firing = operation.firing
        transition = operation.transition
        if (not isinstance(canonical, CanonicalInvocationAuthority)
                or not isinstance(firing, TransitionFiringAuthority)
                or not isinstance(transition, ExecutableTransitionAuthority)):
            raise RegisteredOperationAuthorityRequiredError(
                "dispatcher execution lacks its embedded authority closure")
        if not isinstance(executable, ExecutableNetAuthority):
            raise RegisteredOperationAuthorityRequiredError(
                "dispatcher requires ExecutableNetAuthority")
        if not has_port_methods(registry, RegisteredOperationGateway):
            raise RegisteredOperationAuthorityRequiredError(
                "dispatcher requires a registered operation gateway")
        if not has_port_methods(resources, RegisteredOperationResources):
            raise RegisteredOperationAuthorityRequiredError(
                "dispatcher requires a registered operation resource surface")
        if llm_input_port is not None and not isinstance(llm_input_port, LLMInputPort):
            raise RegisteredOperationAuthorityRequiredError(
                "configured LLM input port must satisfy its declared contract")
        claimed = tuple(claimed_inputs)
        if any(not isinstance(item, ClaimedPetriInputAuthority)
               for item in claimed):
            raise RegisteredOperationAuthorityRequiredError(
                "claimed inputs must be typed Petri input authorities")
        try:
            canonical_claimed = canonical_claimed_petri_inputs(claimed)
        except Exception as exc:
            raise RegisteredOperationAuthorityRequiredError(
                "claimed input authority closure is invalid") from exc
        if claimed != canonical_claimed:
            raise RegisteredOperationAuthorityRequiredError(
                "claimed inputs must use canonical port/resource/token order")
        context = canonical.context
        executable_matches = tuple(
            item for item in executable.transitions if item == transition)
        if (executable.net_ref != context.net_instance_ref
                or firing.net_ref != executable.net_ref
                or len(executable_matches) != 1):
            raise RegisteredOperationAuthorityRequiredError(
                "dispatcher execution is outside its executable net")
        if (context.origin != "petri_operation"
                or context.own_transition_firing_ref
                != firing.transition_firing_ref
                or context.operation_binding_ref
                != firing.operation_binding_ref
                or context.net_instance_ref != firing.net_ref
                or context.own_node_ref != firing.node_ref
                or context.principal_ref != firing.principal_ref
                or transition.transition_id != firing.transition_id
                or transition.node_ref != firing.node_ref
                or transition.operation_binding_ref
                != firing.operation_binding_ref
                or transition.principal_ref != firing.principal_ref
                or operation.operation_binding.operation_binding_ref
                != firing.operation_binding_ref
                or operation.operation_binding.operation_spec_ref
                != operation.spec.operation_spec_ref
                or operation.operation_binding.node_ref != firing.node_ref
                or operation.operation_binding.principal_ref
                != firing.principal_ref):
            raise RegisteredOperationAuthorityRequiredError(
                "dispatcher authorities do not name one admitted Petri firing")
        ports = {port.port_id: port for port in operation.spec.input_ports}
        embedded_inputs = tuple((
            item.claimed_token_ref,
            item.resource_ref,
            item.port_id,
            item.schema_ref,
            item.artifact,
        ) for item in operation.inputs)
        presented_inputs = tuple((
            item.token_ref,
            item.resource_ref,
            item.port.port_id,
            item.port.schema_ref,
            item.artifact,
        ) for item in claimed)
        if (embedded_inputs != presented_inputs
                or any(ports.get(item.port.port_id) != item.port
                       for item in claimed)):
            raise RegisteredOperationAuthorityRequiredError(
                "claimed inputs differ from the canonical execution closure")
        if not {item.token_ref for item in claimed}.issubset(
                set(firing.claimed_input_refs)):
            raise RegisteredOperationAuthorityRequiredError(
                "semantic Petri input is not among transition firing claims")
        for item in claimed:
            _validate_petri_input_artifact(item.artifact, label="claimed input")
        self._execution = execution
        self._executable_net = executable
        self._registry = registry
        self._resources = resources
        self._llm_input_port = llm_input_port
        self._registered_llm = registered_llm
        self._dispatch_permit: object | None = None
        self._resource_resume = None

    def _block_bottom_error(
            self, execution: "OperationExecutionAuthority", error: object,
            *, block_kind: str, boundary: str,
            agent_loop: object | None = None,
            idempotency_suffix: str,
    ) -> RegisteredOperationExecutionBlock:
        from cpn.rpnh.registry.models import VersionRef
        from cpn.rpnh.registry.resources import ResourceVersionRef

        exact_ref = None
        observed_authority = getattr(error, "observed_authority", None)
        for candidate in (
                getattr(error, "conflict_ref", None),
                getattr(error, "llm_call_ref", None),
                getattr(observed_authority, "response_resource_ref", None),
                execution.operation_execution_lease_ref):
            if isinstance(candidate, ResourceVersionRef):
                exact_ref = candidate.as_version_ref()
                break
            if isinstance(candidate, VersionRef):
                exact_ref = candidate
                break
        if exact_ref is None:
            raise RegisteredOperationAuthorityRequiredError(
                "operation block lacks exact bottom-error evidence")
        _LOGGER.error(
            "registered_operation_bottom_error transition=%s boundary=%s "
            "error_code=%s error_message=%s exact_error_ref=%s",
            execution.operation.firing.transition_id,
            boundary, type(error).__name__, str(error), exact_ref.version_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        del agent_loop, idempotency_suffix
        return self._block_condition(
            execution,
            block_kind=block_kind,
            error_code=type(error).__name__,
            error_message=str(error),
            boundary=boundary,
            exact_error_ref=exact_ref)

    def _block_condition(
            self, execution: "OperationExecutionAuthority", *,
            block_kind: str, error_code: str, error_message: str | None,
            boundary: str,
            exact_error_ref: "VersionRef",
    ) -> RegisteredOperationExecutionBlock:
        """Return one provisional block without fabricating a Petri outcome."""
        block = self._registry.register_operation_execution_block(
            execution,
            block_kind=block_kind,
            operation_or_tool_identity=(
                execution.operation.spec.operation_id),
            error_code=error_code,
            error_message=error_message,
            boundary=boundary,
            consecutive_count=1,
            exact_error_ref=exact_error_ref,
            retry_not_before_utc=None,
        )
        _LOGGER.warning(
            "registered_operation_block transition=%s kind=%s boundary=%s "
            "error_code=%s exact_error_ref=%s",
            execution.operation.firing.transition_id,
            block_kind, boundary, error_code, exact_error_ref.version_id)
        return RegisteredOperationExecutionBlock(execution, block)

    @property
    def permit(self) -> _PermittedRegisteredOperationDispatch:
        """Mint one process-local capability for the retained execution."""
        permit = object()
        self._dispatch_permit = permit
        return _PermittedRegisteredOperationDispatch(self, permit)

    def _dispatch_permitted(
        self, permit: object,
    ) -> (RegisteredOperationProducts | RegisteredOperationResourceWait
          | RegisteredOperationExecutionBlock
          | RegisteredOperationTerminalHandoff):
        """Dispatch through one local capability and the retained execution."""
        if permit is not self._dispatch_permit:
            raise RegisteredOperationAuthorityRequiredError(
                "registered operation dispatch requires one exact local permit")
        self._dispatch_permit = None
        execution = self._execution
        authority = execution.operation
        # Selection happens only after Registry has verified the operation spec
        # identity. The key is semantic data, never an import locator.
        implementation = _registered_implementation(
            authority.spec.operation_id, authority.spec.executor_key)
        if (implementation.operation_id != authority.spec.operation_id
                or implementation.executor_key != authority.spec.executor_key):
            raise RegisteredOperationAuthorityRequiredError(
                "operation implementation differs from its Registry authority")
        from cpn.rpnh.registry.operations import (
            registered_operation_executor,
        )
        executor = registered_operation_executor(authority.spec.executor_key)
        host_context = (
            _RegisteredLLMHostContext(self, self._registered_llm)
            if self._registered_llm is not None else self)
        try:
            result = executor(
                execution=execution, gateway=self._registry,
                resources=self._resources, host_context=host_context)
        except Exception as exc:
            from .registered_host_llm import RegisteredHostLLMCallBlocked
            if not isinstance(exc, RegisteredHostLLMCallBlocked):
                raise
            return self._block_condition(
                execution, block_kind=exc.block_kind,
                error_code=exc.error_code, error_message=None,
                boundary="registered_host_llm",
                exact_error_ref=exc.exact_error_ref)
        return self._accept_executor_result(execution, result)

    def install_resource_resume(self, execution, resume) -> None:
        """Retain the HOST owner's continuation for this exact execution."""
        if (execution is not self._execution or not callable(resume)
                or self._resource_resume is not None):
            raise RegisteredOperationAuthorityRequiredError(
                "HOST resource continuation differs from retained execution")
        self._resource_resume = resume

    def resume_resource_grant(self, exact_grant: object):
        """Delegate one exact grant to the registered HOST continuation."""
        if self._resource_resume is None:
            raise RegisteredOperationAuthorityRequiredError(
                "resource grant has no registered HOST continuation")
        return self._accept_executor_result(
            self._execution, self._resource_resume(exact_grant))

    def _accept_executor_result(self, execution, result):
        from cpn.rpnh.registry.operations import OperationExecutionResult

        if execution is not self._execution:
            raise RegisteredOperationAuthorityRequiredError(
                "HOST outcome differs from retained execution")
        if isinstance(result, OperationExecutionResult):
            return self._close_execution_result(execution, result)
        if isinstance(result, RegisteredOperationCompletion):
            if result.execution is not execution:
                raise RegisteredOperationAuthorityRequiredError(
                    "HOST completion differs from retained execution")
            return self._close_execution_result(
                execution, result.result,
                generic_critic_selection=result.generic_critic_selection,
                timing_observation=result.timing_observation,
                selected_outcome_id=result.selected_outcome_id)
        if (not isinstance(result, (
                RegisteredOperationResourceWait, RegisteredOperationExecutionBlock,
                RegisteredOperationTerminalHandoff))
                or result.execution is not execution):
            raise RegisteredOperationAuthorityRequiredError(
                "HOST executor returned no exact registered operation outcome")
        return result

    def _close_execution_result(
        self,
        authority: OperationExecutionAuthority,
        result: OperationExecutionResult,
        *,
        generic_critic_selection: GenericCriticSelectionAuthority | None = None,
        timing_observation: object | None = None,
        selected_outcome_id: str | None = None,
    ) -> (RegisteredOperationProducts
          | RegisteredOperationExecutionBlock):
        """Close a typed shell outcome only through the public Registry API."""

        from cpn.rpnh.registry.operations import (
            OperationExecutionAuthority,
            OperationExecutionResult,
            RegisteredOperationOutputsAuthority,
        )

        if (not isinstance(authority, OperationExecutionAuthority)
                or authority is not self._execution):
            raise RegisteredOperationAuthorityRequiredError(
                "operation closure requires the canonical execution")
        if not isinstance(result, OperationExecutionResult):
            raise RegisteredOperationAuthorityRequiredError(
                "exact shell returned no OperationExecutionResult")
        if (selected_outcome_id is not None and result.selected_outcome_id is not None
                and selected_outcome_id != result.selected_outcome_id):
            raise RegisteredOperationAuthorityRequiredError("completion and shell outcome identities differ")
        selected_outcome_id = selected_outcome_id or result.selected_outcome_id
        identity = (
            f"registered-operation:"
            f"{authority.operation.canonical.context.invocation_ref.version_id}:"
            f"{authority.operation.firing.transition_firing_ref.version_id}:"
            f"{authority.operation.spec.operation_spec_ref.version_id}"
        )
        try:
            outcome_arguments = (
                {} if selected_outcome_id is None
                else {"selected_outcome_id": selected_outcome_id})
            closed_outputs = self._registry.register_operation_outputs(
                authority,
                result.outputs,
                idempotency_key=f"{identity}:outputs",
                **outcome_arguments,
            )
        except Exception as exc:
            # Preserve strict Registry/parser rejection as its existing typed
            # block authority; output rejection is never operation success.
            return self._block_bottom_error(
                authority, exc, block_kind="framework_repair",
                boundary="operation_output_closure",
                idempotency_suffix="operation-output-closure")
        if (not isinstance(
                closed_outputs, RegisteredOperationOutputsAuthority)
                or closed_outputs.execution is not authority
                or (selected_outcome_id is not None
                    and closed_outputs.selected_outcome_id != selected_outcome_id)):
            raise RegisteredOperationAuthorityRequiredError(
                "Registry output closure differs from operation authority")
        completion_event = self._registry.record_registered_operation_completion(
            closed_outputs,
            idempotency_key=f"{identity}:completion-recorded",
        )
        if (getattr(completion_event, "event_type", None)
                != "registered_operation_completion_recorded/v1"
                or getattr(completion_event, "payload", {}).get(
                    "operation_execution_lease_ref") != {
                        "entity_type": "operation_execution_lease/v1",
                        "logical_id": str(
                            authority.operation_execution_lease_ref.entity_id),
                        "version_id": str(
                            authority.operation_execution_lease_ref.version_id),
                    }):
            raise RegisteredOperationAuthorityRequiredError(
                "Registry completion record differs from operation authority")
        records = tuple(
            RegisteredOperationOutputRecord(
                place=output.place,
                output_resource_ref=output.resource_ref,
                work_resource_ref=output.work_resource_ref,
                kind=output.kind,
                verdict=output.verdict,
                continuation=output.continuation,
            )
            for output in closed_outputs.outputs
        )
        products = RegisteredOperationProducts(
            closed_outputs, records, generic_critic_selection,
            timing_observation=timing_observation)
        try:
            origin = getattr(timing_observation, "timing_origin_ns", None)
            if (not isinstance(origin, bool) and isinstance(origin, int)
                    and origin >= 0):
                offset = time.monotonic_ns() - origin
                if offset >= 0:
                    timing_observation.operation_finish_return = offset
        except Exception:
            pass
        return products


__all__ = [
    "ClaimedPetriInputAuthority",
    "RegisteredOperationAuthorityRequiredError",
    "RegisteredOperationContractUnavailableError",
    "RegisteredOperationCompletion",
    "RegisteredOperationContext",
    "RegisteredOperationDispatcher",
    "RegisteredOperationExecutionBlock",
    "RegisteredOperationImplementation",
    "RegisteredOperationOutputRecord",
    "RegisteredOperationProducts",
    "RegisteredOperationResourceWait",
    "RegisteredOperationTerminalHandoff",
    "supported_registered_operation_ids",
]
