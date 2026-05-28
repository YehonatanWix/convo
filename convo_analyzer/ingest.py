# convo_analyzer/ingest.py
from __future__ import annotations
import pathlib
from typing import Callable, Optional
from .parse import parse_session_file
from .load import open_db, load_session
from .blobs import BlobStore
from .manifest import Manifest

ProgressFn = Callable[[dict], None]


def ingest_all(
    projects_root: pathlib.Path,
    db_path: pathlib.Path,
    blobs_path: pathlib.Path,
    manifest_path: pathlib.Path,
    on_progress: Optional[ProgressFn] = None,
) -> dict:
    projects_root = pathlib.Path(projects_root)
    blobs = BlobStore(blobs_path)
    manifest = Manifest(manifest_path)
    con = open_db(db_path)

    jsonls = sorted(projects_root.rglob("*.jsonl"))
    total = len(jsonls)
    if on_progress:
        on_progress({"event": "start", "total": total})

    n = skipped = 0
    for i, jsonl in enumerate(jsonls, 1):
        parsed = parse_session_file(jsonl)
        last_ts = parsed.session.ended_at or ""
        if manifest.has_seen(parsed.session_id, last_ts):
            skipped += 1
            if on_progress:
                on_progress({"event": "skip", "i": i, "total": total,
                             "session_id": parsed.session_id})
            continue
        load_session(con, parsed, blobs=blobs)
        manifest.mark(parsed.session_id, last_ts)
        n += 1
        if on_progress:
            on_progress({"event": "load", "i": i, "total": total,
                         "session_id": parsed.session_id,
                         "project": parsed.session.project,
                         "events": len(parsed.events),
                         "tool_calls": len(parsed.tool_calls)})
    manifest.save()
    con.close()
    if on_progress:
        on_progress({"event": "done", "ingested": n, "skipped": skipped, "total": total})
    return {"sessions_ingested": n, "skipped": skipped, "total": total}
