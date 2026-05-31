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
