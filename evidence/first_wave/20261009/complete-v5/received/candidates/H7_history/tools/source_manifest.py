import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
rows=[{'path':p.relative_to(root).as_posix(),'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
      for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts]
encoded=json.dumps(rows,sort_keys=True,separators=(',',':')).encode()
print(json.dumps({'files':rows,'file_count':len(rows),'manifest_sha256':hashlib.sha256(encoded).hexdigest()},indent=2))
