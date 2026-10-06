"""Strong explicit closure of one saved open root for a prospective member."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json

from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.schema_catalog import canonical_text
from .authoring import NetRevision
from .assemblies import _identity, _MEMBER, _ELEMENT, _command as _check_command
from .assembly_v2 import _binding_at, _host_at, _material_ref
from .materials import (ClosedModuleAuthor, ValidatedClosedRevision, MODULE_SCHEMA, ELEMENT_SCHEMA,
    BOUNDARY_SCHEMA, HOST_SCHEMA, _material, _same_json, validate_closed_revision)
from .open_region import (PREFIX, SOURCE_MATERIALS, _key, _result_ref, _obj, _resource, _owner, _producer_at,
    _source_at, _prepare_specs, _command_document, _read_command_at, _check_prepared_at,
    _open_at, _authorities_at, _publish_command)
from ._open_region_inventory import signature
from ._open_region_closure import ADAPTER, RESOLVER, reconstruct

INTENT_SCHEMA = PREFIX + "prospective_member_intent/v1"
ORIGIN_SCHEMA = PREFIX + "open_region_origin_map/v1"
ADAPTATION_SCHEMA = PREFIX + "open_region_boundary_adaptation/v1"
CLOSURE_COMMAND_SCHEMA = PREFIX + "open_region_closure_author_command/v1"
CLOSURE_MARKER = "open_region_closure_author_command_v1"
CLOSURE_MATERIALS = (("intent", INTENT_SCHEMA), ("origin_map", ORIGIN_SCHEMA), ("adaptation", ADAPTATION_SCHEMA),
                    *SOURCE_MATERIALS)


def _intent_at(db, core, binding, request, registration, visiting):
    intent = request["intent"]
    core.catalog.validate_schema_ref(INTENT_SCHEMA, intent)
    if (intent["source_id"] != binding["source_id"] or intent["owner_task_ref"] != _owner(core, binding).to_dict()
            or intent["assembly_protocol"] != "collaboration_assembly_revision/v4"
            or intent["resolver_contract"] != RESOLVER):
        raise ValueError("target_mismatch: prospective intent requires this exact source/owner/v4 resolver")
    _check_command(intent["assembly_command_id"])
    _identity(intent["member_id"], _MEMBER, "prospective member")
    if intent["parent_assembly_revision_ref"] is not None:
        from .assembly_v4 import _validate_at as assembly_at
        assembly_at(db, core, _obj(core, intent["parent_assembly_revision_ref"]), registration, binding, visiting)
    for row in intent["ingress_expectations"]:
        if row["kind"] == "assembly_connection":
            _identity(row["other_member_id"], _MEMBER, "other member")
            _identity(row["other_exit_element_id"], _ELEMENT, "other exit")
            if row["other_member_id"] == intent["member_id"]:
                raise ValueError("target_mismatch: prospective connection must cross members")
            other = _obj(core, row["other_revision_ref"])
            if other.source_id != binding["source_id"]:
                raise ValueError("target_mismatch: other endpoint must belong to the selected source")


def _prepare_closure_at(db, core, registration, binding, producer, command_id, request, authorities, visiting,
                        *, fill_dispositions=False):
    _producer_at(db, core, binding, producer)
    if set(request) != {"open_revision_ref", "provenance_ref", "context", "completion", "extract_plan", "intent", "dispositions"}:
        raise ValueError("invalid_selection: closure requires all explicit request fields")
    request = deepcopy(request)
    opened = _open_at(db, core, _obj(core, request["open_revision_ref"]), registration, binding, visiting)
    if request["provenance_ref"] != opened.provenance_ref.to_dict():
        raise RegistryConflict("claim_not_proved: closure must pin O's exact extraction provenance")
    source = _source_at(db, core, registration, binding, _obj(core, opened.provenance["source_revision_ref"]), visiting)
    _intent_at(db, core, binding, request, registration, visiting)
    reference = _result_ref(core, binding, command_id, closure=True)
    key = _key(command_id, closure=True)
    module, compiled, elements, boundaries, origins, dispositions = reconstruct(
        source, opened, request, reference, registration)
    if fill_dispositions:
        request["dispositions"] = dispositions
    elif not _same_json(request["dispositions"], dispositions):
        raise RegistryConflict("claim_not_proved: every explicit disposition must match reconstruction")
    host = _host_at(db, core, binding, compiled)
    source_host = {(row["kind"], row["key"]): row["resource_ref"] for row in source.host_requirements["declaration_refs"]}
    for row in host["declaration_refs"]:
        if not _same_json(row["resource_ref"], source_host.get((row["kind"], row["key"]))):
            raise RegistryConflict("authority_mismatch: closure consumed an unpinned source HOST declaration")
    from ..executable_net import _canonical
    from dataclasses import asdict
    for name, fragment in compiled.fragments.items():
        if not _same_json(_canonical(asdict(fragment)), _canonical(asdict(source.compiled.fragments[name]))):
            raise RegistryConflict("incompatible_boundary: result lowering changed a retained source fragment")
    material_bodies = (module.to_dict(), elements, boundaries, host)
    result_pins = {role: {"resource_ref": _material_ref(core, binding, key + ":" + role).to_dict(),
        "schema": schema, "schema_authority_ref": authorities[schema], **signature(body)}
        for (role, schema), body in zip(SOURCE_MATERIALS, material_bodies, strict=True)}
    origin = {"schema_version": ORIGIN_SCHEMA, "open_revision_ref": opened.revision.revision_ref.to_dict(),
        "source_revision_ref": source.revision.revision_ref.to_dict(), "result_revision_ref": reference.to_dict(),
        "elements": origins}
    adaptation = {"schema_version": ADAPTATION_SCHEMA, "open_revision_ref": opened.revision.revision_ref.to_dict(),
        "provenance_ref": opened.provenance_ref.to_dict(), "context": request["context"], "completion": request["completion"],
        "extract_plan": request["extract_plan"], "intent_ref": _material_ref(core, binding, key + ":intent").to_dict(),
        "dispositions": dispositions, "origin_map_ref": _material_ref(core, binding, key + ":origin_map").to_dict(),
        "result_materials": result_pins, "claim": "exact_current_result", "deployment_intent": "same_run_candidate"}
    bodies = (request["intent"], origin, adaptation, *material_bodies)
    specs = _prepare_specs(core, binding, key, CLOSURE_MARKER,
        [(role, schema, body) for (role, schema), body in zip(CLOSURE_MATERIALS, bodies, strict=True)], authorities)
    command = _command_document(db, core, binding, producer, command_id, request, authorities, reference, specs,
        CLOSURE_COMMAND_SCHEMA, ADAPTER, [CLOSURE_COMMAND_SCHEMA, *[schema for _, schema in CLOSURE_MATERIALS]])
    refs = {spec["role"]: _resource(core, spec["resource_ref"]) for spec in specs}
    revision = NetRevision(reference, _owner(core, binding), producer, command_id, "closed_module", refs["definition"],
        (), (), refs["element_mapping"], refs["boundary_mapping"], refs["host_requirements"], None)
    return command, revision, module, compiled, elements, boundaries, host, adaptation, origin, request["intent"]


@dataclass(frozen=True)
class ValidatedAdaptedRevision(ValidatedClosedRevision):
    command_ref: object
    adaptation_ref: object
    intent_ref: object
    origin_map_ref: object
    adaptation: dict
    origin_map: dict
    intent: dict
    adaptation_claim: str = "exact_current_result"
    target_status: str = "prospective_unverified"


def _closure_at(db, core, revision, registration, binding, visiting):
    binding = _binding_at(db, core)
    if revision.parent_revision_refs or revision.selected_change_refs or revision.open_region_contract_ref is not None:
        raise RegistryConflict("claim_not_proved: closed adaptation is an independent closed root")
    command_ref, command, metadata, visiting = _read_command_at(db, core, revision, binding,
        definition_schema=MODULE_SCHEMA, command_schema=CLOSURE_COMMAND_SCHEMA, marker=CLOSURE_MARKER, visiting=visiting)
    expected, record, module, compiled, elements, boundaries, host, adaptation, origin, intent = _prepare_closure_at(
        db, core, registration, binding, _obj(core, command["producer_principal_ref"]), command["command_id"],
        command["request"], command["schema_authorities"], visiting)
    _check_prepared_at(db, core, binding, revision, expected, record, command_ref, metadata,
        key=_key(command["command_id"], closure=True), marker=CLOSURE_MARKER, schema=CLOSURE_COMMAND_SCHEMA)
    refs = {spec["role"]: _resource(core, spec["resource_ref"]) for spec in expected["prepared_materials"]}
    return ValidatedAdaptedRevision(revision, module, compiled, elements, boundaries, host, command_ref,
        refs["adaptation"], refs["intent"], refs["origin_map"], adaptation, origin, intent)


class OpenRegionClosureAuthor:
    """Prepare reviewable exact dispositions, then publish their complete command."""

    def __init__(self, gateway, registration, producer_principal_ref):
        author = ClosedModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway, self.registration = author.core, gateway, registration
        self.binding, self.producer, self.schemas = author.binding, author.producer, dict(author.schemas)
        for schema in (CLOSURE_COMMAND_SCHEMA, INTENT_SCHEMA, ORIGIN_SCHEMA, ADAPTATION_SCHEMA):
            document = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": document})

    def prepare_request(self, *, open_revision_ref, provenance_ref, context, completion, extract_plan, intent, command_id):
        """Read-only proof preview from explicit context, terminal and ingress.

        The returned complete request is publication input; previewing it
        creates no command, candidate, Branch, membership or permission.
        """
        request = {"open_revision_ref": open_revision_ref.to_dict(), "provenance_ref": provenance_ref.to_dict(),
            "context": deepcopy(context), "completion": deepcopy(completion), "extract_plan": deepcopy(extract_plan),
            "intent": deepcopy(intent), "dispositions": []}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("authority_mismatch: closure binding changed")
            command, *_ = _prepare_closure_at(db, self.core, self.registration, binding, self.producer,
                command_id, request, _authorities_at(db, self.core, self.schemas), set(), fill_dispositions=True)
            return deepcopy(command["request"])

    def publish(self, *, request, command_id):
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("closure author uses a stale writer")
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("authority_mismatch: closure binding changed")
            command, revision, *_ = _prepare_closure_at(db, self.core, self.registration, binding, self.producer,
                command_id, deepcopy(request), _authorities_at(db, self.core, self.schemas), set())
            if self.core.event_store.object_row(revision.revision_ref.ref.version_id) is not None:
                from .materials import _validate_at
                prior = _validate_at(db, self.core, revision.revision_ref, self.registration, binding, set())
                prior_command, _ = _material(db, self.core, prior.command_ref, binding, CLOSURE_COMMAND_SCHEMA)
                if not _same_json(command, prior_command):
                    raise RegistryConflict("command_conflict: closure replay differs from complete request")
                return prior
        _publish_command(self, command, revision, key=_key(command_id, closure=True), marker=CLOSURE_MARKER,
            schema=CLOSURE_COMMAND_SCHEMA, final_validate=lambda db, record, binding:
                _closure_at(db, self.core, record, self.registration, binding, {record.revision_ref}))
        return validate_closed_revision(self.core, revision.revision_ref, self.registration)
