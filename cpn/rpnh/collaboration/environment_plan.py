"""Pure plans and exact local-wheel resolution; never network or installation."""
from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
import uuid
import zipfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from .environment_contracts import EnvironmentContractError
from .environment_local_contracts import (
    EnvironmentResolutionLock, EnvironmentPreparationPlan, EnvironmentSelection,
)
from .environment_check import probe_python, selected_python, requirement_rows, observed_resolution
from .share_packages import sha256

RESOLVER = "rpnh/local_wheel_resolver/v1"


@dataclass(frozen=True)
class ConcreteSelections:
    resolution: EnvironmentResolutionLock
    artifacts: tuple[tuple[str, str], ...] = ()  # digest -> private local wheel
    def artifact_paths(self):
        return dict(self.artifacts)


def inspect_wheel(path, python):
    """Read wheel metadata only. Never execute a setup/build backend."""
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
        raise EnvironmentContractError("ENVIRONMENT_ARTIFACT_UNAVAILABLE", "explicit local wheel unavailable")
    try:
        name, version, _build, tags = parse_wheel_filename(path.name)
        # Probe supports target tags when packaging is installed; portable pure
        # Python wheels are safe fallback before RPNH itself is installed.
        target_tags = set(python.get("supported_tags", ()))
        py = python["python"]["version"].split(".")
        generic = {f"py{py[0]}-none-any", f"py{py[0]}{py[1]}-none-any", f"cp{py[0]}{py[1]}-none-any"}
        if not {str(t) for t in tags} & (target_tags | generic):
            raise EnvironmentContractError("ENVIRONMENT_WHEEL_PLATFORM_INCOMPATIBLE", "wheel tags do not match selected Python")
        with zipfile.ZipFile(path) as archive:
            matches = [info for info in archive.infolist() if info.filename.endswith(".dist-info/METADATA")]
            if len(matches) != 1 or matches[0].file_size > 2 * 1024 * 1024:
                raise ValueError()
            metadata = BytesParser().parsebytes(archive.read(matches[0]))
        if canonicalize_name(metadata["Name"]) != name or Version(metadata["Version"]) != version:
            raise ValueError()
        requires_python = metadata.get("Requires-Python") or ""
        if requires_python and not SpecifierSet(requires_python).contains(python["python"]["version"], prereleases=True):
            raise EnvironmentContractError("ENVIRONMENT_PYTHON_INCOMPATIBLE", "wheel Requires-Python excludes selected Python")
        dependencies = sorted(metadata.get_all("Requires-Dist", []))
        for text in dependencies:
            if Requirement(text).url:
                raise EnvironmentContractError("ENVIRONMENT_DEPENDENCY_SOURCE_UNSUPPORTED", "wheel has a direct-source dependency")
        return {"name": name, "version": str(version), "requires_dist": dependencies,
                "requires_python": requires_python, "artifact_digest": sha256(path.read_bytes())}
    except EnvironmentContractError:
        raise
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
        raise EnvironmentContractError("ENVIRONMENT_ARTIFACT_INVALID", "invalid bounded wheel metadata") from exc


