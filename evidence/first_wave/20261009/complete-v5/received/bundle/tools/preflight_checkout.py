#!/usr/bin/env python3
"""Read-only checkout preflight. Runs only fixed local Git read commands; never fetch/apply/install/product imports."""
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

BASE = '1f191645c4d60c8b190d42e9fad99c85e8981c03'
PRODUCT = 'd92ff3704b6002bf5ecbccb3e6a3d1489809a805'
FIXTURE = 'examples/net_operations/live_agent_replacement.py'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,required=True,help='Original full Deng-0119/RPNH checkout, before candidate application')
    a = ap.parse_args()
    report = {'status':'BLOCKED','baseline_main':BASE,'product_baseline':PRODUCT,'checks':{},'blockers':[], 'warnings':[], 'product_imports_or_tests':False,'network_or_install':False,'repo_writes':False}
    repo = a.repo.resolve()
    git = shutil.which('git')
    if not git or not repo.is_dir():
        report['blockers'].append('Missing existing Git or checkout directory');print(json.dumps(report,ensure_ascii=False,indent=2));return 2
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1')
    def run(*args):
        p = subprocess.run([git,'--no-optional-locks','-c','core.fsmonitor=false','-C',str(repo),*args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=60)
        if p.returncode:
            raise ValueError('Read-only git '+args[0]+' failed (exit '+str(p.returncode)+'); no recovery attempted')
        return p.stdout
    try:
        actual_root = Path(run('rev-parse','--show-toplevel').decode().strip()).resolve()
        if actual_root != repo:report['blockers'].append('--repo must be exact repository root')
        origin = run('config','--get','remote.origin.url').decode().strip()
        # Never print a remote URL; it may contain credentials. Inspect only the exact authorized repository identity.
        normalized = re.sub(r'\.git$','',origin.rstrip('/'))
        ok_origin = bool(re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)Deng-0119/RPNH',normalized))
        report['checks']['origin_matches_Deng_0119_RPNH'] = ok_origin
        if not ok_origin:report['blockers'].append('Origin is absent, nonstandard or not exact Deng-0119/RPNH; inspect privately, do not change it automatically')
        head = run('rev-parse','HEAD').decode().strip();report['checks']['head'] = head
        branch = run('rev-parse','--abbrev-ref','HEAD').decode().strip();report['checks']['branch'] = branch
        if head != BASE:report['blockers'].append('HEAD differs from V5 locked base; inspect later-main/candidate changes and patch preimages, never reset')
        for commit in (BASE,PRODUCT):
            value=run('rev-parse','--verify',commit+'^{commit}').decode().strip()
            if value != commit:report['blockers'].append('Pinned commit missing or resolves differently: '+commit)
        run('merge-base','--is-ancestor',PRODUCT,BASE)
        try: report['checks']['local_origin_main'] = run('rev-parse','--verify','refs/remotes/origin/main').decode().strip()
        except ValueError: report['warnings'].append('No local origin/main tracking ref; no fetch performed')
        config_rows = run('config','--null','--list','--includes').decode(errors='replace').split('\0')
        external_filters = []
        for row in config_rows:
            key, _, value = row.partition('\n')
            if re.fullmatch(r'filter\..+\.(?:clean|smudge|process)',key,re.I) and value.strip():
                external_filters.append(key)
        report['checks']['external_filter_config_present'] = bool(external_filters)
        if external_filters:
            report['blockers'].append('Git external filters configured; status deliberately NOT_RUN to avoid executing user-defined commands')
            report['checks']['worktree_clean'] = None
        else:
            status=run('status','--porcelain=v1','-z','--untracked-files=all','--ignore-submodules=all')
            report['checks']['worktree_clean'] = not bool(status)
            report['checks']['status_record_count'] = len([s for s in status.split(b'\0') if s])
            report['checks']['status_sha256'] = hashlib.sha256(status).hexdigest()
            if status:report['blockers'].append('Dirty/untracked paths exist; inventory them privately and isolate without overwriting user changes')
        # Detect curated subsets/sparse or missing tracked files, without reading private untracked content.
        tracked=run('ls-tree','-r','--name-only','-z',BASE).decode().split('\0')
        tracked=[p for p in tracked if p]
        missing=[p for p in tracked if not (repo/p).exists() and not (repo/p).is_symlink()]
        report['checks']['baseline_tracked_paths'] = len(tracked)
        report['checks']['missing_tracked_paths'] = missing
        if missing:report['blockers'].append('Full baseline checkout files missing; curated source/ is insufficient')
        if FIXTURE not in tracked:
            report['blockers'].append('Required original fixture not tracked at exact V5 base: '+FIXTURE)
        else:
            blob=run('show','--no-ext-diff','--no-textconv',BASE+':'+FIXTURE)
            p=repo/FIXTURE
            report['checks']['required_fixture']={'path':FIXTURE,'commit':BASE,'baseline_sha256':hashlib.sha256(blob).hexdigest(),'exists':p.is_file()}
            if not p.is_file() or p.is_symlink() or p.read_bytes()!=blob:report['blockers'].append('Missing/modified/symlink original net-operations fixture at exact base; do not invent or copy another lane')
        report['checks']['python']={'executable':sys.executable,'version':sys.version.split()[0]}
        deps={}
        for package in ('pytest','jsonschema','packaging','websockets','numpy','scipy'):
            try:deps[package]=importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:deps[package]=None
        report['checks']['existing_distribution_metadata_only']=deps
        report['checks']['existing_tool_paths_only']={tool:shutil.which(tool) for tool in ('node','pnpm','codex','opencode')}
        report['warnings'].append('Tool presence is not version/provenance verification; follow lane gates. Missing tools => NOT_RUN; do not install.')
        report['warnings'].append('Git status deliberately ignores submodules to avoid external filter execution; submodule content/cleanliness is NOT_VERIFIED, if present.')
        report['warnings'].append('This preflight does not validate candidate preimages, a post-patch stage, native environment, Git LFS content, or combined source identity.')
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        report['blockers'].append(str(exc))
    if not report['blockers']:report['status']='PASS_BASE_CHECKOUT_PREFLIGHT_ONLY'
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if not report['blockers'] else 2

if __name__ == '__main__':
    raise SystemExit(main())
