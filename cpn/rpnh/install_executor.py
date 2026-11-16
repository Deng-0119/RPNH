"""Managed-v2 installation of exact acquired or fixed local materials.

Software preparation and activation never open a business Registry or resume a
task. Published installation supplies its transient acquisition context;
a JSON declaration cannot grant source or cross-release data authority here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from importlib.util import source_from_cache
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
from typing import Callable, Mapping

from cpn.process_supervision import (
    ProcessStartError, SupervisedProcess, start_process, stop_process,
)

from .install_contract import (
    FRONTEND_SCHEMA, INSTALLED_SCHEMA, V2_PROTOCOL, InstallPlan,
    build_install_plan, load_profile_catalog, read_plan, read_software_record,
    runtime_signature, validate_installed, validate_plan,
)
from .install_v2 import (
    ActivationResult, _management_source, _read_json, _write_json,
    activate_locked, check_root, create_root, create_stage_locked,
    management_lease, read_target, recover_locked,
)


PREPARED_SCHEMA = "rpnh/prepared_installation/v1"
RESULT_SCHEMA = "rpnh/install_result/v2"


@dataclass(frozen=True)
class InstallResult:
    activation: ActivationResult
    installation_id: str
    launcher: str
    source_qualification: str = "local_candidate"

    def to_document(self) -> dict:
        return {
            "schema_version": RESULT_SCHEMA,
            "activation": self.activation.to_document(),
            "installation_id": self.installation_id,
            "launcher": self.launcher,
            "source_qualification": self.source_qualification,
            "native_qualification": "not_awarded",
            "business_data_qualification": "checked_at_business_entry",
            "tasks_resumed": False,
        }


def plan_from_document(document: Mapping) -> InstallPlan:
    """Reconstruct the existing plan type without a second plan authority."""
    value = validate_plan(document)
    return InstallPlan(
        value["installation_id"], value["operation"], value["expected_from"],
        value["source"], value["platform"], value["runtime_bindings"],
        value["components"], tuple(value["conflicts"]),
    )


def plan_local_install(wheel: Path, materials_path: Path, *, root: Path,
                       requested=None) -> InstallPlan:
    """Resolve a candidate using the exact runtimes declared by its materials."""
    from .install_materials import ensure_explicit_reconfigure_extras, load_materials
    from .update_install import lifecycle_lease
    wheel = Path(wheel).expanduser().resolve(strict=True)
    catalog = load_profile_catalog(wheel=wheel)
    document = read_software_record(Path(materials_path), limit=2 * 1024 * 1024)
    if not isinstance(document, dict):
        raise ValueError("installation materials must be an object")
    current = None
    root = Path(root).expanduser().absolute()
    if os.path.lexists(root):
        with lifecycle_lease(root, exclusive=False):
            selected = read_target(root, allow_missing=True)
            current = None if selected is None else selected[2]
            if selected is not None:
                ensure_explicit_reconfigure_extras(
                    document, read_software_record(selected[1] / "materials.json", limit=1024 * 1024))
    source = {"kind": "local_candidate", "rpnh_version": catalog.rpnh_version,
              "release": None, "channel": None,
              "artifact": {"filename": wheel.name, "location": str(wheel)}}
    plan = build_install_plan(
        source, document.get("platform"), document.get("runtime_bindings"),
        requested=requested, current=current, catalog=catalog,
    )
    load_materials(materials_path, plan, catalog=catalog)
    return plan


def prepared_record(directory: Path, plan: Mapping | None = None) -> dict:
    """Turn prepared observations into a record retaining the plan's source."""
    directory = Path(directory)
    plan = read_plan(directory) if plan is None else validate_plan(plan)
    prepared = _read_json(directory / "prepared.json")
    if (set(prepared) != {"schema_version", "runtime_bindings", "components"}
            or prepared["schema_version"] != PREPARED_SCHEMA):
        raise ValueError("invalid prepared installation observations")
    components = prepared["components"]
    if not isinstance(components, dict) or set(components) != set(plan["components"]):
        raise ValueError("preparation did not produce the complete selected frontend set")
    for entry, selection in plan["components"].items():
        item = components[entry]
        if (not isinstance(item, dict) or item.get("profile") != selection["profile"]
                or item.get("binding") != selection["binding"]):
            raise ValueError(f"preparation changed the selected profile/binding for {entry}")
    runtime = prepared["runtime_bindings"]
    if runtime_signature(runtime) != runtime_signature(plan["runtime_bindings"]):
        raise ValueError("prepared runtime facts differ from the fixed plan")
    if runtime["python"]["path"] != str(directory / "venv/bin/python"):
        raise ValueError("prepared Python is not this installation's interpreter")
    for name, observed in runtime.items():
        if name != "python" and observed != plan["runtime_bindings"][name]:
            raise ValueError(f"preparation changed the fixed {name} binding")
    record = {"schema_version": INSTALLED_SCHEMA, "protocol": V2_PROTOCOL,
              "installation_id": plan["installation_id"], "rpnh_version": plan["rpnh_version"],
              "source": plan["source"], "platform": plan["platform"],
              "runtime_bindings": runtime, "components": components, "complete": True}
    return validate_installed(record, expected_id=directory.name)


