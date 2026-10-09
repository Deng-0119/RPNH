"""Non-mutating verification: python verify_patch.py /path/to/RPNH."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parent
checkout=Path(sys.argv[1]).resolve()
manifest=json.loads((root/'file-manifest.json').read_text())
patch=root/manifest['patch']
assert hashlib.sha256(patch.read_bytes()).hexdigest()==manifest['patch_sha256']
for row in manifest['files']:
    p=checkout/row['path']
    if row['status']=='added':
        assert not p.exists(), f'new file already exists: {p}'
    else:
        b=p.read_bytes()
        actual=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
        assert actual==row['baseline_git_blob_sha1'], f'baseline differs: {p}'
subprocess.run(['git','apply','--check',str(patch)],cwd=checkout,check=True)
print('Verified baseline blobs, patch SHA256, and git apply --check; no files changed.')
