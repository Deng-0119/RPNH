"""Artifact verification only; never runs product code. Python 3 standard library + git."""
import argparse,hashlib,json,pathlib,shutil,subprocess,tempfile,xml.etree.ElementTree as ET
R=pathlib.Path(__file__).resolve().parent
P=argparse.ArgumentParser();P.add_argument('--candidate',type=pathlib.Path);args=P.parse_args()
def sha(b):return hashlib.sha256(b).hexdigest()
def blob(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
expected={}
for line in (R/'SHA256SUMS').read_text().splitlines():
 h,p=line.split('  ',1);q=pathlib.PurePosixPath(p)
 assert not q.is_absolute() and '..' not in q.parts
 assert p not in expected;expected[p]=h
actual={p.relative_to(R).as_posix() for p in R.rglob('*') if p.is_file() and p.name!='SHA256SUMS'}
assert actual==set(expected),('bundle file list differs',actual^set(expected))
for p,h in expected.items():assert sha((R/p).read_bytes())==h,p
for p in R.rglob('*.json'):json.loads(p.read_text())
for p in R.rglob('*.xml'):ET.parse(p)
m=json.loads((R/'file-manifest.json').read_text());rows=m['files'];assert len(rows)==12
assert sha((R/m['patch']).read_bytes())==m['patch_sha256']
assert {p.relative_to(R/'source').as_posix() for p in (R/'source').rglob('*') if p.is_file()}=={r['path'] for r in rows}
for r in rows:
 b=(R/'source'/r['path']).read_bytes();assert sha(b)==r['sha256'] and blob(b)==r['final_git_blob_sha1']
 if r['status']=='modified':assert blob((R/'baseline'/r['path']).read_bytes())==r['baseline_git_blob_sha1']
with tempfile.TemporaryDirectory(prefix='rpnh-codec-package-check-') as t:
 q=pathlib.Path(t)
 shutil.copytree(R/'baseline',q,dirs_exist_ok=True)
 subprocess.run(['git','apply','--check',str(R/m['patch'])],cwd=q,check=True)
 subprocess.run(['git','apply',str(R/m['patch'])],cwd=q,check=True)
 for r in rows:assert (q/r['path']).read_bytes()==(R/'source'/r['path']).read_bytes(),r['path']
if args.candidate:
 for r in rows:assert sha((args.candidate.resolve()/r['path']).read_bytes())==r['sha256'],r['path']
print(json.dumps({'status':'PASS','checks':['file_allowlist','all_sha256','json_xml_parse','12_final_source_blobs','5_old_blobs','fresh_git_apply_check','fresh_git_apply_exact_bytes']+(['candidate_12_final_hashes'] if args.candidate else []),'product_tests_run':False}))