def prepare_candidate_materials(context) -> dict:
    """The fixed child handler prepares only its own frontend assets."""
    from .install_materials import load_materials, prepare_frontends
    import sys
    plan = validate_plan(context.record)
    materials = load_materials(context.directory / "materials.json", plan)
    components = prepare_frontends(context.directory, plan, materials)
    runtime = deepcopy(plan["runtime_bindings"])
    runtime["python"] = {
        "path": str(Path(sys.executable).absolute()), "version": platform.python_version(),
        "implementation": platform.python_implementation().lower(), "architecture": platform.machine(),
    }
    _write_json(context.directory / "prepared.json", {
        "schema_version": PREPARED_SCHEMA,
        "runtime_bindings": runtime, "components": components,
    })
    prepared_record(context.directory, plan)
    return {"operation": "prepare", "status": "prepared", "installation_id": context.installation_id}


def probe_candidate_materials(context) -> dict:
    """Probe the retained software without depending on its original inputs."""
    from .install_materials import load_materials, probe_installation
    plan = read_plan(context.directory)
    materials = load_materials(context.directory / "materials.json", plan, installed=True)
    record = (validate_installed(context.record) if context.record.get("complete") is True
              else prepared_record(context.directory, plan))
    observations = probe_installation(context.directory, record, materials,
                                     retained=context.record.get("complete") is True)
    if not isinstance(observations, dict):
        raise ValueError("candidate probe must return its actual software observations")
    _write_json(context.directory / "probe.json", observations)
    return {"operation": "probe", "status": "verified", "installation_id": context.installation_id}


def _candidate_environment(root: Path, directory: Path, descriptor: int) -> dict[str, str]:
    """Do not inherit Python, package-manager or user frontend configuration."""
    state = directory / ".prepare-state"
    state.mkdir(mode=0o700, exist_ok=True)
    for child in ("home", "cache", "config", "data", "tmp"):
        (state / child).mkdir(mode=0o700, exist_ok=True)
    env = {key: os.environ[key] for key in ("LANG", "LC_ALL", "LC_CTYPE", "TZ") if key in os.environ}
    env.update({
        "PATH": str(directory / "venv/bin") + ":/usr/bin:/bin",
        "HOME": str(state / "home"), "XDG_CACHE_HOME": str(state / "cache"),
        "XDG_CONFIG_HOME": str(state / "config"), "XDG_DATA_HOME": str(state / "data"),
        "TMPDIR": str(state / "tmp"), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "RPNH_MANAGED_ROOT": str(root), "RPNH_FRONTEND_CONFIG": str(directory / "frontend.json"),
        "RPNH_CANDIDATE_ID": directory.name, "RPNH_CANDIDATE_FD": str(descriptor),
        "RPNH_UPDATES": "off",
    })
    if sys.platform == "linux":
        for child in ("state", "runtime"):
            (state / child).mkdir(mode=0o700, exist_ok=True)
        env.update({
            "TMP": env["TMPDIR"], "TEMP": env["TMPDIR"],
            "XDG_STATE_HOME": str(state / "state"), "XDG_RUNTIME_DIR": str(state / "runtime"),
            "PIP_CACHE_DIR": str(state / "cache/pip"), "UV_CACHE_DIR": str(state / "cache/uv"),
            "npm_config_cache": str(state / "cache/npm"), "NODE_COMPILE_CACHE": str(state / "cache/node"),
        })
    return env


