"""Strict public data ABI, not executable authority or a purity proof.

The original installer is trusted to declare finite code/data dependencies.
Registry validation proves exact declared facts, never arbitrary Python safety.
"""
from __future__ import annotations
import hashlib
import json
import math
from .registry.runtime_binding_contracts import copy_candidate_document

C_ENCODING = 'rpnh/public-json-c14n/v1'
ORIGINAL_ENCODING = 'original-json-bytes/v1'
OPAQUE_ENCODING = 'opaque-public-bytes/v1'
CONTRACT = 'rpnh/installed_public_material_contract/v1'
OBSERVATION = 'rpnh/public_implementation_observation/v1'
SELECTION = 'rpnh/public_material_selection/v1'
POLICY = 'rpnh/public_execution_policy/v1'
INVENTORY = 'rpnh/registered_public_material_inventory/v1'
PRODUCER = 'rpnh/registry_public_material_producer/v1'
DOMAIN = 'rpnh/registered-public-material-digest/v1'
SCHEMAS = (CONTRACT, OBSERVATION, SELECTION, POLICY, INVENTORY)
MODULE_SCHEMAS = (CONTRACT, OBSERVATION, SELECTION, INVENTORY)
JSON_CAP = 1048576
INLINE_CAP = 8 * JSON_CAP
AGGREGATE_CAP = 16 * JSON_CAP


def strict_copy(value):
    # Reuse the original exact-builtin/cycle/finite-float boundary.
    result = copy_candidate_document({'value': value})['value']
    def scalar(v):
        if type(v) is str:
            v.encode('utf-8', errors='strict')
        elif type(v) is dict:
            for k, child in v.items(): scalar(k); scalar(child)
        elif type(v) is list:
            for child in v: scalar(child)
    scalar(result)
    return result


def _int_text(value):
    if not value: return '0'
    negative = value < 0; value = abs(value); parts = []
    while value:
        value, remainder = divmod(value, 1000000000)
        parts.append(remainder)
    return ('-' if negative else '') + str(parts[-1]) + ''.join(f'{v:09d}' for v in reversed(parts[:-1]))


def _parse_int(text):
    negative = text.startswith('-'); text = text[1:] if negative else text
    value = 0
    for start in range(0, len(text), 9):
        chunk = text[start:start+9]; value = value * 10 ** len(chunk) + int(chunk)
    return -value if negative else value


def canonical(value):
    value = strict_copy(value)
    def string(s):
        return '"' + ''.join('\\"' if c == '"' else '\\\\' if c == '\\' else
            '\\u%04x' % ord(c) if ord(c) < 32 else c for c in s) + '"'
    def encode(v):
        if v is None: return 'null'
        if type(v) is bool: return 'true' if v else 'false'
        if type(v) is int: return _int_text(v)
        if type(v) is float:
            if not v: return '0.0'
            n, d = v.as_integer_ratio(); k = d.bit_length() - 1
            digits = str(abs(n) * 5 ** k).rjust(k + 1, '0')
            text = digits[:-k] + '.' + digits[-k:] if k else digits + '.0'
            text = text.rstrip('0') if k else text
            if text.endswith('.'): text += '0'
            return ('-' if n < 0 else '') + text
        if type(v) is str: return string(v)
        if type(v) is list: return '[' + ','.join(map(encode, v)) + ']'
        return '{' + ','.join(string(k) + ':' + encode(v[k]) for k in sorted(v)) + '}'
    return encode(value).encode('utf-8')


def decode(raw, *, canonical_required=False):
    if type(raw) is not bytes or len(raw) > JSON_CAP:
        raise ValueError('public JSON requires bounded immutable bytes')
    def pairs(rows):
        value = {}
        for key, child in rows:
            if key in value: raise ValueError('duplicate public JSON key')
            value[key] = child
        return value
    def constant(_): raise ValueError('nonfinite public JSON')
    result = strict_copy(json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant, parse_int=_parse_int))
    if canonical_required and canonical(result) != raw:
        raise ValueError('noncanonical public JSON bytes')
    return result


def sha(raw):
    if type(raw) is not bytes: raise TypeError('digest needs bytes')
    return hashlib.sha256(raw).hexdigest()


def ordered(rows, key=lambda v: v):
    keys = [key(v) for v in rows]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ValueError('public set-like array must be sorted and unique')


