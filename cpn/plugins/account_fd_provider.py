"""Explicit installed local-FD provider for the existing account resolver ABI.

The caller supplies an already opened descriptor and its existing current-use
authority callback. This module performs no login, path lookup, ambient lookup,
account discovery or permission grant. Tests use synthetic bytes only.
"""
from __future__ import annotations

import os
import stat
import tempfile
from threading import Lock
from types import MappingProxyType

from .account_bindings import AccountBindingRequest
from .account_credentials import AccountCredentialError, InstalledAccountResolver, ResolvedAccountFD


class LocalAccountFDProvider:
    """Own an explicit descriptor, opening a fresh private FD after permission.

    The source is never read during registration, publication or terminal replay.
    pread leaves its offset unchanged, permitting a same-binding byte refresh.
    The caller remains responsible for the account/source association and passes
    the real current-use check; descriptor possession is not account authority.
    """
    __slots__ = ("_request", "_fd", "_current_use_check", "_lock")

    def __init__(self, request, source_fd, *, current_use_check):
        if type(request) is not AccountBindingRequest or not callable(current_use_check):
            raise TypeError("local account provider requires a fixed request and current-use callback")
        if type(source_fd) is not int or source_fd < 0:
            raise TypeError("local account provider requires an explicitly supplied open descriptor")
        fd = None
        try:
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                raise AccountCredentialError()
            fd = os.dup(source_fd)
            os.set_inheritable(fd, False)
        except Exception:
            if fd is not None:
                os.close(fd)
            raise AccountCredentialError() from None
        self._request, self._fd, self._current_use_check = request, fd, current_use_check
        self._lock = Lock()

    @property
    def request(self):
        return self._request

    def __repr__(self):
        return "LocalAccountFDProvider(<explicit private descriptor>)"

    def __reduce__(self):
        raise TypeError("local account descriptors and authority callbacks stay in the HOST process")

    def close(self):
        with self._lock:
            fd, self._fd = self._fd, None
            if fd is not None:
                os.close(fd)

    def bind(self, selection):
        if not self.request.matches(selection):
            raise AccountCredentialError("account_resolver_mismatch")
        return InstalledAccountResolver(selection, self._fresh_fd,
                                         current_use_check=self._current_use_check)

    def _fresh_fd(self, selection, scope):
        if not self.request.matches(selection):
            raise AccountCredentialError("account_resolver_mismatch")
        buffer = None
        descriptor = None
        source = None
        try:
            # Duplication and close share one lock. A released descriptor number
            # can be reused by the OS, so never read self._fd after unlocking.
            with self._lock:
                if self._fd is None:
                    raise AccountCredentialError()
                source = os.dup(self._fd)
                os.set_inheritable(source, False)
            buffer = bytearray(os.pread(source, 4097, 0))
            if not buffer or len(buffer) > 4096:
                raise AccountCredentialError()
            # An unnamed temporary FD is transferred and consumed once; this is
            # not a named credential store or a source path selected from JSON.
            with tempfile.TemporaryFile() as temporary:
                temporary.write(buffer)
                temporary.flush()
                temporary.seek(0)
                descriptor = os.dup(temporary.fileno())
                os.set_inheritable(descriptor, False)
            result = ResolvedAccountFD(selection, scope, descriptor)
            descriptor = None
            return result
        except Exception:
            raise AccountCredentialError() from None
        finally:
            if source is not None:
                os.close(source)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if buffer is not None:
                buffer[:] = b"\0" * len(buffer)


class LocalAccountFDResolverFactory:
    """Explicit installed provider map, never a global active-account lookup."""
    __slots__ = ("_providers",)

    def __init__(self, providers):
        providers = tuple(providers)
        if not providers or any(type(provider) is not LocalAccountFDProvider for provider in providers):
            raise TypeError("account factory requires explicit local FD provider objects")
        keyed = {(provider.request.node_id, provider.request.tool_name): provider for provider in providers}
        if len(keyed) != len(providers):
            raise ValueError("local account provider keys must be unique")
        self._providers = MappingProxyType(keyed)

    def __reduce__(self):
        raise TypeError("installed account providers remain in the HOST process")

    def __call__(self, *, owner, catalog, node_id, account_selections):
        del owner
        node = "dsh" if node_id is None else node_id
        result = {}
        for name, selection in account_selections.items():
            catalog.declaration(name)
            provider = self._providers.get((node, name))
            if provider is None:
                raise AccountCredentialError("account_resolver_mismatch")
            result[name] = provider.bind(selection)
        return result


__all__ = ("LocalAccountFDProvider", "LocalAccountFDResolverFactory")
