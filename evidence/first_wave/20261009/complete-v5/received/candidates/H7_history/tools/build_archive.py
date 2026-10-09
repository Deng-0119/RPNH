"""Package only portable source, tests, documents and evidence, never run databases."""
from pathlib import Path
import hashlib,json,zipfile
p=Path(__file__).resolve().parents[1];archive=p.with_suffix('.zip')
omitted={'PACKAGE_MANIFEST.json','ARCHIVE_RESULT.json'}
files=[f for f in sorted(p.rglob('*')) if f.is_file() and f.relative_to(p).as_posix() not in omitted and not any(x in f.parts for x in ('__pycache__','.pytest_cache','.venv','node_modules'))]
assert not any(f.suffix in ('.sqlite','.sqlite3','.db','.pyc') for f in files),'Runtime/database file in package'
rows=[{'path':f.relative_to(p).as_posix(),'size':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in files]
manifest={'file_count_excluding_manifest':len(rows),'manifest_excludes':['PACKAGE_MANIFEST.json','ARCHIVE_RESULT.json'],'files':rows}
(p/'PACKAGE_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for f in sorted([*files,p/'PACKAGE_MANIFEST.json']):
  name=p.name+'/'+f.relative_to(p).as_posix();info=zipfile.ZipInfo(name,(2026,10,8,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
  z.writestr(info,f.read_bytes(),compresslevel=9)
with zipfile.ZipFile(archive) as z:
 assert z.testzip() is None
 assert len(z.namelist())==len(rows)+1
 for row in rows:
  raw=z.read(p.name+'/'+row['path']);assert len(raw)==row['size'] and hashlib.sha256(raw).hexdigest()==row['sha256']
result={'archive_path':str(archive),'archive_size':archive.stat().st_size,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'archive_entries':len(rows)+1,'crc_check':'PASS','all_entry_hashes':'PASS','source_manifest_sha256':json.loads((p/'SOURCE_IDENTITY.json').read_text())['source_manifest_sha256'],'uploaded':False,'note':'ARCHIVE_RESULT.json is an external packaging record and not inside the archive'}
(p/'ARCHIVE_RESULT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