def validate(schema, value):
    from .registry.schema_catalog import SchemaCatalog
    value = strict_copy(value)
    # jsonschema permits 1.0 as integer; public ABI explicitly does not.
    from jsonschema import Draft7Validator, validators
    checker = Draft7Validator.TYPE_CHECKER.redefine('integer', lambda _, v: type(v) is int)
    checker = checker.redefine('number', lambda _, v: type(v) in (int, float))
    StrictValidator = validators.extend(Draft7Validator, type_checker=checker)
    doc = json.loads(SchemaCatalog._mechanical_schema_path(schema).read_text())
    StrictValidator(doc).validate(value)
    if len(canonical(value)) > JSON_CAP: raise ValueError('public JSON exceeds fixed ABI cap')
    return value


def _dag(nodes, dependencies, roots):
    active, done = set(), set()
    def visit(node, depth):
        if depth > 256: raise ValueError('public graph depth cap')
        if node not in nodes: raise ValueError('dangling public dependency')
        if node in active: raise ValueError('cyclic public dependency')
        if node in done: return
        active.add(node)
        for child in dependencies(node): visit(child, depth + 1)
        active.remove(node); done.add(node)
    for root in roots: visit(root, 1)
    return done


def validate_contract(contract):
    contract = validate(CONTRACT, contract)
    ordered(contract['supported_payloads'])
    ordered(contract['implementation_units'], lambda v: v['unit_id'])
    units = {v['unit_id']: v for v in contract['implementation_units']}
    assets = []
    for unit in units.values():
        ordered(unit['asset_ids']); ordered(unit['dependency_unit_ids'])
        assets += unit['asset_ids']
        for field in ('name','version'):
            if unit['distribution'][field].strip() != unit['distribution'][field] or unit['distribution'][field] in ('latest','*'):
                raise ValueError('unfixed implementation distribution identity')
    if len(assets) > 16384 or len(set(assets)) != len(assets): raise ValueError('asset identity/cap')
    _dag(units, lambda n: units[n]['dependency_unit_ids'], units)
    for group in ('preparation_roots','execution_roots'):
        for rows in contract[group].values():
            ordered(rows)
            if not set(rows) <= units.keys(): raise ValueError('unknown implementation root')
    for role in ('profile_factory','registration_factory','lowerer','serializer','compiler','catalog_factory'):
        if not contract['preparation_roots'][role]: raise ValueError('Module preparation role must be declared')
    ordered(contract['registrations'], lambda v: (v['kind'],v['key']))
    ordered(contract['public_slots'], lambda v: v['slot_id'])
    slots = {s['slot_id']:s for s in contract['public_slots']}
    for entry in contract['registrations']:
        ordered(entry['implementation_unit_ids']); ordered(entry['public_slot_ids'])
        if not set(entry['implementation_unit_ids']) <= units.keys() or not set(entry['public_slot_ids']) <= slots.keys():
            raise ValueError('Registration declaration dependency missing')
        if entry['kind'] != 'schema' and not entry['implementation_unit_ids']: raise ValueError('undeclared callable')
    for slot in slots.values():
        rule = slot['selection_rule']; kind = rule['kind']
        if (kind == 'always' and (rule['payload_kind'] is not None or rule['registration_key'] is not None)
            or kind == 'payload' and (rule['payload_kind'] is None or rule['registration_key'] is not None)
            or kind == 'registration' and (rule['payload_kind'] is not None or rule['registration_key'] is None)):
            raise ValueError('invalid finite public selection rule')
    ordered(contract['binding_slots'], lambda v: v['slot_id'])
    for role in ('resolver','renderer'):
        ids = []
        for slot in contract['binding_slots']:
            ordered(slot['service_ids']); ordered(slot['account_binding_ids'])
            cap = slot[role]; ids.append(cap['id'])
            if cap['implementation_unit_id'] not in units: raise ValueError('credential capability unit missing')
        if len(ids) != len(set(ids)): raise ValueError('ambiguous credential capability')
    ordered(contract['supported_shape']['module_operations'])
    return contract


def selected_units(contract, pairs):
    units = {u['unit_id']:u for u in contract['implementation_units']}
    roots = set(v for group in ('preparation_roots','execution_roots') for rows in contract[group].values() for v in rows)
    roots.update(slot[role]['implementation_unit_id'] for slot in contract['binding_slots'] for role in ('resolver','renderer'))
    roots.update(v for e in contract['registrations'] if (e['kind'],e['key']) in pairs for v in e['implementation_unit_ids'])
    return _dag(units, lambda n: units[n]['dependency_unit_ids'], roots)


