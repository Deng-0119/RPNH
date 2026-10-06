"""Owner-published author Branch versions; no runtime adoption or reset."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from cpn.rpnh.registry._event_store.branch_publication import (
    BRANCH_SCHEMA, BRANCH_TYPE, GRAPH_BRANCH_SCHEMA, GRAPH_BRANCH_TYPE,
    GRAPH_MERGE_BRANCH_SCHEMA, GRAPH_MERGE_BRANCH_TYPE,
    branch_command_key, branch_id_for_command,
    branch_version_id, current_branch_document, exact_branch_document,
)
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json

from .authoring import _exact_object_ref, _revision_ref, read_net_revision
from .references import SourceQualifiedVersionRef
from .sources import get_local_source_identity


def _branch_ref(value):
    return _exact_object_ref(value, entity_type=BRANCH_TYPE,
                             logical_kind="resource", version_kind="resource_version")


@dataclass(frozen=True, slots=True)
class _BranchRecord:
    branch_ref: SourceQualifiedVersionRef
    owner_task_ref: SourceQualifiedVersionRef
    publisher_bootstrap_ref: SourceQualifiedVersionRef
    head_revision_ref: SourceQualifiedVersionRef
    fork_base_revision_ref: SourceQualifiedVersionRef
    upstream_branch_ref: SourceQualifiedVersionRef | None
    predecessor_branch_ref: SourceQualifiedVersionRef | None
    expected_head_revision_ref: SourceQualifiedVersionRef | None
    expected_stream_head: int
    sequence: int
    command_id: str

    def __post_init__(self):
        self._check_branch_ref(self.branch_ref)
        _exact_object_ref(self.owner_task_ref, entity_type="task/v1", logical_kind="task", version_kind="task_version")
        _exact_object_ref(self.publisher_bootstrap_ref, entity_type="bootstrap_command/v1",
                          logical_kind="bootstrap_command", version_kind="bootstrap_command_version")
        self._check_revision_ref(self.head_revision_ref)
        self._check_revision_ref(self.fork_base_revision_ref)
        refs = [self.owner_task_ref, self.publisher_bootstrap_ref, self.head_revision_ref, self.fork_base_revision_ref]
        for ref in (self.upstream_branch_ref, self.predecessor_branch_ref):
            if ref is not None:
                self._check_branch_ref(ref)
                refs.append(ref)
        if self.expected_head_revision_ref is not None:
            self._check_revision_ref(self.expected_head_revision_ref)
            refs.append(self.expected_head_revision_ref)
        if any(ref.source_id != self.branch_ref.source_id for ref in refs):
            raise ValueError("this Branch contract supports same-source publication only")
        if (type(self.expected_stream_head) is not int or self.expected_stream_head < 0
                or type(self.sequence) is not int or self.sequence != self.expected_stream_head + 1):
            raise ValueError("Branch sequence must follow the exact caller stream expectation")
        if ((self.predecessor_branch_ref is None) != (self.sequence == 1)
                or (self.expected_head_revision_ref is None) != (self.sequence == 1)):
            raise ValueError("Branch creation and advancement expectations differ")
        if self.predecessor_branch_ref is not None:
            if (self.predecessor_branch_ref.ref.entity_id != self.branch_ref.ref.entity_id
                    or self.predecessor_branch_ref == self.branch_ref):
                raise ValueError("Branch successor must retain identity and have a fresh version")
        if (not isinstance(self.command_id, str) or not self.command_id
                or self.command_id != self.command_id.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in self.command_id)):
            raise ValueError("command_id must be one canonical command identity")

    def to_dict(self):
        return {"schema_version": self._branch_schema, **{
            name: value.to_dict() if isinstance(value, SourceQualifiedVersionRef) else value
            for name in self.__dataclass_fields__ for value in (getattr(self, name),)
        }}

    @classmethod
    def from_dict(cls, document: Mapping[str, Any], *, catalog: SchemaCatalog):
        catalog.validate_instance(cls._branch_type, category="object", instance=document)
        return cls(**{
            name: (SourceQualifiedVersionRef.from_dict(document[name], catalog=catalog)
                   if name.endswith("_ref") and document[name] is not None else document[name])
            for name in cls.__dataclass_fields__
        })


class BranchVersion(_BranchRecord):
    """Legacy v1 descriptor contract, including open/multi-parent revisions."""
    __slots__ = ()
    _branch_type = BRANCH_TYPE
    _branch_schema = BRANCH_SCHEMA
    _check_branch_ref = staticmethod(_branch_ref)
    _check_revision_ref = staticmethod(_revision_ref)


class GraphBranchVersion(_BranchRecord):
    """Explicit v2 Branch; all revision and Branch refs stay in v2."""
    __slots__ = ()
    _branch_type = GRAPH_BRANCH_TYPE
    _branch_schema = GRAPH_BRANCH_SCHEMA

    @staticmethod
    def _check_branch_ref(value):
        return _exact_object_ref(value, entity_type=GRAPH_BRANCH_TYPE,
                                 logical_kind="resource", version_kind="resource_version")

    @staticmethod
    def _check_revision_ref(value):
        return _exact_object_ref(value, entity_type="collaboration_net_revision/v2",
                                 logical_kind="resource", version_kind="resource_version")


class GraphMergeBranchVersion(_BranchRecord):
    """Opt-in v3 Branch over exact ordinary graph-v2/v3 author histories."""
    __slots__ = ()
    _branch_type = GRAPH_MERGE_BRANCH_TYPE
    _branch_schema = GRAPH_MERGE_BRANCH_SCHEMA

    @staticmethod
    def _check_branch_ref(value):
        return _exact_object_ref(value, entity_type=GRAPH_MERGE_BRANCH_TYPE,
                                 logical_kind="resource", version_kind="resource_version")

    @staticmethod
    def _check_revision_ref(value):
        if not isinstance(value, SourceQualifiedVersionRef) or value.ref.entity_type not in {
                "collaboration_net_revision/v2", "collaboration_net_revision/v3"}:
            raise ValueError("graph merge Branch requires an exact graph-v2/v3 author")
        return _exact_object_ref(value, entity_type=value.ref.entity_type,
                                 logical_kind="resource", version_kind="resource_version")


def _branch_record_type(entity_type):
    # Exact known versions only. Never interpret an unknown record as legacy.
    if entity_type == BRANCH_TYPE:
        return BranchVersion
    if entity_type == GRAPH_BRANCH_TYPE:
        return GraphBranchVersion
    if entity_type == GRAPH_MERGE_BRANCH_TYPE:
        return GraphMergeBranchVersion
    raise ValueError("unsupported author Branch version")


def _decode_branch(document, catalog):
    record = _branch_record_type(document["branch_ref"]["ref"]["entity_type"])
    return record.from_dict(document, catalog=catalog)


def read_branch_version(core, reference: SourceQualifiedVersionRef) -> BranchVersion | GraphBranchVersion:
    record = _branch_record_type(reference.ref.entity_type)
    record._check_branch_ref(reference)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        document = exact_branch_document(db, core.catalog, core.task_id, reference.to_dict(), core.object_store)
    return record.from_dict(document, catalog=core.catalog)


def current_branch(core, branch_id) -> BranchVersion | GraphBranchVersion | None:
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        document = current_branch_document(db, core.catalog, core.task_id, str(branch_id), core.object_store)
    if document is None:
        return None
    version = _decode_branch(document, core.catalog)
    return read_branch_version(core, version.branch_ref)


def _publish_branch(core, version):
    ref, document = version.branch_ref.ref, version.to_dict()
    tx = core.begin(idempotency_key=branch_command_key(version.command_id))
    try:
        tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
                    payload=canonical_json(document), metadata=document, media_type="application/json",
                    schema_ref=version._branch_schema)
    except ObjectIntegrityError as exc:
        raise RegistryConflict("Branch command conflicts with immutable prior material") from exc
    tx.commit()
    return read_branch_version(core, version.branch_ref)


def _read_target(core, reference, record, source_id):
    record._check_revision_ref(reference)
    if record is BranchVersion:
        return read_net_revision(core, reference, local_source_id=source_id)
    from .graph_authoring import _read_graph_revision_at
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        if record is GraphMergeBranchVersion:
            from .graph_merge import _read_revision_at
            from .assembly_v2 import _binding_at
            return _read_revision_at(db, core, reference, _binding_at(db, core))
        return _read_graph_revision_at(db, core, reference, local_source_id=source_id)


def _create_branch_record(core, record, *, task_ref, bootstrap_ref, head_revision_ref,
                          command_id, upstream_branch_ref=None):
    binding = get_local_source_identity(core)
    if binding is None:
        raise RegistryConflict("Branch publication requires an explicit local source binding")
    _read_target(core, head_revision_ref, record, binding.source_id)
    if upstream_branch_ref is not None:
        record._check_branch_ref(upstream_branch_ref)
        upstream = read_branch_version(core, upstream_branch_ref)
        if upstream.head_revision_ref != head_revision_ref:
            raise RegistryConflict("fork base differs from the exact upstream Branch version")
    return _publish_branch(core, record(
        branch_ref=SourceQualifiedVersionRef(binding.source_id, VersionRef(record._branch_type,
            branch_id_for_command(core.task_id, binding.source_id, command_id), branch_version_id(core.task_id, command_id))),
        owner_task_ref=SourceQualifiedVersionRef(binding.source_id, task_ref),
        publisher_bootstrap_ref=SourceQualifiedVersionRef(binding.source_id, bootstrap_ref),
        head_revision_ref=head_revision_ref, fork_base_revision_ref=head_revision_ref,
        upstream_branch_ref=upstream_branch_ref, predecessor_branch_ref=None, expected_head_revision_ref=None,
        expected_stream_head=0, sequence=1, command_id=command_id,
    ))


def _advance_branch_record(core, record, *, task_ref, bootstrap_ref, expected_branch_version_ref,
                           expected_head_revision_ref, expected_stream_head, next_revision_ref, command_id):
    record._check_branch_ref(expected_branch_version_ref)
    previous = read_branch_version(core, expected_branch_version_ref)
    if expected_branch_version_ref.ref.version_id == branch_version_id(core.task_id, command_id):
        raise RegistryConflict("Branch command conflicts with its own predecessor version")
    binding = get_local_source_identity(core)
    if binding is None:
        raise RegistryConflict("Branch publication requires an explicit local source binding")
    _read_target(core, next_revision_ref, record, binding.source_id)
    return _publish_branch(core, record(
        branch_ref=SourceQualifiedVersionRef(binding.source_id, VersionRef(record._branch_type,
            expected_branch_version_ref.ref.entity_id, branch_version_id(core.task_id, command_id))),
        owner_task_ref=SourceQualifiedVersionRef(binding.source_id, task_ref),
        publisher_bootstrap_ref=SourceQualifiedVersionRef(binding.source_id, bootstrap_ref),
        head_revision_ref=next_revision_ref, fork_base_revision_ref=previous.fork_base_revision_ref,
        upstream_branch_ref=previous.upstream_branch_ref, predecessor_branch_ref=expected_branch_version_ref,
        expected_head_revision_ref=expected_head_revision_ref, expected_stream_head=expected_stream_head,
        sequence=expected_stream_head + 1, command_id=command_id,
    ))


def _create_branch(core, **kwargs):
    return _create_branch_record(core, BranchVersion, **kwargs)


def _advance_branch(core, **kwargs):
    return _advance_branch_record(core, BranchVersion, **kwargs)


def _create_graph_branch(core, **kwargs):
    return _create_branch_record(core, GraphBranchVersion, **kwargs)


def _advance_graph_branch(core, **kwargs):
    return _advance_branch_record(core, GraphBranchVersion, **kwargs)


def _create_graph_merge_branch(core, **kwargs):
    return _create_branch_record(core, GraphMergeBranchVersion, **kwargs)


def _advance_graph_merge_branch(core, **kwargs):
    return _advance_branch_record(core, GraphMergeBranchVersion, **kwargs)
