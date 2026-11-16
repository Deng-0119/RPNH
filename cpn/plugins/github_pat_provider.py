"""Explicit GitHub PAT source association over the existing account FD ABI.

The installed HOST supplies current-use authority and a credential source. Its
source reports a host/service/account association; this adapter compares that
report with the fixed selection, not with the GitHub network. It neither logs
in nor discovers active accounts, grants permission, stores PATs or refreshes
OAuth tokens. All private delivery remains ResolvedAccountFD/SCM_RIGHTS.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType

from .account_bindings import AccountBindingRequest
from .account_credentials import (
    AccountCredentialError, AccountSelection, InstalledAccountResolver,
    ResolvedAccountFD, _same_scope, _text,
)


@dataclass(frozen=True, slots=True)
class GitHubPATAccount:
    """Fixed non-secret HOST association, without implicit host normalization."""

    host: str
    service_id: str
    account_id: str

    def __post_init__(self):
        if (type(self.host) is not str or len(self.host) > 253
                or re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*",
                    self.host) is None):
            raise ValueError("GitHub PAT host requires an explicit canonical DNS hostname")
        _text(self.service_id)
        _text(self.account_id)


@dataclass(frozen=True, slots=True, repr=False)
class GitHubPATCredential:
    """A configured source's reported association and original one-shot lease.

    Creating this envelope transfers the lease to its recipient. If a source
    raises before returning it, that source must close its allocated lease.
    Metadata is a HOST source assertion, not independent GitHub verification.
    """

    account: GitHubPATAccount
    credential: ResolvedAccountFD

    def __post_init__(self):
        if (type(self.account) is not GitHubPATAccount
                or type(self.credential) is not ResolvedAccountFD):
            if isinstance(self.credential, ResolvedAccountFD):
                ResolvedAccountFD.close(self.credential)
            raise TypeError("GitHub PAT source requires an exact association and original FD lease")

    def close(self):
        ResolvedAccountFD.close(self.credential)

    def __repr__(self):
        return "GitHubPATCredential(<private one-shot lease>)"

    def __reduce__(self):
        raise TypeError("GitHub PAT credentials use the original private FD transfer")


class GitHubPATAccountProvider:
    """One explicit request, with independently supplied HOST callbacks.

    Both callbacks receive (account, selection, scope). current_use_check must
    consult the existing HOST authority on every new call. credential_source
    runs only after the original resolver admits the literal boolean True and
    must return GitHubPATCredential with a fresh original ResolvedAccountFD.
    A PAT may be replaced for the same binding; no account retarget or OAuth
    refresh is inferred. None explicitly represents an unavailable callback.
    """

    __slots__ = ("_request", "_account", "_current_use_check", "_credential_source")

    def __init__(self, request, *, host, current_use_check, credential_source):
        if type(request) is not AccountBindingRequest:
            raise TypeError("GitHub PAT provider requires one fixed account binding request")
        if (current_use_check is not None and not callable(current_use_check)
                or credential_source is not None and not callable(credential_source)):
            raise TypeError("GitHub PAT HOST callbacks must be callable or explicitly unavailable")
        self._request = request
        self._account = GitHubPATAccount(host, request.service_id, request.account_id)
        self._current_use_check = current_use_check
        self._credential_source = credential_source

    @property
    def request(self):
        return self._request

    @property
    def account(self):
        return self._account

    def __repr__(self):
        return "GitHubPATAccountProvider(<explicit HOST association>)"

    def __reduce__(self):
        raise TypeError("GitHub PAT authority and source callbacks remain in the HOST process")

    def _require_selection(self, selection, scope=None):
        if not self.request.matches(selection):
            raise AccountCredentialError("account_resolver_mismatch")
        if scope is not None:
            _same_scope(selection, scope)

    def bind(self, selection: AccountSelection):
        self._require_selection(selection)
        return InstalledAccountResolver(selection, self._credential,
                                         current_use_check=self._current_use)

    def _current_use(self, selection, scope):
        self._require_selection(selection, scope)
        if self._current_use_check is None:
            raise AccountCredentialError("account_use_unavailable")
        # Do not coerce truthy values or infer permission from source access.
        return self._current_use_check(self.account, selection, scope)

    def _credential(self, selection, scope):
        self._require_selection(selection, scope)
        if self._credential_source is None:
            raise AccountCredentialError()
        reported = None
        handed_off = False
        try:
            reported = self._credential_source(self.account, selection, scope)
            if type(reported) is not GitHubPATCredential:
                # A bare original lease is a recognizable malformed result;
                # close it rather than silently relabel its account/host.
                if isinstance(reported, ResolvedAccountFD):
                    ResolvedAccountFD.close(reported)
                raise AccountCredentialError("account_resolver_mismatch")
            if (reported.account != self.account
                    or reported.credential.selection != selection
                    or reported.credential.scope != scope):
                raise AccountCredentialError("account_resolver_mismatch")
            handed_off = True
            return reported.credential
        except AccountCredentialError:
            raise
        except Exception:
            raise AccountCredentialError() from None
        finally:
            if isinstance(reported, GitHubPATCredential) and not handed_off:
                # Subclasses are not accepted source responses, but their
                # recognizable original lease still transferred ownership.
                # Bypass a subclass cleanup override while rejecting it.
                GitHubPATCredential.close(reported)


class GitHubPATResolverFactory:
    """Existing installed-factory ABI with explicit node/tool associations.

    An installed entry-point wrapper constructs this factory using its HOST's
    real authority and source callbacks. No built-in account, authority store,
    credential location or global active-account fallback is installed here.
    Its fixed host/service/account mapping belongs to the pinned provider
    contract; changing it requires existing provider/binding revision updates.
    """

    __slots__ = ("_providers",)

    def __init__(self, providers):
        providers = tuple(providers)
        if not providers or any(type(item) is not GitHubPATAccountProvider for item in providers):
            raise TypeError("GitHub PAT factory requires explicit account provider objects")
        keyed = {(item.request.node_id, item.request.tool_name): item for item in providers}
        if len(keyed) != len(providers):
            raise ValueError("GitHub PAT provider node/tool keys must be unique")
        self._providers = MappingProxyType(keyed)

    def __reduce__(self):
        raise TypeError("GitHub PAT account providers remain in the HOST process")

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


__all__ = ("GitHubPATAccount", "GitHubPATCredential", "GitHubPATAccountProvider",
           "GitHubPATResolverFactory")
