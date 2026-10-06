"""Pure declaration diagnostics, never execution readiness or HOST preparation.

The caller supplies inert requirements and, optionally, declarations copied from
an already-prepared HOST. This module does not discover plugins, resolve Python,
compile/lower, probe providers, publish records, or reserve runtime resources.
A matching snapshot is neither callable identity nor a frozen execution inventory.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from ..registration import Registration
from ..registry.models import VersionRef
from ..registry.runtime_binding_contracts import freeze_candidate_document


_KINDS = frozenset({"schema", "component", "executor", "tool", "analyzer"})
_LOCATORS = frozenset({"callable", "import_locator", "import_path", "module_path", "entrypoint"})


def _digest(document):
    return hashlib.sha256(freeze_candidate_document(document).encode("utf-8")).hexdigest()


def _reject_locators(value):
    if type(value) is dict:
        if _LOCATORS.intersection(value):
            raise ValueError("HOST declarations cannot contain executable locators")
        for child in value.values():
            _reject_locators(child)
    elif type(value) is list:
        for child in value:
            _reject_locators(child)


def _registrations(value):
    # Copy and validate concrete builtins before traversing caller-owned input;
    # no Mapping adapter, to_dict(), string coercion, or user callback is used.
    result = json.loads(freeze_candidate_document(value))
    if not set(result) <= _KINDS:
        raise ValueError("unknown HOST registration kind")
    for kind, entries in result.items():
        if type(entries) is not dict:
            raise TypeError("HOST registration entries must be a standard object")
        for key, declaration in entries.items():
            if not key:
                raise ValueError("HOST registration keys must be nonempty")
            if type(declaration) is not dict:
                raise TypeError("HOST declaration must be a standard object")
            fields = {"kind", "key", "schema"} if kind == "schema" else {"kind", "key", "identity", "contracts"}
            if set(declaration) != fields:
                raise ValueError("HOST declaration fields differ from its registration kind")
            if declaration["kind"] != kind or declaration["key"] != key:
                raise ValueError("HOST declaration kind/key differs from its inventory address")
            if kind == "schema":
                schema = declaration["schema"]
                if type(schema) is not dict:
                    raise TypeError("HOST schema declaration must contain an object")
                if (schema.get("$id") != key
                        or schema.get("$schema") != "http://json-schema.org/draft-07/schema#"):
                    raise ValueError("HOST schema identity/dialect differs from its declaration")
                # Schema contents are inert bytes here, not runtime schema
                # authority. In particular, never follow or rewrite a $ref.
            else:
                if type(declaration["identity"]) is not dict or not declaration["identity"]:
                    raise ValueError("HOST declaration requires explicit nonempty identity data")
                if type(declaration["contracts"]) is not dict:
                    raise TypeError("HOST declaration contracts must be an object")
                _reject_locators(declaration["identity"])
                _reject_locators(declaration["contracts"])
    return result


@dataclass(frozen=True, slots=True, init=False)
class HostDeclarationSnapshot:
    """Immutable inert declaration bytes; not a trusted execution capability.

    Direct construction accepts a standard nested registrations dict. A copied
    JSON snapshot has exactly the same limited diagnostic meaning as any other
    caller-supplied declaration data. No callable objects are retained.
    """

    _registrations_json: str

    def __init__(self, registrations: dict):
        object.__setattr__(self, "_registrations_json", freeze_candidate_document(_registrations(registrations)))

    @property
    def registrations(self) -> dict:
        return json.loads(self._registrations_json)

    @property
    def declarations_digest(self) -> str:
        return hashlib.sha256(self._registrations_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        """Return fresh JSON data; modifying it cannot change this snapshot."""
        return {"registrations": self.registrations, "declarations_digest": self.declarations_digest}


def snapshot_host_declarations(registration: Registration) -> HostDeclarationSnapshot:
    """Copy only declarations of a previously prepared concrete Registration.

    Preparing/loading that Registration is an explicit separate HOST action.
    Implicit mechanical schemas not returned by declarations() are not loaded or
    added, and concurrent Registration mutation is not an atomic inventory cut.
    """
    if type(registration) is not Registration:
        raise TypeError("snapshot requires an already-prepared standard Registration")
    registrations = {kind: {} for kind in sorted(_KINDS)}
    for declaration in registration.declarations():
        registrations[declaration["kind"]][declaration["key"]] = declaration
    return HostDeclarationSnapshot(registrations)


def _comparison(required, snapshot):
    available = {} if snapshot is None else snapshot.registrations
    checks = []
    for kind, entries in sorted(required.items()):
        for key, declaration in sorted(entries.items()):
            actual = available.get(kind, {}).get(key)
            expected_digest = _digest(declaration)
            actual_digest = None if actual is None else _digest(actual)
            if snapshot is None:
                status, reason = "not_checked", "no_host_snapshot"
            elif actual is None:
                status, reason = "missing", "missing_host_declaration"
            elif actual_digest != expected_digest:
                status, reason = "mismatch", "declaration_digest_mismatch"
            else:
                status, reason = "matched", "exact_declaration_match"
            checks.append({"kind": kind, "key": key, "status": status, "reason": reason,
                "required_declaration_digest": expected_digest, "host_declaration_digest": actual_digest})
    states = {check["status"] for check in checks}
    status = ("not_checked" if snapshot is None else "mismatch" if "mismatch" in states
              else "missing" if "missing" in states else "matched")
    return status, checks


@dataclass(frozen=True, slots=True, init=False)
class HostReadinessDiagnostic:
    """Detached diagnostic only; execution_ready is unconditionally false."""

    _document_json: str

    def __init__(self, required_registrations: dict, snapshot: HostDeclarationSnapshot | None = None,
                 *, plan_ref: VersionRef | None = None, plan_sha256: str | None = None):
        required = _registrations(required_registrations)
        if snapshot is not None and type(snapshot) is not HostDeclarationSnapshot:
            raise TypeError("HOST snapshot must be an inert HostDeclarationSnapshot or None")
        reference = None
        if plan_ref is not None:
            from ..registry.preserved_binding_contracts import PLAN_V2_TYPE
            from ..registry.runtime_binding_contracts import PLAN_TYPE, _copy_record_ref
            from ..registry.strict_contracts import ref_payload
            if type(plan_ref) is not VersionRef or type(plan_ref.entity_type) is not str:
                raise TypeError("diagnostic plan requires a standard exact VersionRef")
            if plan_ref.entity_type not in (PLAN_TYPE, PLAN_V2_TYPE):
                raise ValueError("unsupported candidate plan version")
            reference = ref_payload(_copy_record_ref(plan_ref, plan_ref.entity_type))
        if (reference is None) != (plan_sha256 is None):
            raise ValueError("diagnostic plan ref and digest must be supplied together")
        if plan_sha256 is not None and (type(plan_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", plan_sha256) is None):
            raise ValueError("plan_sha256 must be a lowercase SHA-256 digest")
        status, checks = _comparison(required, snapshot)
        document = {"purpose": "diagnostic", "evidence_scope": "declarations_only",
            "execution_ready": False,
            "required_declarations_digest": _digest(required),
            "host_declarations_digest": None if snapshot is None else snapshot.declarations_digest,
            "declarations_status": status, "declaration_checks": checks,
            "callable_identity": "not_checked", "lowering": "not_checked",
            "runtime_schema_authority": "not_checked", "permission": "not_checked",
            "capacity": "not_checked", "reservation": "not_reserved",
            "plan_ref": reference, "plan_sha256": plan_sha256}
        object.__setattr__(self, "_document_json", freeze_candidate_document(document))

    @property
    def execution_ready(self) -> bool:
        return False

    def to_dict(self) -> dict:
        """Return a fresh JSON tree; this is not a persisted readiness record."""
        return json.loads(self._document_json)


def diagnose_host_requirements(required_registrations: dict,
                               snapshot: HostDeclarationSnapshot | None = None) -> HostReadinessDiagnostic:
    """Compare full exact declarations without loading or executing HOST code.

    ``required_registrations`` is the existing nested ``registrations`` member
    of author HOST requirements or compiled wire, not the whole envelope.
    Missing snapshot means not_checked, including for an empty requirement set.
    """
    return HostReadinessDiagnostic(required_registrations, snapshot)


def diagnose_candidate_plan(core, plan_ref: VersionRef,
                            snapshot: HostDeclarationSnapshot | None = None) -> HostReadinessDiagnostic:
    """Diagnose only an exact canonical historical v1/v2 plan and dependencies.

    Each supported version delegates to its existing fixed-cut, offline reader.
    Reader errors propagate: malformed/unavailable evidence never becomes a
    positive diagnostic. This is not current permission or first-admission proof.
    """
    from ..registry.preserved_binding_contracts import PLAN_V2_TYPE
    from ..registry.runtime_binding_contracts import PLAN_TYPE, _copy_record_ref
    if type(plan_ref) is not VersionRef or type(plan_ref.entity_type) is not str:
        raise TypeError("candidate diagnostic requires a standard exact VersionRef")
    if plan_ref.entity_type not in (PLAN_TYPE, PLAN_V2_TYPE):
        raise ValueError("unsupported candidate plan version")
    reference = _copy_record_ref(plan_ref, plan_ref.entity_type)
    if snapshot is not None and type(snapshot) is not HostDeclarationSnapshot:
        raise TypeError("HOST snapshot must be an inert HostDeclarationSnapshot or None")
    if reference.entity_type == PLAN_TYPE:
        from .candidate_plans import read_candidate_plan
        draft = read_candidate_plan(core, reference)
    else:
        from .preserved_candidate_plans import read_preserved_candidate_plan
        draft = read_preserved_candidate_plan(core, reference)
    plan = draft.plan
    return HostReadinessDiagnostic(plan["host_requirements"]["registrations"], snapshot,
        plan_ref=reference, plan_sha256=_digest(plan))


__all__ = ("HostDeclarationSnapshot", "HostReadinessDiagnostic", "snapshot_host_declarations",
           "diagnose_host_requirements", "diagnose_candidate_plan")
