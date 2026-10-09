"""Single authorized pytest process; reuse original resource/source checks."""
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import time

OUT = Path(__file__).resolve().parent
NATIVE = OUT.parent
sys.path.insert(0, str(NATIVE))
import run_batch as common


def save(name, value):
    p = OUT / name
    assert p.resolve().is_relative_to(OUT) and not p.is_symlink()
    assert not p.exists() or p.stat().st_nlink == 1
    p.write_text(json.dumps(value, indent=2) + '\n')


def main():
    pre = common.source_check()
    budget = common.resources()
    save('source-pre.json', pre)
    assert pre['status'] == 'PASS' and budget['allowed']
    assert (OUT / 'test_native_s1.py').read_bytes() == (NATIVE / 'a004/test_native_s1.py').read_bytes()
    for name in ('test_g7_control.py', 'test_native_s1.py', 'run_control.py'):
        p = OUT / name
        compile(p.read_text(), str(p), 'exec')
    temp = common.safe(common.ROOT / '.v26/unS1-control001')
    assert not temp.exists()
    command = [str(common.PYTHON), '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
        '--confcutdir=' + str(OUT), '--rootdir=' + str(OUT), '--basetemp=' + str(temp),
        '--junitxml=' + str(OUT / 'control.xml'), str(OUT / 'test_g7_control.py') + '::test_g7_no_pending_replacement']
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
        'PYTEST_ADDOPTS': '', 'PYTHONPATH': str(common.SOURCE) + ':' + str(common.SOURCE / 'tests'),
        'TMPDIR': str(temp), 'TMP': str(temp), 'TEMP': str(temp)}
    row = dict(command=command, cwd=str(common.SOURCE), started_utc=common.utc(), resources_before=budget,
        env={k: env[k] for k in ('PYTHONDONTWRITEBYTECODE', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONPATH', 'PYTEST_ADDOPTS', 'TMPDIR')})
    with (OUT / 'control.stdout.log').open('xb') as stdout, (OUT / 'control.stderr.log').open('xb') as stderr:
        process = subprocess.Popen(command, cwd=common.SOURCE, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        row['pid'] = process.pid
        deadline = time.monotonic() + 120
        while process.poll() is None:
            time.sleep(2)
            budget = common.resources()
            with (OUT / 'resources.jsonl').open('a') as f:
                f.write(json.dumps(budget) + '\n')
            if not budget['allowed'] or time.monotonic() > deadline:
                row['interruption'] = 'resource or 120s bound'
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                break
        row['exit_code'] = process.wait()
    post = common.source_check()
    save('source-post.json', post)
    row.update(finished_utc=common.utc(), source_unchanged=pre == post, source_final=post,
        resources_after=common.resources(), process_reaped=True,
        residual_sockets=[str(p) for p in temp.rglob('*') if p.is_socket()],
        runtime_dir=str(temp), junit=str(OUT / 'control.xml'))
    row['status'] = 'PASS' if row['exit_code'] == 0 and row['source_unchanged'] and not row['residual_sockets'] else 'FAIL'
    save('execution.json', row)
    print(json.dumps(row, indent=2))


if __name__ == '__main__':
    main()
