"""Offline, nonoverwriting SCB publication collector. Requires settled-run consent.

Run only with the accepted task venv after the parent confirms terminal:
  upstream-venv/bin/python -B work/scb-publication/collect.py --collect --terminal-confirmed
No owner, provider, container, grader, source program or subprocess is executed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import traceback
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent
TASK = Path('/home/deng123/RPNH/task-first-wave-examples-20261008')
RUN = Path('/home/deng123/RPNH/.s26/scb01')
ACCEPTED_PREFIX = TASK / 'upstream-venv'
CHECKPOINTS = tuple(f'checkpoint_{n}' for n in range(1, 4))
ROOT_FILES = ('condition.json', 'environment.json', 'definition.json',
              'input-identities.json', 'endpoint-condition.json', 'development-summary.json')
CHECKPOINT_FILES = ('request.json', 'before.json', 'after.json', 'result.json',
                    'rpnh-result.json', 'commands.jsonl', 'failure.json', 'evaluation.json',
                    'evaluation/stdout.txt', 'evaluation/stderr.txt', 'evaluation/report.json')
# Explicit preparation inputs, never whole configuration/profile/build directories.
PREPARATION_FILES = ('private/scb-real01.log', 'evidence/scb-real01-exit.json',
    'work/scb-real-plan01/adaptation.json', 'work/scb-real-plan01/condition.json',
    'work/scb-real-plan01/docker-python3.12-uv-eval-host.yaml',
    'work/scb-base-build01/Dockerfile', 'work/scb-base-build01/result.json',
    'work/scb-base-build01/owner.json', 'evidence/scb-base-build01.log',
    'evidence/scb-real-env-install01.json', 'evidence/scb-real-env-install01.log',
    'work/pinned-sources-preparation.json', 'evidence/scb-real-install-byte-identity.json',
    'work/scb-base-build02/Dockerfile', 'work/scb-base-build02/build-compatibility.diff',
    'work/scb-base-build02/owner.json', 'work/scb-base-build02/result.json',
    'evidence/scb-base-build02.log')
DENIED_SCHEMAS = ('llm_execution_target', 'provider_backend', 'execution_selection',
    'adapter_config', 'transport_contract', 'credential', 'plugin_configuration')
OBJECT_FAMILIES = frozenset(('llm_call_spec', 'llm_invocation_spec', 'llm_invocation_attempt',
    'provider_attempt_spec', 'provider_payload_materialization_receipt', 'registered_host_llm_attempt',
    'agent_action', 'agent_turn', 'invocation', 'operation_result', 'run_terminal_evidence',
    'run_execution_authority', 'native_run_identity'))


def selected_object_types(types):
    return sorted(kind for kind in types if re.fullmatch(r'[^/]+/v[0-9]+', kind)
                  and kind.split('/')[0] in OBJECT_FAMILIES)


def call_count_projection(counts):
    """Label the accounting API tuple; neither position classifies real/fake."""
    settled, excess = counts
    return {'settled_total_calls': settled, 'post_limit_excess_calls': excess}


class ExcludedConfigurationResource(Exception):
    """Excluded by declared schema before reading its private payload."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode()


def canonical_hash(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode())


def confined(root, name):
    root = Path(root)
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('relative path required')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('path escapes selected root')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink forbidden')
    return path


def write_new(root, name, data):
    path = confined(root, name)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def resource_refs(value):
    if isinstance(value, dict):
        if all(isinstance(value.get(k), str) for k in ('resource_id', 'resource_version_id')):
            yield {k: value[k] for k in ('resource_id', 'resource_version_id')}
        elif value.get('entity_type') == 'resource_version/v1' and all(
                isinstance(value.get(k), str) for k in ('logical_id', 'version_id')):
            yield {'resource_id': value['logical_id'], 'resource_version_id': value['version_id']}
        else:
            for child in value.values():
                yield from resource_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from resource_refs(child)
    elif isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            yield from resource_refs(json.loads(value))
        except ValueError:
            pass


