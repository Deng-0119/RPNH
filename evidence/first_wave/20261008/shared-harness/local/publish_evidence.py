"""Copy a reviewed positive list of task text evidence; no runtime stores."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-shared-harness-validation-20261008'
PACKET = TASK / 'RPNH_Shared_Harness_Local_Validation_20261008'
REPO = ROOT / 'RPNH-main'
DEST = REPO / 'evidence/first_wave/20261008/shared-harness'

def main():
    assert DEST.resolve().is_relative_to(REPO.resolve()) and not DEST.exists()
    selected = {}
    def add(source, relative):
        assert source.is_file() and source.resolve().is_relative_to(ROOT)
        assert relative not in selected
        selected[relative] = source
    for path in PACKET.rglob('*'):
        if path.is_file():
            assert path.suffix in {'.json','.py','.md','.log','.xml','.patch','.sha256'}
            add(path, 'cloud-handoff/' + path.relative_to(PACKET).as_posix())
    names = ['package-verification.json','source-verification.json','integration-verification.json',
        'offline-test-inventory.json','historical-reproject-prerequisites.json','environment-blocker.json',
        'RETURN_VALIDATION_SNAPSHOT.json','REPORT_ZH.md','local_validation_additions.diff',
        'capture_run.py','build_return.py','publish_evidence.py','native-inventory.json']
    for name in names:
        add(TASK/name, 'local/'+name)
    for directory in ['logs','probes','review']:
        for path in (TASK/directory).rglob('*'):
            if path.is_file():
                assert path.suffix in {'.json','.py','.md','.log','.xml','.patch','.diff','.stdout','.stderr','.sh','.txt'}, path
                add(path, 'local/'+path.relative_to(TASK).as_posix())
    patterns = [re.compile(p) for p in [r'-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----',
        r'\bsk-[A-Za-z0-9_-]{20,}',r'\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}',
        r'Bearer [A-Za-z0-9_.-]{20,}']]
    records = []
    for relative, source in sorted(selected.items()):
        original = source.read_bytes()
        assert not original.startswith(b'SQLite format 3\0'), relative
        text = original.decode('utf-8')
        assert not any(p.search(text) for p in patterns), relative
        # Cloud packet includes its own earlier redactions; preserve received bytes.
        count = 0 if relative.startswith('cloud-handoff/') else text.count(str(ROOT))
        included = original if not count else text.replace(str(ROOT), '<WORKSPACE>').encode()
        target = DEST/relative
        assert target.resolve().is_relative_to(DEST.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(included)
        records.append({'path':relative,'source_path_relative_to_workspace':source.relative_to(ROOT).as_posix(),
            'original_sha256':hashlib.sha256(original).hexdigest(),
            'included_sha256':hashlib.sha256(included).hexdigest(),
            'original_bytes':len(original),'included_bytes':len(included),
            'redactions':[] if not count else [{'rule':'private workspace prefix','replacement':'<WORKSPACE>','count':count}]})
    (DEST/'MANIFEST.json').write_text(json.dumps({
        'schema_version':'rpnh/shared-harness-publication/v1',
        'tested_base':'c545621c30452650202ad0aca2bc023b6956929b',
        'code_acceptance':'ACCEPTED_FINITE','local_overall':'PARTIAL_ENV','A':'BLOCKED_ENV','B':'PASS',
        'source_files':17,'frozen_patch_A_sha256':'61f2f7115306b8c39846d34e77918c31ae4ccf9f62c0a8835a661c7124400137',
        'frozen_patch_B_sha256':'af1e325ad37099ed1430d282337999c8d1080fa00fae31f8ff2d2c9f59a41a76',
        'files':records,'payload_count':len(records),
        'validation_snapshot_timing':'RETURN_VALIDATION_SNAPSHOT.json was captured before owner-authorized GitHub delivery; no_push=true refers to that capture. Final receipt kept separately.',
        'original_hash_fields':'Nested probe/source hashes identify actual original tested bytes; included hashes/redactions here identify public copies.',
        'excluded':['Registry/Bank databases and WAL/SHM','provider profiles','full requests/raw conversations',
                    'venvs/caches','ZIPs','unrelated experiments'],
        'originals':'Supplied packet and complete original local runs/logs remain local; no transformation of originals.'
    },indent=2)+'\n')
    print(json.dumps({'payload_files':len(records),'bytes':sum(r['included_bytes'] for r in records),
        'unchanged':sum(not r['redactions'] for r in records),'prefix_redacted':sum(bool(r['redactions']) for r in records)}))

if __name__ == '__main__':
    main()
