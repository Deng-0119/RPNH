"""Read-only admission for selected registered tool runtimes and capabilities.

The original interpreter identity and any selected workspace capability are
observed without opening a Registry writer or running business tools. These
bounded probes do not establish complete package or native product support.
The current host Python never substitutes for a registered environment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import sys
import time

from cpn.process_supervision import ProcessCleanupError, start_process, stop_process

from .registry.publication import _version_from_payload
from .registry.strict_contracts import _registered, ref_payload


class ToolEnvironmentAdmissionError(ValueError):
    """One exact registered environment cannot currently be resumed."""

    def __init__(self, environment_ref, field: str, path: str, reason: str):
        self.environment_ref = environment_ref
        self.field = field
        self.path = path
        super().__init__(
            f"registered tool environment {environment_ref.version_id}: "
            f"{field} {path!r} {reason}; original binding retained")


class ToolCapabilityAdmissionError(ValueError):
    """An exact selected operation cannot satisfy its registered capability."""

    def __init__(self, binding_ref, capability: str, reason: str):
        self.binding_ref = binding_ref
        self.capability = capability
        super().__init__(
            f"registered operation {binding_ref.version_id}: selected "
            f"{capability} capability {reason}; original bindings retained")


_PROBE_TIMEOUT_SECONDS = 5
_PROBE_OUTPUT_BYTES = 16 * 1024
_WORKSPACE_PROBE_TIMEOUT_SECONDS = 5
# Product-owned code; no registered payload is interpolated or imported. Match
# the numerical worker's ordinary isolated Python startup, including venv site
# initialization (Python 3.12 determines the venv prefix there).
_IDENTITY_PROGRAM = (
    "import json, os, sys; "
    "print(json.dumps({'executable_realpath': os.path.realpath(sys.executable), "
    "'prefix_realpath': os.path.realpath(sys.prefix)}, sort_keys=True))"
)


def _unique_fields(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate interpreter observation field")
        document[key] = value
    return document


def _observe_registered_python(reference, environment):
    from cpn.components.tool_executors import ExecutionEnvironmentIdentity

    identity = ExecutionEnvironmentIdentity(
        reference, environment["name"], environment["python_executable"],
        environment["python_prefix"])
    process = None
    primary_error = None
    try:
        linux = sys.platform == "linux"
        deadline = time.monotonic() + _PROBE_TIMEOUT_SECONDS
        launch = start_process if linux else subprocess.Popen
        process = launch(
            [identity.python_launcher, "-I", "-B", "-c", _IDENTITY_PROGRAM],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={}, cwd="/",
            **({"deadline": deadline} if linux else {"start_new_session": True}))
        if not linux:
            deadline = time.monotonic() + _PROBE_TIMEOUT_SECONDS
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("interpreter observation timed out")
                for key, _events in selector.select(remaining):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > _PROBE_OUTPUT_BYTES:
                        raise ValueError("interpreter observation exceeded its output bound")
        if process.wait(timeout=max(0, deadline - time.monotonic())):
            raise ValueError("registered interpreter did not complete the identity observation")
        if linux:
            if not process.cleanup_complete:
                raise ProcessCleanupError("interpreter observation cleanup was not confirmed")
            if process.had_live_descendants:
                raise ValueError("interpreter observation left unfinished descendants")
        else:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise ValueError("interpreter observation left unfinished descendants")
        if not output.endswith(b"\n") or output.count(b"\n") != 1:
            raise ValueError("interpreter observation must be one JSON line")
        observed = json.loads(output.decode("utf-8"), object_pairs_hook=_unique_fields)
        if (type(observed) is not dict
                or set(observed) != {"executable_realpath", "prefix_realpath"}
                or any(type(value) is not str or not os.path.isabs(value)
                       for value in observed.values())):
            raise ValueError("invalid interpreter identity observation")
        return observed
    except BaseException as error:
        cleanup_error = None
        if process is not None and process.returncode is None:
            try:
                if sys.platform == "linux":
                    stop_process(process)
                else:
                    # Retain the existing non-Linux ownership check.
                    from .install_executor import _stop_owned_process
                    _stop_owned_process(process, own_group=True)
            except BaseException as failure:
                cleanup_error = failure
        if isinstance(error, (OSError, ValueError, subprocess.TimeoutExpired, ProcessCleanupError)):
            reason = f"cannot satisfy its fixed identity probe ({type(error).__name__}: {error})"
            if cleanup_error is not None:
                reason += ("; interrupted child cleanup is unconfirmed "
                           f"({type(cleanup_error).__name__}: {cleanup_error})")
            failure = ToolEnvironmentAdmissionError(
                reference, "python_executable", environment["python_executable"], reason)
            if cleanup_error is not None:
                failure.cleanup_error = cleanup_error
            primary_error = failure
            raise failure from error
        if cleanup_error is not None:
            error.add_note("Interrupted interpreter cleanup is unconfirmed: "
                           f"{type(cleanup_error).__name__}: {cleanup_error}")
            error.cleanup_error = cleanup_error
        primary_error = error
        raise
    finally:
        if process is not None and process.stdout is not None:
            # A completed wait has released our ownership of the numeric PID /
            # PGID. Parsing/group-observation errors must never signal it.
            try:
                process.stdout.close()
            except BaseException as close_error:
                if primary_error is None:
                    if isinstance(close_error, (OSError, ValueError)):
                        raise ToolEnvironmentAdmissionError(
                            reference, "python_executable", environment["python_executable"],
                            "cannot close its fixed identity probe output "
                            f"({type(close_error).__name__}: {close_error})") from close_error
                    raise
                primary_error.add_note("Interpreter probe output close is unconfirmed: "
                                       f"{type(close_error).__name__}: {close_error}")
                primary_error.stream_close_error = close_error


def _selected_workspace_runtimes(core, bindings):
    """Resolve exact catalog/profile bindings, never global historical rows."""
    from cpn.components.agent_loop.tool_catalog import parse_agent_tool_catalog
    from cpn.components.tool_executors import (
        ExecutionEnvironmentIdentity, NumericalToolProfile,
    )

    selected = {}
    for binding_ref, binding in bindings:
        _, specification = _registered(
            core, _version_from_payload(binding["operation_spec_ref"]), "operation_spec/v1")
        workspace_declared = "workspace" in specification["allowed_tool_ids"]
        catalogs = []
        for raw in binding["readable_resource_refs"]:
            reference = _version_from_payload(raw)
            if reference.entity_type != "resource_version/v1":
                continue
            _, metadata = _registered(core, reference, "resource_version/v1")
            if metadata.get("descriptors", {}).get("content_role") == "optional_agent_tool_catalog":
                catalogs.append(reference)
        default_agent = (
            specification["transport"] == "llm"
            and specification["implementation_identity"] == {
                "implementation_id": "components.execute_default_operation", "revision": "v1"})
        if not catalogs and not (default_agent and workspace_declared):
            # An unrelated HOST can legitimately use the same tool name. Only
            # the original optional executor or an explicit optional catalog
            # selects this executor's workspace capability.
            continue
        try:
            if len(catalogs) != 1:
                raise ValueError("requires one exact selected tool catalog")
            prepared = core.get_version(catalogs[0].version_id)
            catalog = parse_agent_tool_catalog(core.object_store.read_registered(prepared))
            if ("workspace" in catalog.tool_names) != workspace_declared:
                raise ValueError("selected tool catalog and operation specification disagree about workspace")
            if not workspace_declared:
                continue
            artifacts = tuple(_version_from_payload(raw) for raw in binding["module_artifact_refs"])
            environments = tuple(ref for ref in artifacts
                                 if ref.entity_type == "execution_environment_identity/v1")
            profiles = tuple(ref for ref in artifacts
                             if ref.entity_type == "numerical_tool_profile/v1")
            if len(environments) != 1 or len(profiles) != 1:
                raise ValueError("requires one exact registered environment and profile")
            environment_ref, profile_ref = environments[0], profiles[0]
            _, environment = _registered(core, environment_ref, environment_ref.entity_type)
            _, profile = _registered(core, profile_ref, profile_ref.entity_type)
            core.catalog.validate_instance(profile_ref.entity_type, category="object", instance=profile)
            if profile["profile_ref"] != ref_payload(profile_ref):
                raise ValueError("registered profile self reference differs")
            if profile["environment_ref"] != ref_payload(environment_ref):
                raise ValueError("registered profile differs from its selected environment")
            identity = ExecutionEnvironmentIdentity(
                environment_ref, environment["name"], environment["python_executable"],
                environment["python_prefix"])
            limits = NumericalToolProfile(
                profile_ref, environment_ref, profile["timeout_seconds"],
                profile["memory_bytes"], profile["process_limit"],
                profile["source_size_bytes"], profile["input_size_bytes"])
            selected.setdefault((environment_ref, profile_ref), (binding_ref, identity, limits))
        except (KeyError, TypeError, ValueError) as error:
            raise ToolCapabilityAdmissionError(
                binding_ref, "workspace", f"has invalid registered inputs ({error})") from error
    return tuple(selected.values())


def require_selected_tool_environment_paths(core, net) -> None:
    """Check selected interpreter/capability pairs once before a resume writer."""
    if not core.read_only:
        raise TypeError("tool environment admission requires the read-only resume reader")
    environments = set()
    bindings = []
    for raw in net["operation_binding_refs"]:
        binding_ref = _version_from_payload(raw)
        _, binding = _registered(core, binding_ref, "operation_binding/v1")
        bindings.append((binding_ref, binding))
        environments.update(
            _version_from_payload(value)
            for value in binding["module_artifact_refs"]
            if value["entity_type"] == "execution_environment_identity/v1")
    for reference in sorted(environments, key=lambda ref: str(ref.version_id)):
        _, environment = _registered(core, reference, "execution_environment_identity/v1")
        core.catalog.validate_instance("execution_environment_identity/v1",
                                       category="object", instance=environment)
        if environment["environment_ref"] != ref_payload(reference):
            raise ValueError(f"registered tool environment self reference differs: {reference.version_id}")
        for field, expected in (("python_executable", stat.S_ISREG),
                                ("python_prefix", stat.S_ISDIR)):
            path = environment[field]
            try:
                mode = Path(path).stat().st_mode
            except OSError as error:
                raise ToolEnvironmentAdmissionError(
                    reference, field, path, f"is unavailable ({error.strerror})") from error
            if not expected(mode):
                kind = "regular file" if field == "python_executable" else "directory"
                raise ToolEnvironmentAdmissionError(reference, field, path, f"is not a {kind}")
            if field == "python_executable" and not os.access(path, os.X_OK):
                raise ToolEnvironmentAdmissionError(reference, field, path, "is not executable")
        observed = _observe_registered_python(reference, environment)
        for field, observed_field in (("python_executable", "executable_realpath"),
                                      ("python_prefix", "prefix_realpath")):
            # Compare against the original registered strings, not against a
            # newly realpath-normalized DTO that could hide a replaced symlink.
            if observed[observed_field] != environment[field]:
                raise ToolEnvironmentAdmissionError(
                    reference, field, environment[field],
                    f"differs from the observed interpreter path {observed[observed_field]!r}")

    from cpn.components.tool_executors import (
        RuntimeEnvironmentFrameworkFault, preflight_workspace_execution,
    )

    for binding_ref, identity, profile in _selected_workspace_runtimes(core, bindings):
        # A fixed temporary workspace exercises the original executor, with
        # original profile limits and no invocation/firing or business bytes.
        # Bound admission even if the run's eventual tool timeout is unlimited.
        timeout = min(_WORKSPACE_PROBE_TIMEOUT_SECONDS,
                      profile.timeout_seconds or _WORKSPACE_PROBE_TIMEOUT_SECONDS)
        try:
            preflight_workspace_execution(
                environment=identity, profile=profile, timeout_seconds=timeout)
        except (OSError, ValueError, RuntimeEnvironmentFrameworkFault) as error:
            raise ToolCapabilityAdmissionError(
                binding_ref, "workspace", f"is unavailable ({type(error).__name__}: {error})") from error
