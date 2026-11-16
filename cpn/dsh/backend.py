"""DSH driver on the existing RunOwner/Harness and MainThreadRegistry.

Only an admitted operation may ask the DSH capability host for an observation.
The observation is a candidate until existing products/Success settlement.
The offline conformance profile allows one adapter call per execution, no retry.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
import json
import re
from pathlib import Path
from typing import Callable, Iterable, Mapping
from jsonschema import Draft7Validator
from .message_codec import decode_tool_result, make_tool_result
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
from cpn.plugins.api import PluginError, canonical, json_copy, validate
from cpn.plugins.catalog import read_config
from cpn.plugins.managed_batch import ManagedOperationBatchService
from cpn.plugins.managed_output import (
    MANAGED_OPERATION_RESULTS_DOCUMENT,
    MANAGED_OPERATION_RESULTS_SCHEMA,
    MANAGED_OPERATION_OUTPUT_INPUT_SCHEMA,
    MANAGED_OPERATION_TRANSFER_INPUT_SCHEMA,
    MANAGED_OPERATION_RESULT_IMPORT_SCHEMA,
    MANAGED_OPERATION_RESULT_IMPORT_DOCUMENT,
    MANAGED_OPERATION_RESULTS_EXPORT_TOOL,
    MANAGED_OPERATION_RESULTS_EXPORT_IDENTITY,
    MANAGED_OPERATION_RESULTS_EXPORT_CONTRACTS,
    MANAGED_OPERATION_OUTPUT_TOOL as MANAGED_OPERATION_OUTPUT_READER_KEY,
    MANAGED_OPERATION_OUTPUT_IDENTITY as MANAGED_OPERATION_OUTPUT_READER_IDENTITY,
    MANAGED_OPERATION_OUTPUT_CONTRACTS as MANAGED_OPERATION_OUTPUT_READER_CONTRACTS,
    ManagedOperationOutputReader,
    ManagedOperationOutputReadDenied,
    fit_managed_output_page,
    managed_operation_result_document,
    managed_operation_output_locator,
    render_managed_operation_output,
    validate_managed_operation_result_delivery,
)
from cpn.plugins.managed_scheduler import (
    ManagedRunCapacity, ManagedSchedulerPolicy, ManagedToolCall,
    ManagedToolScheduler,
)
from cpn.components.agent_loop.managed_execution import (
    managed_scheduler_policy_from_document,
)
from cpn.components.agent_loop.compact import approximate_tokens
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.task_control import owner_socket_path
from cpn.rpnh.harness import Harness, OperationDispatch, OperationDisposition
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (ArcDeclaration, ColourExpression, InputVerdictGuard,
    LeaseIdentityDeclaration, PNFragment, PlaceDeclaration, PortBinding,
    PortDeclaration, ResourceLeasePoolBinding, TransitionDeclaration)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry, _parse_ref
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.resource_access import ResourceReadContract
from cpn.rpnh.run import OwnerInput, start_run, resume_run

REVISION = '687f282e8625c6b8d11ff58211a90524be2e4317'
PROTOCOL = 'rpnh/dsh/v1'
ENVELOPE = 'application/rpnh_dsh_envelope/v1'
ENVELOPE_V2 = 'application/rpnh_dsh_envelope/v2'
CONFIG = 'application/rpnh_dsh_config/v1'
CONFIG_V2 = 'application/rpnh_dsh_operation_config/v2'
CONFIG_BATCH = 'application/rpnh_dsh_managed_batch_config/v1'
CONFIG_RESULTS = 'application/rpnh_dsh_managed_results_config/v1'
CONFIG_RESULTS_V2 = 'application/rpnh_dsh_managed_results_config/v2'
CONFIG_MANAGED_TERMINAL = 'application/rpnh_dsh_managed_terminal_config/v1'
COMPONENT = 'rpnh/dsh-driver/v1'
EXECUTOR = 'rpnh/dsh-capability/v1'
CONFIGURED_EXECUTOR = 'rpnh/dsh-configured-capability/v1'
MANAGED_BATCH_EXECUTOR = 'rpnh/dsh-managed-batch/v1'
MANAGED_RESULTS_EXECUTOR = 'rpnh/dsh-managed-results/v1'
MANAGED_RESULTS_TRANSFER_EXECUTOR = 'rpnh/dsh-managed-results/v2'
MANAGED_TERMINAL_EXECUTOR = 'rpnh/dsh-managed-terminal/v1'
MANAGED_RESULT_IMPORT_SYMBOL = 'dsh.managed_result_import'
MANAGED_RESULT_READER = 'read_managed_output'
TERMINAL = 'rpnh/dsh-terminal/v1'
MAX_MANAGED_TOOL_RESULT_BYTES = 16 * 1024 * 1024
# Legacy inline results appear in both formal arguments and provider DTOs.
# The reader-enabled path keeps full bodies in original Petri products and
# uses bounded exact pages. Both paths retain the same effect-frame limit.
MAX_BYTES = 4 * MAX_MANAGED_TOOL_RESULT_BYTES + 4 * 1024 * 1024
DEFAULT_ATTEMPT_BUDGET = 48
STAGES = ('inspect', 'accept', 'reject_request', 'model', 'inspect_tool', 'tool', 'reject_tool', 'finalize')


def _managed_result_reader_declaration(*, delivery=False):
    return {
        'type': 'function',
        'function': {
            'name': MANAGED_RESULT_READER,
            'description': (
                'Read an exact completed managed result page, or list the '
                'completed result directory through an exact firing. '
                + ('For earlier main turns, list imported_archive first and use '
                   'the returned local import_resource_ref with its original locator. '
                   if delivery else '')
                +
                'Keep all locator fields unchanged; follow the returned '
                'continuation offset to read another bounded page.'),
            'parameters': {'type': 'object',
                           **json_copy(MANAGED_OPERATION_TRANSFER_INPUT_SCHEMA
                                       if delivery else MANAGED_OPERATION_OUTPUT_INPUT_SCHEMA)},
        },
    }


def _read_managed_operation_output(*, service, execution, arguments):
    return service.read(execution, arguments)


def _export_managed_operation_results(*, service, execution):
    return service.export(execution)


@dataclass(frozen=True)
class _DshManagedProducts:
    """Unpublished worker material, settled only with the original operation."""

    state: Mapping
    calls: tuple
    observations: tuple
    entries: tuple

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
    from cpn.plugins.account_bindings import BINDING_SCHEMA
    account_ports = tuple(port for port in context.ports if port.schema == BINDING_SCHEMA)
    schemas = {port.schema for port in context.ports if port not in account_ports}
    if len(schemas) != 1 or not schemas.issubset({ENVELOPE, ENVELOPE_V2}):
        raise ValueError('DSH component ports require one envelope revision')
    for port in account_ports:
        if (port.direction != 'input' or re.fullmatch(r'account_binding_[0-9]+', port.name) is None
                or tuple(operation.name for operation in context.operations
                         if port.name in operation.inputs) != ('tool',)):
            raise ValueError('DSH account binding requires its exact tool read input')
    envelope_schema = next(iter(schemas))
    managed_results = any(
        operation.name == 'tool'
        and operation.executor in {
            MANAGED_RESULTS_EXECUTOR, MANAGED_RESULTS_TRANSFER_EXECUTOR}
        for operation in context.operations)
    result_delivery = any(
        operation.name == 'tool'
        and operation.executor == MANAGED_RESULTS_TRANSFER_EXECUTOR
        for operation in context.operations)
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
    operation_outcomes = {
        operation.name: {outcome.name for outcome in operation.outcomes}
        for operation in context.operations}
    arcs = []
    arcs.extend(ArcDeclaration(port.name, 'tool', 'input', mode='read')
                for port in account_ports)
    for stage, (source, targets) in routes.items():
        arcs.append(ArcDeclaration(source, stage, 'input'))
        colour = {'colour_expression': ColourExpression(kind='input_colour', source_place=source)} if source in {'decision', 'response', 'tool_decision'} else {}
        if 'interrupted' in operation_outcomes[stage]:
            arcs.append(ArcDeclaration(source, stage, 'output', mode='produce', outcome='interrupted', emit='forward', forward_source=source, **colour))
        for target, outcome in targets:
            kw = {'colour_expression': ColourExpression(value=outcome)} if target in {'decision', 'response', 'tool_decision'} else {}
            arcs.append(ArcDeclaration(target, stage, 'output', mode='produce', outcome=outcome, **kw))
    if managed_results:
        arcs.append(ArcDeclaration(
            'managed_results', 'tool', 'output', mode='produce',
            outcome='complete'))
    internal = tuple(PortDeclaration(f'{name}_{direction}', direction, envelope_schema)
                     for name, _ in places if name not in {'request', 'done', 'denied_request', 'denied_tool'}
                     for direction in ('input', 'output')) + (
                         PortDeclaration(
                             'request_return', 'output', envelope_schema),)
    if managed_results:
        internal += (PortDeclaration(
            'managed_results_output', 'output',
            MANAGED_OPERATION_RESULTS_SCHEMA),)
    declared_places = tuple(
        PlaceDeclaration(name, envelope_schema, capacity=1, colours=colours)
        for name, colours in places) + tuple(
            PlaceDeclaration(port.name, BINDING_SCHEMA)
            for port in account_ports)
    if managed_results:
        # The original PN keeps each completed immutable result product.  No
        # scheduling token or fixed history count is introduced here.
        declared_places += (PlaceDeclaration(
            'managed_results', MANAGED_OPERATION_RESULTS_SCHEMA),)
    if result_delivery:
        # A single original owner resource is carried by the PN lease.  Its
        # body is never another semantic envelope input or a per-step copy.
        declared_places += (PlaceDeclaration(
            'managed_result_import', MANAGED_OPERATION_RESULT_IMPORT_SCHEMA,
            token_kind='resource_lease', capacity=1, reusable=True),)
        arcs.append(ArcDeclaration(
            'managed_result_import', 'tool', 'input', mode='read'))
    return PNFragment(
        places=declared_places,
        transitions=tuple(TransitionDeclaration(s, s, input_verdicts=(InputVerdictGuard(*guards[s]),) if s in guards else ()) for s in STAGES),
        arcs=tuple(arcs), operations=context.operations,
        ports=(PortBinding('request', 'request'), PortBinding('done', 'done'), PortBinding('denied_request', 'denied_request'), PortBinding('denied_tool', 'denied_tool'),
               *(PortBinding(port.name, port.name) for port in account_ports)),
        internal_ports=internal,
        internal_bindings=tuple(PortBinding(p.name, ('request' if p.name == 'request_return' else p.name.rsplit('_', 1)[0])) for p in internal),
        lease_identities=((LeaseIdentityDeclaration('managed_result_import'),)
                          if result_delivery else ()),
        lease_pools=((ResourceLeasePoolBinding(
            'managed_result_import', 'managed_result_import',
            ('managed_result_import',)),) if result_delivery else ()))


def declaration(*, configured: bool = False,
                managed_tools: ManagedPluginToolCatalog | None = None,
                managed_tool_policy: ManagedSchedulerPolicy | None = None,
                managed_result_reader: bool = False,
                managed_result_delivery: bool = False,
                managed_result_output_bytes: int = 10_000,
                managed_history_retained_tokens: int | None = None,
                attempt_budget: int | None = DEFAULT_ATTEMPT_BUDGET,
                managed_accounts=None, managed_account_provider=None):
    if (attempt_budget is not None
            and (type(attempt_budget) is not int or attempt_budget < 1)):
        raise ValueError('DSH attempt budget must be null or a positive integer')
    if managed_tools is not None and not configured:
        raise ValueError('managed provider tools require configured DSH')
    if managed_tool_policy is not None and not configured:
        raise ValueError('managed scheduling requires configured DSH')
    if type(managed_result_reader) is not bool or (managed_result_reader and not configured):
        raise ValueError('managed result reader requires configured DSH')
    if (type(managed_result_delivery) is not bool
            or (managed_result_delivery and not managed_result_reader)):
        raise ValueError('managed result delivery requires the explicit result reader')
    if type(managed_result_output_bytes) is not int or managed_result_output_bytes < 128:
        raise ValueError('managed result projection requires the runtime minimum of 128 bytes')
    if (managed_history_retained_tokens is not None
            and (type(managed_history_retained_tokens) is not int
                 or managed_history_retained_tokens < 1)):
        raise ValueError('managed history retention requires a positive estimate or null')
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
        if configured and name == 'tool' and managed_tool_policy is not None:
            executor = MANAGED_BATCH_EXECUTOR
            operation_config = {
                'managed_tool_policy': managed_tool_policy.identity()}
        if configured and name == 'tool' and managed_result_reader:
            executor = (MANAGED_RESULTS_TRANSFER_EXECUTOR
                        if managed_result_delivery else MANAGED_RESULTS_EXECUTOR)
            operation_config = {
                **operation_config,
                'managed_result_reader': True,
                'context_tool_output_byte_limit': managed_result_output_bytes,
                **({'context_compaction_retained_tokens': managed_history_retained_tokens}
                   if managed_history_retained_tokens is not None else {}),
                **({'managed_result_import_symbol': MANAGED_RESULT_IMPORT_SYMBOL}
                   if managed_result_delivery else {}),
            }
        terminal_export = managed_result_delivery and name in {
            'finalize', 'reject_request', 'reject_tool'}
        if terminal_export:
            executor = MANAGED_TERMINAL_EXECUTOR
            operation_config = {'managed_result_export_source': 'dsh.tool'}
        # A managed tool firing may already own successful or unknown physical
        # effects. Returning its unchanged input would enqueue those calls again.
        # Leave incomplete tool work for the original reconciliation boundary.
        interrupt_outcomes = ([] if configured and name == 'tool' else
                              [{'name': 'interrupted', 'products': []}])
        result_products = ([{'port': 'managed_results_output'}]
                           if configured and name == 'tool' and managed_result_reader else [])
        operations.append({'name': name, 'executor': executor, 'inputs': [source],
            'outputs': [target, 'request_return' if source == 'request' else source.rsplit('_',1)[0] + '_output',
                        *([item['port'] for item in result_products])],
            'tools': ([*managed_registration_keys,
                       *([MANAGED_OPERATION_OUTPUT_READER_KEY] if managed_result_reader else [])]
                      if configured and name == 'tool' else
                      [MANAGED_OPERATION_RESULTS_EXPORT_TOOL] if terminal_export else []),
            'config': operation_config,
            'request_port': source if configured and name == 'model' else None,
            'budget_binding': binding,
            'outcomes': [{'name': o, 'products': [{'port': target}, *result_products]} for o in outcomes] + interrupt_outcomes})
    def terminal(stage, port):
        return {'key': TERMINAL, 'source': {'component': 'dsh', 'port': port}, 'operation': stage,
                'outcome': 'complete', 'config': {'run_outcome': 'complete'}}
    envelope = ENVELOPE_V2 if configured else ENVELOPE
    module = ModuleDeclaration.from_dict({'schema_version': 'rpnh/module_declaration/v1', 'name': 'DSHDriver',
        'components': [{'name': 'dsh', 'key': COMPONENT, 'config_schema': CONFIG, 'config': {},
                        'ports': [{'name': n, 'direction': d, 'schema': envelope} for n,d in [('request','input'),('done','output'),('denied_request','output'),('denied_tool','output')]],
                        'operations': operations}], 'links': [],
        'entry': {'request': {'component': 'dsh', 'port': 'request'}},
        'exit': {n: {'component': 'dsh', 'port': n} for n in ('done','denied_request','denied_tool')},
        'terminal': terminal('finalize', 'done'),
        'terminal_alternatives': [terminal('reject_request', 'denied_request'), terminal('reject_tool', 'denied_tool')],
        'required_schemas': [
            envelope, CONFIG, *((CONFIG_V2,) if configured else ()),
            *((CONFIG_BATCH,) if managed_tool_policy is not None else ()),
            *((CONFIG_RESULTS, MANAGED_OPERATION_RESULTS_SCHEMA)
              if managed_result_reader else ()),
            *((CONFIG_RESULTS_V2, CONFIG_MANAGED_TERMINAL,
               MANAGED_OPERATION_RESULT_IMPORT_SCHEMA)
              if managed_result_delivery else ())],
        'budgets': {},
        'budget_buckets': [{**binding, 'max_attempts': attempt_budget}]})
    if managed_accounts is not None:
        from cpn.plugins.account_bindings import AccountBindingPlan, with_account_binding_inputs
        plan = (managed_accounts if type(managed_accounts) is AccountBindingPlan
                else AccountBindingPlan.from_dict(managed_accounts))
        if not configured or any(row.node_id != 'dsh' for row in plan.bindings):
            raise ValueError('DSH account plan requires its configured dsh tool node')
        plan.require_catalogs({'dsh': managed_tools})
        module = with_account_binding_inputs(module, plan, consumers={'dsh': 'tool'},
            provider_selection=managed_account_provider)
    elif managed_account_provider is not None:
        raise ValueError('DSH account provider requires an explicit account plan')
    return module


def registration(executor=None,
                 managed_tools: ManagedPluginToolCatalog | None = None, *,
                 managed_registration_keys=None,
                 managed_result_reader: bool = False,
                 managed_result_delivery: bool = False,
                 account_bindings: bool = False):
    if (type(managed_result_delivery) is not bool
            or (managed_result_delivery and not managed_result_reader)):
        raise ValueError('managed result delivery requires the explicit result reader')
    executor = dict if executor is None else executor
    reg = Registration()
    if account_bindings:
        from cpn.components.basic import register_basic_components
        from cpn.plugins.account_bindings import register_account_binding_support
        register_basic_components(reg)
        register_account_binding_support(reg)
    reg.register_schema(ENVELOPE, ENVELOPE_SCHEMA)
    reg.register_schema(ENVELOPE_V2, ENVELOPE_V2_SCHEMA)
    reg.register_schema(
        EXECUTION_PROVENANCE_SCHEMA, EXECUTION_PROVENANCE_DOCUMENT)
    reg.register_schema(CONFIG, {'$id': CONFIG, '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object', 'additionalProperties': False, 'properties': {}})
    reg.register_schema(CONFIG_BATCH, {
        '$id': CONFIG_BATCH,
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'type': 'object', 'additionalProperties': False,
        'required': ['managed_tool_policy'],
        'properties': {'managed_tool_policy': {'type': 'object'}},
    })
    if managed_result_reader:
        reg.register_schema(
            MANAGED_OPERATION_RESULTS_SCHEMA,
            MANAGED_OPERATION_RESULTS_DOCUMENT)
        reg.register_schema(CONFIG_RESULTS, {
            '$id': CONFIG_RESULTS,
            '$schema': 'http://json-schema.org/draft-07/schema#',
            'type': 'object', 'additionalProperties': False,
            'required': ['managed_result_reader', 'context_tool_output_byte_limit'],
            'properties': {
                'managed_result_reader': {'const': True},
                'context_tool_output_byte_limit': {'type': 'integer', 'minimum': 128},
                'context_compaction_retained_tokens': {'type': 'integer', 'minimum': 1},
                'managed_tool_policy': {'type': 'object'},
            },
        })
        if managed_result_delivery:
            result_config = json_copy(reg.declaration('schema', CONFIG_RESULTS)['schema'])
            result_config['$id'] = CONFIG_RESULTS_V2
            result_config['required'].append('managed_result_import_symbol')
            result_config['properties']['managed_result_import_symbol'] = {
                'const': MANAGED_RESULT_IMPORT_SYMBOL}
            reg.register_schema(CONFIG_RESULTS_V2, result_config)
            reg.register_schema(
                MANAGED_OPERATION_RESULT_IMPORT_SCHEMA,
                MANAGED_OPERATION_RESULT_IMPORT_DOCUMENT)
            reg.register_schema(CONFIG_MANAGED_TERMINAL, {
                '$id': CONFIG_MANAGED_TERMINAL,
                '$schema': 'http://json-schema.org/draft-07/schema#',
                'type': 'object', 'additionalProperties': False,
                'required': ['managed_result_export_source'],
                'properties': {
                    'managed_result_export_source': {'const': 'dsh.tool'},
                },
            })
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
    reg.register_executor(MANAGED_BATCH_EXECUTOR, executor,
        identity={'implementation_id': 'rpnh.dsh.managed_batch', 'revision': 'v1'},
        contracts={'transport': 'deterministic',
                   'capability_transport': 'dsh-jsonl-v1',
                   'input_ports': None, 'output_ports': None,
                   'config_schema': CONFIG_BATCH})
    if managed_result_reader:
        reg.register_executor(MANAGED_RESULTS_EXECUTOR, executor,
            identity={'implementation_id': 'rpnh.dsh.managed_results', 'revision': 'v1'},
            contracts={
                'transport': 'deterministic',
                'capability_transport': 'dsh-jsonl-v1',
                'input_ports': None, 'output_ports': None,
                'config_schema': CONFIG_RESULTS,
                'resource_read_contracts': [ResourceReadContract(
                    metadata_only=False,
                    context_origins=('petri_operation',),
                    origin_kinds=('petri_output',),
                    metadata_equals={
                        'content_schema_ref': MANAGED_OPERATION_RESULTS_SCHEMA},
                    require_provenance_binding=True,
                ).to_dict()],
            })
        reg.register_tool(
            MANAGED_OPERATION_OUTPUT_READER_KEY,
            _read_managed_operation_output,
            identity=json_copy(MANAGED_OPERATION_OUTPUT_READER_IDENTITY),
            contracts=json_copy(MANAGED_OPERATION_OUTPUT_READER_CONTRACTS))
        if managed_result_delivery:
            transfer_contracts = json_copy(
                reg.declaration('executor', MANAGED_RESULTS_EXECUTOR)['contracts'])
            transfer_contracts['config_schema'] = CONFIG_RESULTS_V2
            reg.register_executor(MANAGED_RESULTS_TRANSFER_EXECUTOR, executor,
                identity={'implementation_id': 'rpnh.dsh.managed_results', 'revision': 'v2'},
                contracts=transfer_contracts)
            reg.register_executor(MANAGED_TERMINAL_EXECUTOR, executor,
                identity={'implementation_id': 'rpnh.dsh.managed_terminal', 'revision': 'v1'},
                contracts={
                    'transport': 'deterministic',
                    'capability_transport': 'dsh-jsonl-v1',
                    'input_ports': None, 'output_ports': None,
                    'config_schema': CONFIG_MANAGED_TERMINAL,
                    'resource_read_contracts': [ResourceReadContract(
                        metadata_only=False,
                        context_origins=('petri_operation',),
                        origin_kinds=('petri_output',),
                        metadata_equals={
                            'content_schema_ref': MANAGED_OPERATION_RESULTS_SCHEMA},
                        descriptor_equals={'place': 'dsh.managed_results'},
                    ).to_dict()],
                })
            reg.register_tool(
                MANAGED_OPERATION_RESULTS_EXPORT_TOOL,
                _export_managed_operation_results,
                identity=json_copy(MANAGED_OPERATION_RESULTS_EXPORT_IDENTITY),
                contracts=json_copy(MANAGED_OPERATION_RESULTS_EXPORT_CONTRACTS))
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
        ManagedPluginToolAdapter(managed_tools).register(
            reg, registration_keys=managed_registration_keys,
            include_legacy=managed_registration_keys is not None)
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
    'managed_results_output': 'dsh.managed_results',
}


class _DshExecutor:
    """Thin DSH HOST adapter over generic operation/provider services."""

    def __init__(self, backend):
        self.backend = backend

    def __call__(self, *, execution, gateway, resources, host_context):
        del resources
        value = json.loads(_dsh_state_payload(execution,
            tuple(item.consumer_payload for item in execution.operation.inputs)))
        registered_llm = getattr(host_context, 'registered_llm', None)
        outcome, output_port, output = self.backend._invoke(
            execution, value, gateway=gateway,
            registered_llm=registered_llm)
        extra_outputs = ()
        if isinstance(output, _DshManagedProducts):
            document = managed_operation_result_document(
                execution, output.entries)
            result_artifact = self._publish(
                execution, gateway, outcome, 'managed_results_output', document)
            output = self.backend._project_managed_products(
                execution, output, result_artifact.header.ref)
            extra_outputs = (result_artifact,)
        artifact = self._publish(
            execution, gateway, outcome, output_port, output)
        return OperationExecutionResult(
            outputs=(*extra_outputs, artifact), selected_outcome_id=outcome)

    @staticmethod
    def _publish(execution, gateway, outcome, output_port, output):
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
                + (':product' if output_port != 'managed_results_output'
                   else ':managed-results')),
        ))
        artifact = gateway.verify_resource(
            execution.operation.canonical, ref)
        return artifact


class DshContextBudgetError(ValueError):
    """Only the original model/frame capacity checks, not input integrity."""


def _dsh_state_payload(execution, input_payloads):
    """Select the one registered envelope port, never inspect bodies to choose.

    The gateway provides exact acknowledged payloads for managed tool paths.
    Extra declared inputs may only be the non-secret fixed account bindings;
    these are not part of the model's conversation or DSH state machine.
    """
    from cpn.plugins.account_bindings import BINDING_SCHEMA
    inputs = execution.operation.inputs
    ports = {port.port_id: port for port in execution.operation.spec.input_ports}
    if (len(input_payloads) != len(inputs)
            or len({item.port_id for item in inputs}) != len(inputs)
            or {item.port_id for item in inputs} != set(ports)):
        raise ValueError('DSH state requires exact original input closure')
    envelopes = []
    for item, payload in zip(inputs, input_payloads, strict=True):
        port = ports.get(item.port_id)
        if (port is None or item.projected_payload is not None
                or item.source_resource_ref is not None):
            raise ValueError('DSH state requires exact unprojected input ports')
        if port.content_schema_id in {ENVELOPE, ENVELOPE_V2}:
            envelopes.append(payload)
        elif port.content_schema_id != BINDING_SCHEMA:
            raise ValueError('DSH state contains an undeclared semantic input')
    if len(envelopes) != 1:
        raise ValueError('DSH requires one exact registered envelope input')
    return envelopes[0]


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
            managed_tools: Mapping[str, object] | None = None,
            managed_tool_admitted_effects: Iterable[str] = ("pure",),
            managed_tool_policy: Mapping[str, object] | ManagedSchedulerPolicy | None = None,
            managed_result_reader: bool = False,
            managed_result_import_max_bytes: int | None = None,
            attempt_budget: int | None = DEFAULT_ATTEMPT_BUDGET,
            input_port_factory=build_llm_input_port,
            managed_account_resolver_factory=None,
            managed_accounts=None,
            managed_account_provider=None,
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
        if managed_account_resolver_factory is not None and not callable(managed_account_resolver_factory):
            raise TypeError('managed account resolver factory must be installed HOST code')
        if (attempt_budget is not None
                and (type(attempt_budget) is not int or attempt_budget < 1)):
            raise ValueError(
                'DSH attempt budget must be null or a positive integer')
        self.root, self.session_id, self.effect = root / session_id, session_id, effect
        self.execution_config_path = (
            execution_config_path.resolve()
            if execution_config_path is not None else None)
        self.selection = (
            load_llm_execution_selection(self.execution_config_path)
            if self.execution_config_path is not None else selection)
        self.configured = self.selection is not None
        self.offline = offline
        if type(managed_result_reader) is not bool or (managed_result_reader and not self.configured):
            raise ValueError('managed result reader requires explicit configured DSH')
        self.managed_result_reader = managed_result_reader
        # Resume replaces this fresh selection with the original compiled key.
        if managed_result_import_max_bytes is not None and (
                type(managed_result_import_max_bytes) is not int
                or managed_result_import_max_bytes < 0 or not managed_result_reader):
            raise ValueError('optional import budget requires exact nonnegative integer and configured reader')
        self.managed_result_import_max_bytes = managed_result_import_max_bytes
        self.managed_result_delivery = managed_result_reader
        self.managed_result_output_bytes = (
            self.selection.runtime_policy.context_tool_output_byte_limit
            if self.configured else 10_000)
        target = self.selection.input_target if self.configured else None
        self.managed_history_retained_tokens = (
            target.context_compaction_retained_tokens
            if target is not None and target.context_compaction_retained_tokens is not None
            else max(1, target.context_window_tokens // 4)
            if target is not None and target.context_window_tokens is not None
            else None)
        if plugin_config_path is not None:
            if not isinstance(plugin_config_path, Path):
                raise TypeError('plugin_config_path requires pathlib.Path')
            if not plugin_config_path.is_absolute():
                raise ValueError('plugin_config_path must be absolute')
        if (plugin_config_path is None) != (managed_tools is None):
            raise ValueError(
                'managed tools require one explicit plugin config and allowlist')
        if managed_tools is not None:
            if not isinstance(managed_tools, Mapping) or not managed_tools:
                raise ValueError('managed tool allowlist must be a nonempty mapping')
            if not self.configured:
                raise ValueError('managed provider tools require configured DSH')
            selected_plugins = load_catalog(read_config(plugin_config_path))
            self.managed_tools = build_managed_plugin_tool_catalog(
                selected_plugins, managed_tools,
                admitted_effects=managed_tool_admitted_effects,
                protocol_version="v3" if selected_plugins.versioned else "v2")
        else:
            if tuple(managed_tool_admitted_effects) != ("pure",):
                raise ValueError(
                    'managed admitted effects require an explicit tool allowlist')
            self.managed_tools = build_managed_plugin_tool_catalog(
                load_catalog(), ())
        for tool in self.managed_tools.tools:
            _plugin, operation = self.managed_tools.binding(tool.name)
            if operation.max_result_bytes > MAX_MANAGED_TOOL_RESULT_BYTES:
                raise ValueError(
                    'managed DSH tool result limit exceeds 16 MiB')
        if managed_account_resolver_factory is not None and not self.managed_tools.tools:
            raise ValueError('managed account resolver factory requires selected tools')
        self.account_plan = None
        self.managed_account_provider = None
        if managed_accounts is not None:
            from cpn.plugins.account_bindings import AccountBindingPlan
            self.account_plan = AccountBindingPlan.from_dict(managed_accounts)
            if not self.configured or any(row.node_id != 'dsh' for row in self.account_plan.bindings):
                raise ValueError('DSH account plan requires its configured dsh tool node')
            self.account_plan.require_catalogs({'dsh': self.managed_tools})
            if managed_account_provider is not None:
                if managed_account_resolver_factory is not None:
                    raise ValueError('account provider selection and in-process factory are mutually exclusive')
                from cpn.plugins.account_provider_catalog import AccountProviderSelection, load_account_provider
                self.managed_account_provider = AccountProviderSelection.from_dict(managed_account_provider)
                managed_account_resolver_factory = load_account_provider(self.managed_account_provider)
            if managed_account_resolver_factory is None:
                raise ValueError('managed account plan requires an explicitly installed resolver factory')
        elif managed_account_provider is not None:
            raise ValueError('DSH account provider requires an explicit account plan')
        self.managed_account_resolver_factory = managed_account_resolver_factory
        self._business_tool_names = tuple(
            tool.name for tool in self.managed_tools.tools)
        if self.managed_result_reader and MANAGED_RESULT_READER in self._business_tool_names:
            raise ValueError('managed plugin name conflicts with the explicit result reader')
        if managed_tool_policy is not None:
            if not self.configured or not self._business_tool_names:
                raise ValueError('managed scheduling requires an explicit configured tool allowlist')
            if not isinstance(managed_tool_policy, ManagedSchedulerPolicy):
                if not isinstance(managed_tool_policy, Mapping):
                    raise ValueError('managed scheduling policy must be an object')
                managed_tool_policy = managed_scheduler_policy_from_document(
                    managed_tool_policy)
            for tool in self.managed_tools.tools:
                managed_tool_policy.validate_declaration(tool)
        self.managed_tool_policy = managed_tool_policy
        self.execution_profile = None
        if self.configured:
            if execution_profile is not None:
                self.execution_profile = _public_execution_profile(
                    self.selection, execution_profile)
        elif execution_profile is not None:
            raise ValueError('offline DSH does not accept an execution profile')
        self.input_port_factory = input_port_factory
        self.attempt_budget = attempt_budget
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
        self.managed_output_service = None
        self.managed_run_capacity = None
        self._active_managed_policy = None
        self.cancelled = False
        self.before_dispatch = lambda owner, execution: None
        self.before_settlement = lambda owner: None

    @property
    def managed_tool_names(self):
        return (*self._business_tool_names,
                *((MANAGED_RESULT_READER,) if self.managed_result_reader else ()))

    def _provider_tool_declarations(self):
        return [*self.managed_tools.provider_declarations,
                *([_managed_result_reader_declaration(
                    delivery=self.managed_result_delivery)]
                  if self.managed_result_reader else [])]

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
        self.managed_result_delivery = self.managed_result_reader
        history_messages = (state['committed_history'][-1]['answer']['messages']
                            if state['committed_history'] else [])
        imported_results = None
        import_context = None
        context_denials = None
        if self.managed_result_delivery:
            from .result_delivery import collect_result_deliveries
            context_denials = self._committed_context_denials(state, history_messages)
            omissions = []
            options = ({'optional': True,
                        'max_import_bytes': self.managed_result_import_max_bytes,
                        'notices': omissions}
                       if self.managed_result_import_max_bytes is not None else {})
            imported_results = collect_result_deliveries(
                self.thread, expected_thread_ref=state['thread_ref'], **options)
            import_context = self._result_import_context(imported_results)
            if omissions:
                import_context['omissions'] = omissions
            if self.managed_result_import_max_bytes is not None:
                imported_results, import_context = self._fit_optional_import_context(
                    request, state, imported_results, import_context,
                    context_denials=context_denials)
        if self.configured:
            self._validate_configured_frame_budget(
                request, state, import_context=import_context,
                context_denials=context_denials)
        self.cancelled = False
        advance = self.thread.accept_turn(thread_ref=_parse_ref(state['thread_ref']), user_input=request,
                                         idempotency_key=request['request_id'])
        attachment = self.thread.attach_attempt(thread_ref=advance.thread_ref, turn_ref=advance.turn_ref,
                                               idempotency_key=request['request_id'])
        module = declaration(
            configured=self.configured,
            managed_tools=(self.managed_tools if self.configured else None),
            managed_tool_policy=self.managed_tool_policy,
            managed_result_reader=self.managed_result_reader,
            managed_result_delivery=self.managed_result_delivery,
            managed_result_output_bytes=self.managed_result_output_bytes,
            managed_history_retained_tokens=self.managed_history_retained_tokens,
            attempt_budget=self.attempt_budget,
            managed_accounts=self.account_plan,
            managed_account_provider=self.managed_account_provider)
        envelope = {**request, 'messages': [*history_messages, *request['messages']], 'calls': [], 'step': 0,
                    **({'history_prefix_length': len(history_messages)}
                       if self.managed_result_reader else {}),
                    **({'managed_result_import': import_context}
                       if import_context is not None else {}),
                    **({'model_context_denials': context_denials}
                       if context_denials is not None else {})}
        envelope_schema = ENVELOPE_V2 if self.configured else ENVELOPE
        inp = OwnerInput(envelope_schema, canonical_json(envelope), 'DSH exact request and explicit immutable data snapshot')
        entry_inputs = {'request': inp}
        if self.account_plan is not None:
            from cpn.plugins.account_bindings import PUBLISHER, account_binding_input
            entry_inputs[PUBLISHER] = account_binding_input(self.account_plan)
        host_bindings = self._host_bindings()
        owner = start_run(
            module, registration(
                _DshExecutor(self),
                self.managed_tools if self.configured else None,
                managed_result_reader=self.managed_result_reader,
                managed_result_delivery=self.managed_result_delivery,
                account_bindings=self.account_plan is not None),
            run_dir=attachment.attempt_path, task_input=inp,
            resource_inputs=({MANAGED_RESULT_IMPORT_SYMBOL: OwnerInput(
                MANAGED_OPERATION_RESULT_IMPORT_SCHEMA,
                canonical_json(imported_results),
                'Exact selected terminal result deliveries from committed main turns')}
                if imported_results is not None else None),
            entry_inputs=entry_inputs, budgets=ModuleBudgetDeclaration(
                tuple(module.to_dict()['budget_buckets']),
                ('rpnh/module_declaration/v1',), self.attempt_budget, 0,
                self.attempt_budget, 0),
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
        from cpn.plugins.account_bindings import verify_account_binding_plan
        verify_account_binding_plan(path, self._catalog() or SchemaCatalog(), self.account_plan,
            provider_selection=self.managed_account_provider)
        managed_registration_keys = (
            self._persisted_managed_registration_keys(path)
            if self.configured else None)
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
                self.managed_tools if self.configured else None,
                managed_registration_keys=managed_registration_keys,
                managed_result_reader=self.managed_result_reader,
                managed_result_delivery=self.managed_result_delivery,
                account_bindings=self.account_plan is not None),
            run_dir=path,
            model_condition=self._model_condition,
            host_execution_bindings=self._host_bindings(),
            catalog=self._catalog())
        return self._execute(owner, _parse_ref(state['thread_ref']), _parse_ref(state['active_turn_ref']))

    def _persisted_managed_registration_keys(self, run_dir):
        from cpn.rpnh.registry.module_runtime import hydrate_module_runtime

        core = _RegistryCore(
            run_dir, create=False, read_only=True, catalog=self._catalog())
        _executable, structure, _marking = hydrate_module_runtime(core)
        self._validate_configured_tool_interruption(structure)
        self._select_registered_result_reader(structure)
        persisted = set(structure.compiled.registrations.get('tool', {}))
        available = self.managed_tools.registration_declarations(
            include_legacy=True)
        return {
            declaration.registration_key for declaration in available
            if declaration.registration_key in persisted
        }

    def _validate_configured_tool_interruption(self, structure):
        """Reject unsafe old executable declarations before writer adoption.

        Persisted modules are never silently rewritten. Completed history stays
        readable, but an old tool return-to-input route cannot execute safely
        when an observation is lost during an owner stop.
        """
        if not self.configured:
            return
        for operation in structure.compiled.operations:
            if (operation.declaration.name == 'dsh.tool'
                    and any(outcome.name == 'interrupted'
                            for outcome in operation.declaration.outcomes)):
                raise ValueError(
                    'registered DSH tool interruption can replay unknown effects; '
                    'automatic continuation of this legacy active declaration '
                    'is unsupported')

    def _select_registered_result_reader(self, structure):
        """The exact registered operation snapshot owns result presentation."""
        operations = tuple(
            operation for operation in structure.compiled.operations
            if operation.declaration.name == 'dsh.tool')
        if len(operations) != 1:
            raise ValueError('DSH requires one registered tool operation')
        operation = operations[0]
        enabled = operation.executor_key in {
            MANAGED_RESULTS_EXECUTOR, MANAGED_RESULTS_TRANSFER_EXECUTOR}
        delivery = operation.executor_key == MANAGED_RESULTS_TRANSFER_EXECUTOR
        if self.managed_result_reader and not enabled:
            raise ValueError('cannot add a result reader to an existing run declaration')
        if enabled:
            config = operation.declaration.config
            if (config.get('managed_result_reader') is not True
                    or MANAGED_OPERATION_OUTPUT_READER_KEY
                    not in operation.declaration.tools
                    or type(config.get('context_tool_output_byte_limit')) is not int
                    or config['context_tool_output_byte_limit'] < 128):
                raise ValueError('registered result reader declaration is incomplete')
            if delivery and config.get('managed_result_import_symbol') != MANAGED_RESULT_IMPORT_SYMBOL:
                raise ValueError('registered result delivery lacks its exact import symbol')
            self.managed_result_output_bytes = config['context_tool_output_byte_limit']
            self.managed_history_retained_tokens = config.get('context_compaction_retained_tokens')
        self.managed_result_reader = enabled
        self.managed_result_delivery = delivery

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
                'tools': self._provider_tool_declarations()},
        )

    def _catalog(self):
        if not self.configured:
            return None
        schemas, types = registered_host_llm_schema_data()
        catalog = SchemaCatalog(schemas=schemas, types=types)
        if self.account_plan is not None:
            from cpn.plugins.account_bindings import register_account_binding_catalog
            register_account_binding_catalog(catalog)
        return catalog

    def _fit_optional_import_context(self, request, thread_state, imported_results,
                                     import_context, *, context_denials=None):
        """Original budgets decide both attempts; only optional discovery changes."""
        self._validate_configured_frame_budget(request, thread_state,
            context_denials=context_denials)
        try:
            self._validate_configured_frame_budget(request, thread_state,
                import_context=import_context, context_denials=context_denials)
        except DshContextBudgetError:
            count = len(imported_results['deliveries']) + len(import_context.get('omissions', []))
            imported_results = {**imported_results, 'deliveries': []}
            import_context = {**self._result_import_context(imported_results),
                'omission_summary': {'source_count': count,
                    'reason': 'import_discovery_frame_budget_exhausted'}}
            self._validate_configured_frame_budget(request, thread_state,
                import_context=import_context, context_denials=context_denials)
        return imported_results, import_context

    def _result_import_context(self, document):
        """Discovery metadata only; reader authority remains the original lease."""
        deliveries = document['deliveries']
        return {
            'kind': 'dsh_managed_result_import_context/v1',
            'source_count': len(deliveries),
            'entry_count': sum(len(product['document']['entries'])
                               for item in deliveries
                               for product in item['delivery']['products']),
            'read_arguments': {
                'imported_archive': True, 'offset': 0,
                'max_bytes': self.managed_result_output_bytes,
            },
        }

    @staticmethod
    def _committed_context_denials(thread_state, history_messages):
        """Derive presentation markers from the original committed transcript.

        A MainThread answer records conversation content, not proof of a
        physical tool outcome. Only its exact final model proposal may be
        presented as a denied conversation item. Native records are retained.
        """
        markers = []
        for item in thread_state['committed_history']:
            answer = item['answer']
            if not isinstance(answer, Mapping) or answer.get('status') != 'denied':
                continue
            records, messages = answer.get('calls'), answer.get('messages')
            if not isinstance(records, list) or not records or not isinstance(messages, list) or not messages:
                continue
            record = records[-1]
            ticket = record.get('ticket', {})
            message = messages[-1]
            if (ticket.get('kind') != 'model'
                    or not isinstance(message, Mapping)
                    or message.get('role') != 'assistant'
                    or not isinstance(message.get('content'), list)
                    or not any(block.get('type') == 'tool-call'
                               for block in message['content'] if isinstance(block, Mapping))):
                # A partial result tail, a request denial with no model, or an
                # unknown/current execution is never repaired by this view.
                continue
            if (record.get('observation', {}).get('message') != message
                    or len(messages) > len(history_messages)
                    or history_messages[:len(messages)] != messages
                    or ticket.get('request_id') != item['user_input']['request_id']
                    or ticket.get('session_id') != item['user_input']['session_id']):
                raise ValueError('committed denied proposal differs from its exact native transcript prefix')
            turn_ref, receipt_ref = item['turn_ref'], item['execution_receipt_ref']
            if (_parse_ref(turn_ref).entity_type != 'main_turn/v1'
                    or _parse_ref(receipt_ref).entity_type != 'main_turn_execution_receipt/v1'):
                raise ValueError('denied context requires its original committed turn and receipt refs')
            raw_execution = ticket.get('execution_ref')
            if (not isinstance(raw_execution, Mapping)
                    or set(raw_execution) != {'entity_type', 'entity_id', 'version_id'}):
                raise ValueError('denied context lacks its exact original model execution identity')
            execution_ref = {'entity_type': raw_execution['entity_type'],
                             'logical_id': raw_execution['entity_id'],
                             'version_id': raw_execution['version_id']}
            if _parse_ref(execution_ref).entity_type != 'operation_execution_lease/v1':
                raise ValueError('denied context model identity is not an operation execution')
            markers.append({
                'message_index': len(messages) - 1,
                'main_turn_ref': turn_ref,
                'execution_receipt_ref': receipt_ref,
                'model_execution_ref': execution_ref,
                'source_message': message,
            })
        return markers

    @staticmethod
    def _project_context_denials(state):
        """Replace only fixed prior transcript proposals in the model view."""
        messages = list(state['messages'])
        markers = state.get('model_context_denials', [])
        prefix = state.get('history_prefix_length', 0)
        if (not isinstance(markers, list) or type(prefix) is not int
                or not 0 <= prefix <= len(messages)):
            raise ValueError('denied context requires its original history prefix')
        indices = set()
        for marker in markers:
            if (not isinstance(marker, Mapping) or set(marker) != {
                    'message_index', 'main_turn_ref', 'execution_receipt_ref',
                    'model_execution_ref', 'source_message'}):
                raise ValueError('denied context marker is not an exact committed transcript projection')
            index = marker['message_index']
            if (type(index) is not int or not 0 <= index < prefix
                    or index in indices or marker['source_message'] != messages[index]):
                raise ValueError('denied context marker differs from its fixed prior message')
            for key, kind in (
                    ('main_turn_ref', 'main_turn/v1'),
                    ('execution_receipt_ref', 'main_turn_execution_receipt/v1'),
                    ('model_execution_ref', 'operation_execution_lease/v1')):
                if _parse_ref(marker[key]).entity_type != kind:
                    raise ValueError('denied context source identity is mistyped')
            message = messages[index]
            if (message.get('role') != 'assistant'
                    or not isinstance(message.get('content'), list)
                    or not any(isinstance(block, Mapping) and block.get('type') == 'tool-call'
                               for block in message['content'])
                    or (index + 1 < len(messages)
                        and (messages[index + 1].get('role') == 'tool'
                             or messages[index + 1].get('source', {}).get('kind') == 'tool'))):
                raise ValueError('denied context cannot replace a current or partially answered batch')
            capsule = {
                'kind': 'dsh_denied_tool_batch_context/v1',
                'notice': (
                    'This committed conversation turn is recorded as denied. '
                    'Its original tool proposal remains in native history. '
                    'This context projection does not contain a corresponding tool result.'),
                **{key: marker[key] for key in (
                    'main_turn_ref', 'execution_receipt_ref', 'model_execution_ref')},
                'original_assistant_message': message,
            }
            messages[index] = {
                'id': f'dsh-denied-tool-batch-{index}',
                'role': 'user', 'source': {'kind': 'context'},
                'content': [{'type': 'text', 'text': canonical_json(capsule).decode('utf-8')}],
            }
            indices.add(index)
        return messages

    def _validate_configured_frame_budget(self, request, thread_state, *,
                                         import_context=None, context_denials=None):
        """Reject a request before writer admission if its response cannot fit."""
        history_messages = (
            thread_state['committed_history'][-1]['answer']['messages']
            if thread_state['committed_history'] else [])
        messages = [*history_messages, *request['messages']]
        if self.managed_result_reader:
            messages = self._model_messages({
                'messages': messages,
                'history_prefix_length': len(history_messages),
                **({'managed_result_import': import_context}
                   if import_context is not None else {}),
                **({'model_context_denials': context_denials}
                   if context_denials is not None else {}),
            })
        if not self._configured_frame_budget_fits(
                route=request['route'], messages=messages,
                request_id=request['request_id']):
            raise DshContextBudgetError(
                'configured provider response budget exceeds the bounded '
                'DSH capability frame')

    @staticmethod
    def _history_message_groups(messages):
        """Keep each original assistant batch and all results indivisible."""
        groups, index = [], 0
        while index < len(messages):
            message = messages[index]
            group = [message]
            index += 1
            if message.get('role') == 'user':
                while index < len(messages) and messages[index].get('role') == 'user':
                    group.append(messages[index])
                    index += 1
            elif (message.get('role') == 'assistant'
                    and any(block.get('type') == 'tool-call'
                            for block in message.get('content', ()))):
                while index < len(messages):
                    following = messages[index]
                    if (following.get('role') != 'tool'
                            and following.get('source', {}).get('kind') != 'tool'):
                        break
                    group.append(following)
                    index += 1
            elif (message.get('role') == 'tool'
                    or message.get('source', {}).get('kind') == 'tool'):
                raise ValueError('DSH context contains an orphaned tool result')
            groups.append(group)
        return groups

    def _model_messages(self, state, *, reserved_bytes=0):
        """A bounded projection; original messages and calls remain intact.

        The original AgentLoop UTF-8/4 helper supplies only the optional
        retention estimate.  The independent effect limit is checked using
        actual encoded bytes.  Completed tool groups are never split, and a
        removed current-run result remains discoverable at an exact published
        firing cut through the original Registry products.
        """
        messages = (self._project_context_denials(state)
                    if self.managed_result_delivery else list(state['messages']))
        if not self.managed_result_reader:
            return messages
        if type(reserved_bytes) is not int or reserved_bytes < 0:
            raise ValueError('context reservation requires a nonnegative byte count')
        byte_limit = MAX_BYTES // 8
        token_limit = self.managed_history_retained_tokens
        import_context = state.get('managed_result_import')
        import_notice = None
        if (self.managed_result_delivery and isinstance(import_context, Mapping)
                and (import_context.get('source_count', 0) > 0
                     or import_context.get('omissions') or import_context.get('omission_summary'))):
            import_notice = {
                'id': 'dsh-managed-result-import', 'role': 'user',
                'source': {'kind': 'context'},
                'content': [{'type': 'text', 'text': canonical_json({
                    **import_context,
                    'notice': (
                        (('Omitted sources have no local imported copy and cannot be read. '
                          if import_context.get('omissions') or import_context.get('omission_summary') else '') +
                        'Earlier main-turn results explicitly delivered into this '
                        'run are available as a fixed local registered copy. List '
                        'imported_archive to obtain its exact import_resource_ref '
                        'and original call locators; retain both when reading a '
                        'body. Old nested locators alone do not authorize access '
                        'to another run. Conversation history remains unchanged.')),
                }).decode('utf-8')}],
            }

        def with_import_notice(value):
            return [import_notice, *value] if import_notice is not None else value

        def encoded(value):
            return json.dumps(value, ensure_ascii=True, allow_nan=False,
                              separators=(',', ':')).encode('utf-8')

        def fits(value, *, use_estimate=True):
            raw = encoded(with_import_notice(value))
            return (len(raw) + reserved_bytes <= byte_limit
                    and (not use_estimate or token_limit is None
                         or approximate_tokens(raw) + (reserved_bytes + 3) // 4
                         <= token_limit))

        if fits(messages):
            return with_import_notice(messages)
        archive = state.get('managed_result_archive')
        if not isinstance(archive, Mapping):
            # A prior main turn has a different child Registry.  Its nested
            # result refs are not imports, and cannot justify discarding the
            # only inline view while claiming a current-run reader can recover
            # them.  Fresh input still follows the exact byte admission bound.
            if fits(messages, use_estimate=False):
                return with_import_notice(messages)
            raise DshContextBudgetError('DSH input exceeds the bounded model context without a current-run result archive')
        groups = self._history_message_groups(messages)
        if not groups:
            raise ValueError('DSH model context requires the original user input')
        current_user = next((index for index in range(len(groups) - 1, -1, -1)
                             if groups[index][0].get('role') == 'user'), None)
        mandatory = {len(groups) - 1}
        if current_user is not None:
            mandatory.add(current_user)
        prefix_length = state.get('history_prefix_length', 0)
        if (type(prefix_length) is not int or not 0 <= prefix_length <= len(messages)):
            raise ValueError('DSH prior-turn prefix differs from its admitted input')
        consumed = 0
        for index, group in enumerate(groups):
            if consumed < prefix_length:
                mandatory.add(index)
            consumed += len(group)

        def project(indices, *, omitted_count=None):
            recent = [message for index, group in enumerate(groups)
                      if index in indices for message in group]
            capsule = {
                'kind': 'dsh_managed_history_context/v1',
                'scope': 'current_registered_run_only',
                'notice': (
                    'Earlier completed results in this run remain in the exact '
                    'result directory. Read its pages to find original call '
                    'locators, then read the result body by that unchanged locator. '
                    'Previous main turns require their separately delivered local '
                    'copy, when listed; their nested result refs alone do not '
                    'grant this reader access.'),
                'archive': archive,
                'read_arguments': {
                    'archive_firing_ref': archive['archive_firing_ref'],
                    'offset': 0, 'max_bytes': self.managed_result_output_bytes,
                },
                'omitted_message_count': (len(messages) - len(recent)
                                          if omitted_count is None else omitted_count),
            }
            notice = {
                'id': 'dsh-managed-result-archive', 'role': 'user',
                'source': {'kind': 'context'},
                'content': [{'type': 'text',
                             'text': canonical_json(capsule).decode('utf-8')}],
            }
            return [notice, *recent]

        selected = set(mandatory)
        projected = project(selected)
        if not fits(projected, use_estimate=False):
            raise DshContextBudgetError('current DSH user input and complete tool batch exceed the bounded model context')
        # Count each original message once. Re-encoding every growing candidate
        # suffix would turn long-history presentation into quadratic work.
        # A notice with the largest possible omission count is a conservative
        # envelope; its decimal count can only shrink in the final projection.
        notice = project(set(), omitted_count=len(messages))[0]
        group_bytes = [sum(len(encoded(message)) + 1 for message in group)
                       for group in groups]
        selected_bytes = 2 + len(encoded(notice)) + sum(
            group_bytes[index] for index in selected)
        if import_notice is not None:
            selected_bytes += len(encoded(import_notice)) + 1

        def candidate_fits(size):
            # Same UTF-8/4 fallback as approximate_tokens, computed from the
            # exact byte count here to avoid allocating another history copy.
            return (size + reserved_bytes <= byte_limit
                    and (token_limit is None or
                         (size + 3) // 4 + (reserved_bytes + 3) // 4 <= token_limit))

        # Protect the current user's input and complete newest batch even when
        # their bytes exceed the optional tokenizer-independent retention
        # estimate.  They must still satisfy the exact transport byte bound.
        for index in range(len(groups) - 1, -1, -1):
            if index in selected:
                continue
            candidate_bytes = selected_bytes + group_bytes[index]
            if candidate_fits(candidate_bytes):
                selected.add(index)
                selected_bytes = candidate_bytes
            else:
                break
        return with_import_notice(project(selected))

    def _configured_frame_budget_fits(self, *, route, messages, request_id,
                                      extra_encoded_bytes=0):
        """Bound one exact future configured model response frame."""
        arguments = {
            'route': route,
            'messages': messages,
            'step': 0,
            'registered_tools': self._provider_tool_declarations(),
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
            self.selection.input_target.max_response_bytes) + extra_encoded_bytes <= MAX_BYTES

    def _configured_batch_budget_fits(self, state, calls):
        """Account for all results and exact message/record wrappers at once.

        Plugin limits count already encoded UTF-8 JSON. Re-encoding it as an
        ASCII-escaped JSON string expands by at most three; next model frames
        repeat messages twice. The reply reserves one original value and seven
        text regions: content, observation message, history message, two next
        model argument copies and two conservative wire-request copies.
        No result-sized dummy strings are allocated here.
        """
        if self.managed_result_reader:
            return self._configured_result_pages_fit(state, calls)
        projected_messages = list(state['messages'])
        projected_records = list(state['calls'])
        result_bytes = 0
        wide_ref = {'entity_type': 'x' * 128, 'entity_id': 'x' * 128,
                    'version_id': 'x' * 128}
        ticket = {
            'protocol': PROTOCOL, 'session_id': self.session_id,
            'request_id': state['request_id'], 'execution_ref': wide_ref,
            'start_event_id': 'x' * 128, 'transition': 'dsh.tool',
            'input_refs': [{'resource_id': 'x' * 128,
                            'resource_version_id': 'x' * 128}],
            'upstream_revision': REVISION, 'kind': 'tool',
        }
        for call in calls:
            _plugin, operation = self.managed_tools.binding(call['name'])
            declaration = self.managed_tools.declaration(call['name'])
            result_bytes += operation.max_result_bytes
            content = [{'type': 'text', 'text': ''}]
            message = make_tool_result(
                message_id=f"result-{call['id']}", call_id=call['id'],
                content=content, is_error=False, revision=REVISION)
            projected_messages.append(message)
            projected_records.append({
                'ticket': ticket,
                'arguments': {'call': call, 'data': state['data']},
                'observation': {
                    'value': None, 'content': content, 'message': message,
                    'isError': False,
                    'managed_result': {
                        'schema_version': 'x' * 128,
                        'name': declaration.name,
                        'selector': declaration.selector,
                        'call_id': call['id'], 'effect': declaration.effect,
                        'outcome': 'x' * 128,
                        'admitted_at_utc': 'x' * 128,
                        'started_receipt_ref': ticket['input_refs'][0],
                        'terminal_receipt_ref': ticket['input_refs'][0],
                        'error': {'code': 'x' * 128},
                    },
                },
            })
        legacy_single = (len(calls) == 1
                         and self.managed_tool_policy is None
                         and self._active_managed_policy is None)
        if not self._configured_frame_budget_fits(
                route=state['route'], messages=projected_messages,
                request_id=state['request_id'],
                extra_encoded_bytes=(4 if legacy_single else 6) * result_bytes):
            return False
        if legacy_single:
            # Preserve the old single-call/no-policy admission contract.
            # Its large-history/Unicode transport limits remain a separate
            # known coverage gap, not a reason to shrink existing calls here.
            return True
        # Reserve the next actual model record as well. Its arguments contain
        # two copies of messages. A provider-native message never exceeds two
        # copies of its DSH source message: text blocks flatten, and the extra
        # function wrapper is smaller than the original call block itself.
        # Use that conservative shape for wire_request without implementing a
        # second provider codec or retaining another execution authority.
        future_arguments = {
            'route': state['route'], 'messages': projected_messages,
            'step': state['step'],
            'registered_tools': self._provider_tool_declarations(),
            'request': {**state['route'], 'messages': projected_messages,
                        'sessionId': self.session_id,
                        'maxTokens': self.selection.input_target.max_output_tokens},
        }
        future_message = {
            'id': 'x' * 128, 'role': 'assistant',
            'source': {'kind': 'model', **state['route']}, 'content': [],
        }
        projected_records.append({
            'ticket': {**ticket, 'kind': 'model', 'transition': 'dsh.model'},
            'arguments': future_arguments,
            'observation': {
                'message': future_message, 'chunks': [],
                'finish': {'kind': 'tool-calls'},
                'wire_request': {
                    'protocol': 'registered_llm/v1',
                    'messages': [*projected_messages, *projected_messages],
                    'tools': self._provider_tool_declarations(),
                    'tool_choice': 'auto', 'placeholders': [],
                },
            },
        })
        reply = {
            'protocol': PROTOCOL, 'kind': 'reply', 'id': 'x' * 128,
            'result': {'status': 'terminal', 'replayed': False,
                       'receipt_ref': wide_ref,
                       'answer': {**state,
                                  'messages': [*projected_messages, future_message],
                                  'calls': projected_records,
                                  'step': state['step'] + 1,
                                  'status': 'completed', 'text': ''}},
        }
        return (len(json.dumps(reply, allow_nan=False,
                               separators=(',', ':')).encode('utf-8'))
                + 24 * result_bytes + self._next_response_projection_bound()
                <= MAX_BYTES)

    def _configured_result_pages_fit(self, state, calls):
        """Bound model-visible pages, never a business result's storage limit.

        Complete native history is transported in bounded reply fragments.
        Its original records remain immutable products; only model-facing
        messages enter this effect-frame budget.
        """
        page_bytes = self.managed_result_output_bytes
        projected_messages = list(state['messages'])
        for ordinal, call in enumerate(calls):
            if call['name'] != MANAGED_RESULT_READER:
                # A locator has fixed-width Registry references, but call IDs
                # are provider data. Check the full actual ID before physical
                # admission so a small presentation limit cannot lose a result.
                zeros = '0' * 32
                placeholder = {
                    'kind': 'managed_operation_output_page/v1',
                    'reader': MANAGED_RESULT_READER,
                    'caller_execution_ref': {
                        'entity_type': 'operation_execution_lease/v1',
                        'logical_id': 'operation_execution_lease:' + zeros,
                        'version_id': 'operation_execution_lease_version:' + zeros},
                    'caller_firing_ref': {
                        'entity_type': 'transition_firing/v1',
                        'logical_id': 'transition_firing:' + zeros,
                        'version_id': 'transition_firing_version:' + zeros},
                    'result_resource_ref': {
                        'resource_id': 'resource:' + zeros,
                        'resource_version_id': 'resource_version:' + zeros},
                    'terminal_receipt_ref': {
                        'resource_id': 'resource:' + zeros,
                        'resource_version_id': 'resource_version:' + zeros},
                    'call_id': call['id'], 'content': '\\',
                    'offset_chars': 0, 'next_offset_chars': 1,
                    'total_chars': MAX_MANAGED_TOOL_RESULT_BYTES * 3,
                    'truncated': True,
                }
                try:
                    fit_managed_output_page(placeholder, page_bytes)
                except ValueError:
                    return False
                locator = {key: placeholder[key] for key in (
                    'caller_execution_ref', 'caller_firing_ref',
                    'result_resource_ref', 'terminal_receipt_ref', 'call_id')}
                if len(canonical_json(self._failed_result_notice(locator))) > page_bytes:
                    return False
            projected_messages.append(make_tool_result(
                message_id=f"result-{'0' * 32}-{ordinal}", call_id=call['id'],
                content=[{'type': 'text', 'text': ''}],
                is_error=False, revision=REVISION))
        # Page serialization itself is bounded. Its nested text has at most
        # three times that encoded size; the two model DTO copies need 6B.
        try:
            messages = self._model_messages(
                {**state, 'messages': projected_messages},
                reserved_bytes=3 * page_bytes * len(calls))
        except ValueError:
            return False
        return self._configured_frame_budget_fits(
            route=state['route'], messages=messages,
            request_id=state['request_id'],
            extra_encoded_bytes=6 * page_bytes * len(calls))

    def _next_response_projection_bound(self):
        """Bound one normalized response in message/chunks/pending projections.

        The registered response is canonical ASCII JSON bounded by R. Message,
        history and chunks use at most 3R; raw pending argument aliases 2R;
        final text R. Parsed argument aliases use at most 16R: finite Python
        float repr is at most 24 characters and its shortest JSON token has
        at least three, so each parsed copy is bounded by 8R.
        Compute block-wrapper overhead separately from the minimum legal call
        size, rather than omitting many tiny tool-call blocks from the bound.
        """
        limit = self.selection.input_target.max_response_bytes
        encoded_size = lambda value: len(json.dumps(
            value, ensure_ascii=True, separators=(',', ':')).encode('utf-8'))
        minimum_call = encoded_size({'id': 'x', 'name': 'x', 'arguments': '{}'})
        count = limit // minimum_call
        block = {'type': 'tool-call', 'id': '', 'name': '', 'arguments': ''}
        pending = {**block, 'arguments': {}, 'raw_arguments': ''}
        start = {'type': 'block-start', 'index': count + 2, 'blockType': 'tool-call'}
        end = {'type': 'block-end', 'index': count + 2, 'block': block}
        per_call = (2 * encoded_size(block) + encoded_size(start)
                    + encoded_size(end) + 2 * encoded_size(pending) + 16)
        text_wrappers = 0
        for kind in ('text', 'reasoning'):
            text = {'type': kind, 'text': ''}
            text_wrappers += (2 * encoded_size(text)
                              + encoded_size({'type': 'block-start', 'index': count + 1,
                                              'blockType': kind})
                              + encoded_size({'type': 'block-end', 'index': count + 1,
                                              'block': text}) + 16)
        finish_wrapper = encoded_size({'type': 'finish', 'reason': {'kind': 'tool-calls'}}) + 8
        return 22 * limit + count * per_call + text_wrappers + finish_wrapper

    def _commit(self, thread_ref, turn_ref, output):
        receipt = self.thread.record_execution_receipt(turn_ref=turn_ref, idempotency_key='dsh:receipt')
        from .result_delivery import is_managed_result_terminal
        if is_managed_result_terminal(
                self.thread, turn_ref=turn_ref, receipt_ref=receipt):
            output = self._terminal_answer_projection(output)
        self.thread.commit_terminal_answer(thread_ref=thread_ref, turn_ref=turn_ref, receipt_ref=receipt,
            answer=output, idempotency_key='dsh:answer')
        return {'status': 'terminal', 'answer': output, 'receipt_ref': _identity(receipt), 'replayed': False}

    @staticmethod
    def _terminal_answer_projection(output):
        """Keep the selected terminal's body delivery out of parent/UI copies.

        Selection is checked against the original terminal executor before
        this pure projection. The receipt pins the full selected product;
        conversation messages and native execution records remain untouched.
        """
        delivery = output['managed_result_delivery']
        validate_managed_operation_result_delivery(delivery)
        answer = {key: value for key, value in output.items()
                  if key != 'managed_result_delivery'}
        answer['managed_result_delivery_summary'] = {
            'schema_version': 'managed_operation_result_delivery_summary/v1',
            **{key: delivery[key] for key in (
                'source_task_ref', 'source_run_ref', 'producer_execution_ref',
                'producer_firing_ref', 'source_operation_binding_ref')},
            'product_count': len(delivery['products']),
            'entry_count': sum(len(product['document']['entries'])
                               for product in delivery['products']),
        }
        return answer

    @staticmethod
    def _pending_calls(state):
        calls = state.get('pending_tools')
        return tuple(calls) if calls is not None else (state['pending_tool'],)

    def _managed_declaration(self, execution, call):
        declarations = tuple(
            declaration for declaration in
            self.managed_tools.registration_declarations(include_legacy=True)
            if (declaration.name == call['name']
                and declaration.registration_key in
                execution.operation.spec.allowed_tool_ids))
        if len(declarations) != 1:
            raise ValueError('DSH tool firing lacks one exact current or legacy registration')
        return declarations[0]

    def _project_managed_batch(self, execution, input_payloads):
        """Owner-only projection from the original committed Petri input."""
        if (execution.operation.spec.executor_key not in {
                    MANAGED_BATCH_EXECUTOR, MANAGED_RESULTS_EXECUTOR,
                    MANAGED_RESULTS_TRANSFER_EXECUTOR}):
            raise ValueError('managed batch requires its registered DSH tool operation')
        state = json.loads(_dsh_state_payload(execution, input_payloads))
        if state['session_id'] != self.session_id:
            raise ValueError('managed batch input belongs to another DSH session')
        return tuple(ManagedToolCall(
            ordinal, call['name'], call['id'], call['arguments'],
            self._managed_declaration(execution, call).registration_key)
            for ordinal, call in enumerate(self._pending_calls(state)))

    def _registered_managed_policy(self, owner):
        """Resume uses registered policy; a new constructor cannot replace it."""
        from cpn.rpnh.registry.module_runtime import hydrate_module_runtime

        _executable, structure, _marking = hydrate_module_runtime(owner._core)
        self._validate_configured_tool_interruption(structure)
        self._select_registered_result_reader(structure)
        operations = tuple(operation for operation in structure.compiled.operations
                           if (operation.executor_key in {
                                   MANAGED_BATCH_EXECUTOR, MANAGED_RESULTS_EXECUTOR,
                                   MANAGED_RESULTS_TRANSFER_EXECUTOR}
                               and 'managed_tool_policy' in operation.declaration.config))
        if not operations:
            if self.managed_tool_policy is not None:
                raise ValueError('cannot add managed scheduling to an existing unconfigured run')
            return None
        if len(operations) != 1:
            raise ValueError('DSH requires one registered managed tool operation')
        policy = managed_scheduler_policy_from_document(
            operations[0].declaration.config['managed_tool_policy'])
        if (self.managed_tool_policy is not None
                and policy.identity() != self.managed_tool_policy.identity()):
            raise ValueError('managed scheduling differs from the registered run policy')
        return policy

    def _execute(self, owner, thread_ref, turn_ref):
        policy = self._registered_managed_policy(owner)
        account_selections = None
        if self.account_plan is not None:
            from cpn.plugins.account_bindings import prepare_account_bindings
            account_selections = prepare_account_bindings(owner, self.account_plan,
                provider_selection=self.managed_account_provider)['dsh']
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
            kernel, repository = owner.operation_repository()
            from cpn.plugins.account_credentials import bind_installed_account_resolvers
            account_resolvers = bind_installed_account_resolvers(
                self.managed_account_resolver_factory, owner=owner,
                catalog=self.managed_tools, node_id=None, account_selections=account_selections)
            self.managed_tool_service = ManagedPluginInvocationService(
                owner, kernel, repository, self.managed_tools,
                account_resolvers=account_resolvers)
            self._active_managed_policy = policy
            self.managed_output_service = (
                ManagedOperationOutputReader(self.managed_tool_service)
                if self.managed_result_reader else None)
            batch_service = None
            if policy is not None:
                self.managed_run_capacity = ManagedRunCapacity(policy.max_in_flight)
                batch_service = ManagedOperationBatchService(
                    self.managed_tool_service, self._project_managed_batch)
            services = ExecutionServices(
                owner=owner, event_loop=loop, llm_input_port=port,
                interruption_requested=lambda: self.cancelled,
                managed_operation_batches=batch_service,
                managed_operation_outputs=self.managed_output_service)

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
            self.managed_output_service = None
            if self.managed_run_capacity is not None:
                self.managed_run_capacity.close()
                self.managed_run_capacity = None
            self._active_managed_policy = None
            if port is not None:
                port.close()
            loop.close()
        if result.stop_reason != 'terminal':
            return {'status': result.stop_reason, 'answer': None}
        ref, = result.goal_resource_refs
        output = json.loads(owner._core.object_store.read_registered(owner._core.get_version(ref.resource_version_id)))
        return self._commit(thread_ref, turn_ref, output)

    @staticmethod
    def _managed_observation(call, result=None, error=None, *, render=True):
        if error is None:
            output = result['output']
            evidence = {key: value for key, value in result.items()
                        if key != 'output'}
            is_error = False
        elif isinstance(error, ManagedPluginInvocationFailed):
            output = {'error': error.code}
            evidence = json_copy(error.evidence or {})
            is_error = True
        else:
            # Unknown, cancellation and admission faults are not fabricated
            # model-visible "known tool failure" results.
            raise error
        observation = {'value': output, 'isError': is_error,
                       'managed_result': evidence}
        return (DshBackend._render_tool_observation(call, observation)
                if render else observation)

    @staticmethod
    def _render_tool_observation(call, observation):
        content = [{'type': 'text',
                    'text': canonical(observation['value']).decode('utf-8')}]
        return {**observation, 'content': content,
                'message': make_tool_result(
                    message_id=f"result-{call['id']}", call_id=call['id'],
                    content=content, is_error=observation['isError'],
                    revision=REVISION)}

    @staticmethod
    def _failed_result_notice(locator):
        """A bounded notice; the original code stays in evidence and product."""
        return {
            'kind': 'managed_operation_failure_notice/v1',
            **locator, 'reader': None, 'outcome': 'failed',
            'error': 'managed_plugin_failed', 'error_code_inline': False,
        }

    def _bounded_result_observation(self, call, entry, locator, observation):
        if not observation['isError']:
            value = render_managed_operation_output(
                entry, locator, max_bytes=self.managed_result_output_bytes)
            observation = {**observation, 'value': value}
        elif len(canonical_json(observation['value'])) > self.managed_result_output_bytes:
            notice = self._failed_result_notice(locator)
            if len(canonical_json(notice)) > self.managed_result_output_bytes:
                raise RuntimeError('known-failure notice exceeded its admitted presentation bound')
            observation = {**observation, 'value': notice}
        return self._render_tool_observation(call, observation)

    def _invoke_configured_tools(self, execution, state, gateway):
        if gateway is None or self.managed_tool_service is None:
            raise ValueError('configured DSH tools lack their owner-bound service')
        observations = []
        result_products = execution.operation.spec.executor_key in {
            MANAGED_RESULTS_EXECUTOR, MANAGED_RESULTS_TRANSFER_EXECUTOR}
        if result_products:
            # Byte count and typed identity do not prove a copied worker DTO's
            # payload. Both the scheduler and the reader/serial path must use
            # the exact input already claimed by this original operation.
            input_payloads = gateway.read_managed_operation_inputs_v1(execution)
            state = json.loads(_dsh_state_payload(execution, input_payloads))
            if state['session_id'] != self.session_id:
                raise ValueError('managed result input belongs to another DSH session')
        calls = self._pending_calls(state)
        has_reader = result_products and any(
            call['name'] == MANAGED_RESULT_READER for call in calls)
        if (execution.operation.spec.executor_key == MANAGED_BATCH_EXECUTOR
                or (result_products and self._active_managed_policy is not None
                    and not has_reader)):
            if self.managed_run_capacity is None:
                raise ValueError('registered DSH scheduling lacks its run capacity')
            scope = gateway.bind_managed_operation_batch_v1(execution)
            try:
                # Replies and raw arguments use the same original registered
                # input as owner admission, never the worker's copied payload.
                state = json.loads(_dsh_state_payload(execution, scope.input_payloads))
                calls = self._pending_calls(state)
                scheduler = ManagedToolScheduler(scope.policy, self.managed_run_capacity)
                batch = scheduler.run(
                    scope.calls,
                    operation_key=str(execution.operation_execution_lease_ref.version_id),
                    prepare=lambda call: gateway.prepare_managed_operation_call_v1(
                        scope, call.ordinal),
                    finish=lambda prepared, completion:
                        gateway.finish_managed_operation_call_v1(
                            scope, prepared, completion),
                    cancelled=lambda: self.cancelled)
                if batch.reconciliation_required:
                    from cpn.plugins.managed_tools import (
                        ManagedPluginInvocationReconciliationRequired,
                    )
                    for item in batch.outcomes:
                        if isinstance(item.error, ManagedPluginInvocationReconciliationRequired):
                            # Preserve the original call and exact receipt
                            # evidence, as the existing single-call path does.
                            raise item.error
                    raise ManagedPluginInvocationReconciliationRequired(
                        'managed DSH batch has an unknown outcome; original receipts retained')
                if batch.cancelled:
                    raise RuntimeError('managed DSH batch cancelled; original receipts retained')
                if (tuple(item.ordinal for item in batch.outcomes)
                        != tuple(range(len(calls)))):
                    raise RuntimeError('managed scheduler omitted an original DSH call')
                for call, item in zip(calls, batch.outcomes, strict=True):
                    if item.call_id != call['id'] or item.not_started:
                        raise RuntimeError('managed DSH batch did not settle every original call')
                    observations.append(self._managed_observation(
                        call, item.result, item.error,
                        render=not result_products))
            finally:
                gateway.release_managed_operation_batch_v1(scope)
        else:
            calls = self._pending_calls(state)
            for call in calls:
                if self.cancelled:
                    raise RuntimeError('DSH batch cancelled before its next call')
                if result_products and call['name'] == MANAGED_RESULT_READER:
                    if self.managed_output_service is None:
                        raise ValueError('result reader is outside this exact tool declaration')
                    try:
                        result = gateway.invoke_registered_tool(
                            execution, MANAGED_OPERATION_OUTPUT_READER_KEY,
                            identity=json_copy(MANAGED_OPERATION_OUTPUT_READER_IDENTITY),
                            contracts=json_copy(MANAGED_OPERATION_OUTPUT_READER_CONTRACTS),
                            kwargs={
                                'service': self.managed_output_service,
                                'execution': execution,
                                'arguments': call['arguments'],
                            })
                        is_error = False
                    except ManagedOperationOutputReadDenied:
                        # A pure HOST read can fail in a known way without
                        # inventing a managed invocation receipt or revealing
                        # another scope's resource body.
                        result = {'error': 'managed_result_read_denied'}
                        is_error = True
                    observations.append(self._render_tool_observation(
                        call, {'value': result, 'isError': is_error}))
                    continue
                declaration = self._managed_declaration(execution, call)
                try:
                    result = gateway.invoke_registered_tool(
                        execution, declaration.registration_key,
                        identity=json_copy(declaration.identity),
                        contracts=json_copy(declaration.contracts),
                        kwargs={
                            'service': self.managed_tool_service,
                            'execution': execution, 'call_id': call['id'],
                            'arguments': call['arguments'],
                            'interruption_requested': lambda: self.cancelled,
                        })
                    observation = self._managed_observation(
                        call, result, render=not result_products)
                except ManagedPluginInvocationFailed as exc:
                    observation = self._managed_observation(
                        call, error=exc, render=not result_products)
                observations.append(observation)
        if result_products:
            entries = tuple({
                'call': call,
                'name': call['name'], 'call_id': call['id'],
                'terminal_receipt_ref': observation['managed_result']['terminal_receipt_ref'],
                'output': observation['value'],
                'managed_result': observation['managed_result'],
            } for call, observation in zip(calls, observations, strict=True)
                if call['name'] != MANAGED_RESULT_READER)
            return 'complete', 'ready_output', _DshManagedProducts(
                state, tuple(calls), tuple(observations), entries)
        return 'complete', 'ready_output', self._append_tool_records(
            execution, state, calls, observations)

    def _project_managed_products(self, execution, products, result_ref):
        from cpn.rpnh.registry.publication import _ref_payload

        entries = {entry['call_id']: entry for entry in products.entries}
        observations = []
        for call, observation in zip(products.calls, products.observations, strict=True):
            if call['name'] == MANAGED_RESULT_READER:
                observations.append(observation)
                continue
            entry = entries[call['id']]
            locator = managed_operation_output_locator(
                execution, result_ref, entry)
            observations.append(self._bounded_result_observation(
                call, entry, locator, observation))
        state = self._append_tool_records(
            execution, products.state, products.calls, observations)
        return {**state, 'managed_result_archive': {
            'kind': 'managed_operation_result_archive/v1',
            'reader': MANAGED_RESULT_READER,
            'archive_firing_ref': _ref_payload(
                execution.operation.firing.transition_firing_ref),
            'result_resource_ref': {
                'resource_id': str(result_ref.resource_id),
                'resource_version_id': str(result_ref.resource_version_id)},
        }}

    def _append_tool_records(self, execution, state, calls, observations):
        ticket = {
            'protocol': PROTOCOL, 'session_id': self.session_id,
            'request_id': state['request_id'],
            'execution_ref': _identity(execution.operation_execution_lease_ref),
            'start_event_id': str(execution.start_event_id),
            'transition': execution.operation.firing.transition_id,
            'input_refs': [_identity(item.resource_ref)
                           for item in execution.operation.inputs],
            'upstream_revision': REVISION, 'kind': 'tool',
        }
        records, messages = list(state['calls']), list(state['messages'])
        for ordinal, (call, observation) in enumerate(zip(calls, observations, strict=True)):
            if execution.operation.spec.executor_key in {
                    MANAGED_RESULTS_EXECUTOR, MANAGED_RESULTS_TRANSFER_EXECUTOR}:
                # Provider call IDs belong to model batches and may repeat.
                # A display message gets its own exact execution/ordinal ID;
                # neither the call identity nor raw arguments are rewritten.
                token = str(execution.operation_execution_lease_ref.version_id).rsplit(':', 1)[-1]
                observation = {**observation, 'message': {
                    **observation['message'], 'id': f'result-{token}-{ordinal}'}}
            records.append({'ticket': ticket,
                            'arguments': {'call': call, 'data': state['data']},
                            'observation': observation})
            messages.append(observation['message'])
        return {**state, 'calls': records, 'messages': messages}

    def _invoke(self, execution, state, *, gateway=None, registered_llm=None):
        stage = execution.operation.firing.transition_id.rsplit('.',1)[-1]
        if execution.operation.spec.executor_key == MANAGED_TERMINAL_EXECUTOR:
            if (stage not in {'finalize', 'reject_request', 'reject_tool'}
                    or gateway is None or self.managed_output_service is None):
                raise ValueError('result delivery requires its original terminal operation')
            payloads = gateway.read_managed_operation_inputs_v1(execution)
            if len(payloads) != 1:
                raise ValueError('result delivery requires one exact terminal input')
            state = json.loads(payloads[0])
            if state['session_id'] != self.session_id:
                raise ValueError('terminal input belongs to another DSH session')
            delivery = gateway.invoke_registered_tool(
                execution, MANAGED_OPERATION_RESULTS_EXPORT_TOOL,
                identity=json_copy(MANAGED_OPERATION_RESULTS_EXPORT_IDENTITY),
                contracts=json_copy(MANAGED_OPERATION_RESULTS_EXPORT_CONTRACTS),
                kwargs={'service': self.managed_output_service, 'execution': execution})
            state = {**state, 'managed_result_delivery': delivery}
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
            calls = self._pending_calls(state)
            if self.configured:
                allow = bool(calls)
                for call in calls:
                    if (call['name'] not in policy.get('tools', [])
                            or call['name'] not in self.managed_tool_names):
                        allow = False
                        break
                    try:
                        if self.managed_result_reader and call['name'] == MANAGED_RESULT_READER:
                            schema = (MANAGED_OPERATION_TRANSFER_INPUT_SCHEMA
                                      if self.managed_result_delivery
                                      else MANAGED_OPERATION_OUTPUT_INPUT_SCHEMA)
                        else:
                            schema = self.managed_tools.declaration(
                                call['name']).input_schema
                        validate(schema, call['arguments'])
                    except PluginError:
                        allow = False
                        break
                if allow:
                    allow = self._configured_batch_budget_fits(state, calls)
            else:
                allow = bool(calls) and all(call['name'] in policy.get('tools', [])
                         and call['name'] in {'read_dataset','sum_values'}
                         and ((call['name'] == 'read_dataset'
                               and call['arguments'] == {}) or
                              (call['name'] == 'sum_values'
                               and set(call['arguments']) == {'values'}
                               and call['arguments']['values']
                               == state.get('read_values')))
                         for call in calls)
            return ('allow' if allow else 'deny'), 'tool_decision_output', state
        if stage == 'finalize':
            blocks = state['messages'][-1]['content']
            text = ''.join(b['text'] for b in blocks if b['type'] == 'text')
            return 'complete', 'done', {**state, 'status': 'completed', 'text': text}
        if stage not in {'model', 'tool'}:
            raise ValueError('undeclared capability stage')
        if stage == 'tool' and self.configured:
            return self._invoke_configured_tools(execution, state, gateway)
        ticket = {'protocol': PROTOCOL, 'session_id': self.session_id, 'request_id': state['request_id'],
                  'execution_ref': _identity(execution.operation_execution_lease_ref),
                  'start_event_id': str(execution.start_event_id), 'transition': execution.operation.firing.transition_id,
                  'input_refs': [_identity(i.resource_ref) for i in execution.operation.inputs],
                  'upstream_revision': REVISION, 'kind': stage}
        model_messages = self._model_messages(state) if stage == 'model' and self.configured else state['messages']
        args = ({'route': state['route'], 'messages': model_messages, 'step': state['step'],
                 **({'registered_tools': self._provider_tool_declarations()}
                    if self.configured else {}),
                 'request': {**state['route'], 'messages': model_messages, 'sessionId': self.session_id,
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

        if stage == 'model' and self.configured:
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
        if stage == 'model' and self.configured:
            # The exact input and registered provider request already retain
            # the executed material.  History needs the observed message, not
            # another copy of every prior message inside each model record.
            # Keep the original input locator so the projection is traceable.
            history_call = {
                'ticket': ticket,
                'arguments': {
                    'route': args['route'], 'step': args['step'],
                    'request_input_ref': ticket['input_refs'][0],
                },
                'observation': {
                    'message': observation.get('message'),
                    'finish': observation.get('finish'),
                },
            }
        else:
            history_call = {'ticket': ticket, 'arguments': args,
                            'observation': observation}
        state = {**state, 'calls': [*state['calls'], history_call]}
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
            if len(calls) > 1 and not self.configured:
                raise ValueError('offline conformance capability tickets admit one call per firing')
            finish = observation.get('finish')
            expected_finish = 'tool-calls' if calls else 'stop'
            if (not isinstance(finish, Mapping)
                    or finish.get('kind') != expected_finish):
                raise RuntimeError(
                    'model response did not finish with the admitted outcome')
            state = {**state, 'messages': [*state['messages'], message], 'step': state['step'] + 1}
            if calls:
                pending = []
                identities = set()
                for call in calls:
                    call_id = call.get('id')
                    if (not isinstance(call_id, str) or not call_id.strip()
                            or len(call_id) > 512 or call_id in identities
                            or not isinstance(call.get('name'), str)
                            or not call['name']):
                        raise ValueError('model batch requires unique bounded call identities')
                    identities.add(call_id)
                    raw_arguments = call.get('arguments')
                    parsed = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                    if not isinstance(parsed, dict):
                        raise ValueError('tool arguments must be a JSON object')
                    pending.append({**call, 'raw_arguments': raw_arguments, 'arguments': parsed})
                updated = {**state, 'pending_tools': pending}
                if len(pending) == 1:
                    updated['pending_tool'] = pending[0]
                else:
                    updated.pop('pending_tool', None)
                return 'tool', 'response_output', updated
            return 'answer', 'response_output', state
        call = state['pending_tool']
        is_error = observation.get('isError')
        if type(is_error) is not bool or 'value' not in observation:
            raise RuntimeError('admitted DSH tool execution did not settle')
        if call['name'] == 'read_dataset' and not is_error:
            state['read_values'] = observation['value']
        tool_message = observation.get('message')
        try:
            decoded = decode_tool_result(
                tool_message, REVISION, require_error=True)
        except ValueError as exc:
            raise ValueError(
                'tool message is not correlated with the admitted tool call') from exc
        if (decoded['source'] != {'kind': 'tool', 'callId': call['id']}
                or decoded['callId'] != call['id']
                or decoded['isError'] is not is_error):
            raise ValueError('tool message is not correlated with the admitted tool call')
        return 'complete', 'ready_output', {**state, 'messages': [*state['messages'], tool_message]}
