"""Exact-version-addressed immutable payload storage."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from .identities import TypedId
from .models import PreparedObject
from .schema_catalog import SchemaCatalog, canonical_json


class ObjectIntegrityError(RuntimeError):
    pass


class ObjectStore:
    _CHUNK_SIZE = 1024 * 1024

    def __init__(self, root: Path | str, catalog: SchemaCatalog, *,
                 read_only: bool = False) -> None:
        self.root = Path(root)
        self.read_only = bool(read_only)
        if self.read_only:
            if not self.root.is_dir():
                raise ObjectIntegrityError("read-only immutable object root is missing")
        else:
            self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog

    def path_for_version(self, version_id: TypedId) -> Path:
        if not isinstance(version_id, TypedId):
            raise ObjectIntegrityError("payload path requires an exact version id")
        return self.root / version_id.kind / version_id.value

    @staticmethod
    def locator_for_version(version_id: TypedId) -> str:
        if not isinstance(version_id, TypedId):
            raise ObjectIntegrityError("payload locator requires an exact version id")
        return f"registry-object:{version_id}"

    def _publish_payload(
            self, payload: bytes, version_id: TypedId) -> None:
        destination = self.path_for_version(version_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix="prewrite-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._publish_temporary_exact(
                Path(temporary), destination)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def prewrite(self, *, object_type: str, logical_id: TypedId,
                 version_id: TypedId, payload: bytes,
                 metadata: Mapping[str, Any], media_type: str,
                 schema_ref: str,
                 producer_invocation_id: TypedId | None = None) -> PreparedObject:
        if self.read_only:
            raise ObjectIntegrityError("read-only immutable object store cannot prewrite")
        definition = self.catalog.require(object_type, category="object")
        if not isinstance(payload, bytes):
            raise TypeError("object payload must be bytes")
        if not media_type or not schema_ref:
            raise ValueError("media_type and schema_ref are required")
        if schema_ref != definition.schema_ref:
            raise ValueError(
                f"schema ref {schema_ref!r} does not match registered type {object_type!r}")
        self.catalog.validate_instance(
            object_type, category="object", instance=dict(metadata))
        prepared = PreparedObject(
            object_type=object_type, logical_id=logical_id, version_id=version_id,
            size=len(payload),
            media_type=media_type, schema_ref=schema_ref,
            producer_invocation_id=producer_invocation_id,
            storage_locator=self.locator_for_version(version_id),
            metadata=dict(metadata))
        self.validate_envelope(prepared)
        self._publish_payload(payload, version_id)
        return prepared

    def prewrite_with_metadata_factory(
            self, *, object_type: str, logical_id: TypedId,
            version_id: TypedId, payload: bytes,
            metadata_factory: Callable[[int], Mapping[str, Any]],
            media_type: str, schema_ref: str,
            producer_invocation_id: TypedId | None = None) -> PreparedObject:
        """Prewrite bytes whose metadata records their exact byte count."""

        if self.read_only:
            raise ObjectIntegrityError(
                "read-only immutable object store cannot prewrite")
        definition = self.catalog.require(object_type, category="object")
        if not isinstance(payload, bytes):
            raise TypeError("object payload must be bytes")
        if not callable(metadata_factory):
            raise TypeError("object metadata factory must be callable")
        if not media_type or not schema_ref:
            raise ValueError("media_type and schema_ref are required")
        if schema_ref != definition.schema_ref:
            raise ValueError(
                f"schema ref {schema_ref!r} does not match registered type "
                f"{object_type!r}")
        size = len(payload)
        metadata = metadata_factory(size)
        if not isinstance(metadata, Mapping):
            raise TypeError("object metadata factory must return a mapping")
        frozen_metadata = dict(metadata)
        self.catalog.validate_instance(
            object_type, category="object", instance=frozen_metadata)
        prepared = PreparedObject(
            object_type=object_type, logical_id=logical_id,
            version_id=version_id, size=size,
            media_type=media_type, schema_ref=schema_ref,
            producer_invocation_id=producer_invocation_id,
            storage_locator=self.locator_for_version(version_id),
            metadata=frozen_metadata)
        self.validate_envelope(prepared)
        self._publish_payload(payload, version_id)
        return prepared

    def prewrite_descriptor(
            self, *, object_type: str, logical_id: TypedId,
            version_id: TypedId, descriptor: int,
            expected_size: int, metadata: Mapping[str, Any], media_type: str,
            schema_ref: str,
            producer_invocation_id: TypedId | None = None) -> PreparedObject:
        """Prewrite an already-pinned descriptor without retaining its bytes."""

        if self.read_only:
            raise ObjectIntegrityError("read-only immutable object store cannot prewrite")

        definition = self.catalog.require(object_type, category="object")
        if (not isinstance(descriptor, int) or descriptor < 0
                or isinstance(expected_size, bool)
                or not isinstance(expected_size, int) or expected_size < 0):
            raise ObjectIntegrityError("streamed object contract is invalid")
        if not media_type or not schema_ref:
            raise ValueError("media_type and schema_ref are required")
        if schema_ref != definition.schema_ref:
            raise ValueError(
                f"schema ref {schema_ref!r} does not match registered type {object_type!r}")
        self.catalog.validate_instance(
            object_type, category="object", instance=dict(metadata))
        prepared = PreparedObject(
            object_type=object_type, logical_id=logical_id,
            version_id=version_id, size=expected_size,
            media_type=media_type, schema_ref=schema_ref,
            producer_invocation_id=producer_invocation_id,
            storage_locator=self.locator_for_version(version_id),
            metadata=dict(metadata))
        self.validate_envelope(prepared)
        destination = self.path_for_version(version_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix="prewrite-stream-", dir=destination.parent)
        total = 0
        try:
            with os.fdopen(fd, "wb") as stream:
                while True:
                    chunk = os.read(descriptor, self._CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if total != expected_size:
                raise ObjectIntegrityError(
                    "streamed object differs from its declared byte count")
            self._publish_temporary_exact(
                Path(temporary), destination)
            return prepared
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _publish_temporary_exact(
            self, temporary: Path, destination: Path) -> None:
        """Atomically publish once, accepting only exact-byte replay.

        The temporary file is in the destination directory, so a hard link is
        one atomic no-clobber publication on the same filesystem.  A competing
        publisher either wins that link or observes its exact bytes here; no
        participant can overwrite another exact version.
        """

        try:
            os.link(temporary, destination)
        except FileExistsError:
            if not self._paths_have_same_bytes(destination, temporary):
                raise ObjectIntegrityError(
                    f"immutable version collision at {destination}")
        # Persist both the destination link and removal of this publisher's
        # temporary alias.  Exact-byte concurrent losers also perform the
        # directory sync, so they cannot acknowledge a winner's unpersisted
        # link merely because it became visible first.
        temporary.unlink()
        self._fsync_directory(destination.parent)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        directory_fd = os.open(directory, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _paths_have_same_bytes(self, left: Path, right: Path) -> bool:
        """Compare two payload files without retaining either payload in memory."""

        try:
            with left.open("rb") as left_stream, right.open("rb") as right_stream:
                while True:
                    left_chunk = left_stream.read(self._CHUNK_SIZE)
                    right_chunk = right_stream.read(self._CHUNK_SIZE)
                    if left_chunk != right_chunk:
                        return False
                    if not left_chunk:
                        return True
        except OSError as exc:
            raise ObjectIntegrityError(
                "cannot compare immutable payloads for exact-version reuse") from exc

    def validate_envelope(self, prepared: PreparedObject) -> None:
        definition = self.catalog.require(prepared.object_type, category="object")
        if prepared.schema_ref != definition.schema_ref:
            raise ObjectIntegrityError(
                f"schema ref does not match registered object type {prepared.object_type!r}")
        envelope = {
            "object_type": prepared.object_type,
            "logical_id": str(prepared.logical_id),
            "version_id": str(prepared.version_id),
            "size": prepared.size,
            "media_type": prepared.media_type,
            "schema_ref": prepared.schema_ref,
            "producer_invocation_id": (
                str(prepared.producer_invocation_id)
                if prepared.producer_invocation_id is not None else None
            ),
            "storage_locator": prepared.storage_locator,
            "metadata": dict(prepared.metadata),
        }
        self.catalog.validate_schema_ref("registry_v1/object_envelope/v1", envelope)

    def read_verified(self, prepared: PreparedObject) -> bytes:
        self.validate_envelope(prepared)
        if prepared.storage_locator != self.locator_for_version(prepared.version_id):
            raise ObjectIntegrityError("unsupported exact-version storage locator")
        path = self.path_for_version(prepared.version_id)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ObjectIntegrityError(
                f"missing immutable payload {prepared.version_id}") from exc
        if len(payload) != prepared.size:
            raise ObjectIntegrityError(f"immutable payload verification failed: {prepared.version_id}")
        return payload

    def read_registered(self, prepared: PreparedObject, *,
                        max_bytes: int | None = None) -> bytes:
        """Read exact registered bytes, with an optional physical allocation bound."""

        if max_bytes is not None:
            if type(max_bytes) is not int or max_bytes < 0:
                raise TypeError("registered read bound must be a nonnegative integer")
            if prepared.size > max_bytes:
                raise ObjectIntegrityError("registered payload exceeds reader byte bound")

        self.validate_envelope(prepared)
        expected_locator = self.locator_for_version(prepared.version_id)
        if prepared.storage_locator != expected_locator:
            raise ObjectIntegrityError(
                "registered object locator is not in exact canonical form")
        path = self.path_for_version(prepared.version_id)
        try:
            if max_bytes is None:
                payload = path.read_bytes()
            else:
                with path.open("rb") as stream:
                    payload = stream.read(max_bytes + 1)
        except OSError as exc:
            raise ObjectIntegrityError(
                f"missing registered payload {prepared.version_id}") from exc
        if not isinstance(payload, bytes) or len(payload) != prepared.size:
            raise ObjectIntegrityError(
                f"registered payload size differs: {prepared.version_id}")
        return payload

    def verify_prepared(self, prepared: PreparedObject) -> None:
        """Verify an immutable object using bounded reads."""

        self.validate_envelope(prepared)
        if prepared.storage_locator != self.locator_for_version(prepared.version_id):
            raise ObjectIntegrityError("unsupported exact-version storage locator")
        self._verify_path(
            self.path_for_version(prepared.version_id), size=prepared.size)

    def _verify_path(self, path: Path, *, size: int) -> None:
        total = 0
        try:
            with path.open("rb") as stream:
                while True:
                    chunk = stream.read(self._CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
        except OSError as exc:
            raise ObjectIntegrityError(f"missing immutable payload {path}") from exc
        if total != size:
            raise ObjectIntegrityError("immutable payload verification failed")

    def verify_locator(
            self, locator: str, *, version_id: TypedId, size: int) -> None:
        if locator != self.locator_for_version(version_id):
            raise ObjectIntegrityError("locator and exact version disagree")
        self._verify_path(self.path_for_version(version_id), size=size)
