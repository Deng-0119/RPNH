"""Registry-internal D1-C storage and transaction coordinator."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping

from .event_store import EventStore, RegistryConflict, TaskControlLedger
from .capabilities import CapabilityService
from .identities import TypedId, new_id
from .errors import IncompleteNativeRun, NotNativeRun
from .models import PendingEvent, PreparedObject, TypedRelation, VersionRef
from .object_store import ObjectStore
from .relations import RelationStore
from .schema_catalog import SchemaCatalog, canonical_json
from .transaction import RegistryTransaction


class RegistryReadError(RuntimeError):
    pass


class _RegistryCore:
    """Own one canonical store behind the typed ResourceService boundary."""

    def __init__(self, run_dir: Path | str, *, create: bool,
                 branch_id: str = "main", writer_epoch: int | None = None,
                 load_dependencies: bool = False,
                 project_task_id: TypedId | None = None,
                 project_catalog_ref: VersionRef | None = None,
                 read_only: bool = False,
                 catalog: SchemaCatalog | None = None) -> None:
        if create and read_only:
            raise TypeError("a read-only Registry cannot be created")
        self.read_only = bool(read_only)
        self.run_dir = Path(run_dir)
        self.root = self.run_dir / ".registry_v1"
        database_path = self.root / "registry.sqlite3"
        if not self.read_only:
            from .parent_bound import preflight_bound_writer
            preflight_bound_writer(database_path)
        if create:
            if database_path.exists():
                raise RuntimeError("native registry already exists")
        elif not database_path.is_file():
            raise NotNativeRun("native registry database is missing")
        if not self.read_only:
            self.root.mkdir(parents=True, exist_ok=True)
        # HOST supplies optional inventories to this same writer/validator.
        # The default is mechanical only; no component/workflow loading occurs.
        self.catalog = SchemaCatalog() if catalog is None else catalog
        self.event_store = EventStore(
            database_path, self.catalog, read_only=self.read_only)
        self.object_store = ObjectStore(
            self.root / "objects", self.catalog, read_only=self.read_only)
        self.relations = RelationStore(self.event_store)
        self.task_control = TaskControlLedger(self)
        self.capabilities = CapabilityService(self)
        self.run_dependencies: Any | None = None
        self.mutable_dependency_source: Any | None = None
        self._mutable_dependency_reads = threading.local()
        # LEGAL_APPEND_HISTORY makes exact resource versions immutable through
        # normal APIs. Ambient authority calls may reuse successful checks;
        # direct recovery/integrity verification deliberately bypasses them.
        self._successful_resource_dependency_verifications: dict[str, str] = {}
        self._successful_schema_authority_verifications: dict[
            str, str | None] = {}
        if create:
            if (project_task_id is not None
                    and project_task_id.kind != "task"):
                raise TypeError("project task identity must be one task id")
            if (project_catalog_ref is not None
                    and (project_catalog_ref.entity_type
                         != "registry_type_catalog/v1"
                         or project_catalog_ref.entity_id.kind != "schema"
                         or project_catalog_ref.version_id.kind
                         != "resource_version")):
                raise TypeError(
                    "project catalog identity must be one exact catalog ref")
            self.task_id = TypedId.parse(
                self.event_store.get_or_create_meta(
                    "task_id", str(project_task_id or new_id("task"))),
                expected="task")
            self.branch_id = self.event_store.get_or_create_meta("branch_id", branch_id)
            if project_catalog_ref is not None:
                self.event_store.get_or_create_meta(
                    "type_catalog_logical_id",
                    str(project_catalog_ref.entity_id))
                self.event_store.get_or_create_meta(
                    "type_catalog_version_id",
                    str(project_catalog_ref.version_id))
        else:
            task = self.event_store.get_meta("task_id")
            stored_branch = self.event_store.get_meta("branch_id")
            if task is None or stored_branch is None:
                raise IncompleteNativeRun("native task or branch identity is missing")
            self.task_id = TypedId.parse(task, expected="task")
            self.branch_id = stored_branch
        self.writer_epoch = (
            self.event_store.writer_epoch
            if self.read_only else
            (writer_epoch if writer_epoch is not None
             else self.event_store.acquire_writer()))
        if create:
            self._publish_type_catalog()
        elif load_dependencies:
            raise RegistryReadError(
                "frozen dependency loading is not part of the current runtime")

    @property
    def provider_attempts(self):
        from .provider_calls import ProviderAttemptLedger
        ledger = getattr(self, "_provider_attempt_ledger", None)
        if ledger is None:
            ledger = self._provider_attempt_ledger = ProviderAttemptLedger(self)
        return ledger

    def reserve_provider_attempt_v2(self, *, context, call, prior_attempt,
                                  idempotency_key):
        from .provider_execution import ProviderExecution
        return ProviderExecution(self).reserve_provider_attempt_v2(
            context=context, call=call, prior_attempt=prior_attempt,
            idempotency_key=idempotency_key)

    def commit_provider_raw_response_v2(self, *, attempt, payload, status_code,
                                      external_request_id, idempotency_key):
        from .provider_execution import ProviderExecution
        return ProviderExecution(self).commit_provider_raw_response_v2(
            attempt=attempt, payload=payload, status_code=status_code,
            external_request_id=external_request_id,
            idempotency_key=idempotency_key)

    def reserve_provider_attempt_v3(self, *, context, call, prior_attempt,
                                    idempotency_key):
        from .provider_execution import ProviderExecution
        return ProviderExecution(self).reserve_provider_attempt_v3(
            context=context, call=call, prior_attempt=prior_attempt,
            idempotency_key=idempotency_key)

    def commit_provider_raw_response_v3(self, *, attempt, payload, status_code,
                                        external_request_id, idempotency_key):
        from .provider_execution import ProviderExecution
        return ProviderExecution(self).commit_provider_raw_response_v3(
            attempt=attempt, payload=payload, status_code=status_code,
            external_request_id=external_request_id,
            idempotency_key=idempotency_key)

    def install_dependency_source(self, source: Any) -> None:
        """Attach an explicit HOST source provider for exact registered reads.

        The Registry does not choose/import a workflow's source library. Its
        consumer checks the resulting exact resource refs/envelopes normally.
        """
        if self.run_dependencies is not None:
            raise RegistryReadError(
                "mutable dependencies cannot replace frozen authority")
        if (not isinstance(getattr(source, "absolute_root", None), str)
                or not callable(getattr(source, "materialize", None))):
            raise TypeError("dependency source requires explicit HOST root/materialize contract")
        if (self.mutable_dependency_source is not None
                and self.mutable_dependency_source.absolute_root
                != source.absolute_root):
            raise RegistryReadError("mutable dependency root is immutable")
        self.mutable_dependency_source = source

    def _mutable_dependency_origin_refs(self) -> tuple[VersionRef, VersionRef]:
        def parse_meta(key: str, label: str) -> VersionRef:
            raw = self.event_store.get_meta(key)
            if raw is None:
                raise RegistryReadError(
                    f"mutable dependency {label} authority is missing")
            try:
                value = json.loads(raw)
                return VersionRef(
                    str(value["entity_type"]),
                    TypedId.parse(str(value["logical_id"])),
                    TypedId.parse(str(value["version_id"])))
            except Exception as exc:
                raise RegistryReadError(
                    f"mutable dependency {label} authority is malformed") from exc

        return (
            parse_meta("task_ref", "task"),
            parse_meta("bootstrap_command_ref", "bootstrap command"),
        )

    def _materialize_mutable_dependency(self, dependency_key: str) -> Any:
        if self.mutable_dependency_source is None:
            raise RegistryReadError("mutable dependency source is unavailable")
        task_ref, bootstrap_ref = self._mutable_dependency_origin_refs()
        return self.mutable_dependency_source.materialize(
            self, dependency_key=dependency_key,
            task_ref=task_ref, bootstrap_ref=bootstrap_ref)


    def dependency_resource_ref(self, dependency_key: str) -> Any:
        if self.run_dependencies is not None:
            raise RegistryReadError(
                "frozen dependencies are unreachable in the current runtime")
        ref = self._materialize_mutable_dependency(dependency_key)
        pending = getattr(self._mutable_dependency_reads, "pending", None)
        if pending is None:
            pending = {}
            self._mutable_dependency_reads.pending = pending
        pending[dependency_key] = ref
        return ref

    def dependency_bytes(self, dependency_key: str) -> bytes:
        """Read current-source or legacy frozen bytes through an exact ref."""

        if self.run_dependencies is None:
            pending = getattr(self._mutable_dependency_reads, "pending", None)
            resource_ref = (
                pending.pop(dependency_key, None) if pending is not None else None)
            if resource_ref is None:
                resource_ref = self._materialize_mutable_dependency(dependency_key)
            prepared = self.get_version(resource_ref.resource_version_id)
            payload = self.object_store.read_verified(prepared)
            if (prepared.object_type != "resource_version/v1"
                    or prepared.logical_id != resource_ref.resource_id
                    or prepared.version_id
                    != resource_ref.resource_version_id
                    or prepared.size != len(payload)):
                raise RegistryReadError(
                    f"mutable dependency resource differs: {dependency_key}")
            return payload
        raise RegistryReadError(
            "frozen dependency resources are historical and unreachable")

    def _publish_type_catalog(self) -> VersionRef:
        bundle = self.catalog.bundle()
        payload = json.dumps(
            bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")
        logical = TypedId.parse(self.event_store.get_or_create_meta(
            "type_catalog_logical_id", str(new_id("schema"))), expected="schema")
        version = TypedId.parse(self.event_store.get_or_create_meta(
            "type_catalog_version_id", str(new_id("resource_version"))),
            expected="resource_version")
        return self.publish_bytes(
            object_type="registry_type_catalog/v1", logical_id=logical,
            version_id=version, payload=payload,
            metadata=bundle,
            media_type="application/json",
            schema_ref="registry_v1/registry_type_catalog/v1",
            idempotency_key="registry-type-catalog:rpnh-v1")

    def begin(self, *, idempotency_key: str, task_round_id: TypedId | None = None,
              net_instance_id: TypedId | None = None) -> RegistryTransaction:
        return RegistryTransaction(
            event_store=self.event_store, object_store=self.object_store,
            task_id=self.task_id, branch_id=self.branch_id,
            task_round_id=task_round_id, net_instance_id=net_instance_id,
            idempotency_key=idempotency_key, writer_epoch=self.writer_epoch)

    def publish_bytes(self, *, object_type: str, logical_id: TypedId,
                      version_id: TypedId, payload: bytes, metadata: Mapping[str, Any],
                      media_type: str, schema_ref: str, idempotency_key: str,
                      producer_invocation_id: TypedId | None = None,
                      relations: tuple[TypedRelation, ...] = ()) -> VersionRef:
        tx = self.begin(idempotency_key=idempotency_key)
        tx.prewrite(object_type=object_type, logical_id=logical_id, version_id=version_id,
                    payload=payload, metadata=metadata, media_type=media_type,
                    schema_ref=schema_ref, producer_invocation_id=producer_invocation_id)
        for relation in relations:
            tx.relate(
                relation,
                producer_invocation_id=producer_invocation_id)
        tx.commit()
        return VersionRef(object_type, logical_id, version_id)

    def commit_dependency_partition_index(
            self, *, logical_id: TypedId, version_id: TypedId,
            payload: bytes, metadata: Mapping[str, Any]) -> VersionRef:
        """Idempotently commit one deterministic dependency partition."""

        existing = self.event_store.object_row(version_id)
        if existing is not None:
            prepared = self.get_version(version_id)
            stored_payload = self.object_store.read_registered(prepared)
            if (prepared.object_type != "dependency_partition_index/v1"
                    or prepared.logical_id != logical_id
                    or stored_payload != payload
                    or prepared.size != len(payload)
                    or prepared.metadata != dict(metadata)):
                raise RegistryConflict(
                    "dependency partition exact identity differs")
            self.object_store.verify_prepared(prepared)
            return VersionRef(
                "dependency_partition_index/v1", logical_id, version_id)
        return self.publish_bytes(
            object_type="dependency_partition_index/v1",
            logical_id=logical_id, version_id=version_id, payload=payload,
            metadata=metadata, media_type="application/json",
            schema_ref="registry_v1/dependency_partition_index/v1",
            idempotency_key=f"dependency-partition:{version_id}")

    def advance_dependency_root_index(
            self, *, expected_predecessor: VersionRef | None,
            root_ref: VersionRef, root_payload: bytes,
            root_metadata: Mapping[str, Any],
            initial_objects: tuple[
                tuple[str, VersionRef, Mapping[str, Any]], ...] = (),
    ) -> VersionRef:
        """Atomically publish one root successor after an exact CAS check."""

        tx = self.begin(idempotency_key=f"dependency-root:{root_ref.version_id}")
        tx._expect_dependency_root_predecessor(
            root_ref=root_ref, predecessor=expected_predecessor)
        for object_type, ref, metadata in initial_objects:
            tx.prewrite(
                object_type=object_type, logical_id=ref.entity_id,
                version_id=ref.version_id, payload=canonical_json(metadata),
                metadata=metadata, media_type="application/json",
                schema_ref=f"registry_v1/{object_type}")
        tx.prewrite(
            object_type="dependency_root_index/v1",
            logical_id=root_ref.entity_id, version_id=root_ref.version_id,
            payload=root_payload, metadata=root_metadata,
            media_type="application/json",
            schema_ref="registry_v1/dependency_root_index/v1")
        tx.commit()
        return root_ref

    def append_fact(self, event: PendingEvent, *, idempotency_key: str) -> None:
        tx = self.begin(idempotency_key=idempotency_key)
        tx.append(event)
        tx.commit()

    def create_recovery_manifest(self, contract: Mapping[str, Any]) -> VersionRef:
        # Inventory schema and budget buckets are explicit owner authority;
        # ordinary and terminal capacity remain separate Registry ledgers.
        required = {
            "budget_buckets", "protocol_versions", "host_resource_inventory_ref", "inventory_schema_id",
            "ordinary_global_cap", "terminal_quota", "task_total_hard_cap",
            "finalization_budget",
        }
        missing = sorted(required - set(contract))
        if missing:
            raise ValueError(f"task recovery manifest is incomplete: {missing}")
        from ..budgets import validate_budget_buckets
        validate_budget_buckets(contract["budget_buckets"])
        inventory_version = TypedId.parse(str(contract["host_resource_inventory_ref"]),
                                          expected="resource_version")
        inventory = self.event_store.object_row(inventory_version)
        if inventory is None or inventory["object_type"] != "resource_version/v1":
            raise ValueError(
                "host resource inventory must be an already-published exact version")
        try:
            inventory_metadata = json.loads(str(inventory["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "recovery manifest resources have malformed exact metadata") from exc
        inventory_schema_id = contract["inventory_schema_id"]
        if (not isinstance(inventory_schema_id, str) or not inventory_schema_id
                or inventory_metadata.get("content_schema_ref") != inventory_schema_id):
            raise ValueError(
                "recovery manifest inventory differs from its explicit schema identity")
        inventory_ref = self.get_version(inventory_version)
        from .content_schemas import hydrate_registered_content_schema
        from .publication import _resource_from_payload, _version_from_payload
        from .resources import ResourceVersionRef
        try:
            task_ref = _version_from_payload(inventory_metadata["task_ref"])
            task = self.get_version(task_ref.version_id)
            source = inventory_metadata["content_schema_authority_ref"]
            schema_ref = (_resource_from_payload(source) if "resource_id" in source
                          else _version_from_payload(source))
            if (task_ref.entity_type != "task/v1" or task_ref.entity_id != self.task_id
                    or task.object_type != task_ref.entity_type or task.logical_id != task_ref.entity_id
                    or inventory_metadata["branch_id"] != self.branch_id
                    or inventory_metadata["resource_id"] != str(inventory_ref.logical_id)
                    or inventory_metadata["resource_version_id"] != str(inventory_version)
                    or inventory_metadata["size"] != inventory_ref.size
                    or inventory_metadata["media_type"] != inventory_ref.media_type):
                raise ValueError("inventory task or immutable envelope differs")

            def read_schema(ref: ResourceVersionRef) -> bytes:
                prepared = self.get_version(ref.resource_version_id)
                if prepared.logical_id != ref.resource_id:
                    raise ValueError("inventory schema source is not exact")
                return self.object_store.read_registered(prepared)

            hydrate_registered_content_schema(self, schema_ref,
                schema_id=inventory_schema_id, fresh_reader=read_schema)
        except Exception as exc:
            raise ValueError(
                "recovery manifest inventory lacks exact task/schema authority") from exc
        try:
            payload = self.object_store.path_for_version(
                inventory_ref.version_id).read_bytes()
        except OSError as exc:
            raise ValueError(
                "host resource inventory exact bytes are unavailable") from exc
        if (inventory_ref.storage_locator
                != self.object_store.locator_for_version(inventory_ref.version_id)
                or len(payload) != inventory_ref.size
                or len(payload) != int(inventory["size"])):
            raise ValueError(
                "host resource inventory byte count differs from its exact envelope")
        if not isinstance(contract["protocol_versions"], (list, tuple)) or not contract["protocol_versions"]:
            raise ValueError("task recovery manifest must pin protocol versions")
        for name in ("ordinary_global_cap", "task_total_hard_cap"):
            value = contract[name]
            if (value is not None
                    and (isinstance(value, bool) or not isinstance(value, int)
                         or value < 1)):
                raise ValueError(
                    "ordinary/task budget bounds must be null or positive integers")
        if ((contract["ordinary_global_cap"] is None)
                != (contract["task_total_hard_cap"] is None)):
            raise ValueError(
                "ordinary/task budget bounds must both be bounded or both be null")
        for name in ("terminal_quota", "finalization_budget"):
            value = contract[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("terminal budget bounds must be nonnegative integers")
        manifest = {
            **dict(contract), "task_id": str(self.task_id), "branch_id": self.branch_id,
            "writer_fencing_epoch": self.writer_epoch,
        }
        version = TypedId.parse(self.event_store.get_or_create_meta(
            "task_recovery_manifest_version_id", str(new_id("resource_version"))),
            expected="resource_version")
        existing = self.event_store.object_row(version)
        if existing is not None:
            try:
                stored = json.loads(existing["metadata_json"])
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RegistryConflict(
                    "task recovery manifest exact version is malformed") from exc
            comparable_stored = {
                key: value for key, value in stored.items()
                if key != "writer_fencing_epoch"
            }
            comparable_requested = {
                key: value for key, value in manifest.items()
                if key != "writer_fencing_epoch"
            }
            if (existing["object_type"] != "task_recovery_manifest/v1"
                    or existing["logical_id"] != str(self.task_id)
                    or comparable_stored != comparable_requested):
                raise RegistryConflict(
                    "task recovery manifest exact version is immutable")
            return self.recovery_manifest_ref()
        ref = self.publish_bytes(
            object_type="task_recovery_manifest/v1", logical_id=self.task_id,
            version_id=version,
            payload=canonical_json(manifest),
            metadata=manifest, media_type="application/json",
            schema_ref="registry_v1/task_recovery_manifest/v1",
            idempotency_key=f"task-recovery-manifest:{version}")
        if ref != self.recovery_manifest_ref():
            raise RegistryConflict(
                "task recovery manifest exact pointer differs after publication")
        return ref

    def recovery_manifest_ref(self) -> VersionRef:
        """Return the frozen startup budget witness by its exact stored pointer.

        Total object cardinality and storage order are deliberately irrelevant:
        later Registry objects/facts may be appended throughout the run.
        """
        raw_version = self.event_store.get_meta(
            "task_recovery_manifest_version_id")
        if raw_version is None:
            raise RegistryReadError(
                "task recovery manifest exact pointer is missing")
        version = TypedId.parse(raw_version, expected="resource_version")
        row = self.event_store.object_row(version)
        if (row is None
                or row["object_type"] != "task_recovery_manifest/v1"
                or row["logical_id"] != str(self.task_id)):
            raise RegistryReadError(
                "task recovery manifest exact pointer is not published")
        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RegistryReadError(
                "task recovery manifest metadata is malformed") from exc
        self.catalog.validate_instance(
            "task_recovery_manifest/v1", category="object", instance=metadata)
        if (metadata.get("task_id") != str(self.task_id)
                or metadata.get("branch_id") != self.branch_id):
            raise RegistryReadError(
                "task recovery manifest belongs to another task or branch")
        with self.event_store.connect() as db:
            publication = db.execute(
                "SELECT t.writer_epoch,t.status FROM transactions t "
                "WHERE t.transaction_id=?", (row["transaction_id"],),
            ).fetchone()
        if (publication is None or publication["status"] != "committed"
                or int(metadata.get("writer_fencing_epoch", -1))
                != int(publication["writer_epoch"])):
            raise RegistryReadError(
                "task recovery manifest writer-fence provenance is invalid")
        return VersionRef("task_recovery_manifest/v1", self.task_id, version)

    def reconfigure_task_total_hard_cap(
            self, new_cap: int,
    ) -> VersionRef:
        """Append and select a new same-run model-call cap authority."""

        if (isinstance(new_cap, bool)
                or not isinstance(new_cap, int)
                or new_cap < 1):
            raise TypeError("task model-call cap must be a positive integer")
        current_ref = self.recovery_manifest_ref()
        current = self.get_version(current_ref.version_id)
        metadata = dict(current.metadata)
        old_cap = metadata.get("task_total_hard_cap")
        if (isinstance(old_cap, bool) or not isinstance(old_cap, int)
                or old_cap < 1):
            raise RegistryConflict(
                "current task cap authority is malformed")
        if new_cap == old_cap:
            return current_ref
        if self.event_store.task_model_call_terminal_phase_entered():
            raise RegistryConflict(
                "task cap cannot change after its terminal handoff")
        actual, extra = self.event_store.actual_model_call_counts()
        if new_cap < actual + extra:
            raise RegistryConflict(
                "task cap cannot be lower than recorded model calls")
        replacement_metadata = {
            **metadata,
            "task_total_hard_cap": new_cap,
            "writer_fencing_epoch": self.writer_epoch,
        }
        self.catalog.validate_instance(
            "task_recovery_manifest/v1", category="object",
            instance=replacement_metadata)
        replacement_ref = self.publish_bytes(
            object_type="task_recovery_manifest/v1",
            logical_id=self.task_id,
            version_id=new_id("resource_version"),
            payload=canonical_json(replacement_metadata),
            metadata=replacement_metadata,
            media_type="application/json",
            schema_ref="registry_v1/task_recovery_manifest/v1",
            idempotency_key=(
                f"task-recovery-manifest-cap:{current_ref.version_id}:"
                f"{new_cap}"),
        )
        self.event_store.compare_and_set_task_recovery_manifest_pointer(
            expected_ref=current_ref,
            replacement_ref=replacement_ref,
            writer_epoch=self.writer_epoch,
        )
        if self.recovery_manifest_ref() != replacement_ref:
            raise RegistryConflict(
                "configured task cap did not become current")
        return replacement_ref

    def get_version(self, version_id: TypedId) -> PreparedObject:
        row = self.event_store.object_row(version_id)
        if row is None:
            raise RegistryReadError(f"unknown immutable version: {version_id}")
        producer = (TypedId.parse(row["producer_invocation_id"], expected="invocation")
                    if row["producer_invocation_id"] else None)
        return PreparedObject(
            object_type=row["object_type"],
            logical_id=TypedId.parse(row["logical_id"]),
            version_id=TypedId.parse(row["version_id"]),
            size=row["size"], media_type=row["media_type"], schema_ref=row["schema_ref"],
            producer_invocation_id=producer, storage_locator=row["storage_locator"],
            metadata=json.loads(row["metadata_json"]))

    def verify_registered_content_schema_ref(
            self, content_schema_ref: VersionRef | Any, *,
            schema_document_ref: VersionRef,
    ) -> Any:
        """Verify one exact catalog/application schema without ambient lookup."""
        from .content_schemas import verify_registered_content_schema
        from .resources import ResourceVersionRef
        fresh_reader = None
        try:
            prepared = self.get_version(schema_document_ref.version_id)
        except RegistryReadError:
            prepared = None
        if prepared is not None and prepared.object_type == "resource_version/v1":
            if not isinstance(
                    prepared.metadata.get("reference_provenance"), Mapping):
                raise RegistryReadError(
                    "current schema resource lacks reference provenance")
            def fresh_reader(ref: ResourceVersionRef) -> bytes:
                return self.object_store.read_registered(
                    self.get_version(ref.resource_version_id))
        return verify_registered_content_schema(
            self, content_schema_ref,
            schema_document_ref=schema_document_ref,
            fresh_reader=fresh_reader)

    def _logical_object(self, logical_id: TypedId, object_type: str) -> Mapping[str, Any]:
        rows = [row for row in self.event_store.object_rows()
                if row["logical_id"] == str(logical_id)
                and row["object_type"] == object_type]
        if len(rows) != 1:
            raise RegistryReadError(
                f"expected one registered {object_type} for {logical_id}; found {len(rows)}")
        return rows[0]

    def rotate_writer(self) -> int:
        replacement = self.event_store.rotate_writer(expected_epoch=self.writer_epoch)
        self.writer_epoch = replacement
        return replacement
