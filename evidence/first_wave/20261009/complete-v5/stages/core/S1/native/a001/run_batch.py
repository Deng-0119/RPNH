"""Run only on explicit parent SLOT_GRANTED. No import of product in runner."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path('<WORKSPACE>')
OUT = Path(__file__).resolve().parent
BUNDLE = OUT
SOURCE = ROOT / '.v26/c1'
TASK = ROOT / 'task-complete-v5-20261009'
PYTHON = ROOT / '.p26/v/bin/python'
TEST = OUT / 'test_native_s1.py'
SCENES = [
    ('g1', 'test_g1_shared_transitions'),
    ('g2', 'test_g2_same_transition'),
    ('g3', 'test_g3_stop_retains_unresolved'),
    ('g4', 'test_g4_durable_reentry'),
    *[('g5-' + mode, 'test_g5_coexistence[' + mode + ']')
      for mode in ('union', 'produce', 'data-read', 'control-read')],
    ('g6', 'test_g6_exact_selection'),
    ('g7', 'test_g7_dynamic_drain_adoption'),
]


def utc():
    return datetime.now(timezone.utc).isoformat()


def safe(path):
    resolved = path.resolve()
    assert resolved.is_relative_to(ROOT.resolve()) and not path.is_symlink()
    assert resolved.is_relative_to(BUNDLE) or (resolved.parent == ROOT / '.v26' and resolved.name.startswith('un'))
    if path.exists() and path.is_file():
        assert path.stat().st_nlink == 1
    return path


def write(name, value):
    safe(OUT / name).write_text(json.dumps(value, indent=2) + '\n')
    if name == 'result.json':
        # Latest aggregate is convenient; each attempt's original stays intact.
        safe(BUNDLE / name).write_text(json.dumps(value, indent=2) + '\n')


def size(path):
    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                s = (Path(base) / name).lstat()
                if stat.S_ISREG(s.st_mode):
                    total += s.st_blocks * 512
            except FileNotFoundError:  # Other authorized jobs may compact files.
                pass
    return total


def resources():
    d = os.statvfs('/mnt/d')  # Windows D mount, never ext4 free as budget.
    used = size(ROOT / '.v26') + size(TASK)
    runtime = sum(size(p) for p in (ROOT / '.v26').glob('unS1-*'))
    free = d.f_bavail * d.f_frsize
    memory = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    return dict(utc=utc(), windows_D_free_bytes=free, task_plus_v26_bytes=used,
        native_runtime_bytes=runtime, memory_available_bytes=memory,
        allowed=free >= 25 * 1024**3 and used < 8 * 1024**3 and runtime < 512 * 1024**2 and memory >= 4 * 1024**3)


def source_check():
    baseline = json.loads((BUNDLE.parent / 'source-before.json').read_text())
    mismatches = []
    observed = []
    expected = {row['path'] for row in baseline['files']}
    for row in baseline['files']:
        p = SOURCE / row['path']
        if not p.is_file() or p.is_symlink():
            mismatches.append(row['path'])
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        executable = bool(p.stat().st_mode & 0o111)
        observed.append(dict(path=row['path'], sha256=digest, executable=executable))
        if digest != row['sha256'] or executable != row['executable']:
            mismatches.append(row['path'])
    actual = {str(p.relative_to(SOURCE)) for p in SOURCE.rglob('*')
              if p.is_file() and '.git' not in p.relative_to(SOURCE).parts}
    return dict(status='PASS' if not mismatches and actual == expected else 'FAIL',
        baseline_head=baseline['head'], files=len(expected), mismatches=mismatches,
        observed_inventory_sha256=hashlib.sha256(json.dumps(
            sorted(observed, key=lambda row: row['path']), sort_keys=True,
            separators=(',', ':')).encode()).hexdigest(),
        inventory_hash_format='UTF-8 canonical JSON sorted path records: path, sha256, executable; sort_keys=True, separators=(comma,colon)',
        added=sorted(actual - expected), missing=sorted(expected - actual))


def main():
    global OUT, TEST
    parser = argparse.ArgumentParser()
    parser.add_argument('--slot-granted', action='store_true', required=True)
    parser.parse_args()
    assert SOURCE.resolve() == SOURCE and OUT.is_relative_to(TASK.resolve())
    # Every attempt is independent, including source snapshots and g*.json.
    # Never rerun pytest into an old attempt or edit its frozen probe copy.
    ordinal = 1
    while (BUNDLE / ('a%03d' % ordinal)).exists():
        ordinal += 1
    attempt = 'a%03d' % ordinal
    OUT = safe(BUNDLE / attempt)
    OUT.mkdir()
    for name in ('test_native_s1.py', 'run_batch.py'):
        target = safe(OUT / name)
        with target.open('xb') as stream:
            stream.write((BUNDLE / name).read_bytes())
    TEST = OUT / 'test_native_s1.py'
    write('attempt.json', dict(attempt=attempt, created_utc=utc(),
        evidence_policy='Frozen probe source and attempt-local observations; never overwrite earlier attempts.',
        acceptance_scenes=7, planned_test_cases=10))
    pre = source_check()
    write('source-pre.json', pre)
    if pre['status'] != 'PASS':
        write('result.json', dict(status='NOT_RUN', reason='Exact source mismatch', source=pre))
        return
    result = dict(status='RUNNING', started_utc=utc(), source=pre,
        attempt=attempt, attempt_dir=str(OUT), acceptance_scenes=7, planned_test_cases=10,
        scope='S1 finite validation; H7 future NOT_RUN/out of scope', scenes=[],
        worker_subprocesses_started=0,
        gaps=['Exact admission is direct Registry; native HOST settlement is separate.',
              'Reset rejection is direct compiler evidence.',
              'Recovery closes a live host transport; no OS crash is injected.',
              'Two live claims do not prove parallel HOST threads or worker Popen.',
              'Deterministic HOST Futures run in owner process; no external worker process.'])
    for index, (scene, node) in enumerate(SCENES):
        budget = resources()
        if not budget['allowed']:
            result['scenes'].append(dict(scene=scene, status='NOT_RUN',
                reason='Storage/memory threshold reached; pause for parent', resources=budget))
            continue
        temp = safe(ROOT / '.v26' / ('unS1-' + attempt + '-' + str(index)))
        assert not temp.exists(), 'Retain original DBs; never reuse basetemp'
        xml = safe(OUT / (scene + '.xml'))
        command = [str(PYTHON), '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
            '--confcutdir=' + str(OUT), '--rootdir=' + str(OUT),
            '--basetemp=' + str(temp), '--junitxml=' + str(xml), str(TEST) + '::' + node]
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': '',
            'PYTHONPATH': str(SOURCE) + ':' + str(SOURCE / 'tests'),
            'TMPDIR': str(temp), 'TMP': str(temp), 'TEMP': str(temp)}
        row = dict(scene=scene, node_id=str(TEST) + '::' + node, command=command,
            cwd=str(SOURCE), started_utc=utc(), resources_before=budget,
            env={k: env[k] for k in ('PYTHONDONTWRITEBYTECODE', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
                'PYTEST_ADDOPTS', 'PYTHONPATH', 'TMPDIR', 'TMP', 'TEMP')})
        with safe(OUT / (scene + '.stdout.log')).open('xb') as stdout, safe(OUT / (scene + '.stderr.log')).open('xb') as stderr:
            process = subprocess.Popen(command, cwd=SOURCE, env=env, stdout=stdout,
                stderr=stderr, start_new_session=True)
            row['pid'] = process.pid
            deadline = time.monotonic() + 240
            interrupted = None
            while process.poll() is None:
                time.sleep(2)
                budget_now = resources()
                with safe(OUT / 'resource-monitor.jsonl').open('a') as monitor:
                    monitor.write(json.dumps(dict(scene=scene, **budget_now)) + '\n')
                if not budget_now['allowed'] or time.monotonic() > deadline:
                    interrupted = 'storage/memory pause' if not budget_now['allowed'] else '240s scene bound'
                    # Only our own process group; never parent/other workers.
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    break
            row.update(exit_code=process.wait(), finished_utc=utc(), interruption=interrupted)
        row['status'] = 'PASS' if row['exit_code'] == 0 else 'NOT_RUN' if interrupted else 'FAIL'
        row['junit'] = str(xml) if xml.exists() else None
        if xml.exists():
            cases = ET.parse(xml).findall('.//testcase')
            row['junit_cases'] = [dict(name=c.get('name'), outcome='FAIL' if c.find('failure') is not None
                or c.find('error') is not None else 'NOT_RUN' if c.find('skipped') is not None else 'PASS') for c in cases]
            if any(c['outcome'] == 'NOT_RUN' for c in row['junit_cases']):
                row['status'] = 'NOT_RUN'
        row['resources_after'] = resources()
        row['residual_sockets'] = [str(p) for p in temp.rglob('*') if p.is_socket()] if temp.exists() else []
        row['process_reaped'] = process.returncode is not None
        row['evidence'] = sorted(p.name for p in OUT.glob(scene + '*.json'))
        if row['residual_sockets']:
            row['status'] = 'FAIL'
        write(scene + '-execution.json', row)
        result['scenes'].append(row)
        write('result.json', result)
        print(json.dumps(dict(scene=scene, status=row['status'], exit_code=row['exit_code'])), flush=True)
    post = source_check()
    write('source-post.json', post)
    result.update(finished_utc=utc(), source_unchanged=post, resources_after=resources())
    result['source_final_inventory_sha256'] = post['observed_inventory_sha256']
    result['source_pre_post_hash_equal'] = pre['observed_inventory_sha256'] == post['observed_inventory_sha256']
    result['executed_junit_cases'] = sum(len(row.get('junit_cases', [])) for row in result['scenes'])
    result['case_status_counts'] = {status: sum(row['status'] == status for row in result['scenes'])
                                  for status in ('PASS', 'FAIL', 'NOT_RUN')}
    statuses = {row['status'] for row in result['scenes']}
    result['status'] = ('FAIL' if 'FAIL' in statuses or post['status'] != 'PASS' else
                        'NOT_RUN' if 'NOT_RUN' in statuses else 'PASS')
    write('result.json', result)


if __name__ == '__main__':
    main()
