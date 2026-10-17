"""Save exact official Viewer projections while the actual owners are available."""
from datetime import datetime, timezone
import json
from pathlib import Path

from cpn.frontend.dashboard import RegistryDashboard, exact


def capture(owner, destination):
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    provider=RegistryDashboard(owner._core.run_dir,catalog=owner._core.catalog)
    frame=provider.dashboard()
    (destination/'frame.json').write_text(json.dumps(frame,ensure_ascii=False,indent=2)+'\n')
    executions=[]
    source=frame['source']
    marking=frame['net'].get('marking')
    if marking:
        for node in frame['net']['nodes']:
            for firing in node.get('runtime',{}).get('firings',[]):
                execution=provider.execution_nets(net_ref=exact(source['net_ref']),
                    checkpoint_ref=exact(marking['checkpoint_ref']),cut=frame['position']['cursor'],
                    firing_ref=exact(firing['firing_ref']),
                    expected_source={k:source[k] for k in ('task_id','run_dir')},
                    expected_capture={'head_ordinal':source['verified_head_ordinal'],
                                      'writer_fencing_epoch':source['writer_fencing_epoch']})
                executions.append(execution)
    (destination/'executions.json').write_text(json.dumps(executions,ensure_ascii=False,indent=2)+'\n')
    (destination/'provenance.json').write_text(json.dumps({
        'at':datetime.now(timezone.utc).isoformat(),'actual_task':str(owner.identity.task_ref),
        'principal':str(owner.principal_ref),'source':source,'position':frame['position'],
        'provider':'cpn.frontend.dashboard.RegistryDashboard','executed_new_business':False,
        'execution_export_count':len(executions)},indent=2)+'\n')
    return frame
