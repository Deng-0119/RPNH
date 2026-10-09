"""RSI lane only: frozen-patch replay and existing local gates, no source edits."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path('<WORKSPACE>')
TASK = ROOT / 'task-complete-v5-20261009'
OUT = TASK / 'stages/rsi'
PY = ROOT / '.p26/v/bin/python'
REC = TASK / 'record.py'
BASE = '1f191645c4d60c8b190d42e9fad99c85e8981c03'
INDEX = json.loads((TASK / 'rpnh-complete-local-bundle-v5/CANDIDATE_INDEX.json').read_text())
CANDS = {c['id']: c for c in INDEX['candidates'] if c['lane'] == 'rsi'}
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
           TMPDIR=str(ROOT / '.v26/tmp'), PYTHONUNBUFFERED='1')
GATES = []
STAGES = {}


def utc():
    return datetime.now(timezone.utc).isoformat()


def owned(path):
    path = Path(path)
    valid = [OUT, ROOT / '.v26/r1', ROOT / '.v26/r2']
    assert any(path.resolve().is_relative_to(p) for p in valid) or (
        path.resolve().parent == ROOT / '.v26' and path.name.startswith('tr'))
    if path.is_file():
        assert path.stat().st_nlink == 1, path
    return path


def dump(path, value):
    p = owned(path)
    assert not p.exists(), p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git_facts(repo):
    results = []
    for args in [('rev-parse', 'HEAD'), ('branch', '--show-current'),
                 ('rev-parse', 'origin/main'), ('status', '--porcelain=v1', '--untracked-files=all'),
                 ('diff', '--binary', 'HEAD')]:
        cmd = ['git', '-C', str(repo), *args]
        start = utc()
        p = subprocess.run(cmd, env=ENV, capture_output=True)
        results.append(dict(command=cmd, started_at_utc=start, finished_at_utc=utc(),
            exit_code=p.returncode, stdout=p.stdout.decode(), stderr=p.stderr.decode()))
        assert p.returncode == 0, results[-1]
    return dict(repo=str(repo), head=results[0]['stdout'].strip(),
                status=results[3]['stdout'], raw_commands=results)


def inventory(repo, paths):
    rows = []
    for rel in sorted(set(paths)):
        p = repo / rel
        assert p.resolve().is_relative_to(repo) and not p.is_symlink(), p
        rows.append(dict(path=rel, exists=p.exists(), sha256=sha(p) if p.is_file() else None,
            bytes=p.stat().st_size if p.is_file() else None,
            mode=oct(p.stat().st_mode & 0o777) if p.exists() else None))
    return rows


def snap(label, repo, paths):
    v = dict(utc=utc(), git=git_facts(repo), changed_files=inventory(repo, paths))
    dump(OUT / f'{label}.identity.json', v)
    return v


def command(label, repo, paths, args, pythonpath=None, category='static', junit=None):
    before = snap(label + '-before', repo, paths)
    argv = [str(PY), str(REC), '--out', str(OUT / label), '--cwd', str(repo),
            '--pythonpath', pythonpath or str(repo), '--', *map(str, args)]
    result = subprocess.run(argv, env=ENV)
    after = snap(label + '-after', repo, paths)
    metadata = json.loads((OUT / f'{label}.json').read_text())
    assert result.returncode == metadata['exit_code']
    assert before['git']['head'] == after['git']['head']
    gate = dict(label=label, category=category, status='PASS' if result.returncode == 0 else 'FAIL',
        exit_code=result.returncode, record=str(OUT / f'{label}.json'),
        before_identity=str(OUT / f'{label}-before.identity.json'),
        after_identity=str(OUT / f'{label}-after.identity.json'),
        source_unchanged=None,
        junit=str(junit) if junit else None)
    # git_facts retains timestamps; compare actual state independently.
    gate['source_unchanged'] = (before['changed_files'] == after['changed_files'] and
        [r['stdout'] for r in before['git']['raw_commands']] ==
        [r['stdout'] for r in after['git']['raw_commands']])
    GATES.append(gate)
    dump(OUT / f'{label}.gate.json', gate)
    return result.returncode


def package(cid):
    return OUT / 'packages' / cid


def prepare_packages():
    rows = []
    for cid, c in CANDS.items():
        raw = TASK / 'raw' / Path(c['archive_path']).stem / c['root_member']
        dest = owned(package(cid))
        assert raw.is_dir() and not dest.exists()
        members = []
        for p in sorted(raw.rglob('*')):
            assert not p.is_symlink(), p
            if p.is_file():
                members.append(dict(path=str(p.relative_to(raw)), sha256=sha(p), bytes=p.stat().st_size))
        archive = TASK / 'rpnh-complete-local-bundle-v5' / c['archive_path']
        assert sha(archive) == c['archive_sha256']
        patch = raw / Path(c['selected_final_patch_member']).relative_to(c['root_member'])
        assert sha(patch) == c['patch_sha256']
        shutil.copytree(raw, dest)
        assert all(sha(dest / m['path']) == m['sha256'] for m in members)
        rows.append(dict(id=cid, raw=str(raw), stage_copy=str(dest), archive=str(archive),
            archive_sha256=sha(archive), patch_sha256=sha(patch), raw_inventory=members))
    dump(OUT / 'package-receipt.json', rows)


def apply(label, cid, repo, allpaths):
    c = CANDS[cid]
    pkg = package(cid)
    patch = pkg / Path(c['selected_final_patch_member']).relative_to(c['root_member'])
    manifest = json.loads((pkg / 'file-manifest.json').read_text())
    assert set(c['changed_paths']) == {f['path'] for f in manifest['files']}
    rows = []
    for f in manifest['files']:
        p = repo / f['path']
        owned(p)
        assert not p.exists(), f'BLOCKED preimage exists: {p}'
        assert sha(pkg / 'source' / f['path']) == f['sha256']
        rows.append(dict(path=f['path'], old_sha256=None, old_exists=False,
                         manifest_new_sha256=f['sha256'], superseded_sha256_not_preimage=f.get('superseded_sha256')))
    assert sha(patch) == c['patch_sha256']
    dump(OUT / f'{label}-manifest-before.json', dict(patch_sha256=sha(patch), files=rows))
    for suffix, flags in [('check', ['--check', '--whitespace=error']), ('apply', ['--whitespace=error'])]:
        code = command(f'{label}-{suffix}', repo, allpaths, ['git', 'apply', *flags, patch], category='patch')
        assert code == 0, f'BLOCKED {label}-{suffix}'
    rows = inventory(repo, c['changed_paths'])
    assert all(r['sha256'] == next(f['sha256'] for f in manifest['files'] if f['path'] == r['path']) for r in rows)
    dump(OUT / f'{label}-manifest-after.json', dict(patch_sha256=sha(patch), files=rows, status='PASS'))


def imports(label, repo, paths, r2=False):
    modules = ['cpn', 'cpn.rpnh.iteration_profile', 'cpn.rpnh.registry.run_authority', 'cpn.rpnh.task_control']
    if r2:
        modules += ['examples.rsi_workflows.deterministic', 'cpn.rpnh.control_server', 'cpn.orchestrator.runner']
    code = ('import importlib,importlib.metadata,json,sys; from pathlib import Path; '
        f'mods={modules!r}; rows={{m:importlib.import_module(m).__file__ for m in mods}}; '
        f'assert all(Path(v).resolve().is_relative_to(Path({str(repo)!r})) for v in rows.values()); '
        'print(json.dumps({"python":sys.executable,"version":sys.version,"imports":rows,'
        '"distributions":{n:importlib.metadata.version(n) for n in ["pytest","jsonschema","packaging","websockets"]}},indent=2))')
    return command(label, repo, paths, [PY, '-c', code], category='import')


def pytest(label, repo, paths, selectors, base, category='offline', pythonpath=None, extra=()):
    owned(base)
    assert not base.exists()
    xml = OUT / f'{label}.xml'
    return command(label, repo, paths, [PY, '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
        *selectors, '-o', 'junit_family=xunit1', f'--basetemp={base}', f'--junitxml={xml}', *extra],
        category=category, pythonpath=pythonpath, junit=xml)


def main():
    prepare_packages()
    r1paths = CANDS['R1_v2_full']['changed_paths']
    r2paths = r1paths + CANDS['R2']['changed_paths']
    for lane in ['r1', 'r2']:
        repo = ROOT / '.v26' / lane
        before = snap(lane + '-initial', repo, r2paths)
        assert before['git']['head'] == BASE and not before['git']['status'], 'BLOCKED nonclean/nonbase'
    repo = ROOT / '.v26/r1'
    apply('r1-full', 'R1_v2_full', repo, r1paths)
    assert imports('r1-import', repo, r1paths) == 0
    pytest('r1-author', repo, r1paths, ['tests/test_iteration_profile.py', 'tests/test_compiler_json_contract.py'], ROOT / '.v26/tr1a')
    command('r1-compile', repo, r1paths, [PY, '-c',
        'import py_compile; from pathlib import Path; '
        f'out=Path({str(OUT)!r})/"compiled"; out.mkdir(); '
        '[py_compile.compile(p,cfile=str(out/(Path(p).name+"c")),doraise=True) for p in '
        '["cpn/rpnh/iteration_profile.py","tests/test_iteration_profile.py"]]'], category='syntax')
    review = package('R1_v2_full') / 'review'
    pytest('r1-independent-compiler-collection', repo, r1paths,
        [str(review / 'test_terminal_revision_independent.py')], ROOT / '.v26/tr1c',
        category='collection', pythonpath=f'{repo}:{review}', extra=['--collect-only'])
    pytest('r1-independent-registry', repo, r1paths,
        [str(review / 'test_terminal_registry_independent.py')], ROOT / '.v26/tr1i',
        pythonpath=f'{repo}:{repo / "cpn"}:{review}')
    STAGES['R1_v2_full'] = snap('r1-final', repo, r1paths)
    repo = ROOT / '.v26/r2'
    apply('r2-prerequisite-r1-full', 'R1_v2_full', repo, r2paths)
    STAGES['R1_v2_full_on_r2_before_R2'] = snap('r2-r1-stage', repo, r1paths)
    dependency = json.loads((package('R2') / 'r1-successor-overlay.json').read_text())
    assert all(sha(repo / f['path']) == f['sha256'] for f in dependency['files'])
    dump(OUT / 'r2-r1-dependency.json', dict(status='PASS', inventory=inventory(repo, r1paths)))
    apply('r2-delta', 'R2', repo, r2paths)
    assert imports('r2-import', repo, r2paths, r2=True) == 0
    pytest('r2-r1-compiler', repo, r2paths, ['tests/test_iteration_profile.py', 'tests/test_compiler_json_contract.py'], ROOT / '.v26/tr2c')
    pytest('r2-author-step', repo, r2paths, ['examples/rsi_workflows/tests/test_runtime.py', '-k', 'not original_orchestrator'], ROOT / '.v26/tr2s')
    review = package('R2') / 'review/independent_probes'
    pytest('r2-independent', repo, r2paths, [str(review / 'test_reviewer_boundaries.py')], ROOT / '.v26/tr2i', pythonpath=f'{repo}:{review}')
    nativebase = ROOT / '.v26/tr2n'
    pytest('r2-native', repo, r2paths, ['examples/rsi_workflows/tests/test_runtime.py', '-k', 'original_orchestrator'], nativebase, category='AF_UNIX_native')
    if nativebase.exists():
        command('r2-native-readback', repo, r2paths,
            [PY, package('R2') / 'h2a_readback.py', nativebase, OUT / 'r2-native-readback-result.json'], category='readonly')
    command('r2-step-readback', repo, r2paths,
        [PY, package('R2') / 'h2a_readback.py', ROOT / '.v26/tr2s', OUT / 'r2-step-readback-result.json'], category='readonly')
    STAGES['R2'] = snap('r2-final', repo, r2paths)
    receipt = json.loads((OUT / 'package-receipt.json').read_text())
    assert all(sha(Path(p['raw']) / f['path']) == f['sha256'] for p in receipt for f in p['raw_inventory'])
    dump(OUT / 'raw-immutability-final.json', dict(status='PASS', checked_files=sum(len(p['raw_inventory']) for p in receipt)))
    dump(OUT / 'stages.json', STAGES)
    dump(OUT / 'gates.json', GATES)
    print('RSI_EXISTING_GATES_FINISHED', flush=True)


if __name__ == '__main__':
    main()
