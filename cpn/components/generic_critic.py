"""Public immutable DTOs for the Registry-backed invariant critic chain."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

from cpn.components import generic_a2c as _generic_a2c
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.selection import OutputSelectionWitness
from cpn.rpnh.registry.operations import OperationExecutionAuthority
from cpn.rpnh.registry.resources import ResourceVersionRef, VerifiedResourceArtifact


ActivationAdmission = _generic_a2c.ActivationAdmission
CriticInvocation = _generic_a2c.CriticInvocation
ExactRef = _generic_a2c.ExactRef
ValidatedSelection = _generic_a2c.ValidatedSelection
ProtocolViolation = _generic_a2c.ProtocolViolation
A2C_VERDICTS = _generic_a2c.A2C_VERDICTS
DESIGN_CRITIC_VERDICTS = _generic_a2c.DESIGN_CRITIC_VERDICTS


class CriticVerdictFormatError(ValueError):
    """A substantive critique was authored but its verdict needs correction."""

    def __init__(self, critique: str, message: str) -> None:
        self.critique = critique
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DesignCriticDecision:
    """LLM-authored design critique and one literal closed verdict."""

    critique: str
    verdict: str

    def __post_init__(self) -> None:
        if not isinstance(self.critique, str):
            raise TypeError("design critic critique must be text when present")
        if self.verdict not in DESIGN_CRITIC_VERDICTS:
            raise ValueError(
                "design critic verdict must be exactly adopt, rework, or reject")


def parse_design_critic_llm_content(content: str) -> DesignCriticDecision:
    """Decode the declared critique/verdict interface without prose matching."""

    if not isinstance(content, str) or not content:
        raise ValueError("design critic LLM content must not be empty")
    try:
        fields = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("design critic response is not JSON") from exc
    if not isinstance(fields, Mapping) or "verdict" not in fields:
        raise ValueError("design critic response must contain one verdict")
    critique = fields.get("critique")
    verdict = fields["verdict"]
    return DesignCriticDecision(
        critique=(critique if isinstance(critique, str) else content),
        verdict=verdict)


def exact_ref(ref: VersionRef) -> ExactRef:
    """Convert one Registry exact ref without weakening any identity field."""

    if not isinstance(ref, VersionRef):
        raise TypeError("generic critic requires an exact VersionRef")
    return ExactRef(ref.entity_type, str(ref.entity_id), str(ref.version_id))


def version_ref(ref: ExactRef) -> VersionRef:
    """Convert one protocol exact ref back to the Registry DTO."""

    from cpn.rpnh.registry.identities import TypedId

    if not isinstance(ref, ExactRef):
        raise TypeError("generic critic requires an ExactRef")
    return VersionRef(
        ref.entity_type,
        TypedId.parse(ref.logical_id),
        TypedId.parse(ref.version_id),
    )


def declared_selected_output_colour(
        net: object, selected_output_binding_ref: VersionRef, *,
        output_place: str,
        registered_outcome_id: str | None = None,
) -> str | None:
    """Resolve one selected binding's explicit Petri colour declaration.

    Uncoloured selection places use the registered output-port
    identity directly.  A coloured place is resolved from the direct Petri
    guard declared for either the exact binding or its Registry-registered
    outcome port.  This is the common rule for fixed and structurally grown
    critics; transition names and lifecycle phases are not route authority.
    """

    if (not isinstance(selected_output_binding_ref, VersionRef)
            or selected_output_binding_ref.entity_type != "output_binding/v1"
            or not isinstance(output_place, str) or not output_place):
        raise ProtocolViolation(
            "selected output colour requires one exact binding and place")
    transitions = getattr(net, "transitions", None)
    place_color_sets = getattr(net, "place_color_sets", None)
    verdict_guards_of = getattr(net, "verdict_guards_of", None)
    input_arc_weight = getattr(net, "input_arc_weight", None)
    if (not isinstance(transitions, Mapping)
            or not isinstance(place_color_sets, Mapping)
            or not callable(verdict_guards_of)
            or not callable(input_arc_weight)):
        raise ProtocolViolation(
            "selected output colour requires one loaded exact TeamNet")
    selection_domain = tuple(place_color_sets.get(output_place, ()))
    matches: list[tuple[str, Mapping[str, object], str, VersionRef | None]] = []
    for transition_id, transition in transitions.items():
        config = getattr(transition, "config", None)
        guards = tuple(verdict_guards_of(str(transition_id)))
        if (not isinstance(config, Mapping)
                or not any(place == output_place for place, _colour in guards)):
            continue
        raw_ref = config.get("output_binding_ref")
        declared_ref: VersionRef | None = None
        if raw_ref is not None:
            if (not isinstance(raw_ref, Mapping)
                    or set(raw_ref) != {
                        "entity_type", "logical_id", "version_id"}):
                raise ProtocolViolation(
                    "direct action route output binding ref is malformed")
            try:
                declared_ref = version_ref(ExactRef(
                    entity_type=str(raw_ref["entity_type"]),
                    logical_id=str(raw_ref["logical_id"]),
                    version_id=str(raw_ref["version_id"]),
                ))
            except Exception as exc:
                raise ProtocolViolation(
                    "direct action route output binding ref is malformed") from exc
        exact_binding_match = declared_ref == selected_output_binding_ref
        registered_outcome_match = (
            registered_outcome_id is not None
            and config.get("declared_outcome_id") == registered_outcome_id)
        if declared_ref is not None:
            if not exact_binding_match:
                continue
        elif not registered_outcome_match:
            continue
        if (len(guards) != 1
                or guards[0][0] != output_place
                or not isinstance(guards[0][1], str)
                or not guards[0][1]
                or input_arc_weight(output_place, str(transition_id)) != 1):
            raise ProtocolViolation(
                "selected binding differs from its direct Petri guard")
        colour = guards[0][1]
        configured_colour = config.get("outcome_color")
        if configured_colour is not None and configured_colour != colour:
            raise ProtocolViolation(
                "selected binding route colour differs from its Petri guard")
        matches.append((
            str(transition_id), config, colour, declared_ref))
    if not selection_domain:
        if registered_outcome_id is None:
            if matches:
                raise ProtocolViolation(
                    "uncoloured selected output requires its registered outcome")
            return None
        if registered_outcome_id not in A2C_VERDICTS:
            raise ProtocolViolation(
                "uncoloured selected output has no registered A2C outcome")
        if not matches:
            raise ProtocolViolation(
                "uncoloured selected output lacks its direct outcome guard")
        route_colours = {
            colour for _transition_id, _config, colour, _ref in matches}
        if route_colours != {registered_outcome_id}:
            raise ProtocolViolation(
                "uncoloured selected output differs from its direct outcome guard")
        return registered_outcome_id
    if not matches:
        raise ProtocolViolation(
            "coloured selected output lacks one exact binding colour declaration")
    colours = {colour for _transition_id, _config, colour, _ref in matches}
    if (len(colours) != 1
            or not isinstance(next(iter(colours)), str)
            or not next(iter(colours))
            or next(iter(colours)) != next(iter(colours)).strip()):
        raise ProtocolViolation(
            "selected binding direct routes disagree on their outcome colour")
    colour = next(iter(colours))
    if colour not in selection_domain:
        raise ProtocolViolation(
            "selected binding colour differs from its direct route guards")
    for transition_id, transition in transitions.items():
        guards = tuple(verdict_guards_of(str(transition_id)))
        if guards != ((output_place, colour),):
            continue
        config = getattr(transition, "config", None)
        raw_ref = (config.get("output_binding_ref")
                   if isinstance(config, Mapping) else None)
        if raw_ref is None:
            continue
        try:
            sibling_ref = version_ref(ExactRef(
                entity_type=str(raw_ref["entity_type"]),
                logical_id=str(raw_ref["logical_id"]),
                version_id=str(raw_ref["version_id"]),
            ))
        except Exception as exc:
            raise ProtocolViolation(
                "direct action route output binding ref is malformed") from exc
        if (sibling_ref != selected_output_binding_ref
                or input_arc_weight(output_place, str(transition_id)) != 1):
            raise ProtocolViolation(
                "selected colour is shared by a different direct binding")
    return colour


@dataclass(frozen=True, slots=True)
class GenericCriticActivationAuthority:
    """Admitted activation plus the exact schema-bound prompt envelope."""

    execution: OperationExecutionAuthority
    admission: ActivationAdmission
    prompt_resource_ref: ResourceVersionRef
    prompt_artifact: VerifiedResourceArtifact

    def __post_init__(self) -> None:
        if not isinstance(self.execution, OperationExecutionAuthority):
            raise TypeError("critic activation requires operation execution authority")
        if not isinstance(self.admission, ActivationAdmission):
            raise TypeError("critic activation requires typed admission")
        if not isinstance(self.prompt_resource_ref, ResourceVersionRef):
            raise TypeError("critic activation requires exact prompt resource ref")
        if not isinstance(self.prompt_artifact, VerifiedResourceArtifact):
            raise TypeError("critic activation requires verified prompt artifact")
        if self.prompt_artifact.header.ref != self.prompt_resource_ref:
            raise ValueError("critic prompt artifact differs from its exact ref")


@dataclass(frozen=True, slots=True)
class GenericCriticInvocationAuthority:
    """One persisted invariant-critic invocation and its expected evidence ref."""

    activation: GenericCriticActivationAuthority
    invocation: CriticInvocation
    actor_candidate_ref: ResourceVersionRef
    llm_prompt_ref: ResourceVersionRef
    critic_evidence_ref: VersionRef

    def __post_init__(self) -> None:
        if not isinstance(self.activation, GenericCriticActivationAuthority):
            raise TypeError("critic invocation requires admitted activation")
        if not isinstance(self.invocation, CriticInvocation):
            raise TypeError("critic invocation requires typed protocol invocation")
        if not isinstance(self.actor_candidate_ref, ResourceVersionRef):
            raise TypeError("critic invocation requires exact actor candidate")
        if not isinstance(self.llm_prompt_ref, ResourceVersionRef):
            raise TypeError("critic invocation requires exact LLM prompt")
        if (not isinstance(self.critic_evidence_ref, VersionRef)
                or self.critic_evidence_ref.entity_type
                != "generic_critic_selection_authority/v1"):
            raise TypeError(
                "critic invocation requires exact typed selection authority ref")
        if self.invocation.activation_ref != self.activation.admission.activation_ref:
            raise ValueError("critic invocation differs from admitted activation")

    @property
    def generic_critic_invocation_ref(self) -> VersionRef:
        return version_ref(self.invocation.generic_critic_invocation_ref)


@dataclass(frozen=True, slots=True)
class GenericCriticDecision:
    """LLM-authored critique and its one exact routing decision."""

    critique: str
    selected_output_binding_refs: tuple[VersionRef, ...]
    llm_response_ref: ResourceVersionRef | None = None
    critique_response_ref: ResourceVersionRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.critique, str):
            raise TypeError("critic decision critique must be text when present")
        values = tuple(self.selected_output_binding_refs)
        if any(not isinstance(ref, VersionRef)
               or ref.entity_type != "output_binding/v1" for ref in values):
            raise TypeError("critic decision requires output_binding/v1 refs")
        if len(set(values)) != len(values):
            raise ValueError("critic decision repeats an exact output binding")
        object.__setattr__(self, "selected_output_binding_refs", values)
        if (self.llm_response_ref is not None
                and not isinstance(
                    self.llm_response_ref, ResourceVersionRef)):
            raise TypeError("critic decision response must be an exact resource ref")
        if (self.critique_response_ref is not None
                and (not isinstance(self.critique_response_ref, ResourceVersionRef)
                     or self.llm_response_ref == self.critique_response_ref)):
            raise TypeError(
                "critic correction requires a distinct exact critique response ref")


def parse_generic_critic_llm_content(
        content: str, invocation: CriticInvocation) -> GenericCriticDecision:
    """Decode one critique plus one literal verdict without synonyms/defaults."""

    if not isinstance(content, str) or not content.strip():
        raise ValueError("generic critic LLM content must not be empty")

    try:
        pairs = json.loads(content, object_pairs_hook=lambda value: value)
    except json.JSONDecodeError as exc:
        raise ValueError("generic critic response is not JSON") from exc
    if (not isinstance(pairs, list)
            or any(not isinstance(item, tuple) or len(item) != 2
                   for item in pairs)):
        raise ValueError("generic critic response root is not an object")
    critiques = [
        value for name, value in pairs
        if name == "critique" and isinstance(value, str)]
    critique = critiques[-1] if critiques else content
    verdicts = [value for name, value in pairs if name == "verdict"]
    if len(verdicts) != 1:
        raise CriticVerdictFormatError(
            critique, "critic response has a missing or multiple verdict")
    verdict = verdicts[0]
    if not isinstance(verdict, str) or verdict not in A2C_VERDICTS:
        raise CriticVerdictFormatError(
            critique,
            "critic verdict must be exactly continue, pass, escalate, or give_up")
    try:
        selected = version_ref(
            _generic_a2c.offered_binding_for_verdict(invocation, verdict))
    except Exception as exc:
        raise ValueError("generic critic verdict has no exact offered binding") from exc
    return GenericCriticDecision(
        critique=critique,
        selected_output_binding_refs=(selected,),
    )


def parse_generic_critic_verdict_correction(
        content: str, invocation: CriticInvocation, *, critique: str,
        critique_response_ref: ResourceVersionRef,
        correction_response_ref: ResourceVersionRef,
) -> GenericCriticDecision:
    """Parse the bounded verdict-only correction; never merge response bodies."""

    if not isinstance(content, str) or not content.strip():
        raise ValueError("generic critic verdict correction must not be empty")
    try:
        pairs = json.loads(content, object_pairs_hook=lambda value: value)
    except json.JSONDecodeError as exc:
        raise ValueError("generic critic verdict correction is not JSON") from exc
    if (not isinstance(pairs, list)
            or any(not isinstance(item, tuple) or len(item) != 2
                   for item in pairs)):
        raise ValueError("generic critic correction root is not an object")
    verdicts = [value for name, value in pairs if name == "verdict"]
    if len(verdicts) != 1:
        raise ValueError(
            "generic critic correction has a missing or multiple verdict")
    verdict = verdicts[0]
    if not isinstance(verdict, str) or verdict not in A2C_VERDICTS:
        raise ValueError(
            "corrected verdict must be exactly continue, pass, escalate, or give_up")
    selected = version_ref(
        _generic_a2c.offered_binding_for_verdict(invocation, verdict))
    return GenericCriticDecision(
        critique=critique,
        selected_output_binding_refs=(selected,),
        llm_response_ref=correction_response_ref,
        critique_response_ref=critique_response_ref,
    )


@dataclass(frozen=True, slots=True)
class GenericCriticSelectionAuthority:
    """Registry-validated selection and its immutable evidence object."""

    invocation_authority: GenericCriticInvocationAuthority
    selection: ValidatedSelection
    critic_evidence_ref: VersionRef

    def __post_init__(self) -> None:
        if not isinstance(
                self.invocation_authority, GenericCriticInvocationAuthority):
            raise TypeError("critic selection requires invocation authority")
        if not isinstance(self.selection, ValidatedSelection):
            raise TypeError("critic selection requires validated protocol selection")
        if self.critic_evidence_ref != self.invocation_authority.critic_evidence_ref:
            raise ValueError("critic selection evidence differs from invocation")
        if version_ref(self.selection.critic_evidence_ref) != self.critic_evidence_ref:
            raise ValueError("critic selection carries another evidence ref")

    @property
    def generic_critic_invocation_ref(self) -> VersionRef:
        return version_ref(self.selection.generic_critic_invocation_ref)

    @property
    def selected_output_binding_ref(self) -> VersionRef:
        return version_ref(self.selection.selected_output_binding_ref)

    @property
    def registered_selection_witness(self) -> OutputSelectionWitness:
        return OutputSelectionWitness(
            self.generic_critic_invocation_ref, self.critic_evidence_ref,
            self.selected_output_binding_ref)


__all__ = [
    "DesignCriticDecision",
    "GenericCriticActivationAuthority",
    "GenericCriticDecision",
    "GenericCriticInvocationAuthority",
    "GenericCriticSelectionAuthority",
    "CriticVerdictFormatError",
    "declared_selected_output_colour",
    "parse_design_critic_llm_content",
    "parse_generic_critic_llm_content",
    "parse_generic_critic_verdict_correction",
]
