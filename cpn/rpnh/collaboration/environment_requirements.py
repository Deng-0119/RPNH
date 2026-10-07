"""Inert, exact-closure environment requirements for portable package v2.

No interpreter probing, package resolution, plugin loading or installation occurs
here. Package requirements are declarations, not trusted code or permissions.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .environment_contracts import (
    EnvironmentContractError, PackageTarget, StrictEnvironmentDTO,
    KEY_SCHEMA, TEXT_SCHEMA, schema_array, schema_object, validate_document,
)
from .share_packages import (
    DEFAULT_LIMITS, DRAFT7, PackageError, PackagePreview, PackageResolutionLock,
    canonical_bytes, sha256, strict_json,
)

REQUIREMENTS_SCHEMA = "rpnh/environment_requirements/v1"
COLLECTIONS = ("distributions", "system_requirements", "tools", "plugins", "services")
CONTRACT = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_./:-]*$", "maxLength": 240}
SPECIFIER = {"type": "string", "minLength": 1, "maxLength": 240}
CAPABILITIES = schema_array(CONTRACT, unique=True)
HOST_IDS = schema_array(KEY_SCHEMA, unique=True)


def environment_requirements_schema():
    common = {"requirement_id": KEY_SCHEMA, "satisfies_host_requirement_ids": HOST_IDS}
    scalar = {"anyOf": [{"type": "null"}, {"type": "boolean"},
        {"type": "number"}, {"type": "string", "maxLength": 240}]}
    constraint = {"anyOf": [scalar, schema_object({
        "operator": {"enum": ["present", "eq", "gte", "lte"]}, "value": scalar})]}
    system = {"oneOf": [
        schema_object({**common, "kind": {"const": "platform"},
            "os_family": {"enum": ["linux", "macos", "windows", "freebsd"]},
            "architecture": {"enum": ["x86_64", "aarch64", "arm64", "x86", "ppc64le", "s390x"]}}),
        schema_object({**common, "kind": {"const": "capability"},
            "capability_contract_id": CONTRACT, "constraint": constraint}),
        schema_object({**common, "kind": {"const": "os_package"},
            "manager_contract_id": CONTRACT, "name": CONTRACT, "version_constraint": SPECIFIER}),
    ]}
    model = {"oneOf": [{"type": "null"},
        schema_object({"kind": {"const": "exact"}, "model_condition": TEXT_SCHEMA}),
        schema_object({"kind": {"const": "capabilities"}, "contract_ids": CAPABILITIES})]}
    return {"$schema": DRAFT7, "$id": REQUIREMENTS_SCHEMA, **schema_object({
        "schema_version": {"const": REQUIREMENTS_SCHEMA}, "entry_id": KEY_SCHEMA,
        "python": schema_object({"requirement_id": KEY_SCHEMA, "implementation": {"const": "cpython"},
                                  "version_specifier": SPECIFIER}),
        "distributions": schema_array(schema_object({**common,
            "name": {"type": "string", "pattern": r"^[a-z0-9]+(?:-[a-z0-9]+)*$", "maxLength": 160},
            "version_specifier": SPECIFIER}), minimum=1),
        "system_requirements": schema_array(system),
        "tools": schema_array(schema_object({**common, "tool_contract_id": CONTRACT,
            "version_constraint": SPECIFIER, "required_capabilities": CAPABILITIES})),
        "plugins": schema_array(schema_object({**common, "plugin_id": CONTRACT,
            "api_contract": CONTRACT, "version_specifier": SPECIFIER,
            "distribution_requirement_id": KEY_SCHEMA})),
        "services": schema_array(schema_object({**common, "service_contract_id": CONTRACT,
            "required_capabilities": CAPABILITIES, "model_constraint": model,
            "authentication_required": {"type": "boolean"}})),
    })}


def _specifier(value):
    try:
        spec = SpecifierSet(value)
        if not spec:
            raise InvalidSpecifier()
        for item in spec:
            if item.operator == "===":
                raise InvalidSpecifier()
            Version(item.version[:-2] if item.version.endswith(".*") else item.version)
    except (InvalidSpecifier, InvalidVersion):
        raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "expected explicit PEP 440 version constraints") from None


def _validate_requirements(document, *, entry_id=None, host_requirement_ids=None):
    validate_document(document, environment_requirements_schema(), code="INVALID_ENVIRONMENT_REQUIREMENTS")
    if entry_id is not None and document["entry_id"] != entry_id:
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "requirements entry differs from manifest entry")
    rows = [document["python"], *(row for category in COLLECTIONS for row in document[category])]
    ids = [row["requirement_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "requirement IDs must be unique within their package entry")
    _specifier(document["python"]["version_specifier"])
    distributions = {row["requirement_id"]: row for row in document["distributions"]}
    names = [row["name"] for row in distributions.values()]
    if "rpnh-harness" not in names or len(names) != len(set(names)):
        raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "a unique rpnh-harness distribution requirement is mandatory")
    for row in distributions.values():
        if canonicalize_name(row["name"]) != row["name"]:
            raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "distribution names must be normalized")
        _specifier(row["version_specifier"])
    for row in document["plugins"]:
        if row["distribution_requirement_id"] not in distributions:
            raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "plugin distribution reference is absent")
        _specifier(row["version_specifier"])
    for row in document["services"]:
        model = row["model_constraint"]
        if model is not None and model["kind"] == "exact":
            condition = model["model_condition"].lower()
            if condition in {"latest", "default", "auto"} or condition.endswith(":latest"):
                raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "floating model aliases are not exact selections")
    if host_requirement_ids is not None:
        permitted = set(host_requirement_ids)
        for row in rows:
            if not set(row.get("satisfies_host_requirement_ids", ())) <= permitted:
                raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "HOST requirement reference is absent from this manifest")
    # Stable semantic traversal without modifying the raw artifact digest domain.
    for category in COLLECTIONS:
        document[category].sort(key=lambda row: row["requirement_id"])
        for row in document[category]:
            for field in ("satisfies_host_requirement_ids", "required_capabilities"):
                if field in row:
                    row[field].sort()
            model = row.get("model_constraint")
            if model and model["kind"] == "capabilities":
                model["contract_ids"].sort()
    return document


class EnvironmentRequirements(StrictEnvironmentDTO):
    SCHEMA = environment_requirements_schema()
    __slots__ = ()

    def _validate(self, document):
        _validate_requirements(document)


def validate_environment_requirements(payload, *, entry_id=None, host_requirement_ids=None, limits=DEFAULT_LIMITS):
    if type(payload) is not bytes or len(payload) > limits.artifact_bytes:
        raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "requirements artifact byte limit")
    try:
        document = strict_json(payload, path="environment-requirements", limits=limits)
    except PackageError as exc:
        raise EnvironmentContractError(exc.code, exc.message) from exc
    return _validate_requirements(document, entry_id=entry_id, host_requirement_ids=host_requirement_ids)


@dataclass(frozen=True, slots=True)
class ScopedEnvironmentRequirements:
    manifest_digest: str
    entry_id: str
    artifact_path: str
    artifact_digest: str
    payload: bytes
    host_requirements_bytes: bytes

    def __post_init__(self):
        from .share_packages import DIGEST, safe_path, manifest_schema
        if any(type(value) is not str or re.fullmatch(DIGEST, value) is None
               for value in (self.manifest_digest, self.artifact_digest)):
            raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "scoped requirement digest is invalid")
        safe_path(self.artifact_path)
        if type(self.payload) is not bytes or type(self.host_requirements_bytes) is not bytes:
            raise TypeError("scoped requirements retain immutable artifact and HOST bytes")
        if sha256(self.payload) != self.artifact_digest:
            raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "raw requirements artifact digest differs")
        if len(self.host_requirements_bytes) > DEFAULT_LIMITS.artifact_bytes:
            raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "HOST requirements byte limit")
        host = strict_json(self.host_requirements_bytes, path="host-requirements")
        validate_document(host, manifest_schema()["properties"]["requirements"], code="INVALID_ENVIRONMENT_REQUIREMENTS")
        ids = [row["requirement_id"] for row in host]
        if len(ids) != len(set(ids)):
            raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "duplicate HOST requirement ID")
        validate_environment_requirements(self.payload, entry_id=self.entry_id, host_requirement_ids=ids)
        object.__setattr__(self, "host_requirements_bytes", canonical_bytes(host))

    @property
    def document(self):
        return validate_environment_requirements(self.payload, entry_id=self.entry_id)

    @property
    def host_requirements(self):
        return tuple(strict_json(self.host_requirements_bytes, path="host-requirements"))

    def scoped_id(self, requirement_id):
        if requirement_id not in {row["requirement_id"] for _, row in self.rows()}:
            raise EnvironmentContractError("INVALID_ENVIRONMENT_REQUIREMENTS", "requirement is outside this package entry")
        return {"manifest_digest": self.manifest_digest, "entry_id": self.entry_id,
                "requirement_id": requirement_id}

    def rows(self):
        document = self.document
        return (("python", document["python"]),
                *((category, row) for category in COLLECTIONS for row in document[category]))

    def to_dict(self):
        return {"manifest_digest": self.manifest_digest, "entry_id": self.entry_id,
                "artifact_path": self.artifact_path, "artifact_digest": self.artifact_digest,
                "document": self.document, "host_requirements": list(self.host_requirements)}


@dataclass(frozen=True, slots=True)
class PackageEnvironment:
    target: PackageTarget
    requirements: tuple[ScopedEnvironmentRequirements, ...]
    package_lock: PackageResolutionLock
    previews: tuple[PackagePreview, ...]

    def __post_init__(self):
        if (type(self.target) is not PackageTarget or type(self.package_lock) is not PackageResolutionLock
                or type(self.requirements) is not tuple or type(self.previews) is not tuple
                or any(type(row) is not ScopedEnvironmentRequirements for row in self.requirements)
                or any(type(row) is not PackagePreview for row in self.previews)):
            raise TypeError("package environment fields must be immutable exact contract values")
        target = self.target.to_dict()
        inventory = [{"manifest_digest": row.manifest_digest, "entry_id": row.entry_id,
            "artifact_path": row.artifact_path, "artifact_digest": row.artifact_digest} for row in self.requirements]
        if target["requirement_artifacts"] != inventory or target["package_lock_digest"] != self.package_lock.package_lock_digest:
            raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "requirements set differs from exact target")

    @property
    def requirements_digest(self):
        return self.target.requirements_digest

    def rows(self):
        return tuple((requirement.scoped_id(row["requirement_id"]), kind, row)
                     for requirement in self.requirements for kind, row in requirement.rows())


EnvironmentRequirementsSet = PackageEnvironment


def read_environment_requirements(package_lock, verified_packages, *, entry_id=None, limits=DEFAULT_LIMITS):
    """Recheck exact selected archive/manifest/lock bytes, then read declarations.

    Unselected catalog archives never supply requirements or schema closure. A
    forged preview report, substituted ZIP, or edited lock is not accepted.
    """
    from .package_preview import preview_package
    from .package_resolution import resolve_package
    from .share_packages import LOCK_SCHEMA_V2, PACKAGE_SCHEMA_V2
    if type(package_lock) is not PackageResolutionLock:
        raise TypeError("an exact PackageResolutionLock is required")
    if len(package_lock.to_bytes()) > limits.artifact_bytes:
        raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "package lock byte limit")
    try:
        lock = strict_json(package_lock.to_bytes(), path="package-lock", limits=limits)
        wanted_entry = lock["root_entry_id"] if entry_id is None else entry_id
        node_list = lock["nodes"]
        if type(node_list) is not list or len(node_list) > limits.packages:
            raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "selected package count limit")
        wanted = {(node["manifest_digest"], node["archive_digest"]) for node in node_list}
    except (KeyError, TypeError):
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "malformed package lock") from None
    selected = {}
    count, total, expanded = 0, 0, 0
    from dataclasses import replace
    for source in verified_packages:
        count += 1
        if count > limits.packages:
            raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "package catalog count limit")
        remaining = limits.closure_expanded_bytes - expanded
        if remaining <= 0:
            raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "package catalog expanded byte limit")
        preview = preview_package(source.archive_bytes if type(source) is PackagePreview else source,
                                  limits=replace(limits, expanded_bytes=min(limits.expanded_bytes, remaining)))
        total += len(preview.archive_bytes)
        expanded += len(preview.manifest_bytes) + sum(len(data) for _, data in preview.artifacts)
        if total > limits.closure_bytes:
            raise EnvironmentContractError("PACKAGE_LIMIT_EXCEEDED", "package catalog byte limit")
        key = (preview.manifest_digest, preview.archive_digest)
        if key in wanted:
            selected[key] = preview
    if set(selected) != wanted:
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "selected exact package bytes are unavailable")
    root_candidates = [item for item in selected.values() if item.manifest_digest == lock.get("root_manifest_digest")]
    if len(root_candidates) != 1 or wanted_entry != lock.get("root_entry_id"):
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "root package or entry differs")
    root = root_candidates[0]
    others = [item for item in selected.values() if item is not root]
    computed = resolve_package(root, others, wanted_entry, limits=limits)
    if computed.to_bytes() != package_lock.to_bytes():
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "package lock differs from verified exact closure")
    if lock["schema_version"] != LOCK_SCHEMA_V2 or any(item.manifest["schema_version"] != PACKAGE_SCHEMA_V2 for item in selected.values()):
        raise EnvironmentContractError("ENVIRONMENT_REQUIREMENTS_UNDECLARED", "all selected package entries require formal environment declarations")
    requirements, inventory = [], []
    for item in sorted(selected.values(), key=lambda value: value.manifest_digest):
        manifest = item.manifest
        entry = manifest["entries"][0]
        path = entry["environment_requirements_path"]
        payload = dict(item.artifacts)[path]
        document = validate_environment_requirements(payload, entry_id=entry["entry_id"],
            host_requirement_ids=[row["requirement_id"] for row in manifest["requirements"]], limits=limits)
        digest = sha256(payload)
        requirements.append(ScopedEnvironmentRequirements(item.manifest_digest, entry["entry_id"], path,
            digest, payload, canonical_bytes(manifest["requirements"])))
        inventory.append({"manifest_digest": item.manifest_digest, "entry_id": entry["entry_id"],
            "artifact_path": path, "artifact_digest": digest})
    target = PackageTarget.from_dict({"package_lock_digest": computed.package_lock_digest,
        "root_manifest_digest": root.manifest_digest, "root_archive_digest": root.archive_digest,
        "entry_id": wanted_entry, "requirement_artifacts": inventory,
        "requirements_digest": sha256(canonical_bytes(inventory))})
    return PackageEnvironment(target, tuple(requirements), computed,
        tuple(sorted(selected.values(), key=lambda item: item.manifest_digest)))


def read_package_environment(root, local_packages=(), root_entry_id="main", *, package_lock=None, limits=DEFAULT_LIMITS):
    from .package_resolution import resolve_package
    packages = tuple(local_packages)
    lock = package_lock if package_lock is not None else resolve_package(root, packages, root_entry_id, limits=limits)
    return read_environment_requirements(lock, (root, *packages), entry_id=root_entry_id, limits=limits)
