"""Read-only dashboard companion for main 6e213556; no Registry write hooks.

Historical support is deliberately bounded to canonical checkpoints in the
currently adopted net. Provisional detail is current-only, never backfilled.
All browser data is an observation DTO, never a Registry authority capability.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from cpn.rpnh.inspection import project_registry_net, project_registry_observation
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import SchemaCatalog

SCHEMA = 'rpnh/dashboard/v1'
MAX_CHAIN = 2048
MAX_FIRINGS = 2000


def exact(value):
    """Normalize existing dataclass/wire references; never allocate an ID."""
    def text(v):
        return f"{v['kind']}:{v['value']}" if isinstance(v, dict) else v
    result = {'entity_type': value['entity_type'],
              'logical_id': text(value.get('logical_id', value.get('entity_id'))),
              'version_id': text(value['version_id'])}
    _version_from_payload(result)
    return result


def topology_digest(net):
    value = {'nodes': sorted((n['id'], n['kind'], n['category']) for n in net['nodes']),
             'edges': sorted((e['id'], e['source'], e['target'], e['kind'], e.get('mode'),
                              e['weight'], e.get('outcome')) for e in net['edges'])}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def public_net(net):
    """Default browser disclosure excludes prompts, configs and resource bodies."""
    result = deepcopy(net)
    allowed = {'id', 'label', 'kind', 'category', 'hidden_by_default', 'operation',
               'operation_id', 'executor', 'inputs', 'outputs', 'token_kind',
               'capacity', 'schema', 'active_token_count', 'tokens', 'runtime'}
    result['nodes'] = [{k: v for k, v in n.items() if k in allowed} for n in result['nodes']]
    edge_keys = {'id', 'source', 'target', 'kind', 'mode', 'weight', 'outcome',
                 'hidden_by_default', 'resource', 'direction', 'emit', 'forward_source'}
    result['edges'] = [{k: v for k, v in e.items() if k in edge_keys} for e in result['edges']]
    result.pop('execution', None)
    return result


def _presentation_for(manifest, net):
    signature = topology_digest(net)
    # Built-in UI copy belongs to the browser locale, not the run or manifest.
    result = {'title': None, 'description': None,
              'nodes': {}, 'topology_digest': signature, 'status': 'default'}
    if manifest is None:
        return result
    if (not isinstance(manifest, Mapping) or manifest.get('schema_version') != 'rpnh/presentation/v1'
            or manifest.get('topology_digest') != signature):
        result.update(status='mismatch', warning='展示说明与这张网不匹配，已使用默认名称。')
        return result
    ids = {n['id'] for n in net['nodes']}
    if not isinstance(manifest.get('nodes', {}), dict) or not set(manifest.get('nodes', {})) <= ids:
        raise ValueError('presentation contains unknown node IDs')
    for field, maximum in (('title', 120), ('description', 400)):
        if field in manifest:
            if not isinstance(manifest[field], str) or len(manifest[field]) > maximum:
                raise ValueError('invalid presentation text')
            result[field] = manifest[field]
    for key, value in manifest.get('nodes', {}).items():
        if not isinstance(value, dict) or set(value) - {'name', 'description', 'type', 'group'}:
            raise ValueError('unknown presentation field')
        if any(not isinstance(v, str) or len(v) > 400 for v in value.values()):
            raise ValueError('invalid presentation node text')
        result['nodes'][key] = dict(value)
    if 'agent_nodes' in manifest:
        agents = manifest['agent_nodes']
        transitions = {n['id'] for n in net['nodes'] if n['kind'] == 'transition'}
        if (not isinstance(agents, list) or any(not isinstance(i, str) for i in agents)
                or len(agents) != len(set(agents)) or not set(agents) <= transitions):
            raise ValueError('agent_nodes must reference distinct existing transitions')
        result['agent_nodes'] = list(agents)
    result.update(status='matched', annotation_scope='current_annotation_not_historical_fact')
    return result


def presentation_for(manifest, net):
    # Bad display metadata cannot invalidate an otherwise valid execution.
    try:
        return _presentation_for(manifest, net)
    except (ValueError, TypeError, KeyError):
        result = _presentation_for(None, net)
        result.update(status='invalid', warning='展示说明无效，已保留原流程并使用默认名称。')
        return result

def load_presentation(path):
    if path is None:
        return None
    if path.stat().st_size > 100_000:
        raise ValueError('presentation file exceeds 100 KB')
    return json.loads(path.read_text(encoding='utf8'))


class RegistryDashboard:
    """Callable v1 provider plus optional dashboard/history methods for the server."""
    def __init__(self, run_dir: Path, *, catalog: SchemaCatalog, presentation=None,
                 binding=None):
        if not isinstance(catalog, SchemaCatalog):
            raise TypeError('an explicit SchemaCatalog is required')
        self.run_dir, self.catalog = Path(run_dir).resolve(), catalog
        self.presentation, self.binding = presentation, binding
        core = self._open()
        self.task_id = str(core.task_id)

    def _open(self):
        return _RegistryCore(self.run_dir, create=False, read_only=True, catalog=self.catalog)

    def _bound(self):
        if self.binding is not None:
            # Reuse the adapter's exact path/run identity checks, never a host.
            self.binding()

    def __call__(self):
        self._bound()
        return public_net(project_registry_net(self.run_dir, catalog=self.catalog))

    def _load(self, core, ref, kind, upper):
        reference = _version_from_payload(exact(ref))
        row = core.event_store.object_row_for_view(
            core.event_store.canonical_view(through_ordinal=upper), reference.version_id)
        if (row is None or row['object_type'] != kind or reference.entity_type != kind
                or str(reference.entity_id) != row['logical_id']):
            raise ValueError('historical object is not canonical at the selected boundary')
        value = json.loads(row['metadata_json'])
        core.catalog.validate_instance(kind, category='object', instance=value)
        return value

    def _commit(self, core, ref, upper):
        store = core.event_store
        ordinal = store.canonical_object_publication_ordinal(
            _version_from_payload(exact(ref)).version_id, through_ordinal=upper)
        if ordinal is None:
            raise ValueError('checkpoint has no canonical publication')
        events = store.canonical_events(after_ordinal=ordinal - 1, through_ordinal=ordinal)
        if len(events) != 1:
            raise ValueError('checkpoint publication boundary is ambiguous')
        commits = [e for e in store.list_events_by_transaction(str(events[0].transaction_id))
                   if e.event_type == 'transaction_committed/v1' and e.ordinal <= upper]
        if len(commits) != 1:
            raise ValueError('checkpoint publication transaction is incomplete')
        return commits[0]

    def _chain(self, core, observation):
        upper = observation['source']['verified_head_ordinal']
        net_ref = exact(observation['source']['net_ref'])
        ref = exact(observation['net']['marking']['checkpoint_ref'])
        items, seen, end = [], set(), 'initial_checkpoint'
        while ref is not None and len(items) < MAX_CHAIN:
            if ref['version_id'] in seen:
                raise ValueError('checkpoint predecessor cycle')
            seen.add(ref['version_id'])
            checkpoint = self._load(core, ref, 'marking_checkpoint/v1', upper)
            if checkpoint['net_instance_ref'] != net_ref:
                end = 'net_version_boundary'
                break
            commit = self._commit(core, ref, upper)
            if items and commit.ordinal >= items[-1]['cursor']:
                raise ValueError('checkpoint chain is not strictly ordered')
            items.append({'cursor': commit.ordinal, 'at': commit.recorded_at,
                          'checkpoint_ref': ref, 'epoch': checkpoint['epoch'],
                          'previous_checkpoint_ref': checkpoint.get('previous_checkpoint_ref'),
                          'checkpoint': checkpoint})
            ref = checkpoint.get('previous_checkpoint_ref')
        if ref is not None and len(items) == MAX_CHAIN:
            end = 'reader_limit'
        return items, end

    def _capture(self):
        self._bound()
        observation = project_registry_observation(self.run_dir, catalog=self.catalog)
        core = self._open()
        if str(core.task_id) != self.task_id:
            raise ValueError('bound task identity changed')
        items, end = self._chain(core, observation)
        return core, observation, items, end

    @staticmethod
    def _stable(core, observation):
        source = observation['source']
        if (core.event_store.max_ordinal() != source['verified_head_ordinal'] or
                core.event_store.writer_epoch != source['writer_fencing_epoch']):
            raise RuntimeError('Registry advanced during dashboard read; retry')

    def history(self, *, before=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('history limit must be within 1..100')
        if before is not None and (type(before) is not int or before < 0):
            raise ValueError('invalid history cursor')
        core, obs, chain, end = self._capture()
        eligible = [x for x in chain if before is None or x['cursor'] < before]
        page = eligible[:limit]
        result = {'schema_version': 'rpnh/dashboard_history/v1', 'source': obs['source'],
                  'items': [{k: v for k, v in x.items() if k != 'checkpoint'} for x in reversed(page)],
                  'next_before': page[-1]['cursor'] if len(eligible) > limit else None,
                  'coverage': 'current_net_canonical_checkpoints', 'end_reason': end,
                  'provisional_history': 'unsupported', 'max_chain': MAX_CHAIN}
        self._stable(core, obs)
        return result

    def _firings(self, core, obs, upper, historical):
        net_ref = exact(obs['source']['net_ref'])
        net = self._load(core, net_ref, 'net_instance/v1', upper)
        root = self._load(core, net['team_design_root_ref'], 'team_design_root/v1', upper)
        task = _version_from_payload(root['task_ref'])
        records = {}
        total = 0
        for binding in obs['transition_bindings']:
            tid = binding['transition_id']
            rows = core.event_store.transition_firing_rows_for_task_net_transition(
                task_ref=task, net_instance_ref=_version_from_payload(net_ref), transition_id=tid)
            if total + len(rows) > MAX_FIRINGS:
                raise ValueError('run exceeds dashboard firing limit; narrow the run scope')
            total += len(rows)
            records[tid] = []
            for row in rows:
                firing = json.loads(row['metadata_json'])
                ref = firing['transition_firing_ref']
                if historical and core.event_store.canonical_object_row(
                        _version_from_payload(ref).version_id, through_ordinal=upper) is None:
                    continue
                record = core.event_store.ordered_firing_record(ref['version_id'])
                events = [e for e in record['events'] if e.ordinal <= upper]
                if not events:
                    continue
                item = {'firing_ref': ref, 'attempt_index': firing['attempt_index'],
                        'node_ref': firing['node_ref'], 'admission_ordinal': events[0].ordinal,
                        'admission_checkpoint_ref': firing['admission_marking_checkpoint_ref'],
                        'publication_state': record['state'], 'status': 'admitted'}
                if any(e.event_type == 'operation_execution_started/v1' for e in events):
                    item['status'] = 'started'
                if record['state'] == 'PUBLISHED':
                    result_ref = record['firing_completion']['operation_result_ref']
                    result = self._load(core, result_ref, 'operation_result/v1', upper)
                    item.update(status='settled', business_outcome=result['business_outcome'],
                                result_ref=result_ref,
                                successor_checkpoint_ref=record['successor_checkpoint']['marking_checkpoint_ref'])
                elif historical:
                    continue
                elif record['state'] == 'PROVISIONAL':
                    # Explicit diagnostics capability, never a FiringView.
                    gate = core.event_store.firing_publication_row(ref['version_id'])
                    core.event_store.provisional_observation_view(
                        firing_version_id=ref['version_id'], invocation_version_id=gate['invocation_version_id'])
                else:
                    item['status'] = 'invalidated'
                item['events'] = [{'event_id': str(e.event_id), 'type': e.event_type,
                                   'ordinal': e.ordinal, 'at': e.recorded_at} for e in events]
                records[tid].append(item)
        return records

    def _agent_nodes(self, core, observation, upper, presentation):
        """Read existing Agent bindings/LLM contracts; never guess from names.

        A trusted host may explicitly map an externally-owned Agent anchor via
        the topology-matched presentation file. Such mappings remain annotations,
        not Registry execution authority. No code or prompt is loaded from them.
        """
        candidates = {n['id']: n for n in observation['net']['nodes']}
        mapped = set(presentation.get('agent_nodes', ()))
        agents = []
        for binding in observation['transition_bindings']:
            tid = binding['transition_id']
            value = self._load(core, binding['executable_binding_ref'],
                               'executable_transition_binding/v1', upper)
            if (value['transition_id'] != tid
                    or exact(value['node_ref']) != exact(binding['node_ref'])
                    or exact(value['net_instance_ref']) != exact(observation['source']['net_ref'])):
                raise ValueError('Agent binding differs from the observed net')
            agent_ref = value.get('agent_ref')
            contract = candidates[tid].get('executor_declaration', {}).get('contracts', {})
            source = ('registered_agent_binding' if agent_ref is not None else
                      'declared_llm_executor' if contract.get('transport') == 'llm' else
                      'explicit_host_annotation' if tid in mapped else None)
            if source:
                # Explicit semantic IDs are read, never changed. Scope them to
                # the compiled component so rework variants of one Agent can
                # share one display card without globally merging equal names.
                semantic = candidates[tid].get('config', {}).get('semantic_node_id')
                group = ({'component': candidates[tid].get('operation', tid).rsplit('.', 1)[0],
                          'semantic_node_id': semantic} if isinstance(semantic, str) and semantic else None)
                agents.append({'transition_id': tid, 'source': source,
                               'semantic_group': group,
                               'agent_ref': agent_ref,
                               'executable_binding_ref': binding['executable_binding_ref']})
        return agents

    def dashboard(self, *, cursor=None):
        if cursor is not None and (type(cursor) is not int or cursor < 0):
            raise ValueError('invalid checkpoint cursor')
        core, obs, chain, end = self._capture()
        current_upper = obs['source']['verified_head_ordinal']
        selected = chain[0] if cursor is None else next((x for x in chain if x['cursor'] == cursor), None)
        if selected is None:
            raise ValueError('cursor is not a retained checkpoint in the current net')
        upper = current_upper if cursor is None else selected['cursor']
        checkpoint = selected['checkpoint']
        net = public_net(obs['net'])
        net['source']['verified_head_ordinal'] = upper
        tokens = []
        for ref in checkpoint['token_refs']:
            token = self._load(core, ref, 'petri_token/v1', upper)
            if token['net_instance_ref'] != exact(obs['source']['net_ref']):
                raise ValueError('token belongs to another net')
            tokens.append({'token_ref': ref, 'place': token['place'], 'kind': token['kind'],
                           'active_in_checkpoint': token['epoch'] == checkpoint['epoch'] and token['consumed_by'] is None})
        firings = self._firings(core, obs, upper, cursor is not None)
        for node in net['nodes']:
            if node['kind'] == 'place':
                node['tokens'] = [t for t in tokens if t['place'] == node['id']]
                node['active_token_count'] = sum(t['active_in_checkpoint'] for t in node['tokens'])
            else:
                node['runtime'] = {'firings': firings.get(node['id'], [])}
        net['marking'] = {'checkpoint_ref': selected['checkpoint_ref'], 'epoch': checkpoint['epoch'],
                          'token_count': len(tokens), 'active_token_count': sum(t['active_in_checkpoint'] for t in tokens)}
        change = {'coverage': 'not_provided', 'consumed': [], 'deposited': [],
                  'firing_refs': checkpoint['transition_firing_refs'],
                  'previous_checkpoint_ref': checkpoint.get('previous_checkpoint_ref')}
        if checkpoint.get('settlement_delta_ref') is not None:
            delta = self._load(core, checkpoint['settlement_delta_ref'], 'marking_delta/v1', upper)
            if not delta.get('declared_effects'):
                for field, key in [('consumed_refs', 'consumed'), ('deposited_refs', 'deposited')]:
                    for ref in delta[field]:
                        token = self._load(core, ref, 'petri_token/v1', upper)
                        change[key].append({'token_ref': ref, 'place': token['place']})
                change['coverage'] = 'settlement_delta'
            else:
                change['coverage'] = 'declared_effects_not_animated'
        presentation = presentation_for(self.presentation, net)
        agents = self._agent_nodes(core, obs, upper, presentation)
        response = {'schema_version': SCHEMA, 'source': {**obs['source'], 'verified_head_ordinal': upper},
                    'net': net, 'boundaries': obs['boundaries'], 'transition_bindings': obs['transition_bindings'],
                    'presentation': presentation, 'agent_nodes': agents, 'change': change,
                    'position': {'mode': 'live' if cursor is None else 'history', 'cursor': selected['cursor'],
                                 'at': selected['at'], 'latest_head': current_upper},
                    'coverage': {'agent_nodes': 'declared_or_explicit_host_annotation',
                                 'history': 'current_net_canonical_checkpoints', 'end_reason': end,
                                 'firings': 'current_observations' if cursor is None else 'canonical_only',
                                 'provisional_history': 'unsupported', 'terminal_evidence': 'not_provided'},
                    'observed_at': datetime.now(timezone.utc).isoformat()}
        self._stable(core, obs)
        return response


def main():
    parser = argparse.ArgumentParser(description='RPNH read-only workflow dashboard')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--presentation', type=Path)
    parser.add_argument('--describe', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--show-resources', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from cpn.frontend.server import serve_projection
    try:
        manifest = load_presentation(args.presentation)
        provider = RegistryDashboard(args.run, catalog=agent_task_catalog(), presentation=manifest)
        if args.describe:
            frame = provider.dashboard()
            print(json.dumps({'source': frame['source'], 'presentation': frame['presentation'],
                              'coverage': frame['coverage']}, ensure_ascii=False, indent=2))
        else:
            serve_projection(provider, port=args.port, open_browser=not args.no_open,
                             show_resources=args.show_resources)
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        parser.exit(2, f'dashboard: {error}\n')


if __name__ == '__main__':
    main()
