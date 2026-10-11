"""USER-COLLABORATION-V2 validation in the original BEGIN IMMEDIATE cut.

No validator here opens a SQLite connection or reads staged metadata as
authority. Exact historical witnesses are independent of today's membership.
"""
import hashlib
import json
from datetime import datetime, timezone

from ..event_store import RegistryConflict
from ..schema_catalog import canonical_json
from ..strict_contracts import ref_payload
from ..user_collaboration import (TYPES, SESSION, PROJECT, PARTICIPANT, PROPOSAL,
    REVIEW, DECISION, RECEIPT, DELIVERY, EXECUTION, check_handle, fact_ref, command_key)
from .collaboration_descriptors import descriptor_store, exact_descriptor, exact_prepared, readable_descriptor
from .branch_publication import BRANCH_TYPES, _local_binding, current_branch_document


def exact(store, db, task, reference, kind=None):
    if not isinstance(reference, dict) or (kind and reference.get('entity_type') != kind):
        raise RegistryConflict('collaboration requires an exact canonical reference')
    return exact_descriptor(db, descriptor_store(store), task, reference)


def facts(store, db, task, kind):
    rows = db.execute('SELECT o.logical_id,o.version_id FROM objects o JOIN events e '
        'ON e.event_id=o.published_event_id WHERE o.object_type=? AND e.task_id=? ORDER BY e.ordinal',
        (kind, str(task))).fetchall()
    return [exact(store, db, task, {'entity_type': kind, 'logical_id': r['logical_id'],
                                   'version_id': r['version_id']}, kind) for r in rows]


def session_document(store, db, task, action):
    check_handle(store, action)
    if action.session is None:
        raise RegistryConflict('collaboration action has no actor session')
    value = exact(store, db, task, ref_payload(action.session.ref), SESSION)
    if value['data']['actor'] != action.session.actor:
        raise RegistryConflict('collaboration actor does not match canonical session')
    return value


def participant(store, db, task, project, session_ref):
    rows = [v for v in facts(store, db, task, PARTICIPANT)
            if v['project_ref'] == project['self_ref'] and v['data']['subject_session_ref'] == session_ref]
    return rows[-1] if rows else None


def require_owner(store, db, task, project, session):
    if project['data']['owner_session_ref'] != session['self_ref'] or not session['data']['owner']:
        raise RegistryConflict('collaboration requires the target owner A')
    current = participant(store, db, task, project, session['self_ref'])
    if current is not None and current['data']['status'] == 'revoked':
        raise RegistryConflict('collaboration owner authority is revoked')


def require_propose(store, db, task, project, session_ref):
    session = exact(store, db, task, session_ref, SESSION)
    if session_ref == project['data']['owner_session_ref']:
        require_owner(store, db, task, project, session)
        return
    current = participant(store, db, task, project, session_ref)
    if current is None or current['data']['status'] != 'joined' or current['data']['role'] != 'propose':
        raise RegistryConflict('collaboration current proposal eligibility is absent or revoked')


