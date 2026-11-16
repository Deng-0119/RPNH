"""Fixed-account association and private FD rendering for managed tools.

Selections are exact, non-secret operation inputs, never account-use authority.
The installed HOST delegates current permission and revocation to its existing
account authority immediately before each new dispatch. No login, account
registry, ambient credential lookup, connection pool or retry is created here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import json
import os
from typing import Callable, Iterator, Mapping

from cpn.rpnh.collaboration.references import (
    SourceQualifiedResourceRef, SourceQualifiedVersionRef,
)
from cpn.rpnh.registry.credentials import (
    InheritedCredentialFD, _read_transferred_secret,
)

_SLOT = "github_pat/v1"
_SECRET_VARIABLE = "GITHUB_PERSONAL_ACCESS_TOKEN"
_BASE_ENVIRONMENT = frozenset({"PATH", "LANG", "LC_ALL", "TMPDIR"})
_ERROR_CODES = frozenset({
    "account_credential_unavailable", "account_scope_mismatch",
    "account_binding_unavailable", "account_binding_input_missing",
    "account_credential_consumed",
    "account_resolver_mismatch", "account_resolver_unavailable", "account_environment_rejected",
    "account_use_denied", "account_use_unavailable",
    "account_provider_unavailable", "account_provider_identity_mismatch",
    "account_required", "account_requirement_mismatch",
})


class AccountCredentialError(RuntimeError):
    """Closed diagnostic; never format resolver errors or credential material."""

    def __init__(self, code: str = "account_credential_unavailable") -> None:
        if code not in _ERROR_CODES:
            raise ValueError("unsupported account credential diagnostic")
        self.code = code
        super().__init__(code)


def _text(value: object, *, maximum: int = 512) -> str:
    if (type(value) is not str or not value or value != value.strip()
            or len(value) > maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("account metadata requires bounded canonical text")
    return value


def _version(value: object, kind: str) -> None:
    if (type(value) is not SourceQualifiedVersionRef
            or value.ref.entity_type != kind):
        raise TypeError("account metadata requires an exact source-qualified reference")
    stem = kind.split("/")[0]
    if (value.ref.entity_id.kind != stem
            or value.ref.version_id.kind != stem + "_version"):
        raise TypeError("account reference kinds differ from the declared entity type")
    _text(value.source_id)


@dataclass(frozen=True, slots=True)
class AccountSelection:
    """One selected account for one exact source-qualified principal and task."""

    principal_ref: SourceQualifiedVersionRef
    task_ref: SourceQualifiedVersionRef
    binding_ref: SourceQualifiedResourceRef
    service_id: str
    account_id: str
    binding_revision: str
    slot: str = _SLOT

    def __post_init__(self) -> None:
        _version(self.principal_ref, "principal/v1")
        _version(self.task_ref, "task/v1")
        if (type(self.binding_ref) is not SourceQualifiedResourceRef
                or self.binding_ref.ref.resource_id.kind != "resource"
                or self.binding_ref.ref.resource_version_id.kind != "resource_version"):
            raise TypeError("account binding requires one exact resource version")
        _text(self.binding_ref.source_id)
        if len({self.principal_ref.source_id, self.task_ref.source_id,
                self.binding_ref.source_id}) != 1:
            raise ValueError("the first account bridge requires one canonical local source")
        for value in (self.service_id, self.account_id, self.binding_revision):
            _text(value)
        if self.slot != _SLOT:
            raise ValueError("unsupported installed credential renderer slot")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "rpnh/stdio_account_selection/v1",
            "principal_ref": self.principal_ref.to_dict(),
            "task_ref": self.task_ref.to_dict(),
            "binding_ref": self.binding_ref.to_dict(),
            "service_id": self.service_id,
            "account_id": self.account_id,
            "binding_revision": self.binding_revision,
            "slot": self.slot,
        }

    def binding_document(self) -> dict[str, object]:
        """Non-secret resource body; its own exact resource ref stays external."""
        document = self.to_dict()
        document["schema_version"] = "rpnh/stdio_account_binding/v1"
        del document["binding_ref"]
        return document

    @classmethod
    def from_dict(cls, value, *, catalog):
        fields = {"schema_version", "principal_ref", "task_ref", "binding_ref",
                  "service_id", "account_id", "binding_revision", "slot"}
        if (type(value) is not dict or set(value) != fields
                or value["schema_version"] != "rpnh/stdio_account_selection/v1"):
            raise ValueError("account selection is not the closed non-secret shape")
        return cls(
            SourceQualifiedVersionRef.from_dict(value["principal_ref"], catalog=catalog),
            SourceQualifiedVersionRef.from_dict(value["task_ref"], catalog=catalog),
            SourceQualifiedResourceRef.from_dict(value["binding_ref"], catalog=catalog),
            value["service_id"], value["account_id"], value["binding_revision"],
            value["slot"],
        )


@dataclass(frozen=True, slots=True)
class AccountCallScope:
    """Non-secret association with an already authorized managed execution."""

    principal_ref: SourceQualifiedVersionRef
    task_ref: SourceQualifiedVersionRef
    execution_ref: SourceQualifiedVersionRef
    registration_key: str
    call_id: str

    def __post_init__(self) -> None:
        _version(self.principal_ref, "principal/v1")
        _version(self.task_ref, "task/v1")
        _version(self.execution_ref, "operation_execution_lease/v1")
        if len({self.principal_ref.source_id, self.task_ref.source_id,
                self.execution_ref.source_id}) != 1:
            raise ValueError("call scope crosses canonical local sources")
        _text(self.registration_key, maximum=4096)
        _text(self.call_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "principal_ref": self.principal_ref.to_dict(),
            "task_ref": self.task_ref.to_dict(),
            "execution_ref": self.execution_ref.to_dict(),
            "registration_key": self.registration_key,
            "call_id": self.call_id,
        }


def _same_scope(selection: AccountSelection, scope: AccountCallScope) -> None:
    if (type(selection) is not AccountSelection or type(scope) is not AccountCallScope
            or selection.principal_ref != scope.principal_ref
            or selection.task_ref != scope.task_ref):
        raise AccountCredentialError("account_scope_mismatch")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AccountCredentialError("account_binding_unavailable")
        result[key] = value
    return result


def _current_operation(service, execution):
    """Use original rehydrated inputs, not caller-supplied projection fields."""
    from cpn.rpnh.registry.operations import hydrate_operation_authority
    supplied = execution.operation
    return hydrate_operation_authority(
        service.repository, input_plan=supplied.input_plan,
        petri_inputs=tuple(item.artifact for item in supplied.inputs))


def _binding_input_bytes(operation, selection):
    """Select an exact acknowledged full input after original authorization.

    This helper does not grant access. It deliberately does not use canonical
    resource visibility or static read policy as a substitute for the existing
    acknowledged operation-input chain.
    """
    from cpn.rpnh.registry.operations import RegisteredOperationAuthority
    if type(operation) is not RegisteredOperationAuthority:
        raise AccountCredentialError("account_binding_input_missing")
    matches = tuple(item for item in operation.inputs
                    if item.resource_ref == selection.binding_ref.ref)
    if (len(matches) != 1 or matches[0].projected_payload is not None
            or matches[0].source_resource_ref is not None
            or matches[0].substituted_content_schema_id is not None
            or type(matches[0].artifact.receipt.positive_byte_count) is not int
            or matches[0].artifact.receipt.positive_byte_count <= 0
            or matches[0].artifact.receipt.positive_byte_count
            != matches[0].artifact.resource.header.byte_size):
        raise AccountCredentialError("account_binding_input_missing")
    return matches[0].consumer_payload


def _verify_settled_binding_input(service, execution, operation, selection):
    """Reuse original durable closure for hydration's settled-carrier branch.

    Live/historical inputs already receive original repository reverification.
    Settled DTO fields alone do not prove a complete acknowledged read. Obtain
    the original receipt/header before the registered body becomes readable.
    """
    from cpn.rpnh.registry.publication import _ref_payload
    from cpn.rpnh.registry.resource_verification import verify_petri_input_receipt
    from cpn.rpnh.registry.resources import ResourceDeliveryReceipt, SettledPetriInputArtifact
    item = next(item for item in operation.inputs
                if item.resource_ref == selection.binding_ref.ref)
    artifact = item.artifact
    if not isinstance(artifact, SettledPetriInputArtifact):
        return
    try:
        context = operation.canonical.context
        if artifact.operation_start_event_id != execution.start_event_id:
            raise AccountCredentialError("account_binding_input_missing")
        terminal = service.kernel._exact_object(
            artifact.receipt.terminal_delivery_ref, expected_type="resource_delivery/v1")
        if terminal.metadata.get("context_ref") != _ref_payload(context.invocation_ref):
            raise AccountCredentialError("account_binding_input_missing")
        receipt = verify_petri_input_receipt(
            service.core, service.kernel,
            ResourceDeliveryReceipt(
                delivery_ref=artifact.receipt.terminal_delivery_ref,
                terminal_event_id=artifact.receipt.terminal_event_id,
                outcome="acknowledged",
                observed_read_event_id=artifact.receipt.observed_read_event_id),
            item.resource_ref)
        header = service.kernel._firing_header(context, item.resource_ref)
        if (replace(receipt, verified_at_head=artifact.receipt.verified_at_head)
                != artifact.receipt
                or replace(header,
                    relation_kinds=artifact.resource.header.relation_kinds,
                    published_at_head=artifact.resource.header.published_at_head)
                != artifact.resource.header
                or type(receipt.positive_byte_count) is not int
                or receipt.positive_byte_count <= 0
                or receipt.positive_byte_count != header.byte_size):
            raise AccountCredentialError("account_binding_input_missing")
    except AccountCredentialError:
        raise
    except Exception:
        raise AccountCredentialError("account_binding_input_missing") from None


def prepare_account_scope(service, execution, declaration, selection, *, call_id):
    """Owner-only adapter over original managed admission and acknowledged inputs.

    The selection resource must already be an exact full input of this operation.
    Canonical visibility, static read policy, a copied descriptor or an observer
    grant cannot substitute for that input. No new permission is issued here.
    """
    from cpn.plugins.managed_tools import ManagedPluginInvocationService
    if type(service) is not ManagedPluginInvocationService:
        raise TypeError("account preparation requires the original managed service")
    execution = service._authorize(execution, declaration)
    return _account_scope_from_authorized(
        service, execution, declaration, selection, call_id=call_id)


def _account_scope_from_authorized(service, execution, declaration, selection, *, call_id):
    """Original service's same-admission adapter after its completed authorization.

    No operation permission is changed between the caller's _authorize and this
    adapter. It still rehydrates the actual inputs rather than trusting wrapper
    projection fields. External callers use prepare_account_scope above.
    """
    from cpn.plugins.managed_tools import ManagedPluginInvocationService
    from cpn.rpnh.registry._event_store.branch_publication import _local_binding
    if type(service) is not ManagedPluginInvocationService:
        raise TypeError("account preparation requires the original managed service")
    operation = _current_operation(service, execution)
    context = operation.canonical.context
    with service.core.event_store.connect() as db:
        db.execute("BEGIN")
        source = _local_binding(
            db, service.core.catalog, service.core.task_id,
            service.core.object_store)["source_id"]
    scope = AccountCallScope(
        SourceQualifiedVersionRef(source, context.principal_ref),
        SourceQualifiedVersionRef(source, context.task_ref),
        SourceQualifiedVersionRef(source, execution.operation_execution_lease_ref),
        declaration.registration_key, call_id,
    )
    _same_scope(selection, scope)
    try:
        _binding_input_bytes(operation, selection)
        _verify_settled_binding_input(service, execution, operation, selection)
        # Hydration preserves supplied DTO bytes. Only the exact original full
        # input/receipt gates above authorize this registered firing-body read.
        body = service.kernel._read_firing_registered(context, selection.binding_ref.ref)
        if not isinstance(body, bytes) or len(body) > 32768:
            raise AccountCredentialError("account_binding_unavailable")
        document = json.loads(
            body, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if document != selection.binding_document():
            raise AccountCredentialError("account_binding_unavailable")
    except AccountCredentialError:
        raise
    except Exception:
        raise AccountCredentialError("account_binding_unavailable") from None
    return scope


class ResolvedAccountFD:
    """HOST-owned single-use descriptor, never serializable credential bytes."""

    __slots__ = ("_selection", "_scope", "_fd")

    def __init__(self, selection: AccountSelection, scope: AccountCallScope,
                 fd: int) -> None:
        if type(fd) is not int or fd < 3:
            raise AccountCredentialError()
        try:
            _same_scope(selection, scope)
            os.fstat(fd)
            if os.get_inheritable(fd):
                raise AccountCredentialError()
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise AccountCredentialError() from None
        self._selection, self._scope, self._fd = selection, scope, fd

    @property
    def selection(self) -> AccountSelection:
        return self._selection

    @property
    def scope(self) -> AccountCallScope:
        return self._scope

    def __repr__(self):
        return "ResolvedAccountFD(<private single-use descriptor>)"

    def __reduce__(self):
        raise TypeError("use the existing private descriptor transfer")

    def take_fd(self) -> int:
        if self._fd is None:
            raise AccountCredentialError("account_credential_consumed")
        fd, self._fd = self._fd, None
        return fd

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


@dataclass(frozen=True, slots=True)
class InstalledAccountResolver:
    """HOST-installed authority and credential callbacks for one fixed selection.

    current_use_check delegates to the existing account authority; it must return
    the literal boolean True for current permission. This adapter stores no
    grants and cannot infer account permission from a readable binding resource.
    callback opens a fresh one-shot FD only after that permission check succeeds.
    """

    selection: AccountSelection
    callback: Callable[[AccountSelection, AccountCallScope], ResolvedAccountFD] = field(
        repr=False, compare=False)
    current_use_check: Callable[[AccountSelection, AccountCallScope], bool] = field(
        repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if (type(self.selection) is not AccountSelection
                or not callable(self.callback) or not callable(self.current_use_check)):
            raise TypeError("an installed account resolver requires fixed selection and authority callbacks")

    def __reduce__(self):
        raise TypeError("installed account callbacks must remain in the HOST process")


def bind_installed_account_resolvers(factory, *, owner, catalog, node_id=None,
                                    account_selections=None):
    """Call an explicitly installed HOST factory after the real owner exists.

    This only supplies a process-local map to the original managed service. It
    does not publish a binding, issue a grant, select an active account, or put
    the callable into a task/configuration document. Installed factories provide
    explicit fixed selections only; permission and secret callbacks remain
    deferred until dispatch. Service construction checks
    every returned resolver against its catalog; operation prepare still requires
    the exact binding resource to be an actual acknowledged input.
    """
    if factory is None:
        if account_selections is not None:
            raise AccountCredentialError("account_resolver_unavailable")
        return None
    from cpn.rpnh.run import RunOwner
    from cpn.plugins.managed_tools import ManagedPluginToolCatalog
    if (not callable(factory) or not isinstance(owner, RunOwner)
            or not isinstance(catalog, ManagedPluginToolCatalog)):
        raise TypeError("installed account factory requires an original owner and managed catalog")
    if not catalog.tools:
        raise ValueError("installed account factory requires selected managed tools")
    if node_id is not None and (type(node_id) is not str or not node_id):
        raise TypeError("account factory node identity must be explicit text or None")
    if account_selections is not None:
        from types import MappingProxyType
        if (not isinstance(account_selections, Mapping) or not account_selections
                or any(type(name) is not str or type(selection) is not AccountSelection
                       for name, selection in account_selections.items())):
            raise TypeError("account factory selections require exact original publication products")
        account_selections = MappingProxyType(dict(account_selections))
        for name in account_selections:
            catalog.declaration(name)
    try:
        arguments = {"owner": owner, "catalog": catalog, "node_id": node_id}
        if account_selections is not None:
            arguments["account_selections"] = account_selections
        result = factory(**arguments)
    except Exception:
        raise AccountCredentialError("account_resolver_unavailable") from None
    if not isinstance(result, Mapping):
        raise TypeError("installed account factory must return an explicit resolver mapping")
    try:
        result = dict(result)
    except Exception:
        raise AccountCredentialError("account_resolver_unavailable") from None
    if account_selections is not None and (
            set(result) != set(account_selections)
            or any(type(result[name]) is not InstalledAccountResolver
                   or result[name].selection != selection
                   for name, selection in account_selections.items())):
        raise AccountCredentialError("account_resolver_mismatch")
    return result


@contextmanager
def resolve_account_fd(
    selection: AccountSelection, scope: AccountCallScope,
    resolver: InstalledAccountResolver,
) -> Iterator[ResolvedAccountFD]:
    """Resolve once immediately before dispatch; no cached active-account lookup.

    The original owner has admitted scope and the exact selection input. Current
    account-use permission/revocation are delegated afresh to current_use_check;
    a readable selection is insufficient. Credential callback responsibilities
    are a fresh bounded descriptor for the same service/account/revision. Refresh
    may change bytes only. This context owns the returned descriptor until taken.
    """
    _same_scope(selection, scope)
    if (type(resolver) is not InstalledAccountResolver
            or resolver.selection != selection):
        raise AccountCredentialError("account_resolver_mismatch")
    resolved = None
    try:
        try:
            permitted = resolver.current_use_check(selection, scope)
        except Exception:
            raise AccountCredentialError("account_use_unavailable") from None
        if permitted is not True:
            raise AccountCredentialError("account_use_denied")
        try:
            resolved = resolver.callback(selection, scope)
        except Exception:
            raise AccountCredentialError() from None
        if (type(resolved) is not ResolvedAccountFD
                or resolved.selection != selection or resolved.scope != scope):
            raise AccountCredentialError("account_resolver_mismatch")
        yield resolved
    finally:
        if isinstance(resolved, ResolvedAccountFD):
            ResolvedAccountFD.close(resolved)


@contextmanager
def resolved_stdio_environment(
    selection: AccountSelection, scope: AccountCallScope,
    received: ResolvedAccountFD, *,
    base_environment: Mapping[str, str],
) -> Iterator[dict[str, str]]:
    """Worker-only FD consumption into one explicit child environment.

    The caller creates received only from the private descriptor channel and
    the exact HOST-prepared metadata. The original worker deadline/kill owns
    stalled reads and process cleanup. This is not a new credential transport.
    """
    environment: dict[str, str] = {}
    secret_buffer = None
    try:
        _same_scope(selection, scope)
        if (type(received) is not ResolvedAccountFD
                or received.selection != selection or received.scope != scope):
            raise AccountCredentialError("account_resolver_mismatch")
        if not isinstance(base_environment, Mapping):
            raise AccountCredentialError("account_environment_rejected")
        for key, value in base_environment.items():
            if (type(key) is not str or key not in _BASE_ENVIRONMENT
                    or type(value) is not str or "\x00" in value):
                raise AccountCredentialError("account_environment_rejected")
            environment[key] = value
        if sum(len(k.encode("utf-8")) + len(v.encode("utf-8"))
               for k, v in environment.items()) > 65536:
            raise AccountCredentialError("account_environment_rejected")
        fd = received.take_fd()
        try:
            secret_buffer = _read_transferred_secret(
                InheritedCredentialFD(fd=fd), fd)
        except Exception:
            raise AccountCredentialError() from None
        environment[_SECRET_VARIABLE] = secret_buffer.decode("ascii")
        yield environment
    finally:
        environment.clear()
        if secret_buffer is not None:
            secret_buffer[:] = b"\x00" * len(secret_buffer)
        if isinstance(received, ResolvedAccountFD):
            ResolvedAccountFD.close(received)

