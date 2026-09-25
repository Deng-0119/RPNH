"""Exact data witness for an optional registered output-selection effect.

This carrier does not validate an operation, publish evidence, transfer claims
or settle a firing. Registry still verifies the actual immutable contract and
objects when accepting the effect. Optional components validate their own
semantic selection before producing this mechanical association.
"""
from dataclasses import dataclass
from .models import VersionRef


@dataclass(frozen=True, slots=True)
class OutputSelectionWitness:
    invocation_ref: VersionRef
    evidence_ref: VersionRef
    output_binding_ref: VersionRef

    def __post_init__(self):
        if not all(isinstance(ref, VersionRef) for ref in (
                self.invocation_ref, self.evidence_ref, self.output_binding_ref)):
            raise TypeError("output selection association requires exact typed refs")
        if self.output_binding_ref.entity_type != "output_binding/v1":
            raise ValueError("selection target must be a registered output binding")
