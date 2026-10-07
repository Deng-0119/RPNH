"""Immutable receiver-only environment data; no document grants authority.

Private selection/binding/plan objects contain local paths and references. Only
``public_check_summary`` and the explicit evidence projection are exportable.
"""
from __future__ import annotations

from pathlib import Path

from .environment_contracts import (
    StrictEnvironmentDTO, EnvironmentContractError, PackageTarget,
    schema_object as obj, schema_array as arr, nullable, DIGEST_SCHEMA as DIGEST,
    TEXT_SCHEMA as TEXT, SCOPED_REQUIREMENT_SCHEMA as SCOPE,
    PACKAGE_TARGET_SCHEMA as TARGET,
)

DRAFT7 = "http://json-schema.org/draft-07/schema#"
ID = {"type": "string", "minLength": 1, "maxLength": 240}
PATH = {"type": "string", "minLength": 1, "maxLength": 4096}
STATUS = {"enum": ["not_checked", "satisfied", "missing", "incompatible", "unsupported", "failed", "stale"]}
LEVEL = {"enum": ["author_declared", "configuration_observed", "installed_metadata", "artifact_verified", "local_probe", "host_assembled", "runtime_observed"]}
AGGREGATE = {"enum": ["passed_for_checked_scope", "blocked", "incomplete", "stale"]}
MODE = {"enum": ["existing", "new_venv"]}
PLUGIN = obj({"scoped_requirement_id": SCOPE, "plugin_id": ID, "configuration_ref": ID})
SERVICE = obj({"scoped_requirement_id": SCOPE, "service_contract_id": ID,
    "profile_ref": ID, "credential_ref": nullable(ID), "model_condition": ID})
TOOL = obj({"scoped_requirement_id": SCOPE, "tool_contract_id": ID, "executable": PATH})
BOUND_TOOL = obj({"scoped_requirement_id": SCOPE, "tool_contract_id": ID, "executable_realpath": PATH})
PYTHON_SELECTION = {"oneOf": [obj({"executable": PATH}), obj({"base_executable": PATH, "prefix": PATH})]}
PYTHON_IDENTITY = obj({"implementation": ID, "version": ID, "abi": ID, "platform": ID})
PLATFORM = obj({"os_family": ID, "architecture": ID})
DISTRIBUTION_IDENTITY = obj({"name": ID, "version": ID, "requires_dist": arr(TEXT),
    "requires_python": {"type": "string", "maxLength": 1024}, "artifact_digest": nullable(DIGEST)})
TOOL_IDENTITY = obj({"tool_contract_id": ID, "version": ID, "content_digest": nullable(DIGEST),
    "capabilities": arr(ID)})
PLUGIN_IDENTITY = obj({"plugin_id": ID, "version": ID, "api_contract": ID,
    "distribution": ID, "implementation_digest": nullable(DIGEST), "configuration_digest": nullable(DIGEST)})
SERVICE_IDENTITY = obj({"service_contract_id": ID, "model_condition": ID, "profile_digest": DIGEST})
SYSTEM_IDENTITY = obj({"contract_id": ID, "satisfied": {"type": "boolean"}, "identity_digest": nullable(DIGEST)})
PROFILE_IDENTITY = obj({"profile_id": ID, "implementation_digest": DIGEST})
SELECTION_ROW = {"oneOf": [obj({"scoped_requirement_id": nullable(SCOPE), "kind": {"const": kind},
    "identity": identity, "source_contract": ID, "evidence_level": LEVEL})
    for kind, identity in [("python", PYTHON_IDENTITY), ("distribution", DISTRIBUTION_IDENTITY),
        ("platform", PLATFORM), ("tool", TOOL_IDENTITY), ("plugin", PLUGIN_IDENTITY),
        ("service", SERVICE_IDENTITY), ("system", SYSTEM_IDENTITY), ("host_profile", PROFILE_IDENTITY)]]}
ISSUE = obj({"scoped_requirement_id": nullable(SCOPE), "reason_code": ID})
# Probe observations are intentionally closed; no raw output/environment map.
OBSERVED = obj({"python": nullable(PYTHON_IDENTITY), "distribution": nullable(DISTRIBUTION_IDENTITY),
    "platform": nullable(PLATFORM), "tool": nullable(TOOL_IDENTITY), "plugin": nullable(PLUGIN_IDENTITY),
    "service": nullable(SERVICE_IDENTITY), "system": nullable(SYSTEM_IDENTITY),
    "host_profile": nullable(PROFILE_IDENTITY), "present": nullable({"type": "boolean"})})
