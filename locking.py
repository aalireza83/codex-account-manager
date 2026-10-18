"""Reentrant thread and process locks for CLI/WebUI state transactions."""
import fcntl
import os
import threading
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def file_lock(path):
    path = Path(path).absolute()
    key = str(path)
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, "held", {})
        if key in held:
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            held[key] = 1
            _local.held = held
            try:
                yield
            finally:
                del held[key]
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


class FileRLock:
    def __init__(self, path_factory):
        self.path_factory = path_factory
        self.local = threading.local()

    def __enter__(self):
        context = file_lock(self.path_factory())
        context.__enter__()
        stack = getattr(self.local, "stack", [])
        stack.append(context)
        self.local.stack = stack
        return self

    def __exit__(self, *args):
        return self.local.stack.pop().__exit__(*args)
