"""Continue only after correcting a local import-diagnostic module name."""
import json
from pathlib import Path
import run_lane as lane

OUT, ROOT, PY = lane.OUT, lane.ROOT, lane.PY
repo = ROOT / '.v26/r2'
paths = lane.CANDS['R1_v2_full']['changed_paths'] + lane.CANDS['R2']['changed_paths']
# Preserve the failed diagnostic. The existing runtime imports the runner here.
assert lane.imports('r2-import-corrected', repo, paths, r2=True) == 0
lane.pytest('r2-r1-compiler', repo, paths,
    ['tests/test_iteration_profile.py', 'tests/test_compiler_json_contract.py'], ROOT / '.v26/tr2c')
lane.pytest('r2-author-step', repo, paths,
    ['examples/rsi_workflows/tests/test_runtime.py', '-k', 'not original_orchestrator'], ROOT / '.v26/tr2s')
review = lane.package('R2') / 'review/independent_probes'
lane.pytest('r2-independent', repo, paths, [str(review / 'test_reviewer_boundaries.py')],
    ROOT / '.v26/tr2i', pythonpath=f'{repo}:{review}')
lane.pytest('r2-native', repo, paths,
    ['examples/rsi_workflows/tests/test_runtime.py', '-k', 'original_orchestrator'],
    ROOT / '.v26/tr2n', category='AF_UNIX_native')
lane.command('r2-native-readback', repo, paths,
    [PY, lane.package('R2') / 'h2a_readback.py', ROOT / '.v26/tr2n', OUT / 'r2-native-readback-result.json'],
    category='readonly')
lane.command('r2-step-readback', repo, paths,
    [PY, lane.package('R2') / 'h2a_readback.py', ROOT / '.v26/tr2s', OUT / 'r2-step-readback-result.json'],
    category='readonly')
lane.snap('r2-final', repo, paths)
receipt = json.loads((OUT / 'package-receipt.json').read_text())
assert all(lane.sha(Path(p['raw']) / f['path']) == f['sha256'] for p in receipt for f in p['raw_inventory'])
lane.dump(OUT / 'raw-immutability-final.json', dict(status='PASS',
    checked_files=sum(len(p['raw_inventory']) for p in receipt)))
print('RSI_EXISTING_GATES_FINISHED')