CHECK_ROW = obj({"scoped_requirement_id": nullable(SCOPE), "check_id": ID, "status": STATUS,
    "evidence_level": LEVEL, "observed": OBSERVED, "expected": obj({"requirement_digest": nullable(DIGEST)}),
    "reason_code": ID})


def schema(name, properties):
    return {"$schema": DRAFT7, "$id": "rpnh/" + name + "/v1", **obj({
        "schema_version": {"const": "rpnh/" + name + "/v1"}, **properties})}


SELECTION_SCHEMA = schema("environment_selection", {"selection_id": ID, "target": TARGET,
    "mode": MODE, "python_selection": PYTHON_SELECTION, "tools": arr(TOOL),
    "plugins": arr(PLUGIN), "services": arr(SERVICE), "host_profile_id": ID})
BINDING_SCHEMA = schema("local_environment_binding", {"binding_id": ID, "binding_revision": ID,
    "target": TARGET, "resolution_digest": DIGEST, "mode": MODE,
    "python": obj({"executable_realpath": PATH, "prefix_realpath": PATH}),
    "tools": arr(BOUND_TOOL), "plugins": arr(PLUGIN), "services": arr(SERVICE), "host_profile_id": ID})
RESOLUTION_SCHEMA = schema("environment_resolution_lock", {"target": TARGET,
    "resolver_contract": ID, "target_platform": PLATFORM, "selections": arr(SELECTION_ROW),
    "unresolved": arr(ISSUE), "coverage": arr(ID)})
CHECK_SCHEMA = schema("environment_check", {"target": TARGET, "selection_digest": nullable(DIGEST),
    "binding_revision": nullable(ID), "binding_digest": nullable(DIGEST), "resolution_digest": nullable(DIGEST),
    "checked_at": ID, "probe_contracts": arr(ID), "checks": arr(CHECK_ROW), "aggregate": AGGREGATE,
    "not_checked": arr(ID), "execution_permitted": {"const": False}})
# Fixed adapter actions. Paths remain private and cannot supply executable code.
ACTION_TARGET = obj({"path": nullable(PATH), "scoped_requirement_id": nullable(SCOPE), "name": nullable(ID)})
ACTION_OPTIONS = obj({"base_executable": nullable(PATH), "version": nullable(ID),
    "artifact_digest": nullable(DIGEST), "source_path": nullable(PATH),
    "reference": nullable(ID), "host_profile_digest": nullable(DIGEST)})
ACTION = obj({"action_id": ID, "kind": {"enum": ["create_venv", "install_distribution", "configure_local_reference",
    "manual_system_step", "verify_local_capability", "assemble_trusted_host"]}, "trusted_adapter_contract": ID,
    "target": ACTION_TARGET, "options": ACTION_OPTIONS, "expected_effect": ID, "retained_on_failure": ID})
PLAN_SCHEMA = schema("environment_preparation_plan", {"plan_id": ID, "target": TARGET,
    "selection_digest": nullable(DIGEST), "binding_revision": nullable(ID), "resolution_digest": nullable(DIGEST),
    "based_on_check_digest": DIGEST, "mode": MODE, "actions": arr(ACTION), "unresolved": arr(ISSUE),
    "authorization_requirements": arr(ID)})
RECEIPT_SCHEMA = schema("environment_preparation_receipt", {"target": TARGET,
    "result_binding_revision": nullable(ID), "result_binding_digest": nullable(DIGEST), "plan_digest": DIGEST,
    "before_check_digest": DIGEST, "action_results": arr(obj({"action_id": ID,
        "status": {"enum": ["completed", "failed", "cancelled", "not_started"]}, "reason_code": ID})),
    "after_check_digest": nullable(DIGEST), "host_declarations_digest": nullable(DIGEST),
    "failure": nullable(ID), "completed_at": ID})


def _local_paths(document):
    if "python_selection" in document:
        paths = document["python_selection"].values()
    else:
        paths = document.get("python", {}).values()
    for path in paths:
        if not Path(path).is_absolute():
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "local interpreter paths must be absolute")
    for row in document.get("tools", []):
        if not Path(row.get("executable", row.get("executable_realpath", ""))).is_absolute():
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "tool paths must be absolute")
    for category in ("tools", "plugins", "services"):
        keys = [tuple(sorted(row["scoped_requirement_id"].items())) for row in document.get(category, [])]
        if len(keys) != len(set(keys)):
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "duplicate scoped local binding")


