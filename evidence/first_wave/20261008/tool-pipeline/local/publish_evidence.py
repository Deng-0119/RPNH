"""Publish only task-linked text exports; leave stores and environments local."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-tool-pipeline-validation-20261008'
REPO = ROOT / 'RPNH-main'
DEST = REPO / 'evidence/first_wave/20261008/tool-pipeline'
PACKET = TASK / 'RPNH_Tool_Pipeline_Local_Validation_20261008'
EXPORT_NAMES = {'evidence.json', 'report.json', 'projection.json', 'adopted-pn.json'}

def main():
    assert DEST.resolve().is_relative_to(REPO.resolve()) and not DEST.exists()
    sources = {}
    def add(path, relative):
        assert path.is_file() and path.resolve().is_relative_to(ROOT)
        assert relative not in sources
        sources[relative] = path
    # The supplied packet contains only reviewed source, text logs and JSON exports.
    for path in PACKET.rglob('*'):
        if path.is_file():
            assert path.suffix in {'.json', '.py', '.md', '.txt', '.xml', '.log', '.patch'}
            add(path, 'cloud-handoff/' + path.relative_to(PACKET).as_posix())
    for name in ('package-verification.json', 'tested-source.json', 'readback-protocol.json',
                 'native-evidence-audit.json', 'supplement-evidence-audit.json', 'native-test-inventory.json',
                 'LAYERED_MANIFEST.json', 'REPORT_ZH.md',
                 'capture_run.py', 'audit_native_evidence.py', 'publish_evidence.py'):
        add(TASK / name, 'local/' + name)
    for directory in ('logs', 'probes', 'review'):
        for path in (TASK / directory).rglob('*'):
            if path.is_file():
                assert path.suffix in {'.json', '.py', '.md', '.txt', '.xml', '.log'}
                add(path, 'local/' + path.relative_to(TASK).as_posix())
    for prefix, directory in [('standard', ROOT / '.p26/b/preserved-export'),
                              ('readback', ROOT / '.p26/b/readback-export'),
                              ('rounding', ROOT / '.p26/c/export')]:
        for name in EXPORT_NAMES:
            add(directory / name, 'native/' + prefix + '/' + name)
    base = ROOT / '.p26/a'
    for path in base.rglob('*'):
        if not path.is_file():
            continue
        if ((path.name in EXPORT_NAMES and path.parent.name in {'export', 'rebuilt-export'})
                or path.name == 'controlled-observations.json'):
            add(path, 'native/default-tests/' + path.relative_to(base).as_posix())
    diagnostic_names = {'input.json', 'mutation.json', 'rebuild.json', 'capacity.json',
                        'observations.json', 'expected.traceback.txt', 'cleanup.traceback.txt',
                        'blocked.traceback.txt'}
    for label in ('x', 'y'):
        base = ROOT / '.p26' / label
        assert base.is_dir(), base
        for path in base.rglob('*'):
            if not path.is_file():
                continue
            if ((path.name in EXPORT_NAMES and path.parent.name in
                 {'e', 'before-rebuild', 'held-e', 'blocked-e', 'failure-e'})
                    or path.name in diagnostic_names):
                add(path, 'native/supplement-' + label + '/' + path.relative_to(base).as_posix())
    patterns = {name: re.compile(pattern) for name, pattern in {
        'private_key': r'-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----',
        'api_key': r'\bsk-[A-Za-z0-9_-]{20,}',
        'github_token': r'\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}',
        'bearer_value': r'Bearer [A-Za-z0-9_.-]{20,}',
    }.items()}
    records = []
    for relative, source in sorted(sources.items()):
        original = source.read_bytes()
        decoded = original.decode('utf-8')
        for label, pattern in patterns.items():
            assert not pattern.search(decoded), (relative, label)
        # Preserve cloud packet bytes (it has its own transformation manifest).
        count = 0 if relative.startswith('cloud-handoff/') else decoded.count(str(ROOT))
        exported = original if not count else decoded.replace(str(ROOT), '<WORKSPACE>').encode('utf-8')
        target = DEST / relative
        assert target.resolve().is_relative_to(DEST.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(exported)
        records.append({'path': relative, 'source_path_relative_to_workspace': source.relative_to(ROOT).as_posix(),
                        'original_sha256': hashlib.sha256(original).hexdigest(),
                        'exported_sha256': hashlib.sha256(exported).hexdigest(),
                        'original_bytes': len(original), 'exported_bytes': len(exported),
                        'transformations': [] if not count else
                            [{'kind': 'private_workspace_prefix', 'replacement': '<WORKSPACE>', 'count': count}]})
    (DEST / 'MANIFEST.json').write_text(json.dumps({
        'schema_version': 'tool_pipeline/local_publication/v1',
        'tested_head': '00f2d29c7deffed44e2ec635a24f390c6e0d9ace',
        'source_set_sha256': 'a547de500345f51c102fb197dfabedafc2aa48c251dc19d9e10733cbe2553618',
        'files': records, 'files_count': len(records),
        'credential_pattern_scan': 'PASS; reviewed offline synthetic data, no credential-bearing sources selected',
        'excluded': ['Registry stores/databases/WAL/SHM', 'venvs/caches', 'ZIP archives',
                     'provider profiles', 'unrelated experiments'],
        'originals_policy': 'Complete supplied packet and local logs/stores remain local. '
                            'Cloud bytes unchanged; local prefix substitutions listed per file. '
                            'Expected negative outcomes and historical blocked/partial evidence are retained.'
    }, indent=2) + '\n')
    print(json.dumps({'files': len(records), 'bytes': sum(r['exported_bytes'] for r in records),
                      'unchanged': sum(not r['transformations'] for r in records),
                      'prefix_redacted': sum(bool(r['transformations']) for r in records)}))

if __name__ == '__main__':
    main()
