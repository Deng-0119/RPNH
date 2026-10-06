"""Synthetic adapter contracts only; no Core faults, body reads, or new runs."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode
from unittest.mock import patch
from cpn.frontend.checkpoint_view import (token_resource_target, _token_resource_metadata, _resource_ref,
    TokenResourceInvalid, TokenResourceStale, TokenResourceAccessChanged, validate_token_resource_response)
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.frontend.server import handle_request


def ref(kind, digit, stem=None):
    stem = stem or kind.split('/')[0]
    return {'entity_type':kind,'logical_id':stem+':'+digit*32,'version_id':stem+'_version:'+digit*32}
TOKEN=ref('petri_token/v1','1');PAIR={'resource_id':'resource:'+'2'*32,'resource_version_id':'resource_version:'+'3'*32}
SELECTED={'net_ref':ref('net_instance/v1','4'),'checkpoint_ref':ref('marking_checkpoint/v1','5'),'cut':75}
IDENTITY={'task_ref':ref('task/v1','6'),'run_ref':ref('native_run_identity/v1','7','run')}
CAPTURE={'head_ordinal':130,'writer_fencing_epoch':2}
TARGET={'token_ref':TOKEN,'resource_ref':PAIR,'expected_task_id':IDENTITY['task_ref']['logical_id'],'expected_capture':CAPTURE}
OCCURRENCE={'token_ref':TOKEN,'resource_ref':PAIR,'place':'p','active_in_checkpoint':True}

def material():
    row={'object_type':'resource_version/v1','logical_id':PAIR['resource_id'],'version_id':PAIR['resource_version_id'],
         'size':6,'media_type':'application/json','published_event_id':'event:'+'8'*32,'transaction_id':'tx'}
    metadata={'resource_id':PAIR['resource_id'],'resource_version_id':PAIR['resource_version_id'],
              'size':6,'media_type':'application/json','content_schema_ref':None,'task_ref':IDENTITY['task_ref']}
    reads=SimpleNamespace(cache={canonical_json(_resource_ref(PAIR)):(row,metadata)},
        events=[SimpleNamespace(event_id=row['published_event_id'],transaction_id='tx',ordinal=40)],
        verified_transactions={'tx'},transaction_commit_event=lambda _:SimpleNamespace(ordinal=42))
    return reads,row,metadata

def payload():
    reads,_,_=material()
    source={'mode':'registry_current','task_id':TARGET['expected_task_id'],'run_dir':'/display-only/run',
            'net_ref':deepcopy(SELECTED['net_ref']),'verified_head_ordinal':75,'writer_fencing_epoch':2}
    return deepcopy({'schema_version':'rpnh/checkpoint_view/v1','selector':deepcopy(SELECTED),'capture':deepcopy(CAPTURE),
        'frame':{'schema_version':'rpnh/dashboard/v1','source':source,
            'position':{'mode':'history','cursor':75,'latest_head':130},'net':{'schema_version':'rpnh/net_view/v1',
            'source':deepcopy(source),'marking':{'checkpoint_ref':deepcopy(SELECTED['checkpoint_ref'])},'summary':{},'nodes':[{'id':'p','label':'p','kind':'place','category':'place','hidden_by_default':False,'tokens':[deepcopy(OCCURRENCE)]}],'edges':[]}},
        'token_resource_metadata':_token_resource_metadata(reads,[OCCURRENCE],IDENTITY,SELECTED,CAPTURE,TARGET)})

def path(target=TARGET):
    return '/api/v2/checkpoint-view?'+urlencode({**{k:json.dumps(v) if isinstance(v,dict) else v for k,v in SELECTED.items()},'token_resource':json.dumps(target)})

class Contracts(unittest.TestCase):
    def test_target_exact_keys_types_ids_and_safe_capture(self):
        self.assertEqual(token_resource_target(TARGET),TARGET)
        mutations=[lambda v:v.update(extra=1),lambda v:v['token_ref'].update(extra=1),
            lambda v:v['resource_ref'].pop('resource_id'),lambda v:v.update(resource_ref=None),
            lambda v:v['resource_ref'].update(resource_id='task:'+'2'*32),
            lambda v:v['token_ref'].update(logical_id='petri_token:ABC'),
            lambda v:v.update(expected_task_id='task:bad'),lambda v:v['expected_capture'].update(extra=1),
            lambda v:v['expected_capture'].update(head_ordinal=True),lambda v:v['expected_capture'].update(head_ordinal=2**53),
            lambda v:v['expected_capture'].update(writer_fencing_epoch=float('nan')),
            lambda v:v['expected_capture'].update(writer_fencing_epoch=-1)]
        for mutate in mutations:
            value=deepcopy(TARGET);mutate(value)
            with self.assertRaises(TokenResourceInvalid):token_resource_target(value)

    def test_cached_projection_is_finite_allowlist_and_no_reader_calls(self):
        reads,row,metadata=material()
        for key in ('summary','descriptors','extensions','origin','reference_provenance','storage_locator','rawpayload','producer_ref'):
            row[key]=metadata[key]='POISON-PRIVATE'
        value=_token_resource_metadata(reads,[OCCURRENCE],IDENTITY,SELECTED,CAPTURE,TARGET)
        self.assertNotIn('POISON-PRIVATE',json.dumps(value))
        self.assertEqual(value['registered_metadata'],{'byte_size':6,'media_type':'application/json','content_schema_ref':None})
        self.assertEqual(value['registration']['publication_transaction_commit_ordinal'],42)
        self.assertIsNone(value['verification']['actual_verified_byte_size'])
        self.assertIs(value['verification']['body_read_by_metadata_projection'],False)
        self.assertNotIn('body_not_read',value['verification'])
        self.assertNotIn('visible_at_commit',value['registration'])
        validate_token_resource_response(payload(),TARGET)

    def test_wrong_occurrence_or_pair_never_resolves(self):
        for mutate in (lambda v:v['token_ref'].update(version_id='petri_token_version:'+'a'*32),
            lambda v:v['resource_ref'].update(resource_id='resource:'+'a'*32),lambda v:v.update(resource_ref=None)):
            occurrence=deepcopy(OCCURRENCE);mutate(occurrence)
            with self.assertRaises(TokenResourceInvalid):_token_resource_metadata(material()[0],[occurrence],IDENTITY,SELECTED,CAPTURE,TARGET)
        with self.assertRaises(TokenResourceInvalid):_token_resource_metadata(material()[0],[OCCURRENCE,OCCURRENCE],IDENTITY,SELECTED,CAPTURE,TARGET)

    def test_cached_metadata_identity_registration_and_transaction_consistency(self):
        for mutate in (lambda r,row,m:row.update(logical_id='resource:'+'a'*32),lambda r,row,m:m.update(size=7),
            lambda r,row,m:m.update(resource_version_id='resource_version:'+'a'*32),lambda r,row,m:m.update(task_ref={}),
            lambda r,row,m:m.update(media_type='text/plain'),lambda r,row,m:r.verified_transactions.clear(),
            lambda r,row,m:setattr(r,'transaction_commit_event',lambda _:SimpleNamespace(ordinal=76)),
            lambda r,row,m:setattr(r.events[0],'ordinal',43)):
            reads,row,metadata=material();mutate(reads,row,metadata)
            with self.assertRaises(ValueError):_token_resource_metadata(reads,[OCCURRENCE],IDENTITY,SELECTED,CAPTURE,TARGET)

    def test_response_rejects_contamination_substitution_and_false_verification(self):
        mutations=[lambda v:v['token_resource_metadata'].update(locator='secret'),
            lambda v:v['token_resource_metadata']['registered_metadata'].update(summary='secret'),
            lambda v:v['token_resource_metadata']['scope'].update(cut=130),
            lambda v:v['token_resource_metadata']['scope']['run_ref'].update(logical_id='task:'+'7'*32),
            lambda v:v['token_resource_metadata']['occurrence'].update(place='wrong'),
            lambda v:v['token_resource_metadata']['verification'].update(actual_verified_byte_size=6),
            lambda v:v['token_resource_metadata']['registration'].update(publication_transaction_commit_ordinal=76),
            lambda v:v['token_resource_metadata']['registered_metadata'].update(byte_size=True)]
        for mutate in mutations:
            value=payload();mutate(value)
            with self.assertRaises(ValueError):validate_token_resource_response(value,TARGET)

    def test_route_get_head_exact_target_and_no_target_compatibility(self):
        calls=[]
        class Provider:
            def checkpoint_view(self,**kwargs):calls.append(kwargs);return payload()
        self.assertEqual(handle_request(Provider(),'GET',path()).status,200)
        self.assertEqual(calls[-1],{**SELECTED,'token_resource':TARGET})
        self.assertEqual(handle_request(Provider(),'HEAD',path()).body,b'')
        self.assertEqual(handle_request(Provider(),'POST',path()).status,405)
        handle_request(Provider(),'GET',path().split('&token_resource=')[0])
        self.assertEqual(calls[-1],SELECTED)

    def test_optin_rejects_every_contradictory_frame_axis_and_occurrence_placement(self):
        changes = [
            lambda v:v['frame']['source']['net_ref'].update(logical_id='net_instance:'+'0'*32),
            lambda v:v['frame']['net']['source']['net_ref'].update(version_id='net_instance_version:'+'0'*32),
            lambda v:v['frame']['net']['marking']['checkpoint_ref'].update(logical_id='marking_checkpoint:'+'0'*32),
            lambda v:v['frame']['position'].update(cursor=130),
            lambda v:v['frame']['position'].update(latest_head=129),
            lambda v:v['frame']['position'].update(mode='live'),
            lambda v:v['frame']['source'].update(writer_fencing_epoch=3),
            lambda v:v['frame']['net']['source'].update(writer_fencing_epoch=3),
            lambda v:v['frame']['source'].update(verified_head_ordinal=130),
            lambda v:v['frame']['net']['source'].update(verified_head_ordinal=130),
            lambda v:v['frame']['net']['source'].update(task_id='task:'+'0'*32),
            lambda v:v['frame']['net']['source'].update(run_dir='/another-run'),
            lambda v:v['frame']['net']['nodes'][0].update(id='another-place'),
            lambda v:v['frame']['net']['nodes'][0].update(kind='transition'),
            lambda v:v['frame']['net']['nodes'][0]['tokens'][0].update(active_in_checkpoint=1),
            lambda v:v['frame']['position'].pop('cursor'),
        ]
        for change in changes:
            value=payload();change(value)
            class Provider:
                def checkpoint_view(self,**kwargs):return value
            result=handle_request(Provider(),'GET',path())
            self.assertEqual(result.status,400)
            self.assertEqual(json.loads(result.body),{'error':'invalid_target'})

    def test_wrong_frame_containers_are_fixed_errors_without_extra_reader_work(self):
        locations=[('frame',),('frame','source'),('frame','position'),('frame','net'),
            ('frame','net','source'),('frame','net','marking'),('frame','net','nodes'),
            ('frame','net','nodes',0),('frame','net','nodes',0,'tokens'),
            ('frame','net','nodes',0,'tokens',0)]
        for location in locations:
            for replacement in (None, 'PRIVATE-CONTAINER', 7, False):
                value=payload();container=value
                for key in location[:-1]:container=container[key]
                container[location[-1]]=replacement
                class Provider:
                    def checkpoint_view(self,**kwargs):return value
                with self.subTest(location=location,replacement=replacement):
                    result=handle_request(Provider(),'GET',path())
                    self.assertEqual(result.status,400)
                    self.assertEqual(json.loads(result.body),{'error':'invalid_target'})
        for location,replacement in [(('frame','position'),[]),(('frame','net','source'),[]),
            (('frame','net','nodes'),{}),(('frame','net','nodes',0),[]),
            (('frame','net','nodes',0,'tokens'),{}),(('frame','net','nodes',0,'tokens',0),[])]:
            value=payload();container=value
            for key in location[:-1]:container=container[key]
            container[location[-1]]=replacement
            class Provider:
                def checkpoint_view(self,**kwargs):return value
            self.assertEqual(handle_request(Provider(),'GET',path()).status,400)

    def test_boolean_capture_is_not_an_integer_and_no_target_route_stays_legacy(self):
        value=payload();target=deepcopy(TARGET)
        target['expected_capture']['writer_fencing_epoch']=1
        value['capture']['writer_fencing_epoch']=value['token_resource_metadata']['scope']['capture']['writer_fencing_epoch']=True
        with self.assertRaises(ValueError):validate_token_resource_response(value,target)
        # No optional target: historical custom-provider shape is still governed by the old net validator only.
        value=payload();value['frame'].pop('position');value.pop('token_resource_metadata')
        class Legacy:
            def checkpoint_view(self,**kwargs):return value
        self.assertEqual(handle_request(Legacy(),'GET',path().split('&token_resource=')[0]).status,200)

    def test_extended_root_containers_return_fixed_get_and_empty_head_errors(self):
        for bad in (None, [], 7, False, 'PRIVATE-CONTAINER'):
            class Provider:
                def checkpoint_view(self,**kwargs):return bad
            for method in ('GET','HEAD'):
                with self.subTest(container=type(bad).__name__,method=method):
                    result=handle_request(Provider(),method,path())
                    self.assertEqual(result.status,400)
                    if method=='GET':
                        self.assertEqual(json.loads(result.body),{'error':'invalid_target'})
                    else:
                        self.assertEqual(result.body,b'')

    def test_route_duplicate_extra_nonfinite_and_error_redaction(self):
        calls=[]
        class Provider:
            def checkpoint_view(self,**kwargs):calls.append(kwargs);return payload()
        for query in (path()+'&token_resource={}',path()+'&unexpected=x',path(None),path({**TARGET,'extra':1}),
            path().split('&token_resource=')[0]+'&'+urlencode({'token_resource':json.dumps(TARGET)[:-1]+',"expected_task_id":"duplicate"}'})):
            result=handle_request(Provider(),'GET',query)
            self.assertEqual(result.status,400);self.assertEqual(json.loads(result.body),{'error':'invalid_target'})
        self.assertEqual(calls,[])
        for error,status,code in ((TokenResourceStale('private'),409,'stale_observation'),
            (TokenResourceAccessChanged('private'),403,'access_changed'),(RuntimeError('/private/path SQL'),503,'read_failed')):
            class Failing:
                def checkpoint_view(self,**kwargs):raise error
            result=handle_request(Failing(),'GET',path())
            self.assertEqual(result.status,status);self.assertEqual(json.loads(result.body),{'error':code})

if __name__=='__main__':unittest.main()
