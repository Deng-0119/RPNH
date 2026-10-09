"""Package the frozen offline candidate and small evidence only; never upload."""
from pathlib import Path
import datetime
import hashlib
import json
import shutil
import zipfile

p=Path(__file__).resolve().parents[1]
review=p.parent/'rpnh-parent-child-core-boundary-review'
if review.exists():
    target=p/'independent-review'; target.mkdir(exist_ok=True)
    delivery=json.loads((review/'DELIVERY_MANIFEST.json').read_text())
    for row in delivery['files']:
        data=(review/row['path']).read_bytes()
        assert len(data)==row['size'] and hashlib.sha256(data).hexdigest()==row['sha256']
        destination=target/row['path']; destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data)
    shutil.copyfile(review/'DELIVERY_MANIFEST.json',target/'DELIVERY_MANIFEST.json')
assert (p/'independent-review/FINAL_REVIEW.md').is_file()
assert (p/'independent-review/FINAL_AUDIT.json').is_file()
manifest=json.loads((p/'evidence/final21-source-after.json').read_text())
validation=json.loads((p/'evidence/FINAL_VALIDATION_INVENTORY.json').read_text())
assert validation['unique_testcases']==139
files=[]
for f in p.iterdir():
    if f.is_file() and f.name not in ('PACKAGE_MANIFEST.json','ARCHIVE_RESULT.json'):
        files.append(f)
for subtree in ('inputs','tools','independent-review'):
    files += [f for f in (p/subtree).rglob('*') if f.is_file() and '__pycache__' not in f.parts and '.pytest_cache' not in f.parts]
files += [p/'source'/r['path'] for r in manifest['files']]
files += [f for f in (p/'evidence').iterdir() if f.is_file()]
files += [f for f in (p/'evidence/source-v1').rglob('*') if f.is_file()]
files=sorted(set(files),key=lambda f:f.relative_to(p).as_posix())
sha=lambda b:hashlib.sha256(b).hexdigest()
rows=[{'path':f.relative_to(p).as_posix(),'size':f.stat().st_size,'sha256':sha(f.read_bytes())} for f in files]
package_manifest={'format':'rpnh-h7-offline-core-bundle/v1','source_aggregate_sha256':manifest['manifest_sha256'],
 'source_files':992,'S1_input_files':979,'unique_final_testcases':139,
 'file_count_excluding_this_manifest':len(rows),'files':rows,
 'excluded':['Python environments and installed dependencies','__pycache__/.pytest_cache','generated Registry/SQLite/object-store temporary directories','duplicated reviewer source snapshots'],
 'status':'offline candidate only; full H7 D0/D1/H7b not completed; no upload/push/native run'}
(p/'PACKAGE_MANIFEST.json').write_text(json.dumps(package_manifest,indent=2)+'\n')
files.append(p/'PACKAGE_MANIFEST.json')
archive=p.parent/'rpnh-parent-child-core-candidate-20261008.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for f in sorted(files,key=lambda x:x.relative_to(p).as_posix()):
        info=zipfile.ZipInfo(p.name+'/'+f.relative_to(p).as_posix(),date_time=(1980,1,1,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
        z.writestr(info,f.read_bytes())
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert len(z.infolist())==len(rows)+1
    for row in rows:
        data=z.read(p.name+'/'+row['path'])
        assert len(data)==row['size'] and sha(data)==row['sha256']
record={'path':str(archive),'filename':archive.name,'size_bytes':archive.stat().st_size,'sha256':sha(archive.read_bytes()),
 'zip_entries':len(rows)+1,'verified_file_hashes':len(rows),'zip_crc':'PASS','manifest_hash_verification':'PASS',
 'source_files':992,'source_aggregate_sha256':manifest['manifest_sha256'],
 'H7_patch_sha256':sha((p/'H7-core.patch').read_bytes()),'uploaded':False}
(p/'ARCHIVE_RESULT.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
