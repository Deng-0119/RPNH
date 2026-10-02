"""Real native worker + real pinned upstream. No SDK doubles or APIs."""
import json
import os
from pathlib import Path
import tempfile
import pytest
from rpnh_ab.broker import Broker
from rpnh_ab.native import build_spec, run, project_registry
from rpnh_ab.upstream import Upstream
from offline_support.fixtures import synthetic_row, tool_steps, create_profile

UPSTREAM = os.environ.get('RPNH_AB_UPSTREAM')
pytestmark = pytest.mark.skipif(not UPSTREAM, reason='set RPNH_AB_UPSTREAM to real pinned upstream')

@pytest.fixture(scope='module')
def upstream():
    return Upstream(Path(UPSTREAM))

@pytest.mark.parametrize('padding',[0,47])
def test_real_native_scripted_world_and_rubric(upstream,padding):
    # Short socket path is an existing native owner constraint, not hidden by mocks.
    temp_parent = Path(os.environ.get('RPNH_AB_TMPDIR', tempfile.gettempdir()))
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='abn-', dir=temp_parent) as tmp:
        root=Path(tmp); attempt=root/'attempt'; attempt.mkdir()
        steps=tool_steps(padding); row=synthetic_row()
        profile=create_profile(root/'profile',steps)
        direct=upstream.start(row); state=upstream.start(row)
        expected=[upstream.dispatch(direct,x['tool'],x['arguments']) for x in steps]
        with Broker(upstream,state,attempt,'offline-native') as broker:
            spec=build_spec(root/'native',profile,broker.endpoint,broker.run_id,row['prompt'],upstream.schemas)
            assert spec.max_attempts_per_stage is None
            lifecycle=run(spec,root/'control')
        assert lifecycle['terminal'], lifecycle
        assert lifecycle['process_exit_confirmed'] and broker.closed
        events=[json.loads(x) for x in (attempt/'tool_events.jsonl').read_text().splitlines()]
        outputs=[x['response']['result'] for x in events if x['kind']=='dispatch_finished']
        assert outputs==expected
        assert upstream.dump_world(state)==upstream.dump_world(direct)
        assert upstream.score(upstream.dump_world(state), state['initial_state'], state['info'])['task_completed_correctly']==1.0
        facts=project_registry(root/'native', attempt)
        assert facts['actual_model_calls']>=len(steps)+1
        assert not (root/'profile/denied-network.jsonl').exists()
        archive=os.environ.get('RPNH_AB_EVIDENCE_DIR')
        if archive:
            import shutil
            shutil.copytree(root,Path(archive)/f'native-padding-{padding}',dirs_exist_ok=True)
