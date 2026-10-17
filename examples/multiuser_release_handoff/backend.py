"""Trusted transport glue: original Registry reads and original source PN decide.

The mutex serializes commands to one source writer; it is never resource access
authority. Long reads retain native PN claims after this mutex is released.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pickle
import socketserver
import threading
import time
import uuid

from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.identities import TypedId
from . import plugin
from .delegation import SourceDelegation, SourceBusy, StaleVersion
from .user_runtime import IndependentUser, grant_resource, read_resource
from .world import BusinessWorld


class ReleaseBackend:
    def __init__(self, root, users, *, stop, world=None, source=None,
                 participants=None, events_path=None):
        self.root, self.users, self.stop = Path(root), users, stop
        self.participants = participants if participants is not None else users
        self.reconciled_effects = {}
        self.command = threading.RLock()
        self.changed = threading.Condition(self.command)
        self.calls, self.reads, self.messages = {}, {}, []
        self.confirmations, self.finished_reads = {}, {}
        self.pending_edit = None
        self.incident, self.note, self.patch = None, None, None
        self.checklist = None
        self.initial_readers = set()
        self.revision_attempted, self.revision_published = False, False
        self.world = world if world is not None else BusinessWorld(self.root/'world', workspace_root=self.root)
        self.source = source if source is not None else SourceDelegation.create(run_dir=self.root/'canonical-source', body='',
            requesters={role:u.owner for role,u in users.items()},
            editors=(users['operations'].owner.principal_ref,), max_operations=512,workspace_root=self.root)
        # The current public runtime explicitly has one process-wide HOST
        # implementation inventory. Compose it once; per-operation allowed tools
        # remain the exact, narrower declarations in each private Registry.
        from cpn.rpnh.registration import Registration
        from cpn.rpnh.registry.operations import bind_operation_registration
        inventory, seen = Registration(), {}
        for owner in [*(u.owner for u in users.values()), self.source.owner]:
            for declaration in owner.registration.declarations():
                kind,key=declaration['kind'],declaration['key']
                if (kind,key) in seen:
                    if seen[kind,key]!=declaration:
                        raise ValueError('Conflicting HOST implementation inventories')
                    continue
                seen[kind,key]=declaration
                if kind=='schema': inventory.register_schema(key,declaration['schema'])
                else:
                    getattr(inventory,'register_'+kind)(key,owner.registration.resolve(kind,key),
                        identity=declaration['identity'],contracts=declaration['contracts'])
        bind_operation_registration(inventory)
        self.host_inventory=inventory
        source_session=self.source.host.create_session(origin_core=self.source.owner._core,
            principal_ref=self.source.owner.principal_ref,command_id='source-owner-session')
        self.source_user=IndependentUser(self.source.owner,None,self.source.source_id,
            'source.message',False,self.source.host,source_session)
        self.events = Path(events_path or self.root/'actual-tool-events.jsonl').open('a',buffering=1)

    def restore_projection(self, *, note, patch, messages, effects,
                           current_resource_ref=None, checklist=None, confirmations=None):
        """Install the selected clean cut, without reviving old foreign handles."""
        from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
        from cpn.rpnh.registry.module_execution import active_module_firings
        executable, _, marking = hydrate_module_runtime(self.source.owner._core)
        pending = [t for t in marking.tokens if t.state.epoch == marking.epoch
                   and t.state.consumed_by is None
                   and (t.state.place.startswith('source.request')
                        or t.state.place == 'source.write_request')]
        if pending or active_module_firings(self.source.owner._core, executable.net_ref):
            raise ValueError('continuation requires the selected clean Source cut')
        current = self.source.current()
        expected = effects['canonical_resource_ref'] if current_resource_ref is None else current_resource_ref
        if current['resource_ref'] != expected:
            raise ValueError('restored Source body differs from selected note')
        self.note, self.patch = note, patch
        self.messages = messages
        self.reconciled_effects = effects
        # result0/result1 are retained historical completed reads, not current
        # QA/support confirmations or claims in their selected initial cuts.
        self.reads, self.finished_reads = {}, {}
        self.confirmations = dict(confirmations or {})
        self.pending_edit = None
        self.initial_readers = set()
        self.revision_published = current['version'] != effects['canonical_resource_ref']['resource_version_id']
        self.revision_attempted = self.revision_published
        self.checklist = checklist
        self.incident = None

    def prepared(self, user, execution, call_id, name, arguments, prepared):
        packet = pickle.loads(prepared.packet)
        context = packet['context']
        role = next(role for role,u in self.users.items() if u is user)
        key = (context['firing_id'],call_id)
        with self.command:
            self.calls[key] = dict(user=user, role=role, execution=execution,
                tool=name, arguments=json.loads(json.dumps(dict(arguments))),
                identity={k:context[k] for k in ('operation_id','invocation_id','firing_id','call_id')},
                started_ref=dict(prepared.started_receipt_ref))

    def _bind(self, call):
        return self.source.bind_execution(origin_owner=call['user'].owner,
            execution=call['execution'], principal_ref=call['user'].owner.principal_ref,
            call_id=call['identity']['call_id'], tool_name=call['tool'], arguments=call['arguments'])

    def dispatch(self, frame):
        identity, tool, args = frame['identity'],frame['tool'],frame['arguments']
        key = (identity['firing_id'],identity['call_id'])
        with self.changed:
            call = self.calls.get(key)
            if call is None or call['identity'] != identity or call['tool'] != tool or call['arguments'] != args:
                return {'status':'denied','reason':'No exact native prepared tool invocation','effect':'not_executed'}
            if self.stop.is_set():
                return {'status':'stopped','effect':'not_executed'}
            started = time.monotonic()
            try:
                result = self._dispatch(call)
            except (SourceBusy, StaleVersion) as exc:
                if isinstance(exc,StaleVersion) and tool=='update_note':
                    # Version rejection precedes native request preparation.
                    self.pending_edit=None
                result = {'status':'busy' if isinstance(exc,SourceBusy) else 'stale',
                    'reason':str(exc),'effect':'not_executed'}
            except Exception as exc:
                # No broad retry: a business mutation may already have succeeded.
                self.stop.set()
                result = {'status':'manual_hold','error_type':type(exc).__name__,
                    'reason':str(exc),'effect':'requires_reconciliation'}
            self.events.write(json.dumps({'at':datetime.now(timezone.utc).isoformat(),
                'started_monotonic':started,'ended_monotonic':time.monotonic(),
                'role':call['role'],'identity':identity,'tool':tool,'arguments':args,
                'observation':result},ensure_ascii=False)+'\n')
            self.changed.notify_all()
            return result

    def _dispatch(self, call):
        role, tool, args = call['role'],call['tool'],call['arguments']
        if tool not in plugin.ROLE_TOOLS[role]:
            return {'status':'denied','effect':'not_executed','reason':'Tool outside exact role declaration'}
        if tool == 'discover':
            path = args.get('path','project')
            if path not in ('project','project/index','project/release-note'):
                return {'status':'denied','effect':'not_executed','reason':'Unknown published relative link'}
            binding = self._bind(call)
            result = json.loads(binding.request_json)
            return {'status':'found','path':path,'links':{'index':'project/index','release_note':'project/release-note'},
                'provenance':result,'body_included':False}
        if tool == 'create_file':
            if self.note is not None:
                if self.reconciled_effects:
                    original = self.reconciled_effects['create_file']
                    if args != original['arguments']:
                        raise ValueError('create_file differs from retained original effect')
                    return {'status':'existing/reconciled','effect':'not_executed',
                        'drive':self.world.read_note(self.note['id']),
                        'canonical':self.source.current(), 'original_receipt_ref':original['ref']}
                return {'status':'exists','effect':'not_executed','note':self.note}
            body = args.get('body','')
            binding = self._bind(call)
            edit = self.source.begin_edit(binding,expected_version=self.source.current()['version'],new_body=body)
            result = self.source.commit_edit(edit)
            note = self.world.create_file(**args)
            self.note = note
            return {'status':'created','drive':note,'canonical':result}
        if tool == 'read_note':
            if self.note is None:
                return {'status':'pending','effect':'not_executed'}
            if args.get('path','project/release-note') != 'project/release-note':
                return {'status':'denied','effect':'not_executed','reason':'Use discovered release-note link'}
            if any(r['role']==role for r in self.reads.values()):
                return {'status':'already_reading','reads':[k for k,r in self.reads.items() if r['role']==role]}
            active = self.source.begin_read(self._bind(call))
            read_id = uuid.uuid4().hex
            self.reads[read_id] = {'role':role,'active':active,'started':time.monotonic()}
            if role in ('qa','support') and not self.revision_published:
                self.initial_readers.add(role)
            return {'status':'reading','read_id':read_id,'drive_version':self.note['version'],**active.result}
        if tool == 'finish_read':
            row = self.reads.get(args.get('read_id'))
            if row is None or row['role']!=role:
                return {'status':'denied','effect':'not_executed','reason':'Read does not belong to this user'}
            result = self.source.finish_read(row['active'])
            self.finished_reads[args['read_id']] = dict(row,completed=time.monotonic(),receipt=result,
                findings=args.get('findings',''))
            del self.reads[args['read_id']]
            return {'read_id':args['read_id'],**result}
        if tool == 'update_note':
            if self.note is None:
                return {'status':'pending','effect':'not_executed'}
            retained_revision = self.reconciled_effects.get('update_note')
            if retained_revision is not None:
                current = self.source.current()
                if (args.get('body') == self.note['body']
                        and args.get('expected_version') in (
                            current['version'], retained_revision['arguments']['expected_version'])):
                    return {'status':'existing/reconciled', 'effect':'not_executed',
                        'drive':self.note, 'canonical':current,
                        'original_source_write_receipt_ref':retained_revision['ref']}
                return {'status':'denied','effect':'not_executed',
                    'reason':'The retained v2 revision is already published and QA-confirmed. '
                             'This continuation finalizes that version; inspect its permanent receipt and current note.'}
            self.revision_attempted = True
            expected, body = args['expected_version'],args['body']
            # A denied admission may leave the exact native request pending.
            # Preserve its originating tool identity instead of inventing a new one.
            if self.pending_edit is None:
                self.pending_edit = (self._bind(call),expected,body)
            binding,old_expected,old_body = self.pending_edit
            if (expected,body)!=(old_expected,old_body):
                return {'status':'busy','effect':'not_executed','reason':'Retry exact pending edit body/version first'}
            edit = self.source.begin_edit(binding,expected_version=expected,new_body=body)
            result = self.source.commit_edit(edit)
            mirror = self.world.update_note(self.note['id'],body,self.note['version'])
            self.note,self.pending_edit,self.revision_published = mirror,None,True
            return {'status':'updated','drive':mirror,'canonical':result,
                'resumed_request':json.loads(binding.request_json)['call_id']}
        if tool == 'create_branch':
            if self.reconciled_effects:
                original = self.reconciled_effects['create_branch']
                if args != original['arguments']:
                    raise ValueError('branch request differs from retained effect')
                return {'status':'existing/reconciled','effect':'not_executed',
                    **{k:v for k,v in original['output'].items() if k != 'status'},
                    'current_head':self.world._git('rev-parse','--verify',original['output']['ref']).strip(),
                    'original_receipt_ref':original['ref']}
            return {'status':'created',**self.world.create_branch(**args)}
        if tool in ('write_sql','write_checklist'):
            expected = 'sql/patches/sql_export_guard.sql' if tool=='write_sql' else 'qa/checklists/sql-export-guard_qa_checklist.md'
            if args.get('filePath')!=expected:
                return {'status':'denied','effect':'not_executed','reason':'Path outside declared role tool'}
            if tool == 'write_sql' and self.reconciled_effects:
                original = self.reconciled_effects['write_sql']
                if args != original['arguments']:
                    raise ValueError('SQL request differs from retained committed effect')
                observed = self.world.read_git_file(args['owner'], args['repo'], args['filePath'],
                    args['branch_name'], commit=original['output']['commit'])
                if observed['content'] != args['content']:
                    raise ValueError('retained SQL object differs')
                return {**original['output'], 'status':'existing/reconciled',
                    'effect':'not_executed', 'original_receipt_ref':original['ref']}
            if (tool == 'write_checklist' and self.checklist is not None
                    and args == self.checklist['arguments']):
                # Reuse the exact known success; a different request may correct
                # a prior known success through the original update semantics.
                previous = self.checklist['result']
                observed = self.world.read_git_file(args['owner'], args['repo'], args['filePath'],
                    args['branch_name'], commit=previous['commit'])
                if observed['content'] != args['content']:
                    raise ValueError('existing checklist object differs')
                return {**previous, 'status':'existing/reconciled', 'effect':'not_executed'}
            result=self.world.create_or_update_file(**args)
            if tool=='write_sql': self.patch=result
            if tool=='write_checklist': self.checklist={'arguments':dict(args), 'result':result}
            return {'status':'committed',**result}
        if tool == 'read_git_file':
            return {'status':'read',**self.world.read_git_file(**args)}
        if tool == 'create_incident':
            if self.incident is not None:
                fields = {k:v for k,v in args.items() if k != 'validation_evidence'}
                existing = json.loads((self.world.root/'incidents'/(self.incident['id']+'.json')).read_text())
                if (existing != self.incident or fields != existing['fields']
                        or args.get('validation_evidence') != existing.get('validation_evidence')):
                    raise ValueError('incident request differs from existing exact effect')
                return {'status':'existing/reconciled','effect':'not_executed','incident':existing}
            self.incident=self.world.create_incident(**args)
            return {'status':'created','incident':self.incident}
        if tool == 'handoff':
            recipient=args['recipient']
            if recipient not in self.participants:
                return {'status':'denied','effect':'not_executed','reason':'Unknown recipient'}
            ref=self.source.publish_message(self._bind(call))
            raw={'resource_id':str(ref.resource_id),'resource_version_id':str(ref.resource_version_id)}
            message={'sender':role,'recipient':recipient,'topic':args['topic'],'ref':ref,'observer':None,'delivered':False}
            self.messages.append(message)
            return {'status':'published','source_id':self.source.source_id,'resource_ref':raw,'topic':args['topic']}
        if tool == 'await_handoff':
            deadline=time.monotonic()+45
            while not self.stop.is_set():
                if role=='operations' and args.get('topic')=='readers_active' and self.initial_readers=={'qa','support'}:
                    return {'status':'observed','topic':'readers_active','source':'actual native read claims',
                        'readers':[{'role':r['role'],'read_id':k,'binding':r['active'].result} for k,r in self.reads.items() if r['role'] in ('qa','support')]}
                for m in self.messages:
                    if m['recipient']==role and not m['delivered'] and (not args.get('topic') or m['topic']==args['topic']):
                        # Native grants bind the current control sequence. A
                        # queued message can outlive intervening source PN work;
                        # its owner grants this exact immutable message at read
                        # time, under the same serialized source command lock.
                        observer=grant_resource(self.source_user,self.users[role],m['ref'],
                            expires_at=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),
                            command_id='deliver:'+uuid.uuid4().hex)
                        m['observer']=observer
                        result=read_resource(self.source_user,self.users[role],m['ref'],observer=m['observer'])
                        body=json.loads(result['bytes'])
                        m['delivered']=True
                        return {'status':'delivered','sender':m['sender'],'topic':m['topic'],
                            'source_id':self.source.source_id,'resource_ref':{'resource_id':str(m['ref'].resource_id),'resource_version_id':str(m['ref'].resource_version_id)},
                            'native_call_receipt':body}
                remaining=deadline-time.monotonic()
                if remaining<=0: return {'status':'pending','effect':'not_executed'}
                self.changed.wait(min(remaining,0.5))
            return {'status':'stopped','effect':'not_executed'}
        if tool == 'confirm_release':
            row=self.finished_reads.get(args.get('read_id'))
            current=self.source.current()
            if row is None or row['role']!=role or row['receipt']['version']!=current['version'] or args.get('note_version')!=current['version']:
                return {'status':'stale','effect':'not_executed','reason':'Current version requires an actual completed read'}
            if not self.revision_published or self.patch is None or args.get('sql_commit')!=self.patch['commit']:
                return {'status':'pending','effect':'not_executed','reason':'Revision and exact SQL commit required'}
            if role=='support' and (self.incident is None or args.get('incident_id')!=self.incident['id'] or self.confirmations.get('qa',{}).get('note_version')!=current['version']):
                return {'status':'pending','effect':'not_executed','reason':'Current QA confirmation and actual incident required'}
            timestamp=datetime.fromisoformat(args['final_verification_time'].replace('Z','+00:00'))
            if timestamp.tzinfo is None: raise ValueError('verification timestamp requires timezone')
            self.confirmations[role]=dict(args,received_at=datetime.now(timezone.utc).isoformat(),
                provenance=row['receipt'],native_call_receipt_ref=call['started_ref'])
            return {'status':'confirmed',**self.confirmations[role]}
        if tool == 'status':
            return {'status':'available','canonical':self.source.current(),'revision_published':self.revision_published,
                'sql_commit':None if self.patch is None else self.patch['commit'],
                'incident_id':None if self.incident is None else self.incident['id'],
                'confirmed_roles':{k:v['note_version'] for k,v in self.confirmations.items()},
                'now':datetime.now(timezone.utc).isoformat()}
        raise ValueError('Unhandled exact role tool')


class ReleaseServer:
    def __init__(self, endpoint, backend):
        self.endpoint,self.backend=Path(endpoint),backend
    def __enter__(self):
        backend=self.backend
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(0.1)
                frame=plugin.receive(self.request,lambda: None)
                result=backend.dispatch(frame)
                plugin.send(self.request,{'identity':frame['identity'],'observation':result})
        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads=True
        self.server=Server(str(self.endpoint),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        return self
    def __exit__(self,*_):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.endpoint.unlink(missing_ok=True)