class Redactor:
    HEADER = re.compile(rb'''(?i)(?:authorization|proxy-authorization)(?:\\?["'])?\s*[:=]\s*(?:\\?["'])?\s*(?:Bearer|Basic)\s+(?P<value>[A-Za-z0-9+/_=.-]+)''')
    COOKIE = re.compile(rb'''(?i)(?:^|[\r\n{"'])cookie(?:\\?["'])?\s*[:=]\s*(?:\\?["'])?(?P<value>[A-Za-z0-9_.-]+=[^\r\n"'\\]+)''')
    SUSPECT = re.compile(rb'''(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret)(?:\\?["'])?\s*[:=]\s*(?:\\?["'])(?P<value>[A-Za-z0-9+/_=.-]{8,})(?:\\?["'])''')

    def __init__(self):
        self.atoms = {}
        self.proxy_variable_names = []

    def add(self, value, category):
        if not isinstance(value, str) or not value:
            return
        for variant in (value, json.dumps(value, ensure_ascii=True)[1:-1], quote(value, safe='')):
            self.atoms.setdefault(variant.encode(), set()).add(category)

    @staticmethod
    def placeholder(value):
        return any(word in value.lower() for word in
                   (b'redacted', b'placeholder', b'synthetic', b'dummy', b'your_', b'not_available'))

    def inspect_headers(self, data):
        for regex, category in ((self.HEADER, 'authorization_credential'), (self.COOKIE, 'cookie_credential')):
            for match in regex.finditer(data):
                if not self.placeholder(match['value']):
                    self.add(match['value'].decode(), category)

    def inspect_profile(self, document):
        credentials = re.compile(r'^(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret|secret[_-]?key)$', re.I)
        endpoints = re.compile(r'(?:endpoint|base[_-]?url|api[_-]?base|server[_-]?url)$', re.I)
        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(child, str):
                        if credentials.search(key):
                            self.add(child, 'confirmed_configured_credential')
                        if endpoints.search(key) and urlsplit(child).scheme in ('http', 'https', 'ws', 'wss'):
                            # Configured route values remain private; diagnostic paths do not.
                            self.add(child, 'configured_private_endpoint')
                        if key.lower() in ('authorization', 'cookie'):
                            self.inspect_headers(encoded({key: child}))
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(document)
        argv = document.get('argv', [])
        for i, token in enumerate(argv):
            if not isinstance(token, str):
                continue
            flag, _, inline = token.partition('=')
            value = inline or (argv[i + 1] if i + 1 < len(argv) else None)
            if flag in ('--api-key', '--access-token', '--password', '--client-secret'):
                self.add(value, 'confirmed_configured_credential')
            if flag in ('--endpoint', '--base-url', '--api-base'):
                self.add(value, 'configured_private_endpoint')

    def inspect_proxies(self, environ):
        # Explicit parent authorization: proxy values/endpoint atoms in memory only.
        # Never dump the environment or follow ambient auth files.
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            value = environ.get(key)
            if not value:
                continue
            self.proxy_variable_names.append(key)
            self.add(value, 'confirmed_proxy_value')
            parsed = urlsplit(value)
            if parsed.hostname:
                host = ('[' + parsed.hostname + ']') if ':' in parsed.hostname else parsed.hostname
                endpoint = host + (':' + str(parsed.port) if parsed.port else '')
                self.add(endpoint, 'confirmed_private_proxy_endpoint')
                if parsed.scheme:
                    self.add(parsed.scheme + '://' + endpoint, 'confirmed_private_proxy_endpoint')

    def redact(self, data):
        spans = []
        for atom, categories in self.atoms.items():
            start = 0
            while (at := data.find(atom, start)) >= 0:
                spans.append([at, at + len(atom), set(categories)])
                start = at + len(atom)
        uncertain = [{'category': 'unconfirmed_credential_literal',
                      'original_byte_range': list(m.span('value'))}
                     for m in self.SUSPECT.finditer(data)
                     if m['value'] not in self.atoms and not self.placeholder(m['value'])]
        merged = []
        for start, end, categories in sorted(spans, key=lambda x: x[0]):
            if merged and start < merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1]); merged[-1][2].update(categories)
            else:
                merged.append([start, end, categories])
        result, ledger, cursor = bytearray(), [], 0
        for start, end, categories in merged:
            result.extend(data[cursor:start]); output_start = len(result)
            result.extend(('[REDACTED:' + '+'.join(sorted(categories)) + ']').encode())
            ledger.append({'categories': sorted(categories), 'original_byte_range': [start, end],
                           'distributed_byte_range': [output_start, len(result)]})
            cursor = end
        result.extend(data[cursor:])
        return bytes(result), ledger, uncertain


