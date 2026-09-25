"""Credential-to-header binding for user-configured HTTPS providers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import re
from typing import Iterator, Mapping


_MAX_CREDENTIAL_BYTES = 4096
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_FORBIDDEN_CREDENTIAL_HEADERS = {
    "connection", "content-length", "content-type", "host",
    "proxy-authorization", "transfer-encoding",
}


class CredentialMetadataError(ValueError):
    """Credential metadata is malformed or unsafe for an HTTP request."""


class CredentialResolutionError(RuntimeError):
    """The adapter could not resolve one credential without exposing it."""

    code = "credential_resolution_failed"

    def __init__(self) -> None:
        super().__init__("external adapter credential resolution failed")


class CredentialMaterialMalformed(CredentialResolutionError):
    code = "credential_material_malformed"


@dataclass(frozen=True, slots=True)
class ProviderCredentialBinding:
    """User-declared environment-to-header mapping; never credential bytes."""

    environment: str
    header: str
    prefix: str = ""

    def __post_init__(self) -> None:
        if (not isinstance(self.environment, str)
                or _ENVIRONMENT_NAME.fullmatch(self.environment) is None
                or not isinstance(self.header, str)
                or _HEADER_NAME.fullmatch(self.header) is None
                or self.header.lower() in _FORBIDDEN_CREDENTIAL_HEADERS
                or not isinstance(self.prefix, str)
                or len(self.prefix.encode("utf-8")) > 1024
                or any(ord(character) < 32 or ord(character) == 127
                       for character in self.prefix)):
            raise CredentialMetadataError(
                "provider credential requires a safe environment/header mapping")


def credential_binding_from_document(
        value: object,
) -> ProviderCredentialBinding | None:
    """Read one optional generic credential mapping from adapter config."""
    if value is None:
        return None
    if (not isinstance(value, Mapping)
            or set(value) != {"environment", "header", "prefix"}):
        raise CredentialMetadataError(
            "provider credential must be null or the current generic shape")
    return ProviderCredentialBinding(
        environment=value.get("environment"),
        header=value.get("header"),
        prefix=value.get("prefix"),
    )


@contextmanager
def resolved_provider_headers(
        binding: ProviderCredentialBinding | None,
) -> Iterator[dict[str, str]]:
    """Resolve one optional credential for the immediate HTTPS request."""
    headers = {"Content-Type": "application/json"}
    if binding is None:
        try:
            yield headers
        finally:
            headers.clear()
        return
    secret = os.environ.get(binding.environment)
    if secret is None:
        raise CredentialResolutionError() from None
    try:
        secret_buffer = bytearray(secret.encode("utf-8"))
    except UnicodeEncodeError:
        raise CredentialMaterialMalformed() from None
    if (not secret_buffer or len(secret_buffer) > _MAX_CREDENTIAL_BYTES
            or b"\r" in secret_buffer or b"\n" in secret_buffer
            or any(value < 33 or value > 126 for value in secret_buffer)):
        secret_buffer[:] = b"\x00" * len(secret_buffer)
        raise CredentialMaterialMalformed()
    secret_text: str | None = None
    try:
        try:
            secret_text = secret_buffer.decode("utf-8")
        except UnicodeDecodeError:
            raise CredentialMaterialMalformed() from None
        headers[binding.header] = binding.prefix + secret_text
        yield headers
    finally:
        headers.clear()
        secret_text = None
        secret_buffer[:] = b"\x00" * len(secret_buffer)
        del secret_buffer


__all__ = [
    "CredentialMetadataError",
    "ProviderCredentialBinding",
    "credential_binding_from_document",
    "resolved_provider_headers",
]
