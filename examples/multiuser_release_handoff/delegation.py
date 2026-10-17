"""Bounded trusted HOST delegation onto one original source PN.

Call only outside managed invocation contexts, on the source owner's serialized
command transport. Never expose owners, executions or these handles to model JSON.
Foreign service.prepare supplies the actual execution/call fields. This is a local
HOST composition, not a distributed delegation API. Registry marking is the only
claim/version authority; Python handles merely retain exact native executions.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import posixpath
import uuid

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
from cpn.rpnh.collaboration.references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from cpn.rpnh.collaboration.sources import get_local_source_identity
from cpn.rpnh.collaboration.registry_read_contracts import ExplicitSources, ReadSessionRequest, SourceSelection
from cpn.rpnh.collaboration.registry_read_session import (
    ExistingReadAuthorityProvider, RegistryReadHostBinding, open_readonly_source, open_registry_session,
)
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    ArcDeclaration, InitialTokenDeclaration, LeaseClaimExpression, LeaseClaimTemplate,
    LeaseIdentityDeclaration, LogicalSlotBinding, PNFragment, PlaceDeclaration, PortBinding,
    ResourceLeasePoolBinding, TransitionDeclaration, VariableResourceArc, PortDeclaration,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access, revoke_observer_access
from cpn.rpnh.registry.operation_execution import verify_operation_execution
from cpn.rpnh.registry import operations as native_operations
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resources import BindResourceAddress, ResourceAddress
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.run import OwnerInput, start_run

DATA = "application/multiuser_source_delegation/v1"
EXECUTOR = "example/multiuser-source-host/v1"
TERMINAL = "example/multiuser-source-terminal/v1"


class DelegationDenied(ValueError):
    pass


class SourceBusy(DelegationDenied):
    """Native source admission refused; no world write occurred."""


class StaleVersion(DelegationDenied):
    pass


def _key():
    return "source-delegation:" + uuid.uuid4().hex


def _execution(owner, execution):
    with _inventory(owner):
        kernel, repository = owner.operation_repository()
        return verify_operation_execution(owner._core, kernel, repository, execution)


@contextmanager
def _inventory(owner=None):
    # Never replace the process inventory while independent owners execute.
    # Creation composes the single trusted HOST inventory before concurrency.
    current = native_operations._OPERATION_REGISTRATION
    if owner is not None:
        for kind in ('executor', 'tool'):
            for item in owner.registration.declarations(kind):
                if current is None or current.declaration(kind,item['key']) != item:
                    raise DelegationDenied('HOST inventory must be composed before execution')
    yield


def _source_inventory(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with _inventory(self.owner):
            return method(self, *args, **kwargs)
    return call


def _preserve_inventory(method):
    @wraps(method)
    def call(*args, **kwargs):
        previous = native_operations._OPERATION_REGISTRATION
        result = method(*args, **kwargs)
        inventories = [u.registration for u in kwargs['requesters'].values()]
        inventories.append(result.owner.registration)
        if previous is not None: inventories.append(previous)
        combined, seen = Registration(), {}
        for inventory in inventories:
            for item in inventory.declarations():
                kind,key=item['kind'],item['key']
                if (kind,key) in seen:
                    if seen[kind,key] != item: raise DelegationDenied('Conflicting HOST inventory')
                    continue
                seen[kind,key]=item
                if kind=='schema': combined.register_schema(key,item['schema'])
                else: getattr(combined,'register_'+kind)(key,inventory.resolve(kind,key),
                    identity=item['identity'],contracts=item['contracts'])
        native_operations.bind_operation_registration(combined)
        return result
    return call


def _execution_doc(owner, execution):
    op = execution.operation
    ctx = op.canonical.context
    identity = get_local_source_identity(owner._core)
    if identity is None:
        raise DelegationDenied("foreign owner requires its native source identity")
    return dict(source_id=identity.source_id, task_ref=_ref_payload(ctx.task_ref),
                net_ref=_ref_payload(ctx.net_instance_ref), principal_ref=_ref_payload(ctx.principal_ref),
                invocation_ref=_ref_payload(ctx.invocation_ref),
                firing_ref=_ref_payload(op.firing.transition_firing_ref),
                execution_lease_ref=_ref_payload(execution.operation_execution_lease_ref),
                start_event_id=str(execution.start_event_id))


def _module(reader_count, limit):
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(DATA, {"$id": DATA, "$schema": "http://json-schema.org/draft-07/schema#", "type": "object"})
    registration.register_executor(EXECUTOR, lambda **kwargs: None,
        identity={"implementation_id": EXECUTOR, "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": TERMINAL, "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    budget = {"bucket_id": "source", "budget_scope": "module", "finalization_scope": None}
    ports, operations = [], []
    mapping = {}
    def port(name, direction, place=None):
        ports.append(dict(name=name, direction=direction, schema=DATA))
        mapping[name] = place or name
        return name
    def operation(name, inputs, outputs):
        operations.append(dict(name=name, executor=EXECUTOR, inputs=inputs, outputs=outputs,
            request_port=None, tools=[], config={}, budget_binding=budget,
            outcomes=[dict(name="complete", products=[dict(port=p) for p in outputs]),
                      # Native checkpoint reentry may abandon an active source
                      # firing without publishing products or applying effects.
                      dict(name="interrupted", products=[], effects=[])]))
    port("seed", "input", "seed_input")
    port("write_request", "output")
    port("write_input", "input", "write_request")
    port("body", "output")
    port("body_input", "input", "body")
    port("write_receipt", "output")
    port("index", "output")
    operation("seed", ["seed"], ["write_request"])
    operation("prepare_edit", ["body_input"], ["write_request"])
    operation("write", ["write_input"], ["body", "write_receipt", "index"])
    port('gate_message','input')
    port('message','output','message_result')
    operation('message',['gate_message'],['message'])
    for i in range(reader_count):
        port(f"gate{i}", "input")
        port(f"request{i}", "output")
        port(f"input{i}", "input", f"request{i}")
        port(f"result{i}", "output")
        operation(f"prepare{i}", [f"gate{i}"], [f"request{i}"])
        operation(f"read{i}", [f"input{i}"], [f"result{i}"])

    def lower(config, context):
        places = [PlaceDeclaration(name, DATA,
            initial_tokens=(InitialTokenDeclaration(value={}, schema=DATA),) if name.startswith("gate") else ())
            for name in dict.fromkeys(mapping.values())]
        places.append(PlaceDeclaration("lease", DATA, token_kind="resource_lease", capacity=1, reusable=True))
        arcs = [ArcDeclaration("seed_input", "seed", "input"),
                ArcDeclaration("write_request", "seed", "output", mode="produce", outcome="complete",
                               lease_claims=(LeaseClaimExpression("note", "produce"),)),
                ArcDeclaration("body", "prepare_edit", "input"),
                ArcDeclaration("write_request", "prepare_edit", "output", mode="produce", outcome="complete",
                               lease_claims=(LeaseClaimExpression("note", "edit", "body"),)),
                ArcDeclaration("write_request", "write", "input")]
        arcs.extend((ArcDeclaration('gate_message','message','input',mode='read'),
                     ArcDeclaration('message_result','message','output',mode='produce',outcome='complete')))
        for name in ("body", "write_receipt", "index"):
            arcs.append(ArcDeclaration(name, "write", "output", mode="produce", outcome="complete"))
        variables = [VariableResourceArc("write", "write_request", "lease")]
        for i in range(reader_count):
            arcs.extend((ArcDeclaration(f"gate{i}", f"prepare{i}", "input", mode="read"),
                         ArcDeclaration(f"request{i}", f"prepare{i}", "output", mode="produce", outcome="complete"),
                         ArcDeclaration(f"request{i}", f"read{i}", "input"),
                         ArcDeclaration(f"result{i}", f"read{i}", "output", mode="produce", outcome="complete")))
            variables.append(VariableResourceArc(f"read{i}", f"request{i}", "lease",
                              (LeaseClaimTemplate("note", access_mode="read"),)))
        internal = tuple(PortDeclaration(p["name"], p["direction"], p["schema"]) for p in ports
                         if p["direction"] == "input" and p["name"] != "seed")
        return PNFragment(tuple(places), tuple(TransitionDeclaration(op.name, op.name) for op in context.operations),
            tuple(arcs), tuple(PortBinding(p.name, mapping[p.name]) for p in context.ports), context.operations,
            internal_ports=internal, internal_bindings=tuple(PortBinding(p.name, mapping[p.name]) for p in internal),
            lease_identities=(LeaseIdentityDeclaration("note", kind="slot", slot="note"),),
            lease_pools=(ResourceLeasePoolBinding("lease", "lease", initial_slots=("note",)),),
            variable_resource_arcs=tuple(variables),
            logical_slots=(LogicalSlotBinding("note", "write", "body", "body", "body", DATA),))
    registration.register_component("source", lower,
        identity={"implementation_id": "example.source-delegation", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    doc = dict(schema_version="rpnh/module_declaration/v1", name="SourceDelegation",
        components=[dict(name="source", key="source", config_schema=CONFIG_SCHEMA_ID,
                         config={}, ports=[p for p in ports if p["direction"] == "output" or p["name"] == "seed"], operations=operations)], links=[],
        entry={"seed": {"component": "source", "port": "seed"}},
        exit={"receipt": {"component": "source", "port": "write_receipt"}},
        terminal=dict(key=TERMINAL, source={"component": "source", "port": "write_receipt"},
                      operation="write", outcome="complete", config={"run_outcome": "complete"}),
        required_schemas=[CONFIG_SCHEMA_ID, DATA], budgets={},
        budget_buckets=[dict(**budget, max_attempts=limit)])
    return ModuleDeclaration.from_dict(doc), registration, doc["budget_buckets"]


@dataclass(frozen=True)
class ForeignBinding:
    adapter: object
    origin_owner: object
    execution: object
    principal_ref: object
    session: object
    reader_index: int
    address_result: object
    index_address_result: object
    request_json: bytes


@dataclass(frozen=True)
class ActiveRead:
    binding: ForeignBinding
    execution: object
    result_json: bytes
    observer: object

    @property
    def result(self):
        return json.loads(self.result_json)


@dataclass(frozen=True)
class ActiveEdit:
    binding: ForeignBinding
    execution: object
    previous: dict
    new_body: str


class SourceDelegation:
    """HOST-only original source owner, with native per-requester read lanes."""

    @classmethod
    @_preserve_inventory
    def create(cls, *, run_dir, body, requesters, editors, max_operations=256, workspace_root=None):
        path = Path(run_dir).absolute()
        workspace = Path(workspace_root).resolve() if workspace_root is not None else path.parent.resolve()
        if path.resolve() != path or not path.is_relative_to(workspace) or path.exists():
            raise ValueError("source needs a fresh directory inside the writable workspace")
        if not isinstance(body, str) or not requesters:
            raise ValueError("fresh body and actual requester owners required")
        users = tuple(requesters.values())
        if len({u.principal_ref for u in users}) != len(users):
            raise ValueError("requesters must own distinct native principals")
        if not set(editors) <= {u.principal_ref for u in users}:
            raise ValueError("editors must be exact configured requester principals")
        module, registration, buckets = _module(len(users), max_operations)
        schemas, types, paths = user_collaboration_schema_data()
        task = OwnerInput(DATA, canonical_json({"requesters": {name: _ref_payload(u.principal_ref)
            for name, u in requesters.items()}, "editors": [_ref_payload(p) for p in editors]}), "Source HOST policy")
        owner = start_run(module, registration, run_dir=path, task_input=task, entry_inputs={"seed": task},
            budgets=ModuleBudgetDeclaration(tuple(buckets), (DATA,), max_operations, 0, max_operations, 0),
            model_condition="trusted-local-host-no-provider", owner_statement="Delegate exact foreign executions onto source slot",
            owner_name="Release note source owner", command_id=_key(),
            catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
        owner.schema_gateway.bind_source_identity(source_id="release-source:" + str(owner.identity.task_ref.entity_id), command_id=_key())
        self = cls()
        self.owner, self.requesters, self.editors = owner, users, frozenset(editors)
        self.host = UserCollaborationHost(owner._core, owner.schema_gateway)
        self.source_id = get_local_source_identity(owner._core).source_id
        seed = self._start("seed")
        self._settle(seed, {"source.write_request": (canonical_json({"bootstrap": True}),)})
        execution = self._start("write")
        self._write(execution, body, {"bootstrap": True})
        return self

    @classmethod
    def resume(cls, *, run_dir, requesters, participants, checkpoint_version_id,
               command_id, reason, expected_identity):
        """Attach original Source with four fixed lanes and its persisted policy."""
        from cpn.rpnh.run import resume_run, _load_resume_material
        from cpn.rpnh.registry.publication import _version_from_payload
        # Factory supplies implementations only. Its new interrupted declaration
        # is never compiled or published over the original complete-only net.
        _, registration, _ = _module(4, 512)
        schemas, types, paths = user_collaboration_schema_data()
        owner = resume_run(registration, run_dir=run_dir,
            model_condition="trusted-local-host-no-provider",
            catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths),
            checkpoint_version_id=checkpoint_version_id,
            reopen_command_id=command_id, reopen_reason=reason)
        material = _load_resume_material(owner._core, registration)
        policy = json.loads(owner._core.object_store.read_registered(
            owner._core.get_version(material.original_input_ref.resource_version_id)))
        roles = ('operations', 'developer', 'qa', 'support')
        if (policy['requesters'] != {r: participants[r]['principal_ref'] for r in roles}
                or policy['editors'] != [participants['operations']['principal_ref']]):
            raise DelegationDenied("original source policy differs from selected participants")
        identity = get_local_source_identity(owner._core)
        if (_ref_payload(owner.identity.task_ref) != expected_identity['task_ref']
                or _ref_payload(owner.principal_ref) != expected_identity['principal_ref']
                or identity.source_id != expected_identity['source_id']):
            raise DelegationDenied("recovered Source identity differs")
        self = cls()
        self.owner = owner
        # None is the retained terminal developer lane, never an invented owner.
        self.requesters = tuple(requesters.get(r) for r in roles)
        self.editors = frozenset(_version_from_payload(p) for p in policy['editors'])
        self.host = UserCollaborationHost(owner._core, owner.schema_gateway)
        self.source_id = identity.source_id
        return self

    def _start(self, name):
        admission = self.owner.admit("source." + name, logical_tau=0, command_id=_key())
        if admission is None:
            raise SourceBusy("native source PN denies admission: " + name)
        return self.owner.start(admission, command_id=_key())

    def _settle(self, execution, products):
        outputs = self.owner.products(execution, outcome_id="complete", products=products, command_id=_key())
        self.owner.succeed(outputs, command_id=_key())
        return outputs

    @_source_inventory
    def current(self):
        executable, _, marking = hydrate_module_runtime(self.owner._core)
        matches = [t for t in marking.tokens if t.state.place == "source.lease"
                   and t.state.consumed_by is None and t.state.epoch == marking.epoch]
        if len(matches) != 1:
            raise SourceBusy("source lease is not available")
        token = matches[0]
        return dict(source_id=self.source_id, task_ref=_ref_payload(self.owner.identity.task_ref),
            net_ref=_ref_payload(executable.net_ref), slot_ref=_ref_payload(token.state.lease_identity_ref),
            lease_identity_ref=_ref_payload(token.state.lease_identity_ref), token_ref=_ref_payload(token.token_ref),
            resource_ref=None if token.state.resource_ref is None else _resource_payload(token.state.resource_ref),
            version=None if token.state.resource_ref is None else str(token.state.resource_ref.resource_version_id))

    def _resolve(self, name):
        # Trusted owner metadata resolution only. v2 observers never enter the
        # older v1 address API, and this grants no material-read authority.
        kernel, _ = self.owner.operation_repository()
        return kernel._resolve_address_unchecked(ResourceAddress(self.owner.identity.task_ref, name), kernel._head())

    def _material(self, session, ref):
        exact = SourceQualifiedResourceRef(self.source_id, ref)
        observer = issue_observer_access(self.owner.schema_gateway,
            principal_ref=self.host.session_principal(session), scope=ObserverReadScope({}, {}, (exact,)),
            purpose="inspect", expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id=_key())
        verified = "trusted-release-host"
        def resolve(source_id, access_path):
            if (source_id, access_path) != (self.source_id, "source"):
                raise DelegationDenied("unknown source route")
            return open_readonly_source(self.owner._core.run_dir, catalog=self.owner._core.catalog)
        host = RegistryReadHostBinding(verified, resolve,
            ExistingReadAuthorityProvider({(verified, self.source_id, "source"): observer}), TypedReaderCatalog())
        reader = open_registry_session(ReadSessionRequest(ExplicitSources((SourceSelection(
            SourceQualifiedVersionRef(self.source_id, self.owner.identity.task_ref), "source"),)), "inspect"), host=host)
        try:
            material = reader.read_material(resource_ref=exact, at_cut=reader.capture_cut(self.source_id))
            return json.loads(material["bytes"]), observer, material["source_cut"]
        finally:
            reader.close()

    @_source_inventory
    def bind_execution(self, *, origin_owner, execution, principal_ref, call_id, tool_name, arguments,
                       relative_link="release_note", project_index="project/index"):
        actual = _execution(origin_owner, execution)
        principal = actual.operation.canonical.context.principal_ref
        if principal != principal_ref or origin_owner.principal_ref != principal:
            raise DelegationDenied("foreign execution principal differs from requester")
        try:
            index = next(i for i, u in enumerate(self.requesters)
                         if u is not None and u is origin_owner and u.principal_ref == principal)
        except StopIteration:
            raise DelegationDenied("foreign owner was not configured by source HOST") from None
        if not call_id or not tool_name or not isinstance(arguments, dict):
            raise ValueError("actual managed service call fields required")
        session = self.host.create_session(origin_core=origin_owner._core, principal_ref=principal, command_id=_key())
        index_address = self._resolve(project_index)
        document, grant, _ = self._material(session, index_address.resource_ref)
        revoke_observer_access(self.owner.schema_gateway, grant_ref=grant.grant_ref, command_id=_key())
        relative = document["links"][relative_link]
        name = posixpath.normpath(posixpath.join(posixpath.dirname(project_index), relative))
        if relative.startswith("/") or name.startswith("../"):
            raise DelegationDenied("project link escapes its local address namespace")
        address = self._resolve(name)
        current = self.current()
        if _resource_payload(address.resource_ref) != current["resource_ref"]:
            raise StaleVersion("discovered address does not identify current source slot")
        data = dict(foreign_execution=_execution_doc(origin_owner, actual),
            source_caller_principal_ref=_ref_payload(self.owner.principal_ref),
            requester_principal_ref=_ref_payload(principal), session_ref=_ref_payload(session.ref),
            call_id=call_id, tool_name=tool_name, arguments=arguments,
            address_binding_ref=_ref_payload(address.binding_ref.as_version_ref()),
            index_address_binding_ref=_ref_payload(index_address.binding_ref.as_version_ref()),
            resolution_chain=[project_index, relative_link, relative, name], **current)
        return ForeignBinding(self, origin_owner, actual, principal, session, index, address, index_address, canonical_json(data))

    def _check(self, binding):
        if type(binding) is not ForeignBinding or binding.adapter is not self:
            raise DelegationDenied("foreign binding belongs to another HOST")
        actual = _execution(binding.origin_owner, binding.execution)
        if actual.operation.canonical.context.principal_ref != binding.principal_ref:
            raise DelegationDenied("foreign requester changed")
        if _execution_doc(binding.origin_owner, actual) != json.loads(binding.request_json)["foreign_execution"]:
            raise DelegationDenied("foreign execution changed")
        return json.loads(binding.request_json)

    def _prepare(self, name, output, request):
        _, _, marking = hydrate_module_runtime(self.owner._core)
        pending = [t for t in marking.tokens if t.state.place == "source." + output
                   and t.state.consumed_by is None and t.state.epoch == marking.epoch]
        if pending:
            prepared = self.owner._core.get_version(pending[0].state.resource_ref.resource_version_id)
            if len(pending) != 1 or self.owner._core.object_store.read_registered(prepared) != canonical_json(request):
                raise SourceBusy("another exact source request is pending")
            return
        execution = self._start(name)
        return self._settle(execution, {"source." + output: (canonical_json(request),)})

    def _check_input(self, execution, request):
        kernel, _ = self.owner.operation_repository()
        inputs = execution.operation.inputs
        if len(inputs) != 1 or kernel._read_firing_registered(
                execution.operation.canonical.context, inputs[0].resource_ref) != canonical_json(request):
            raise DelegationDenied("source firing consumed another exact delegation input")

    @_source_inventory
    def begin_read(self, binding):
        request = self._check(binding)
        if self.current()["resource_ref"] != request["resource_ref"]:
            raise StaleVersion("bound source body changed before read admission")
        self._prepare(f"prepare{binding.reader_index}", f"request{binding.reader_index}", request)
        execution = self._start(f"read{binding.reader_index}")
        self._check_input(execution, request)
        document, observer, cut = self._material(binding.session, binding.address_result.resource_ref)
        result = dict(request, body=document["body"], source_execution=_execution_doc(self.owner, execution),
            source_cut=cut, observer_grant_ref=_ref_payload(observer.grant_ref),
            source_input_refs=[_resource_payload(i.resource_ref) for i in execution.operation.inputs])
        return ActiveRead(binding, execution, canonical_json(result), observer)

    @_source_inventory
    def finish_read(self, read):
        self._check(read.binding)
        _execution(self.owner, read.execution)
        self._check_input(read.execution, json.loads(read.binding.request_json))
        result = read.result
        outputs = self._settle(read.execution, {f"source.result{read.binding.reader_index}": (read.result_json,)})
        revoke_observer_access(self.owner.schema_gateway, grant_ref=read.observer.grant_ref, command_id=_key())
        return dict(result, source_output_refs=[_resource_payload(i.resource_ref) for i in outputs.outputs], status="read_complete")

    @_source_inventory
    def begin_edit(self, binding, *, expected_version, new_body):
        request = self._check(binding)
        if binding.principal_ref not in self.editors:
            raise DelegationDenied("source HOST did not authorize this exact editor principal")
        if not isinstance(new_body, str):
            raise TypeError("release body must be text")
        previous = self.current()
        if expected_version != previous["version"] or request["resource_ref"] != previous["resource_ref"]:
            raise StaleVersion("edit expected version differs from canonical source slot")
        request = dict(request, expected_version=expected_version, new_body=new_body)
        self._prepare("prepare_edit", "write_request", request)
        execution = self._start("write")
        self._check_input(execution, request)
        return ActiveEdit(binding, execution, previous, new_body)

    @_source_inventory
    def commit_edit(self, edit):
        self._check(edit.binding)
        _execution(self.owner, edit.execution)
        # Exceptions intentionally leave native firing and lease claim active.
        # Caller must inspect/reconcile; unknown effects are never Success.
        request = json.loads(edit.binding.request_json)
        self._check_input(edit.execution, dict(request, expected_version=edit.previous["version"], new_body=edit.new_body))
        return self._write(edit.execution, edit.new_body, dict(request, previous=edit.previous))

    def edit(self, binding, *, expected_version, new_body):
        return self.commit_edit(self.begin_edit(binding, expected_version=expected_version, new_body=new_body))

    def try_edit(self, binding, *, expected_version, new_body):
        """Only admission refusal is busy/no body effect; commit failures propagate."""
        try:
            edit = self.begin_edit(binding, expected_version=expected_version, new_body=new_body)
        except SourceBusy as exc:
            return {"status": "busy", "body_effect": "none", "reason": str(exc)}
        return self.commit_edit(edit)

    @_source_inventory
    def publish_message(self, binding):
        """Publish the verified foreign request as a canonical source PN output.

        The foreign firing remains active; its provisional managed receipt is
        not exposed as a canonical source. Source/foreign principals stay distinct.
        """
        request=self._check(binding)
        execution=self._start('message')
        payload=dict(request,source_execution=_execution_doc(self.owner,execution))
        outputs=self._settle(execution,{'source.message':(canonical_json(payload),)})
        return outputs.outputs[0].resource_ref

    def _write(self, execution, body, request):
        evidence = dict(request, source_execution=_execution_doc(self.owner, execution),
                        source_input_refs=[_resource_payload(i.resource_ref) for i in execution.operation.inputs],
                        source_caller_principal_ref=_ref_payload(self.owner.principal_ref))
        outputs = self.owner.products(execution, outcome_id="complete", products={
            "source.body": (canonical_json({"body": body}),),
            "source.write_receipt": (canonical_json(evidence),),
            "source.index": (canonical_json({"links": {"release_note": "./release-note"}}),),
        }, command_id=_key())
        # Output order is the declared operation output order.
        body_ref, receipt_ref, index_ref = [a.resource_ref for a in outputs.outputs]
        kernel, _ = self.owner.operation_repository()
        ctx = execution.operation.canonical.context
        bindings = []
        for name, ref in (("project/release-note", body_ref), ("project/index", index_ref)):
            address = ResourceAddress(self.owner.identity.task_ref, name)
            previous = kernel._current_binding(address, through_ordinal=kernel._head().ordinal, include_tombstone=True)
            bindings.append(kernel.bind_address(ctx, BindResourceAddress(address, ref, ctx.operation_binding_ref, previous, _key()),
                                                reference_only=True))
        self.owner.succeed(outputs, command_id=_key())
        return dict(evidence, **self.current(), body=body, status="published",
                    address_binding_ref=_ref_payload(bindings[0].as_version_ref()),
                    source_output_refs=[_resource_payload(r) for r in (body_ref, receipt_ref, index_ref)])
