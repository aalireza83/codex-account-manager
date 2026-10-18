"""Private, process-safe Ping/Switch counters; never stores credentials or output."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from accounts import json_bytes, private_write
from locking import FileRLock


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ActivityStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = FileRLock(lambda: self.path.with_suffix('.lock'))

    def _read(self):
        if not self.path.exists():
            return {"schema_version": 1, "tracking_since": now(), "records": {}}
        document = json.loads(self.path.read_text())
        if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("records"), dict):
            raise ValueError("Activity data is invalid")
        for entity in document["records"].values():
            if not isinstance(entity,dict):raise ValueError("Activity data is invalid")
            for metric in entity.values():
                if not isinstance(metric,dict) or any(type(metric.get(key)) is not int or metric[key]<0 for key in ("success","failed")):
                    raise ValueError("Activity counters are invalid")
        return document

    def snapshot(self):
        with self.lock:
            document = self._read()
            if not self.path.exists():
                private_write(self.path, json_bytes(document))
            return document

    def record(self, kind, identifier, action, success, elapsed_ms):
        if kind not in ("account", "api") or action not in ("ping", "switch"):
            raise ValueError("Invalid activity type")
        with self.lock:
            document = self._read()
            entity = document["records"].setdefault(kind + ':' + identifier, {})
            metric = entity.setdefault(action, {"success": 0, "failed": 0})
            metric["success" if success else "failed"] += 1
            metric.update(last_at=now(), last_result="ok" if success else "error", last_duration_ms=round(max(0, elapsed_ms), 1))
            private_write(self.path, json_bytes(document))
