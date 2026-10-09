"""Verify exact source, frozen input and overlay without importing the product."""
from pathlib import Path
import hashlib,json
p=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def manifest(root):
 rows=[{'path':f.relative_to(root).as_posix(),'size':f.stat().st_size,'sha256':sha(f)} for f in sorted(root.rglob('*')) if f.is_file() and '__pycache__' not in f.parts and '.pytest_cache' not in f.parts]
 return {'files':rows,'file_count':len(rows),'manifest_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
identity=json.loads((p/'SOURCE_IDENTITY.json').read_text())
source=manifest(p/'source')
assert source==json.loads((p/identity['source_manifest']).read_text()),'Candidate manifest mismatch'
assert source['manifest_sha256']==identity['source_manifest_sha256']
assert source['file_count']==identity['source_file_count']
base=manifest(p/'inputs/H7-core-source')
assert base==json.loads((p/'inputs/H7-core-source-manifest.json').read_text()),'Frozen core mismatch'
assert base['manifest_sha256']==identity['H7_core_manifest_sha256']
assert sha(p/'acceptance-history.patch')==identity['overlay_patch_sha256']
for item in identity['preserved_inputs']:
 assert sha(p/item['path'])==item['sha256'],item['path']
print(json.dumps({'status':'PASS','candidate_files':source['file_count'],'candidate_aggregate_sha256':source['manifest_sha256'],'frozen_core_files':base['file_count'],'frozen_core_aggregate_sha256':base['manifest_sha256'],'overlay_patch_sha256':identity['overlay_patch_sha256']},indent=2))
