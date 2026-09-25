"""Secret-safe generic bearer credential metadata and FD resolution."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing import reduction
import os
import re
from typing import Iterator, Mapping


_MAX_CREDENTIAL_BYTES = 4096
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_BEARER_HEADERS = ("Authorization", "Content-Type")
_BEARER_RECIPE = "bearer_json/v1"


class CredentialMetadataError(ValueError):
    """Credential metadata does not match the generic FD transport policy."""


class CredentialResolutionError(RuntimeError):
    """A transferred credential could not be resolved without exposing it."""

    code = "credential_resolution_failed"

    def __init__(self) -> None:
        super().__init__("provider credential resolution failed")


class CredentialFDUnavailable(CredentialResolutionError):
    code = "credential_fd_unavailable"


class CredentialMaterialMalformed(CredentialResolutionError):
    code = "credential_material_malformed"


@dataclass(frozen=True, slots=True)
class InheritedCredentialFD:
    """One bounded source descriptor for explicit one-shot process transfer."""

    fd: int
    kind: str = "inherited_fd"
    max_bytes: int = _MAX_CREDENTIAL_BYTES
    trailing_newline: str = "strip_one_lf"

    def __post_init__(self) -> None:
        if (isinstance(self.fd, bool) or not isinstance(self.fd, int)
                or self.fd < 3
                or self.kind != "inherited_fd"
                or self.max_bytes != _MAX_CREDENTIAL_BYTES
                or self.trailing_newline != "strip_one_lf"):
            raise CredentialMetadataError(
                "credential delivery metadata violates the inherited-FD policy")


@dataclass(frozen=True, slots=True)
class ProviderCredentialBinding:
    """Generic bearer credential metadata; never contains credential bytes."""

    logical_ref: str
    allowed_header_names: tuple[str, ...]
    header_recipe: str
    delivery: InheritedCredentialFD

    def __post_init__(self) -> None:
        if (not isinstance(self.logical_ref, str) or not self.logical_ref
                or self.logical_ref != self.logical_ref.strip()
                or any(ord(character) < 32 or ord(character) == 127
                       for character in self.logical_ref)
                or self.allowed_header_names != _BEARER_HEADERS
                or any(_HEADER_NAME.fullmatch(name) is None
                       for name in self.allowed_header_names)
                or self.header_recipe != _BEARER_RECIPE
                or not isinstance(self.delivery, InheritedCredentialFD)):
            raise CredentialMetadataError(
                "provider credential metadata is not the generic bearer profile")


def provider_credential_binding(
        logical_ref: str, *, fd: int,
) -> ProviderCredentialBinding:
    """Build a generic bearer binding for any user-declared logical reference."""
    return ProviderCredentialBinding(
        logical_ref=logical_ref,
        allowed_header_names=_BEARER_HEADERS,
        header_recipe=_BEARER_RECIPE,
        delivery=InheritedCredentialFD(fd=fd),
    )


def credential_binding_document(
        binding: ProviderCredentialBinding,
) -> dict[str, object]:
    if not isinstance(binding, ProviderCredentialBinding):
        raise CredentialMetadataError(
            "provider credential document requires a typed binding")
    return {
        "logical_ref": binding.logical_ref,
        "allowed_header_names": list(binding.allowed_header_names),
        "header_recipe": binding.header_recipe,
        "delivery": {
            "kind": binding.delivery.kind,
            "fd": binding.delivery.fd,
            "max_bytes": binding.delivery.max_bytes,
            "trailing_newline": binding.delivery.trailing_newline,
        },
    }


def credential_binding_from_document(value: object) -> ProviderCredentialBinding:
    if not isinstance(value, Mapping):
        raise CredentialMetadataError(
            "provider credential metadata is not an object")
    delivery = value.get("delivery")
    allowed_names = value.get("allowed_header_names")
    if (set(value) != {
            "logical_ref", "allowed_header_names", "header_recipe", "delivery"}
            or not isinstance(delivery, Mapping)
            or set(delivery) != {
                "kind", "fd", "max_bytes", "trailing_newline"}
            or not isinstance(allowed_names, list)
            or any(not isinstance(name, str) for name in allowed_names)):
        raise CredentialMetadataError(
            "provider credential metadata is not the closed document shape")
    return ProviderCredentialBinding(
        logical_ref=value.get("logical_ref"),
        allowed_header_names=tuple(allowed_names),
        header_recipe=value.get("header_recipe"),
        delivery=InheritedCredentialFD(
            fd=delivery.get("fd"),
            kind=delivery.get("kind"),
            max_bytes=delivery.get("max_bytes"),
            trailing_newline=delivery.get("trailing_newline"),
        ),
    )


def validate_credential_binding(
        binding: ProviderCredentialBinding, *, logical_ref: str,
        allowed_header_names: tuple[str, ...], header_recipe: str,
) -> None:
    if (not isinstance(binding, ProviderCredentialBinding)
            or binding.logical_ref != logical_ref
            or binding.allowed_header_names != allowed_header_names
            or binding.header_recipe != header_recipe):
        raise CredentialMetadataError(
            "provider credential metadata differs from the configured profile")


def receive_credential_fd(channel: object) -> int:
    """Receive exactly one credential capability over a private local channel."""
    received_fd: int | None = None
    try:
        received_fd = reduction.recv_handle(channel)
        if (isinstance(received_fd, bool) or not isinstance(received_fd, int)
                or received_fd < 3):
            raise CredentialFDUnavailable()
        os.set_inheritable(received_fd, False)
        os.fstat(received_fd)
        return received_fd
    except CredentialResolutionError:
        if received_fd is not None:
            try:
                os.close(received_fd)
            except OSError:
                pass
        raise
    except (EOFError, OSError, RuntimeError, TypeError, ValueError):
        if received_fd is not None:
            try:
                os.close(received_fd)
            except OSError:
                pass
        raise CredentialFDUnavailable() from None


def _read_transferred_secret(
        delivery: InheritedCredentialFD, transferred_fd: int,
) -> bytearray:
    buffer = bytearray(delivery.max_bytes + 1)
    view = memoryview(buffer)
    count = 0
    try:
        if (isinstance(transferred_fd, bool)
                or not isinstance(transferred_fd, int)
                or transferred_fd < 3
                or os.get_inheritable(transferred_fd)):
            raise CredentialFDUnavailable()
        while count <= delivery.max_bytes:
            read_count = os.readv(transferred_fd, (view[count:],))
            if read_count == 0:
                break
            count += read_count
            if count > delivery.max_bytes:
                raise CredentialMaterialMalformed()
    except CredentialResolutionError:
        buffer[:] = b"\x00" * len(buffer)
        raise
    except (OSError, ValueError):
        buffer[:] = b"\x00" * len(buffer)
        raise CredentialFDUnavailable() from None
    finally:
        view.release()
        try:
            os.close(transferred_fd)
        except OSError:
            pass
    del buffer[count:]
    if not buffer:
        raise CredentialMaterialMalformed()
    if buffer.endswith(b"\n"):
        del buffer[-1:]
    if (not buffer or b"\r" in buffer or b"\n" in buffer
            or any(value < 33 or value > 126 for value in buffer)):
        buffer[:] = b"\x00" * len(buffer)
        raise CredentialMaterialMalformed()
    return buffer


@contextmanager
def resolved_provider_headers(
        binding: ProviderCredentialBinding, *, source_fd: int,
        transferred_fd: int, logical_ref: str,
        allowed_header_names: tuple[str, ...], header_recipe: str,
) -> Iterator[dict[str, str]]:
    """Resolve one generic bearer credential at the immediate transport boundary."""
    try:
        if (isinstance(source_fd, bool) or not isinstance(source_fd, int)
                or source_fd != binding.delivery.fd):
            raise CredentialMetadataError(
                "source credential FD differs from persisted delivery metadata")
        validate_credential_binding(
            binding,
            logical_ref=logical_ref,
            allowed_header_names=allowed_header_names,
            header_recipe=header_recipe,
        )
    except CredentialMetadataError:
        try:
            os.close(transferred_fd)
        except (OSError, TypeError):
            pass
        raise CredentialResolutionError() from None
    secret_buffer = _read_transferred_secret(binding.delivery, transferred_fd)
    headers: dict[str, str] = {}
    secret_text: str | None = None
    try:
        try:
            secret_text = secret_buffer.decode("utf-8")
        except UnicodeDecodeError:
            raise CredentialMaterialMalformed() from None
        headers = {
            "Authorization": f"Bearer {secret_text}",
            "Content-Type": "application/json",
        }
        yield headers
    finally:
        headers.clear()
        secret_text = None
        secret_buffer[:] = b"\x00" * len(secret_buffer)
        del secret_buffer


__all__ = [
    "CredentialFDUnavailable",
    "CredentialMaterialMalformed",
    "CredentialMetadataError",
    "CredentialResolutionError",
    "InheritedCredentialFD",
    "ProviderCredentialBinding",
    "credential_binding_document",
    "credential_binding_from_document",
    "provider_credential_binding",
    "receive_credential_fd",
    "resolved_provider_headers",
    "validate_credential_binding",
]
