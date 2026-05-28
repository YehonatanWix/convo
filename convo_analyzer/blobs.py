from __future__ import annotations
import hashlib
import pathlib

class BlobStore:
    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, h: str) -> pathlib.Path:
        return self.root / h[:2] / f"{h}.txt"

    def put(self, content: str) -> str:
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        p = self._path(h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return h

    def get(self, h: str) -> str:
        return self._path(h).read_text()