def selected_slots(contract, pairs, child_kind='module'):
    result = []
    for slot in contract['public_slots']:
        rule = slot['selection_rule']
        if (rule['kind'] == 'always' or rule['kind'] == 'payload' and rule['payload_kind'] == child_kind
            or rule['kind'] == 'registration' and (rule['registration_key']['kind'],rule['registration_key']['key']) in pairs):
            result.append(slot)
    selected_ids={v['slot_id'] for v in result}
    required={sid for entry in contract['registrations'] if (entry['kind'],entry['key']) in pairs for sid in entry['public_slot_ids']}
    if not required<=selected_ids:raise ValueError('selected Registration public dependency is not covered by finite slot rules')
    return result


def public_digest(child_kind, contract_sha, inventory, roots, bindings):
    return sha(canonical({'domain':DOMAIN,'child_kind':child_kind,'installed_contract_sha256':contract_sha,
        'content_inventory':inventory,'roots':roots,'opaque_bindings':bindings}))


def validate_policy(policy):
    policy = validate(POLICY, policy)
    from .runtime_policy import runtime_policy_from_document
    if type(policy['runtime']['context_pressure_trigger_ratio']) is not float: raise ValueError('ratio must be binary64 float')
    if canonical(runtime_policy_from_document(policy['runtime']).as_document()) != canonical(policy['runtime']):
        raise ValueError('runtime defaults may not be silently expanded by consumer')
    route = policy['route_provenance'][0]
    import re
    for name in ('model_condition',):
        if policy[name]!=policy[name].strip(): raise ValueError('public model must be trimmed')
    for name in ('route_id','provider','backend','protocol','endpoint','outbound_model'):
        value=route[name]
        if value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value): raise ValueError('public route text is invalid')
    for effort in [*policy['supported_reasoning_efforts'],policy['reasoning_effort'],policy['default_reasoning_effort']]:
        if effort is not None and re.fullmatch(r'[a-z0-9][a-z0-9._-]*',effort) is None: raise ValueError('public reasoning effort is invalid')
    if route['outbound_model'] != policy['model_condition']: raise ValueError('public policy requires exact model')
    from urllib.parse import urlsplit
    endpoint = urlsplit(route['endpoint'])
    endpoint.port  # Reject malformed or out-of-range ports before construction.
    import ipaddress
    try: loopback=ipaddress.ip_address(endpoint.hostname).is_loopback
    except ValueError: loopback=bool(endpoint.hostname and endpoint.hostname.lower()=='localhost')
    if (endpoint.scheme != route['transport'] or not endpoint.hostname or endpoint.username is not None or endpoint.password is not None or endpoint.query or endpoint.fragment
        or endpoint.scheme == 'http' and not loopback):
        raise ValueError('invalid public route endpoint')
    ordered(policy['supported_reasoning_efforts'])
    for name in ('reasoning_effort','default_reasoning_effort'):
        if policy[name] is not None and policy[name] not in policy['supported_reasoning_efforts']: raise ValueError('reasoning selection unavailable')
    window, retained = policy['context_window_tokens'], policy['context_compaction_retained_tokens']
    if window is not None and retained is not None and retained >= window: raise ValueError('invalid context retention')
    nulls = [route[k] is None for k in ('account_binding_id','secret_binding_id','credential_renderer_id')]
    if any(nulls) and not all(nulls): raise ValueError('half-null credential binding')
    return policy


def module_operation_shapes(module, declarations):
    """Logical operation names remain distinct from exact executor identities.

    Plugin names come only from the original plugin declaration. Ordinary
    Module names come only from OperationDeclaration.name. A malformed plugin
    declaration never falls back to an ordinary name, and mixed identities
    cannot collide under one selected logical name.
    """
    lookup={(d['kind'],d['key']):d for d in declarations}
    identities={}
    for component in module.components:
        for operation in component.operations:
            declaration=lookup.get(('executor',operation.executor))
            if declaration is None: raise ValueError('selected executor is undeclared')
            contracts=declaration['contracts']
            if 'native_plugin' in contracts:
                plugin=contracts['native_plugin']
                if type(plugin) is not dict or type(plugin.get('operation')) is not dict:
                    raise ValueError('malformed plugin identity cannot become ordinary Module')
                name=plugin['operation'].get('name'); selector=plugin.get('selector')
                if type(name) is not str or type(selector) is not str or selector.rsplit('/',1)[-1]!=name:
                    raise ValueError('plugin logical name differs from original declaration')
                identity=('plugin',operation.executor,selector)
            else:
                name=operation.name;identity=('module',operation.executor)
            if name in identities and identities[name]!=identity:
                raise ValueError('ambiguous same-name operation executor/declaration source')
            identities[name]=identity
    return identities