def resolve_local_wheels(requirements, selection, check, wheel_paths=(), *, allow_existing_changes=False):
    """Resolve solely against explicitly supplied wheel bytes and installed data.

    Conservative bounded solver: ambiguous/inconsistent graphs stay unresolved.
    No index, download, build, shell, hidden upgrade or alternate venv is used.
    """
    if not isinstance(selection, EnvironmentSelection):
        raise TypeError("wheel resolution requires an initial environment selection")
    sd = selection.to_dict()
    if sd["target"] != requirements.target.to_dict() or check.to_dict()["selection_digest"] != selection.digest:
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "resolver selection/check differs")
    executable = sd["python_selection"].get("base_executable", sd["python_selection"].get("executable"))
    inventory = probe_python(executable)
    candidates, paths = {}, {}
    for path in wheel_paths:
        identity = inspect_wheel(str(Path(path).absolute()), inventory)
        candidates.setdefault(identity["name"], []).append(identity)
        paths[identity["artifact_digest"]] = str(Path(path).absolute())
    for values in candidates.values():
        values.sort(key=lambda item: (Version(item["version"]), item["artifact_digest"]), reverse=True)
    installed = inventory["distributions"] if sd["mode"] == "existing" else {}
    roots = [Requirement(row["name"] + row["version_specifier"])
             for _, category, row in requirement_rows(requirements) if category == "distributions"]
    chosen, problems = {}, []
    for _iteration in range(64):
        constraints, extras, queue = {}, {}, [(r, ()) for r in roots]
        seen = set()
        while queue:
            requirement, parent_extras = queue.pop(0)
            key = (str(requirement), tuple(parent_extras))
            if key in seen:
                continue
            seen.add(key)
            if requirement.marker and not any(requirement.marker.evaluate({**inventory["markers"], "extra": extra}) for extra in ("", *parent_extras)):
                continue
            name = canonicalize_name(requirement.name)
            constraints.setdefault(name, []).append(requirement.specifier)
            extras.setdefault(name, set()).update(requirement.extras)
            if len(constraints) > 256:
                raise EnvironmentContractError("ENVIRONMENT_SELECTION_UNRESOLVED", "dependency graph exceeds resolver bound")
            current = chosen.get(name)
            if current:
                queue.extend((Requirement(text), tuple(sorted(extras[name]))) for text in current["requires_dist"])
        next_choices, problems = {}, []
        for name, specs in sorted(constraints.items()):
            existing_options = [installed[name]] if name in installed else []
            options = (candidates.get(name, []) + existing_options) if allow_existing_changes else (existing_options + candidates.get(name, []))
            good = [row for row in options if all(spec.contains(row["version"], prereleases=True) for spec in specs)]
            if not good:
                problems.append({"scoped_requirement_id": None, "reason_code": "ENVIRONMENT_SELECTION_UNRESOLVED"})
                continue
            choice = good[0]
            if name in installed and choice != installed[name] and not allow_existing_changes:
                problems.append({"scoped_requirement_id": None, "reason_code": "ENVIRONMENT_EXISTING_CHANGE_REQUIRES_EXPLICIT_PLAN"})
                continue
            if choice["requires_python"] and not SpecifierSet(choice["requires_python"]).contains(inventory["python"]["version"], prereleases=True):
                problems.append({"scoped_requirement_id": None, "reason_code": "ENVIRONMENT_PYTHON_INCOMPATIBLE"})
                continue
            next_choices[name] = choice
        if next_choices == chosen:
            break
        chosen = next_choices
    else:
        problems.append({"scoped_requirement_id": None, "reason_code": "ENVIRONMENT_DEPENDENCY_CONFLICT"})
    observed = observed_resolution(requirements, check).to_dict()
    selections = [row for row in observed["selections"] if row["kind"] not in {"python", "distribution", "platform"}]
    selections.append({"kind": "platform", "scoped_requirement_id": None, "identity": inventory["platform"],
                       "source_contract": RESOLVER, "evidence_level": "local_probe"})
    for scoped, category, row in requirement_rows(requirements):
        if category != "python":
            continue
        if (row["implementation"] != inventory["python"]["implementation"] or not SpecifierSet(row["version_specifier"]).contains(inventory["python"]["version"], prereleases=True)):
            problems.append({"scoped_requirement_id": scoped.scoped_id(row["requirement_id"]), "reason_code": "ENVIRONMENT_PYTHON_INCOMPATIBLE"})
        selections.append({"kind": "python", "scoped_requirement_id": scoped.scoped_id(row["requirement_id"]),
            "identity": inventory["python"], "source_contract": RESOLVER, "evidence_level": "local_probe"})
    for identity in chosen.values():
        selections.append({"kind": "distribution", "scoped_requirement_id": None, "identity": identity,
            "source_contract": RESOLVER, "evidence_level": "artifact_verified" if identity["artifact_digest"] else "installed_metadata"})
    # Missing target interpreter/distributions are resolved by the exact wheel
    # plan. System, service, profile and other requirements remain real blockers.
    ignored = {"ENVIRONMENT_PYTHON_MISSING", "ENVIRONMENT_DISTRIBUTION_MISSING", "ENVIRONMENT_DEPENDENCY_CONFLICT"}
    platforms = [(scoped.scoped_id(row["requirement_id"]), row) for scoped, category, row in requirement_rows(requirements)
                 if category == "system_requirements" and row["kind"] == "platform"]
    platform_scopes = [scope for scope, _ in platforms]
    problems.extend(issue for issue in observed["unresolved"] if issue["reason_code"] not in ignored
                    and issue["scoped_requirement_id"] not in platform_scopes
                    and not (issue["scoped_requirement_id"] is None and issue["reason_code"] == "ENVIRONMENT_PYTHON_INCOMPATIBLE"))
    for scope, required_platform in platforms:
        if any(inventory["platform"][key] != required_platform[key] for key in ("os_family", "architecture")):
            problems.append({"scoped_requirement_id": scope, "reason_code": "ENVIRONMENT_PLATFORM_INCOMPATIBLE"})
    selected_paths = [paths[row["artifact_digest"]] for row in chosen.values() if row["artifact_digest"]]
    fingerprint = selected_wheel_profile_fingerprint(selected_paths, sd["host_profile_id"])
    if fingerprint is not None:
        selections = [row for row in selections if row["kind"] != "host_profile"]
        selections.append({"kind": "host_profile", "scoped_requirement_id": None,
            "identity": {"profile_id": sd["host_profile_id"], "implementation_digest": fingerprint},
            "source_contract": RESOLVER, "evidence_level": "artifact_verified"})
        problems = [row for row in problems if row["reason_code"] != "HOST_PROFILE_UNAVAILABLE"]
    for scoped, category, requirement in requirement_rows(requirements):
        if category != "plugins":
            continue
        scope = scoped.scoped_id(requirement["requirement_id"])
        distribution = next(row for row in scoped.document["distributions"] if row["requirement_id"] == requirement["distribution_requirement_id"])
        choice = chosen.get(distribution["name"])
        binding = next((row for row in sd["plugins"] if row["scoped_requirement_id"] == scope), None)
        if choice is None or not choice["artifact_digest"] or binding is None:
            continue
        identity = wheel_plugin_identity(paths[choice["artifact_digest"]], requirement, binding, distribution["name"])
        if identity is not None:
            selections = [r for r in selections if not (r["kind"] == "plugin" and r["scoped_requirement_id"] == scope)]
            selections.append({"kind": "plugin", "scoped_requirement_id": scope, "identity": identity,
                "source_contract": "rpnh/wheel_plugin_metadata/v1", "evidence_level": "artifact_verified"})
            # Exact inert wheel metadata can supply this missing plugin. It
            # cannot explain a crashed/ambiguous probe or another requirement.
            # Keep the original check as the installed-state observation;
            # readiness still requires installation and a fresh HOST check.
            replaceable = {row["reason_code"] for row in check.to_dict()["checks"]
                if row["scoped_requirement_id"] == scope and row["check_id"].startswith("plugins:")
                and (row["status"], row["reason_code"]) in {
                    ("unsupported", "ENVIRONMENT_PROBE_UNSUPPORTED"),
                    ("missing", "ENVIRONMENT_PLUGIN_MISSING")}}
            problems = [r for r in problems if not (r["scoped_requirement_id"] == scope and r["reason_code"] in replaceable)]
    resolution = EnvironmentResolutionLock.from_dict({"schema_version": "rpnh/environment_resolution_lock/v1",
        "target": sd["target"], "resolver_contract": RESOLVER, "target_platform": inventory["platform"],
        "selections": selections, "unresolved": problems, "coverage": ["selected_local_wheel_hashes", "installed_metadata_only",
            "remote_service_availability_not_checked", "author_implementation_identity_not_supplied"]})
    used = {row["artifact_digest"] for row in chosen.values() if row["artifact_digest"]}
    return ConcreteSelections(resolution, tuple(sorted((digest, path) for digest, path in paths.items() if digest in used)))


