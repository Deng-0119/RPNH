"""Explicit exact-plan application; partial preparation never means business success."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import uuid
from typing import Callable

from .environment_contracts import EnvironmentContractError
from .environment_local_contracts import (
    EnvironmentSelection, LocalEnvironmentBinding, PreparationReceipt, EnvironmentCheckReport,
)
from .environment_check import check_environment, probe_python, selected_python, now, ProbePolicy
from .environment_host import host_request, invoke_selected_host
from .environment_plan import inspect_wheel
from .share_packages import canonical_bytes, sha256


@dataclass(frozen=True)
class PreparationExecutionContext:
    requirements: object
    selection_or_binding: object
    resolution: object
    before_check: EnvironmentCheckReport
    archive: str | Path
    local_packages: tuple[str | Path, ...]
    authorize: Callable
    probe_policy: ProbePolicy = ProbePolicy()
    cancelled: Callable = lambda: False
    report_directory: str | Path | None = None
    action_timeout_seconds: float = 600

    def __post_init__(self):
        if self.report_directory is None:
            from ..user_config import config_path
            object.__setattr__(self, "report_directory", config_path().parent / "environment-preparation" / binding_id_for(self.selection_or_binding))


def binding_id_for(selection_or_binding):
    return selection_or_binding.to_dict().get("binding_id", "environment-" + selection_or_binding.digest[:32])


@dataclass(frozen=True)
class PreparedEnvironmentResult:
    binding: LocalEnvironmentBinding | None
    receipt: PreparationReceipt
    after_check: EnvironmentCheckReport | None
    def to_dict(self):
        return {"binding": None if self.binding is None else self.binding.to_dict(),
                "receipt": self.receipt.to_dict(), "after_check": None if self.after_check is None else self.after_check.to_dict()}


@dataclass(frozen=True)
class LaunchExecutionContext:
    archive: str | Path
    local_packages: tuple[str | Path, ...]
    resolution: object
    run_dir: str | Path
    authorize_run: Callable


class _Cancelled(Exception):
    pass


def _run(command, context, *, environment=None):
    if context.cancelled():
        raise _Cancelled()
    import time
    start = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=(os.name != "nt"), env=environment)
    try:
        while True:
            if context.cancelled():
                raise _Cancelled()
            try:
                status = process.wait(timeout=0.1)
                if status:
                    raise EnvironmentContractError("ENVIRONMENT_PREPARATION_ACTION_FAILED", "trusted preparation adapter failed")
                return
            except subprocess.TimeoutExpired:
                if time.monotonic() - start > context.action_timeout_seconds:
                    raise EnvironmentContractError("ENVIRONMENT_PREPARATION_ACTION_TIMEOUT", "trusted preparation adapter timed out")
    finally:
        if process.poll() is None:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()


def _binding(context):
    local = context.selection_or_binding.to_dict()
    inventory = probe_python(selected_python(context.selection_or_binding))
    return LocalEnvironmentBinding.from_dict({"schema_version": "rpnh/local_environment_binding/v1",
        "binding_id": binding_id_for(context.selection_or_binding), "binding_revision": "revision-" + uuid.uuid4().hex,
        "target": local["target"], "resolution_digest": context.resolution.digest, "mode": local["mode"],
        "python": {key: inventory[key] for key in ("executable_realpath", "prefix_realpath")},
        "tools": [{"scoped_requirement_id": row["scoped_requirement_id"], "tool_contract_id": row["tool_contract_id"],
                   "executable_realpath": os.path.realpath(row.get("executable", row.get("executable_realpath")))} for row in local["tools"]],
        "plugins": local["plugins"], "services": local["services"], "host_profile_id": local["host_profile_id"]})


def save_immutable(directory, name, value):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    file = path / name
    payload = value.to_bytes() if hasattr(value, "to_bytes") else canonical_bytes(value)
    if file.exists():
        if file.read_bytes() == payload:
            return file
        raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE", "immutable preparation report was changed")
    with file.open("xb") as stream:
        os.chmod(file, 0o600)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return file


def _validate_context(plan, context):
    if type(context) is not PreparationExecutionContext:
        raise TypeError("prepare requires a trusted in-process execution context")
    pd, local = plan.to_dict(), context.selection_or_binding.to_dict()
    initial = isinstance(context.selection_or_binding, EnvironmentSelection)
    if (pd["target"] != context.requirements.target.to_dict() or local["target"] != pd["target"]
            or context.resolution.to_dict()["target"] != pd["target"]
            or context.resolution.digest != pd["resolution_digest"] or context.before_check.digest != pd["based_on_check_digest"]
            or (initial and context.selection_or_binding.digest != pd["selection_digest"])
            or (not initial and local["binding_revision"] != pd["binding_revision"])):
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "execution context does not name the exact approved plan")
    # Re-read exact archive bytes at application time, never trust a report copy.
    from .environment_requirements import read_package_environment
    verified = read_package_environment(context.archive, context.local_packages, pd["target"]["entry_id"],
                                        package_lock=context.requirements.package_lock)
    if verified.target != context.requirements.target:
        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "preparation materials changed")
    if pd["unresolved"] or context.resolution.to_dict()["unresolved"]:
        raise EnvironmentContractError("ENVIRONMENT_SELECTION_UNRESOLVED", "plan still has unresolved exact selections")
    from .environment_plan import validate_concrete_resolution
    # The base interpreter is inspected only as a planned venv creator here;
    # it is never substituted for the missing target in a check report.
    base = local.get("python_selection", {}).get("base_executable")
    planned_inventory = probe_python(base or selected_python(context.selection_or_binding))
    validate_concrete_resolution(context.requirements, context.selection_or_binding,
                                 context.resolution, inventory=planned_inventory)
    if not callable(context.authorize) or not context.authorize(plan.digest, tuple(pd["actions"]), pd["target"]):
        raise EnvironmentContractError("PREPARATION_AUTHORIZATION_REQUIRED", "trusted execution approval must cover exact plan/actions/paths")


def prepare_environment(plan, *, execution_context):
    context = execution_context
    _validate_context(plan, context)
    pd = plan.to_dict()
    action_results, binding, after, declarations, failure = [], None, None, None, None
    for index, action in enumerate(pd["actions"]):
        try:
            if context.cancelled():
                raise _Cancelled()
            kind, options, target = action["kind"], action["options"], action["target"]
            if kind == "create_venv" and action["trusted_adapter_contract"] == "rpnh/venv_adapter/v1":
                path = Path(target["path"])
                if path.exists() or path.is_symlink():
                    raise EnvironmentContractError("ENVIRONMENT_TARGET_ALREADY_EXISTS", "refusing to replace an existing environment")
                expected = context.selection_or_binding.to_dict()["python_selection"]
                if expected != {"base_executable": options["base_executable"], "prefix": str(path)}:
                    raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "venv action path differs from selection")
                # Reserve the absent path atomically so a concurrent creator is
                # never silently merged/overwritten. Keep it if interrupted.
                path.mkdir(mode=0o700)
                _run([options["base_executable"], "-I", "-m", "venv", "--copies", str(path)], context)
            elif kind == "install_distribution" and action["trusted_adapter_contract"] == "rpnh/hashed_wheel_install/v1":
                selected = next((r["identity"] for r in context.resolution.to_dict()["selections"]
                    if r["kind"] == "distribution" and r["identity"]["name"] == target["name"]), None)
                if selected is None or selected["version"] != options["version"] or selected["artifact_digest"] != options["artifact_digest"]:
                    raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "install action differs from exact lock")
                source = Path(options["source_path"])
                payload = source.read_bytes()
                if sha256(payload) != options["artifact_digest"]:
                    raise EnvironmentContractError("ENVIRONMENT_BINDING_STALE", "wheel bytes changed after approval")
                python = selected_python(context.selection_or_binding)
                if target["path"] != python:
                    raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "install interpreter differs from selection")
                # Immutable verified copy removes the source-path race. No index,
                # dependency resolver, source build or unlisted install occurs.
                with tempfile.TemporaryDirectory(prefix="rpnh-approved-wheel-") as staging:
                    copied = Path(staging) / source.name
                    copied.write_bytes(payload)
                    target_inventory = probe_python(python)
                    identity = inspect_wheel(copied, target_inventory)
                    if identity != selected:
                        raise EnvironmentContractError("ENVIRONMENT_TARGET_MISMATCH", "wheel metadata differs from exact lock")
                    install_environment = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
                    install_environment["PIP_CONFIG_FILE"] = os.devnull
                    _run([python, "-I", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", "--force-reinstall",
                        "--no-user", "--prefix", target_inventory["prefix_realpath"], "--disable-pip-version-check", str(copied)],
                        context, environment=install_environment)
            elif kind == "assemble_trusted_host" and action["trusted_adapter_contract"] == "rpnh/trusted_host_profile/v1":
                binding = _binding(context)
                request = host_request(context.requirements, context.archive, context.local_packages,
                                       binding, context.resolution, mode="assemble")
                response = invoke_selected_host(request, binding, timeout=context.action_timeout_seconds, cancelled=context.cancelled)
                declarations = response["host_declarations_digest"]
                after = EnvironmentCheckReport.from_dict(response["check"])
                if after.to_dict()["aggregate"] != "passed_for_checked_scope":
                    raise EnvironmentContractError("ENVIRONMENT_PREPARATION_INCOMPLETE", "selected HOST checks did not pass")
            elif kind == "verify_local_capability" and action["trusted_adapter_contract"] == "rpnh/receiver_recheck/v1":
                after = check_environment(context.requirements, context.selection_or_binding, probe_policy=context.probe_policy)
            else:
                raise EnvironmentContractError("ENVIRONMENT_PREPARATION_INCOMPLETE", "action needs a supported trusted adapter or actual manual completion")
            action_results.append({"action_id": action["action_id"], "status": "completed", "reason_code": "ACTION_COMPLETED"})
        except (Exception, KeyboardInterrupt) as exc:
            failure = "ENVIRONMENT_PREPARATION_CANCELLED" if isinstance(exc, (_Cancelled, KeyboardInterrupt)) else getattr(exc, "code", "ENVIRONMENT_PREPARATION_ACTION_FAILED")
            action_results.append({"action_id": action["action_id"], "status": "cancelled" if failure == "ENVIRONMENT_PREPARATION_CANCELLED" else "failed", "reason_code": failure})
            action_results.extend({"action_id": later["action_id"], "status": "not_started", "reason_code": "PRIOR_ACTION_INCOMPLETE"} for later in pd["actions"][index + 1:])
            break
    if after is None:
        # Same checker after interruption. Crucially a cancelled pre-create
        # new_venv checks the missing target; it never probes its base as target.
        try:
            after = check_environment(context.requirements, binding or context.selection_or_binding,
                probe_policy=context.probe_policy, resolution=context.resolution if binding else None)
        except Exception:
            after = None
    if failure is None and (binding is None or after is None or after.to_dict()["aggregate"] != "passed_for_checked_scope" or declarations is None):
        failure = "ENVIRONMENT_PREPARATION_INCOMPLETE"
    receipt = PreparationReceipt.from_dict({"schema_version": "rpnh/environment_preparation_receipt/v1", "target": pd["target"],
        "result_binding_revision": None if binding is None else binding.to_dict()["binding_revision"],
        "result_binding_digest": None if binding is None else binding.digest, "plan_digest": plan.digest,
        "before_check_digest": context.before_check.digest, "action_results": action_results,
        "after_check_digest": None if after is None else after.digest, "host_declarations_digest": declarations,
        "failure": failure, "completed_at": now()})
    result = PreparedEnvironmentResult(binding, receipt, after)
    if context.report_directory is not None:
        folder = Path(context.report_directory)
        for name, value in (("plan", plan), ("resolution", context.resolution), ("before-check", context.before_check),
                            ("binding", binding), ("after-check", after), ("receipt", receipt)):
            if value is not None:
                save_immutable(folder, name + "-" + value.digest + ".json", value)
    return result
