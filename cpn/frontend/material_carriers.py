"""Explicit Viewer observation of original literal-material carrier proofs.

The capability catalog only resolves exact metadata. Archive provenance is
read only after the separate HOST gate and current Owner session are checked
for an explicit inspection. No command, grant, body delivery or cleanup is
created, and the original inspector remains the carrier-proof authority.
"""
from __future__ import annotations

from copy import deepcopy
import sqlite3

from cpn.rpnh.registry.event_store import RegistryConflict, RegistryCorruptError
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.registry._event_store.collaboration_descriptors import (
    exact_descriptor, exact_prepared,
)
from cpn.rpnh.registry._event_store.material_control import (
    TEXT_SCHEMA, reference, require_session,
)
from .material_management import MaterialManagementError, MAX_REQUEST_BYTES

SCHEMA = "rpnh/viewer_material_carriers/v1"
CONTENT = "metadata_and_proof_refs_only"
_PROOF_ERRORS = (RegistryConflict, RegistryCorruptError, ResourceIntegrityFault,
    ObjectIntegrityError, SchemaGovernanceError, sqlite3.DatabaseError,
    KeyError, TypeError, ValueError, OSError)


def closed_carrier_catalog(source_id, *, reason="management_not_configured"):
    return {"schema_version": SCHEMA, "action": "carrier_catalog", "source_id": source_id,
        "supported": False, "inspection_allowed": False, "reason": reason,
        "resource_ref": None, "selection_status": "not_selected", "as_of_ordinal": None,
        "logical_availability": None, "response_content": CONTENT}


