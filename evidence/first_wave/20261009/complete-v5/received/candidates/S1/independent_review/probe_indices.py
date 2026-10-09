"""Mutation only in test wrapper before actual Registry commit."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
sys.path.insert(0,str(Path(__file__).parent))
from probe_lowlevel import scope,make_claim
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.firing_authority import canonical_invocation, verify_transition_firing
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.identities import new_id

for mode in ['missing_index','missing_reference_index','foreign_index','duplicate_index','wrong_logical']:
    with TemporaryDirectory(prefix='static-lease-indices-') as tmp:
        owner=scope['lease_owner'](Path(tmp)/'run')
        claim=make_claim(owner,'correct')
        core=owner._core
        original_begin=core.begin
        def begin(**kwargs):
            tx=original_begin(**kwargs)
            original_prewrite=tx.prewrite
            def prewrite(**kw):
                if kw['object_type']=='transition_firing/v1':
                    m=dict(kw['metadata'])
                    if mode=='missing_index':m['claimed_input_version_ids']=[]
                    elif mode=='missing_reference_index':m['claimed_input_version_ids']=sorted(str(r.version_id) for r in claim.consumed_input_refs)
                    elif mode=='foreign_index':m['claimed_input_version_ids']=[str(new_id('petri_token_version'))]
                    elif mode=='duplicate_index':m['claimed_input_version_ids']=m['claimed_input_version_ids']*2
                    elif mode=='wrong_logical':
                        m['claimed_input_refs']=[dict(x) for x in m['claimed_input_refs']]
                        m['claimed_input_refs'][0]['logical_id']=str(new_id('petri_token'))
                    kw={**kw,'metadata':m,'payload':canonical_json(m)}
                return original_prewrite(**kw)
            tx.prewrite=prewrite
            return tx
        core.begin=begin
        try:
            a=InvocationLifecycle(core).admit_firing(claim,idempotency_key=f'review:{mode}')
            print(mode,'COMMIT ACCEPTED',flush=True)
            try:
                kernel=_ResourceServiceKernel(core)
                verify_transition_firing(core,kernel,canonical_invocation(core,kernel,a.context.invocation_ref))
                print(mode,'READ ACCEPTED',flush=True)
            except Exception as e: print(mode,'READ REJECTED',type(e).__name__,str(e),flush=True)
        except Exception as e: print(mode,'COMMIT REJECTED',type(e).__name__,str(e),flush=True)
