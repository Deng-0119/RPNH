"""Real owner/Registry fixtures with explicitly synthetic OS boundaries.

OwnerEventLoop, RegistryGateway, Registration, Start, and ExecutionServices are
the original implementations. Only its socket/socketpair/selector transport is
in memory. ``bind`` creates an empty *ordinary file* for the original
constructor's chmod/stat/inode cleanup; it never creates an OS socket.

The optional HOST's numerical inventory capture alone returns a declared
synthetic inventory. No numerical worker, native child, model, or transport is
executed. These are offline owner-gate fixtures, never native runtime evidence.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import selectors
import socket
from types import SimpleNamespace

from cpn.components.execution_services import ExecutionServices
from cpn.rpnh.agent_tasks import (
    AgentStage, TEXT_SCHEMA, agent_task_catalog, agent_task_registration,
    build_agent_task_module,
)
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.harness import Harness
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run


class MemorySocket:
    """Only the owner wake channel's byte operations; no client/network API."""

    def __init__(self, state, identity):
        self.state = state
        self.identity = identity
        self.peer = None
        self.received = bytearray()
        self.closed = False

    def fileno(self):
        return self.identity

    def bind(self, address):
        self.state.counts['memory_bind'] += 1
        # The original unix_socket_address points through an open directory
        # descriptor. This is only an inert ordinary-file identity marker.
        with Path(address).open('xb'):
            pass

    def setblocking(self, value):
        if value is not False:
            raise AssertionError('fixture owner transport must be nonblocking')
        self.state.counts['memory_setblocking'] += 1

    def listen(self):
        self.state.counts['memory_listen'] += 1

    def send(self, data):
        if self.closed or self.peer is None or self.peer.closed:
            raise BrokenPipeError('closed in-memory owner wake channel')
        self.state.counts['memory_send'] += 1
        self.state.counts['memory_sent_bytes'] += len(data)
        self.peer.received.extend(data)
        return len(data)

    def recv(self, size):
        if not self.received:
            raise BlockingIOError('empty in-memory owner wake channel')
        self.state.counts['memory_recv'] += 1
        data = bytes(self.received[:size])
        del self.received[:size]
        return data

    def accept(self):
        raise AssertionError('fixture has no owner socket clients')

    def close(self):
        if not self.closed:
            self.state.counts['memory_socket_close'] += 1
        self.closed = True


class MemorySelector:
    def __init__(self, state):
        self.state = state
        self.keys = {}
        self.closed = False

    def register(self, fileobj, events, data=None):
        if fileobj in self.keys:
            raise KeyError('in-memory selector already registered')
        key = selectors.SelectorKey(fileobj, fileobj.fileno(), events, data)
        self.keys[fileobj] = key
        self.state.counts['memory_register'] += 1
        return key

    def unregister(self, fileobj):
        return self.keys.pop(fileobj)

    def modify(self, fileobj, events, data=None):
        self.unregister(fileobj)
        return self.register(fileobj, events, data)

    def select(self, timeout=None):
        if self.closed:
            raise RuntimeError('in-memory selector closed')
        self.state.counts['memory_select'] += 1
        return [(key, selectors.EVENT_READ) for key in self.keys.values()
                if key.events & selectors.EVENT_READ
                and key.fileobj.received and not key.fileobj.closed]

    def close(self):
        self.state.counts['memory_selector_close'] += 1
        self.keys.clear()
        self.closed = True