def _require_group_absent(process: subprocess.Popen, *, label: str) -> None:
    """Require clean Linux completion, or observe legacy group absence.

    A supervised target that left live descendants must still be rejected.

    An observed group or an observation error rejects this operation. Even a
    reused PGID must never turn that rejection into a destructive cleanup.
    """
    if process.returncode is None:
        raise RuntimeError(f"{label} direct child exit is unconfirmed")
    if isinstance(process, SupervisedProcess):
        if not process.cleanup_complete:
            raise RuntimeError(f"{label} owned process cleanup is unconfirmed")
        if process.had_live_descendants:
            raise RuntimeError(f"{label} left unfinished descendants; operation rejected")
        return
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    except (AttributeError, OSError, ValueError) as error:
        raise RuntimeError(f"{label} group absence is unconfirmed") from error
    raise RuntimeError(f"{label} group is still observable; operation rejected")


def _stop_owned_process(process: subprocess.Popen, *, own_group: bool) -> None:
    """Stop a Linux owned tree, or use the original bounded child cleanup.

    In the legacy path, no call reaps before its final possible signal. Shared-group
    commands may signal only their direct child, never the candidate/HOST group.
    This does not establish atomic ownership or cleanup of escaped descendants.
    """
    if isinstance(process, SupervisedProcess):
        stop_process(process)
        return
    if process.returncode is not None:
        raise RuntimeError("interrupted preparation child was already reaped")
    try:
        observed = os.waitid(os.P_PID, process.pid,
                             os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if observed is not None and observed.si_pid != process.pid:
            raise ChildProcessError("waitid returned another preparation child")
    except (AttributeError, OSError, ValueError) as error:
        raise RuntimeError("interrupted preparation child ownership is unknown") from error
    failure = None
    try:
        if own_group:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            os.kill(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except (AttributeError, OSError, ValueError) as error:
        failure = error
    try:
        process.wait(timeout=1)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        failure = failure or error
    if failure is not None:
        raise RuntimeError("interrupted preparation cleanup is unconfirmed") from failure


def run_candidate_child(root: Path, directory: Path, lease, operation: str, *,
                        timeout: float = 1800.0) -> dict:
    """Execute the installed fixed module, inheriting only the target EX lease."""
    if operation not in {"prepare", "probe"}:
        raise ValueError("unknown candidate operation")
    output_path = directory / f"candidate-{operation}.out"
    error_path = directory / f"candidate-{operation}.err"
    # Logs are installation-local and bounded when read. They never select code.
    with output_path.open("wb") as output, error_path.open("wb") as errors:
        os.fchmod(output.fileno(), 0o600)
        os.fchmod(errors.fileno(), 0o600)
        deadline = time.monotonic() + timeout
        argv = [str(directory / "venv/bin/python"), "-I", "-m", "cpn.rpnh.install_candidate", operation]
        options = dict(cwd=directory, env=_candidate_environment(root, directory, lease.fileno()),
                       pass_fds=(lease.fileno(),), stdin=subprocess.DEVNULL,
                       stdout=output, stderr=errors)
        if sys.platform == "linux":
            try:
                process = start_process(argv, **options, deadline=deadline)
            except ProcessStartError as exc:
                if exc.code == "timeout":
                    raise subprocess.TimeoutExpired(argv, timeout) from exc
                raise
        else:
            process = subprocess.Popen(argv, **options, start_new_session=True)
        try:
            result = process.wait(timeout=max(0, deadline - time.monotonic())
                                  if sys.platform == "linux" else timeout)
        except BaseException:
            _stop_owned_process(process, own_group=True)
            raise
        # Linux requires confirmed cleanup and no leftovers at target exit.
        # Other platforms retain the non-destructive group absence check.
        _require_group_absent(process, label="candidate")
    if result:
        raise RuntimeError(f"candidate {operation} failed (exit {result}); inspect {error_path.name} in the retained stage")
    if output_path.stat().st_size > 256 * 1024:
        raise ValueError("candidate returned oversized observations")
    try:
        document = json.loads(output_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate did not return one complete JSON result") from exc
    expected = "prepared" if operation == "prepare" else "verified"
    if (not isinstance(document, dict) or document.get("operation") != operation
            or document.get("status") != expected or document.get("installation_id") != directory.name):
        raise ValueError("candidate result does not identify this installation and operation")
    return document


def _site_packages(directory: Path) -> Path:
    prefix = directory / "venv"
    choices = list((prefix / "lib").glob("python*/site-packages"))
    if (prefix != prefix.resolve() or len(choices) != 1
            or choices[0] != choices[0].resolve() or not choices[0].is_dir()):
        raise ValueError("installation does not contain one real Python package directory")
    return choices[0]


def _payload_files(directory: Path) -> dict[str, Path]:
    site = _site_packages(directory)
    result = {}
    for name in ("cpn", "integrations"):
        base = site / name
        if base.is_symlink():
            raise ValueError("RPNH installed payload contains an aliased package")
        if not base.is_dir():
            if name == "cpn":
                raise ValueError("installation has no real cpn package")
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(site)
            if path.is_symlink():
                raise ValueError("RPNH installed payload contains an aliased member")
            if path.is_file() and path.parent.name == "__pycache__" and path.suffix == ".pyc":
                try:
                    source = Path(source_from_cache(str(path)))
                except ValueError:
                    pass
                else:
                    # Ignore only conventional caches for actual source in
                    # this package. Sourceless/importable extra bytecode and
                    # other cache members remain part of payload equality.
                    if (source.is_relative_to(base) and source.suffix == ".py"
                            and source.is_file() and not source.is_symlink()):
                        continue
            if path.is_file():
                result[str(relative)] = path
    metadata = list(site.glob("rpnh_harness-*.dist-info/METADATA"))
    if len(metadata) != 1:
        raise ValueError("installation has ambiguous RPNH distribution metadata")
    for name in ("METADATA", "entry_points.txt"):
        path = metadata[0].parent / name
        if path.is_file() and not path.is_symlink():
            result["distribution/" + name] = path
    return result


def same_release_payload(left: Path, right: Path) -> bool:
    """A same-R reconfiguration may change frontends, not the RPNH payload."""
    old, new = _payload_files(left), _payload_files(right)
    return old.keys() == new.keys() and all(old[key].read_bytes() == new[key].read_bytes() for key in old)


def _local_source_scope(plan: Mapping, current) -> None:
    if plan["source"]["kind"] != "local_candidate":
        raise ValueError("published-release installation requires its current official asset acquisition")
    if current is not None:
        record = current[2]
        if record["source"]["kind"] != "local_candidate":
            raise ValueError("local candidates cannot replace a published installation; use a new dedicated root")
        if record["rpnh_version"] != plan["rpnh_version"]:
            raise ValueError("cross-release activation needs verified format and release-pair scope; use separate candidate roots")


def _display_selection(value: str, components: Mapping) -> str:
    """Normalize a display choice only within the already selected complete set."""
    if not isinstance(value, str) or value not in {"auto", "basic", "codex", "opencode", "dsh", "dsh-web"}:
        raise ValueError("select a supported display choice from the complete installation")
    entry = "dsh-web" if value in {"dsh", "dsh-web"} else value
    if entry != "auto" and entry not in components:
        raise ValueError("select an installed frontend explicitly when removing the current display choice")
    return "dsh" if entry == "dsh-web" else entry


def execute_install(root: Path, plan: InstallPlan, materials_path: Path, *,
                    selected_frontend: str | None = None,
                    expected_materials: Mapping | None = None,
                    published_context=None,
                    event: Callable[[str], None] | None = None) -> InstallResult:
    """Run the original real preparation, probes and activation transaction.

    Bootstrap and immutable same-release reconfiguration share this executor.
    Published execution consumes only the resolver's acquired complete bootstrap
    or same-release reconfiguration for its exact original predecessor.
    Cross-release scope is not manufactured from metadata-only tests.
    Failed stages remain distinct; a retry always creates a fresh I.
    """
    from cpn.update_context import reject_candidate_runtime
    from .install_materials import (
        ensure_explicit_reconfigure_extras, load_materials, prepare_python_environment,
    )
    reject_candidate_runtime()
    _management_source()  # Invalid source context must fail before creating a root.
    document = validate_plan(plan.to_document())
    if document["conflicts"]:
        raise ValueError("installation plan contains unresolved conflicts")
    if selected_frontend is not None:
        selected_frontend = _display_selection(selected_frontend, document["components"])
    published = document["source"]["kind"] == "published_release"
    if not published:
        if published_context is not None:
            raise ValueError("a local candidate cannot use published acquisition context")
        _local_source_scope(document, None)
    elif published_context is None:
        raise ValueError("published-release installation requires its current official asset acquisition")
    materials = load_materials(materials_path, plan)
    if expected_materials is not None and materials.document != expected_materials:
        raise ValueError("selected materials changed after confirmation; inspect a new plan")
    root = Path(root).expanduser().absolute()
    if published:
        from .published_install import _verify_acquisition
        if plan.operation == "bootstrap":
            if os.path.lexists(root):
                raise ValueError("published bootstrap needs a new dedicated real root")
        elif plan.operation == "reconfigure":
            root = check_root(root)
        else:
            raise ValueError("published cross-release activation requires its reviewed release-pair scope")
        selected_frontend = _verify_acquisition(published_context, root, document, materials,
                                               selected_frontend=selected_frontend)
        # A root appearing while official metadata is refreshed belongs to
        # another operation. Never fall through to the local adoption branch.
        if plan.operation == "bootstrap":
            root = create_root(root)
    elif not os.path.lexists(root):
        if plan.operation != "bootstrap":
            raise ValueError("only bootstrap may create a new managed root")
        root = create_root(root)
    else:
        root = check_root(root)
    with management_lease(root) as lease:
        recover_locked(root)
        current = read_target(root, allow_missing=True)
        if published:
            # Re-read the actual material document under EX. The context checks
            # exact files and fresh official metadata, not a cached success flag.
            materials = load_materials(materials_path, plan)
            _verify_acquisition(published_context, root, document, materials,
                                current=current, under_lock=True, selected_frontend=selected_frontend)
        else:
            _local_source_scope(document, current)
        if (None if current is None else current[0]) != plan.expected_from:
            raise ValueError("current changed after the installation plan was selected")
        expected_operation = "bootstrap" if current is None else "reconfigure"
        if plan.operation != expected_operation:
            raise ValueError("plan operation does not match the actual installation relation")
        if current is not None:
            ensure_explicit_reconfigure_extras(
                materials.document, read_software_record(current[1] / "materials.json", limit=1024 * 1024))
        if selected_frontend is None:
            from .install_contract import read_frontend_view
            selected_frontend = _display_selection(
                "basic" if current is None else read_frontend_view(current[1])["frontend"],
                document["components"])
        directory = create_stage_locked(root, plan)
        _write_json(directory / "materials.json", materials.document)
        _write_json(directory / "frontend.json", {
            "schema_version": FRONTEND_SCHEMA, "installation_id": plan.installation_id,
            "frontend": selected_frontend,
        })
        prepare_python_environment(directory, plan, materials)
        run_candidate_child(root, directory, lease, "prepare")
        run_candidate_child(root, directory, lease, "probe")
        record = prepared_record(directory, document)
        if current is not None and not same_release_payload(current[1], directory):
            raise ValueError("same-release reconfiguration changed the RPNH payload; assign a new release and validate its compatibility")
        # The parent alone publishes complete software facts, after every probe.
        _write_json(directory / "installed.json", record)
        result = activate_locked(
            root, plan.target, operation=plan.operation, expected_from=plan.expected_from,
            # The exact candidate was just probed under this same EX. Do not
            # repeat all package-manager checks at the unchanged commit boundary.
            probe=lambda target, observed: _prepared_activation_identity(directory, record, target, observed),
            event=event,
        )
        return InstallResult(result, plan.installation_id, str(root / "bin/rpnh"),
                             "official_release_assets_observed" if published else "local_candidate")


def _prepared_activation_identity(directory, record, target, observed) -> None:
    if target != directory or observed != record:
        raise ValueError("activation no longer identifies the successfully probed candidate")


def _published_rollback_pair(current, previous) -> dict:
    """Observe the exact retained same-R relation; callers hold SH or EX."""
    from .published_install import _published_installation_observation, _root_asset, _same_release_python
    from .install_contract import read_frontend_view
    if previous is None:
        raise ValueError("this root has no retained previous installation")
    outgoing = _published_installation_observation(current)
    destination = _published_installation_observation(previous)
    destination["frontend_view"] = read_frontend_view(previous[1],
        expected_id=destination["record"]["installation_id"], selected_entries=destination["record"]["components"])
    if (outgoing["record"]["source"] != destination["record"]["source"]
            or outgoing["record"]["platform"] != destination["record"]["platform"]
            or not _same_release_python(outgoing["plan"]["runtime_bindings"], destination["plan"]["runtime_bindings"])
            or _root_asset(outgoing["materials"]["published_selection"])
            != _root_asset(destination["materials"]["published_selection"])):
        raise ValueError("published retained rollback requires the same release, base Python and original RPNH asset")
    if current[0] != previous[0]:
        origin = outgoing["plan"]
        if origin["operation"] != "reconfigure" or origin["expected_from"] != previous[0]:
            raise ValueError("retained installations lack the verified same-release reconfiguration relation")
    return {"current": outgoing, "previous": destination}


def _rollback_retained(root: Path, *, published: bool, expected_pair=None) -> InstallResult:
    """Use the original retained transaction after checking its source scope."""
    root = Path(root).expanduser().absolute()
    with management_lease(root) as lease:
        recover_locked(root)
        current = read_target(root)
        previous = read_target(root, "previous", allow_missing=True)
        if previous is None:
            raise ValueError("this root has no retained previous installation")
        qualification = "local_candidate"
        if published:
            pair = _published_rollback_pair(current, previous)
            if expected_pair is not None and pair != expected_pair:
                raise ValueError("retained published pair changed after confirmation; inspect again")
            qualification = "retained_official_release_installation"
        else:
            for row in (current, previous):
                if row[2]["source"]["kind"] != "local_candidate":
                    raise ValueError("published rollback requires the authenticated v2 compatibility scope")
        if current[0] == previous[0]:
            return InstallResult(ActivationResult(current[0], current[0], True, False, "rollback"),
                                 current[2]["installation_id"], str(root / "bin/rpnh"), qualification)
        if current[2]["rpnh_version"] != previous[2]["rpnh_version"]:
            raise ValueError("cross-release rollback requires verified release-pair and persistent-format scope")
        # Reconfiguration published current only after actual payload equality.
        # Its retained plan identifies that exact predecessor. Do not require
        # the outgoing environment to remain healthy to recover the destination.
        origin = read_plan(current[1])
        if origin["operation"] != "reconfigure" or origin["expected_from"] != previous[0]:
            raise ValueError("retained installations lack the verified same-release reconfiguration relation")
        result = activate_locked(
            root, previous[0], operation="rollback", expected_from=current[0],
            probe=lambda directory, _record: run_candidate_child(root, directory, lease, "probe"),
        )
        return InstallResult(result, previous[2]["installation_id"], str(root / "bin/rpnh"), qualification)


def rollback_local(root: Path) -> InstallResult:
    """Reprobe the retained destination; damaged outgoing clients do not block it."""
    return _rollback_retained(root, published=False)


def rollback_published(root: Path, *, expected_pair=None) -> InstallResult:
    """Restore the exact retained predecessor of a published reconfiguration.

    This consumes its completed same-R relation and probes the destination. It
    neither downloads again nor grants a cross-release rollback or data scope.
    """
    return _rollback_retained(root, published=True, expected_pair=expected_pair)
