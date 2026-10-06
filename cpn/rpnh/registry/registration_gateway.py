"""Persist explicit HOST declarations through the execution owner's Registry.

This gateway never resolves callables, imports implementation locators, or owns
another writer. It publishes immutable resources with exact bootstrap lineage;
schema documents become the sole schema-resource source for fresh compilation.
"""
from __future__ import annotations
import json

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.schema_catalog import PROTECTED_SCHEMA_REFS, _SCHEMA_ID
from .resource_service import _publish_private_system


class RegistryRegistrationGateway:
    def __init__(self, core: _RegistryCore, task_ref: VersionRef,
                 bootstrap_ref: VersionRef) -> None:
        if (not isinstance(core, _RegistryCore) or core.read_only
                or task_ref.entity_type != "task/v1"
                or task_ref.entity_id != core.task_id
                or bootstrap_ref.entity_type != "bootstrap_command/v1"):
            raise TypeError("HOST registration requires the execution owner's exact Registry")
        for ref in (task_ref, bootstrap_ref):
            obj = core.get_version(ref.version_id)
            if obj.object_type != ref.entity_type or obj.logical_id != ref.entity_id:
                raise ValueError("registration source is not an exact registered authority")
        self._core = core
        self._task_ref = task_ref
        self._bootstrap_ref = bootstrap_ref
        self._refs: dict[tuple[str, str], ResourceVersionRef] = {}

    def publish_source_set(self, *, members, command_id, expected=None, expected_sequence=0):
        """Publish an opt-in expected source manifest; never grant source access."""
        from ..collaboration.source_sets import _publish_source_set
        return _publish_source_set(self, members=members, command_id=command_id, expected=expected, expected_sequence=expected_sequence)

    def record_source_observation(self, *, query, draft, command_id):
        """Explicitly record a finite query; query and Viewer paths never call this."""
        from ..collaboration.source_observations import _record_source_observation
        return _record_source_observation(self, query=query, draft=draft, command_id=command_id)

    def __call__(self, kind: str, key: str, declaration: dict) -> ResourceVersionRef:
        if (kind not in {"schema", "component", "executor", "tool", "analyzer"}
                or declaration.get("kind") != kind or declaration.get("key") != key):
            raise ValueError("registration declaration identity differs from its registered key")
        data = json.loads(json.dumps(declaration, allow_nan=False))
        if kind == "schema":
            from jsonschema import Draft7Validator
            if (_SCHEMA_ID.fullmatch(key) is None or key in PROTECTED_SCHEMA_REFS
                    or data["schema"].get("$id") != key):
                raise ValueError("schema registration cannot replace mechanical identities")
            Draft7Validator.check_schema(data["schema"])
        payload = canonical_json(data["schema"] if kind == "schema" else data)
        ref = _publish_private_system(
            self._core, self._task_ref,
            PublishResource(
                origin=PrivateSystemOrigin(self._bootstrap_ref), payload=payload,
                media_type="application/schema+json" if kind == "schema" else "application/json",
                content_schema_ref="registry_v1/registry_type_catalog/v1" if kind == "schema" else None,
                summary=f"HOST registered {kind} {key}", lifetime_ref=self._bootstrap_ref,
                descriptors={"host_registration_kind": kind, "registered_key": key},
                idempotency_key=f"host-registration:{kind}:{key}"))
        self._refs[kind, key] = ref
        return ref

    @property
    def schema_refs(self) -> dict[str, ResourceVersionRef]:
        return {key: ref for (kind, key), ref in self._refs.items() if kind == "schema"}

    def bind_builtin_schema(self, key: str) -> ResourceVersionRef:
        """Persist the exact existing mechanical document, never replace it."""
        if key not in PROTECTED_SCHEMA_REFS:
            raise ValueError("builtin binding requires an existing mechanical schema")
        document = json.loads(self._core.catalog.schema_path(key).read_text(encoding="utf-8"))
        ref = _publish_private_system(self._core, self._task_ref, PublishResource(
            origin=PrivateSystemOrigin(self._bootstrap_ref), payload=canonical_json(document),
            media_type="application/schema+json", content_schema_ref="registry_v1/registry_type_catalog/v1",
            summary=f"Existing mechanical schema {key}", lifetime_ref=self._bootstrap_ref,
            descriptors={"host_registration_kind": "schema", "registered_key": key, "mechanical_schema": True},
            idempotency_key=f"builtin-schema-source:{key}"))
        self._refs["schema", key] = ref
        return ref

    @property
    def declaration_refs(self) -> dict[tuple[str, str], ResourceVersionRef]:
        return dict(self._refs)

    def bind_source_identity(self, *, source_id: str, command_id: str):
        """Explicit opt-in source association by this trusted owner/HOST.

        Uses the same current writer and registered task/bootstrap authority as
        schema publication. This is not a task-authored operation or an access
        grant, and a declared principal string is not an authorization input.
        """
        from cpn.rpnh.collaboration.sources import _bind_source_identity
        return _bind_source_identity(
            self._core, task_ref=self._task_ref, bootstrap_ref=self._bootstrap_ref,
            source_id=source_id, command_id=command_id,
        )

    def create_author_branch(self, *, head_revision_ref, command_id: str, upstream_branch_ref=None):
        """Create a local author Branch; this does not adopt its head in a run."""
        from cpn.rpnh.collaboration.branches import _create_branch
        return _create_branch(self._core, task_ref=self._task_ref, bootstrap_ref=self._bootstrap_ref,
                              head_revision_ref=head_revision_ref, command_id=command_id,
                              upstream_branch_ref=upstream_branch_ref)

    def advance_author_branch(self, *, expected_branch_version_ref, expected_head_revision_ref,
                              expected_stream_head: int, next_revision_ref, command_id: str):
        """Publish a direct descendant with all three caller expectations fixed."""
        from cpn.rpnh.collaboration.branches import _advance_branch
        return _advance_branch(self._core, task_ref=self._task_ref, bootstrap_ref=self._bootstrap_ref,
            expected_branch_version_ref=expected_branch_version_ref, expected_head_revision_ref=expected_head_revision_ref,
            expected_stream_head=expected_stream_head, next_revision_ref=next_revision_ref, command_id=command_id)

    def create_graph_author_branch(self, *, head_revision_ref, command_id: str, upstream_branch_ref=None):
        """Opt-in graph-v2 descriptor Branch, without compile or adoption."""
        from cpn.rpnh.collaboration.branches import _create_graph_branch
        return _create_graph_branch(self._core, task_ref=self._task_ref, bootstrap_ref=self._bootstrap_ref,
            head_revision_ref=head_revision_ref, command_id=command_id, upstream_branch_ref=upstream_branch_ref)

    def advance_graph_author_branch(self, *, expected_branch_version_ref, expected_head_revision_ref,
                                    expected_stream_head: int, next_revision_ref, command_id: str):
        """Advance graph-v2 only, using the shared Branch-family command domain."""
        from cpn.rpnh.collaboration.branches import _advance_graph_branch
        return _advance_graph_branch(self._core, task_ref=self._task_ref, bootstrap_ref=self._bootstrap_ref,
            expected_branch_version_ref=expected_branch_version_ref, expected_head_revision_ref=expected_head_revision_ref,
            expected_stream_head=expected_stream_head, next_revision_ref=next_revision_ref, command_id=command_id)


    def create_graph_merge_branch(self, *, head_revision_ref, command_id: str, upstream_branch_ref=None):
        """Create an opt-in v3 Branch; no material proof or runtime adoption."""
        from cpn.rpnh.collaboration.branches import _create_graph_merge_branch
        return _create_graph_merge_branch(self._core, task_ref=self._task_ref, bootstrap_ref=self._bootstrap_ref,
            head_revision_ref=head_revision_ref, command_id=command_id, upstream_branch_ref=upstream_branch_ref)

    def advance_graph_merge_branch(self, *, expected_branch_version_ref, expected_head_revision_ref,
                                   expected_stream_head: int, next_revision_ref, command_id: str):
        """Direct-parent membership and the shared exact three-axis CAS contract."""
        from cpn.rpnh.collaboration.branches import _advance_graph_merge_branch
        return _advance_graph_merge_branch(self._core, task_ref=self._task_ref, bootstrap_ref=self._bootstrap_ref,
            expected_branch_version_ref=expected_branch_version_ref, expected_head_revision_ref=expected_head_revision_ref,
            expected_stream_head=expected_stream_head, next_revision_ref=next_revision_ref, command_id=command_id)


__all__ = ("RegistryRegistrationGateway",)
