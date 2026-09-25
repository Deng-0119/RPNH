"""Durable append-only audit private to LLM adapters."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
from typing import Mapping

from cpn.rpnh.llm_contracts import LLMCallAttempt


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PrivateAttemptAudit:
    """Reserve attempt identities durably before generation submission."""

    def __init__(self, destination_run_root: Path) -> None:
        if not isinstance(destination_run_root, Path):
            raise TypeError("adapter audit requires a pathlib.Path run root")
        self.path = (
            destination_run_root.resolve()
            / "adapter-private" / "llm-attempts.jsonl")
        self._seen: set[str] = set()
        self._seen_external_attempts: set[str] = set()

    @staticmethod
    def _attempt_identity(attempt: LLMCallAttempt) -> str:
        if not isinstance(attempt, LLMCallAttempt):
            raise TypeError("adapter audit requires LLMCallAttempt")
        return str(attempt.attempt_ref.version_id)

    def has_seen(self, attempt: LLMCallAttempt) -> bool:
        identity = self._attempt_identity(attempt)
        descriptor = self._open_locked()
        try:
            return identity in self._seen
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _mkdir_durable(cls, path: Path) -> None:
        missing: list[Path] = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            if cursor.parent == cursor:
                break
            cursor = cursor.parent
        if not cursor.is_dir():
            raise RuntimeError(
                "adapter-private audit parent is not a directory")
        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                if not directory.is_dir():
                    raise RuntimeError(
                        "adapter-private audit parent is not a directory")
            cls._fsync_directory(directory.parent)
            cls._fsync_directory(directory)

    def _fsync_parent(self) -> None:
        self._fsync_directory(self.path.parent)

    def _open_locked(self) -> int:
        """Create/recover the audit under its exclusive file lock."""
        self._mkdir_durable(self.path.parent)
        descriptor = os.open(
            self.path, os.O_APPEND | os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            seen, external_attempts = self._recover_locked(descriptor)
            self._seen.update(seen)
            self._seen_external_attempts.update(external_attempts)
            os.fsync(descriptor)
            self._fsync_parent()
        except BaseException:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _parse_committed(payload: bytes) -> tuple[set[str], set[str]]:
        seen: set[str] = set()
        external_attempts: set[str] = set()
        if not payload:
            return seen, external_attempts
        try:
            lines = payload[:-1].split(b"\n")
            for line in lines:
                if not line:
                    raise RuntimeError(
                        "adapter-private attempt audit is unreadable")
                value = json.loads(line)
                if (not isinstance(value, Mapping)
                        or not isinstance(value.get("attempt_ref"), str)
                        or not value["attempt_ref"]
                        or not isinstance(value.get("lifecycle"), str)
                        or not value["lifecycle"]):
                    raise RuntimeError(
                        "adapter-private attempt audit is unreadable")
                lifecycle = value["lifecycle"]
                if lifecycle in {
                        "generation_submission_started",
                        "external_provider_invocation_started"}:
                    seen.add(str(value["attempt_ref"]))
                    invocation_ref = value.get("invocation_ref")
                    if (lifecycle == "external_provider_invocation_started"
                            and isinstance(invocation_ref, str)
                            and invocation_ref):
                        external_attempts.add(str(value["attempt_ref"]))
        except RuntimeError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "adapter-private attempt audit is unreadable") from exc
        return seen, external_attempts

    @staticmethod
    def _read_locked(descriptor: int) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    @classmethod
    def _recover_locked(
            cls, descriptor: int,
    ) -> tuple[set[str], set[str]]:
        payload = cls._read_locked(descriptor)
        if payload and not payload.endswith(b"\n"):
            committed_length = payload.rfind(b"\n") + 1
            os.ftruncate(descriptor, committed_length)
            os.fsync(descriptor)
            payload = payload[:committed_length]
        return cls._parse_committed(payload)

    @staticmethod
    def _write_locked(descriptor: int, document: Mapping[str, object]) -> None:
        payload = json.dumps(
            dict(document), ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])

    def append(self, document: Mapping[str, object]) -> None:
        descriptor = self._open_locked()
        try:
            self._write_locked(descriptor, document)
            os.fsync(descriptor)
            self._fsync_parent()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def latest_selected_external_route_id(
            self, *, model_condition: str,
    ) -> str | None:
        """Return the last durable selected route without creating an audit."""
        if (not isinstance(model_condition, str) or not model_condition
                or model_condition != model_condition.strip()):
            raise TypeError("adapter audit model condition is invalid")
        if not self.path.is_file():
            return None
        descriptor = self._open_locked()
        try:
            payload = self._read_locked(descriptor)
            for line in reversed(payload.splitlines()):
                try:
                    value = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        "adapter-private attempt audit is unreadable") from exc
                if (not isinstance(value, Mapping)
                        or value.get("model_condition") != model_condition):
                    continue
                if (value.get("lifecycle")
                        == "external_provider_invocation_finished"
                        and value.get("outcome") == "response_returned"):
                    detail = value.get("detail")
                    route = (
                        detail.get("route")
                        if isinstance(detail, Mapping) else None)
                    route_id = (
                        route.get("route_id")
                        if isinstance(route, Mapping) else None)
                else:
                    continue
                if (not isinstance(route_id, str) or not route_id
                        or route_id != route_id.strip()):
                    raise RuntimeError(
                        "selected external route audit is unreadable")
                return route_id
            return None
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def reserve_submission(
            self, attempt: LLMCallAttempt, *, route: Mapping[str, object],
    ) -> bool:
        identity = self._attempt_identity(attempt)
        descriptor = self._open_locked()
        try:
            document = {
                "attempt_ref": identity,
                "invocation_ref": str(attempt.invocation_ref.version_id),
                "attempt_ordinal": attempt.attempt_ordinal,
                **dict(route),
            }
            if identity in self._seen:
                document.update({
                    "lifecycle": "duplicate_submission_prevented",
                    "outcome": "no_submission",
                    "detail": {
                        "failure_category": "adapter_not_submitted",
                        "retry_eligible": False,
                        "recovery_disposition": "block_no_retry",
                    },
                    "recorded_at_utc": _timestamp(),
                })
                allowed = False
            else:
                document.update({
                    "lifecycle": "generation_submission_started",
                    "outcome": "pending",
                    "submitted_at_utc": _timestamp(),
                })
                allowed = True
            self._write_locked(descriptor, document)
            os.fsync(descriptor)
            self._fsync_parent()
            if allowed:
                self._seen.add(identity)
            return allowed
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def reserve_external_invocation(self, attempt: LLMCallAttempt) -> bool:
        """Reserve one complete provider chain for a neutral invocation."""
        attempt_identity = self._attempt_identity(attempt)
        descriptor = self._open_locked()
        try:
            invocation_identity = str(attempt.invocation_ref.version_id)
            document: dict[str, object] = {
                "attempt_ref": attempt_identity,
                "invocation_ref": invocation_identity,
                "attempt_ordinal": attempt.attempt_ordinal,
                "model_condition": attempt.model_condition,
            }
            if attempt_identity in self._seen_external_attempts:
                document.update({
                    "lifecycle": "duplicate_external_invocation_prevented",
                    "outcome": "no_submission",
                    "detail": {
                        "failure_category": "adapter_not_submitted",
                        "retry_eligible": False,
                        "recovery_disposition": "block_no_retry",
                    },
                    "recorded_at_utc": _timestamp(),
                })
                allowed = False
            else:
                document.update({
                    "lifecycle": "external_provider_invocation_started",
                    "outcome": "pending",
                    "started_at_utc": _timestamp(),
                })
                allowed = True
            self._write_locked(descriptor, document)
            os.fsync(descriptor)
            self._fsync_parent()
            if allowed:
                self._seen.add(attempt_identity)
                self._seen_external_attempts.add(attempt_identity)
            return allowed
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def provider_attempt_started(
            self, attempt: LLMCallAttempt, *, route: Mapping[str, object],
            provider_attempt_id: str, provider_request_id: str,
            call_kind: str, call_ordinal: int,
            prior_provider_attempt_id: str | None,
    ) -> None:
        document: dict[str, object] = {
            "attempt_ref": self._attempt_identity(attempt),
            "invocation_ref": str(attempt.invocation_ref.version_id),
            "attempt_ordinal": attempt.attempt_ordinal,
            "model_condition": attempt.model_condition,
            "provider_attempt_id": provider_attempt_id,
            "provider_request_id": provider_request_id,
            "call_kind": call_kind,
            "call_ordinal": call_ordinal,
            **dict(route),
            "lifecycle": "provider_attempt_started",
            "outcome": "pending",
            "started_at_utc": _timestamp(),
        }
        if prior_provider_attempt_id is not None:
            document["prior_provider_attempt_id"] = prior_provider_attempt_id
        self.append(document)

    def provider_progress(
            self, attempt: LLMCallAttempt, *, route: Mapping[str, object],
            provider_attempt_id: str, provider_request_id: str,
            call_kind: str, phase: str,
            detail: Mapping[str, object] | None = None,
    ) -> None:
        document: dict[str, object] = {
            "attempt_ref": self._attempt_identity(attempt),
            "invocation_ref": str(attempt.invocation_ref.version_id),
            "attempt_ordinal": attempt.attempt_ordinal,
            "model_condition": attempt.model_condition,
            "provider_attempt_id": provider_attempt_id,
            "provider_request_id": provider_request_id,
            "call_kind": call_kind,
            **dict(route),
            "lifecycle": "provider_transport_progress",
            "transport_phase": phase,
            "recorded_at_utc": _timestamp(),
        }
        if detail:
            document["detail"] = dict(detail)
        self.append(document)

    def provider_attempt_finished(
            self, attempt: LLMCallAttempt, *, route: Mapping[str, object],
            provider_attempt_id: str, provider_request_id: str,
            call_kind: str, outcome: str,
            external_request_id: str | None = None,
            detail: Mapping[str, object] | None = None,
    ) -> None:
        document: dict[str, object] = {
            "attempt_ref": self._attempt_identity(attempt),
            "invocation_ref": str(attempt.invocation_ref.version_id),
            "attempt_ordinal": attempt.attempt_ordinal,
            "model_condition": attempt.model_condition,
            "provider_attempt_id": provider_attempt_id,
            "provider_request_id": provider_request_id,
            "call_kind": call_kind,
            **dict(route),
            "lifecycle": "provider_attempt_finished",
            "outcome": outcome,
            "completed_at_utc": _timestamp(),
        }
        if external_request_id is not None:
            document["external_request_id"] = external_request_id
        if detail:
            document["detail"] = dict(detail)
        self.append(document)

    def finish_external_invocation(
            self, attempt: LLMCallAttempt, *, outcome: str,
            detail: Mapping[str, object] | None = None,
    ) -> None:
        document: dict[str, object] = {
            "attempt_ref": self._attempt_identity(attempt),
            "invocation_ref": str(attempt.invocation_ref.version_id),
            "attempt_ordinal": attempt.attempt_ordinal,
            "model_condition": attempt.model_condition,
            "lifecycle": "external_provider_invocation_finished",
            "outcome": outcome,
            "completed_at_utc": _timestamp(),
        }
        if detail:
            document["detail"] = dict(detail)
        self.append(document)

    def finish(
            self, attempt: LLMCallAttempt, *, route: Mapping[str, object],
            outcome: str, external_request_id: str | None = None,
            detail: Mapping[str, object] | None = None,
    ) -> None:
        document: dict[str, object] = {
            "attempt_ref": self._attempt_identity(attempt),
            "invocation_ref": str(attempt.invocation_ref.version_id),
            "attempt_ordinal": attempt.attempt_ordinal,
            **dict(route),
            "lifecycle": "generation_submission_finished",
            "outcome": outcome,
            "completed_at_utc": _timestamp(),
        }
        if external_request_id is not None:
            document["external_request_id"] = external_request_id
        if detail:
            document["detail"] = dict(detail)
        self.append(document)


__all__ = ["PrivateAttemptAudit"]
