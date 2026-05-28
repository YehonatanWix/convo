# convo_analyzer/manifest.py
from __future__ import annotations
import json
import pathlib

class Manifest:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text() or "{}")

    def has_seen(self, session_id: str, last_ts: str) -> bool:
        prev = self._data.get(session_id)
        return prev is not None and prev >= last_ts

    def mark(self, session_id: str, last_ts: str) -> None:
        self._data[session_id] = last_ts

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))
