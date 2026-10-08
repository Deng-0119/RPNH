"""Publish received handoff bytes and task-scoped reviewed text evidence."""
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-h2a-validation-20261008'
PACKET = TASK / 'rpnh-taskcontrol-reader-convergence'
REPO = ROOT / 'RPNH-main'
DEST = REPO / 'evidence/first_wave/20261009/taskcontrol-reader'
EXPORT_NAMES = ('evidence.json', 'report.json', 'projection.json', 'adopted-pn.json')


def transform(data, suffix):
    rules = {str(ROOT).encode(): ('private workspace prefix', '<WORKSPACE>'),
             b'<INPUT_DIRECTORY>': ('private input directory', '<INPUT_DIRECTORY>')}
    pattern = re.compile(b'|'.join(re.escape(p) for p in rules))
    parts, edits, cursor, included_size = [], [], 0, 0
    for match in pattern.finditer(data):
        at = match.start()
        rule, placeholder = rules[match.group()]
        replacement = (placeholder.replace('<', '&lt;').replace('>', '&gt;')
                       if suffix == '.xml' else placeholder).encode()
        chunk = data[cursor:at]
        parts.extend((chunk, replacement))
        included_size += len(chunk)
        edits.append({'rule': rule,
                      'original_byte_range': [at, match.end()],
                      'included_byte_range': [included_size, included_size + len(replacement)],
                      'original_line_range': [data[:at].count(b'\n') + 1] * 2,
                      'replacement': replacement.decode()})
        included_size += len(replacement)
        cursor = match.end()
    parts.append(data[cursor:])
    return b''.join(parts), edits


def main():
    assert DEST.resolve().is_relative_to(REPO.resolve()) and not DEST.exists()
    assert json.loads((TASK / 'LAYERED_MANIFEST.json').read_text())['status'] == 'PASS_NATIVE_FINITE'
    files = {}

    def add(source, relative):
        assert source.is_file() and not source.is_symlink()
        assert source.resolve().is_relative_to(ROOT) and relative not in files
        files[relative] = source

    for source in PACKET.rglob('*'):
        if source.is_file():
            assert source.suffix in {'.json', '.py', '.md', '.txt', '.log', '.xml', '.patch', '.sha256'} or source.name in {'SHA256SUMS', 'ORIGINAL_SHA256SUMS'}
            add(source, 'cloud-handoff/' + source.relative_to(PACKET).as_posix())
    for name in ('receipt.json', 'package-final.json', 'source-verification.json',
                 'source-before.json', 'source-after.json', 'integration-verification.json',
                 'native-cli-readback-audit.json', 'test-inventory.json', 'LAYERED_MANIFEST.json',
                 'REPORT_ZH.md', 'capture_run.py', 'audit_cli_readback.py',
                 'closeout.py', 'render_report.py', 'publish_evidence.py', 'validate_publication.py'):
        add(TASK / name, 'local/' + name)
    for directory in ('logs', 'probes', 'reviews'):
        for source in (TASK / directory).rglob('*'):
            if source.is_file():
                assert source.suffix in {'.json', '.py', '.md', '.log', '.xml', '.txt'}
                add(source, 'local/' + source.relative_to(TASK).as_posix())
    for label, directory in (('cli', ROOT / '.t26/c/export'), ('readback', ROOT / '.t26/c/readback')):
        for name in EXPORT_NAMES:
            add(directory / name, 'native/' + label + '/' + name)
    for source in (ROOT / '.t26/f').rglob('controlled-observations.json'):
        add(source, 'native/focused/' + source.relative_to(ROOT / '.t26/f').as_posix())
    patterns = [re.compile(p) for p in (r'-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----',
                r'\bsk-[A-Za-z0-9_-]{20,}', r'\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}',
                r'Bearer [A-Za-z0-9_.-]{20,}')]
    rows = []
    for relative, source in sorted(files.items()):
        original = source.read_bytes()
        assert not original.startswith(b'SQLite format 3\0'), relative
        text = original.decode('utf-8')
        assert not any(p.search(text) for p in patterns), relative
        included, edits = (original, []) if relative.startswith('cloud-handoff/') else transform(original, source.suffix)
        if source.suffix == '.xml':
            ET.fromstring(included)
        target = DEST / relative
        assert target.resolve().is_relative_to(DEST.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(included)
        rows.append({'path': relative, 'source_path_relative_to_workspace': source.relative_to(ROOT).as_posix(),
                     'original_sha256': hashlib.sha256(original).hexdigest(),
                     'included_sha256': hashlib.sha256(included).hexdigest(),
                     'original_bytes': len(original), 'included_bytes': len(included), 'edits': edits})
    manifest = {'schema_version': 'rpnh/h2a_native_publication/v1', 'status': 'PASS_NATIVE_FINITE',
                'tested_head': '715468dab0b1bea07d7e94a7aa0606eaf194365c',
                'unique_tests': 166, 'test_executions': 166, 'frozen_h2a_files': 5,
                'combined_h1_files_verified': 7, 'files': rows, 'payload_count': len(rows),
                'offset_convention': 'Zero-based half-open byte ranges, one-based inclusive lines',
                'received_cloud_policy': 'All 64 received ZIP member bytes unchanged. Earlier cloud/distribution sanitization is recorded in cloud-handoff/packaging/DISTRIBUTION_PROVENANCE.json; this does not claim identity to pre-distribution originals.',
                'local_policy': 'Original bytes remain local. Only private workspace/input-directory prefix substitutions listed in edits. XML replacements are escaped and reparsed.',
                'excluded': ['Registry databases and WAL/SHM', 'private profiles', 'raw provider transcripts', 'venvs/caches', 'ZIP archives'],
                'scope': 'H2a plus required combined H1 finite gates; previous A1 block not retested; no real provider, Actions, Docker or business benchmark'}
    (DEST / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'payloads': len(rows), 'bytes': sum(r['included_bytes'] for r in rows),
                      'received_cloud_files': sum(r['path'].startswith('cloud-handoff/') for r in rows),
                      'redacted_files': sum(bool(r['edits']) for r in rows),
                      'substitutions': sum(len(r['edits']) for r in rows)}))


if __name__ == '__main__':
    main()