def verified_policy_identity(contract, policy, bindings, observations):
    """Pure exact joins; these facts alone confer no execution permission."""
    contract = validate_contract(contract)
    policy = validate_policy(policy)
    from .registry.schema_catalog import SchemaCatalog
    from jsonschema import Draft7Validator
    schema = json.loads(SchemaCatalog._mechanical_schema_path(INVENTORY).read_text())
    if type(bindings) is not list or type(observations) is not dict:
        raise TypeError('public binding/observation inputs require exact JSON containers')
    bindings = strict_copy(bindings)
    observations = strict_copy(observations)
    Draft7Validator(schema['properties']['opaque_bindings']).validate(bindings)
    ordered(bindings, lambda row: row['binding_id'])
    observations = {key: validate(OBSERVATION, value) for key, value in observations.items()}
    units = {unit['unit_id']: unit for unit in contract['implementation_units']}
    for key, value in observations.items():
        if (key != value['unit_id'] or key not in units
                or value['contract_id'] != contract['contract_id']
                or value['contract_revision'] != contract['revision']
                or value['installed_contract_sha256'] != sha(canonical(contract))
                or value['distribution'] != units[key]['distribution']
                or value['mode'] != units[key]['mode']
                or value['runtime']['abi_tag'] != units[key]['runtime_abi']
                or [a['asset_id'] for a in value['assets']] != units[key]['asset_ids']):
            raise ValueError('public capability observation contract mismatch')
    adapter = observations.get(policy['implementation_unit_id'])
    if adapter is None or sha(canonical(adapter)) != policy['implementation_digest']:
        raise ValueError('public adapter observation mismatch')
    route = policy['route_provenance'][0]
    result = {'policy': policy, 'binding': None, 'adapter_observation': adapter,
              'resolver': None, 'renderer': None}
    if route['secret_binding_id'] is None:
        if bindings: raise ValueError('unauthenticated policy has credential bindings')
        return result
    if len(bindings) != 1 or bindings[0]['binding_id'] != route['secret_binding_id']:
        raise ValueError('public policy requires its one exact opaque binding')
    binding = bindings[0]
    if (binding['service_id'] != route['service_id']
            or binding['account_binding_id'] != route['account_binding_id']
            or binding['renderer_id'] != route['credential_renderer_id']):
        raise ValueError('public credential route join mismatch')
    slots = [slot for slot in contract['binding_slots'] if slot['slot_id'] == binding['slot_id']]
    if len(slots) != 1: raise ValueError('opaque binding slot unavailable')
    slot = slots[0]
    if binding['service_id'] not in slot['service_ids'] or binding['account_binding_id'] not in slot['account_binding_ids']:
        raise ValueError('opaque service/account outside installed slot')
    result['binding'] = binding
    for role in ('resolver', 'renderer'):
        capability = slot[role]
        unit = binding[role + '_unit_id']
        observation = observations.get(unit)
        if (capability['id'] != binding[role + '_id']
                or capability['revision'] != binding[role + '_revision']
                or capability['implementation_unit_id'] != unit
                or observation is None
                or sha(canonical(observation)) != binding[role + '_implementation_sha256']):
            raise ValueError('opaque capability identity mismatch')
        result[role] = {'identity': {
            'contract_id': contract['contract_id'], 'contract_revision': contract['revision'],
            'role': role, 'capability_id': capability['id'],
            'capability_revision': capability['revision'], 'implementation_unit_id': unit,
            'implementation_observation_sha256': sha(canonical(observation))},
            'observation': observation}
    return result


def validate_public_backend(document):
    """Validate nested public mode at original producer and every reader."""
    document = strict_copy(document)
    policy = document.get('selection')
    if type(policy) is not dict: return document
    version = policy.get('schema_version')
    if type(version) is not str or not version.startswith('rpnh/public_execution_policy/'):
        return document
    policy = validate_policy(policy)
    route = policy['route_provenance'][0]
    if document.get('schema_version') not in ('optional_agent_execution_provenance/v1','registered_host_execution_provenance/v1'):
        raise ValueError('public backend outer schema is unsupported')
    expected = {'schema_version':document['schema_version'],'model':policy['model_condition'],'backend':policy['adapter_kind'],
        'timeout_seconds':policy['timeout_seconds'],'selection':policy,'transport_kind':route['transport'],'response_protocol':'llm_response_envelope/v1'}
    if canonical(document)!=canonical(expected): raise ValueError('public backend differs from full exact policy')
    return document
