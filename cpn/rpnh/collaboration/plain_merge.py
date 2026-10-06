"""Opt-in immutable plain Module merge analysis. No result/adoption publisher."""
from __future__ import annotations

from dataclasses import dataclass
import json
import uuid

from ..registration import Registration
from ..registry._event_store.collaboration_descriptors import exact_descriptor
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.object_store import ObjectIntegrityError
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.resources import PrivateSystemOrigin, PublishResource
from ..registry.resource_service import _publish_private_system
from ..registry.errors import ResourceIdempotencyConflict
from ..registry.schema_catalog import canonical_json, canonical_text
from .authoring import NET_REVISION_TYPE, _read_net_revision_at
from .materials import ClosedModuleAuthor, _material, _same_json, _validate_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .assembly_v2 import _material_ref, _binding_at
from .assemblies import _command as _check_command
from ._plain_merge_model import ALGORITHM, NORMALIZATION, normalize, compare

ANALYSIS_SCHEMA = "rpnh/collaboration/plain_merge_analysis/v1"
COMMAND_SCHEMA = "rpnh/collaboration/plain_merge_analysis_command/v1"
COMMAND_MARKER = "plain_merge_analysis_command_v1"
ANALYSIS_MARKER = "plain_merge_analysis_v1"


def _command_key(command_id):
    _check_command(command_id)
    return "collaboration-plain-merge-analysis:" + json.dumps(command_id, ensure_ascii=False)


def _reference(value, binding):
    if (not isinstance(value, SourceQualifiedVersionRef) or value.source_id != binding["source_id"]
            or value.ref.entity_type != NET_REVISION_TYPE):
        raise ValueError("plain merge requires exact local NetRevision/v1 references")
    return value


def _lineage_at(db, core, references, binding):
    records, active = {}, set()
    def visit(ref):
        _reference(ref, binding)
        if ref in active:
            raise RegistryConflict("plain merge ancestry contains a cycle")
        if ref in records: return
        active.add(ref)
        record = _read_net_revision_at(db, core, ref, local_source_id=binding["source_id"])
        if record.owner_task_ref.to_dict()["ref"] != binding["task_ref"]:
            raise RegistryConflict("plain merge ancestry owner differs from the bound owner")
        for parent in record.parent_revision_refs: visit(parent)
        records[ref] = record
        active.remove(ref)
    for reference in references: visit(reference)
    return records


def _nearest(records, left, right):
    def ancestors(ref):
        found, pending = set(), [ref]
        while pending:
            current = pending.pop()
            if current in found: continue
            found.add(current)
            pending.extend(records[current].parent_revision_refs)
        return found
    common = ancestors(left) & ancestors(right)
    nearest = [candidate for candidate in common
               if not any(candidate in ancestors(other) for other in common - {candidate})]
    return sorted(nearest, key=lambda ref: canonical_json(ref.to_dict()))


def _plain(value, core, binding):
    from .plain_transform import ValidatedPlainTransformRevision
    if isinstance(value, ValidatedPlainTransformRevision):
        raise RegistryConflict("unsupported_contract: legacy merge cannot consume author transform histories")
    if getattr(value, "transplant_proof", False):
        raise RegistryConflict("unsupported_contract: legacy merge cannot consume a transplant proof")
    if (getattr(value, "adaptation_claim", None) is not None
                    or getattr(value, "historical_origins_ref", None) is not None):
        raise RegistryConflict("unsupported_contract: legacy merge cannot consume adapted author claims")
    if (value.revision.revision_ref.ref.entity_type != NET_REVISION_TYPE or value.module.designer_constraints
            or any(c.key in {f"rpnh/agent-workflow-graph/v{n}" for n in range(1, 5)} for c in value.module.components)):
        raise ValueError("plain merge excludes graph authors, generated Assembly and opaque constraints")
    from .plain_merge_result import ValidatedPlainMergeRevision
    if isinstance(value, ValidatedPlainMergeRevision):
        return  # Its full proof already checked the distinct merge identity rule.
    # The legacy input domain keeps the existing v1 producer identity contract.
    # This is deterministic identity compatibility, not execution/authentication
    # provenance. Keep the inherited general closed reader's contract unchanged.
    revision = value.revision
    _check_command(revision.command_id)
    key = "collaboration-author:" + canonical_text({"command_id": revision.command_id})
    stable = lambda kind: TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "scope": key, "task": str(core.task_id), "source": binding["source_id"], "kind": kind})).hex)
    logical = stable("resource") if not revision.parent_revision_refs else revision.parent_revision_refs[0].ref.entity_id
    expected = SourceQualifiedVersionRef(binding["source_id"], VersionRef(NET_REVISION_TYPE, logical, stable("resource_version")))
    if revision.revision_ref != expected:
        raise RegistryConflict("plain merge input differs from canonical v1 producer identities")