class EnvironmentSelection(StrictEnvironmentDTO):
    __slots__ = ()
    SCHEMA = SELECTION_SCHEMA
    def _validate(self, document):
        PackageTarget.from_dict(document["target"])
        _local_paths(document)
        expected = {"executable"} if document["mode"] == "existing" else {"base_executable", "prefix"}
        if set(document["python_selection"]) != expected:
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "Python selection differs from mode")


class LocalEnvironmentBinding(StrictEnvironmentDTO):
    __slots__ = ()
    SCHEMA = BINDING_SCHEMA
    def _validate(self, document):
        PackageTarget.from_dict(document["target"])
        _local_paths(document)
        import re
        if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,239}", document[key]) is None for key in ("binding_id", "binding_revision")):
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "binding IDs must be opaque safe identifiers")


class EnvironmentResolutionLock(StrictEnvironmentDTO):
    __slots__ = ()
    SCHEMA = RESOLUTION_SCHEMA
    def _validate(self, document):
        PackageTarget.from_dict(document["target"])
        keys = [(row["kind"], repr(sorted(row["scoped_requirement_id"].items())) if row["scoped_requirement_id"]
                 else row["identity"].get("name", row["identity"].get("profile_id", ""))) for row in document["selections"]]
        if len(keys) != len(set(keys)):
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "duplicate resolution selection")
        # Reconstructed external DTOs receive the same privacy/source checks
        # as product-built locks, including nested Requires-Dist entries.
        from packaging.requirements import Requirement, InvalidRequirement
        from packaging.specifiers import SpecifierSet, InvalidSpecifier
        from packaging.utils import canonicalize_name
        from packaging.version import Version, InvalidVersion
        for row in document["selections"]:
            stack = list(row["identity"].values())
            while stack:
                value = stack.pop()
                if type(value) is list:
                    stack.extend(value)
                elif isinstance(value, str) and (value.startswith(("/", "\\")) or "://" in value):
                    raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "private locator in resolution identity")
            if row["kind"] == "distribution":
                identity = row["identity"]
                try:
                    if canonicalize_name(identity["name"]) != identity["name"]:
                        raise ValueError()
                    Version(identity["version"])
                    SpecifierSet(identity["requires_python"])
                    if any(Requirement(text).url for text in identity["requires_dist"]):
                        raise ValueError()
                except (ValueError, InvalidRequirement, InvalidSpecifier, InvalidVersion) as exc:
                    raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "invalid or direct-source distribution lock") from exc



class EnvironmentCheckReport(StrictEnvironmentDTO):
    __slots__ = ()
    SCHEMA = CHECK_SCHEMA
    def _validate(self, document):
        PackageTarget.from_dict(document["target"])
        bound = [document[key] is not None for key in ("binding_revision", "binding_digest", "resolution_digest")]
        if any(bound) != all(bound):
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "partial binding identity in check")


class EnvironmentPreparationPlan(StrictEnvironmentDTO):
    __slots__ = ()
    SCHEMA = PLAN_SCHEMA
    def _validate(self, document):
        PackageTarget.from_dict(document["target"])
        ids = [row["action_id"] for row in document["actions"]]
        if len(set(ids)) != len(ids):
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "duplicate plan action")
        if document["resolution_digest"] is None and not document["unresolved"]:
            raise EnvironmentContractError("INVALID_ENVIRONMENT_CONTRACT", "unresolved plan requires reasons")


class PreparationReceipt(StrictEnvironmentDTO):
    __slots__ = ()
    SCHEMA = RECEIPT_SCHEMA
    def _validate(self, document):
        PackageTarget.from_dict(document["target"])


def observation(kind=None, identity=None, *, present=None):
    value = {key: None for key in OBSERVED["properties"]}
    value["present"] = present
    if kind is not None:
        value[kind] = identity
    return value


def public_check_summary(check):
    """White-list statuses only; never serialize receiver paths/references."""
    value = check.to_dict()
    return {"target": value["target"], "aggregate": value["aggregate"],
        "checks": [{key: row[key] for key in ("scoped_requirement_id", "check_id", "status", "evidence_level", "reason_code")}
                   for row in value["checks"] if not row["check_id"].startswith("installed_distribution:")],
        "not_checked": value["not_checked"], "execution_permitted": False}
