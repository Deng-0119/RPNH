from pathlib import Path
import base64
import json
import sys

def synthetic_row():
    return {'prompt':[{'role':'user','content':'Mark the synthetic Gmail message offline-message as STARRED. This is a local deterministic acceptance task.'}], 'info': {'task_name':'offline-synthetic-label', 'initial_state':{'meta':{'current_time':'2026-10-02T00:00:00Z'},'gmail':{'messages':[{'id':'offline-message','thread_id':'offline-thread','from_':'sender@example.test','to':['receiver@example.test'],'subject':'Offline acceptance','body_plain':'Verified business tool path','label_ids':['INBOX'],'date':1790899200000,'internal_date':1790899200000}]}}, 'assertions':[{'type':'gmail_message_has_label','message_id':'offline-message','label_id':'STARRED'}], 'zapier_tools':[]}}

def tool_steps(padding=0):
    return [{'tool':'api_search','arguments':{'query':'gmail modify message labels','top_k':2}}, {'tool':'base64_encode','arguments':{'text':'Verified business tool path'}}, *[{'tool':'base64_encode','arguments':{'text':f'padding-{i}'}} for i in range(padding)], {'tool':'api_fetch','arguments':{'method':'POST','url':'https://gmail.googleapis.com/gmail/v1/users/me/messages/offline-message/modify','params':None,'body':json.dumps({'addLabelIds':['STARRED'],'removeLabelIds':[]})}}]

def create_profile(root, steps):
    root.mkdir(parents=True, exist_ok=True)
    support=Path(__file__).parent.resolve()
    scenario=root/'scenario.json'
    scenario.write_text(json.dumps({'steps':steps, 'requests_log':str(root/'requests.jsonl')}))
    adapter=root/'adapter.json'
    adapter.write_text(json.dumps({'schema_version':'local_process_adapter_config/v1','adapter_kind':'local_process','model_condition':'offline-scripted-ab','argv':[sys.executable,str(support/'scripted_adapter.py'),str(scenario)],'probe_argv':[sys.executable,'-c','pass'],'env':{'PYTHONPATH':str(support),'RPNH_OFFLINE_NETWORK_LOG':str(root/'denied-network.jsonl')},'inherit_env':[]}))
    selection=root/'selection.json'
    selection.write_text(json.dumps({'schema_version':'llm_execution_selection/v1','adapter_kind':'local_process','model_condition':'offline-scripted-ab','adapter_config_path':str(adapter),'timeout_seconds':30,'max_output_tokens':8192,'max_response_bytes':32*1024*1024}))
    return selection
