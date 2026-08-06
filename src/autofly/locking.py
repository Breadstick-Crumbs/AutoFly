from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from types import TracebackType

from autofly.errors import LockUnavailable


class ProcessLock:
    """Advisory, non-blocking process lock held by an open file descriptor."""

    def __init__(self, path: Path):
        self.path = path
        self._file: object | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.path.open("a+b")
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
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
            if os.name == "nt":
                import msvcrt

                handle.seek(0)  # type: ignore[attr-defined]
                with suppress(OSError):
                    msvcrt.locking(  # type: ignore[attr-defined]
                        handle.fileno(),
                        msvcrt.LK_UNLCK,
                        1,  # type: ignore[attr-defined]
                    )
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        finally:
            handle.close()  # type: ignore[attr-defined]
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