def _action(action_id, kind, adapter, *, path=None, scope=None, name=None, **options):
    return {"action_id": action_id, "kind": kind, "trusted_adapter_contract": adapter,
        "target": {"path": path, "scoped_requirement_id": scope, "name": name},
        "options": {key: options.get(key) for key in ("base_executable", "version", "artifact_digest", "source_path", "reference", "host_profile_digest")},
        "expected_effect": {"create_venv": "create_absent_isolated_python", "install_distribution": "install_exact_wheel_without_dependency_resolution",
            "assemble_trusted_host": "load_explicit_trusted_profile_without_business_run"}.get(kind, "receiver_verification_required"),
        "retained_on_failure": "retain_completed_actions_and_user_files"}


def plan_environment(requirements, selection_or_binding, check, *, concrete_selections=None):
    sd, cd = selection_or_binding.to_dict(), check.to_dict()
    if sd["target"] != requirements.target.to_dict() or cd["target"] != sd["target"]:
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "plan material identity differs")
    initial = isinstance(selection_or_binding, EnvironmentSelection)
    if (initial and cd["selection_digest"] != selection_or_binding.digest) or (not initial and cd["binding_digest"] != selection_or_binding.digest):
        raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE", "check does not name this exact selection/binding")
    if concrete_selections is None:
        concrete_selections = ConcreteSelections(observed_resolution(requirements, check))
    if isinstance(concrete_selections, EnvironmentResolutionLock):
        concrete_selections = ConcreteSelections(concrete_selections)
    resolution = concrete_selections.resolution
    rd = resolution.to_dict()
    if rd["target"] != sd["target"]:
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "resolution targets different material")
    paths, actions, unresolved = concrete_selections.artifact_paths(), [], list(rd["unresolved"])
    if initial and sd["mode"] == "new_venv":
        prefix = Path(sd["python_selection"]["prefix"])
        if prefix.exists() or prefix.is_symlink():
            unresolved.append({"scoped_requirement_id": None, "reason_code": "ENVIRONMENT_TARGET_ALREADY_EXISTS"})
        actions.append(_action("create-venv", "create_venv", "rpnh/venv_adapter/v1", path=str(prefix),
                               base_executable=sd["python_selection"]["base_executable"]))
    actual = {row["observed"]["distribution"]["name"]: row["observed"]["distribution"] for row in cd["checks"] if row["observed"]["distribution"]}
    for row in rd["selections"]:
        if row["kind"] != "distribution":
            continue
        identity = row["identity"]
        installed = actual.get(identity["name"])
        if installed and identity["artifact_digest"] is None and all(installed[k] == identity[k] for k in ("name", "version", "requires_dist", "requires_python")):
            continue
        digest = identity["artifact_digest"]
        if digest is None or digest not in paths:
            unresolved.append({"scoped_requirement_id": row["scoped_requirement_id"], "reason_code": "ENVIRONMENT_ARTIFACT_UNAVAILABLE"})
            continue
        actions.append(_action("install-" + identity["name"], "install_distribution", "rpnh/hashed_wheel_install/v1",
            path=selected_python(selection_or_binding), name=identity["name"], version=identity["version"],
            artifact_digest=digest, source_path=paths[digest]))
    for row in cd["checks"]:
        resolved_plugin = row["check_id"].startswith("plugins:") and any(r["kind"] == "plugin" and r["scoped_requirement_id"] == row["scoped_requirement_id"] for r in rd["selections"])
        resolved_platform = row["check_id"].startswith("system_requirements:") and any(scope == row["scoped_requirement_id"] and all(rd["target_platform"][k] == req[k] for k in ("os_family", "architecture"))
            for scoped, category, req in requirement_rows(requirements) if category == "system_requirements" and req["kind"] == "platform" for scope in (scoped.scoped_id(req["requirement_id"]),))
        if row["status"] != "satisfied" and not resolved_plugin and not resolved_platform and row["check_id"].startswith(("system_requirements:", "tools:", "services:", "plugins:")):
            actions.append(_action("manual-" + str(len(actions)), "manual_system_step", "rpnh/manual_receiver_step/v1",
                scope=row["scoped_requirement_id"], name=row["reason_code"]))
    profile = next((r["identity"] for r in rd["selections"] if r["kind"] == "host_profile"), None)
    actions.append(_action("assemble-host", "assemble_trusted_host", "rpnh/trusted_host_profile/v1",
        name=sd["host_profile_id"], host_profile_digest=profile["implementation_digest"] if profile else None))
    return EnvironmentPreparationPlan.from_dict({"schema_version": "rpnh/environment_preparation_plan/v1",
        "plan_id": "plan-" + uuid.uuid4().hex, "target": sd["target"], "selection_digest": selection_or_binding.digest if initial else None,
        "binding_revision": None if initial else sd["binding_revision"], "resolution_digest": resolution.digest,
        "based_on_check_digest": check.digest, "mode": sd["mode"], "actions": actions,
        "unresolved": unresolved, "authorization_requirements": ["exact_plan_digest", "exact_action_inventory", "exact_local_paths", "trusted_host_code_loading"]})