class OwnerTestBoundaries:
    """Counts permitted synthetic boundaries and forbidden real attempts."""

    def __init__(self):
        self.counts = Counter()
        self.sockets = []

    def _socket(self):
        channel = MemorySocket(self, 1000000 + len(self.sockets))
        self.sockets.append(channel)
        return channel

    def socket(self, family, kind):
        if (family, kind) != (socket.AF_UNIX, socket.SOCK_STREAM):
            raise AssertionError('only synthetic owner AF_UNIX is supported')
        self.counts['memory_socket'] += 1
        return self._socket()

    def socketpair(self):
        self.counts['memory_socketpair'] += 1
        left, right = self._socket(), self._socket()
        left.peer, right.peer = right, left
        return left, right

    def selector(self):
        self.counts['memory_selector'] += 1
        return MemorySelector(self)

    def forbid(self, name):
        def forbidden(*args, **kwargs):
            self.counts['forbidden:' + name] += 1
            raise AssertionError('offline owner fixture forbids ' + name)
        return forbidden

    def inventory(self, *, environment, profile):
        from cpn.components.tool_executors import (
            _validated_execution_environment_inventory,
        )
        self.counts['synthetic_inventory_capture'] += 1
        # Complete only for this explicitly empty synthetic fixture universe;
        # this does not describe the executor's installed distributions.
        return _validated_execution_environment_inventory({
            'kind': 'execution_environment_inventory/v1',
            'environment_name': environment.name,
            'python_version': 'synthetic-offline-fixture',
            'inventory_complete': True,
            'installed_resources': [],
        }, environment)

    def assert_no_external_calls(self):
        assert not {key: count for key, count in self.counts.items()
                    if key.startswith('forbidden:') and count}, self.counts


def install_owner_test_boundaries(monkeypatch):
    """Install before creating an owner; never patch Registry or any gate."""
    import http.client
    import os
    import subprocess
    import urllib.request
    from cpn.rpnh import control_server
    from cpn.components import tool_executors
    from cpn.components.agent_loop import optional_host_bindings

    state = OwnerTestBoundaries()
    # Replace this module's transport namespaces, leaving the actual
    # OwnerEventLoop constructor and dispatch/close methods unmodified.
    monkeypatch.setattr(control_server, 'socket', SimpleNamespace(
        socket=state.socket, socketpair=state.socketpair,
        AF_UNIX=socket.AF_UNIX, SOCK_STREAM=socket.SOCK_STREAM))
    monkeypatch.setattr(control_server, 'selectors', SimpleNamespace(
        DefaultSelector=state.selector,
        EVENT_READ=selectors.EVENT_READ, EVENT_WRITE=selectors.EVENT_WRITE))
    monkeypatch.setattr(optional_host_bindings,
                        'capture_execution_environment_inventory',
                        state.inventory)
    monkeypatch.setattr(tool_executors, '_execute_numerical_worker',
                        state.forbid('numerical_worker'))
    for name in ('socket', 'socketpair', 'create_connection'):
        monkeypatch.setattr(socket, name, state.forbid('socket.' + name))
    monkeypatch.setattr(urllib.request, 'urlopen', state.forbid('urlopen'))
    for name in ('HTTPConnection', 'HTTPSConnection'):
        monkeypatch.setattr(http.client, name, state.forbid(name))
    for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, name, state.forbid('subprocess.' + name))
    for name in ('system', 'fork', 'forkpty', 'posix_spawn', 'posix_spawnp'):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, state.forbid('os.' + name))
    return state


@dataclass
class RegisteredAgentOwnerFixture:
    owner: object
    event_loop: OwnerEventLoop
    admitted: object
    execution: object
    services: ExecutionServices
    target: object
    policy: dict
    boundaries: OwnerTestBoundaries

    @property
    def executable(self):
        return self.admitted.executable

    @property
    def claimed_inputs(self):
        return Harness._claimed_inputs(self.execution)

    def prepare_dispatcher(self):
        """Prepare only. Never invoke the returned operation dispatcher."""
        return self.services.prepare_dispatcher(
            execution=self.execution, executable=self.executable,
            claimed_inputs=self.claimed_inputs, event_loop=self.event_loop)

    def gateway_call(self, name, *args, **kwargs):
        future = self.services.gateway.submit(name, *args, **kwargs)
        self.event_loop.dispatch_ready(timeout=0)
        if not future.done():
            raise AssertionError('in-memory owner pump did not execute request')
        return future.result()

    def close(self):
        self.event_loop.close()
        self.boundaries.assert_no_external_calls()


