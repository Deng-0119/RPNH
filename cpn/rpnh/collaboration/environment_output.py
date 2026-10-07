"""Private file delivery for a package run, without any execution authority."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import stat

from .environment_contracts import EnvironmentContractError


class ReservedRunOutput:
    """Reserve once before approval and write only through the owned descriptor.

    The directory must already exist. Keeping its descriptor also makes cleanup
    independent of later changes to a parent path. Failed delivery is never a
    reason to restart a business run or overwrite a different output.
    """

    def __init__(self, path):
        self.path = Path(path).absolute()
        self._directory = None
        self._stream = None
        self._identity = None
        self._delivered = False

    def __enter__(self):
        try:
            self._directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            fd = os.open(self.path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=self._directory)
            try:
                info = os.fstat(fd)
                self._identity = (info.st_dev, info.st_ino)
                self._stream = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise
            return self
        except BaseException as exc:
            self.__exit__(None, None, None)
            if isinstance(exc, FileExistsError):
                raise EnvironmentContractError("ENVIRONMENT_OUTPUT_EXISTS",
                    "output already exists; choose another filename") from exc
            raise

    def _owns_leaf(self):
        if self._identity is None:
            return False
        try:
            info = os.stat(self.path.name, dir_fd=self._directory, follow_symlinks=False)
        except OSError:
            return False
        return (info.st_dev, info.st_ino) == self._identity and stat.S_ISREG(info.st_mode)

    def _check_destination(self):
        info = os.fstat(self._stream.fileno())
        visible = os.stat(self.path, follow_symlinks=False)
        if (not self._owns_leaf() or (visible.st_dev, visible.st_ino) != self._identity
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            raise OSError(errno.ESTALE, "reserved output changed")

    def write(self, payload):
        self._check_destination()
        if self._stream.write(payload) != len(payload):
            raise OSError(errno.EIO, "incomplete result write")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._check_destination()
        # Closing is part of delivery: delayed I/O failures must not look like
        # a successfully saved result merely because write() returned.
        self._stream.close()
        self._delivered = True

    def __exit__(self, exc_type, exc, traceback):
        try:
            if not self._delivered and self._owns_leaf():
                try:
                    os.unlink(self.path.name, dir_fd=self._directory)
                except OSError:
                    # The caller reports delivery failure. An unremovable
                    # partial file is not evidence of successful delivery.
                    pass
        finally:
            if self._stream is not None:
                try:
                    self._stream.close()
                except OSError:
                    pass
            if self._directory is not None:
                try:
                    os.close(self._directory)
                except OSError:
                    pass
                self._directory = None
