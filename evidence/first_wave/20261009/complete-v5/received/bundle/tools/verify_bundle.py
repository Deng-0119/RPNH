#!/usr/bin/env python3
"""Read-only stdlib audit. No imports of product, subprocess, network or extraction."""
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    parts = PurePosixPath(name).parts
    if not name or name.startswith('/') or '\\' in name or any(p in ('..', '.') for p in parts) or any(':' in p for p in parts):
        raise ValueError('Unsafe member: ' + name)
    if str(PurePosixPath(name)) != name.rstrip('/'):
        raise ValueError('Noncanonical member: ' + name)


def scan_zip(z):
    names = z.namelist()
    if len(names) != len(set(names)) or len(names) != len(set(n.casefold() for n in names)):
        raise ValueError('Duplicate/case-colliding ZIP names')
    for info in z.infolist():
        safe_name(info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise ValueError('Special file: ' + info.filename)
        if info.flag_bits & 1:
            raise ValueError('Encrypted member: ' + info.filename)
    if z.testzip() is not None:
        raise ValueError('ZIP CRC failure')
    return names


def local_bytes(root, rel):
    safe_name(rel)
    p = root / rel
    for component in (p, *p.parents):
        if component == root.parent:
            break
        if component.is_symlink():
            raise ValueError('Symlink in bundle path: ' + rel)
    return p.read_bytes()


def audit(root):
    failures = []
    top = json.loads(local_bytes(root, 'BUNDLE_MANIFEST.json'))
    expected = {r['path'] for r in top['files']} | {'BUNDLE_MANIFEST.json'}
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != expected:
        raise ValueError('Top-level member set differs: missing=' + repr(sorted(expected-actual)) + ' extra=' + repr(sorted(actual-expected)))
    for row in top['files']:
        data = local_bytes(root, row['path'])
        if len(data) != row['size'] or sha(data) != row['sha256']:
            raise ValueError('Top-level hash/size mismatch: ' + row['path'])
    locks = json.loads(local_bytes(root, 'ARCHIVE_LOCK.json'))['archives']
    candidates = json.loads(local_bytes(root, 'CANDIDATE_INDEX.json'))['candidates']
    originals = json.loads(local_bytes(root, 'evidence/ORIGINAL_MANIFEST_CHECKS.json'))['checks']
    if len(locks) != 13 or len(candidates) != 12:
        raise ValueError('Expected exactly 12 candidate archives and one V5 plan')
    reports = []
    for lock in locks:
        data = local_bytes(root, lock['path'])
        if len(data) != lock['size_bytes'] or sha(data) != lock['sha256']:
            raise ValueError('Frozen archive differs: ' + lock['id'])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = scan_zip(z)
            expected_members = {r['path'] for r in lock['members']}
            if set(names) != expected_members or len(names) != lock['member_count']:
                raise ValueError('Member set differs: ' + lock['id'])
            for row in lock['members']:
                payload = z.read(row['path'])
                if len(payload) != row['size'] or sha(payload) != row['sha256']:
                    raise ValueError('Member digest differs: ' + row['path'])
            checked_rows = 0
            for check in originals:
                if check['id'] != lock['id']:
                    continue
                if sha(z.read(check['manifest_member'])) != check['manifest_sha256']:
                    raise ValueError('Original manifest differs')
                # Rows are frozen translations of original manifests; the manifest itself is byte-locked.
                for row in check['rows']:
                    payload = z.read(row['member'])
                    if sha(payload) != row['sha256'] or (row['size'] is not None and len(payload) != row['size']):
                        raise ValueError('Original manifest row mismatch: ' + row['member'])
                    checked_rows += 1
            candidate = next((c for c in candidates if c['id'] == lock['id']), None)
            if candidate:
                payload = z.read(candidate['selected_final_patch_member'])
                if sha(payload) != candidate['patch_sha256']:
                    raise ValueError('Final root patch mismatch')
                for member in candidate['read_first']:
                    if member not in names:
                        raise ValueError('Missing required entry: ' + member)
            reports.append({'id':lock['id'],'status':'PASS_STATIC_ONLY','members':len(names),'original_manifest_rows_checked':checked_rows})
    # Recompute complete curated source identities from frozen archive members, never from the user's repo.
    aggregates = []
    sources = [
        ('S1', 'H7_core', 'rpnh-parent-child-core-implementation/inputs/S1-source/', 979, '4a7841ac16432aeb173562ec797bc062839a006fdb684d5ea1e3b161c6c49fbc'),
        ('H7_core','H7_core','rpnh-parent-child-core-implementation/source/',992,'ac68327e442b7bda8a6aa2ba93c0cd20ad72181a661b0497627b08713334907e'),
        ('H7_history','H7_history','rpnh-acceptance-history-validator/source/',994,'a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2'),
        ('H7_lowering','H7_lowering','rpnh-bound-child-material-lowering/source/',999,'7867bec81c0830f19cfd6f8a58b0d1598d5efc8220c69e6cfdd02604f98f1a0e')]
    for sid, aid, prefix, count, digest in sources:
        lock = next(a for a in locks if a['id'] == aid)
        with zipfile.ZipFile(io.BytesIO(local_bytes(root, lock['path']))) as z:
            rows = []
            for name in sorted(z.namelist()):
                if name.startswith(prefix) and not name.endswith('/'):
                    body = z.read(name)
                    rows.append({'path':name[len(prefix):], 'size':len(body), 'sha256':sha(body)})
            actual_digest = sha(json.dumps(rows,sort_keys=True,separators=(',',':')).encode())
            if len(rows) != count or actual_digest != digest:
                raise ValueError('Curated source aggregate differs: ' + sid)
            aggregates.append({'id':sid,'files':count,'sha256':digest})
    codex_lock = next(a for a in locks if a['id'] == 'Codex_0161')
    with zipfile.ZipFile(io.BytesIO(local_bytes(root, codex_lock['path']))) as z:
        manifest = json.loads(z.read('RPNH_Codex_0161_Candidate_Local_Gate_20261008/file-manifest.json'))
        fingerprint = sha(json.dumps(manifest['source_files'],sort_keys=True,separators=(',',':')).encode())
        if len(manifest['source_files']) != 1044 or fingerprint != '316a06aad41653e890d16a19519d07b09042c54c4961586b54cf2842b10d8658' or manifest['source_fingerprint'] != fingerprint:
            raise ValueError('Codex 1044-file target lock changed')
    return {'status':'PASS_STATIC_BUNDLE_ONLY','archives':reports,'source_aggregates':aggregates,'codex_target_lock_files':1044,'h7_full_native_status':'NOT_READY_UNIMPLEMENTED','product_tests_run':False,'native_or_model_run':False,'repo_writes':False,'network_or_install':False,'does_not_certify':'local checkout, dependencies, native execution, complete H7 or final combined tree'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument('--bundle-zip',type=Path,help='Also read/CRC/hash-check the final outer ZIP against the extracted tree')
    args = ap.parse_args()
    try:
        root = args.root.resolve()
        result = audit(root)
        if args.bundle_zip:
            with zipfile.ZipFile(args.bundle_zip) as z:
                names = scan_zip(z)
                prefix = root.name + '/'
                expected = {prefix + str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
                if set(names) != expected:
                    raise ValueError('Outer ZIP member set differs')
                for name in names:
                    if z.read(name) != local_bytes(root, name[len(prefix):]):
                        raise ValueError('Outer ZIP bytes differ: '+name)
                result['outer_zip_members'] = len(names)
                result['outer_zip_sha256'] = sha(args.bundle_zip.read_bytes())
                result['outer_zip_bytes'] = args.bundle_zip.stat().st_size
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'FAIL_STATIC_BUNDLE','error':str(exc)},ensure_ascii=False))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