def _backend(policy, target, *, registered_host=False):
    from cpn.components.registered_host_llm import registered_host_execution_identity
    return {
        'schema_version': ('registered_host_execution_provenance/v1'
                           if registered_host else
                           'optional_agent_execution_provenance/v1'),
        'model': target.model_condition,
        'backend': policy['adapter_kind'],
        'timeout_seconds': policy['timeout_seconds'],
        'selection': (registered_host_execution_identity(policy)
                      if registered_host else policy),
        'transport_kind': policy['route_provenance'][0]['transport'],
        'response_protocol': 'llm_response_envelope/v1',
    }


def _started_owner(path, monkeypatch, *, target, policy, port,
                   registered_host, registered_material_context=None,
                   material_context_factory=None, backend=None,
                   configuration_sources=None, boundaries=None):
    if registered_material_context is not None and material_context_factory is not None:
        raise ValueError('supply context or an owner-local context factory, not both')
    boundaries = boundaries or install_owner_test_boundaries(monkeypatch)
    module = build_agent_task_module(
        (AgentStage('worker', 'Return one offline fixture result.'),),
        max_attempts_per_stage=1)
    transport = {
        'interaction_protocol_ref': 'llm_request_envelope/v1',
        'response_adapter_ref': 'llm_response_envelope/v1',
    }
    backend = backend or _backend(policy, target, registered_host=registered_host)
    if registered_host:
        # Original test Registration with unused executor and actual HOST ABI.
        from test_registered_host_llm import _registration
        from cpn.components.registered_host_llm import (
            EXECUTION_PROVENANCE_DOCUMENT, EXECUTION_PROVENANCE_SCHEMA,
            make_registered_llm_host_bindings,
        )
        registration = _registration()
        registration.register_schema(EXECUTION_PROVENANCE_SCHEMA,
                                     EXECUTION_PROVENANCE_DOCUMENT)
        host_bindings = make_registered_llm_host_bindings(
            target, provider_backend_config=backend,
            provider_backend_schema_ref=EXECUTION_PROVENANCE_SCHEMA,
            transport_contract=transport,
            prompt={'messages': [{'role': 'user', 'content': 'ready'}]},
            tool_catalog={'tools': []})
    else:
        from cpn.components.agent_loop.optional_host_bindings import (
            make_optional_agent_host_bindings,
        )
        registration = agent_task_registration()
        host_bindings = make_optional_agent_host_bindings(
            target, provider_backend_config=backend,
            transport_contract=transport)
    request = OwnerInput(TEXT_SCHEMA, canonical_json('Offline owner gate.'),
                         'Synthetic boundary owner-gate fixture')
    owner = start_run(
        module, registration, run_dir=path, task_input=request,
        entry_inputs={'request': request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']),
                                       ('rpnh/module_declaration/v1',), 1, 0, 1, 0),
        model_condition=target.model_condition,
        owner_statement='Offline owner gate; no native or model execution',
        command_id='offline-owner:start', catalog=agent_task_catalog(),
        host_execution_bindings=host_bindings,
        **({} if configuration_sources is None else
           {'configuration_sources': configuration_sources}))
    event_loop = OwnerEventLoop(owner, Path(path) / 'owner.sock')
    try:
        admitted = owner.admit('worker.run', logical_tau=0,
                               command_id='offline-owner:admit')
        if admitted is None:
            raise AssertionError('original owner did not admit fixture firing')
        execution = owner.start(admitted, command_id='offline-owner:firing')
        context = (material_context_factory(owner) if material_context_factory
                   is not None else registered_material_context)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=port,
            registered_material_context=context)
        return RegisteredAgentOwnerFixture(owner, event_loop, admitted,
            execution, services, target, dict(policy), boundaries)
    except BaseException:
        event_loop.close()
        raise


def start_optional_owner(path, monkeypatch, **kwargs):
    return _started_owner(path, monkeypatch, registered_host=False, **kwargs)


def start_registered_host_owner(path, monkeypatch, **kwargs):
    return _started_owner(path, monkeypatch, registered_host=True, **kwargs)