def _wheel_entry_identity(path, group, name):
    import configparser
    from .share_packages import canonical_bytes
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        points = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
        if not points:
            return None
        if len(points) != 1 or archive.getinfo(points[0]).file_size > 256 * 1024:
            raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "bounded wheel entry-point metadata unavailable")
        parser = configparser.ConfigParser(interpolation=None); parser.optionxform = str
        parser.read_string(archive.read(points[0]).decode("utf-8"))
        if not parser.has_option(group, name):
            return None
        locator = parser.get(group, name)
        root = locator.split(":")[0].strip().split(".")[0]
        rows = []
        for filename in names:
            if filename.endswith((".py", ".so", ".pyd")) and filename.split("/")[0] in {root, root + ".py"}:
                if archive.getinfo(filename).file_size > 128 * 1024 * 1024:
                    raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "wheel implementation exceeds bound")
                rows.append((filename, sha256(archive.read(filename))))
        if not rows:
            raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "selected wheel implementation absent")
        return {"code": sha256(canonical_bytes(sorted(rows))), "entry_point": locator}


def selected_wheel_profile_fingerprint(paths, profile_id):
    """Compose selected host and read-only probe code across exact wheels."""
    from .environment_host import NATIVE_PROFILE, PROFILE_GROUP
    from .share_packages import canonical_bytes
    if profile_id == NATIVE_PROFILE:
        results = []
        for path in paths:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                files = ["cpn/rpnh/collaboration/environment_host.py", "cpn/rpnh/collaboration/environment_check.py",
                         "cpn/rpnh/collaboration/__init__.py", "cpn/rpnh/__init__.py", "cpn/__init__.py"]
                if files[0] in names:
                    results.append(sha256(canonical_bytes([(name.removeprefix("cpn/"), sha256(archive.read(name))) for name in files if name in names])))
        if len(results) > 1:
            raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "multiple selected wheels contain the builtin HOST")
        return results[0] if results else None
    hosts = [result for path in paths if (result := _wheel_entry_identity(path, PROFILE_GROUP, profile_id)) is not None]
    probes = [result for path in paths if (result := _wheel_entry_identity(path, "rpnh.environment_probes", profile_id)) is not None]
    if len(hosts) > 1 or len(probes) > 1:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "selected HOST/probe entry point is ambiguous")
    return sha256(canonical_bytes({"host": hosts[0], "probe": probes[0] if probes else None})) if hosts else None


