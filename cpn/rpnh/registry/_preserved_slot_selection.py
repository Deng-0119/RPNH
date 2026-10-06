"""Explicit static slot compatibility against one exact historical basis.

The candidate is mechanically checked data, not authenticated HOST output.
Nothing here enables a candidate producer or grants publication/execution rights.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from ..executable_net import CompiledPetriNet, _load_compiled_net_offline
from ._candidate_plan_reads import same
from ._event_store.collaboration_descriptors import readable_payload
from ._module_resource_projection import _copy_compiler_data
from ._preserved_basis_reads import _BasisReadContext, _basis_read_context
from ._preserved_resource_reads import _resource_plan_at
from .content_schemas import _validate_schema_bytes
from .event_store import RegistryConflict
from .models import VersionRef
from .module_resources import compatible_slot_symbols
from .preserved_binding_contracts import PreservedBasis, _copy_id
from .publication import _resource_from_payload, _version_from_payload
from .resources import ResourceVersionRef
from .runtime_binding_contracts import freeze_candidate_document
from .schema_catalog import canonical_json
from .strict_contracts import content_schema_ref_payload, ref_payload


@dataclass(frozen=True, slots=True)
class _SlotSelectionRead:
    """Detached input/selection/evidence snapshot; not a persisted plan."""
    _document_json: str

    @property
    def document(self):
        return json.loads(self._document_json)

    @property
    def basis(self):
        value = self.document["preserved_basis"]
        return None if value is None else PreservedBasis.from_document(value)

    @property
    def selected_slot_refs(self):
        return {key: _version_from_payload(value) for key, value in self.document["selected_slot_refs"].items()}

    @property
    def selected_schema_refs(self):
        return {key: _resource_from_payload(value) for key, value in self.document["selected_schema_refs"].items()}

    @property
    def dependency_evidence(self):
        return self.document["dependency_evidence"]


def _candidate_data(compiled):
    if type(compiled) is not CompiledPetriNet:
        raise TypeError("slot selection requires standard compiled candidate data")
    copied = _copy_compiler_data(compiled)
    for entry in copied.registrations["schema"].values():
        _validate_schema_bytes(canonical_json(entry["schema"]))
    document = json.loads(freeze_candidate_document(copied.to_dict()))
    loaded = _load_compiled_net_offline(document)
    # to_dict derives ports/operations/handles. Do not silently discard supplied
    # derived fields that disagree with the mechanically checked wire inventory.
    if not same(asdict(copied), asdict(loaded)):
        raise RegistryConflict("candidate compiled fields differ from its exact wire inventory")
    return loaded, document


def _selections(slots, schemas):
    if type(slots) is not dict or type(schemas) is not dict:
        raise TypeError("slot selection maps must be builtin dictionaries")
    if any(type(key) is not str for mapping in (slots, schemas) for key in mapping):
        raise TypeError("slot selection keys must be builtin strings")
    copied_slots, copied_schemas = {}, {}
    for name, ref in slots.items():
        if (type(ref) is not VersionRef or type(ref.entity_type) is not str
                or ref.entity_type != "logical_artifact_slot/v1"):
            raise TypeError("slot selection requires an exact logical slot VersionRef")
        copied_slots[name] = VersionRef(ref.entity_type, _copy_id(ref.entity_id, "logical_slot"),
            _copy_id(ref.version_id, "logical_slot_version"))
    if len(set(copied_slots.values())) != len(copied_slots):
        raise RegistryConflict("selected slot symbols cannot merge exact identities")
    for name, ref in schemas.items():
        if type(ref) is not ResourceVersionRef:
            raise TypeError("selected schema requires a standard ResourceVersionRef")
        copied_schemas[name] = ResourceVersionRef(_copy_id(ref.resource_id, "resource"),
            _copy_id(ref.resource_version_id, "resource_version"))
    return copied_slots, copied_schemas


def read_preserved_slot_selection(core, candidate_compiled, *, preserved_basis,
        selected_slot_refs, selected_schema_refs, _db=None):
    """Check an explicit subset of the basis's current same-symbol slots.

    Schema refs are explicit inputs and must equal each selected current output's
    exact schema resource. Shared schema IDs must therefore agree on one ref.
    An empty selection requires no basis/schema refs and makes no Registry claim.
    A nonempty selection returns same-cut canonical dependency evidence. A future
    producer must freeze/use these inputs and separately prove author/HOST origin.
    """
    slots, schemas = _selections(selected_slot_refs, selected_schema_refs)
    candidate, document = _candidate_data(candidate_compiled)
    result = {"candidate_document": document, "selected_slot_refs": {name: ref_payload(ref) for name, ref in slots.items()},
        "selected_schema_refs": {name: content_schema_ref_payload(ref) for name, ref in schemas.items()},
        "preserved_basis": None, "dependency_evidence": []}
    if not slots:
        if preserved_basis is not None or schemas:
            raise ValueError("empty slot selection requires no basis or selected schemas")
        return _SlotSelectionRead(freeze_candidate_document(result))
    declarations = {slot.name: slot for slot in candidate.symbolic.logical_slots}
    if (len(declarations) != len(candidate.symbolic.logical_slots) or not set(slots) <= set(declarations)
            or set(schemas) != {declarations[name].schema for name in slots}):
        raise RegistryConflict("selected symbols/schemas differ from the candidate slot inventory")
    with _basis_read_context(core, preserved_basis, _db=_db) as context:
        _check_slot_selection_at(context, candidate, slots, schemas, declarations)
        result["preserved_basis"] = context.basis.to_dict()
        result["dependency_evidence"] = context.reads.finish_dependencies()
    return _SlotSelectionRead(freeze_candidate_document(result))


def _check_slot_selection_at(context, candidate, slots, schemas, declarations):
    """Fixed selection kernel over already-normalized candidate and input data.

    The caller owns evidence finalization so a candidate collector can share
    one RecordingStore with its author/business dependency reads.
    """
    if type(context) is not _BasisReadContext:
        raise TypeError("slot selection requires its fixed basis read context")
    plan = _resource_plan_at(context)
    basis_compiled = context.reads.compiled(_resource_from_payload(
        context.net["team_net_declaration_resource_ref"]).as_version_ref())
    compatible = compatible_slot_symbols(basis_compiled, candidate)
    current = context.net["module_resource_bindings"]["slot_bindings"]
    for name, ref in slots.items():
        if name not in compatible or plan.slot_refs.get(name) != ref:
            raise RegistryConflict("selected slot differs from exact compatible basis symbol")
        output = context.reads.metadata(_version_from_payload(current[name]["producer_output_binding_ref"]), "output_binding/v1")
        schema_id = declarations[name].schema
        schema_ref = schemas[schema_id]
        if not same(content_schema_ref_payload(schema_ref), output["content_schema_ref"]):
            raise RegistryConflict("selected schema differs from exact basis output authority")
        prepared = context.reads.prepared(schema_ref.as_version_ref(), "resource_version/v1")
        actual_id, schema_document = _validate_schema_bytes(readable_payload(
            context.reads.object_store, prepared, media_type="application/schema+json"))
        if actual_id != schema_id or not same(schema_document, candidate.registrations["schema"][schema_id]["schema"]):
            raise RegistryConflict("selected schema bytes differ from candidate registration")
