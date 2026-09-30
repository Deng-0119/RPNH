"""DSH driver on the existing RunOwner/Harness and MainThreadRegistry.

Only an admitted operation may ask the DSH capability host for an observation.
The observation is a candidate until existing products/Success settlement.
The offline conformance profile allows one adapter call per execution, no retry.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
import json
import re
from pathlib import Path
from typing import Callable, Mapping
from jsonschema import Draft7Validator
from cpn.components.execution_services import ExecutionServices
from cpn.components.registered_host_llm import (
    EXECUTION_PROVENANCE_DOCUMENT,
    EXECUTION_PROVENANCE_SCHEMA,
    HOST_PROTOCOL,
    make_registered_llm_host_bindings,
    registered_host_execution_route,
    registered_host_llm_schema_data,
)
from cpn.llm_adapters import (
    LLMExecutionSelection,
    build_llm_input_port,
    load_llm_execution_selection,
)
from cpn.plugins import (
    ManagedPluginInvocationFailed,
    ManagedPluginInvocationService,
    ManagedPluginToolAdapter,
    ManagedPluginToolCatalog,
    build_managed_plugin_tool_catalog,
    load_catalog,
)
from cpn.plugins.api import PluginError, json_copy, validate
from cpn.plugins.catalog import read_config
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.task_control import owner_socket_path
from cpn.rpnh.harness import Harness, OperationDispatch, OperationDisposition
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (ArcDeclaration, ColourExpression, InputVerdictGuard,
    PNFragment, PlaceDeclaration, PortBinding, PortDeclaration, TransitionDeclaration)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry, _parse_ref
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.resource_access import ResourceReadContract
from cpn.rpnh.run import OwnerInput, start_run, resume_run

REVISION = 'ddefc45fbc7f8e46dd73185e68295696d1297887'
PROTOCOL = 'rpnh/dsh/v1'
ENVELOPE = 'application/rpnh_dsh_envelope/v1'
ENVELOPE_V2 = 'application/rpnh_dsh_envelope/v2'
CONFIG = 'application/rpnh_dsh_config/v1'
CONFIG_V2 = 'application/rpnh_dsh_operation_config/v2'
COMPONENT = 'rpnh/dsh-driver/v1'
EXECUTOR = 'rpnh/dsh-capability/v1'
CONFIGURED_EXECUTOR = 'rpnh/dsh-configured-capability/v1'
TERMINAL = 'rpnh/dsh-terminal/v1'
MAX_BYTES = 2 * 1024 * 1024
MAX_MANAGED_TOOL_RESULT_BYTES = 64 * 1024
STAGES = ('inspect', 'accept', 'reject_request', 'model', 'inspect_tool', 'tool', 'reject_tool', 'finalize')

ENVELOPE_SCHEMA = {
    '$id': ENVELOPE,
    '$schema': 'http://json-schema.org/draft-07/schema#',
    'type': 'object',
    'required': ['session_id', 'request_id', 'route', 'messages', 'policy', 'data'],
    'properties': {
        'session_id': {'type': 'string'},
        'request_id': {'type': 'string'},
        'route': {'type': 'object'},
        'messages': {'type': 'array'},
        'policy': {'type': 'object'},
        'data': {'type': 'array'},
    },
}
_ENVELOPE_VALIDATOR = Draft7Validator(ENVELOPE_SCHEMA)

ENVELOPE_V2_SCHEMA = {
    '$id': ENVELOPE_V2,
    '$schema': 'http://json-schema.org/draft-07/schema#',
    'type': 'object',
    'required': [
        'session_id', 'request_id', 'route', 'messages', 'policy', 'data',
        'execution_profile',
    ],
    'properties': {
        **ENVELOPE_SCHEMA['properties'],
        'execution_profile': {
            'type': 'object',
            'required': [
                'schema_version', 'profile', 'selection_id', 'provider',
                'provider_display_name', 'model_condition', 'adapter_kind',
                'transport_kind', 'timeout_seconds', 'max_output_tokens',
                'max_response_bytes',
            ],
            'properties': {
                'schema_version': {
                    'enum': [
                        'rpnh/dsh_execution_profile/v2',
                        'rpnh/dsh_execution_profile/v3']},
                'profile': {'type': 'string', 'minLength': 1},
                'selection_id': {'type': 'string', 'minLength': 1},
                'provider': {'type': 'string', 'minLength': 1},
                'provider_display_name': {'type': 'string', 'minLength': 1},
                'model_condition': {'type': 'string', 'minLength': 1},
                'adapter_kind': {
                    'enum': ['external_provider', 'local_process']},
                'transport_kind': {'type': 'string', 'minLength': 1},
                'timeout_seconds': {'type': 'integer', 'minimum': 1},
                'max_output_tokens': {'type': 'integer', 'minimum': 1},
                'max_response_bytes': {'type': 'integer', 'minimum': 1},
                'reasoning_effort': {
                    'type': ['string', 'null']},
                'supported_reasoning_efforts': {
                    'type': 'array', 'uniqueItems': True,
                    'items': {'type': 'string', 'minLength': 1}},
                'default_reasoning_effort': {
                    'type': ['string', 'null']},
            },
            'additionalProperties': False,
        },
    },
    'additionalProperties': True,
}
_ENVELOPE_V2_VALIDATOR = Draft7Validator(ENVELOPE_V2_SCHEMA)


def _validate_turn_request(
        request, *, session_id, execution_profile=None,
        managed_tool_names=()):
    if not isinstance(request, Mapping):
        raise ValueError('DSH turn request must be an object')
    raw = canonical_json(request)
    validator = (
        _ENVELOPE_VALIDATOR
        if execution_profile is None else _ENVELOPE_V2_VALIDATOR)
    validator.validate(request)
    if len(raw) > MAX_BYTES or request.get('session_id') != session_id:
        raise ValueError('request size or session identity differs')
    if request.get('upstream_revision') != REVISION:
        raise ValueError('DSH capability revision differs')
    if not isinstance(request.get('request_id'), str) or not request['request_id']:
        raise ValueError('missing request identity')
    if execution_profile is None:
        if request.get('route') != {
                'provider': 'rpnh-offline', 'model': 'deterministic-v1'}:
            raise ValueError(
                'offline mode supports only the exact offline model '
                '(deterministic-v1); no fallback')
    else:
        if request.get('execution_profile') != execution_profile:
            raise ValueError(
                'DSH request execution profile differs from the selected '
                'shared provider route')
        if request.get('route') != {
                'provider': execution_profile['provider'],
                'model': execution_profile['model_condition']}:
            raise ValueError(
                'DSH route differs from the selected exact provider/model')
    if (not isinstance(request.get('data'), list)
            or any(type(item) not in (float, int) for item in request['data'])):
        raise ValueError('data must be an explicit numeric snapshot')
    messages = request.get('messages')
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(message, Mapping)
                   for message in messages)):
        raise ValueError('new input must be user/context messages')
    for message in messages:
        content = message.get('content')
        if (message.get('role') != 'user'
                or not isinstance(message.get('id'), str)
                or not message['id']
                or not isinstance(message.get('source'), Mapping)
                or not isinstance(content, list) or not content
                or any(not isinstance(block, Mapping)
                       or not isinstance(block.get('type'), str)
                       for block in content)):
            raise ValueError('new input must be user/context messages')
    policy = request['policy']
    tools = policy.get('tools')
    if (type(policy.get('allow_request')) is not bool
            or not isinstance(tools, list)
            or any(not isinstance(tool, str) or not tool for tool in tools)):
        raise ValueError('DSH policy must declare allow_request and tool names')
    if execution_profile is not None and tools != list(managed_tool_names):
        raise ValueError(
            'configured DSH tools differ from the explicit managed plugin '
            'allowlist; provider configuration never grants tools implicitly')
    if ('header' in request
            and not isinstance(request.get('header'), Mapping)):
        raise ValueError('DSH session header must be an object')
    return raw


def _public_execution_profile(
        selection: LLMExecutionSelection, supplied: Mapping[str, object],
) -> dict[str, object]:
    """Validate launcher metadata against the shared selection authority."""
    if not isinstance(supplied, Mapping):
        raise ValueError('configured DSH requires one public execution profile')
    policy = selection.as_registry_policy()
    routes = policy.get('route_provenance')
    if (not isinstance(routes, list) or len(routes) != 1
            or not isinstance(routes[0], Mapping)):
        raise ValueError('configured DSH selection lacks one exact route')
    schema_version = supplied.get('schema_version')
    expected = {
        'schema_version': schema_version,
        'profile': supplied.get('profile'),
        'selection_id': supplied.get('selection_id'),
        'provider': supplied.get('provider'),
        'provider_display_name': supplied.get('provider_display_name'),
        'model_condition': selection.input_target.model_condition,
        'adapter_kind': selection.adapter_kind,
        'transport_kind': routes[0].get('transport'),
        'timeout_seconds': selection.timeout_seconds,
        'max_output_tokens': selection.input_target.max_output_tokens,
        'max_response_bytes': selection.input_target.max_response_bytes,
    }
    if schema_version == 'rpnh/dsh_execution_profile/v3':
        expected.update({
            'reasoning_effort': selection.reasoning_effort,
            'supported_reasoning_efforts': list(
                selection.supported_reasoning_efforts),
            'default_reasoning_effort': selection.default_reasoning_effort,
        })
    document = json.loads(canonical_json(dict(supplied)))
    if (schema_version not in {
                'rpnh/dsh_execution_profile/v2',
                'rpnh/dsh_execution_profile/v3'}
            or (schema_version == 'rpnh/dsh_execution_profile/v2'
                and selection.reasoning_effort is not None)
            or (selection.adapter_kind == 'external_provider'
                and document.get('provider') != routes[0].get('provider'))
            or document != expected
            or any(not isinstance(document.get(field), str)
                   or not document[field]
                   for field in (
                       'profile', 'selection_id', 'provider',
                       'provider_display_name'))):
        raise ValueError(
            'launcher execution profile differs from the shared selection')
    return document


def _configured_response_frame_upper_bound(
        ticket: Mapping[str, object], arguments: Mapping[str, object],
        max_response_bytes: int,
) -> int:
    """Bound the exact owner-to-DSH response frame before provider dispatch."""
    frame = {
        'protocol': PROTOCOL,
        'kind': 'effect',
        'id': '0' * 32,
        'ticket': dict(ticket),
        'arguments': {**arguments, 'provider_response': {}},
    }
    empty_response_bytes = len(json.dumps(
        frame, allow_nan=False, separators=(',', ':')).encode())
    return empty_response_bytes - len(b'{}') + max_response_bytes


def _lower(config, context):
    schemas = {port.schema for port in context.ports}
    if len(schemas) != 1 or not schemas.issubset({ENVELOPE, ENVELOPE_V2}):
        raise ValueError('DSH component ports require one envelope revision')
    envelope_schema = next(iter(schemas))
    places = [('request', ()), ('decision', ('allow', 'deny')), ('ready', ()),
              ('response', ('tool', 'answer')), ('tool_decision', ('allow', 'deny')),
              ('done', ()), ('denied_request', ()), ('denied_tool', ())]
    routes = {
        'inspect': ('request', [('decision', 'allow'), ('decision', 'deny')]),
        'accept': ('decision', [('ready', 'complete')]),
        'reject_request': ('decision', [('denied_request', 'complete')]),
        'model': ('ready', [('response', 'tool'), ('response', 'answer')]),
        'inspect_tool': ('response', [('tool_decision', 'allow'), ('tool_decision', 'deny')]),
        'tool': ('tool_decision', [('ready', 'complete')]),
        'reject_tool': ('tool_decision', [('denied_tool', 'complete')]),
        'finalize': ('response', [('done', 'complete')]),
    }
    guards = {'accept': ('decision', 'allow'), 'reject_request': ('decision', 'deny'),
              'inspect_tool': ('response', 'tool'), 'tool': ('tool_decision', 'allow'),
              'reject_tool': ('tool_decision', 'deny'), 'finalize': ('response', 'answer')}
    arcs = []
    for stage, (source, targets) in routes.items():
        arcs.append(ArcDeclaration(source, stage, 'input'))
        colour = {'colour_expression': ColourExpression(kind='input_colour', source_place=source)} if source in {'decision', 'response', 'tool_decision'} else {}
        arcs.append(ArcDeclaration(source, stage, 'output', mode='produce', outcome='interrupted', emit='forward', forward_source=source, **colour))
        for target, outcome in targets:
            kw = {'colour_expression': ColourExpression(value=outcome)} if target in {'decision', 'response', 'tool_decision'} else {}
            arcs.append(ArcDeclaration(target, stage, 'output', mode='produce', outcome=outcome, **kw))
    internal = tuple(PortDeclaration(f'{name}_{direction}', direction, envelope_schema)
                     for name, _ in places if name not in {'request', 'done', 'denied_request', 'denied_tool'}
                     for direction in ('input', 'output')) + (
                         PortDeclaration(
                             'request_return', 'output', envelope_schema),)
    return PNFragment(
        places=tuple(PlaceDeclaration(name, envelope_schema, capacity=1, colours=colours) for name, colours in places),
        transitions=tuple(TransitionDeclaration(s, s, input_verdicts=(InputVerdictGuard(*guards[s]),) if s in guards else ()) for s in STAGES),
        arcs=tuple(arcs), operations=context.operations,
        ports=(PortBinding('request', 'request'), PortBinding('done', 'done'), PortBinding('denied_request', 'denied_request'), PortBinding('denied_tool', 'denied_tool')),
        internal_ports=internal,
        internal_bindings=tuple(PortBinding(p.name, ('request' if p.name == 'request_return' else p.name.rsplit('_', 1)[0])) for p in internal))


def declaration(*, configured: bool = False,
                managed_tools: ManagedPluginToolCatalog | None = None):
    if managed_tools is not None and not configured:
        raise ValueError('managed provider tools require configured DSH')
    managed_tools = managed_tools or ManagedPluginToolCatalog(
        load_catalog(), ())
    managed_registration_keys = tuple(
        tool.registration_key for tool in managed_tools.tools)
    binding = {'bucket_id': 'dsh', 'budget_scope': 'module', 'finalization_scope': None}
    routes = {
        'inspect': ('request', 'decision_output', ('allow', 'deny')),
        'accept': ('decision_input', 'ready_output', ('complete',)),
        'reject_request': ('decision_input', 'denied_request', ('complete',)),
        'model': ('ready_input', 'response_output', ('tool', 'answer')),
        'inspect_tool': ('response_input', 'tool_decision_output', ('allow', 'deny')),
        'tool': ('tool_decision_input', 'ready_output', ('complete',)),
        'reject_tool': ('tool_decision_input', 'denied_tool', ('complete',)),
        'finalize': ('response_input', 'done', ('complete',)),
    }
    operations = []
    for name, (source, target, outcomes) in routes.items():
        executor = CONFIGURED_EXECUTOR if configured and name == 'model' else EXECUTOR
        operation_config = (
            {'provider_attempt_limit': 1, 'resource_bounds': {
                'max_llm_attempts': 1, 'max_tool_turns': 0}}
            if configured and name == 'model' else {})
        operations.append({'name': name, 'executor': executor, 'inputs': [source],
            'outputs': [target, 'request_return' if source == 'request' else source.rsplit('_',1)[0] + '_output'],
            'tools': (list(managed_registration_keys)
                      if configured and name == 'tool' else []),
            'config': operation_config,
            'request_port': source if configured and name == 'model' else None,
            'budget_binding': binding,
            'outcomes': [{'name': o, 'products': [{'port': target}]} for o in outcomes] + [{'name': 'interrupted', 'products': []}]})
    def terminal(stage, port):
        return {'key': TERMINAL, 'source': {'component': 'dsh', 'port': port}, 'operation': stage,
                'outcome': 'complete', 'config': {'run_outcome': 'complete'}}
    envelope = ENVELOPE_V2 if configured else ENVELOPE
    return ModuleDeclaration.from_dict({'schema_version': 'rpnh/module_declaration/v1', 'name': 'DSHDriver',
        'components': [{'name': 'dsh', 'key': COMPONENT, 'config_schema': CONFIG, 'config': {},
                        'ports': [{'name': n, 'direction': d, 'schema': envelope} for n,d in [('request','input'),('done','output'),('denied_request','output'),('denied_tool','output')]],
                        'operations': operations}], 'links': [],
        'entry': {'request': {'component': 'dsh', 'port': 'request'}},
        'exit': {n: {'component': 'dsh', 'port': n} for n in ('done','denied_request','denied_tool')},
        'terminal': terminal('finalize', 'done'),
        'terminal_alternatives': [terminal('reject_request', 'denied_request'), terminal('reject_tool', 'denied_tool')],
        'required_schemas': [
            envelope, CONFIG, *((CONFIG_V2,) if configured else ())],
        'budgets': {},
        'budget_buckets': [{**binding, 'max_attempts': 48}]})


def registration(executor=None,
                 managed_tools: ManagedPluginToolCatalog | None = None):
    executor = dict if executor is None else executor
    reg = Registration()
    reg.register_schema(ENVELOPE, ENVELOPE_SCHEMA)
    reg.register_schema(ENVELOPE_V2, ENVELOPE_V2_SCHEMA)
    reg.register_schema(
        EXECUTION_PROVENANCE_SCHEMA, EXECUTION_PROVENANCE_DOCUMENT)
    reg.register_schema(CONFIG, {'$id': CONFIG, '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object', 'additionalProperties': False, 'properties': {}})
    reg.register_schema(CONFIG_V2, {
        '$id': CONFIG_V2,
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'provider_attempt_limit': {'const': 1},
            'resource_bounds': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'max_llm_attempts': {'const': 1},
                    'max_tool_turns': {'const': 0},
                },
                'required': ['max_llm_attempts', 'max_tool_turns'],
            },
        },
        'required': ['provider_attempt_limit', 'resource_bounds'],
    })
    reg.register_component(COMPONENT, _lower, identity={'implementation_id': 'rpnh.dsh.graph', 'revision': 'v1'}, contracts={'config_schema': CONFIG})
    reg.register_executor(EXECUTOR, executor, identity={'implementation_id': 'rpnh.dsh.dispatch', 'revision': REVISION},
        contracts={'transport': 'deterministic', 'capability_transport': 'dsh-jsonl-v1', 'input_ports': None, 'output_ports': None, 'config_schema': CONFIG})
    reg.register_executor(CONFIGURED_EXECUTOR, executor,
        identity={
            'implementation_id': 'rpnh.dsh.configured_dispatch',
            'revision': REVISION,
        },
        contracts={
            'transport': 'llm',
            'capability_transport': 'dsh-jsonl-v1',
            'input_ports': None,
            'output_ports': None,
            'config_schema': CONFIG_V2,
            'host_protocols': [HOST_PROTOCOL],
            'resource_read_contracts': [
                ResourceReadContract(
                    metadata_only=False,
                    context_origins=('petri_operation',),
                    origin_kinds=('provider_request',),
                    require_producer_invocation=True,
                    require_provenance_binding=True,
                ).to_dict(),
            ],
            'provider_request_schema': 'runtime/llm_request_envelope/v1',
        })
    reg.register_tool(TERMINAL, dict, identity={'implementation_id': 'rpnh.dsh.terminal', 'revision': 'v1'},
        contracts={'binding_protocol': 'rpnh/module_terminal/v1'})
    if managed_tools is not None:
        ManagedPluginToolAdapter(managed_tools).register(reg)
    return reg


def _identity(ref):
    return {f.name: str(getattr(ref, f.name)) for f in fields(ref)}


_OUTPUT_PLACES = {
    'decision_output': 'dsh.decision',
    'ready_output': 'dsh.ready',
    'response_output': 'dsh.response',
    'tool_decision_output': 'dsh.tool_decision',
    'request_return': 'dsh.request',
    'done': 'dsh.done',
    'denied_request': 'dsh.denied_request',
    'denied_tool': 'dsh.denied_tool',
}


class _DshExecutor:
    """Thin DSH HOST adapter over generic operation/provider services."""

    def __init__(self, backend):
        self.backend = backend

    def __call__(self, *, execution, gateway, resources, host_context):
        del resources
        value = json.loads(execution.operation.inputs[0].artifact.payload)
        registered_llm = getattr(host_context, 'registered_llm', None)
        outcome, output_port, output = self.backend._invoke(
            execution, value, gateway=gateway,
            registered_llm=registered_llm)
        try:
            place = _OUTPUT_PLACES[output_port]
        except KeyError as exc:
            raise ValueError('DSH executor selected an unknown output') from exc
        bindings = tuple(
            item for item in
            execution.operation.operation_binding.output_port_bindings
            if item.place == place)
        if len(bindings) != 1:
            raise ValueError('DSH output lacks one exact Petri binding')
        binding = bindings[0]
        ports = tuple(
            item for item in execution.operation.spec.output_ports
            if item.port_id == binding.port_id)
        if len(ports) != 1:
            raise ValueError('DSH output lacks one exact operation port')
        context = execution.operation.canonical.context
        ref = gateway.publish_bytes(context, PublishResource(
            origin=PetriOutputOrigin(
                binding.output_binding_ref, context.activation_ref),
            payload=canonical_json(output),
            media_type='application/json',
            content_schema_ref=ports[0].content_schema_id,
            summary='DSH registered operation product',
            lifetime_ref=context.invocation_ref,
            descriptors={
                'output_outcome_id': outcome,
                'output_port_id': binding.port_id,
                'place': binding.place,
            },
            idempotency_key=(
                'dsh:'
                + str(execution.operation_execution_lease_ref.version_id)
                + ':product'),
        ))
        artifact = gateway.verify_resource(
            execution.operation.canonical, ref)
        return OperationExecutionResult(
            outputs=(artifact,), selected_outcome_id=outcome)


class DshBackend:
    """One session owner; its reader thread may request stop but never writes."""
    def __init__(
            self, root: Path, session_id: str,
            effect: Callable[[dict], dict], *, create=False,
            execution_config_path: Path | None = None,
            execution_profile: Mapping[str, object] | None = None,
            offline: bool = True,
            selection: LLMExecutionSelection | None = None,
            plugin_config_path: Path | None = None,
            managed_tools: Mapping[str, str] | None = None,
            input_port_factory=build_llm_input_port,
    ):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id):
            raise ValueError('invalid session identity')
        if (execution_config_path is not None
                and not isinstance(execution_config_path, Path)):
            raise TypeError('execution_config_path requires pathlib.Path')
        if selection is not None and not isinstance(
                selection, LLMExecutionSelection):
            raise TypeError('selection requires LLMExecutionSelection')
        if execution_config_path is not None and selection is not None:
            raise ValueError('configured DSH selection has two authorities')
        if (execution_config_path is not None or selection is not None) and offline:
            raise ValueError('configured DSH cannot also use offline mode')
        if not callable(input_port_factory):
            raise TypeError('input port factory must be callable')
        self.root, self.session_id, self.effect = root / session_id, session_id, effect
        self.execution_config_path = (
            execution_config_path.resolve()
            if execution_config_path is not None else None)
        self.selection = (
            load_llm_execution_selection(self.execution_config_path)
            if self.execution_config_path is not None else selection)
        self.configured = self.selection is not None
        self.offline = offline
        if plugin_config_path is not None:
            if not isinstance(plugin_config_path, Path):
                raise TypeError('plugin_config_path requires pathlib.Path')
            if not plugin_config_path.is_absolute():
                raise ValueError('plugin_config_path must be absolute')
        if (plugin_config_path is None) != (managed_tools is None):
            raise ValueError(
                'managed tools require one explicit plugin config and allowlist')
        if managed_tools is not None:
            if (not isinstance(managed_tools, Mapping) or not managed_tools
                    or any(not isinstance(name, str)
                           or not isinstance(selector, str)
                           for name, selector in managed_tools.items())):
                raise ValueError('managed tool allowlist must be a nonempty mapping')
            if not self.configured:
                raise ValueError('managed provider tools require configured DSH')
            selected_plugins = load_catalog(read_config(plugin_config_path))
            self.managed_tools = build_managed_plugin_tool_catalog(
                selected_plugins, managed_tools)
        else:
            self.managed_tools = build_managed_plugin_tool_catalog(
                load_catalog(), ())
        for tool in self.managed_tools.tools:
            _plugin, operation = self.managed_tools.binding(tool.name)
            if operation.max_result_bytes > MAX_MANAGED_TOOL_RESULT_BYTES:
                raise ValueError(
                    'managed DSH tool result limit exceeds 64 KiB')
        self.managed_tool_names = tuple(
            tool.name for tool in self.managed_tools.tools)
        self.execution_profile = None
        if self.configured:
            if execution_profile is not None:
                self.execution_profile = _public_execution_profile(
                    self.selection, execution_profile)
        elif execution_profile is not None:
            raise ValueError('offline DSH does not accept an execution profile')
        self.input_port_factory = input_port_factory
        self.root.mkdir(parents=True, exist_ok=True)
        self.core = _RegistryCore(self.root / 'main', create=create)
        self.thread = MainThreadRegistry(
            self.core, session_root=self.root,
            initialize_path_base=(
                MainThreadRegistry.REGISTRY_ROOT_PATH_BASE if create else None),
        )
        if create:
            self.thread.create_thread(idempotency_key='dsh-session')
        self.runner = None
        self.managed_tool_service = None
        self.cancelled = False
        self.before_dispatch = lambda owner, execution: None
        self.before_settlement = lambda owner: None

    def cancel(self):
        self.cancelled = True
        if self.runner is not None:
            self.runner.request_owner_stop()

    def history(self):
        state = self.thread.recover_thread()
        active = state['active_turn_ref']
        state['active'] = self.thread.observe_turn_execution(turn_ref=_parse_ref(active)) if active else None
        state['session_id'] = self.session_id
        return state

    def turn(self, request: dict):
        if not self.offline and not self.configured:
            raise ValueError(
                'history-only DSH owner cannot accept or resume a request')
        if self.configured and self.execution_profile is None:
            self.execution_profile = _public_execution_profile(
                self.selection, request.get('execution_profile'))
        raw = _validate_turn_request(
            request, session_id=self.session_id,
            execution_profile=self.execution_profile,
            managed_tool_names=self.managed_tool_names)
        state = self.history()
        for item in state['committed_history']:
            if item['user_input']['request_id'] == request['request_id']:
                if item['user_input'] != request:
                    raise ValueError('request identity reused with different material')
                return {'status': 'terminal', 'answer': item['answer'], 'replayed': True}
        if state['active']:
            raise ValueError('session has an active request; query and explicitly resume it')
        if self.configured:
            self._validate_configured_frame_budget(request, state)
        self.cancelled = False
        advance = self.thread.accept_turn(thread_ref=_parse_ref(state['thread_ref']), user_input=request,
                                         idempotency_key=request['request_id'])
        attachment = self.thread.attach_attempt(thread_ref=advance.thread_ref, turn_ref=advance.turn_ref,
                                               idempotency_key=request['request_id'])
        module = declaration(
            configured=self.configured,
            managed_tools=(self.managed_tools if self.configured else None))
        history_messages = state['committed_history'][-1]['answer']['messages'] if state['committed_history'] else []
        envelope = {**request, 'messages': [*history_messages, *request['messages']], 'calls': [], 'step': 0}
        envelope_schema = ENVELOPE_V2 if self.configured else ENVELOPE
        inp = OwnerInput(envelope_schema, canonical_json(envelope), 'DSH exact request and explicit immutable data snapshot')
        host_bindings = self._host_bindings()
        owner = start_run(
            module, registration(
                _DshExecutor(self),
                self.managed_tools if self.configured else None),
            run_dir=attachment.attempt_path, task_input=inp,
            entry_inputs={'request': inp}, budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']),
                ('rpnh/module_declaration/v1',), 48, 0, 48, 0),
            model_condition=self._model_condition,
            owner_statement=(
                'Managed DSH profile over the shared registered provider'
                if self.configured else
                'Managed DSH offline profile: declared pure read/compute capabilities only'),
            command_id='dsh:fresh', host_execution_bindings=host_bindings,
            catalog=self._catalog())
        return self._execute(owner, attachment.thread_ref, attachment.turn_ref)

    def resume(self):
        if not self.offline and not self.configured:
            raise ValueError(
                'history-only DSH owner cannot accept or resume a request')
        state = self.history()
        active = state['active']
        if active is None:
            completed_idle = (
                bool(state['committed_history'])
                and state['latest_turn_ref']
                == state['latest_committed_turn_ref'])
            if completed_idle:
                return {'status': 'idle', 'history': state}
            return {
                'status': 'failed', 'answer': None,
                'failure_kind': 'no_terminal_current_turn',
                'history': state,
            }
        if active['state'] == 'terminal':
            return self._commit(_parse_ref(state['thread_ref']), _parse_ref(state['active_turn_ref']), active['output'])
        path = self.thread.resolve_child_path(
            active['attempt_relative_path'])
        request = active['user_input']
        if self.configured:
            if self.execution_profile is None:
                self.execution_profile = _public_execution_profile(
                    self.selection, request.get('execution_profile'))
            _validate_turn_request(
                request, session_id=self.session_id,
                execution_profile=self.execution_profile,
                managed_tool_names=self.managed_tool_names)
            self._validate_configured_frame_budget(request, state)
        else:
            _validate_turn_request(request, session_id=self.session_id)
        self.cancelled = False
        owner = resume_run(
            registration(
                _DshExecutor(self),
                self.managed_tools if self.configured else None), run_dir=path,
            model_condition=self._model_condition,
            host_execution_bindings=self._host_bindings(),
            catalog=self._catalog())
        return self._execute(owner, _parse_ref(state['thread_ref']), _parse_ref(state['active_turn_ref']))

    @property
    def _model_condition(self):
        return (
            self.selection.input_target.model_condition
            if self.configured else 'rpnh-offline/deterministic-v1')

    def _host_bindings(self):
        if not self.configured:
            return None
        return make_registered_llm_host_bindings(
            self.selection.input_target,
            provider_backend_config=registered_host_execution_route(
                self.selection),
            provider_backend_schema_ref=EXECUTION_PROVENANCE_SCHEMA,
            transport_contract={
                'interaction_protocol_ref': 'llm_request_envelope/v1',
                'response_adapter_ref': 'llm_response_envelope/v1',
            },
            prompt={
                'messages': [{
                    'role': 'system',
                    'content': 'DSH registered host model request',
                }],
            },
            tool_catalog={
                'tools': list(self.managed_tools.provider_declarations)},
        )

    def _catalog(self):
        if not self.configured:
            return None
        schemas, types = registered_host_llm_schema_data()
        return SchemaCatalog(schemas=schemas, types=types)

    def _validate_configured_frame_budget(self, request, thread_state):
        """Reject a request before writer admission if its response cannot fit."""
        history_messages = (
            thread_state['committed_history'][-1]['answer']['messages']
            if thread_state['committed_history'] else [])
        messages = [*history_messages, *request['messages']]
        if not self._configured_frame_budget_fits(
                route=request['route'], messages=messages,
                request_id=request['request_id']):
            raise ValueError(
                'configured provider response budget exceeds the bounded '
                'DSH capability frame')

    def _configured_frame_budget_fits(self, *, route, messages, request_id):
        """Bound one exact future configured model response frame."""
        arguments = {
            'route': route,
            'messages': messages,
            'step': 0,
            'registered_tools': list(
                self.managed_tools.provider_declarations),
            'request': {
                **route,
                'messages': messages,
                'sessionId': self.session_id,
                'maxTokens': self.selection.input_target.max_output_tokens,
            },
        }
        # Registry identifiers are fixed-width typed IDs. These placeholders
        # deliberately exceed every real field while preserving the exact
        # ticket/frame shape and one model input reference.
        wide = 'x' * 128
        ticket = {
            'protocol': PROTOCOL,
            'session_id': self.session_id,
            'request_id': request_id,
            'execution_ref': {
                'entity_type': wide, 'entity_id': wide, 'version_id': wide},
            'start_event_id': wide,
            'transition': 'dsh.model',
            'input_refs': [{
                'resource_id': wide, 'resource_version_id': wide}],
            'upstream_revision': REVISION,
            'kind': 'model_response',
        }
        return _configured_response_frame_upper_bound(
            ticket, arguments,
            self.selection.input_target.max_response_bytes) <= MAX_BYTES

    def _commit(self, thread_ref, turn_ref, output):
        receipt = self.thread.record_execution_receipt(turn_ref=turn_ref, idempotency_key='dsh:receipt')
        self.thread.commit_terminal_answer(thread_ref=thread_ref, turn_ref=turn_ref, receipt_ref=receipt,
            answer=output, idempotency_key='dsh:answer')
        return {'status': 'terminal', 'answer': output, 'receipt_ref': _identity(receipt), 'replayed': False}

    def _execute(self, owner, thread_ref, turn_ref):
        # Same short namespace as main; include the exact turn and run identities.
        endpoint = owner_socket_path(
            self.root, f'dsh:{turn_ref.version_id}', owner._core.run_dir)
        loop = OwnerEventLoop(owner, endpoint)
        port = None
        try:
            if self.configured:
                port = self.input_port_factory(
                    self.selection,
                    destination_run_root=owner._core.run_dir)
            services = ExecutionServices(
                owner=owner, event_loop=loop, llm_input_port=port,
                interruption_requested=lambda: self.cancelled)
            kernel, repository = owner.operation_repository()
            self.managed_tool_service = ManagedPluginInvocationService(
                owner, kernel, repository, self.managed_tools)

            def prepare(*, execution, **kwargs):
                self.before_dispatch(owner, execution)
                if self.cancelled:
                    return OperationDispatch(
                        execution,
                        lambda: OperationDisposition(
                            execution, 'execution_block'))
                return services.prepare_dispatcher(
                    execution=execution, **kwargs)

            with ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix='rpnh-dsh-operation') as worker:
                self.runner = Harness(
                    owner=owner, event_loop=loop,
                    prepare_dispatcher=prepare,
                    submit_operation=worker.submit, max_in_flight=1)
                self.before_settlement(owner)
                result = self.runner.exact_execute()
        finally:
            self.runner = None
            self.managed_tool_service = None
            if port is not None:
                port.close()
            loop.close()
        if result.stop_reason != 'terminal':
            return {'status': result.stop_reason, 'answer': None}
        ref, = result.goal_resource_refs
        output = json.loads(owner._core.object_store.read_registered(owner._core.get_version(ref.resource_version_id)))
        return self._commit(thread_ref, turn_ref, output)

    def _invoke(self, execution, state, *, gateway=None, registered_llm=None):
        stage = execution.operation.firing.transition_id.rsplit('.',1)[-1]
        policy = state['policy']
        if state['session_id'] != self.session_id:
            raise ValueError('formal input belongs to another session')
        if stage == 'inspect':
            allow = policy.get('allow_request') is True
            return ('allow' if allow else 'deny'), 'decision_output', state
        if stage == 'accept':
            return 'complete', 'ready_output', state
        if stage.startswith('reject'):
            return 'complete', ('denied_request' if stage == 'reject_request' else 'denied_tool'), {**state, 'status': 'denied', 'text': 'Request denied by the declared business inspector.'}
        if stage == 'inspect_tool':
            call = state['pending_tool']
            if self.configured:
                allow = (call['name'] in policy.get('tools', [])
                         and call['name'] in self.managed_tool_names)
                if allow:
                    try:
                        declaration = self.managed_tools.declaration(
                            call['name'])
                        validate(
                            declaration.input_schema, call['arguments'])
                    except PluginError:
                        allow = False
                if allow:
                    _plugin, operation = self.managed_tools.binding(
                        call['name'])
                    worst_content = [{
                        'type': 'text',
                        # A canonical plugin result has at most this many
                        # UTF-8 bytes. Backslashes conservatively cover JSON
                        # string re-escaping in both repeated message fields.
                        'text': '\\' * operation.max_result_bytes,
                    }]
                    projected = [*state['messages'], {
                        'id': f"result-{call['id']}",
                        'role': 'user',
                        'source': {'kind': 'tool', 'callId': call['id']},
                        'content': [{
                            'type': 'tool-result',
                            'toolCallId': call['id'],
                            'content': worst_content,
                            'isError': False,
                        }],
                    }]
                    allow = self._configured_frame_budget_fits(
                        route=state['route'], messages=projected,
                        request_id=state['request_id'])
            else:
                allow = (call['name'] in policy.get('tools', [])
                         and call['name'] in {'read_dataset','sum_values'}
                         and ((call['name'] == 'read_dataset'
                               and call['arguments'] == {}) or
                              (call['name'] == 'sum_values'
                               and set(call['arguments']) == {'values'}
                               and call['arguments']['values']
                               == state.get('read_values'))))
            return ('allow' if allow else 'deny'), 'tool_decision_output', state
        if stage == 'finalize':
            blocks = state['messages'][-1]['content']
            text = ''.join(b['text'] for b in blocks if b['type'] == 'text')
            return 'complete', 'done', {**state, 'status': 'completed', 'text': text}
        if stage not in {'model', 'tool'}:
            raise ValueError('undeclared capability stage')
        ticket = {'protocol': PROTOCOL, 'session_id': self.session_id, 'request_id': state['request_id'],
                  'execution_ref': _identity(execution.operation_execution_lease_ref),
                  'start_event_id': str(execution.start_event_id), 'transition': execution.operation.firing.transition_id,
                  'input_refs': [_identity(i.resource_ref) for i in execution.operation.inputs],
                  'upstream_revision': REVISION, 'kind': stage}
        args = ({'route': state['route'], 'messages': state['messages'], 'step': state['step'],
                 **({'registered_tools': list(
                     self.managed_tools.provider_declarations)}
                    if self.configured else {}),
                 'request': {**state['route'], 'messages': state['messages'], 'sessionId': self.session_id,
                             **({'maxTokens': self.selection.input_target.max_output_tokens}
                                if self.configured else {})}}
                if stage == 'model' else {'call': state['pending_tool'], 'data': state['data']})

        def invoke_effect(effect_ticket, effect_args):
            response = self.effect({
                'ticket': effect_ticket, 'arguments': effect_args})
            if response.get('ticket') != effect_ticket:
                raise ValueError(
                    'DSH response belongs to another exact execution')
            if 'error' in response:
                raise RuntimeError(
                    'DSH effect failed; outcome not replayable: '
                    f"{response['error']}")
            value = response.get('value')
            if not isinstance(value, Mapping):
                raise ValueError('DSH effect response has no structured value')
            return value

        if stage == 'tool' and self.configured:
            if (gateway is None or self.managed_tool_service is None):
                raise ValueError(
                    'configured DSH tool firing lacks its owner-bound service')
            call = state['pending_tool']
            declaration = self.managed_tools.declaration(call['name'])
            try:
                result = gateway.invoke_registered_tool(
                    execution, declaration.registration_key,
                    identity=json_copy(declaration.identity),
                    contracts=json_copy(declaration.contracts),
                    kwargs={
                        'service': self.managed_tool_service,
                        'execution': execution,
                        'call_id': call['id'],
                        'arguments': call['arguments'],
                        'interruption_requested': lambda: self.cancelled,
                    })
                output = result['output']
                is_error = False
            except ManagedPluginInvocationFailed as exc:
                output = {'error': exc.code}
                is_error = True
            content = [{
                'type': 'text',
                'text': canonical_json(output).decode('utf-8'),
            }]
            observation = {
                'value': output,
                'isError': is_error,
                'content': content,
                'message': {
                    'id': f"result-{call['id']}",
                    'role': 'user',
                    'source': {'kind': 'tool', 'callId': call['id']},
                    'content': [{
                        'type': 'tool-result',
                        'toolCallId': call['id'],
                        'content': content,
                        'isError': is_error,
                    }],
                },
            }
        elif stage == 'model' and self.configured:
            if registered_llm is None:
                raise ValueError(
                    'configured DSH model firing lacks registered_llm/v1')
            response_ticket = {**ticket, 'kind': 'model_response'}
            if _configured_response_frame_upper_bound(
                    response_ticket, args,
                    self.selection.input_target.max_response_bytes
                    ) > MAX_BYTES:
                raise RuntimeError(
                    'configured response frame exceeded its admitted bound')
            request_ticket = {**ticket, 'kind': 'model_request'}
            prepared = invoke_effect(request_ticket, args)
            provider_request = prepared.get('provider_request')
            if not isinstance(provider_request, Mapping):
                raise ValueError(
                    'DSH configured adapter returned no provider request DTO')
            response_bytes = registered_llm.request(provider_request)
            response_document = json.loads(response_bytes)
            observation = invoke_effect(response_ticket, {
                **args, 'provider_response': response_document})
        else:
            if registered_llm is not None:
                raise ValueError(
                    'registered provider capability appeared outside its '
                    'configured model firing')
            observation = invoke_effect(ticket, args)
        state = {**state, 'calls': [*state['calls'], {'ticket': ticket, 'arguments': args, 'observation': observation}]}
        if stage == 'model':
            message = observation.get('message')
            expected_source = {'kind': 'model', **state['route']}
            if (not isinstance(message, Mapping)
                    or message.get('role') != 'assistant'
                    or message.get('source') != expected_source
                    or not isinstance(message.get('content'), list)
                    or not message['content']
                    or any(not isinstance(block, Mapping)
                           or block.get('type') not in {
                               'text', 'reasoning', 'tool-call'}
                           for block in message['content'])):
                raise ValueError(
                    'model response is not an exact assistant message')
            calls = [
                block for block in message['content']
                if block['type'] == 'tool-call']
            if len(calls) > 1:
                raise ValueError('parallel tool-call batches are not supported by this managed profile')
            finish = observation.get('finish')
            expected_finish = 'tool-calls' if calls else 'stop'
            if (not isinstance(finish, Mapping)
                    or finish.get('kind') != expected_finish):
                raise RuntimeError(
                    'model response did not finish with the admitted outcome')
            state = {**state, 'messages': [*state['messages'], message], 'step': state['step'] + 1}
            if calls:
                call = calls[0]
                raw_arguments = call.get('arguments')
                parsed = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                if not isinstance(parsed, dict):
                    raise ValueError('tool arguments must be a JSON object')
                call = {**call, 'raw_arguments': raw_arguments, 'arguments': parsed}
                return 'tool', 'response_output', {**state, 'pending_tool': call}
            return 'answer', 'response_output', state
        call = state['pending_tool']
        is_error = observation.get('isError')
        if type(is_error) is not bool or 'value' not in observation:
            raise RuntimeError('admitted DSH tool execution did not settle')
        if call['name'] == 'read_dataset' and not is_error:
            state['read_values'] = observation['value']
        tool_message = observation.get('message')
        tool_content = (
            tool_message.get('content')
            if isinstance(tool_message, Mapping) else None)
        if (not isinstance(tool_message, Mapping)
                or tool_message.get('role') != 'user'
                or tool_message.get('source') != {
                    'kind': 'tool', 'callId': call['id']}
                or not isinstance(tool_content, list)
                or len(tool_content) != 1
                or not isinstance(tool_content[0], Mapping)
                or tool_content[0].get('type') != 'tool-result'
                or tool_content[0].get('toolCallId') != call['id']
                or tool_content[0].get('isError') is not is_error):
            raise ValueError('tool message is not correlated with the admitted tool call')
        return 'complete', 'ready_output', {**state, 'messages': [*state['messages'], tool_message]}
