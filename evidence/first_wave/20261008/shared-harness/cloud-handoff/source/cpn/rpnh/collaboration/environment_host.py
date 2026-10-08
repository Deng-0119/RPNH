"""Selected-interpreter HOST glue over the existing sole run owner.

Only installed, explicitly selected profile entry points can supply trusted
callables. Package JSON cannot nominate imports, executable strings or grants.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from typing import Callable

from .environment_contracts import EnvironmentContractError
from .environment_check import ProbePolicy, check_environment, selected_python, probe_python
from .environment_local_contracts import (
    LocalEnvironmentBinding, EnvironmentResolutionLock, PreparationReceipt,
    EnvironmentSelection, public_check_summary,
)
from .share_packages import canonical_bytes, sha256, strict_json
from .host_readiness import snapshot_host_declarations

PROFILE_GROUP = "rpnh.environment_hosts"
NATIVE_PROFILE = "rpnh-native/v1"
MAX_REQUEST = 16 * 1024 * 1024
TERMINAL_RESULT_MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class HostProfile:
    """Trusted receiver profile, reconstructed inside the selected Python.

    The standard driver uses ExecutionServices/Orchestrator and is usable for
    native plugins or explicitly registered ordinary HOST executors. Custom
    service factories can return configured existing ExecutionServices.
    """
    profile_id: str
    registration_factory: Callable
    probe_policy_factory: Callable = ProbePolicy
    execution_services_factory: Callable | None = None
    host_execution_bindings: Callable | None = None
    configuration_sources: Callable | None = None
    catalog: object | None = None
    before_dispatch: Callable | None = None
    def __post_init__(self):
        if not self.profile_id or not callable(self.registration_factory) or not callable(self.probe_policy_factory):
            raise TypeError("HOST profile requires explicit trusted factories")


def _distribution_fingerprint(distribution, module=None):
    """Hash installed implementation files, including all package __init__.py.

    No import or factory load occurs. Incomplete/legacy installation metadata
    cannot provide a trusted profile fingerprint and is rejected explicitly.
    """
    rows = []
    files = distribution.files
    if files is None:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "profile installed file inventory unavailable")
    root = module.split(".")[0] if module else None
    for item in files:
        text = str(item)
        if text.endswith((".py", ".so", ".pyd")) and (root is None or text.split("/")[0] in {root, root + ".py"}):
            path = Path(distribution.locate_file(item))
            if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
                raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "profile implementation file unavailable")
            rows.append((text, sha256(path.read_bytes())))
    if not rows:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "profile implementation inventory unavailable")
    return sha256(canonical_bytes(sorted(rows)))


def _builtin_fingerprint():
    package = Path(__file__).parent
    # The package initializer can change import behavior without changing the
    # entry-point module. Include it and ancestor package initialization bytes.
    files = [Path(__file__), package / "environment_check.py", package / "__init__.py", package.parent / "__init__.py",
             package.parent.parent / "__init__.py"]
    return sha256(canonical_bytes([(str(p.relative_to(package.parent.parent)), sha256(p.read_bytes()))
                                  for p in files if p.is_file()]))


def profile_fingerprints(selected_ids=None):
    selected = None if selected_ids is None else set(selected_ids)
    result = {NATIVE_PROFILE: _builtin_fingerprint()} if selected is None or NATIVE_PROFILE in selected else {}
    for ep in metadata.entry_points(group=PROFILE_GROUP):
        if selected is not None and ep.name not in selected:
            continue
        if ep.name in result:
            raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "profile identity is ambiguous")
        host_digest = _distribution_fingerprint(ep.dist, ep.module)
        probes = [probe for probe in metadata.entry_points(group="rpnh.environment_probes") if probe.name == ep.name]
        if len(probes) > 1:
            raise EnvironmentContractError("ENVIRONMENT_PROBE_UNSUPPORTED", "profile probe identity is ambiguous")
        probe_digest = _distribution_fingerprint(probes[0].dist, probes[0].module) if probes else None
        result[ep.name] = sha256(canonical_bytes({"host": {"code": host_digest, "entry_point": ep.value},
            "probe": None if not probes else {"code": probe_digest, "entry_point": probes[0].value}}))
    return result


def _native_plugin_metadata(*, requirement, binding, interpreter):
    from packaging.specifiers import SpecifierSet
    from cpn.plugins.api import API_VERSION
    value = strict_json(Path(binding["configuration_ref"]).read_bytes(), path="selected-plugin-configuration")
    if value.get("schema_version") != "rpnh/plugins/v1":
        raise ValueError("plugin configuration contract")
    rows = [row for row in value["plugins"] if row["name"] == requirement["plugin_id"]]
    if len(rows) != 1:
        raise ValueError("selected plugin configuration differs")
    selected = rows[0]
    installed = [ep for ep in metadata.entry_points(group="rpnh.plugins") if ep.name == selected["entry_point"]]
    if not installed:
        return {"status": "missing", "reason_code": "ENVIRONMENT_PLUGIN_MISSING",
                "identity": None, "evidence_level": "installed_metadata"}
    if len(installed) != 1:
        raise ValueError("selected installed plugin is ambiguous")
    ep = installed[0]
    # Identity version/API come from the selected configuration and this adapter,
    # not a loaded PluginDefinition. Preserve preflight compatibility decisions;
    # actual factory validation still happens in native_profile.registration.
    # implementation_digest fingerprints installed code, not the original wheel.
    compatible = (requirement["api_contract"] == API_VERSION
        and SpecifierSet(requirement["version_specifier"]).contains(selected["version"], prereleases=True))
    return {"status": "satisfied" if compatible else "incompatible", "reason_code": "PLUGIN_SELECTION_OBSERVED" if compatible else "ENVIRONMENT_PLUGIN_INCOMPATIBLE",
        "identity": {"plugin_id": requirement["plugin_id"], "version": selected["version"], "api_contract": API_VERSION,
            "distribution": __import__("packaging.utils", fromlist=["canonicalize_name"]).canonicalize_name(ep.dist.metadata["Name"]),
            "implementation_digest": sha256(canonical_bytes({"code": _distribution_fingerprint(ep.dist, ep.module), "entry_point": ep.value})),
            "configuration_digest": sha256(Path(binding["configuration_ref"]).read_bytes())}, "evidence_level": "configuration_observed"}


def native_profile(local):
    def policy():
        return ProbePolicy(plugins={row["plugin_id"]: _native_plugin_metadata for row in local["plugins"]},
                           host_profiles=profile_fingerprints((local["host_profile_id"],)))
    def registration():
        from cpn.plugins.catalog import load_catalog, PluginCatalog
        from cpn.plugins.runtime import plugin_registration
        seen, plugins = set(), []
        for row in local["plugins"]:
            ref = row["configuration_ref"]
            if ref in seen:
                continue
            seen.add(ref)
            doc = strict_json(Path(ref).read_bytes(), path="selected-plugin-configuration")
            plugins.extend(load_catalog(doc).plugins)
        return plugin_registration(PluginCatalog(plugins))
    return HostProfile(NATIVE_PROFILE, registration, policy)


def load_host_profile(profile_id, local, *, expected_digest=None):
    before = profile_fingerprints((profile_id,)).get(profile_id)
    if before is None:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "selected installed HOST profile unavailable")
    if expected_digest is not None and before != expected_digest:
        raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE", "HOST profile implementation changed")
    if profile_id == NATIVE_PROFILE:
        profile = native_profile(local)
    else:
        matches = [ep for ep in metadata.entry_points(group=PROFILE_GROUP) if ep.name == profile_id]
        profile = matches[0].load()(local)
    if not isinstance(profile, HostProfile) or profile.profile_id != profile_id:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "installed factory returned an incompatible profile")
    if profile_fingerprints((profile_id,)).get(profile_id) != before:
        raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE", "HOST profile changed during assembly")
    return profile


def validate_host_requirements(requirements, registration):
    """Flat author keys match actual complete Registration declarations.

    Terminal is an ordinary registered tool. Effects grant nothing. Full Module
    schema/contracts are validated by the existing compiler before start_run.
    """
    from ..registration import Registration
    from .host_readiness import diagnose_package_host_requirements
    if type(registration) is not Registration:
        raise TypeError("HOST factory must return the existing Registration")
    snapshot = snapshot_host_declarations(registration)
    diagnostic = diagnose_package_host_requirements(requirements, snapshot).to_dict()
    if diagnostic["declarations_status"] != "matched":
        raise EnvironmentContractError("HOST_CONTRACT_MISMATCH", "required HOST declarations/schema bodies do not match")
    return snapshot


def _expected_profile(resolution, profile_id):
    rows = [row["identity"] for row in resolution.to_dict()["selections"] if row["kind"] == "host_profile" and row["identity"]["profile_id"] == profile_id]
    if len(rows) != 1:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "resolution must select one exact profile implementation")
    return rows[0]["implementation_digest"]


def _revalidate_material(request):
    from .environment_requirements import read_package_environment
    from .share_packages import PackageResolutionLock
    lock = PackageResolutionLock(canonical_bytes(request["package_lock"]))
    return read_package_environment(request["archive"], request["local_packages"],
        request["entry_id"], package_lock=lock)


def _module(requirements):
    from ..module import ModuleDeclaration
    target = requirements.target.to_dict()
    root = next(p for p in requirements.previews if p.manifest_digest == target["root_manifest_digest"])
    entry = next(row for row in root.manifest["entries"] if row["entry_id"] == target["entry_id"])
    return ModuleDeclaration.from_dict(strict_json(dict(root.artifacts)[entry["declaration_path"]], path=entry["declaration_path"]))


def _register_package_schemas(requirements, registration):
    # Exact inert schemas were already checked for bounded local references.
    for preview in requirements.previews:
        for artifact in preview.manifest["artifacts"]:
            if artifact["role"] == "schema":
                doc = strict_json(dict(preview.artifacts)[artifact["path"]], path=artifact["path"])
                schema_id = doc["$id"]
                try:
                    existing = registration.declaration("schema", schema_id)
                except ValueError:
                    registration.register_schema(schema_id, doc)
                else:
                    if existing["schema"] != doc:
                        raise EnvironmentContractError("HOST_CONTRACT_MISMATCH", "HOST schema conflicts with verified package bytes")


def assemble_here(requirements, binding, resolution):
    bd = binding.to_dict()
    actual = probe_python(sys.executable)
    if any(actual[k] != bd["python"][k] for k in ("executable_realpath", "prefix_realpath")):
        raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE", "HOST is not inside selected interpreter")
    profile = load_host_profile(bd["host_profile_id"], bd,
                               expected_digest=_expected_profile(resolution, bd["host_profile_id"]))
    policy = profile.probe_policy_factory()
    if not isinstance(policy, ProbePolicy):
        raise TypeError("HOST profile must recreate trusted ProbePolicy")
    # Always use actual installed profile fingerprints rather than a profile's
    # self-reported code identity. Preserve its credential and custom adapters.
    from dataclasses import replace
    policy = replace(policy, host_profiles=profile_fingerprints((bd["host_profile_id"],)))
    check = check_environment(requirements, binding, probe_policy=policy, resolution=resolution)
    if check.to_dict()["aggregate"] != "passed_for_checked_scope":
        raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE" if check.to_dict()["aggregate"] == "stale" else "ENVIRONMENT_PREPARATION_INCOMPLETE", "selected HOST recheck did not pass")
    registration = profile.registration_factory()
    _register_package_schemas(requirements, registration)
    snapshot = validate_host_requirements(requirements, registration)
    from ..compiler import compile_module
    compile_module(_module(requirements), registration)
    return profile, policy, registration, snapshot, check


def _evidence_publisher(requirements, resolution, check, receipt, snapshot, profile):
    from .environment_evidence import preparation_evidence, EVIDENCE_SCHEMA
    evidence = preparation_evidence(requirements, resolution, check, receipt, snapshot)
    def publish(owner):
        from ..registry.resources import PublishResource, PrivateSystemOrigin
        from ..registry.resource_service import _publish_private_system
        ref = _publish_private_system(owner._core, owner.identity.task_ref, PublishResource(
            origin=PrivateSystemOrigin(owner.bootstrap_ref), payload=canonical_bytes(evidence),
            media_type="application/json", content_schema_ref=EVIDENCE_SCHEMA,
            content_schema_authority_ref=owner.schema_gateway.schema_refs[EVIDENCE_SCHEMA],
            summary="Environment preparation scope, not execution permission", lifetime_ref=owner.bootstrap_ref,
            idempotency_key="environment-preparation:" + receipt.digest))
        # Associate evidence as immutable configuration using the same owner's
        # configuration publisher; original inputs remain distinct and intact.
        if profile.configuration_sources is not None:
            immutable, mutable = profile.configuration_sources(owner)
            return immutable, mutable
        return ref, owner.publication.declaration_resource_ref
    return publish


def _terminal_result(owner, evidence_ref, stop_reason):
    """Render the shared Registry reader's current terminal for local delivery.

    The legacy ``None`` short-circuit reports no candidate from this owner
    call; ``not_terminal`` here does not assert the Registry's current state.
    Use ``read_run_execution`` to query that state even without a caller ref.
    """
    from ..registry.run_authority import read_run_execution
    from ..registry.strict_contracts import ref_payload
    if evidence_ref is None:
        return {"status": "not_terminal"}
    if stop_reason != "terminal":
        raise EnvironmentContractError("ENVIRONMENT_RESULT_INTEGRITY_ERROR", "terminal delivery requires terminal stop")
    core = owner._core
    kernel, _ = owner.operation_repository()
    try:
        # Recovered owners have no startup publication object. An absent
        # caller net expectation never substitutes a different Registry net.
        read = read_run_execution(core, kernel,
            expected_terminal_evidence_ref=evidence_ref,
            expected_run_ref=owner.identity.run_ref,
            expected_task_ref=owner.identity.task_ref,
            expected_net_ref=(None if owner.publication is None else owner.publication.net_ref),
            max_descriptor_bytes=MAX_REQUEST)
        terminal = read.terminal
        prepared = terminal.result
        details = {"terminal_result_ref": ref_payload(terminal.result_ref),
            "terminal_evidence_ref": ref_payload(terminal.evidence_ref),
            "run_execution_authority_ref": ref_payload(read.authority_ref),
            "final_checkpoint_ref": ref_payload(terminal.checkpoint_ref),
            "run_outcome": terminal.run_outcome, "media_type": prepared.media_type,
            "byte_count": prepared.size}
        result = {**details, **_bounded_terminal_json(core, prepared)}
        read.cut.assert_unchanged(core)
        return result
    except (ValueError, RuntimeError) as exc:
        if isinstance(exc, EnvironmentContractError):
            raise
        raise EnvironmentContractError("ENVIRONMENT_RESULT_INTEGRITY_ERROR", str(exc)) from exc


def _bounded_terminal_json(core, prepared):
    """Encode only a previously owner-validated product; never resolve a ref."""
    from .share_packages import PackageError
    if prepared.size > TERMINAL_RESULT_MAX_BYTES:
        return {"status": "omitted_oversize"}
    if prepared.media_type != "application/json":
        return {"status": "omitted_non_json"}
    try:
        raw = core.object_store.read_registered(prepared, max_bytes=TERMINAL_RESULT_MAX_BYTES)
    except (ValueError, RuntimeError) as exc:
        raise EnvironmentContractError("ENVIRONMENT_RESULT_INTEGRITY_ERROR", str(exc)) from exc
    try:
        output = strict_json(raw, path="terminal-result")
    except PackageError:
        return {"status": "omitted_non_json"}
    return {"status": "available", "content_sha256": sha256(raw), "output": output}


def run_here(requirements, binding, resolution, receipt, owner_request, run_dir, *, owner_ready=None,
             include_terminal_result=False):
    """Use the existing owner, admission, dispatch, settlement and terminal path."""
    if type(include_terminal_result) is not bool:
        raise TypeError("include_terminal_result must be a bool")
    destination = Path(run_dir)
    if destination.exists() or destination.is_symlink():
        raise EnvironmentContractError("ENVIRONMENT_RUN_ALREADY_EXISTS", "run directory exists; use existing owner/resume protocol")
    rd = receipt.to_dict()
    if (rd["target"] != requirements.target.to_dict() or rd["result_binding_digest"] != binding.digest
            or rd["result_binding_revision"] != binding.to_dict()["binding_revision"] or rd["failure"] is not None):
        raise EnvironmentContractError("ENVIRONMENT_PREPARATION_INCOMPLETE", "receipt does not name this completed binding")
    profile, policy, registration, snapshot, check = assemble_here(requirements, binding, resolution)
    if snapshot.declarations_digest != rd["host_declarations_digest"]:
        raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE", "actual HOST declarations changed")
    from .environment_evidence import EVIDENCE_SCHEMA, evidence_schema
    registration.register_schema(EVIDENCE_SCHEMA, evidence_schema())
    from ..run import OwnerInput, start_run
    from ..registry.module_budgets import ModuleBudgetDeclaration
    from ..control_server import OwnerEventLoop
    from cpn.components.execution_services import ExecutionServices
    from cpn.orchestrator.runner import Orchestrator
    from ..registry.strict_contracts import ref_payload
    required = {"task_input", "entry_inputs", "budgets", "model_condition", "owner_statement", "owner_name", "command_id", "resource_inputs", "inventory_input"}
    if type(owner_request) is not dict or set(owner_request) != required:
        raise EnvironmentContractError("INVALID_OWNER_REQUEST", "explicit start_run owner fields are required")
    def owner_input(row):
        if type(row) is not dict or set(row) != {"schema_id", "payload", "summary"}:
            raise EnvironmentContractError("INVALID_OWNER_REQUEST", "invalid owner input data")
        return OwnerInput(row["schema_id"], canonical_bytes(row["payload"]), row["summary"])
    b = owner_request["budgets"]
    if set(b) != {"budget_buckets", "protocol_versions", "ordinary_global_cap", "terminal_quota", "task_total_hard_cap", "finalization_budget"}:
        raise EnvironmentContractError("INVALID_OWNER_REQUEST", "explicit budget fields required")
    budgets = ModuleBudgetDeclaration(tuple(b["budget_buckets"]), tuple(b["protocol_versions"]), b["ordinary_global_cap"], b["terminal_quota"], b["task_total_hard_cap"], b["finalization_budget"])
    owner = start_run(_module(requirements), registration, run_dir=destination,
        task_input=owner_input(owner_request["task_input"]),
        entry_inputs={key: (tuple(owner_input(row) for row in value) if isinstance(value, list) else owner_input(value)) for key, value in owner_request["entry_inputs"].items()},
        resource_inputs={key: owner_input(value) for key, value in owner_request["resource_inputs"].items()},
        inventory_input=None if owner_request["inventory_input"] is None else owner_input(owner_request["inventory_input"]),
        budgets=budgets, model_condition=owner_request["model_condition"], owner_statement=owner_request["owner_statement"],
        owner_name=owner_request["owner_name"], command_id=owner_request["command_id"], catalog=profile.catalog,
        host_execution_bindings=profile.host_execution_bindings,
        configuration_sources=_evidence_publisher(requirements, resolution, check, receipt, snapshot, profile))
    # No fallback if AF_UNIX is unavailable. A registered owner is not business
    # completion; retain its genuine Registry facts and report the boundary.
    try:
        loop = OwnerEventLoop(owner, destination / "owner.sock")
    except OSError as exc:
        raise EnvironmentContractError("ENVIRONMENT_OWNER_CONTROL_UNAVAILABLE", "existing owner control transport unavailable; no business terminal was produced") from exc
    stop, runner = threading.Event(), None
    def interrupt(_signum, _frame):
        stop.set()
        if runner is not None:
            runner.executor.request_owner_stop()
    prior = signal.signal(signal.SIGINT, interrupt) if threading.current_thread() is threading.main_thread() else None
    if owner_ready is not None:
        owner_ready({"run_ref": ref_payload(owner.identity.run_ref), "task_ref": ref_payload(owner.identity.task_ref),
            "net_ref": ref_payload(owner.publication.net_ref), "control_connection": {"transport": "af_unix", "socket_path": str(destination / "owner.sock")}})
    try:
        factory = profile.execution_services_factory or ExecutionServices
        services = factory(owner=owner, event_loop=loop, interruption_requested=stop.is_set)
        def dispatch(**kwargs):
            # Recheck exact installed/profile/plugin/tool/config identities at
            # the actual dispatch boundary, after any admission-side work.
            latest = check_environment(requirements, binding, probe_policy=policy, resolution=resolution)
            if latest.to_dict()["aggregate"] != "passed_for_checked_scope" or profile_fingerprints((profile.profile_id,)).get(profile.profile_id) != _expected_profile(resolution, profile.profile_id):
                raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE", "environment changed before dispatch")
            if profile.before_dispatch:
                profile.before_dispatch(owner, kwargs["execution"])
            return services.prepare_dispatcher(**kwargs)
        with ThreadPoolExecutor(max_workers=1) as pool:
            runner = Orchestrator(owner=owner, event_loop=loop, prepare_dispatcher=dispatch,
                                  submit_operation=pool.submit, max_in_flight=1)
            if stop.is_set():
                runner.executor.request_owner_stop()
            result = runner.run()
        response = {"schema_version": "rpnh/package_run_result/v1", "target": requirements.target.to_dict(),
            "run_ref": ref_payload(owner.identity.run_ref), "task_ref": ref_payload(owner.identity.task_ref),
            "net_ref": ref_payload(owner.publication.net_ref), "stop_reason": result.stop_reason,
            "terminal_evidence_ref": None if result.terminal_evidence_ref is None else ref_payload(result.terminal_evidence_ref),
            "actual_model_call_counts": list(owner._core.event_store.actual_model_call_counts()),
            "host_python": {"executable_realpath": os.path.realpath(sys.executable), "prefix_realpath": os.path.realpath(sys.prefix)}}
        if include_terminal_result:
            response["terminal_result"] = _terminal_result(owner, result.terminal_evidence_ref, result.stop_reason)
        return response
    finally:
        loop.close()
        if prior is not None:
            signal.signal(signal.SIGINT, prior)


def host_request(requirements, archive, local_packages, binding, resolution, *, mode, receipt=None, owner_request=None, run_dir=None,
                 include_terminal_result=False):
    if type(include_terminal_result) is not bool:
        raise TypeError("include_terminal_result must be a bool")
    request = {"schema_version": "rpnh/private_environment_host_request/v1", "mode": mode,
        "archive": str(Path(archive).absolute()), "local_packages": [str(Path(p).absolute()) for p in local_packages],
        "entry_id": requirements.target.to_dict()["entry_id"], "package_lock": requirements.package_lock.to_dict(),
        "target": requirements.target.to_dict(), "binding": binding.to_dict(), "resolution": resolution.to_dict(),
        "receipt": None if receipt is None else receipt.to_dict(), "owner_request": owner_request,
        "run_dir": None if run_dir is None else str(Path(run_dir).absolute())}
    if include_terminal_result:
        request["include_terminal_result"] = True
    return request


def invoke_selected_host(request, binding, *, timeout=None, cancelled=None):
    import time
    process = None
    try:
        process = subprocess.Popen([selected_python(binding), "-I", "-m", "cpn.rpnh_cli", "_environment-host"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=cancelled is not None and os.name != "nt")
        pending = canonical_bytes(request)
        start = time.monotonic()
        while True:
            if cancelled is not None and cancelled():
                raise EnvironmentContractError("ENVIRONMENT_PREPARATION_CANCELLED", "selected HOST preparation was cancelled")
            if timeout is not None and time.monotonic() - start >= timeout:
                raise EnvironmentContractError("ENVIRONMENT_HOST_TIMEOUT", "selected HOST exceeded explicit phase timeout")
            try:
                stdout, _ = process.communicate(input=pending, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pending = None
            except KeyboardInterrupt:
                if request["mode"] == "run":
                    # Ask the existing owner to stop at its own boundary; do
                    # not kill it and invent a new lifecycle/checkpoint path.
                    process.send_signal(signal.SIGINT)
                    pending = None
                    continue
                raise EnvironmentContractError("ENVIRONMENT_PREPARATION_CANCELLED", "selected HOST preparation was interrupted")
    except OSError as exc:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "selected HOST cannot be started") from exc
    finally:
        if process is not None and process.poll() is None and request["mode"] != "run":
            if cancelled is not None and os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
    if len(stdout) > MAX_REQUEST:
        raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "selected HOST response exceeds bound")
    try:
        response = strict_json(stdout, path="selected-host-response")
    except Exception as exc:
        raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "selected installed HOST returned invalid protocol") from exc
    if process.returncode or response.get("status") != "ok":
        raise EnvironmentContractError(response.get("reason_code", "ENVIRONMENT_HOST_FAILED"), "selected HOST did not complete the requested phase")
    return response["result"]


@dataclass
class ExistingRunHandle:
    """Connection to the existing selected HOST owner; not a second run ledger.

    The handle appears only after the owner transport really exists. Dropping it
    does not cancel the owner or grant Registry write authority.
    """
    process: object = field(repr=False)
    references: dict
    _result: dict | None = field(default=None, init=False, repr=False)

    @property
    def control_connection(self):
        return dict(self.references["control_connection"])

    def to_dict(self):
        return json.loads(canonical_bytes(self.references))

    def poll(self):
        return self.process.poll()

    def request_stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)

    def wait(self):
        if self._result is not None:
            return json.loads(canonical_bytes(self._result))
        while True:
            try:
                raw = self.process.stdout.read(MAX_REQUEST + 1)
                self.process.wait()
                break
            except KeyboardInterrupt:
                self.request_stop()
        if len(raw) > MAX_REQUEST:
            raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "selected HOST response exceeds bound")
        try:
            response = strict_json(raw, path="selected-owner-result")
        except Exception as exc:
            raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "existing owner result is unavailable") from exc
        if self.process.returncode or response.get("status") != "ok":
            raise EnvironmentContractError(response.get("reason_code", "ENVIRONMENT_HOST_FAILED"), "existing owner did not complete its requested phase")
        self._result = response["result"]
        return json.loads(canonical_bytes(self._result))


def start_selected_host(request, binding):
    """Start selected installed HOST and await its actual owner/control address."""
    try:
        process = subprocess.Popen([selected_python(binding), "-I", "-m", "cpn.rpnh_cli", "_environment-host"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        process.stdin.write(canonical_bytes(request)); process.stdin.close(); process.stdin = None
        raw = process.stdout.readline(MAX_REQUEST + 1)
    except OSError as exc:
        raise EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "selected HOST cannot be started") from exc
    if len(raw) > MAX_REQUEST:
        raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "selected owner startup response exceeds bound")
    try:
        response = strict_json(raw, path="selected-owner-startup")
    except Exception as exc:
        raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "selected owner startup response is unavailable") from exc
    if response.get("status") != "owner_ready":
        process.wait()
        raise EnvironmentContractError(response.get("reason_code", "ENVIRONMENT_HOST_FAILED"), "selected HOST did not establish its existing owner transport")
    result = response.get("result")
    if (type(result) is not dict or set(result) != {"run_ref", "task_ref", "net_ref", "control_connection"}
            or result["control_connection"] != {"transport": "af_unix", "socket_path": str(Path(request["run_dir"]) / "owner.sock")}):
        raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "selected owner returned an inconsistent control address")
    return ExistingRunHandle(process, result)


def launch_package(package_lock, verified_packages, binding, receipt, *, owner_request, execution_context,
                   include_terminal_result=False):
    """A launch is separately authorized; receipt/JSON cannot supply authority."""
    if type(include_terminal_result) is not bool:
        raise TypeError("include_terminal_result must be a bool")
    from .environment_requirements import read_environment_requirements
    requirements = read_environment_requirements(package_lock, verified_packages, entry_id=binding.to_dict()["target"]["entry_id"])
    if not callable(execution_context.authorize_run) or not execution_context.authorize_run(binding.digest, owner_request, str(execution_context.run_dir)):
        raise EnvironmentContractError("RUN_AUTHORIZATION_REQUIRED", "business run requires distinct trusted execution authorization")
    request = host_request(requirements, execution_context.archive, execution_context.local_packages,
        binding, execution_context.resolution, mode="run", receipt=receipt, owner_request=owner_request,
        run_dir=execution_context.run_dir, include_terminal_result=include_terminal_result)
    if Path(execution_context.run_dir).exists():
        raise EnvironmentContractError("ENVIRONMENT_RUN_ALREADY_EXISTS", "run directory exists; use existing owner/resume protocol")
    return start_selected_host(request, binding)


def main(argv=None):
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "HOST request exceeds bound")
        request = strict_json(raw, path="selected-host-request")
        fields = {"schema_version", "mode", "archive", "local_packages", "entry_id", "package_lock", "target", "binding", "resolution", "receipt", "owner_request", "run_dir"}
        if (set(request) not in (fields, fields | {"include_terminal_result"})
                or type(request.get("include_terminal_result", False)) is not bool
                or (request.get("include_terminal_result", False) and request.get("mode") != "run")
                or request["schema_version"] != "rpnh/private_environment_host_request/v1" or request["mode"] not in {"assemble", "run"}):
            raise EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "unsupported HOST request")
        requirements = _revalidate_material(request)
        if requirements.target.to_dict() != request["target"]:
            raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "HOST material changed")
        binding = LocalEnvironmentBinding.from_dict(request["binding"])
        resolution = EnvironmentResolutionLock.from_dict(request["resolution"])
        if request["mode"] == "assemble":
            _profile, _policy, _registration, snapshot, check = assemble_here(requirements, binding, resolution)
            result = {"host_declarations_digest": snapshot.declarations_digest, "check": check.to_dict(),
                      "host_python": binding.to_dict()["python"]}
        else:
            def owner_ready(value):
                sys.stdout.write(canonical_bytes({"status": "owner_ready", "result": value}).decode("ascii") + "\n")
                sys.stdout.flush()
            result = run_here(requirements, binding, resolution, PreparationReceipt.from_dict(request["receipt"]),
                              request["owner_request"], request["run_dir"], owner_ready=owner_ready,
                              include_terminal_result=request.get("include_terminal_result", False))
        sys.stdout.write(canonical_bytes({"status": "ok", "result": result}).decode("ascii"))
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, EnvironmentContractError) else "ENVIRONMENT_HOST_FAILED"
        sys.stdout.write(canonical_bytes({"status": "error", "reason_code": code, "error_type": type(exc).__name__}).decode("ascii"))
        return 5


def readonly_probe_policy(local):
    """Load only the separately installed trusted read-only probe adapter.

    This is executable receiver probe code, never a package-selected module and
    never a HOST/plugin registration factory. Unsupported profiles remain so.
    """
    from dataclasses import replace
    profile_id = local["host_profile_id"]
    if profile_id == NATIVE_PROFILE:
        policy = native_profile(local).probe_policy_factory()
    else:
        matches = [ep for ep in metadata.entry_points(group="rpnh.environment_probes") if ep.name == profile_id]
        if len(matches) > 1:
            raise EnvironmentContractError("ENVIRONMENT_PROBE_UNSUPPORTED", "selected probe profile is ambiguous")
        policy = ProbePolicy() if not matches else matches[0].load()(local)
    if not isinstance(policy, ProbePolicy):
        raise EnvironmentContractError("ENVIRONMENT_PROBE_UNSUPPORTED", "installed readonly probe factory returned incompatible policy")
    return replace(policy, host_profiles=profile_fingerprints((profile_id,)))


def readonly_probe_request(request):
    """Fixed adapter protocol. No arbitrary locator, command or stdout relay."""
    local = request["local"]
    cls = EnvironmentSelection if local.get("schema_version") == "rpnh/environment_selection/v1" else LocalEnvironmentBinding
    checked_local = cls.from_dict(local).to_dict()
    policy = readonly_probe_policy(checked_local)
    if request["kind"] == "inventory":
        return {"host_profiles": dict(policy.host_profiles), "adapters": {name: sorted(getattr(policy, name))
            for name in ("capabilities", "tools", "os_packages", "plugins", "services")},
            "credentials": policy.credential_present is not None}
    if request["kind"] == "credential":
        return bool(policy.credential_present and policy.credential_present(request["reference"]))
    category = request["category"]
    if category not in {"capabilities", "tools", "os_packages", "plugins", "services"}:
        raise EnvironmentContractError("ENVIRONMENT_PROBE_UNSUPPORTED", "unsupported probe category")
    adapter = getattr(policy, category).get(request["contract"])
    if adapter is None:
        raise EnvironmentContractError("ENVIRONMENT_PROBE_UNSUPPORTED", "selected trusted adapter is unavailable")
    return adapter(requirement=request["requirement"], binding=request["binding"], interpreter=sys.executable)


if __name__ == "__main__":
    # Avoid two HostProfile class identities under Python -m. Factories import
    # the canonical installed module, as do all entry points and API callers.
    from cpn.rpnh.collaboration.environment_host import main as canonical_main
    raise SystemExit(canonical_main())
