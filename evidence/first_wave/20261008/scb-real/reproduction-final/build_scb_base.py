"""Build the pinned original SCB base recipe in the task-owned daemon."""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time

ROOT = Path('/home/deng123/RPNH').resolve()
TASK = ROOT / 'task-first-wave-examples-20261008'
ERP = ROOT / 'task-erp-first-wave-20261008'
attempt = int(sys.argv[1]) if len(sys.argv) > 1 else 1
assert attempt in (1, 2)
OUT = TASK / f'work/scb-base-build{attempt:02d}'
assert OUT.resolve().is_relative_to(ROOT) and not OUT.exists()
os.umask(0o077)
OUT.mkdir(mode=0o700)
import yaml
from pydantic import TypeAdapter
from slop_code.execution import EnvironmentSpecType
from slop_code.execution.docker_runtime.images import make_base_image
spec = TypeAdapter(EnvironmentSpecType).validate_python(yaml.safe_load(
    (TASK / 'work/upstream-runner/configs/environments/docker-python3.12-uv.yaml').read_text()))
recipe = make_base_image(spec)
if attempt == 2:
    # Same upstream tools/versions. Fail the pipe on a failed download and use
    # the already configured host proxy via Docker's predefined proxy args.
    recipe = recipe.replace('SHELL ["/bin/bash", "-lc"]',
                            'SHELL ["/bin/bash", "-o", "pipefail", "-lc"]')
    recipe = recipe.replace('curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh',
                            'curl -fsSL --retry 3 --retry-all-errors https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh')
with (OUT / 'Dockerfile').open('x') as stream:
    stream.write(recipe)
if attempt == 2:
    import difflib
    with (OUT / 'build-compatibility.diff').open('x') as stream:
        stream.writelines(difflib.unified_diff(make_base_image(spec).splitlines(True),
            recipe.splitlines(True), fromfile='original-pinned-recipe', tofile='download-compatibility-recipe'))
docker = ERP / 'work/tools/docker/docker'
env = dict(os.environ, DOCKER_HOST='unix:///home/deng123/RPNH/.e26/s',
           DOCKER_CONFIG=str(ERP / 'config/docker'), TMPDIR=str(TASK / 'work/tmp'))
env['PATH'] = str(docker.parent) + ':' + str(TASK / 'upstream-venv/bin') + ':' + env.get('PATH', '')
command = [str(docker), 'buildx', 'build', '--network=host', '--allow=network.host',
           '--load', '--progress=plain', '-t', spec.get_base_image(), str(OUT)]
if attempt == 2:
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy'):
        if env.get(key):
            command[3:3] = ['--build-arg', key]
start = time.monotonic()
started = datetime.now(timezone.utc).isoformat()
with (TASK / f'evidence/scb-base-build{attempt:02d}.log').open('x') as log:
    process = subprocess.Popen(command, cwd=OUT, env=env, stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    with (OUT / 'owner.json').open('x') as stream:
        json.dump({'pid': process.pid, 'command': command, 'started_at': started}, stream, indent=2)
    try:
        code = process.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        code = 124
result = {'status': 'BUILT' if code == 0 else 'BUILD_FAILED', 'exit_code': code,
          'command': command, 'started_at': started,
          'elapsed_seconds': time.monotonic() - start,
          'recipe': 'unmodified pinned make_base_image output' if attempt == 1 else 'same-version download compatibility copy; original and patch retained', 'build_network': 'host',
          'models_launched': 0, 'image_name': spec.get_base_image()}
if code == 0:
    inspect = subprocess.run([str(docker), 'image', 'inspect', spec.get_base_image()],
                             env=env, capture_output=True, check=True)
    with (OUT / 'image-inspect.json').open('xb') as stream:
        stream.write(inspect.stdout)
    image = json.loads(inspect.stdout)[0]
    result.update(image_id=image['Id'], architecture=image['Architecture'], size=image['Size'],
                  repo_digests=image.get('RepoDigests', []), labels=image['Config'].get('Labels'))
with (OUT / 'result.json').open('x') as stream:
    json.dump(result, stream, indent=2)
    stream.write('\n')
print(json.dumps(result))
raise SystemExit(code)
