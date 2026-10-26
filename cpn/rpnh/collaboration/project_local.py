"""Controlled local project launcher and JSON command client.

This launcher owns all configured local identities. Socket names route fixed
sessions, not authenticated people; the same OS user can access every route.
Initialization never executes business work. An explicit owner run uses only
the approved original profile; this module provides no remote login.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import socket
import sys
import uuid

from ..run import resume_run, OwnerInput
from ..module import ModuleDeclaration
from ..registry._registry import _RegistryCore
from ..registry.publication import _version_from_payload
from ..registry.strict_contracts import ref_payload
from ..registry.schema_catalog import canonical_json
from ..registry.user_collaboration import TYPES
from ..registry.observer_access import ObserverReadScope, RegistryReadObserverContext
from ..registry.resources import ResourceVersionRef
from ..registry.identities import TypedId
from . import UserCollaborationHost, ClosedModuleAuthor, SourceQualifiedVersionRef, SourceQualifiedResourceRef
from .materials import _elements
from .project_native import (catalog, registration, start, validation_module, input_value,
                             execute_validation, execute_task, MODEL_CONDITION)
from .project_entry import ProjectEndpoint, parse_request, MAX_REQUEST_BYTES
from .project_framing import encode_response, read_response, ResponseFrameError, response_error
from .read_host_config import _read_owned_file
from .registry_typed_readers import TypedReaderCatalog
from .registry_read_contracts import ReadSessionRequest, ExplicitSources, SourceSelection, ReadLimits
from .registry_read_session import (RegistryReadHostBinding, ExistingReadAuthorityProvider,
                                   open_registry_session, open_readonly_source)


def write_configuration(path, document):
    # Initial local selector file only. No handle, credential or authority is serialized.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(canonical_json(document))


def initialize(root, module, inputs, participants, *, execution_profile=None, package=None, dependencies=(), read_closure_limit=4 * 1024 * 1024):
    if type(read_closure_limit) is not int or not 1 <= read_closure_limit <= 64 * 1024 * 1024:
        raise ValueError("trusted read closure limit must be 1–64 MiB")
    if root.exists() or not 1 <= participants <= 8:
        raise ValueError("choose a fresh directory and 1–8 controlled contributor routes")
    root.mkdir(mode=0o700, parents=True)
    from cpn.llm_adapters import load_llm_execution_selection
    selection = None if execution_profile is None else load_llm_execution_selection(execution_profile)
    owner = start(module, inputs, root / "owner", "Project owner", selection=selection)
    source = "project-" + uuid.uuid4().hex
    owner.schema_gateway.bind_source_identity(source_id=source, command_id="project-source")
    host = UserCollaborationHost(owner._core, owner.schema_gateway)
    session = host.create_session(origin_core=owner._core, principal_ref=owner.principal_ref, command_id="owner-session")
    author = ClosedModuleAuthor(owner.schema_gateway, owner.registration, SourceQualifiedVersionRef(source, owner.principal_ref))
    ids = {path: "element:" + uuid.uuid4().hex for path in _elements(module)}
    if package is None:
        initial = author.publish(module=module, element_ids=ids, command_id="initial-project")
        initial_ref = initial.revision.revision_ref
        import_result = None
    else:
        from .subnet_exchange import plan_subnet_import, import_subnet, TargetRegistrySelection
        from .materials import validate_closed_revision
        plan = plan_subnet_import(package, local_packages=dependencies,
            target=TargetRegistrySelection(SourceQualifiedVersionRef(source, owner.identity.task_ref)))
        imported = import_subnet(author, plan=plan, command_id="initial-project-import",
            expected_target_head={"ordinal": owner._core.event_store.max_ordinal(),
                                  "writer_fencing_epoch": owner._core.writer_epoch})
        initial_ref = imported.revision_ref
        initial = validate_closed_revision(owner._core, initial_ref, owner.registration)
        import_result = imported.to_dict()
    branch = owner.schema_gateway.create_author_branch(head_revision_ref=initial_ref, command_id="project-branch")
    from ..registry.module_runtime import hydrate_module_runtime
    project = host.protect(session, branch_ref=branch.branch_ref,
        runtime_net_ref=hydrate_module_runtime(owner._core)[0].net_ref, command_id="protect-project")
    routes = {"owner": {"session": ref_payload(session.ref), "principal": ref_payload(owner.principal_ref)}}
    for index in range(1, participants + 1):
        name = "contributor-" + str(index)
        personal = start(validation_module(), {"request": input_value(module.to_dict())}, root / name, name)
        personal.schema_gateway.bind_source_identity(source_id=source + "-" + name, command_id="personal-source")
        joined = host.create_session(origin_core=personal._core, principal_ref=personal.principal_ref, command_id=name + "-session")
        routes[name] = {"session": ref_payload(joined.ref), "principal": ref_payload(personal.principal_ref)}
        personal.record_owner_stop(idempotency_key="project-initial-stop")
    owner.record_owner_stop(idempotency_key="project-initial-stop")
    config = {"format": "controlled-project-launcher/v1", "project": ref_payload(project), "routes": routes, "execution_profile": None if execution_profile is None else str(execution_profile.absolute()),
        "read_closure_limit": read_closure_limit}
    write_configuration(root / "project.json", config)
    return {"project": ref_payload(project), "branch": branch.to_dict(), "initial_revision": initial.revision.to_dict(), "import_result": import_result, "routes": routes,
            "status": "STOPPED", "scope": "controlled local identities; no model or business execution"}


def load_configuration(root):
    raw, _ = _read_owned_file(root / "project.json")
    config = json.loads(raw)
    if set(config) != {"format", "project", "routes", "execution_profile", "read_closure_limit"} or config["format"] != "controlled-project-launcher/v1":
        raise ValueError("unsupported local project configuration")
    if type(config["routes"]) is not dict:
        raise ValueError("fixed routes required")
    names = set(config["routes"])
    if "owner" not in names or any(name != "owner" and name not in {"contributor-" + str(i) for i in range(1, 9)} for name in names):
        raise ValueError("only fixed local routes are supported")
    for row in config["routes"].values():
        if type(row) is not dict or set(row) != {"session", "principal"}:
            raise ValueError("route requires exact preexisting session and principal selectors")
        if _version_from_payload(row["session"]).entity_type != "user_collaboration_session/v1" or _version_from_payload(row["principal"]).entity_type != "principal/v1":
            raise ValueError("route selectors have incorrect types")
    if type(config["read_closure_limit"]) is not int or not 1 <= config["read_closure_limit"] <= 64 * 1024 * 1024:
        raise ValueError("invalid trusted read closure limit")
    profile = config["execution_profile"]
    if profile is not None and (type(profile) is not str or not Path(profile).is_absolute()):
        raise ValueError("execution profile is a trusted absolute local startup selector")
    return config


class LocalProjectApplication:
    def __init__(self, root):
        self.root = root
        config = load_configuration(root)
        self.read_limits = ReadLimits(max_material_closure_bytes=config["read_closure_limit"])
        names = set(config["routes"])
        from cpn.llm_adapters import load_llm_execution_selection
        self.selection = None if config["execution_profile"] is None else load_llm_execution_selection(Path(config["execution_profile"]))
        self.owners, self.endpoints, self.readers = {}, {}, {}
        self.viewer_servers = {}
        from threading import get_ident
        self.owner_thread = get_ident()
        self.execution_loop = None
        self.run_requested = None
        self.run_state = None
        # A read-only command never enters this launcher: opening a mutable
        # owner is explicit, and always uses the supported resume lifecycle.
        for name in sorted(names, key=lambda x: x != "owner"):
            selection = self.selection if name == "owner" else None
            bindings = None
            if selection is not None:
                from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
                from ..agent_tasks import _execution_route
                bindings = make_optional_agent_host_bindings(selection.input_target,
                    workspace_policy=selection.runtime_policy.workspace,
                    provider_backend_config=_execution_route(selection),
                    transport_contract={"interaction_protocol_ref": "llm_request_envelope/v1",
                                        "response_adapter_ref": "llm_response_envelope/v1"})
            owner = resume_run(registration(), run_dir=root / name,
                model_condition=MODEL_CONDITION if selection is None else selection.input_target.model_condition,
                catalog=catalog(), host_execution_bindings=bindings)
            expected = _version_from_payload(config["routes"][name]["principal"])
            if owner.principal_ref != expected:
                raise ValueError("owned Registry principal changed")
            self.owners[name] = owner
        self.owner = self.owners["owner"]
        self.host = UserCollaborationHost(self.owner._core, self.owner.schema_gateway)
        self.project = _version_from_payload(config["project"])
        for name, original in self.owners.items():
            session = self.host.resume_session(_version_from_payload(config["routes"][name]["session"]),
                origin_core=original._core, principal_ref=original.principal_ref)
            self.endpoints[name] = ProjectEndpoint(self.host, session, self.project, owner=self.owner,
                origin_core=original._core, principal_ref=original.principal_ref,
                human_validator=self.validator(original))
        self.stopped = False

    def validator(self, personal):
        def validate(session, document, command, restore_from):
            digest = hashlib.sha256(command.encode()).hexdigest()
            destination = self.root / ("validation-" + digest)
            native_input = input_value(document, restore_from=restore_from)
            previous = self.host._prior("execution", command + ":execution")
            if previous is None:
                if destination.exists():
                    raise ValueError("interrupted validation exists without a launch receipt; inspect it before retry")
                execution = start(validation_module(), {"request": native_input}, destination, "Human input validation tool")
                execution.schema_gateway.bind_source_identity(source_id="human-validation-" + digest, command_id="source")
                launch = self.host.bind_execution(session, origin_core=personal._core,
                    execution_core=execution._core, command_id=command + ":execution")
                execute_validation(execution, self.root / ("v-" + digest[:10] + ".sock"))
                core = execution._core
            else:
                core = _RegistryCore(destination, create=False, read_only=True, catalog=catalog())
                # Match original envelope including restore provenance before retry.
                from ..registry.user_collaboration_source import completed_text
                _, completed, witness = completed_text(core)
                # The canonical successful output bytes prove content; source
                # immutable input authority independently pins the envelope.
                from ..run import _load_resume_material
                material = _load_resume_material(core, registration())
                original_input = core.get_version(material.original_input_ref.resource_version_id)
                if (core.object_store.read_registered(original_input) != native_input.payload
                        or canonical_json(json.loads(completed)) != canonical_json(document)):
                    raise ValueError("retry changed its original human input")
                launch = _version_from_payload(previous["self_ref"])
            return core, launch
        return validate

    def grant_read(self, request):
        required = {"op", "subject_session", "command_id", "expires_at", "record_types",
                    "material_refs", "export_refs", "export_destinations"}
        if set(request) != required:
            raise ValueError("grant-read requires exact subject, expiry and explicit typed/material/export selection")
        from ..registry.observer_access import timestamp
        seconds = (timestamp(request["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
        if not 0 < seconds <= 3600:
            raise ValueError("read grant expiry must be within the next hour")
        selected = [e for e in self.endpoints.values() if ref_payload(e.session.ref) == request["subject_session"]]
        if len(selected) != 1:
            raise ValueError("subject is not a configured original local session")
        endpoint = selected[0]
        self.endpoints["owner"]._authorize(owner=True)
        core, source = self.owner._core, self.host._binding["source_id"]
        typed = TypedReaderCatalog()
        if type(request["record_types"]) is not list or any(kind not in typed.entry_types for kind in request["record_types"]):
            raise ValueError("record_types must explicitly name supported typed readers")
        fields = {kind: tuple(typed.fields(kind)) for kind in request["record_types"]}
        # Existing collaboration HOST requires complete collaboration fact
        # record scope. PN records are only the explicitly selected types;
        # body/export refs are exact caller selections, never an all-store scan.
        records = {**fields, **{kind: ("*",) for kind in TYPES}}
        materials = tuple(SourceQualifiedResourceRef.from_dict(value, catalog=core.catalog)
                          for value in request["material_refs"])
        exports = tuple((SourceQualifiedResourceRef if value.get("schema_version") ==
            "rpnh/collaboration/source_resource_ref/v1" else SourceQualifiedVersionRef).from_dict(value, catalog=core.catalog)
            for value in request["export_refs"])
        if any(ref.source_id != source for ref in (*materials, *exports)):
            raise ValueError("read selection must stay in this exact local source")
        purpose = "Inspect this controlled local project"
        observer = self.host.issue_read_access(self.endpoints["owner"].session,
            principal_ref=self.host.session_principal(endpoint.session),
            scope=ObserverReadScope(fields, records, materials, exports, tuple(request["export_destinations"])), purpose=purpose,
            expires_at=request["expires_at"],
            command_id="project-read:" + request["command_id"])
        endpoint.session = self.host.bind_read_authority(endpoint.session, observer)
        def resolver(selected_source, access_path):
            if selected_source != source or access_path != "local":
                raise ValueError("source outside fixed project")
            return open_readonly_source(core.run_dir, catalog=core.catalog)
        caller = endpoint.session.actor
        reader = open_registry_session(ReadSessionRequest(ExplicitSources((SourceSelection(
            SourceQualifiedVersionRef(source, self.owner.identity.task_ref), "local"),)), purpose),
            host=RegistryReadHostBinding(caller, resolver,
                ExistingReadAuthorityProvider({(caller, source, "local"): observer}), typed, limits=self.read_limits))
        old = self.readers.get(caller)
        if old is not None:
            old.close()
        self.readers[caller] = reader
        endpoint.reader = reader
        from cpn.frontend.dashboard import RegistryDashboard
        from cpn.frontend.user_contributions import UserContributionView
        endpoint.viewer = RegistryDashboard(core.run_dir, catalog=core.catalog,
            user_contribution_view=UserContributionView(self.host, endpoint.session, reader, source_id=source))
        return {"grant": observer.to_dict(), "scope": "all collaboration facts in this task; explicitly selected PN record types and exact body/export refs"}

    def process(self, route, request):
        from threading import get_ident
        if get_ident() != self.owner_thread:
            raise RuntimeError("project commands must execute in the sole owner dispatcher")
        endpoint = self.endpoints[route]
        if request.get("op") == "viewer-open":
            if route != "owner" or set(request) != {"op"}:
                raise PermissionError("the installed Viewer is currently owner-only")
            endpoint._authorize(owner=True)
            if endpoint.viewer is None:
                raise ValueError("configure explicit owner read selections before opening contributions")
            cached = self.viewer_servers.get(route)
            if cached is not None and (cached.expired or cached.socket.fileno() < 0):
                cached.shutdown()
                cached.server_close()
                del self.viewer_servers[route]
            if route not in self.viewer_servers:
                from cpn.frontend.server import _ProjectionHTTPServer
                from threading import Thread
                # Reuse the official Viewer server and its loopback/origin/CSP
                # boundary. The wrapper rechecks original owner permission on
                # every Viewer data method; no mutation routes are installed.
                server = _ProjectionHTTPServer(OwnerProjectViewer(endpoint), "127.0.0.1", 0, False)
                self.viewer_servers[route] = server
                Thread(target=server.serve_forever, daemon=True).start()
            return {"url": self.viewer_servers[route].origin + "/", "mode": "owner-only official read-only Viewer"}
        if request.get("op") == "revoke-read":
            if route != "owner" or set(request) != {"op", "grant", "command_id"}:
                raise PermissionError("read revocation requires exact owner-selected grant and command_id")
            endpoint._authorize(owner=True)
            return ref_payload(self.host.revoke_read_access(endpoint.session,
                grant_ref=_version_from_payload(request["grant"]), command_id="project-revoke-read:" + request["command_id"]))
        if request.get("op") == "run":
            if route != "owner" or set(request) != {"op", "command_id"}:
                raise PermissionError("run requires an explicit original owner command")
            endpoint._authorize(owner=True)
            command = request["command_id"]
            if type(command) is not str or not command or len(command) > 160:
                raise ValueError("run command_id must be a nonempty bounded string")
            if self.selection is None or self.execution_loop is None:
                raise ValueError("run needs a configured approved profile and the original live owner dispatcher")
            if self.run_state is not None and self.run_state["command_id"] == command:
                return dict(self.run_state)
            if self.run_requested is not None or self.run_state is not None and self.run_state["status"] == "RUNNING":
                raise ValueError("the original task is already running; inspect runtime-status")
            if self.owner.snapshot()["active_firings"]:
                raise ValueError("unsettled native firings require the original recovery path, not a second run")
            if self.host._prior("execution", "project-task-run:" + command) is not None:
                from ..registry.run_authority import current_run_execution_authority
                from ..registry.resource_service import _ResourceServiceKernel
                _, authority = current_run_execution_authority(
                    self.owner._core, _ResourceServiceKernel(self.owner._core))
                if (authority["status"] != "running"
                        or authority.get("reopen_authorization_ref") is None):
                    raise ValueError("run was previously admitted outside this process; inspect original runtime facts and recovery boundary")
            # Persist original launch provenance, never a second execution ledger.
            # After explicit native checkpoint reentry, bind_execution verifies
            # and replays the exact original association; it creates no new one.
            launch = self.host.bind_execution(endpoint.session, origin_core=self.owner._core,
                execution_core=self.owner._core, command_id="project-task-run:" + command)
            self.run_requested = command
            self.run_state = {"command_id": command, "status": "REQUESTED", "execution_ref": ref_payload(launch)}
            return dict(self.run_state)
        if request.get("op") == "runtime-status":
            result = endpoint.dispatch(request)
            # Convenience transport status is not authoritative runtime state.
            result["host_run_request"] = None if self.run_state is None else dict(self.run_state)
            return result
        if request.get("op") == "grant-read":
            if route != "owner":
                raise PermissionError("only the original owner route issues read access")
            return self.grant_read(request)
        if request.get("op") == "stop-host":
            if route != "owner" or set(request) != {"op", "command_id"}:
                raise PermissionError("stop requires the explicit owner route and command_id")
            endpoint._authorize(owner=True)
            if self.run_state is not None and self.run_state["status"] == "RUNNING":
                raise ValueError("task is running; stop requires its original drained owner boundary")
            for name, owner in self.owners.items():
                owner.record_owner_stop(idempotency_key="project-host-stop:" + request["command_id"] + ":" + name)
            self.stopped = True
            return {"status": "STOPPED"}
        return endpoint.dispatch(request)

    def close_readers(self):
        for server in self.viewer_servers.values():
            server.shutdown(); server.server_close()
        self.viewer_servers.clear()
        for reader in self.readers.values():
            reader.close()


class OwnerProjectViewer:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def __call__(self):
        self.endpoint._authorize(owner=True)
        result = self.endpoint.viewer()
        self.endpoint._authorize(owner=True)
        return result

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        value = getattr(self.endpoint.viewer, name)
        if not callable(value):
            return value
        def checked(*args, **kwargs):
            self.endpoint._authorize(owner=True)
            result = getattr(self.endpoint.viewer, name)(*args, **kwargs)
            self.endpoint._authorize(owner=True)
            return result
        return checked


def _receive_commands(selector, submit, stopped):
    """Transport only. All HOST/Registry work is queued to the original loop."""
    while not stopped.is_set():
        for key, _ in selector.select(timeout=0.2):
            client, _ = key.fileobj.accept()
            with client:
                client.settimeout(5)
                stream = client.makefile("rwb")
                try:
                    raw = stream.readline(MAX_REQUEST_BYTES + 2)
                    if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST_BYTES:
                        raise ValueError("invalid bounded request frame")
                    request = parse_request(raw)
                    # Future cancellation/disconnect does not revoke admitted
                    # commands. Original submit_host claims each Future first.
                    result = submit(key.data, request).result()
                    response = {"ok": True, "result": result}
                except Exception as exc:
                    response = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
                try:
                    stream.write(encode_response(response)); stream.flush()
                except (OSError, ValueError):
                    pass  # Never kill the sole dispatcher for a lost reply.


def serve(root):
    from threading import Thread, Event
    from ..control_server import OwnerEventLoop
    config = load_configuration(root)
    listeners, selector = [], selectors.DefaultSelector()
    application = owner_loop = receiver = None
    stopped = Event()
    try:
        # Check fixed transport before opening any owner writer.
        if (root / "dispatcher.sock").exists():
            raise ValueError("original dispatcher socket already exists")
        for name in config["routes"]:
            path = root / (name + ".sock")
            if path.exists():
                raise ValueError("socket path already exists; do not overwrite another HOST")
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.bind(str(path)); connection.listen(4)
            inode = path.stat(follow_symlinks=False)
            listeners.append((connection, path, (inode.st_dev, inode.st_ino)))
            selector.register(connection, selectors.EVENT_READ, name)
        application = LocalProjectApplication(root)
        owner_loop = OwnerEventLoop(application.owner, root / "dispatcher.sock")
        application.execution_loop = owner_loop
        def submit(route, request):
            return owner_loop.submit_host(lambda: application.process(route, request))
        receiver = Thread(target=_receive_commands, args=(selector, submit, stopped), daemon=True)
        receiver.start()
        print(json.dumps({"status": "READY", "routes": {name: str(root / (name + ".sock")) for name in config["routes"]},
                          "scope": "same-OS controlled routes, not authenticated humans"}), flush=True)
        while not application.stopped:
            owner_loop.dispatch_ready(timeout=0.2)
            if application.run_requested is not None and not application.stopped:
                application.run_requested = None
                application.run_state["status"] = "RUNNING"
                try:
                    result = execute_task(application.owner, root / "dispatcher.sock", application.selection,
                                          event_loop=owner_loop)
                    application.run_state.update(status="RETURNED", result=result)
                except Exception as exc:
                    application.run_state.update(status="FAILED", error=type(exc).__name__, message=str(exc))
                # The same original dispatcher remains available after task
                # terminal/failure. No new writer or scheduling policy appears.
    finally:
        stopped.set()
        if receiver is not None:
            receiver.join(timeout=1)
        if application is not None:
            application.close_readers()
        if owner_loop is not None:
            owner_loop.close()
        for connection, path, identity in listeners:
            connection.close()
            try:
                current = path.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) == identity:
                    path.unlink()
            except FileNotFoundError:
                pass
        selector.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("init", help="Create a controlled project using the fixed builtin stock HOST")
    create.add_argument("--root", type=Path, required=True)
    definition = create.add_mutually_exclusive_group(required=True)
    definition.add_argument("--module", type=Path)
    definition.add_argument("--package", type=Path, help="Import exact package into a fresh independent local project")
    create.add_argument("--dependency", type=Path, action="append", default=[])
    create.add_argument("--inputs", type=Path, required=True, help="Object mapping entry names to {schema_id,value}")
    create.add_argument("--contributors", type=int, default=2)
    create.add_argument("--read-closure-limit", type=int, default=4 * 1024 * 1024,
        help="Trusted startup total projection-body budget in bytes; per-body and response remain 4 MiB")
    create.add_argument("--execution-profile", type=Path, help="Existing approved local provider profile; no model is called by init")
    open_host = commands.add_parser("serve", help="Explicitly resume stopped owners; does not mint read grants")
    open_host.add_argument("--root", type=Path, required=True)
    call = commands.add_parser("call", help="Send one bounded command to a fixed local route")
    call.add_argument("--socket", type=Path, required=True)
    call.add_argument("--request", type=Path)
    args = parser.parse_args(argv)
    if args.action == "init":
        package = None
        if args.package is None:
            module = ModuleDeclaration.from_dict(json.loads(args.module.read_text()))
        else:
            from .package_preview import preview_package
            package = preview_package(args.package.read_bytes())
            entry = next(e for e in package.manifest["entries"] if e["entry_id"] == "main")
            module = ModuleDeclaration.from_dict(json.loads(dict(package.artifacts)[entry["declaration_path"]]))
        inputs = {k: OwnerInput(v["schema_id"], canonical_json(v["value"]), "Operator project input")
                  for k, v in json.loads(args.inputs.read_text()).items()}
        print(json.dumps(initialize(args.root.absolute(), module, inputs, args.contributors, execution_profile=args.execution_profile,
            package=package, dependencies=tuple(p.read_bytes() for p in args.dependency),
            read_closure_limit=args.read_closure_limit), indent=2))
    elif args.action == "serve":
        serve(args.root.absolute())
    else:
        raw = args.request.read_bytes() if args.request else sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        request = parse_request(raw)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(args.socket))
            stream = connection.makefile("rwb")
            stream.write(canonical_json(request) + b"\n"); stream.flush()
            try:
                response = read_response(stream)
            except ResponseFrameError as exc:
                response = response_error(str(exc))
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
