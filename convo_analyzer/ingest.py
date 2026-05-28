# convo_analyzer/ingest.py
from __future__ import annotations
import pathlib
from .parse import parse_session_file
from .load import open_db, load_session
from .blobs import BlobStore
from .manifest import Manifest

def ingest_all(
    projects_root: pathlib.Path,
    db_path: pathlib.Path,
    blobs_path: pathlib.Path,
    manifest_path: pathlib.Path,
) -> dict:
    projects_root = pathlib.Path(projects_root)
    blobs = BlobStore(blobs_path)
    manifest = Manifest(manifest_path)
    con = open_db(db_path)
    n = 0
    for jsonl in projects_root.rglob("*.jsonl"):
        parsed = parse_session_file(jsonl)
        last_ts = parsed.session.ended_at or ""
        if manifest.has_seen(parsed.session_id, last_ts):
            continue
        load_session(con, parsed, blobs=blobs)
        manifest.mark(parsed.session_id, last_ts)
        n += 1
    manifest.save()
    con.close()
    return {"sessions_ingested": n}
