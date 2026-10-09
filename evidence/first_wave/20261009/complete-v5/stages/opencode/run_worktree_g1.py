"""Execute the frozen G1 node selection and guards on this lane's full worktree."""
from pathlib import Path
import ast
import importlib.util
import json
import os
import sys

STAGE = Path(__file__).resolve().parent
PACKAGE = STAGE / 'package/rpnh-opencode-candidate-gate'
SOURCE = Path('<WORKSPACE>/.v26/o')
assert SOURCE.resolve() == SOURCE and Path.cwd() == SOURCE
spec = importlib.util.spec_from_file_location('frozen_g1_guards', PACKAGE / 'run_g1.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
tree = ast.parse((PACKAGE / 'run_g1.py').read_text())
main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
selection = next(n.value for n in main.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'targets' for t in n.targets))
nodes = json.loads((PACKAGE / 'evidence/g1-regression-nodes.json').read_text())
targets = eval(compile(ast.Expression(selection), '<frozen-target-selection>', 'eval'), {'nodes': nodes})
sys.path[:0] = [str(SOURCE), str(SOURCE / 'tests')]
import cpn.frontend.opencode_protocol as protocol
import cpn.frontend.opencode_launcher as launcher
assert Path(protocol.__file__).resolve().is_relative_to(SOURCE)
assert Path(launcher.__file__).resolve().is_relative_to(SOURCE)
print(json.dumps({'source': str(SOURCE), 'protocol': protocol.__file__, 'launcher': launcher.__file__,
                  'frozen_guard_source': str(PACKAGE / 'run_g1.py'), 'targets': targets}, indent=2), flush=True)
import pytest
raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', *targets,
    '--basetemp=<WORKSPACE>/.v26/tow',
    '--junitxml=' + str(STAGE / '11-g1-worktree.xml')], plugins=[gate.NoEffects()]))