def read_exact_resource(core, kernel, ref, resource_parser, version_parser):
    native = resource_parser(ref)
    failure = None
    try:
        prepared = kernel._prepared_reference(native)
        verification = 'exact_registered_reference_envelope_locator_size'
    except Exception as exc:
        # One observed historical shape, not a general bypass of failed integrity.
        if type(exc).__name__ != 'ResourceIntegrityFault' or not isinstance(exc.__cause__, KeyError) or exc.__cause__.args != ('agent_loop_ref',):
            raise
        prepared = kernel._exact_object(version_parser({'entity_type': 'resource_version/v1',
            'logical_id': ref['resource_id'], 'version_id': ref['resource_version_id']}),
            expected_type='resource_version/v1')
        failure = {'error_type': type(exc).__name__, 'missing_field': 'agent_loop_ref',
                   'status': 'PROVENANCE_CHECK_FAILED_BYTES_RECOVERED_BY_EXACT_OBJECT_API'}
        verification = 'exact_canonical_object_envelope_locator_size_only_provenance_failed'
    if (str(prepared.logical_id) != ref['resource_id']
            or str(prepared.version_id) != ref['resource_version_id']
            or prepared.metadata.get('resource_id') != ref['resource_id']
            or prepared.metadata.get('resource_version_id') != ref['resource_version_id']):
        raise ValueError('registered resource identity differs')
    if any(part in str(prepared.metadata.get('content_schema_ref', '')) for part in DENIED_SCHEMAS):
        raise ExcludedConfigurationResource('execution/provider/configuration payload not read')
    return prepared, core.object_store.read_registered(prepared), verification, failure


