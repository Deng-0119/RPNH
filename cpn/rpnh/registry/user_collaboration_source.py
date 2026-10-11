"""Read original successful stock TEXT at the trusted personal-owner boundary."""
import hashlib
import json
import math

from .event_store import RegistryConflict
from ._event_store.collaboration_descriptors import exact_prepared, exact_descriptor

MAX_TEXT = 262144
STOCK_TEXT = 'application/rpnh_agent_text/v1'


def strict_proposal_json(raw):
    if type(raw) is not bytes or len(raw) > MAX_TEXT:
        raise RegistryConflict('proposal exceeds the 256 KiB UTF-8 limit')
    try:
        text = raw.decode('utf-8')
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in '[{':
                depth += 1
                if depth > 64:
                    raise ValueError('proposal nesting exceeds 64')
            elif char in ']}':
                depth -= 1
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError('duplicate proposal key')
                result[key] = value
            return result
        def number(value):
            value = float(value)
            if not math.isfinite(value):
                raise ValueError('nonfinite proposal number')
            return value
        def constant(value):
            raise ValueError('non-JSON proposal constant')
        value = json.loads(text, object_pairs_hook=pairs, parse_float=number, parse_constant=constant)
        if type(value) is not dict:
            raise ValueError('proposal must be a complete Module object')
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RegistryConflict('invalid bounded proposal JSON: ' + str(exc)) from exc


def completed_text(core, *, firing_ref=None):
    """Derive a run terminal or a HOST-selected completed operation TEXT.

    The optional typed firing ref is obtained by the trusted HOST from native
    admission/execution or canonical history. It is not a model JSON field.
    Its complete successful output chain is reclosed here, including when the
    personal project is still running or has since adopted another net.
    """
    store = core.event_store
    cut = store.max_ordinal()
    with store.connect() as db:
        db.execute('BEGIN')
        pointer = json.loads(store.get_meta('native_run_ref'))
        identity = exact_descriptor(db, core.object_store, core.task_id, pointer)
        run_ref = {'entity_type': 'native_run_identity/v1', 'logical_id': identity['run_id'],
                   'version_id': identity['run_version_id']}
        if run_ref != pointer or identity['task_ref']['logical_id'] != str(core.task_id):
            raise RegistryConflict('TEXT origin run differs from original personal task')
        if firing_ref is None:
            terminals = []
            for row in db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='run_terminal_evidence/v1'"):
                ref = {'entity_type': 'run_terminal_evidence/v1', 'logical_id': row['logical_id'], 'version_id': row['version_id']}
                body = exact_descriptor(db, core.object_store, core.task_id, ref)
                if body['run_ref'] == run_ref:
                    terminals.append((ref, body))
            if len(terminals) != 1 or terminals[0][1]['run_outcome'] != 'complete':
                raise RegistryConflict('original successful run terminal is required')
            terminal_ref, terminal = terminals[0]
            product_ref = terminal['terminal_result_ref']
            selected_firing = None
        else:
            from .models import VersionRef
            from .strict_contracts import ref_payload
            if type(firing_ref) is not VersionRef or firing_ref.entity_type != 'transition_firing/v1':
                raise RegistryConflict('operation selection requires a HOST-derived typed firing ref')
            selected_firing = ref_payload(firing_ref)
            exact_descriptor(db, core.object_store, core.task_id, selected_firing)
            record = store.ordered_firing_record(firing_ref.version_id, _db=db)
            completion = record['firing_completion']
            if record['state'] != 'PUBLISHED' or completion is None:
                raise RegistryConflict('selected operation is not successfully published')
            result = exact_descriptor(db, core.object_store, core.task_id, completion['operation_result_ref'])
            products = []
            for ref in result['output_resource_refs']:
                candidate = exact_prepared(db, core.object_store, core.task_id, ref)
                if candidate.metadata['content_schema_ref'] == STOCK_TEXT:
                    products.append(ref)
            if len(products) != 1:
                raise RegistryConflict('selected operation must have exactly one successful stock TEXT output')
            product_ref = products[0]
            terminal_ref = None
            terminal = None
        product = exact_prepared(db, core.object_store, core.task_id, product_ref)
        if product.metadata['content_schema_ref'] != STOCK_TEXT or product.media_type != 'application/json':
            raise RegistryConflict('original product must use the stock JSON-string TEXT codec')
        invocation_ref = product.metadata['reference_provenance']['producer_invocation_ref']
        invocation = exact_descriptor(db, core.object_store, core.task_id, invocation_ref)
        firing_ref = invocation['own_transition_firing_ref']
        if selected_firing is not None and firing_ref != selected_firing:
            raise RegistryConflict('selected output belongs to another original firing')
        exact_descriptor(db, core.object_store, core.task_id, firing_ref)
        if invocation['task_ref'] != identity['task_ref'] or product.metadata['producer_ref'] != invocation_ref:
            raise RegistryConflict('TEXT original producer/task differs')
        raw = core.object_store.read_registered(product, max_bytes=6 * MAX_TEXT + 2)
    # The native ordered-firing reader closes the original PUBLISHED graph.
    record = store.ordered_firing_record(firing_ref['version_id'])
    completion = record['firing_completion']
    if record['state'] != 'PUBLISHED' or completion is None or completion['invocation_ref'] != invocation_ref:
        raise RegistryConflict('TEXT original firing was not successfully published')
    result_ref = completion['operation_result_ref']
    with store.connect() as db:
        result = exact_descriptor(db, core.object_store, core.task_id, result_ref)
    settlements = [event for event in record['events'] if event.event_type == 'transition_firing_settled/v1']
    if (len(settlements) != 1 or settlements[0].payload.get('business_outcome') != 'completed'
            or product.metadata.get('descriptors', {}).get('output_outcome_id') != 'complete'
            or result['invocation_ref'] != invocation_ref or product_ref not in result['output_resource_refs']):
        raise RegistryConflict('TEXT is not an actual successful original firing output')
    try:
        text = json.loads(raw)
        if type(text) is not str:
            raise ValueError('TEXT codec does not contain a string')
        payload = text.encode('utf-8')
        strict_proposal_json(payload)
    except (ValueError, UnicodeError) as exc:
        raise RegistryConflict('invalid original TEXT codec') from exc
    if store.max_ordinal() != cut:
        raise RegistryConflict('original source changed during completed TEXT delivery read')
    return raw, payload, {'run_ref': run_ref, 'task_ref': identity['task_ref'],
        'product_ref': product_ref, 'invocation_ref': invocation_ref,
        'firing_ref': firing_ref, 'principal_ref': invocation['principal_ref'],
        'terminal_ref': terminal_ref, 'operation_result_ref': result_ref,
        'producer': terminal['producer'] if terminal is not None else settlements[0].producer_principal,
        'completion_kind': 'run_terminal' if terminal is not None else 'operation_success', 'source_cut': cut,
        'source_media_type': product.media_type, 'source_content_schema': STOCK_TEXT,
        'stored_digest': hashlib.sha256(raw).hexdigest(), 'text_digest': hashlib.sha256(payload).hexdigest()}