class CarrierRequests:
    def __init__(self, viewer):
        self.viewer = viewer
        self.core = viewer._owner._core
        self.store = self.core.event_store
        self.task_ref = ref_payload(viewer._owner.identity.task_ref)

    @staticmethod
    def _upper(db):
        return int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])

    def _session(self, db):
        try:
            require_session(self.store, db, self.core.task_id, self.viewer._session)
        except RegistryConflict as exc:
            if (str(exc).startswith("ACTOR_NOT_AUTHORIZED") or str(exc) in {
                    "collaboration requires the target owner A",
                    "collaboration owner authority is revoked"}):
                raise MaterialManagementError("access_changed", 403) from exc
            raise

    def _selected(self, db, selected):
        selected = reference(selected, "resource_version/v1")
        try:
            prepared = exact_prepared(db, self.core.object_store, self.core.task_id, selected)
        except RegistryConflict as exc:
            raise MaterialManagementError("target_not_allowed", 403) from exc
        if prepared.metadata.get("task_ref") != self.task_ref:
            raise MaterialManagementError("target_not_allowed", 403)
        return prepared

    @staticmethod
    def _candidate(prepared):
        """Only canonical metadata, never a literal/archive proof or body gate."""
        metadata = prepared.metadata
        producer = metadata.get("producer_ref")
        return (metadata.get("content_schema_ref") == TEXT_SCHEMA
            and prepared.media_type == "application/json"
            and metadata.get("origin_kind") == "petri_output"
            and prepared.producer_invocation_id is not None
            and isinstance(producer, dict) and set(producer) == {"entity_type", "logical_id", "version_id"}
            and producer["entity_type"] == "invocation/v1"
            and producer["logical_id"] == str(prepared.producer_invocation_id)
            and metadata.get("descriptors", {}).get("owner_input_role") is None)

    def _catalog(self, db, selected):
        result = closed_carrier_catalog(self.viewer.source_id)
        result.update(supported=True, reason="selection_required", as_of_ordinal=self._upper(db))
        if selected is None:
            return result
        prepared = self._selected(db, selected)
        candidate = self._candidate(prepared)
        result.update(resource_ref=selected, inspection_allowed=candidate,
            selection_status="candidate" if candidate else "unsupported",
            reason=None if candidate else "selection_not_supported")
        return result

    def _command(self, db, prepared):
        """Resolve through the original producer/admission, never an index guess."""
        from cpn.rpnh.material_execution import is_literal_producer_spec, MATERIAL_EXECUTOR
        from cpn.rpnh.terminal_management import TERMINAL_LITERAL_EXECUTOR
        from cpn.rpnh.registry._event_store.material_settlement import material_admission

        producer = reference(prepared.metadata["producer_ref"], "invocation/v1")
        invocation = exact_descriptor(db, self.core.object_store, self.core.task_id, producer)
        if (invocation["invocation_ref"] != producer or invocation["task_ref"] != self.task_ref
                or producer["logical_id"] != str(prepared.producer_invocation_id)):
            raise RegistryConflict("carrier selection differs from its original invocation")
        binding_ref = invocation["operation_binding_ref"]
        binding = exact_descriptor(db, self.core.object_store, self.core.task_id, binding_ref)
        spec = exact_descriptor(db, self.core.object_store, self.core.task_id, binding["operation_spec_ref"])
        if not is_literal_producer_spec(spec):
            if spec.get("executor_key") in {MATERIAL_EXECUTOR, TERMINAL_LITERAL_EXECUTOR}:
                raise RegistryConflict("carrier literal producer contract is incomplete")
            raise MaterialManagementError("carrier_contract_not_supported", 409)
        firing_ref = reference(invocation["own_transition_firing_ref"], "transition_firing/v1")
        firing = exact_descriptor(db, self.core.object_store, self.core.task_id, firing_ref)
        found = material_admission(self.store, db, self.core.task_id, firing_ref, current=False)
        if found is None:
            raise RegistryConflict("carrier selection lacks its original material command")
        admission, command = found
        if (admission["invocation_ref"] != producer
                or admission["transition_firing_ref"] != firing_ref
                or firing["transition_firing_ref"] != firing_ref
                or firing["operation_binding_ref"] != binding_ref
                or firing["task_ref"] != self.task_ref
                or command["data"]["execution"]["task_ref"] != self.task_ref
                or command["data"]["operation"] not in {"create_text", "replace_text"}):
            raise RegistryConflict("carrier selection differs from its original literal command")
        return admission["owner_command_ref"]

    def _inspect(self, db, selected, observe_payloads):
        from cpn.rpnh.material_carriers import inspect_material_carriers_at
        from cpn.rpnh.registry._event_store.material_availability import resolve_availability

        if type(observe_payloads) is not bool:
            raise MaterialManagementError("invalid_query")
        prepared = self._selected(db, selected)
        if not self._candidate(prepared):
            raise MaterialManagementError("selection_not_supported", 403)
        command_ref = self._command(db, prepared)
        inspection = inspect_material_carriers_at(self.store, db, self.core.task_id,
            material_command_ref=command_ref, observe_payloads=observe_payloads)
        semantic = [entry for entry in inspection["carriers"] if entry["role"] == "semantic_output"]
        if (inspection["task_ref"] != self.task_ref or inspection["command_ref"] != command_ref
                or inspection["output_ref"] != selected or len(semantic) != 1
                or semantic[0]["carrier_ref"] != selected):
            raise RegistryConflict("carrier proof does not name the exact selected semantic output")
        availability = resolve_availability(self.store, db, self.core.task_id, selected)
        upper = self._upper(db)
        if (inspection["observation_ordinal"] != upper or availability["availability_as_of"] != upper):
            raise RegistryConflict("carrier observations differ from the original read cut")
        return {"schema_version": SCHEMA, "action": "read_material_carriers",
            "source_id": self.viewer.source_id, "resource_ref": selected,
            "observe_payloads": observe_payloads, "as_of_ordinal": upper,
            "logical_availability": availability, "inspection": inspection,
            "response_content": CONTENT}

    def dispatch(self, params):
        if params["source_id"] != self.viewer.source_id:
            raise MaterialManagementError("source_not_allowed", 403)
        if not self.viewer._carrier_inspection:
            if params["action"] == "carrier_catalog":
                return closed_carrier_catalog(self.viewer.source_id, reason="action_unavailable")
            raise MaterialManagementError("action_unavailable", 403)
        try:
            with self.store.connect() as db:
                db.execute("BEGIN")
                self._session(db)
                if params["action"] == "carrier_catalog":
                    result = self._catalog(db, params.get("resource_ref"))
                elif params["action"] == "read_material_carriers":
                    result = self._inspect(db, params["resource_ref"], params["observe_payloads"])
                else:
                    raise MaterialManagementError("invalid_query")
        except MaterialManagementError:
            raise
        except _PROOF_ERRORS as exc:
            raise MaterialManagementError("carrier_proof_unavailable", 503) from exc
        if len(canonical_json(result)) > MAX_REQUEST_BYTES:
            raise MaterialManagementError("scope_limit", 413)
        return deepcopy(result)
