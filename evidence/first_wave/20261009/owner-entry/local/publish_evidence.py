"""Publish task-scoped text evidence and selected synthetic exports."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-owner-entry-validation-20261008'
PACKET = TASK / 'RPNH_Owner_Entry_Local_Validation_20261008'
REPO = ROOT / 'RPNH-main'
DEST = REPO / 'evidence/first_wave/20261009/owner-entry'
EXPORT_NAMES = {'evidence.json', 'report.json', 'projection.json', 'adopted-pn.json'}

def main():
    assert DEST.resolve().is_relative_to(REPO.resolve()) and not DEST.exists()
    files = {}
    def add(path, relative):
        assert path.is_file() and path.resolve().is_relative_to(ROOT)
        assert relative not in files
        files[relative] = path
    for path in PACKET.rglob('*'):
        if path.is_file():
            assert path.suffix in {'.json','.py','.md','.txt','.log','.xml','.patch'} or path.name in {'LICENSE','SHA256SUMS'}
            add(path, 'cloud-handoff/' + path.relative_to(PACKET).as_posix())
    for name in ['package-verification.json','source-verification.json','integration-verification.json',
                 'native-cli-readback-audit.json','test-inventory.json','LAYERED_MANIFEST.json','REPORT_ZH.md',
                 'capture_run.py','audit_cli_readback.py','publish_evidence.py','closeout.py']:
        add(TASK/name, 'local/'+name)
    for directory in ['logs','probes','review']:
        for path in (TASK/directory).rglob('*'):
            if path.is_file():
                assert path.suffix in {'.json','.py','.md','.log','.xml','.txt'}
                add(path, 'local/'+path.relative_to(TASK).as_posix())
    for prefix, directory in [('cli', ROOT/'.o26/c/export'),('readback', ROOT/'.o26/c/readback')]:
        for name in EXPORT_NAMES:
            add(directory/name, 'native/'+prefix+'/'+name)
    base = ROOT/'.o26/f'
    for path in base.rglob('*'):
        if not path.is_file():
            continue
        if ((path.name in EXPORT_NAMES and path.parent.name in {'export','rebuilt-export'})
                or path.name == 'controlled-observations.json'):
            add(path, 'native/focused/'+path.relative_to(base).as_posix())
    patterns = [re.compile(p) for p in [r'-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----',
        r'\bsk-[A-Za-z0-9_-]{20,}',r'\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}',
        r'Bearer [A-Za-z0-9_.-]{20,}']]
    rows = []
    for relative, source in sorted(files.items()):
        original = source.read_bytes()
        assert not original.startswith(b'SQLite format 3\0'), relative
        text = original.decode('utf-8')
        assert not any(p.search(text) for p in patterns), relative
        count = 0 if relative.startswith('cloud-handoff/') else text.count(str(ROOT))
        replacement = '&lt;WORKSPACE&gt;' if source.suffix == '.xml' else '<WORKSPACE>'
        included = original if not count else text.replace(str(ROOT), replacement).encode()
        target = DEST/relative
        assert target.resolve().is_relative_to(DEST.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(included)
        rows.append({'path':relative,'source_path_relative_to_workspace':source.relative_to(ROOT).as_posix(),
            'original_sha256':hashlib.sha256(original).hexdigest(), 'included_sha256':hashlib.sha256(included).hexdigest(),
            'original_bytes':len(original),'included_bytes':len(included),
            'redactions':[] if not count else [{'rule':'private workspace prefix','replacement':replacement,'count':count}]})
    (DEST/'MANIFEST.json').write_text(json.dumps({
        'schema_version':'rpnh/owner-entry-native-publication/v1','status':'PASS_NATIVE_FINITE',
        'tested_base':'674252feb836f631c162979f177d1fe91f22559f',
        'delivery_parent':'ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4',
        'patch_sha256':'71261d059d205b375334450aef281702a1c573562c6e905a08d411b907749a6c',
        'frozen_changed_files':7,'unique_local_test_ids':39,'local_test_executions':39,
        'files':rows,'payload_count':len(rows),'originals_policy':'Cloud packet bytes unchanged; local prefix substitutions listed. All original logs/stores remain local.',
        'xml_policy':'Prefix substitutions in local XML, if present, use XML entities. Original cloud XML is never rewritten.',
        'excluded':['Registry databases and WAL/SHM','private profiles','raw provider transcripts','venvs/caches','ZIP archives'],
        'scope_limits':['Focused finite H1 only, not full repository','Default suite includes pure tests; not every testcase is socket integration',
                        'Classic SIGINT restoration scope detailed in local review; no additional OS handler-identity probe',
                        'Earlier shared-harness A PARTIAL_ENV remains historical, not retested','No H2a or business benchmark work']
    },indent=2)+'\n')
    print(json.dumps({'payloads':len(rows),'bytes':sum(r['included_bytes'] for r in rows),
        'unchanged':sum(not r['redactions'] for r in rows),'prefix_redacted':sum(bool(r['redactions']) for r in rows)}))

if __name__ == '__main__':
    main()