class Collector:
    def __init__(self, task=TASK, run=RUN, output=HERE / 'candidate'):
        self.task, self.run, self.output = Path(task), Path(run), Path(output)
        self.redactor = Redactor()
        self.staged, self.source_hashes = {}, {}
        self.missing, self.exclusions, self.errors = [], [], []
        self.registry, self.reference_failures, self.conditions = {}, [], {}
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.preparation_records = []
        self.created_output = False
        self.profile_names_loaded = []

    def read(self, root, name):
        path = confined(root, name)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('source is not a regular file')
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('source changed while reading')
        identity = (len(data), sha(data))
        if str(path) in self.source_hashes and self.source_hashes[str(path)] != identity:
            raise ValueError('previously observed source changed')
        self.source_hashes[str(path)] = identity
        return data

    def stage(self, name, data, origin, scope='byte_original_file', checkpoint=None):
        confined(self.output, name)
        row = {'data': data, 'origin': origin, 'representation_scope': scope, 'checkpoint': checkpoint}
        if name in self.staged and self.staged[name] != row:
            raise ValueError('duplicate destination differs')
        self.staged[name] = row

    def error(self, operation, origin, exc):
        # Retain the original diagnostic, not just an error-class replacement.
        # It undergoes the same final secret review as every other artifact.
        index = len(self.errors) + 1
        name = f'collection-errors/error-{index:03d}.txt'
        original = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)).encode()
        self.stage(name, original, {'operation': operation, 'source': origin},
                   'metadata_serialization_of_collector_exception')
        self.errors.append({'operation': operation, 'source': origin,
                            'error_type': type(exc).__name__, 'diagnostic_artifact': name})

    def copy(self, root, name, namespace, checkpoint=None):
        origin = {'root': 'run' if root == self.run else 'task', 'relative_path': name}
        try:
            raw = self.read(root, name)
            self.stage(f'{namespace}/{name}', raw, origin, checkpoint=checkpoint)
            return raw
        except FileNotFoundError:
            self.missing.append({**origin, 'status': 'NOT_FOUND'})
        except Exception as exc:
            self.error('copy_original_file', origin, exc)
        return None

    def profiles(self):
        self.redactor.inspect_proxies(os.environ)
        # Only frozen SCB-local documents. Do not follow adapter_config_path/auth refs.
        for name in ('selection.json', 'adapter.json', 'original-adapter.json'):
            path = confined(self.run, 'profile/' + name)
            if path.is_file():
                document = json.loads(self.read(self.run, 'profile/' + name))
                self.redactor.inspect_profile(document)
                self.profile_names_loaded.append(name)

    def snapshots(self, checkpoint, after):
        body = {key: value for key, value in after.items() if key != 'sha256'}
        if canonical_hash(body) != after['sha256']:
            raise ValueError('selected snapshot manifest identity differs')
        for row in after['files']:
            name = row['path']
            parts = Path(name).parts
            if (any(p in ('.git', '.venv', 'node_modules', '__pycache__', '.cache', 'profile') for p in parts)
                    or Path(name).suffix in ('.db', '.sqlite', '.sqlite3', '.tar', '.zip', '.pyc')
                    or Path(name).name in ('.env', 'auth.json', 'credentials.json')
                    or name.endswith(('-wal', '-shm'))):
                self.exclusions.append({'source': f'{checkpoint}/snapshot/{name}',
                                        'reason': 'snapshot_private_store_cache_or_archive'})
                continue
            path = confined(self.run / checkpoint / 'snapshot', name)
            raw = self.read(self.run, str(path.relative_to(self.run)))
            if len(raw) != row['size_bytes'] or sha(raw) != row['sha256']:
                raise ValueError('selected snapshot file hash/size differs')
            self.stage(f'run/{checkpoint}/snapshot/{name}', raw,
                {'root': 'run', 'relative_path': str(path.relative_to(self.run)),
                 'source_kind': 'solver_session_submission_not_upstream_reference_solution',
                 'selected_snapshot_sha256': after['sha256'], 'mode': stat.S_IMODE(path.stat().st_mode)},
                checkpoint=checkpoint)

    def files(self):
        for name in ROOT_FILES:
            self.copy(self.run, name, 'run')
        for name in (*PREPARATION_FILES, *self.preparation_records):
            self.copy(self.task, name, 'preparation')
        for number, checkpoint in enumerate(CHECKPOINTS, 1):
            directory = confined(self.run, checkpoint)
            if not directory.is_dir():
                self.conditions[checkpoint] = {'status': 'NO_RETAINED_CHECKPOINT_DIRECTORY', 'calls': None, 'grade': None}
                continue
            self.conditions[checkpoint] = {'status': 'RETAINED_CHECKPOINT', 'calls': None, 'grade': None}
            self.copy(self.run, f'lineage/record-{number:02}.json', 'run', checkpoint)
            for name in CHECKPOINT_FILES:
                raw = self.copy(self.run, f'{checkpoint}/{name}', 'run', checkpoint)
                if raw is not None and name in ('evaluation.json', 'rpnh-result.json', 'result.json'):
                    try:
                        data = json.loads(raw)
                        if name == 'evaluation.json':
                            self.conditions[checkpoint]['grade'] = {k: data.get(k) for k in
                                ('checkpoint_name', 'pass_counts', 'total_counts', 'pytest_exit_code',
                                 'pytest_collected', 'infrastructure_failure', 'duration')}
                        elif name == 'rpnh-result.json':
                            self.conditions[checkpoint]['owner_counts'] = data.get('actual_model_call_counts')
                    except Exception as exc:
                        self.error('interpret_original_record', f'{checkpoint}/{name}', exc)
            prompt = self.copy(self.run, f'rendered-{checkpoint}/prompt.txt', 'run', checkpoint)
            try:
                if prompt is not None:
                    request = json.loads(self.read(self.run, f'{checkpoint}/request.json'))
                    if sha(prompt) != request['prompt_sha256']:
                        raise ValueError('rendered prompt bytes differ from request prompt hash')
                if confined(self.run, f'{checkpoint}/after.json').is_file():
                    self.snapshots(checkpoint, json.loads(self.read(self.run, f'{checkpoint}/after.json')))
            except Exception as exc:
                self.error('verify_prompt_or_selected_snapshot', checkpoint, exc)
            logs = confined(self.run, f'{checkpoint}/control/logs')
            if logs.is_dir():
                for path in sorted(logs.glob('*.log')):
                    self.copy(self.run, str(path.relative_to(self.run)), 'run', checkpoint)
            self.copy(self.run, f'{checkpoint}/registry/adapter-private/llm-attempts.jsonl', 'run', checkpoint)

    def registry_checkpoint(self, checkpoint):
        # Deferred imports: --help and synthetic self-checks never import a live core.
        from cpn.rpnh.agent_tasks import agent_task_catalog
        from cpn.rpnh.registry._registry import _RegistryCore
        from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
        from cpn.rpnh.registry.publication import _resource_from_payload, _version_from_payload
        from cpn.rpnh.registry.run_authority import current_run_execution_authority
        directory = confined(self.run, f'{checkpoint}/registry')
        if not (directory / '.registry_v1/registry.sqlite3').is_file():
            self.missing.append({'checkpoint': checkpoint, 'kind': 'Registry', 'status': 'NOT_FOUND'})
            return
        core = _RegistryCore(directory, create=False, read_only=True, catalog=agent_task_catalog())
        kernel = _ResourceServiceKernel(core)
        head, epoch = core.event_store.max_ordinal(), core.event_store.writer_epoch
        try:
            _, authority = current_run_execution_authority(core, kernel)
        except Exception as exc:
            self.error('read_recorded_owner_authority', checkpoint, exc)
            authority = {'status': 'UNAVAILABLE'}
        db_hashes = {p: sha(p.read_bytes()) for p in
                     (directory / '.registry_v1/registry.sqlite3', directory / '.registry_v1/registry.sqlite3-wal') if p.is_file()}
        queue, seen, index, roles = [], set(), [], {}
        def enqueue(value, role):
            for ref in resource_refs(value):
                queue.append((ref, role))
        # Enumerate actual versioned kinds through the read-only connection;
        # do not assume ERP's v2 call/v1 provider-attempt inventory is complete.
        with core.event_store.connect() as db:
            observed_types = [str(row['object_type']) for row in db.execute('SELECT DISTINCT object_type FROM objects')]
        kinds = selected_object_types(observed_types)
        counts = {}
        for kind in kinds:
            rows = core.event_store.object_rows_by_type(kind); counts[kind] = len(rows)
            for row in rows:
                ref = {'entity_type': kind, 'logical_id': str(row['logical_id']), 'version_id': str(row['version_id'])}
                try:
                    obj = kernel._exact_object(_version_from_payload(ref), expected_type=kind)
                    raw = core.object_store.read_verified(obj)
                    source = core.object_store.path_for_version(obj.version_id)
                    if self.read(self.run, str(source.relative_to(self.run))) != raw:
                        raise ValueError('typed object bytes changed')
                    self.stage(f'objects/{checkpoint}/{kind.replace("/", "_")}/{str(obj.version_id).split(":",1)[-1]}.json',
                        raw, {'object_ref': ref, 'root': 'run', 'relative_path': str(source.relative_to(self.run))},
                        'byte_original_registered_object_payload', checkpoint)
                    data = dict(obj.metadata)
                    for field in ('request_resource_ref', 'semantic_prompt_resource_ref', 'request_recipe_ref', 'tool_catalog_ref'):
                        enqueue(data.get(field), 'request_recipe_or_context')
                    if kind.startswith('agent_action/'):
                        for field in ('arguments', 'raw_arguments', 'result_refs', 'result_metadata', 'input', 'output',
                                      'error', 'result', 'model_visible_result_ref', 'terminal_receipt_ref', 'tool_error_ref'):
                            enqueue(data.get(field), 'visible_tool_input_output')
                    if kind == 'run_terminal_evidence/v1':
                        enqueue(data.get('terminal_result_ref'), 'terminal_report')
                    if kind.split('/')[0] in ('invocation', 'operation_result'):
                        for field in ('input_refs', 'input_resource_refs', 'output_refs', 'result_refs',
                                      'output_resource_refs', 'result_resource_ref'):
                            enqueue(data.get(field), 'visible_tool_input_output')
                except Exception as exc:
                    self.error('read_exact_typed_object', {'checkpoint': checkpoint, 'ref': ref}, exc)
        events = []
        for event in core.event_store.list_events():
            if not event.event_type.startswith(('provider_attempt_', 'provider_payload_', 'llm_', 'agent_action_',
                    'agent_turn_', 'registered_operation_', 'resource_delivery_', 'run_terminal_')):
                continue
            payload = dict(event.payload)
            events.append({'event_id': str(event.event_id), 'ordinal': event.ordinal,
                           'event_type': event.event_type, 'payload': payload})
            if 'response_resource_ref' in payload:
                enqueue(payload['response_resource_ref'], 'adapter_return' if event.event_type.split('/')[0] ==
                        'provider_attempt_submission_observed' else 'normalized_response')
        self.stage(f'metadata/{checkpoint}/events.json', encoded(events),
            {'registry': f'{checkpoint}/registry', 'head': head, 'epoch': epoch},
            'metadata_serialization_of_stored_events', checkpoint)
        while queue:
            ref, role = queue.pop(0); identity = ref['resource_version_id']
            roles.setdefault(identity, set()).add(role)
            if identity in seen:
                continue
            seen.add(identity)
            try:
                obj, raw, verification, failure = read_exact_resource(core, kernel, ref, _resource_from_payload, _version_from_payload)
                schema = str(obj.metadata.get('content_schema_ref'))
                if any(part in schema for part in DENIED_SCHEMAS):
                    self.exclusions.append({'checkpoint': checkpoint, 'ref': ref, 'reason': 'execution_provider_configuration_resource'})
                    continue
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    body = None
                if isinstance(body, dict) and ({'argv', 'env', 'inherit_env'} <= body.keys()
                        or str(body.get('schema_version', '')).startswith(('llm_execution_selection/', 'local_process_adapter_config/'))):
                    self.exclusions.append({'checkpoint': checkpoint, 'ref': ref, 'reason': 'private_configuration_payload'})
                    continue
                source = core.object_store.path_for_version(obj.version_id)
                if self.read(self.run, str(source.relative_to(self.run))) != raw:
                    raise ValueError('registered resource bytes changed')
                name = f'resources/{checkpoint}/{identity.split(":",1)[-1]}' + ('.json' if body is not None else '.bin')
                self.stage(name, raw, {'root': 'run', 'relative_path': str(source.relative_to(self.run)), 'resource_ref': ref,
                    'schema_ref': schema, 'verification': verification}, 'byte_original_registered_resource', checkpoint)
                self.stage(f'metadata/{checkpoint}/resources/{identity.split(":",1)[-1]}.json', encoded(dict(obj.metadata)),
                    {'resource_ref': ref, 'registry': f'{checkpoint}/registry'}, 'metadata_serialization_of_resource_metadata', checkpoint)
                if failure:
                    self.reference_failures.append({'checkpoint': checkpoint, 'ref': ref, **failure})
                index.append({'ref': ref, 'path': name, 'verification': verification})
                if isinstance(body, dict) and body.get('schema_version') == 'logical_provider_request_recipe/v1':
                    for field in ('messages', 'source_prompt_ref', 'tool_catalog_ref', 'placeholders'):
                        enqueue(body.get(field), 'request_input_context')
                elif role in ('request_recipe_or_context', 'visible_tool_input_output', 'request_input_context'):
                    enqueue(body, 'request_input_context')
            except ExcludedConfigurationResource:
                self.exclusions.append({'checkpoint': checkpoint, 'ref': ref,
                    'reason': 'execution_provider_configuration_resource_payload_not_read'})
            except Exception as exc:
                self.error('read_exact_registered_resource', {'checkpoint': checkpoint, 'ref': ref}, exc)
        if (head, epoch) != (core.event_store.max_ordinal(), core.event_store.writer_epoch):
            raise ValueError('Registry head/epoch changed')
        if any(sha(path.read_bytes()) != identity for path, identity in db_hashes.items()):
            raise ValueError('Registry DB/WAL bytes changed')
        for row in index:
            row['roles'] = sorted(roles[row['ref']['resource_version_id']])
        actual_counts = list(core.event_store.actual_model_call_counts())
        self.registry[checkpoint] = {'head': head, 'epoch': epoch, 'stable': True,
            'recorded_execution_status': authority['status'], 'actual_model_call_counts': actual_counts,
            'call_count_projection': call_count_projection(actual_counts),
            'objects_by_type': counts, 'resources': index, 'events': len(events),
            'kind_discovery': 'actual DISTINCT object_type filtered to explicit model/tool/owner families; all observed versions included'}
        self.conditions[checkpoint]['calls'] = actual_counts
        self.conditions[checkpoint]['call_count_projection'] = call_count_projection(actual_counts)

    def finish(self, core_identity):
        for row in self.staged.values():
            self.redactor.inspect_headers(row['data'])
        records = []
        for name, row in self.staged.items():
            raw = row['data']; data, redactions, uncertain = self.redactor.redact(raw)
            if uncertain:
                # Do not pretend an unconfirmed literal is a confirmed credential.
                # Parent can supply a reviewed narrow atom and recollect in a new condition.
                self.exclusions.append({'source': row['origin'], 'proposed_path': name,
                    'reason': 'unconfirmed_credential_location_pending_parent_review',
                    'locations': uncertain, 'original_sha256': sha(raw), 'original_bytes': len(raw)})
                continue
            if any(atom in data for atom in self.redactor.atoms):
                raise ValueError('known secret remains')
            write_new(self.output, name, data)
            records.append({'path': name, 'origin': row['origin'], 'checkpoint': row['checkpoint'],
                'representation_scope': row['representation_scope'],
                'hash_scope': 'new_metadata_serialization_bytes' if row['representation_scope'].startswith('metadata_') else 'retained_source_bytes',
                'original_sha256': sha(raw), 'distributed_sha256': sha(data), 'original_bytes': len(raw),
                'distributed_bytes': len(data), 'changed_bytes': data != raw, 'redactions': redactions})
        unchanged = all(p.is_file() and (p.stat().st_size, sha(p.read_bytes())) == identity
                        for name, identity in self.source_hashes.items() for p in (Path(name),))
        if not unchanged:
            self.errors.append({'operation': 'final_source_check', 'error_type': 'SOURCE_CHANGED'})
        manifest = {'schema_version': 'task-local/scb-original-publication-candidate/v1',
            'privacy_review': 'PENDING_PARENT_REVIEW', 'collection_status': 'COMPLETE' if not self.errors else 'PARTIAL_WITH_ERRORS',
            'started_at': self.started_at, 'finished_at': datetime.now(timezone.utc).isoformat(),
            'source_roots': {'task': str(self.task), 'run': str(self.run)}, 'collector_core': core_identity,
            'trial_source_identity': {'status': 'USE_ORIGINAL_LAUNCH_ACCEPTANCE_RECORDS_ONLY',
                'tested_commit': None,
                'retained_attestation_artifact': 'preparation/evidence/scb-real-install-byte-identity.json'
                    if 'preparation/evidence/scb-real-install-byte-identity.json' in self.staged else None,
                'note': 'Preserve the attestation fields without reinterpretation. Current collector core or current product HEAD is not retroactive proof of launch bytes.'},
            'records': records, 'checkpoint_conditions': self.conditions, 'registry_verification': self.registry,
            'call_accounting_semantics': {
                'api': 'actual_model_call_counts()',
                'tuple_fields_in_order': ['settled_total_calls', 'post_limit_excess_calls'],
                'original_tuple_values_preserved': True,
                'real_fake_classification': 'Separate selection/actual provider and fixture provenance review; neither tuple position is a fake-call count.',
                'zero_excess_meaning': 'No post-limit excess observed; does not imply zero fake calls or classify the provider.'},
            'reference_validation_failures': self.reference_failures, 'missing': self.missing,
            'exclusions': self.exclusions, 'collection_errors': self.errors, 'originals_unchanged': unchanged,
            'secret_atom_count_including_variants': len(self.redactor.atoms),
            'secret_loading': {'SCB_local_profile_names': self.profile_names_loaded,
                'proxy_environment_variable_names': self.redactor.proxy_variable_names,
                'ambient_auth_files_read': False},
            'vendor_wire': {'status': 'NOT_FOUND_IN_SELECTED_RETAINED_BOUNDARY',
                'scope': 'Checkpoint Registry resources, broker/owner logs and launcher log; no ambient Codex session scan.',
                'note': 'Logical request recipes and adapter returns are not vendor wire. No reconstructed request is presented as original wire.'},
            'limitations': ['No upstream tests/oracle/reference-solution tree is copied; retained grade case expectations/failure messages are diagnostics, not a substitute oracle source checkout.',
                'Parent must review snapshot/diagnostics for embedded protected source or unknown credentials before publication.',
                'Broker overflow/timeout may discard output; command_started only records command hashes. No rerun or synthetic missing transcript.',
                'AgentAction retains serialized original record fields; metadata serializations are not original physical provider messages.',
                'Per-Registry raw tuple means settled total calls, post-limit excess. Real/fake attribution requires separate selection/provider and fixture provenance; zero excess does not imply fake=0. Response versions are not additional calls. Unknown cost/tokens remain unknown.',
                'Existing manifests/lineage are unmodified; use this new manifest for distributed redacted hashes. Historical absolute paths are diagnostics, not access grants.']}
        manifest_data = encoded(manifest)
        if any(atom in manifest_data for atom in self.redactor.atoms):
            raise ValueError('secret in publication metadata')
        write_new(self.output, 'MANIFEST.json', manifest_data)
        write_new(self.output, 'README_ZH.md', (
            '# SCB 原始运行证据候选包\n\n仅供父任务隐私审阅后发布。prefix3 不等于完整五 checkpoint。\n'
            '保留原请求 recipe、adapter 返回/规范化响应、工具命令与输出、owner/launcher/grade 失败及选定提交源码。\n'
            'AgentAction 保留原始记录字段的序列化表示；metadata 文件不是原始物理 JSON 消息或 vendor wire。\n'
            'actual_model_call_counts 原 tuple 顺序为 settled_total_calls、post_limit_excess_calls；第二项不是 fake 次数。real/fake 另按实际 provider/selection 与 fixture 边界判断，excess=0 不代表 fake=0。\n'
            '未保存的 wire、溢出输出及未知 usage 不重构、不补零、不重跑。\n'
            '只做确认凭据的最小替换；MANIFEST 记录原始/分发摘要与零基半开字节区间。\n'
            '原错误顺序、case 预期数据和历史绝对路径保留；路径不代表访问授权。\n'
            '不含完整 profile、执行配置、DB/WAL、缓存或上游测试/oracle/reference solution 文件。\n'
            'snapshot 是 agent 提交源码，不是上游参考解。未通过 provenance 的资源回收单独标记，不能冒称校验通过。\n').encode())
        for row in records:
            data = confined(self.output, row['path']).read_bytes()
            if sha(data) != row['distributed_sha256'] or any(atom in data for atom in self.redactor.atoms):
                raise ValueError('distributed bytes verification failed')
        summary = {'status': manifest['collection_status'], 'privacy_review': 'PENDING_PARENT_REVIEW',
            'files': len(records), 'bytes': sum(r['distributed_bytes'] for r in records),
            'original_byte_files': sum(r['hash_scope'] == 'retained_source_bytes' for r in records),
            'metadata_serializations': sum(r['hash_scope'] == 'new_metadata_serialization_bytes' for r in records),
            'distributed_content_unchanged': sum(not r['changed_bytes'] for r in records),
            'redacted_files': sum(r['changed_bytes'] for r in records),
            'redaction_spans': sum(len(r['redactions']) for r in records),
            'collection_errors': len(self.errors), 'excluded_reason_counts': dict(Counter(x['reason'] for x in self.exclusions)),
            'known_secret_residual_matches': 0, 'originals_unchanged': unchanged}
        write_new(self.output, 'safe-summary.json', encoded(summary))
        return summary

    def collect(self, core_identity):
        if self.output.exists():
            raise FileExistsError('candidate already exists; previous collection must not be overwritten')
        confined(self.output.parent, self.output.name)
        self.output.mkdir(mode=0o700)
        self.created_output = True
        try:
            self.profiles()
        except Exception as exc:
            self.error('load_only_SCB_local_secret_atoms', 'run/profile', exc)
            # Inability to load known atoms blocks publication of private content.
            return self.finish(core_identity)
        try:
            self.files()
        except Exception as exc:
            self.error('collect_finite_file_allowlist', 'run/preparation', exc)
        for checkpoint in CHECKPOINTS:
            if self.conditions.get(checkpoint, {}).get('status') != 'RETAINED_CHECKPOINT':
                continue
            before = set(self.staged)
            try:
                self.registry_checkpoint(checkpoint)
            except Exception as exc:
                # Withdraw newly staged unstable Registry content, never old logs.
                self.staged = {p: row for p, row in self.staged.items() if p in before}
                self.error('collect_checkpoint_registry', checkpoint, exc)
        return self.finish(core_identity)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--terminal-confirmed', action='store_true',
                        help='parent explicitly confirmed stopped/settled run; no polling or stopping is performed')
    parser.add_argument('--preparation-record', action='append', default=[], metavar='evidence/scb-NAME.json',
                        help='parent-selected completed prelaunch review/probe JSON; no broad evidence scan')
    args = parser.parse_args(argv)
    if not (args.collect and args.terminal_confirmed):
        parser.error('collection requires --collect --terminal-confirmed; no live reads performed')
    if Path(sys.prefix).absolute() != ACCEPTED_PREFIX:
        parser.error('use the accepted task upstream-venv interpreter; no sys.path source override')
    for name in args.preparation_record:
        if not re.fullmatch(r'evidence/scb-[A-Za-z0-9_.-]+\.json', name):
            parser.error('preparation records must be explicit evidence/scb-*.json paths')
    if (HERE / 'candidate').exists():
        parser.error('candidate exists; no overwrite or append to an earlier collection condition')
    import cpn
    # Receipt must exist before any live run/profile/Registry content is opened.
    receipt = json.loads(confined(TASK, 'evidence/scb-real01-exit.json').read_bytes())
    if not isinstance(receipt.get('exit_code'), int) or not receipt.get('ended_at'):
        parser.error('completed launcher exit receipt required')
    collector = Collector()
    collector.preparation_records = args.preparation_record
    identity = {'python_prefix': sys.prefix, 'cpn_module': str(Path(cpn.__file__).absolute()),
                'collector_sha256': sha(Path(__file__).read_bytes()),
                'scope': 'Accepted installed reader identity, not a trial source attestation.'}
    try:
        summary = collector.collect(identity)
    except Exception as exc:
        # Never overwrite a previous candidate, even on a failed repeat command.
        if not collector.created_output or (collector.output / 'MANIFEST.json').exists():
            print(json.dumps({'status': 'COLLECTION_REJECTED', 'error_type': type(exc).__name__}))
            return 2
        name = 'collector-fatal.json'
        original = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)).encode()
        collector.redactor.inspect_headers(original)
        safe, spans, uncertain = collector.redactor.redact(original)
        write_new(collector.output, name, encoded({'error_type': type(exc).__name__,
            'original_sha256': sha(original), 'redactions': spans,
            'diagnostic': safe.decode(errors='replace') if not uncertain else None,
            'uncertain_locations': uncertain, 'status': 'PARTIAL_COLLECTION_FAILED'}))
        print(json.dumps({'status': 'PARTIAL_COLLECTION_FAILED', 'error_type': type(exc).__name__}))
        return 1
    print(json.dumps(summary))
    return 0 if summary['status'] == 'COMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
