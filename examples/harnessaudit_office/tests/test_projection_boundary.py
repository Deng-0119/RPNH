"""Run exact pure functions from the patch; receipt storage is an explicit double."""
from pathlib import Path
import ast
import copy
import json
from typing import Any, Mapping
import pytest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'src/rpnh_ha/registry_export.py'
selected={'_verify_return_boundary','_capture_complete'}
tree=ast.parse(SOURCE.read_text())
ns={'Any':Any,'Mapping':Mapping,'loads':json.loads}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in selected],type_ignores=[]),str(SOURCE),'exec'),ns)
verify=ns['_verify_return_boundary'];complete=ns['_capture_complete']
START={'resource_id':'resource:start','resource_version_id':'resource_version:start'}
END={'resource_id':'resource:end','resource_version_id':'resource_version:end'}
INV={'entity_type':'invocation/v1','version_id':'invocation_version:one'}
def material():
 start={'state':'started','call_id':'call_1','selector':'synthetic/write','arguments':{'x':1},'registration':{'key':'registered:write'},'producer_invocation_ref':INV,'caller_firing_ref':{'version_id':'firing:one'}}
 action={'outcome':'returned','action_identity_key':'call_1','selector':'synthetic/write','arguments':{'x':1},'registration_key':'registered:write','started_receipt_ref':START,'terminal_receipt_ref':END,'output':{'raw_result':'record created'}}
 terminal={**copy.deepcopy(start),'state':'returned','started_receipt_ref':START,'output':action['output']}
 return start,action,terminal
class Snapshot:
 def __init__(self,terminal):self.terminal=terminal;self.reads=[]
 def resource(self,ref):
  assert ref==END;self.reads.append(ref)
  return {},json.dumps(self.terminal).encode()
def test_return_boundary_does_not_claim_model_consumption():
 start,action,terminal=material();snapshot=Snapshot(terminal)
 result=verify(snapshot,action,start,INV)
 assert result['proves_model_input'] is False
 assert result['terminal_receipt_ref']==END and len(snapshot.reads)==1
 assert 'model_visible_result_ref' not in result
@pytest.mark.parametrize('outcome',['outcome_unknown','failed','rejected'])
def test_no_backend_only_or_unknown_result_is_promoted(outcome):
 start,action,terminal=material();action['outcome']=outcome;snapshot=Snapshot(terminal)
 with pytest.raises(ValueError):verify(snapshot,action,start,INV)
 assert snapshot.reads==[]
@pytest.mark.parametrize('field',['call_id','selector','arguments','registration','producer_invocation_ref','caller_firing_ref'])
def test_changed_terminal_material_is_rejected(field):
 start,action,terminal=material();terminal[field]='different'
 with pytest.raises(ValueError):verify(Snapshot(terminal),action,start,INV)
def test_mismatched_output_rejected():
 start,action,terminal=material();terminal['output']={'raw_result':'not the same'}
 with pytest.raises(ValueError):verify(Snapshot(terminal),action,start,INV)
def test_missing_terminal_and_missing_start_rejected():
 start,action,terminal=material();terminal['state']='outcome_unknown'
 with pytest.raises(ValueError):verify(Snapshot(terminal),action,start,INV)
 with pytest.raises(ValueError):verify(Snapshot(terminal),action,None,INV)
def test_wrong_invocation_rejected():
 start,action,terminal=material()
 with pytest.raises(ValueError):verify(Snapshot(terminal),action,start,{'version_id':'other'})
def base():
 return dict(exported_managed=5,managed_count=5,handoff_communication_count=6,cross_node_reads=6,terminal_evidence_present=True,terminal_rows_count=1)
def test_execution_completeness_separate_from_model_visibility():
 assert complete(**base(),unproven_managed_results=1)
@pytest.mark.parametrize('field,value',[('exported_managed',4),('handoff_communication_count',5),('unmatched_product_reads',1),('unproven_visible_reads',1),('terminal_rows_count',0)])
def test_other_evidence_gates_remain_closed(field,value):
 args=base();args[field]=value
 assert not complete(**args,unproven_managed_results=1)
def test_no_raw_event_or_receipt_override_of_business_semantics():
 s=SOURCE.read_text()
 assert 'emit_observation = False' not in s
 assert 'registered_tool_return' in s
 assert 'model_input_visibility": "unproven"' in s
 assert '_verify_return_boundary(' in s
