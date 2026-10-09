"""Package only preserved source, input, report and offline evidence files."""
import hashlib, json, zipfile
from pathlib import Path
root = Path(__file__).resolve().parent.parent
archive = root.parent / 'rpnh-bound-child-material-lowering.zip'
excluded = {'PACKAGE_MANIFEST.json', 'ARCHIVE_RESULT.json'}
files = [p for p in sorted(root.rglob('*')) if p.is_file()
         and not {'__pycache__', '.pytest_cache'}.intersection(p.parts)
         and p.name not in excluded]
manifest = [{'path': p.relative_to(root).as_posix(), 'size': p.stat().st_size,
             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
(root / 'PACKAGE_MANIFEST.json').write_text(json.dumps({'files': manifest, 'file_count': len(manifest),
    'manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}, indent=2) + '\n')
files.append(root / 'PACKAGE_MANIFEST.json')
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in sorted(files):
        info = zipfile.ZipInfo(root.name + '/' + p.relative_to(root).as_posix(), (2026, 10, 9, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        z.writestr(info, p.read_bytes())
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert len(z.infolist()) == len(files)
    for folder, manifest_name in (("source", "FINAL_SOURCE_MANIFEST.json"), ("inputs/acceptance-history-source", "FINAL_INPUT_MANIFEST.json")):
        source_manifest = json.loads((root / "evidence" / manifest_name).read_text())
        for row in source_manifest["files"]:
            stored = z.read(root.name + "/" + folder + "/" + row["path"])
            assert len(stored) == row["size"] and hashlib.sha256(stored).hexdigest() == row["sha256"]
result = {'archive': str(archive), 'size': archive.stat().st_size,
          'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'file_count': len(files)}
(root / 'ARCHIVE_RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
