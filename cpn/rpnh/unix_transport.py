"""POSIX AF_UNIX path resolution without flattening Registry trees."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Iterator


_SUN_PATH_BYTES = 108
_DARWIN_SUN_PATH_BYTES = 104


def _assert_directory_identity(path: Path, descriptor: int) -> None:
    observed = path.stat()
    opened = os.fstat(descriptor)
    if (not stat.S_ISDIR(observed.st_mode)
            or (observed.st_dev, observed.st_ino)
            != (opened.st_dev, opened.st_ino)):
        raise RuntimeError("Unix socket parent directory changed during transport")


@contextmanager
def _darwin_socket_address(path: Path, directory_fd: int) -> Iterator[str]:
    # macOS's default TMPDIR is often too long for sockaddr_un.  This private
    # 0700 directory is merely a short, temporary name for the original parent;
    # the socket itself remains in that parent.  It is not a Registry locator
    # or a shared socket directory.  Neither process cwd nor Registry is moved.
    source_parent = path.parent.absolute()
    parent = source_parent.resolve(strict=True)
    _assert_directory_identity(source_parent, directory_fd)
    with tempfile.TemporaryDirectory(
            prefix=f"rpnh-{os.geteuid()}-", dir="/tmp") as raw:
        root = Path(raw)
        observed = root.lstat()
        if (not stat.S_ISDIR(observed.st_mode)
                or observed.st_uid != os.geteuid()
                or stat.S_IMODE(observed.st_mode) != 0o700):
            raise RuntimeError("Unix socket alias directory is not private")
        link = root / "d"
        link.symlink_to(parent, target_is_directory=True)
        address = str(link / path.name)
        if len(os.fsencode(address)) >= _DARWIN_SUN_PATH_BYTES:
            raise ValueError("Unix socket file name exceeds Darwin AF_UNIX capacity")
        _assert_directory_identity(source_parent, directory_fd)
        yield address
        # An alias must not turn a successful bind/connect into success for a
        # replacement parent.  Fail closed; cleanup only removes our alias.
        _assert_directory_identity(source_parent, directory_fd)


@contextmanager
def unix_socket_address(
        socket_path: str | Path, *, visible_to_child_process: bool = False,
) -> Iterator[str]:
    """Yield a short transport address for a socket at ``socket_path``.

    The durable/logical path remains relative to its Registry parent. Linux
    retains the descriptor-based ``/proc`` transport. Darwin uses a private
    short parent alias because it has no procfs and has a smaller sun_path.
    Keep this context open while a child uses the yielded address. No transport
    address is persisted as Registry state.
    """
    path = Path(socket_path)
    if path.name in {"", ".", ".."}:
        raise ValueError("Unix socket path requires one file name")
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        raise RuntimeError("Unix socket transport requires Linux or macOS")
    directory_fd = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        if sys.platform == "darwin":
            with _darwin_socket_address(path, directory_fd) as address:
                yield address
            return
        process = str(os.getpid()) if visible_to_child_process else "self"
        address = f"/proc/{process}/fd/{directory_fd}/{path.name}"
        if len(os.fsencode(address)) >= _SUN_PATH_BYTES:
            raise ValueError("Unix socket file name exceeds AF_UNIX capacity")
        yield address
    finally:
        os.close(directory_fd)


__all__ = ("unix_socket_address",)
