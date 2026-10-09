from pathlib import Path
import json
root=Path('rpnh-parent-child-core-implementation/source')
p=root/'cpn/rpnh/registry/parent_bound.py';t=p.read_text().replace("    _require_evidence(core.event_store,'bound_bootstrap',acceptance)","    _require_evidence(core.event_store,'bound_bootstrap',acceptance)\n    _validate_acceptance_copy(core.event_store,acceptance)").replace("        _require_evidence(store,'bound_bootstrap',body['acceptance'])","        _require_evidence(store,'bound_bootstrap',body['acceptance'])\n        _validate_acceptance_copy(store,body['acceptance'])")
t=t.replace("    run=snap.obj(marker['child_run_ref']);genesis=snap.obj(marker['genesis_ref'])","    _validate_acceptance_copy(snap.store,marker['acceptance'])\n    run=snap.obj(marker['child_run_ref']);genesis=snap.obj(marker['genesis_ref'])")
p.write_text(t)
p=root/'tests/parent_child_fixtures.py';t=p.read_text();old="""    external={'parent_source_id':body['parent']['source_id'],'acceptance_json':h7._json(body).decode(),
        'initial_declaration_digest':values[3].materials['initial_declaration_digest']}
"""
new="""    with parent._core.event_store.connect() as db:
        row=db.execute('SELECT transaction_id FROM objects WHERE version_id=?',(str(accepted.version_id),)).fetchone()
        fact=db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='parent_child_recorded/v1'",(row[0],)).fetchone()
        committed=db.execute("SELECT ordinal FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",(row[0],)).fetchone()
    external={'parent_source_id':body['parent']['source_id'],'acceptance_json':h7._json(body).decode(),
        'intent_json':h7._json(parent._core.get_version(values[4].version_id).metadata).decode(),
        'acceptance_transaction_id':row[0],'acceptance_event_id':fact[0],'acceptance_commit_ordinal':committed[0],
        'initial_declaration_digest':values[3].materials['initial_declaration_digest']}
"""
assert old in t;t=t.replace(old,new);p.write_text(t)
props={key:{'type':'string','minLength':1} for key in ['parent_source_id','acceptance_json','intent_json']}
props.update(acceptance_transaction_id={'type':'string','pattern':'^transaction:[a-f0-9]{32}$'},
    acceptance_event_id={'type':'string','pattern':'^event:[a-f0-9]{32}$'},
    acceptance_commit_ordinal={'type':'integer','minimum':1},
    initial_declaration_digest={'type':'string','pattern':'^[a-f0-9]{64}$'})
shape={'type':'object','additionalProperties':False,'properties':props,'required':list(props)}
for filename,key in [('registry_v1/parent_bound_bootstrap.v1.schema.json','acceptance'),('rpnh/parent_origin_capability.v1.schema.json','parent_acceptance')]:
 p=root/'cpn/schemas'/filename;d=json.loads(p.read_text());d['properties'][key]=shape;p.write_text(json.dumps(d,indent=2)+'\n')
