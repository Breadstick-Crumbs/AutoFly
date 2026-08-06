from __future__ import annotations

import os
import sys
from contextlib import suppress
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from autofly.errors import LockUnavailable

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class ProcessLock:
    """Advisory, non-blocking process lock held by an open file descriptor."""

    def __init__(self, path: Path):
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.path.open("a+b")
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError as exc:
            if "handle" in locals():
                handle.close()
            raise LockUnavailable(f"Another AutoFly cycle holds {self.path}") from exc
        handle.seek(0)
        handle.write(str(os.getpid()).encode().ljust(32, b" "))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return
        handle = self._file
        try:
            if sys.platform == "win32":
                handle.seek(0)
                with suppress(OSError):
                    msvcrt.locking(
                        handle.fileno(),
                        msvcrt.LK_UNLCK,
                        1,
                    )
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._file = None

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()
