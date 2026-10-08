"""Synthetic executable only; no provider or external Codex call."""
import json
import subprocess
import sys

from rpnh_erp_bench.endpoint import CONTROLS, prepare_codex_endpoint


def test_private_endpoint_injects_only_controls_inside_exec(tmp_path):
    observed = tmp_path / "arguments.json"
    binary = tmp_path / "official-fixture"
    binary.write_text(f"#!{sys.executable} -I\nimport json, sys\n"
        "if sys.argv[1:] == ['--version']: print('codex-cli synthetic'); raise SystemExit(0)\n"
        f"open({str(observed)!r}, 'x').write(json.dumps(sys.argv[1:]))\n")
    binary.chmod(0o700)
    endpoint, condition = prepare_codex_endpoint(binary, tmp_path / "transport")
    original = ['exec', '--model', 'synthetic-model', '--disable', 'shell_tool', '--json', '-']
    result = subprocess.run([endpoint, *original], capture_output=True, timeout=5)
    assert result.returncode == 0
    assert json.loads(observed.read_text()) == ['exec', '-c', CONTROLS[0], '-c', CONTROLS[1], *original[1:]]
    assert condition['global_wrapper'] == 'bypassed'
    assert (endpoint.parent.stat().st_mode & 0o777) == 0o700
    assert (endpoint.stat().st_mode & 0o777) == 0o700
    rejected = subprocess.run([endpoint, 'app-server'], capture_output=True, timeout=5)
    assert rejected.returncode != 0