def prepare_neutral_attempt(case, *, key='offline-owner:neutral'):
    """Create original call/neutral attempt, stopping before submission gate."""
    service = case.services._optional_agent_service
    catalog = service.current_agent_tool_catalog_v1(case.execution)
    command = service.prepare_agent_loop_start_v1(
        case.execution, catalog, idempotency_key=key + ':start')
    loop = service.start_agent_loop_v1(command)
    waiting = service.mark_agent_loop_waiting_v1(
        loop, expected_revision=loop.revision, idempotency_key=key + ':waiting')
    initialization = service.prepare_agent_system_initialization_v1(
        case.execution, waiting, catalog)
    prepared = service.prepare_agent_turn_context_v1(
        case.execution, waiting, catalog)
    case.waiting_loop=waiting
    case.tool_catalog=catalog
    case.prepared_context=prepared
    return service.prepare_agent_llm_turn_v1(
        case.execution, waiting, catalog, initialization,
        idempotency_key=key + ':prepare', prepared_context=prepared).attempt


def registered_host_request():
    from test_registered_host_llm import _host_request
    return _host_request()


def start_bound_optional_owner(material, monkeypatch, *, port, prompt_override=None,
                               max_attempts_override=None, instruction_override=None,
                               global_cap_override=None):
    """Same sealed Spec with existing explicit D0-only native-evidence seam.

    This is not a real issuer, peer, receipt, reservation, worker, or native
    transport. All origin/net/admit/Start validators are the original product.
    """
    from dataclasses import replace
    import json
    import cpn.rpnh.run as run
    from cpn.rpnh.agent_tasks import AgentTaskSpec
    from cpn.rpnh.bound_child_lowering import with_bound_origin, ORIGIN_SYMBOL
    from cpn.rpnh.registry import parent_child as h7, parent_bound as bound
    from cpn.rpnh.registry.publication import _ref_payload
    from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
    from cpn.components.registered_material_checks import RegisteredMaterialContext
    from registered_agent_material_fixtures import public_selection
    from parent_child_fixtures import evidence, phase
    boundaries=install_owner_test_boundaries(monkeypatch)
    parent=material.owner
    if not hasattr(material,'d0_acceptance'):
        dispatch=phase(parent,h7.PARENT_KINDS[1],material.intent,{'bundle_digest':'a'*64})
        worker=phase(parent,h7.PARENT_KINDS[2],dispatch,{'worker':{'uid':1000,'pid':12345,'start_ticks':100,'boot_id':'offline-only'}})
        intent_body=parent._core.get_version(material.intent.version_id).metadata
        accepted=phase(parent,h7.PARENT_KINDS[3],worker,{'request_digest':h7.digest(h7._json(h7.bootstrap_request_material(intent_body,_ref_payload(dispatch))))})
        material.d0_acceptance=accepted
    accepted=material.d0_acceptance
    body=parent._core.get_version(accepted.version_id).metadata
    with parent._core.event_store.connect() as db:
        row=db.execute('SELECT transaction_id FROM objects WHERE version_id=?',(str(accepted.version_id),)).fetchone()
        fact=db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='parent_child_recorded/v1'",(row[0],)).fetchone()
        committed=db.execute("SELECT ordinal FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",(row[0],)).fetchone()
    external={'parent_source_id':body['parent']['source_id'],'acceptance_json':h7._json(body).decode(),
        'intent_json':h7._json(parent._core.get_version(material.intent.version_id).metadata).decode(),
        'acceptance_transaction_id':row[0],'acceptance_event_id':fact[0],'acceptance_commit_ordinal':committed[0],
        'initial_declaration_digest':material.prepared.materials['initial_declaration_digest']}
    target=material.prepared.materials['normalized_request']['target']
    spec=AgentTaskSpec.from_worker_document(material.request['definition'],document_root=Path(target['document_root']))
    if instruction_override is not None: spec=replace(spec,stages=(AgentStage(spec.stages[0].stage_id,instruction_override),))
    if max_attempts_override is not None: spec=replace(spec,max_attempts_per_stage=max_attempts_override)
    module=with_bound_origin(build_agent_task_module(spec.stages,max_attempts_per_stage=spec.max_attempts_per_stage))
    registration=agent_task_registration()
    selected=public_selection(material)
    policy=selected.as_registry_policy()
    task=OwnerInput(TEXT_SCHEMA,canonical_json(spec.prompt if prompt_override is None else prompt_override),'Same sealed AgentTask prompt')
    original_boot=run._bootstrap_identity;original_publish=run._publish_private_system
    def bootstrap(core,manifest,**kwargs):
        boundaries.counts['d0_native_bootstrap_evidence']+=1
        manifest=replace(manifest,protocol_versions=(*manifest.protocol_versions,h7.BOUND_PROTOCOL))
        with h7._native_boundary(evidence(core.event_store,'bound_bootstrap',external),core.event_store,'bound_bootstrap',external):
            return original_boot(core,manifest,_parent_bound_acceptance=external,**kwargs)
    def publish(core,task_ref,command,**kwargs):
        if command.content_schema_ref!=h7.CAPABILITY_SCHEMA: return original_publish(core,task_ref,command,**kwargs)
        boundaries.counts['d0_native_origin_evidence']+=1
        from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
        RegistryRegistrationGateway(core,task_ref,command.origin.bootstrap_command_ref).bind_source_identity(source_id='child-source',command_id='child:source')
        marker=json.loads(core.event_store.object_rows_by_type(h7.BOUND_KINDS[0])[0]['metadata_json'])
        return bound.close_bound_origin(core,evidence=evidence(core.event_store,'bound_origin',marker))
    placeholder={'schema_version':h7.CAPABILITY_SCHEMA,'origin_ref':_ref_payload(accepted),'marker_ref':_ref_payload(accepted),
        'child_run_ref':_ref_payload(parent.identity.run_ref),'child_task_ref':_ref_payload(parent.identity.task_ref),
        'parent_acceptance':external,'initial_writer_epoch':1,'initial_declaration_digest':external['initial_declaration_digest']}
    buckets=module.to_dict()['budget_buckets']
    cap=None if any(v['max_attempts'] is None for v in buckets) else sum(v['max_attempts'] for v in buckets)
    if global_cap_override is not None: cap=global_cap_override
    bindings=make_optional_agent_host_bindings(selected.input_target,workspace_policy=selected.runtime_policy.workspace,
        provider_backend_config=_backend(policy,selected.input_target),transport_contract={'interaction_protocol_ref':'llm_request_envelope/v1','response_adapter_ref':'llm_response_envelope/v1'})
    with monkeypatch.context() as patch:
        patch.setattr(run,'_bootstrap_identity',bootstrap)
        patch.setattr(run,'_publish_private_system',publish)
        owner=start_run(module,registration,run_dir=spec.run_dir,task_input=task,entry_inputs={'request':task},
            resource_inputs={ORIGIN_SYMBOL:OwnerInput(h7.CAPABILITY_SCHEMA,canonical_json(placeholder),'Explicit D0-only origin injection')},
            budgets=ModuleBudgetDeclaration(tuple(buckets),('rpnh/module_declaration/v1',),cap,0,cap,0),
            model_condition=selected.input_target.model_condition,owner_statement=spec.owner_statement,command_id='bound-agent:fresh',
            catalog=agent_task_catalog(registered_public=True),host_execution_bindings=bindings)
    event_loop=OwnerEventLoop(owner,spec.owner_socket_path)
    try:
        admitted=owner.admit(spec.stages[0].stage_id+'.run',logical_tau=0,command_id='bound-agent:admit')
        if admitted is None: raise AssertionError('same bound Spec did not admit')
        execution=owner.start(admitted,command_id='bound-agent:start')
        context=RegisteredMaterialContext(parent._core,material.inventory.resource_ref,material.inventory.root['public_material_digest'],owner._core)
        services=ExecutionServices(owner=owner,event_loop=event_loop,llm_input_port=port,registered_material_context=context)
        return RegisteredAgentOwnerFixture(owner,event_loop,admitted,execution,services,selected.input_target,policy,boundaries)
    except BaseException:
        event_loop.close();raise
