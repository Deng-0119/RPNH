"""Read-only receiver checks using a selected interpreter and trusted probes.

No plugin factory, provider call, install, compile, lowering or Registry writer is
used here. Interpreters and probe adapters are receiver-trusted executable code;
this is deliberately a different boundary from inert package preview.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Callable, Mapping

from packaging.markers import default_environment
from packaging.requirements import Requirement, InvalidRequirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version, InvalidVersion

from .environment_contracts import EnvironmentContractError
from .environment_local_contracts import (
    EnvironmentSelection, LocalEnvironmentBinding, EnvironmentCheckReport,
    EnvironmentResolutionLock, observation,
)
from .share_packages import canonical_bytes, sha256, strict_json

PROBE_CONTRACT = "rpnh/selected_python_metadata_probe/v1"
# Product-owned constant. Nothing in package material is interpolated as code.
_METADATA_PROGRAM = r'''
import importlib.metadata as m, json, os, platform, sys, sysconfig
rows=[]
for d in m.distributions():
    name=d.metadata.get('Name')
    if name:
        rows.append({'name':name,'version':d.version,'requires_dist':sorted(d.requires or []),
                     'requires_python':d.metadata.get('Requires-Python') or '', 'artifact_digest':None})
value={'python':{'implementation':sys.implementation.name,'version':platform.python_version(),
                'abi':sysconfig.get_config_var('SOABI') or 'unknown','platform':sysconfig.get_platform()},
       'executable_realpath':os.path.realpath(sys.executable),'prefix_realpath':os.path.realpath(sys.prefix),
       'platform':{'os_family':{'Darwin':'macos','Windows':'windows','Linux':'linux','FreeBSD':'freebsd'}.get(platform.system(),platform.system().lower()),
                   'architecture':platform.machine().lower()},
       'markers':{'implementation_name':sys.implementation.name,'implementation_version':platform.python_version(),
          'os_name':os.name,'platform_machine':platform.machine(),'platform_python_implementation':platform.python_implementation(),
          'platform_release':platform.release(),'platform_system':platform.system(),'platform_version':platform.version(),
          'python_full_version':platform.python_version(),'python_version':'.'.join(platform.python_version_tuple()[:2]),'sys_platform':sys.platform},
       'distributions':rows}
try:
    from packaging.tags import sys_tags
    value['supported_tags']=[str(t) for t in sys_tags()]
except ImportError:
    try:
        from pip._vendor.packaging.tags import sys_tags
        value['supported_tags']=[str(t) for t in sys_tags()]
    except ImportError:
        value['supported_tags']=[]
print(json.dumps(value,sort_keys=True,separators=(',',':')))
'''


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ProbePolicy:
    """Trusted local callables, never deserialized from shared JSON.

    Adapters return a fixed identity and status, not raw command output. A
    selected HOST recreates this policy through its installed profile factory.
    Service adapters inspect local configuration only; no availability probes.
    """
    capabilities: Mapping[str, Callable] = field(default_factory=dict)
    tools: Mapping[str, Callable] = field(default_factory=dict)
    os_packages: Mapping[str, Callable] = field(default_factory=dict)
    plugins: Mapping[str, Callable] = field(default_factory=dict)
    services: Mapping[str, Callable] = field(default_factory=dict)
    credential_present: Callable[[str], bool] | None = None
    host_profiles: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    def __post_init__(self):
        if not 0 < self.timeout_seconds <= 120:
            raise ValueError("probe timeout must be bounded")
        for table in (self.capabilities, self.tools, self.os_packages, self.plugins, self.services):
            if any(not isinstance(k, str) or not callable(v) for k, v in table.items()):
                raise TypeError("probe policy requires explicit trusted callable adapters")


def selected_python(selection_or_binding, *, pending_target=True):
    d = selection_or_binding.to_dict()
    if isinstance(selection_or_binding, EnvironmentSelection):
        p = d["python_selection"]
        if d["mode"] == "existing":
            return p["executable"]
        prefix = Path(p["prefix"])
        target = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        # A not-created venv is missing, never the base interpreter as target.
        return str(target)
    p = d["python"]
    prefix = Path(p["prefix_realpath"])
    candidate = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if candidate.exists() and os.path.realpath(candidate) == p["executable_realpath"]:
        return str(candidate)
    return p["executable_realpath"]


def probe_python(executable, *, timeout=30):
    if not Path(executable).is_absolute() or not Path(executable).is_file():
        raise EnvironmentContractError("ENVIRONMENT_PYTHON_MISSING", "selected interpreter is missing")
    try:
        result = subprocess.run([str(executable), "-I", "-c", _METADATA_PROGRAM],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentContractError("ENVIRONMENT_PROBE_FAILED", "selected interpreter probe failed") from exc
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise EnvironmentContractError("ENVIRONMENT_PROBE_FAILED", "selected interpreter probe failed")
    try:
        value = strict_json(result.stdout, path="selected-python-probe")
        if set(value) != {"python", "executable_realpath", "prefix_realpath", "platform", "markers", "distributions", "supported_tags"}:
            raise ValueError()
        inventory = {}
        for row in value["distributions"]:
            name = canonicalize_name(row["name"])
            if name in inventory and inventory[name] != {**row, "name": name}:
                raise ValueError("ambiguous installed distribution")
            if len(canonical_bytes(row)) > 256 * 1024:
                raise ValueError()
            Version(row["version"])
            inventory[name] = {**row, "name": name}
        value["distributions"] = inventory
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise EnvironmentContractError("ENVIRONMENT_PROBE_FAILED", "invalid bounded interpreter metadata") from exc


def requirement_rows(requirements):
    for scoped in requirements.requirements:
        document = scoped.document
        yield scoped, "python", document["python"]
        for name in ("distributions", "system_requirements", "tools", "plugins", "services"):
            for row in document[name]:
                yield scoped, name, row


def _scope_key(scope):
    return tuple(scope[key] for key in ("manifest_digest", "entry_id", "requirement_id"))


def _local_row(document, category, scope):
    return next((row for row in document[category] if row["scoped_requirement_id"] == scope), None)


def _check(scope, check_id, status, reason, *, kind=None, identity=None,
           evidence="configuration_observed", requirement=None, present=None):
    return {"scoped_requirement_id": scope, "check_id": check_id, "status": status,
        "evidence_level": evidence, "observed": observation(kind, identity, present=present),
        "expected": {"requirement_digest": None if requirement is None else sha256(canonical_bytes(requirement))},
        "reason_code": reason}


def installed_closure(requirements, inventory):
    """Actual installed transitive closure, including active environment markers.

    A missing or incompatible dependency blocks; direct URL dependencies never
    silently turn metadata into an install command or a shareable locator.
    """
    selected, issues = {}, []
    queue = [(None, Requirement(row["name"] + row["version_specifier"]), ())
             for _, kind, row in requirement_rows(requirements) if kind == "distributions"]
    visited = set()
    while queue:
        parent, requirement, extras = queue.pop(0)
        key = (str(requirement), tuple(sorted(extras)))
        if key in visited:
            continue
        visited.add(key)
        if requirement.url:
            issues.append("ENVIRONMENT_DEPENDENCY_SOURCE_UNSUPPORTED")
            continue
        if requirement.marker and not any(requirement.marker.evaluate({**inventory["markers"], "extra": extra})
                for extra in ("", *extras)):
            continue
        name = canonicalize_name(requirement.name)
        actual = inventory["distributions"].get(name)
        if actual is None:
            issues.append("ENVIRONMENT_DISTRIBUTION_MISSING")
            continue
        if not requirement.specifier.contains(actual["version"], prereleases=True):
            issues.append("ENVIRONMENT_DEPENDENCY_CONFLICT")
        if actual["requires_python"] and not SpecifierSet(actual["requires_python"]).contains(inventory["python"]["version"], prereleases=True):
            issues.append("ENVIRONMENT_PYTHON_INCOMPATIBLE")
        selected[name] = actual
        for text in actual["requires_dist"]:
            try:
                dep = Requirement(text)
            except InvalidRequirement:
                issues.append("ENVIRONMENT_DEPENDENCY_METADATA_INVALID")
                continue
            queue.append((name, dep, tuple(requirement.extras)))
    return selected, sorted(set(issues))


def check_environment(requirements, selection_or_binding, *, probe_policy=None, resolution=None):
    policy = probe_policy or selected_metadata_policy(selection_or_binding)
    if not isinstance(selection_or_binding, (EnvironmentSelection, LocalEnvironmentBinding)):
        raise TypeError("check needs an immutable selection or binding")
    local = selection_or_binding.to_dict()
    if local["target"] != requirements.target.to_dict():
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "selection targets different exact material")
    bound = isinstance(selection_or_binding, LocalEnvironmentBinding)
    if resolution is not None and (not isinstance(resolution, EnvironmentResolutionLock)
            or resolution.to_dict()["target"] != local["target"]
            or (bound and resolution.digest != local["resolution_digest"])):
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "resolution does not bind this environment")
    checks, contracts = [], {PROBE_CONTRACT}
    inventory = None
    try:
        inventory = probe_python(selected_python(selection_or_binding), timeout=policy.timeout_seconds)
        if bound and any(inventory[key] != local["python"][key] for key in ("executable_realpath", "prefix_realpath")):
            checks.append(_check(None, "python_binding", "stale", "ENVIRONMENT_BINDING_STALE"))
    except EnvironmentContractError as exc:
        checks.append(_check(None, "python_binding", "missing" if exc.code == "ENVIRONMENT_PYTHON_MISSING" else "failed", exc.code))
    for scoped, category, req in requirement_rows(requirements):
        scope = scoped.scoped_id(req["requirement_id"])
        status, reason, kind, identity, evidence = "unsupported", "ENVIRONMENT_PROBE_UNSUPPORTED", None, None, "configuration_observed"
        if category == "python":
            if inventory:
                kind, identity, evidence = "python", inventory["python"], "local_probe"
                ok = identity["implementation"] == req["implementation"] and SpecifierSet(req["version_specifier"]).contains(identity["version"], prereleases=True)
                status, reason = ("satisfied", "PYTHON_OBSERVED") if ok else ("incompatible", "ENVIRONMENT_PYTHON_INCOMPATIBLE")
            else:
                status, reason = "missing", "ENVIRONMENT_PYTHON_MISSING"
        elif category == "distributions":
            identity = None if inventory is None else inventory["distributions"].get(req["name"])
            if identity:
                kind, evidence = "distribution", "installed_metadata"
                try:
                    unsupported_source = any(Requirement(text).url for text in identity["requires_dist"])
                except InvalidRequirement:
                    unsupported_source = True
                if unsupported_source:
                    identity = {**identity, "requires_dist": []}
                    status, reason = "unsupported", "ENVIRONMENT_DEPENDENCY_SOURCE_UNSUPPORTED"
                else:
                    ok = SpecifierSet(req["version_specifier"]).contains(identity["version"], prereleases=True)
                    status, reason = ("satisfied", "DISTRIBUTION_METADATA_OBSERVED") if ok else ("incompatible", "ENVIRONMENT_DEPENDENCY_CONFLICT")
            else:
                status, reason = "missing", "ENVIRONMENT_DISTRIBUTION_MISSING"
        elif category == "system_requirements" and req["kind"] == "platform":
            if inventory:
                kind, identity, evidence = "platform", inventory["platform"], "local_probe"
                ok = all(identity[k] == req[k] for k in ("os_family", "architecture"))
                status, reason = ("satisfied", "PLATFORM_OBSERVED") if ok else ("incompatible", "ENVIRONMENT_PLATFORM_INCOMPATIBLE")
        else:
            adapter = None
            local_row = None
            contract = None
            if category == "system_requirements":
                contract = req.get("capability_contract_id", req.get("manager_contract_id"))
                adapter = (policy.capabilities if req["kind"] == "capability" else policy.os_packages).get(contract)
                kind = "system"
            elif category == "tools":
                contract, kind = req["tool_contract_id"], "tool"
                adapter = policy.tools.get(contract)
                local_row = _local_row(local, "tools", scope)
            elif category == "plugins":
                contract, kind = req["plugin_id"], "plugin"
                adapter = policy.plugins.get(contract)
                local_row = _local_row(local, "plugins", scope)
            elif category == "services":
                contract, kind = req["service_contract_id"], "service"
                adapter = policy.services.get(contract)
                local_row = _local_row(local, "services", scope)
            if category != "system_requirements" and local_row is None:
                status, reason, kind = "missing", "ENVIRONMENT_LOCAL_REFERENCE_MISSING", None
            elif adapter is not None:
                contracts.add(contract)
                try:
                    answer = adapter(requirement=req, binding=local_row, interpreter=selected_python(selection_or_binding))
                    if type(answer) is not dict or set(answer) != {"status", "reason_code", "identity", "evidence_level"}:
                        raise ValueError("invalid trusted probe result")
                    status, reason, identity, evidence = (answer[k] for k in ("status", "reason_code", "identity", "evidence_level"))
                    if category == "plugins" and identity is not None:
                        distribution = next(r for r in scoped.document["distributions"] if r["requirement_id"] == req["distribution_requirement_id"])
                        if (local_row["plugin_id"] != req["plugin_id"] or identity["plugin_id"] != req["plugin_id"]
                                or identity["distribution"] != distribution["name"] or identity["api_contract"] != req["api_contract"]
                                or not SpecifierSet(req["version_specifier"]).contains(identity["version"], prereleases=True)):
                            status, reason = "incompatible", "ENVIRONMENT_PLUGIN_INCOMPATIBLE"
                    if category == "tools" and identity is not None:
                        if (local_row["tool_contract_id"] != req["tool_contract_id"] or identity["tool_contract_id"] != req["tool_contract_id"]
                                or not set(req["required_capabilities"]) <= set(identity["capabilities"])):
                            status, reason = "incompatible", "ENVIRONMENT_TOOL_INCOMPATIBLE"
                    if category == "system_requirements" and identity is not None:
                        if identity["contract_id"] != contract or not identity["satisfied"]:
                            status, reason = "incompatible", "ENVIRONMENT_SYSTEM_INCOMPATIBLE"
                    if category == "services":
                        model = local_row["model_condition"]
                        if identity is None or identity["service_contract_id"] != req["service_contract_id"] or identity["model_condition"] != model or local_row["service_contract_id"] != req["service_contract_id"]:
                            status, reason = "incompatible", "ENVIRONMENT_MODEL_MISMATCH"
                        constraint = req["model_constraint"]
                        if model.lower() in {"latest", "default", "auto"} or model.lower().endswith(":latest"):
                            status, reason = "incompatible", "ENVIRONMENT_EXACT_MODEL_REQUIRED"
                        if constraint and constraint["kind"] == "exact" and constraint["model_condition"] != model:
                            status, reason = "incompatible", "ENVIRONMENT_MODEL_MISMATCH"
                        if req["authentication_required"] and (not local_row["credential_ref"] or policy.credential_present is None
                                or policy.credential_present(local_row["credential_ref"]) is not True):
                            status, reason = "missing", "ENVIRONMENT_CREDENTIAL_REFERENCE_MISSING"
                except Exception:
                    status, reason, identity = "failed", "ENVIRONMENT_PROBE_FAILED", None
            if identity is None:
                kind = None
        checks.append(_check(scope, category + ":" + req["requirement_id"], status, reason,
            kind=kind, identity=identity, evidence=evidence, requirement=req))
    if inventory:
        checks.append(_check(None, "selected_platform", "satisfied", "PLATFORM_OBSERVED", kind="platform", identity=inventory["platform"], evidence="local_probe"))
        selected, issues = installed_closure(requirements, inventory)
        checks.append(_check(None, "installed_dependency_closure", "satisfied" if not issues else "incompatible",
            issues[0] if issues else "INSTALLED_TRANSITIVE_CLOSURE_OBSERVED", evidence="installed_metadata"))
        # Record every actual transitive selection, not just author top-level rows.
        for name, identity in sorted(selected.items()):
            try:
                unsupported_source = any(Requirement(text).url for text in identity["requires_dist"])
            except InvalidRequirement:
                unsupported_source = True
            if unsupported_source:
                continue
            checks.append(_check(None, "installed_distribution:" + name, "satisfied", "DISTRIBUTION_METADATA_OBSERVED",
                kind="distribution", identity=identity, evidence="installed_metadata"))
    fingerprint = policy.host_profiles.get(local["host_profile_id"])
    checks.append(_check(None, "host_profile", "satisfied" if fingerprint else "unsupported",
        "HOST_PROFILE_METADATA_OBSERVED" if fingerprint else "HOST_PROFILE_UNAVAILABLE", kind="host_profile" if fingerprint else None,
        identity={"profile_id": local["host_profile_id"], "implementation_digest": fingerprint} if fingerprint else None))
    if bound:
        if resolution is None:
            checks.append(_check(None, "resolution_binding", "unsupported", "ENVIRONMENT_RESOLUTION_REQUIRED"))
        else:
            # Compare only rows that are locked. Author-required and transitive
            # inventory are checked separately; unrelated local packages ignored.
            observed = {(row["observed"]["distribution"]["name"]): row["observed"]["distribution"] for row in checks if row["observed"]["distribution"]}
            by_scope = {(row["check_id"], str(row["scoped_requirement_id"])): row for row in checks}
            for selected in resolution.to_dict()["selections"]:
                kind = selected["kind"]
                expected = selected["identity"]
                if kind == "distribution":
                    actual = observed.get(expected["name"])
                    # Installed metadata cannot attest original wheel bytes.
                    same = actual is not None and all(actual[k] == expected[k] for k in ("name", "version", "requires_dist", "requires_python"))
                else:
                    candidates = [row["observed"][kind] for row in checks if row["observed"][kind] is not None
                        and (selected["scoped_requirement_id"] is None or selected["scoped_requirement_id"] == row["scoped_requirement_id"])]
                    same = expected in candidates
                if not same:
                    checks.append(_check(selected["scoped_requirement_id"], "locked_identity:" + kind, "stale", "ENVIRONMENT_BINDING_STALE"))
    states = {row["status"] for row in checks}
    aggregate = ("stale" if "stale" in states else "blocked" if states & {"missing", "incompatible", "failed"}
                 else "incomplete" if states & {"unsupported", "not_checked"} else "passed_for_checked_scope")
    return EnvironmentCheckReport.from_dict({"schema_version": "rpnh/environment_check/v1", "target": local["target"],
        "selection_digest": None if bound else selection_or_binding.digest,
        "binding_revision": local["binding_revision"] if bound else None,
        "binding_digest": selection_or_binding.digest if bound else None,
        "resolution_digest": local["resolution_digest"] if bound else None,
        "checked_at": now(), "probe_contracts": sorted(contracts), "checks": checks, "aggregate": aggregate,
        "not_checked": ["remote_service_availability", "live_permission", "runtime_capacity", "installed_file_contents", "author_implementation_identity"],
        "execution_permitted": False})


def observed_resolution(requirements, check):
    """Fix observed exact choices; blocked/incomplete requirements stay unresolved."""
    d = check.to_dict()
    if d["target"] != requirements.target.to_dict():
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "check targets different material")
    rows, seen, unresolved = [], set(), []
    platform = None
    for item in d["checks"]:
        if item["status"] != "satisfied":
            unresolved.append({"scoped_requirement_id": item["scoped_requirement_id"], "reason_code": item["reason_code"]})
            continue
        for kind, identity in item["observed"].items():
            if kind == "present" or identity is None:
                continue
            if kind == "platform":
                platform = identity
            scope = None if kind in {"distribution", "host_profile"} else item["scoped_requirement_id"]
            key = (kind, str(scope), identity.get("name", ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"scoped_requirement_id": scope, "kind": kind, "identity": identity,
                         "source_contract": PROBE_CONTRACT, "evidence_level": item["evidence_level"]})
    if platform is None:
        # Python sysconfig platform is not guessed into a machine declaration.
        platform = {"os_family": "unknown", "architecture": "unknown"}
    return EnvironmentResolutionLock.from_dict({"schema_version": "rpnh/environment_resolution_lock/v1",
        "target": d["target"], "resolver_contract": "rpnh/installed_environment_resolver/v1", "target_platform": platform,
        "selections": rows, "unresolved": unresolved,
        "coverage": ["installed_metadata_only", "remote_service_availability_not_checked", "author_implementation_identity_not_supplied"]})


_READONLY_ADAPTER_PROGRAM = r"""
import contextlib, json, os, sys
from cpn.rpnh.collaboration.environment_host import readonly_probe_request
from cpn.rpnh.collaboration.share_packages import strict_json
request=strict_json(sys.stdin.buffer.read(4*1024*1024),path='readonly-probe-request')
with open(os.devnull,'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
    result=readonly_probe_request(request)
print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
"""


def selected_metadata_policy(selection_or_binding):
    """Reconstruct receiver-trusted read-only adapters in selected Python.

    The separate ``rpnh.environment_probes`` group is a trusted probe boundary,
    not plugin discovery/registration. Ordinary check never loads a HOST profile
    or plugin factory. Package data supplies no probe/import locator.
    """
    local = selection_or_binding.to_dict()
    executable = selected_python(selection_or_binding)
    def invoke(request):
        result = subprocess.run([executable, "-I", "-c", _READONLY_ADAPTER_PROGRAM],
            input=canonical_bytes({"local": local, **request}), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=30, check=False)
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise EnvironmentContractError("ENVIRONMENT_PROBE_FAILED", "selected trusted readonly probe failed")
        return strict_json(result.stdout, path="selected-readonly-probe")
    inventory = {"host_profiles": {}, "adapters": {}, "credentials": False}
    if Path(executable).is_file():
        try:
            candidate = invoke({"kind": "inventory"})
            if type(candidate) is dict and set(candidate) == set(inventory):
                inventory = candidate
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    def remote(category, contract):
        def probe(*, requirement, binding, interpreter):
            return invoke({"kind": "probe", "category": category, "contract": contract,
                           "requirement": requirement, "binding": binding})
        return probe
    kwargs = {category: {contract: remote(category, contract) for contract in inventory["adapters"].get(category, [])}
              for category in ("capabilities", "tools", "os_packages", "plugins", "services")}
    return ProbePolicy(**kwargs, host_profiles=inventory["host_profiles"],
        credential_present=(lambda ref: invoke({"kind": "credential", "reference": ref})) if inventory["credentials"] else None)
