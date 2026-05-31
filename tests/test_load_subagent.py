from convo_analyzer.load import open_db, load_session
from convo_analyzer.models import SessionRow
from convo_analyzer.parse import ParsedSession


def test_sessions_table_has_subagent_columns(tmp_path):
    db = tmp_path / "corpus.db"
    con = open_db(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info('sessions')").fetchall()}
    assert "parent_session_id" in cols
    assert "is_subagent" in cols


def _row(session_id: str, parent_session_id=None, is_subagent=False) -> ParsedSession:
    return ParsedSession(
        session_id=session_id,
        session=SessionRow(
            session_id=session_id,
            project="responsive-editor-packages",
            cwd="~/Workspace/Wix/responsive-editor-packages",
            parent_session_id=parent_session_id,
            is_subagent=is_subagent,
        ),
        events=[], tool_calls=[], skills=[],
    )


def test_load_persists_subagent_fields(tmp_path):
    con = open_db(tmp_path / "corpus.db")
    load_session(con, _row("parent-1"))
    load_session(con, _row("agent-1", parent_session_id="parent-1", is_subagent=True))

    rows = con.execute(
        "SELECT session_id, parent_session_id, is_subagent FROM sessions ORDER BY session_id"
    ).fetchall()
    assert rows == [
        ("agent-1", "parent-1", True),
        ("parent-1", None, False),
    ]


def test_reopen_db_preserves_is_subagent(tmp_path):
    """DuckDB ADD COLUMN IF NOT EXISTS ... DEFAULT FALSE resets existing values
    on re-execution. open_db re-runs schema.sql on every call (e.g. CLI ingest
    opens the DB twice — once during ingest_all, once for derive steps), so any
    DEFAULT on is_subagent would silently zero out subagent flags."""
    db = tmp_path / "corpus.db"
    con = open_db(db)
    load_session(con, _row("agent-1", parent_session_id="parent-1", is_subagent=True))
    con.close()
    con = open_db(db)
    row = con.execute(
        "SELECT is_subagent FROM sessions WHERE session_id = 'agent-1'"
    ).fetchone()
    assert row == (True,)
