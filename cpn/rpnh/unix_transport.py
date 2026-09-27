"""Linux-local AF_UNIX path resolution without flattening Registry trees."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator


_SUN_PATH_BYTES = 108


@contextmanager
def unix_socket_address(
        socket_path: str | Path, *, visible_to_child_process: bool = False,
) -> Iterator[str]:
    """Yield a short transport address for a socket at ``socket_path``.

    The durable/logical path remains relative to its Registry parent.  Linux
    resolves the temporary ``/proc`` address through an open descriptor for
    that immediate parent, so path depth above the Registry never enters
    ``sockaddr_un.sun_path``.  The address is not persisted as Registry state.
    """
    path = Path(socket_path)
    if path.name in {"", ".", ".."}:
        raise ValueError("Unix socket path requires one file name")
    directory_fd = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        process = str(os.getpid()) if visible_to_child_process else "self"
        address = f"/proc/{process}/fd/{directory_fd}/{path.name}"
        if len(os.fsencode(address)) >= _SUN_PATH_BYTES:
            raise ValueError("Unix socket file name exceeds AF_UNIX capacity")
        yield address
    finally:
        os.close(directory_fd)


__all__ = ("unix_socket_address",)
