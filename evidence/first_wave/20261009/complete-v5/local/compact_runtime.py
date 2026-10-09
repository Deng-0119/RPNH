"""Archive and byte-verify only this task's stopped runtime directories."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import stat
import tarfile
import sys

ROOT = Path('<WORKSPACE>').resolve()
RUNTIME = ROOT / '.v26'
TASK = ROOT / 'task-complete-v5-20261009'
NAMES = ['tca', 'tci', 'tdl', 'tdn', 'tha', 'tla', 'tli', 'tmp',
         'tof', 'tog', 'toi', 'tow', 'tr1a', 'tr1i', 'tsa', 'tsi', 'tsr',
         'txeffort-offline', 'txeffort-review', 'txeffort-socket', 'txhistory-offline', 'txreader']


def main():
    destination = TASK / 'preserved-runtime-v2'
    assert destination.resolve().is_relative_to(ROOT)
    destination.mkdir(exist_ok=True)
    for name in NAMES:
        source = RUNTIME / name
        if not source.exists():
            continue
        assert source.resolve() == source and source.is_dir() and not source.is_symlink()
        assert source.resolve().is_relative_to(RUNTIME.resolve())
        archive = destination / (name + '.tar.gz')
        record = destination / (name + '.json')
        assert not archive.exists() and not record.exists()
        files, links, special = {}, {}, []
        for p in source.rglob('*'):
            mode = p.lstat().st_mode
            if stat.S_ISREG(mode):
                assert p.resolve().is_relative_to(source) and p.stat().st_nlink == 1
                files[p.relative_to(RUNTIME).as_posix()] = p
            elif stat.S_ISLNK(mode):
                links[p.relative_to(RUNTIME).as_posix()] = str(p.readlink())
            elif not stat.S_ISDIR(mode):
                special.append(p.relative_to(RUNTIME).as_posix())
        # Retain special entries in place; no active socket/pipe is discarded.
        if special:
            record.write_text(json.dumps({'status': 'NOT_COMPACTED_SPECIAL_FILES', 'entries': special}, indent=2) + '\n')
            print(json.dumps({'name': name, 'status': 'KEPT', 'special': len(special)}), flush=True)
            continue
        with tarfile.open(archive, 'w:gz', compresslevel=6) as out:
            out.add(source, arcname=name)
        seen = set()
        with tarfile.open(archive, 'r:gz') as saved:
            for member in saved:
                if member.issym():
                    assert member.name in links and member.linkname == links[member.name]
                    seen.add(member.name)
                    continue
                if not member.isfile():
                    assert member.isdir()
                    continue
                assert member.name in files
                original = files[member.name]
                assert member.size == original.stat().st_size
                with original.open('rb') as live:
                    retained = saved.extractfile(member)
                    while True:
                        a, b = live.read(1024 * 1024), retained.read(1024 * 1024)
                        assert a == b, member.name
                        if not a:
                            break
                seen.add(member.name)
        assert seen == set(files) | set(links)
        original_bytes = sum(p.stat().st_size for p in files.values())
        digest = hashlib.sha256()
        with archive.open('rb') as saved:
            for chunk in iter(lambda: saved.read(1024 * 1024), b''):
                digest.update(chunk)
        result = {'status': 'BYTE_VERIFIED_ARCHIVED', 'source_relative_to_workspace': source.relative_to(ROOT).as_posix(),
                  'archived_at_utc': datetime.now(timezone.utc).isoformat(), 'regular_files': len(files),
                  'symlink_entries_preserved': len(links),
                  'original_file_bytes': original_bytes, 'archive_bytes': archive.stat().st_size,
                  'archive_sha256': digest.hexdigest(), 'all_regular_file_bytes_verified': True,
                  'original_logs_kept': True, 'expanded_runtime_removed': True}
        # Every deletion target belongs to this task and is verified above.
        assert source.resolve() == source and source.resolve().is_relative_to(RUNTIME.resolve())
        shutil.rmtree(source)
        record.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'name': name, 'files': len(files), 'before_bytes': original_bytes,
                          'archive_bytes': result['archive_bytes']}), flush=True)
    (RUNTIME / 'tmp').mkdir(exist_ok=True)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        NAMES = sys.argv[1:]
        assert all(n.startswith('u') and n.replace('-', '').isalnum() for n in NAMES)
    main()
