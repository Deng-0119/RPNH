"""Source identity and interface checks; no model calls and no artifact hashing."""
from __future__ import annotations
import ast
import platform
from pathlib import Path
import subprocess
from .constants import RPNH_COMMIT, AUDIT_COMMIT, TASK_RELATIVE

CONTRACTS = {
    'rpnh': {
        'cpn/plugins/api.py': ['PluginDefinition','PluginOperation','PluginContext'],
        'cpn/plugins/catalog.py': ['load_catalog','BoundPlugin','PluginCatalog'],
        'cpn/plugins/runtime.py': ['run_plugin','build_plugin_module'],
        'cpn/plugins/host.py': ['NativePluginExecutor','NativePluginHost'],
        'cpn/plugins/worker.py': ['execute_worker'],
        'cpn/components/agent_loop/tool_catalog.py': ['parse_agent_tool_catalog'],
        'cpn/components/agent_loop/action_execution.py': ['ActionExecutionMixin'],
    },
    'audit': {
        'multi_agent/loader.py': ['load_task_with_tools'],
        'multi_agent/banks/office.py': ['OfficeEnterpriseManagementBank'],
        'multi_agent/frameworks/core/base.py': ['RunContext','RunOutcome','MASFrameworkAdapter'],
        'multi_agent/frameworks/core/action_sink.py': ['ActionSink'],
        'multi_agent/frameworks/core/tool_dispatch.py': ['dispatch_tool'],
        'multi_agent/completion_judge.py': ['evaluate_completion_checkpoints'],
        'multi_agent/checker.py': ['check_trace','compute_metrics'],
    },
}


def _git(root, *args):
    return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,
                          timeout=20,check=True).stdout.strip()


def inspect_sources(rpnh_root: Path, audit_root: Path) -> dict:
    checks=[]
    for kind, root, expected in [('rpnh',rpnh_root,RPNH_COMMIT),('audit',audit_root,AUDIT_COMMIT)]:
        try:
            commit=_git(root,'rev-parse','HEAD')
            dirty=_git(root,'status','--porcelain','--untracked-files=no')
            descendant = False
            if kind == 'rpnh' and commit != expected:
                probe = subprocess.run(
                    ['git','-C',str(root),'merge-base','--is-ancestor',expected,commit],
                    capture_output=True, text=True, timeout=20)
                descendant = probe.returncode == 0
            checks.append({'name':kind+':commit','passed':commit==expected or descendant,
                           'actual':commit,'expected':expected,
                           'exact_reference_match':commit==expected,
                           'reference_is_ancestor':descendant,
                           'note':'A descendant is a new recorded source condition, not proof of runtime equivalence.'})
            checks.append({
                'name':kind+':tracked-source-state',
                'passed':not dirty,
                'clean':not dirty,
                'controlled_isolated_patch':False,
                'details':dirty,
            })
        except (OSError,subprocess.SubprocessError) as exc:
            checks.append({'name':kind+':source-access','passed':False,'error':str(exc)})
        for path,names in CONTRACTS[kind].items():
            try:
                tree=ast.parse((root/path).read_text(encoding='utf-8'))
                found={n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
                missing=sorted(set(names)-found)
                checks.append({'name':kind+':'+path,'passed':not missing,'missing':missing})
            except (OSError,SyntaxError) as exc:
                checks.append({'name':kind+':'+path,'passed':False,'error':str(exc)})
    for path in [TASK_RELATIVE,'multi_agent/tools/office.yaml',
                 *['multi_agent/fixtures/office/'+f for f in (
                     'seed_directory.json','seed_assets.json','seed_knowledge.json',
                     'seed_work_records.json','seed_expenses.json','seed_dashboards.json',
                     'budget_allocations.json','records.json')]]:
        checks.append({'name':'data:'+path,'passed':(audit_root/path).is_file()})
    return {'schema_version':'rpnh-ha/preflight/v3','python':platform.python_version(),
            'platform':platform.platform(),'checks':checks,
            'source_preflight_passed':all(c['passed'] for c in checks),
            'native_runtime_passed':None,'native_driver_integrated':True,
            'readiness_scope':'source_and_data_only',
            'execution_ready':None,'scoring_ready':None,
            'actual_model_calls':0}
