"""Opt-in immutable plain Module transplant analysis. No result/adoption publisher."""
from __future__ import annotations

from dataclasses import dataclass
import json

from ..registration import Registration
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.object_store import ObjectIntegrityError
from ..registry.resources import PrivateSystemOrigin, PublishResource
from ..registry.resource_service import _publish_private_system
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.schema_catalog import canonical_json, canonical_text
from .materials import ClosedModuleAuthor, _material, _same_json
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .assembly_v2 import _material_ref, _binding_at
from .assemblies import _command as _check_command


ANALYSIS_SCHEMA = "rpnh/collaboration/plain_transplant_analysis/v1"
COMMAND_SCHEMA = "rpnh/collaboration/plain_transplant_analysis_command/v1"
COMMAND_MARKER = "plain_transplant_analysis_command_v1"
ANALYSIS_MARKER = "plain_transplant_analysis_v1"


def _command_key(command_id):
    _check_command(command_id)
    return "collaboration-plain-transplant-analysis:" + json.dumps(command_id, ensure_ascii=False)


from .plain_merge import _reference, _prepare_at as _ordinary_analysis_at
from ._plain_transplant_resolution import ALGORITHM, analyze_models
from .plain_merge_result import _resource_bytes, _metadata, _schema_authority_at


def _prepare_at(db, core, registration, binding, producer, command_id, request, authorities, visiting=None):
    refs = {key: value for key, value in request.items() if key != "selected_subjects"}
    document, _ = _ordinary_analysis_at(db, core, registration, binding, producer, command_id, refs, visiting)
    if document["status"] != "analyzed":
        raise ValueError("transplant requires one unique nearest common author base")
    left, right = (SourceQualifiedVersionRef.from_dict(request[field], catalog=core.catalog)
                   for field in ("local_revision_ref", "incoming_revision_ref"))
    if left.ref.entity_id != right.ref.entity_id:
        raise ValueError("transplant requires one shared logical lineage")
    projected, differences, conflicts, gaps = analyze_models(document["normalized"], request["selected_subjects"])
    key = _command_key(command_id)
    document.update(schema_version=ANALYSIS_SCHEMA, algorithm=ALGORITHM, request=request,
        command_ref=_material_ref(core, binding, key + ":command").to_dict(),
        projected_incoming=projected, differences=differences, conflicts=conflicts, selection_gaps=gaps)
    if set(authorities) != {ANALYSIS_SCHEMA, COMMAND_SCHEMA}:
        raise RegistryConflict("transplant analysis locks both exact schema authorities")
    digests = {schema: _schema_authority_at(db, core, binding, schema, authority) for schema, authority in authorities.items()}
    command = {"schema_version": COMMAND_SCHEMA, "analysis": document,
        "schema_authorities": authorities, "schema_authority_sha256": digests}
    for schema, body in ((ANALYSIS_SCHEMA, document), (COMMAND_SCHEMA, command)):
        core.catalog.validate_schema_ref(schema, body)
    return document, command


@dataclass(frozen=True)
class ValidatedPlainTransplantAnalysis:
    analysis_ref: SourceQualifiedResourceRef
    command_ref: SourceQualifiedResourceRef
    document: dict


def _read_at(db, core, reference, registration, binding, visiting=None):
    visiting = set() if visiting is None else visiting
    tag = (ANALYSIS_SCHEMA, reference)
    if tag in visiting: raise RegistryConflict("transplant analysis/proof cycle")
    visiting = visiting | {tag}
    document, metadata = _material(db, core, reference, binding, ANALYSIS_SCHEMA)
    command_ref = SourceQualifiedResourceRef.from_dict(document["command_ref"], catalog=core.catalog)
    command, command_metadata = _material(db, core, command_ref, binding, COMMAND_SCHEMA)
    producer = SourceQualifiedVersionRef.from_dict(document["producer_principal_ref"], catalog=core.catalog)
    expected, expected_command = _prepare_at(db, core, registration, binding, producer, document["command_id"], document["request"], command["schema_authorities"], visiting)
    key = _command_key(document["command_id"])
    if (reference != _material_ref(core, binding, key + ":analysis")
            or command_ref != _material_ref(core, binding, key + ":command")
            or _resource_bytes(db, core, reference) != canonical_json(expected)
            or _resource_bytes(db, core, command_ref) != canonical_json(expected_command)
            or not _same_json(document, expected) or not _same_json(command, expected_command)
            or not _same_json(metadata, _metadata(core, binding, reference, ANALYSIS_SCHEMA,
                command["schema_authorities"][ANALYSIS_SCHEMA], expected, "Plain transplant analysis",
                {ANALYSIS_MARKER: canonical_text(command_ref.to_dict())}))
            or not _same_json(command_metadata, _metadata(core, binding, command_ref, COMMAND_SCHEMA,
                command["schema_authorities"][COMMAND_SCHEMA], expected_command, "Plain transplant command",
                {COMMAND_MARKER: COMMAND_SCHEMA}))):
        raise RegistryConflict("transplant analysis differs from its exact immutable command and reconstruction")
    return ValidatedPlainTransplantAnalysis(reference, command_ref, document)


