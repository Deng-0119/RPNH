"""Import exact acquired frontend data for the existing offline recipes.

Trees use one fixed ``material/`` tar.gz layout. No archive command, package
manager hook or alternate source is interpreted here. Original material and
installed launch readers still validate and prepare the selected frontend.
"""
from __future__ import annotations

import gzip
import os
from pathlib import Path, PurePosixPath
import posixpath
import stat
import tarfile

from .install_materials import _TREE_BYTES_LIMIT, _TREE_LIMIT, _tree_check


# Reuse the original material-tree capacity rather than inventing a smaller
# frontend limit which would exclude a complete frozen dependency store.
MAX_FRONTEND_ASSET_BYTES = _TREE_BYTES_LIMIT
MAX_FRONTEND_TOTAL_BYTES = _TREE_BYTES_LIMIT
FRONTEND_STREAM_SECONDS = 300.0
MAX_ARCHIVE_METADATA_READ = 1024 * 1024


class _ArchiveReader:
    """Bound gzip expansion and parser metadata reads before allocation."""
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    def tell(self):
        return self.stream.tell()

    def read(self, size):
        if not 0 <= size <= MAX_ARCHIVE_METADATA_READ or self.tell() + size > self.limit:
            raise ValueError("frontend archive metadata or expansion exceeds its size limit")
        return self.stream.read(size)

    def seek(self, offset, whence=0):
        target = offset if whence == 0 else self.tell() + offset if whence == 1 else -1
        if not 0 <= target <= self.limit:
            raise ValueError("frontend archive seek exceeds its expansion limit")
        return self.stream.seek(target)


def _member_name(member):
    raw = member.name.rstrip("/") if member.isdir() else member.name
    path = PurePosixPath(raw)
    if (not raw or "\\" in raw or any(ord(c) < 32 for c in raw)
            or path.is_absolute() or path.as_posix() != raw or ".." in path.parts
            or path.parts[0] != "material" or len(path.parts) == 1 and not member.isdir()):
        raise ValueError("frontend archive must contain only its fixed material directory")
    return raw


def unpack_frontend_tree(archive_path: Path, destination: Path) -> Path:
    """Extract bounded regular data and internal links into a new private root."""
    destination = Path(destination)
    if destination != destination.resolve() or os.path.lexists(destination):
        raise ValueError("frontend archive requires a new real destination")
    expanded_limit = _TREE_BYTES_LIMIT + (_TREE_LIMIT + 1) * 4096
    try:
        with gzip.open(archive_path, "rb") as compressed, tarfile.open(
                fileobj=_ArchiveReader(compressed, expanded_limit), mode="r:") as archive:
            indexed, total = {}, 0
            tree_entries, entry_count = {}, 0
            for member in archive:
                name = _member_name(member)
                entries = len(indexed) - ("material" in indexed)
                if name in indexed or name != "material" and entries >= _TREE_LIMIT:
                    raise ValueError("frontend archive repeats a member or exceeds the entry limit")
                # Count implicit ancestors before any filesystem creation.
                # The fixed material root is outside the original tree limit.
                cursor = tree_entries
                for part in PurePosixPath(name).parts[1:]:
                    if part not in cursor:
                        entry_count += 1
                        if entry_count > _TREE_LIMIT:
                            raise ValueError("frontend archive exceeds its tree entry limit")
                        cursor[part] = {}
                    cursor = cursor[part]
                if member.issparse() or not (member.isdir() or member.isreg() or member.issym()):
                    raise ValueError("frontend archive contains a special, sparse or hardlinked member")
                if member.isreg():
                    if member.size < 0:
                        raise ValueError("frontend archive has an invalid member size")
                    total += member.size
                    if total > _TREE_BYTES_LIMIT:
                        raise ValueError("frontend archive exceeds the expanded byte limit")
                if member.issym():
                    link = member.linkname
                    resolved = PurePosixPath(posixpath.normpath(str(PurePosixPath(name).parent / link)))
                    if (not link or "\\" in link or any(ord(c) < 32 for c in link)
                            or PurePosixPath(link).is_absolute()
                            or not resolved.parts or resolved.parts[0] != "material"):
                        raise ValueError("frontend archive link escapes its material directory")
                indexed[name] = member
            if not indexed:
                raise ValueError("frontend archive has no material members")
            for name in indexed:
                for parent in PurePosixPath(name).parents:
                    if str(parent) in indexed and not indexed[str(parent)].isdir():
                        raise ValueError("frontend archive writes through a non-directory member")
            destination.mkdir(mode=0o700)
            root = destination / "material"
            root.mkdir(mode=0o700)
            # Links come last: no stream ever follows an archive-created link.
            for name, member in indexed.items():
                target = destination / name
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                elif member.isreg():
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600 | (member.mode & 0o111))
                    with os.fdopen(descriptor, "wb") as output:
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise ValueError("frontend archive member has no data")
                        with stream:
                            copied = 0
                            while chunk := stream.read(min(65536, member.size - copied + 1)):
                                copied += len(chunk)
                                if copied > member.size:
                                    raise ValueError("frontend archive member exceeds its declared size")
                                output.write(chunk)
                            if copied != member.size:
                                raise ValueError("frontend archive member is truncated")
            for name, member in indexed.items():
                if member.issym():
                    target = destination / name
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    target.symlink_to(member.linkname)
            _tree_check(root)
            return root
    except (tarfile.TarError, OSError, EOFError, RuntimeError) as exc:
        raise ValueError("frontend archive is unavailable, malformed or changed during import") from exc


def tree_observation(root: Path) -> tuple:
    """Observe the complete imported item set, without content hashing.

    This transient same-invocation observation includes directories and link
    targets as well as regular files. It is never a durable origin credential.
    """
    root = Path(root)
    try:
        if root != root.resolve(strict=True) or not stat.S_ISDIR(root.lstat().st_mode):
            raise ValueError("acquired frontend tree must remain a real directory")
        rows, total = [], 0

        def observe(path):
            nonlocal total
            info = path.lstat()
            if len(rows) >= _TREE_LIMIT + 1:
                raise ValueError("acquired frontend tree exceeds its entry limit")
            link = None
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(path)
                if os.path.isabs(link) or not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError("acquired frontend tree contains a changed or external link")
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > _TREE_BYTES_LIMIT:
                    raise ValueError("acquired frontend tree exceeds its byte limit")
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError("acquired frontend tree contains a special file")
            rows.append((path.relative_to(root).as_posix(), info.st_dev, info.st_ino, info.st_mode,
                         info.st_size, info.st_mtime_ns, info.st_ctime_ns, link))

        def failed(error):
            raise error

        observe(root)
        for current, directories, files in os.walk(root, followlinks=False, onerror=failed):
            directories.sort()
            for name in sorted((*directories, *files)):
                observe(Path(current) / name)
        return (str(root), "tree", tuple(sorted(rows)))
    except (OSError, RuntimeError) as exc:
        raise ValueError("acquired frontend tree changed or became unavailable") from exc


__all__ = ("unpack_frontend_tree", "tree_observation")