def _pins(value):
    record = value.revision
    return {"revision_ref": record.revision_ref.to_dict(), **{field: getattr(record, field).to_dict()
            for field in ("definition_ref", "element_mapping_ref", "boundary_mapping_ref", "host_requirements_ref")}}


def _prepare_at(db, core, registration, binding, producer, command_id, request, visiting=None):
    visiting = set() if visiting is None else visiting
    key = _command_key(command_id)
    if producer.source_id != binding["source_id"] or producer.ref.entity_type != "principal/v1":
        raise RegistryConflict("merge analysis producer must be an exact local principal")
    principal = exact_descriptor(db, core.object_store, core.task_id, producer.to_dict()["ref"])
    if principal["principal_id"] != str(producer.ref.entity_id) or principal["principal_version_id"] != str(producer.ref.version_id):
        raise RegistryConflict("merge analysis producer self-identity differs")
    parsed = {name: None if value is None else SourceQualifiedVersionRef.from_dict(value, catalog=core.catalog)
              for name, value in request.items()}
    left, right = parsed["local_revision_ref"], parsed["incoming_revision_ref"]
    refs = [left, right]
    if parsed["asserted_base_revision_ref"] is not None: refs.append(parsed["asserted_base_revision_ref"])
    records = _lineage_at(db, core, refs, binding)
    # Every participating ancestor is a full canonical author proof, not just a
    # descriptor. This also keeps unproved multi-parent history unsupported.
    validated = {}
    for ref in records:
        value = _validate_at(db, core, ref, registration, binding, visiting)
        _plain(value, core, binding)
        validated[ref] = value
    candidates = _nearest(records, left, right)
    base = candidates[0] if len(candidates) == 1 else None
    asserted = parsed["asserted_base_revision_ref"]
    if asserted is not None and asserted != base:
        raise ValueError("asserted base is not the unique nearest common author revision")
    status = "unrelated_histories" if not candidates else "multiple_bases" if len(candidates) > 1 else "analyzed"
    models, differences, conflicts = {}, [], []
    if base is not None:
        models = {role: normalize(validated[ref]) for role, ref in (("base", base), ("local", left), ("incoming", right))}
        differences, conflicts = compare(models["base"], models["local"], models["incoming"])
    document = {"schema_version": ANALYSIS_SCHEMA, "algorithm": ALGORITHM, "normalization": NORMALIZATION,
        "source_id": binding["source_id"], "owner_task_ref": SourceQualifiedVersionRef.from_dict({
            "schema_version": "rpnh/collaboration/source_version_ref/v1", "source_id": binding["source_id"],
            "ref": binding["task_ref"]}, catalog=core.catalog).to_dict(),
        "producer_principal_ref": producer.to_dict(), "command_id": command_id,
        "command_ref": _material_ref(core, binding, key + ":command").to_dict(),
        "request": request, "status": status, "base_revision_ref": None if base is None else base.to_dict(),
        "nearest_common_bases": [r.to_dict() for r in candidates],
        "inputs": {"base": None if base is None else _pins(validated[base]),
                   "local": _pins(validated[left]), "incoming": _pins(validated[right])},
        "ancestry": [_pins(validated[ref]) for ref in sorted(records, key=lambda r: canonical_json(r.to_dict()))],
        "normalized": models, "differences": differences, "conflicts": conflicts}
    command = {"schema_version": COMMAND_SCHEMA, "analysis": document}
    for schema, body in ((ANALYSIS_SCHEMA, document), (COMMAND_SCHEMA, command)):
        json.dumps(body, allow_nan=False)  # Data only; never coerce opaque Python or non-finite numbers.
        core.catalog.validate_schema_ref(schema, body)
    return document, command


@dataclass(frozen=True)
class ValidatedPlainMergeAnalysis:
    analysis_ref: SourceQualifiedResourceRef
    command_ref: SourceQualifiedResourceRef
    document: dict