def read_plain_transplant_analysis(core, reference, registration):
    """Strict full proof reader in one cut; never a descriptor-only decoder."""
    if not isinstance(registration, Registration):
        raise TypeError("transplant analysis reading requires an explicit trusted Registration")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return _read_at(db, core, reference, registration, _binding_at(db, core))


class PlainModuleTransplantAnalyzer:
    """Trusted author-only producer. Analysis and future result commands differ."""
    def __init__(self, gateway, registration, producer_principal_ref):
        from ..registry.registration_gateway import RegistryRegistrationGateway
        if not isinstance(gateway, RegistryRegistrationGateway) or not isinstance(registration, Registration):
            raise TypeError("transplant analyzer requires the trusted owner gateway and Registration")
        # Check every actual binding authority before any schema registration.
        # Reuse the accepted same-cut Assembly closure without changing the
        # general source helper or legacy closed-reader permission contract.
        with gateway._core.event_store.connect() as db:
            db.execute("BEGIN")
            _binding_at(db, gateway._core)
        self.author = ClosedModuleAuthor(gateway, registration, producer_principal_ref)
        self.core, self.gateway = self.author.core, gateway
        self.registration, self.producer, self.binding = registration, self.author.producer, self.author.binding
        self.schemas = {}
        for schema in (COMMAND_SCHEMA, ANALYSIS_SCHEMA):
            body = json.loads(self.core.catalog.schema_path(schema).read_text(encoding="utf-8"))
            self.schemas[schema] = gateway("schema", schema, {"kind": "schema", "key": schema, "schema": body})

    def analyze(self, *, local_ref, incoming_ref, selected_subjects, command_id, base_ref=None):
        key = _command_key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("transplant analyzer uses a stale owner writer")
        request = {"selected_subjects": list(selected_subjects) if isinstance(selected_subjects, (list, tuple)) else selected_subjects, "local_revision_ref": _reference(local_ref, self.binding).to_dict(),
                   "incoming_revision_ref": _reference(incoming_ref, self.binding).to_dict(),
                   "asserted_base_revision_ref": None if base_ref is None else _reference(base_ref, self.binding).to_dict()}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("transplant analyzer bound source changed")
            authorities = {schema: {"resource_id": str(ref.resource_id), "resource_version_id": str(ref.resource_version_id)}
                           for schema, ref in self.schemas.items()}
            document, command = _prepare_at(db, self.core, self.registration, binding, self.producer, command_id, request, authorities)
        command_ref = _material_ref(self.core, binding, key + ":command")
        try:
            for suffix, schema, body, marker in (
                ("command", COMMAND_SCHEMA, command, {COMMAND_MARKER: COMMAND_SCHEMA}),
                ("analysis", ANALYSIS_SCHEMA, document, {ANALYSIS_MARKER: canonical_text(command_ref.to_dict())})):
                ref = _publish_private_system(self.core, self.gateway._task_ref, PublishResource(
                    origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(body),
                    media_type="application/json", content_schema_ref=schema, content_schema_authority_ref=self.schemas[schema],
                    summary="Plain transplant " + suffix, lifetime_ref=self.gateway._bootstrap_ref,
                    descriptors=marker, idempotency_key=key + ":" + suffix))
                if SourceQualifiedResourceRef(binding["source_id"], ref) != _material_ref(self.core, binding, key + ":" + suffix):
                    raise RegistryConflict("transplant analysis material identity differs")
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("transplant analysis conflicts with immutable complete command") from exc
        return read_plain_transplant_analysis(self.core, _material_ref(self.core, binding, key + ":analysis"), self.registration)