def wheel_profile_fingerprint(path, profile_id):
    """Single-wheel convenience form; the resolver composes all selected wheels."""
    return selected_wheel_profile_fingerprint((path,), profile_id)


def validate_concrete_resolution(requirements, local, resolution, *, inventory=None):
    """Reject omitted required or transitive choices before preparation effects."""
    rd, sd = resolution.to_dict(), local.to_dict()
    if rd["target"] != requirements.target.to_dict() or rd["target"] != sd["target"] or rd["unresolved"]:
        raise EnvironmentContractError("ENVIRONMENT_SELECTION_UNRESOLVED", "resolution is incomplete for the exact target")
    distributions = {row["identity"]["name"]: row["identity"] for row in rd["selections"] if row["kind"] == "distribution"}
    for scoped, category, requirement in requirement_rows(requirements):
        scope = scoped.scoped_id(requirement["requirement_id"])
        rows = [r for r in rd["selections"] if r["scoped_requirement_id"] == scope]
        if category == "distributions":
            actual = distributions.get(requirement["name"])
            ok = actual is not None and SpecifierSet(requirement["version_specifier"]).contains(actual["version"], prereleases=True)
        elif category == "python":
            ok = any(r["kind"] == "python" and r["identity"]["implementation"] == requirement["implementation"]
                and SpecifierSet(requirement["version_specifier"]).contains(r["identity"]["version"], prereleases=True) for r in rows)
        elif category == "system_requirements" and requirement["kind"] == "platform":
            ok = all(rd["target_platform"][k] == requirement[k] for k in ("os_family", "architecture"))
        else:
            kind = {"system_requirements": "system", "tools": "tool", "plugins": "plugin", "services": "service"}[category]
            ok = any(r["kind"] == kind for r in rows)
        if not ok:
            raise EnvironmentContractError("ENVIRONMENT_SELECTION_UNRESOLVED", "a required exact environment choice is absent or incompatible")
    profiles = [r for r in rd["selections"] if r["kind"] == "host_profile" and r["identity"]["profile_id"] == sd["host_profile_id"]]
    if len(profiles) != 1:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "one exact selected HOST profile identity is required")
    if inventory is not None:
        planned = {**inventory, "distributions": distributions}
        from .environment_check import installed_closure
        _closure, issues = installed_closure(requirements, planned)
        if issues:
            raise EnvironmentContractError(issues[0], "planned transitive dependency closure is incomplete or incompatible")


