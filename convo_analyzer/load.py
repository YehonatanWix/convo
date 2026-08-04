# convo_analyzer/load.py
from __future__ import annotations
import json
import pathlib
import duckdb

from .parse import ParsedSession
from .sanitize import scrub, normalize_path
from .blobs import BlobStore

SCHEMA = pathlib.Path(__file__).parent / "schema.sql"

def open_db(path: pathlib.Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path))
    con.execute(SCHEMA.read_text())
    return con

def _delete_session(con, session_id: str) -> None:
    for tbl in ("sessions", "events", "tool_calls", "skill_invocations", "tool_sequences"):
        con.execute(f"DELETE FROM {tbl} WHERE session_id = ?", [session_id])

def load_session(
    con: duckdb.DuckDBPyConnection,
    parsed: ParsedSession,
    blobs: BlobStore | None = None,
) -> None:
    _delete_session(con, parsed.session_id)

    s = parsed.session
    con.execute(
        """INSERT INTO sessions (
            session_id, project, cwd, git_branch,
            started_at, ended_at, duration_ms, message_count,
            total_input_tokens, total_output_tokens,
            total_cache_read_tokens, total_cache_creation_tokens,
            compaction_count, model, ai_title,
            parent_session_id, is_subagent, ingested_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        [s.session_id, s.project, normalize_path(s.cwd), s.git_branch,
         s.started_at, s.ended_at, s.duration_ms, s.message_count,
         s.total_input_tokens, s.total_output_tokens,
         s.total_cache_read_tokens, s.total_cache_creation_tokens,
         s.compaction_count, s.model, s.ai_title,
         s.parent_session_id, s.is_subagent],
    )

    for ev in parsed.events:
        con.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [ev.event_id, ev.session_id, ev.parent_uuid, ev.ts, ev.type,
             ev.subtype, ev.is_sidechain, ev.is_meta, ev.role,
             ev.input_tokens, ev.output_tokens, ev.cache_read, ev.cache_creation,
             ev.duration_ms, ev.text_len,
             scrub(ev.text_head) if ev.text_head else None,
             ev.blob_hash],
        )

    for tc in parsed.tool_calls:
        args_clean = scrub(tc.args_json)
        args_hash = blobs.put(args_clean) if (blobs and len(args_clean) >= 2048) else None
        con.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [tc.event_id, tc.session_id, tc.ts, tc.tool_name,
             args_clean[:2048], args_hash, tc.result_size,
             tc.result_blob_hash, tc.success, tc.duration_ms,
             tc.retry_attempt, tc.position_in_session],
        )

    for sk in parsed.skills:
        con.execute(
            "INSERT INTO skill_invocations VALUES (?,?,?,?,?,?)",
            [sk.session_id, sk.ts, sk.skill_name, sk.args,
             sk.triggered_by, json.dumps(sk.followed_by_tools)],
        )
