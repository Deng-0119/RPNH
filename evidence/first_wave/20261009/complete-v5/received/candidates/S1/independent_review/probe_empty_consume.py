from pathlib import Path
from tempfile import TemporaryDirectory
import sys
sys.path.insert(0,str(Path(__file__).parent))
from probe_lowlevel import scope,make_claim
from dataclasses import replace
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.firing_preflight import preflight_module_firing
from cpn.rpnh.registry.module_gateway import start_module_firing

with TemporaryDirectory(prefix='static-lease-empty-consume-') as tmp:
    owner=scope['lease_owner'](Path(tmp)/'run')
    claim=replace(make_claim(owner,'correct'),consumed_input_refs=())
    e,s,m=hydrate_module_runtime(owner._core)
    pf=preflight_module_firing(s,m,transition_id='step.run',claimed_token_refs=claim.claimed_input_refs)
    for index in (1,2):
        try:
            adm=InvocationLifecycle(owner._core).admit_firing(replace(claim,attempt_index=index),idempotency_key=f'review:empty:{index}')
            print(index,'ADMITTED',flush=True)
            kernel,repo=owner.operation_repository()
            ex=start_module_firing(owner._core,kernel,repo,adm.context.invocation_ref,preflight=pf,idempotency_key=f'review:start:{index}')
            print(index,'STARTED',flush=True)
        except Exception as exc: print(index,'REJECTED',type(exc).__name__,str(exc),flush=True)
