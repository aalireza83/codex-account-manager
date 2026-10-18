"""A process lease reports WebUI lifecycle without probing network addresses."""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from accounts import json_bytes, private_write


class WebRuntime:
    def __init__(self, config_path):
        path = Path(config_path)
        self.lock_path = path.with_name(path.name + ".web.lock")
        self.info_path = path.with_name(path.name + ".web.runtime.json")

    def status(self):
        if not self.lock_path.exists():
            return {"running": False}
        fd = os.open(self.lock_path, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    info = json.loads(self.info_path.read_text())
                    if not isinstance(info, dict):info = {}
                except (OSError, ValueError):
                    info = {}
                return {"running": True, **{k: info[k] for k in ("pid", "host", "port") if k in info}}
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
                return {"running": False}
        finally:
            os.close(fd)

    @contextmanager
    def lease(self, host, port):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                raise RuntimeError("WebUI is already running for this configuration") from None
            private_write(self.info_path, json_bytes({"pid": os.getpid(), "host": host, "port": port}))
            yield
        finally:
            if acquired:
                self.info_path.unlink(missing_ok=True)
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