def read_fence(store, db, task, action):
    """The original observer grant/profile/revocation fence at this same cut."""
    session = session_document(store, db, task, action)
    if session['data']['owner']:
        return
    from ..observer_access import (_read_owner_document, _control, timestamp,
                                   RegistryReadObserverContext, REVOCATION_TYPE)
    from ..publication import _version_from_payload
    from types import SimpleNamespace
    context = action.observer
    if type(context) is not RegistryReadObserverContext:
        raise RegistryConflict('collaboration response requires current original read authority')
    core = SimpleNamespace(task_id=task, catalog=store.catalog, object_store=descriptor_store(store))
    grant = _read_owner_document(db, core, context.grant_ref)
    profile = _read_owner_document(db, core, context.observer_profile_ref)
    if (any(grant.get(k) != v for k, v in context.to_dict().items())
            or profile['observer_principal_ref'] != session['data']['target_principal_ref']
            or context.task_ref.entity_id != task
            or context.issued_writer_fencing_epoch != int(db.execute(
                "SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
            or context.issued_task_control_sequence != _control(db, task)
            or timestamp(context.expires_at) <= datetime.now(timezone.utc)):
        raise RegistryConflict('collaboration response read fence is stale or belongs to another actor')
    rows = db.execute('SELECT metadata_json FROM objects WHERE object_type=? '
        "AND json_extract(metadata_json,'$.grant_ref.version_id')=?", (REVOCATION_TYPE, str(context.grant_ref.version_id))).fetchall()
    for row in rows:
        value = json.loads(row['metadata_json'])
        _read_owner_document(db, core, _version_from_payload(value['self_ref']))
    if rows:
        raise RegistryConflict('collaboration response read authority is revoked')
    # Returning complete descriptors requires complete record scope, never
    # merely an index grant. HOST callers can instead use the original reader.
    if not all('*' in profile['scope']['record_fields'].get(kind, []) for kind in TYPES):
        raise RegistryConflict('collaboration response lacks complete record scope')


def check_replay(store, db, task, existing, action):
    material = json.loads(existing['command_json'])
    recorded = material.get('user_collaboration')
    if recorded is None and action is None:
        protected = db.execute('SELECT 1 FROM objects o JOIN events e ON e.event_id=o.published_event_id '
            'WHERE o.object_type=? AND e.task_id=? LIMIT 1', (PROJECT, str(task))).fetchone()
        sensitive = (any(o.get('object_type') in BRANCH_TYPES for o in material.get('objects', ()))
            or any(e.get('event_type') == 'net_adopted/v1' for e in material.get('events', ())))
        if protected and sensitive:
            raise RegistryConflict('protected historical material requires a trusted HOST read fence')
        return
    if existing['status'] != 'committed':
        raise RegistryConflict('aborted or prepared collaboration command is not accepted')
    check_handle(store, action)
    if recorded != action.material():
        raise RegistryConflict('collaboration replay changed actor/session/action')
    if action.kind != 'session':
        read_fence(store, db, task, action)


def _publication(store, db, task, obj, events, command):
    doc = readable_descriptor(descriptor_store(store), obj, strict=True, max_bytes=2 * 1024 * 1024)
    if obj.object_type not in TYPES or doc['self_ref'] != ref_payload(fact_ref(task,
            obj.object_type.removeprefix('user_collaboration_').split('/')[0], doc['command_id'])):
        raise RegistryConflict('collaboration fact identity differs from immutable command')
    if (str(obj.logical_id) != doc['self_ref']['logical_id'] or str(obj.version_id) != doc['self_ref']['version_id']
            or obj.producer_invocation_id is not None):
        raise RegistryConflict('collaboration fact has a forged object identity or producer')
    binding = _local_binding(db, store.catalog, task, descriptor_store(store))
    if (doc['source_id'] != binding['source_id'] or doc['owner_task_ref'] != binding['task_ref']
            or doc['publisher_bootstrap_ref'] != binding['bootstrap_command_ref']):
        raise RegistryConflict('collaboration fact source/owner does not match original authority')
    matches = [e for e in events if e.event_type == 'object_version_published/v1'
               and e.payload.get('version_id') == str(obj.version_id)]
    if len(matches) != 1:
        raise RegistryConflict('collaboration fact needs its unique canonical publication')
    event = matches[0]
    if (event.producer_principal != 'framework' or event.producer_invocation_id is not None
            or event.criticality != 'authoritative' or event.task_control
            or event.stream_id != f'object:{obj.logical_id}' or event.aggregate_id != str(obj.logical_id)
            or event.aggregate_type != obj.object_type or event.command_id != command or event.idempotency_key != command
            or event.payload_schema_ref != 'registry_v1/object_version_published/v1'
            or canonical_json(event.payload) != canonical_json({'logical_id': str(obj.logical_id),
                'version_id': str(obj.version_id), 'object_type': obj.object_type, 'size': obj.size,
                'media_type': obj.media_type, 'schema_ref': obj.schema_ref, 'storage_locator': obj.storage_locator,
                'metadata': dict(obj.metadata)})):
        raise RegistryConflict('collaboration typed bytes/publication differ')
    if db.execute('SELECT 1 FROM objects WHERE logical_id=?', (str(obj.logical_id),)).fetchone():
        raise RegistryConflict('collaboration facts are immutable; revisions require a new command')
    return doc


def _proposal(store, db, task, project, session, data):
    require_propose(store, db, task, project, session['self_ref'])
    text_ref = data['source_text_ref']
    if text_ref['entity_type'] != 'resource_version/v1':
        raise RegistryConflict('proposal requires canonical TEXT resource')
    prepared = exact_prepared(db, descriptor_store(store), task, text_ref)
    raw = descriptor_store(store).read_registered(prepared, max_bytes=6 * 262144 + 2)
    if data['delivery_ref'] is not None:
        delivery = exact(store, db, task, data['delivery_ref'], DELIVERY)
        witness = delivery['data']['witness']
        refs = {k: witness[k] for k in ('run_ref', 'task_ref', 'product_ref', 'invocation_ref',
            'firing_ref', 'principal_ref', 'terminal_ref', 'operation_result_ref') if witness[k] is not None}
        refs['producer_ref'] = witness['invocation_ref']
        if (data['synthetic_fixture'] or delivery['session_ref'] != session['self_ref']
                or delivery['data']['received_text_ref'] != text_ref
                or data['execution_refs'] != refs
                or data['origin_text'] != {'source_id': delivery['data']['source_id'], 'ref': witness['product_ref']}
                or hashlib.sha256(raw).hexdigest() != witness['stored_digest'] or prepared.media_type != 'application/json'):
            raise RegistryConflict('proposal differs from its exact original successful TEXT delivery')
        decoded = json.loads(raw)
        if type(decoded) is not str:
            raise RegistryConflict('stock TEXT codec must contain a JSON string')
        raw = decoded.encode('utf-8')
    else:
        # Explicit test-domain path, never described as a model successful TEXT.
        if not data['synthetic_fixture'] or prepared.media_type != 'text/plain':
            raise RegistryConflict('model proposal requires an original successful TEXT delivery witness')
        for reference in data['execution_refs'].values():
            exact_prepared(db, descriptor_store(store), task, reference)
        producer = data['execution_refs'].get('producer_ref')
        if producer is None or producer != prepared.metadata['producer_ref']:
            raise RegistryConflict('synthetic proposal producer differs from original resource')
        if data['origin_text'] != {'source_id': project['source_id'], 'ref': text_ref}:
            raise RegistryConflict('synthetic proposal must be explicitly target local')
    if hashlib.sha256(raw).hexdigest() != data['text_digest']:
        raise RegistryConflict('proposal decoded TEXT digest differs')
    from ..user_collaboration_source import strict_proposal_json
    body = strict_proposal_json(raw)
    if canonical_json(body) != canonical_json(data['payload']):
        raise RegistryConflict('typed proposal differs from original TEXT')
    branch = exact(store, db, task, data['expected_branch_ref'])
    if (branch['branch_ref']['ref']['logical_id'] != project['data']['branch_ref']['logical_id']
            or branch['head_revision_ref']['ref'] != data['expected_head_ref']
            or data['base_revision_ref'] != data['expected_head_ref']):
        raise RegistryConflict('proposal base/Branch/head differs from protected target')
    revision = exact(store, db, task, data['candidate_revision_ref'])
    definition = revision['definition_ref']['ref']
    definition_ref = {'entity_type': 'resource_version/v1', 'logical_id': definition['resource_id'],
                      'version_id': definition['resource_version_id']}
    defined = exact_prepared(db, descriptor_store(store), task, definition_ref)
    definition_body = json.loads(descriptor_store(store).read_registered(defined, max_bytes=6 * 262144 + 2))
    from ...module import ModuleDeclaration
    module = ModuleDeclaration.from_dict(body)
    if (module.designer_constraints or len(module.components) > 64
            or sum(len(component.operations) for component in module.components) > 256
            or canonical_json(module.to_dict()) != canonical_json(definition_body)):
        raise RegistryConflict('proposal Module differs from exact authored candidate or exceeds its plain bounds')
    if data['base_revision_ref'] not in [p['ref'] for p in revision['parent_revision_refs']]:
        raise RegistryConflict('proposal candidate must descend from the exact target-local base')
    if data['previous_proposal_ref'] is not None:
        previous = exact(store, db, task, data['previous_proposal_ref'], PROPOSAL)
        if previous['session_ref'] != session['self_ref'] or previous['project_ref'] != project['self_ref']:
            raise RegistryConflict('proposal revision cannot change its actor or project')


def _decision(store, db, task, project, data, *, accepting=False):
    if len(data['proposal_refs']) != len(data['review_refs']) or not data['proposal_refs']:
        raise RegistryConflict('decision requires exact proposals and separate required approvals')
    for proposal_ref, review_ref in zip(data['proposal_refs'], data['review_refs']):
        proposal = exact(store, db, task, proposal_ref, PROPOSAL)
        review = exact(store, db, task, review_ref, REVIEW)
        if (proposal['project_ref'] != project['self_ref'] or review['project_ref'] != project['self_ref']
                or review['session_ref'] != project['data']['owner_session_ref']
                or review['data']['proposal_ref'] != proposal_ref or review['data']['disposition'] != 'approve'
                or canonical_json(review['data']['exact_proposal']) != canonical_json(proposal['data'])
                or (data['operation'] == 'advance' and (
                    proposal['data']['expected_branch_ref'] != data['expected_branch_ref']
                    or proposal['data']['expected_head_ref'] != data['expected_head_ref']))):
            raise RegistryConflict('decision approval does not cover the exact proposal/base/head')
        if accepting:
            require_propose(store, db, task, project, proposal['session_ref'])
        if data['operation'] == 'advance':
            current_reviews = [value for value in facts(store, db, task, REVIEW)
                if value['project_ref'] == project['self_ref']
                and value['session_ref'] == project['data']['owner_session_ref']
                and value['data']['proposal_ref'] == proposal_ref
                and value['data']['disposition'] != 'comment']
            if not current_reviews or current_reviews[-1]['self_ref'] != review_ref:
                raise RegistryConflict('decision approval is no longer the current required review')
    exact(store, db, task, data['result_revision_ref'])
    if data['operation'] == 'advance':
        if data['accepted_receipt_ref'] is not None:
            raise RegistryConflict('first acceptance cannot borrow an accepted receipt')
        result = exact(store, db, task, data['result_revision_ref'])
        candidates = [exact(store, db, task, p, PROPOSAL)['data']['candidate_revision_ref'] for p in data['proposal_refs']]
        if len(result['parent_revision_refs']) != 2:
            if candidates != [data['result_revision_ref']]:
                raise RegistryConflict('single proposal approval requires its exact candidate, not a child or other result')
        else:
            from .user_collaboration_merge import merge_base
            if merge_base(store, db, task, result) != data['expected_head_ref']:
                raise RegistryConflict('merge original analysis base differs from the exact expected Branch head')
            parents = {canonical_json(p['ref']) for p in result['parent_revision_refs']}
            # The current official base is already project material. Every
            # other direct input must have its own exact current approval and
            # (at first acceptance) author eligibility checked above.
            required = parents - {canonical_json(data['expected_head_ref'])}
            actual = {canonical_json(candidate) for candidate in candidates}
            if actual != required or len(candidates) != len(actual):
                raise RegistryConflict('merge requires the complete exact proposal/approval set for all unaccepted inputs')
    else:
        receipt = exact(store, db, task, data['accepted_receipt_ref'], RECEIPT)
        accepted = exact(store, db, task, receipt['data']['decision_ref'], DECISION)
        if (receipt['project_ref'] != project['self_ref'] or receipt['data']['operation'] != 'advance'
                or receipt['data']['result_revision_ref'] != data['result_revision_ref']
                or accepted['data']['proposal_refs'] != data['proposal_refs']
                or accepted['data']['review_refs'] != data['review_refs']):
            raise RegistryConflict('adoption needs an exact accepted Branch receipt')
        branch = exact(store, db, task, receipt['data']['result_ref'])
        row = db.execute('SELECT transaction_id FROM objects WHERE version_id=?',
                         (receipt['data']['result_ref']['version_id'],)).fetchone()
        if row['transaction_id'] != receipt['data']['transaction_id'] or branch['head_revision_ref']['ref'] != data['result_revision_ref']:
            raise RegistryConflict('accepted receipt was not atomic with its exact Branch advance')
        current = current_branch_document(db, store.catalog, task,
            project['data']['branch_ref']['logical_id'], descriptor_store(store))
        if (data['expected_branch_ref'] != receipt['data']['result_ref']
                or data['expected_head_ref'] != data['result_revision_ref']
                or current is None or current['branch_ref']['ref'] != data['expected_branch_ref']):
            raise RegistryConflict('adoption accepted Branch expectation is stale')


def validate_commit(store, db, *, task_id, transaction_id, idempotency_key,
                    objects, events, relations, action, existing=None):
    """Always called, including when the optional HOST inventory is absent."""
    if existing is not None:
        check_replay(store, db, task_id, existing, action)
        return
    collab = [o for o in objects if o.object_type.startswith('user_collaboration_')]
    # Detect pretransaction protections independently of the caller's catalog.
    project_rows = db.execute('SELECT o.metadata_json FROM objects o JOIN events e ON e.event_id=o.published_event_id '
                              'WHERE o.object_type=? AND e.task_id=?', (PROJECT, str(task_id))).fetchall()
    branches = [o for o in objects if o.object_type in BRANCH_TYPES]
    adoptions = [e for e in events if e.event_type == 'net_adopted/v1']
    observer_changes = [o for o in objects if o.object_type in
        ('registry_observer_profile/v2', 'registry_observer_grant/v2', 'registry_observer_revocation/v1')]
    if not collab and not project_rows and action is None:
        return
    projects = [exact(store, db, task_id, json.loads(r['metadata_json'])['self_ref'], PROJECT) for r in project_rows]
    sensitive = bool(projects and (branches or adoptions or observer_changes))
    if not collab and not sensitive and action is None:
        return
    check_handle(store, action)
    documents = [_publication(store, db, task_id, obj, events, idempotency_key) for obj in collab]
    if action.kind == 'session':
        if (len(objects) != 1 or len(documents) != 1 or objects[0].object_type != SESSION
                or canonical_json(documents[0]['data']) != action.creation or documents[0]['session_ref'] is not None
                or documents[0]['project_ref'] is not None):
            raise RegistryConflict('only trusted HOST origin binding may establish a session')
        session = None
        exact(store, db, task_id, documents[0]['data']['target_principal_ref'], 'principal/v1')
    else:
        session = session_document(store, db, task_id, action)
    if action.kind in ('advance', 'adopt'):
        if len(documents) != 1 or collab[0].object_type != RECEIPT:
            raise RegistryConflict('collaboration apply requires exactly one atomic action receipt')
        decision = exact(store, db, task_id, ref_payload(action.reference), DECISION)
        project = exact(store, db, task_id, decision['project_ref'], PROJECT)
        require_owner(store, db, task_id, project, session)
        data = decision['data']
        if data['operation'] != action.kind or decision['session_ref'] != session['self_ref']:
            raise RegistryConflict('collaboration decision actor/action differs')
        _decision(store, db, task_id, project, data, accepting=action.kind == 'advance')
        if action.kind == 'advance':
            if len(branches) != 1 or adoptions:
                raise RegistryConflict('Branch and runtime adoption have separate receipts')
            branch = readable_descriptor(descriptor_store(store), branches[0])
            if (branch['branch_ref']['ref']['logical_id'] != project['data']['branch_ref']['logical_id']
                    or branch['predecessor_branch_ref']['ref'] != data['expected_branch_ref']
                    or branch['expected_head_revision_ref']['ref'] != data['expected_head_ref']
                    or branch['head_revision_ref']['ref'] != data['result_revision_ref']):
                raise RegistryConflict('protected Branch alias/result/expectation differs from decision')
            result_ref = branch['branch_ref']['ref']
        else:
            if branches or len(adoptions) != 1:
                raise RegistryConflict('adoption needs one original net_adopted event')
            event = adoptions[0]
            if ((data['result_net_ref'] is not None and event.payload.get('net_instance_ref') != data['result_net_ref'])
                    or event.payload.get('supersedes_net_ref') != data['expected_runtime_ref']):
                raise RegistryConflict('protected adoption differs from exact runtime decision')
            if 'owner_command_ref' not in event.payload:
                raise RegistryConflict('protected structural/ordinary adoption is not supported; use owner adoption')
            # The original owner gate proves PN/marking authority. Also bind
            # that declaration to the exact accepted author revision bytes.
            result_ref = event.payload['net_instance_ref']
            net = exact(store, db, task_id, result_ref)
            revision = exact(store, db, task_id, data['result_revision_ref'])
            author_definition = revision['definition_ref']['ref']
            if 'resource_id' in author_definition:
                author_definition = {'entity_type': 'resource_version/v1', 'logical_id': author_definition['resource_id'],
                                     'version_id': author_definition['resource_version_id']}
            candidate = event.payload['candidate_ref']
            if 'resource_id' in candidate:
                candidate = {'entity_type': 'resource_version/v1', 'logical_id': candidate['resource_id'],
                             'version_id': candidate['resource_version_id']}
            object_store = descriptor_store(store)
            from ...executable_net import load_compiled_net
            authored = json.loads(object_store.read_registered(exact_prepared(db, object_store, task_id, author_definition)))
            compiled = load_compiled_net(json.loads(object_store.read_registered(exact_prepared(db, object_store, task_id, candidate))))
            if canonical_json(authored) != canonical_json(compiled.source.to_dict()):
                raise RegistryConflict('runtime declaration differs from accepted author revision')
            submitted = db.execute("SELECT logical_id,version_id FROM objects WHERE object_type='resource_version/v1' "
                "AND json_extract(metadata_json,'$.content_schema_ref')='rpnh/owner_control/v1' "
                "AND json_extract(metadata_json,'$.descriptors.owner_control_kind')='candidate' "
                "AND json_extract(metadata_json,'$.descriptors.command_id')=?", (idempotency_key,)).fetchall()
            if len(submitted) != 1:
                raise RegistryConflict('protected adoption requires its one immutable original owner submission')
            submission_ref = {'entity_type': 'resource_version/v1', 'logical_id': submitted[0]['logical_id'],
                              'version_id': submitted[0]['version_id']}
            submission = json.loads(object_store.read_registered(exact_prepared(db, object_store, task_id, submission_ref)))
            if (submission['source'] != event.payload['owner_principal_ref']
                    or submission['data'].get('user_collaboration') != action.material()
                    or submission['data']['base_net_ref'] != data['expected_runtime_ref']
                    or canonical_json(submission['data']['candidate']) != canonical_json(authored)):
                raise RegistryConflict('final adoption differs from immutable original request/action binding')
        receipt = documents[0]
        expected = {'decision_ref': ref_payload(action.reference), 'operation': action.kind,
            'result_revision_ref': data['result_revision_ref'], 'result_ref': result_ref,
            'transaction_id': str(transaction_id)}
        if (receipt['project_ref'] != project['self_ref'] or receipt['session_ref'] != session['self_ref']
                or receipt['data'] != expected):
            raise RegistryConflict('action receipt differs from atomic original result')
        return
    if action.kind == 'source_read':
        if collab or not observer_changes or not session['data']['owner']:
            raise RegistryConflict('source access action must wrap original observer publication')
        for project in projects:
            require_owner(store, db, task_id, project, session)
        return
    if branches or adoptions or observer_changes or len(objects) != 1 or len(documents) != 1 or relations:
        raise RegistryConflict('collaboration control facts require a separate original transaction')
    document = documents[0]
    kind = collab[0].object_type.removeprefix('user_collaboration_').split('/')[0]
    if action.kind != kind or idempotency_key != command_key(kind, document['command_id']):
        raise RegistryConflict('collaboration fact command kind differs')
    if len(events) != 2 or sorted(e.event_type for e in events) != ['object_version_published/v1', 'transaction_committed/v1']:
        raise RegistryConflict('collaboration fact requires only its publication and terminal')
    if kind == 'session':
        return
    if document['session_ref'] != session['self_ref']:
        raise RegistryConflict('request actor is inconsistent with trusted HOST session')
    data = document['data']
    if kind == 'execution':
        if (document['project_ref'] is not None or canonical_json(data) != action.creation
                or data['author_session_ref'] != session['self_ref']):
            raise RegistryConflict('execution binding requires the trusted original personal-owner launch')
        return
    if kind == 'delivery':
        launch = None if data['execution_ref'] is None else exact(store, db, task_id, data['execution_ref'], EXECUTION)
        if launch is not None and (launch['session_ref'] != session['self_ref']
                or data['witness']['run_ref'] != launch['data']['run_ref']):
            raise RegistryConflict('delivery differs from canonical actor execution launch')
        source_id = session['data']['origin_source'] if launch is None else launch['data']['source_id']
        source_task = session['data']['origin_task_ref'] if launch is None else launch['data']['source_task_ref']
        principal = session['data']['origin_principal_ref'] if launch is None else launch['data']['principal_ref']
        if (document['project_ref'] is not None or canonical_json(data) != action.creation
                or data['source_id'] != source_id
                or data['source_task_ref'] != source_task
                or data['author_session_ref'] != session['self_ref']
                or data['witness']['principal_ref'] != principal):
            raise RegistryConflict('delivery requires trusted original personal-owner evidence')
        received = exact_prepared(db, descriptor_store(store), task_id, data['received_text_ref'])
        raw = descriptor_store(store).read_registered(received, max_bytes=6 * 262144 + 2)
        if (received.media_type != 'application/json' or received.metadata['producer_ref'] != document['publisher_bootstrap_ref']
                or hashlib.sha256(raw).hexdigest() != data['witness']['stored_digest']):
            raise RegistryConflict('received TEXT bytes or target copy publisher differs')
        from ..user_collaboration_source import strict_proposal_json
        decoded = json.loads(raw)
        if type(decoded) is not str or hashlib.sha256(decoded.encode('utf-8')).hexdigest() != data['witness']['text_digest']:
            raise RegistryConflict('delivery decoded TEXT differs from original successful output')
        strict_proposal_json(decoded.encode('utf-8'))
        return
    if kind == 'project':
        if (not session['data']['owner'] or projects or document['project_ref'] is not None
                or data['owner_session_ref'] != session['self_ref']):
            raise RegistryConflict('only original owner may enable immutable task protection')
        exact(store, db, task_id, data['branch_ref'], 'collaboration_branch/v1')
        if data['runtime_net_ref'] is not None:
            exact(store, db, task_id, data['runtime_net_ref'], 'net_instance/v1')
        return
    project = exact(store, db, task_id, document['project_ref'], PROJECT)
    if kind == 'participant':
        subject = exact(store, db, task_id, data['subject_session_ref'], SESSION)
        current = participant(store, db, task_id, project, subject['self_ref'])
        if data['predecessor_ref'] != (None if current is None else current['self_ref']):
            raise RegistryConflict('participant predecessor is stale')
        if data['status'] == 'joined':
            if (subject['self_ref'] != session['self_ref'] or current is None
                    or current['data']['status'] != 'invited' or current['data']['role'] != data['role']):
                raise RegistryConflict('join requires the exact target-issued invitation and actor')
        else:
            require_owner(store, db, task_id, project, session)
    elif kind == 'proposal':
        _proposal(store, db, task_id, project, session, data)
    elif kind == 'review':
        proposal = exact(store, db, task_id, data['proposal_ref'], PROPOSAL)
        if proposal['project_ref'] != project['self_ref'] or canonical_json(data['exact_proposal']) != canonical_json(proposal['data']):
            raise RegistryConflict('review must pin the entire exact proposal')
        if data['disposition'] != 'comment':
            require_owner(store, db, task_id, project, session)
        else:
            require_propose(store, db, task_id, project, session['self_ref'])
    elif kind == 'decision':
        require_owner(store, db, task_id, project, session)
        _decision(store, db, task_id, project, data)
    else:
        raise RegistryConflict('receipt cannot be issued independently of original acceptance')