def _read_at(db, core, reference, registration, binding, visiting=None):
    visiting = set() if visiting is None else visiting
    tag = (ANALYSIS_SCHEMA, reference)
    if tag in visiting: raise RegistryConflict("merge analysis/proof cycle")
    visiting = visiting | {tag}
    document, metadata = _material(db, core, reference, binding, ANALYSIS_SCHEMA)
    command_ref = SourceQualifiedResourceRef.from_dict(document["command_ref"], catalog=core.catalog)
    command, command_metadata = _material(db, core, command_ref, binding, COMMAND_SCHEMA)
    producer = SourceQualifiedVersionRef.from_dict(document["producer_principal_ref"], catalog=core.catalog)
    expected, expected_command = _prepare_at(db, core, registration, binding, producer, document["command_id"], document["request"], visiting)
    key = _command_key(document["command_id"])
    if (reference != _material_ref(core, binding, key + ":analysis")
            or command_ref != _material_ref(core, binding, key + ":command")
            or not _same_json(document, expected) or not _same_json(command, expected_command)
            or not _same_json(metadata["descriptors"], {ANALYSIS_MARKER: canonical_text(command_ref.to_dict())})
            or not _same_json(command_metadata["descriptors"], {COMMAND_MARKER: COMMAND_SCHEMA})):
        raise RegistryConflict("merge analysis differs from its exact immutable command and reconstruction")
    return ValidatedPlainMergeAnalysis(reference, command_ref, document)


def read_plain_merge_analysis(core, reference, registration):
    """Strict full proof reader in one cut; never a descriptor-only decoder."""
    if not isinstance(registration, Registration):
        raise TypeError("merge analysis reading requires an explicit trusted Registration")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return _read_at(db, core, reference, registration, _binding_at(db, core))


class PlainModuleMergeAnalyzer:
    """Trusted author-only producer. Analysis and future result commands differ."""
    def __init__(self, gateway, registration, producer_principal_ref):
        from ..registry.registration_gateway import RegistryRegistrationGateway
        if not isinstance(gateway, RegistryRegistrationGateway) or not isinstance(registration, Registration):
            raise TypeError("merge analyzer requires the trusted owner gateway and Registration")
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

    def analyze(self, *, local_ref, incoming_ref, command_id, base_ref=None):
        key = _command_key(command_id)
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError("merge analyzer uses a stale owner writer")
        request = {"local_revision_ref": _reference(local_ref, self.binding).to_dict(),
                   "incoming_revision_ref": _reference(incoming_ref, self.binding).to_dict(),
                   "asserted_base_revision_ref": None if base_ref is None else _reference(base_ref, self.binding).to_dict()}
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            binding = _binding_at(db, self.core)
            if not _same_json(binding, self.binding):
                raise RegistryConflict("merge analyzer bound source changed")
            document, command = _prepare_at(db, self.core, self.registration, binding, self.producer, command_id, request)
        command_ref = _material_ref(self.core, binding, key + ":command")
        try:
            for suffix, schema, body, marker in (
                ("command", COMMAND_SCHEMA, command, {COMMAND_MARKER: COMMAND_SCHEMA}),
                ("analysis", ANALYSIS_SCHEMA, document, {ANALYSIS_MARKER: canonical_text(command_ref.to_dict())})):
                ref = _publish_private_system(self.core, self.gateway._task_ref, PublishResource(
                    origin=PrivateSystemOrigin(self.gateway._bootstrap_ref), payload=canonical_json(body),
                    media_type="application/json", content_schema_ref=schema, content_schema_authority_ref=self.schemas[schema],
                    summary="Plain merge " + suffix, lifetime_ref=self.gateway._bootstrap_ref,
                    descriptors=marker, idempotency_key=key + ":" + suffix))
                if SourceQualifiedResourceRef(binding["source_id"], ref) != _material_ref(self.core, binding, key + ":" + suffix):
                    raise RegistryConflict("merge analysis material identity differs")
        except (ObjectIntegrityError, ResourceIdempotencyConflict) as exc:
            raise RegistryConflict("merge analysis conflicts with immutable complete command") from exc
        return read_plain_merge_analysis(self.core, _material_ref(self.core, binding, key + ":analysis"), self.registration)