def wheel_plugin_identity(path, requirement, binding, distribution_name):
    """Resolve the fixed optional wheel plugin metadata contract without load.

    The wheel declares metadata; later trusted factory/Registration assembly is
    still separate. Only receiver-selected exact artifact bytes are inspected.
    """
    import configparser
    from .share_packages import strict_json, canonical_bytes
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        descriptors = [name for name in names if name.endswith(".dist-info/rpnh_environment_plugins.json")]
        if not descriptors:
            return None
        if len(descriptors) != 1 or archive.getinfo(descriptors[0]).file_size > 256 * 1024:
            raise EnvironmentContractError("ENVIRONMENT_PLUGIN_METADATA_INVALID", "invalid bounded wheel plugin inventory")
        document = strict_json(archive.read(descriptors[0]), path="wheel-plugin-metadata")
        if (type(document) is not dict or set(document) != {"schema_version", "plugins"}
                or document["schema_version"] != "rpnh/installed_plugin_metadata/v1" or type(document["plugins"]) is not list
                or len(document["plugins"]) > 128):
            raise EnvironmentContractError("ENVIRONMENT_PLUGIN_METADATA_INVALID", "invalid closed wheel plugin inventory")
        for row in document["plugins"]:
            if type(row) is not dict or set(row) != {"plugin_id", "version", "api_contract", "entry_point"} or any(type(v) is not str or not v or len(v) > 240 for v in row.values()):
                raise EnvironmentContractError("ENVIRONMENT_PLUGIN_METADATA_INVALID", "invalid wheel plugin row")
        matches = [row for row in document["plugins"] if row["plugin_id"] == requirement["plugin_id"]]
        if len(matches) != 1:
            return None
        row = matches[0]
        if row["api_contract"] != requirement["api_contract"] or not SpecifierSet(requirement["version_specifier"]).contains(row["version"], prereleases=True):
            raise EnvironmentContractError("ENVIRONMENT_PLUGIN_INCOMPATIBLE", "wheel plugin metadata does not satisfy requirement")
        eps = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(eps) != 1 or archive.getinfo(eps[0]).file_size > 256 * 1024:
            raise EnvironmentContractError("ENVIRONMENT_PLUGIN_METADATA_INVALID", "plugin entry-point metadata unavailable")
        parser = configparser.ConfigParser(interpolation=None); parser.optionxform = str
        parser.read_string(archive.read(eps[0]).decode("utf-8"))
        if not parser.has_option("rpnh.plugins", row["entry_point"]):
            raise EnvironmentContractError("ENVIRONMENT_PLUGIN_METADATA_INVALID", "declared installed plugin entry point absent")
        module = parser.get("rpnh.plugins", row["entry_point"]).split(":")[0].strip()
        root = module.split(".")[0]
        files = [(name, sha256(archive.read(name))) for name in names if name.endswith((".py", ".so", ".pyd")) and name.split("/")[0] in {root, root + ".py"}]
        if not files:
            raise EnvironmentContractError("ENVIRONMENT_PLUGIN_METADATA_INVALID", "declared plugin implementation bytes absent")
        config = Path(binding["configuration_ref"])
        if not config.is_absolute() or not config.is_file() or config.stat().st_size > 32 * 1024 * 1024:
            return None  # An opaque configuration reference needs its trusted resolver.
        return {"plugin_id": row["plugin_id"], "version": row["version"], "api_contract": row["api_contract"],
            "distribution": distribution_name, "implementation_digest": sha256(canonical_bytes({"code": sha256(canonical_bytes(sorted(files))), "entry_point": parser.get("rpnh.plugins", row["entry_point"])})),
            "configuration_digest": sha256(config.read_bytes())}
