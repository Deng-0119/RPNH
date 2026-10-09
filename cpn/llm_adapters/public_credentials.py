"""Trusted installed capability association and execution-only secret slot.

No import strings, storage paths or credentials enter these public identities.
Installer honesty and account binding remain HOST assumptions, not a sandbox.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable
from cpn.rpnh.public_material_contracts import canonical, decode
from ._external_provider_credentials import CredentialResolutionError, CredentialMaterialMalformed


@dataclass(frozen=True, slots=True)
class PublicCredentialCapability:
    identity_bytes: bytes
    observation_bytes: bytes
    callback: Callable

    def __post_init__(self):
        decode(self.identity_bytes, canonical_required=True)
        decode(self.observation_bytes, canonical_required=True)
        if not callable(self.callback): raise TypeError('installed credential capability needs callable')


def check_capability(capability, expected):
    if expected is None:
        if capability is not None: raise ValueError('unauthenticated port cannot hold credential capability')
        return
    if (type(capability) is not PublicCredentialCapability
            or capability.identity_bytes != canonical(expected['identity'])
            or capability.observation_bytes != canonical(expected['observation'])):
        raise ValueError('installed credential capability association differs')


def bearer_secret_slot(secret):
    """Fixed renderer implementation, included in the installed declaration."""
    return ('Authorization', 'Bearer ' + secret)


@contextmanager
def resolved_public_headers(identity, resolver, renderer):
    headers = {'Content-Type': 'application/json'}
    buffer = None
    secret = None
    try:
        check_capability(resolver, identity['resolver'])
        check_capability(renderer, identity['renderer'])
        if identity['binding'] is not None:
            # The resolver must reject a changed account/service/revision. It
            # returns only the immediate slot value, never route configuration.
            try: secret = resolver.callback(decode(canonical(identity['binding'])))
            except Exception: raise CredentialResolutionError() from None
            if type(secret) is not str: raise CredentialMaterialMalformed()
            try: buffer = bytearray(secret.encode('ascii'))
            except UnicodeEncodeError: raise CredentialMaterialMalformed() from None
            if not buffer or len(buffer) > 4096 or any(c < 33 or c > 126 for c in buffer):
                raise CredentialMaterialMalformed()
            try: rendered = renderer.callback(secret)
            except Exception: raise CredentialResolutionError() from None
            # ABI v1 supports one fixed bearer slot, no arbitrary headers.
            if type(rendered) is not tuple or rendered != ('Authorization', 'Bearer ' + secret):
                raise CredentialMaterialMalformed()
            headers[rendered[0]] = rendered[1]
        yield headers
    finally:
        headers.clear()
        secret = None
        if buffer is not None: buffer[:] = b'\x00' * len(buffer)
