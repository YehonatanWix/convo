from typer.testing import CliRunner
from convo_analyzer.cli import app
from convo_analyzer.load import open_db, load_session
from convo_analyzer.models import SessionRow, ToolCall
from convo_analyzer.parse import ParsedSession

runner = CliRunner()


def _seed(db_path):
    con = open_db(db_path)
    parent = ParsedSession(
        session_id="parent-1",
        session=SessionRow(session_id="parent-1", project="p", cwd="/p"),
        events=[], tool_calls=[], skills=[],
    )
    child = ParsedSession(
        session_id="agent-1",
        session=SessionRow(
            session_id="agent-1", project="p", cwd="/p",
            parent_session_id="parent-1", is_subagent=True,
        ),
        events=[],
        tool_calls=[
            ToolCall(event_id="e1", session_id="agent-1", ts="2026-05-29T16:00:00Z",
                     tool_name="LSP", args_json="{}", position_in_session=0),
            ToolCall(event_id="e2", session_id="agent-1", ts="2026-05-29T16:00:01Z",
                     tool_name="Bash", args_json="{}", position_in_session=1),
        ],
        skills=[],
    )
    load_session(con, parent)
    load_session(con, child)
    con.close()


def test_subagents_lists_children(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    _seed(tmp_path / "corpus.db")
    result = runner.invoke(app, ["subagents", "parent-1"])
    assert result.exit_code == 0
    assert "agent-1" in result.stdout
    assert "2" in result.stdout


def test_session_with_subagents_includes_child_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    _seed(tmp_path / "corpus.db")
    # Seed needs at least one event row tied to the child for the timeline to display
    # tool_name. Add via direct insert since _seed has empty events.
    import duckdb
    con = duckdb.connect(str(tmp_path / "corpus.db"))
    con.execute(
        "INSERT INTO events (event_id, session_id, ts, type, role) VALUES (?,?,?,?,?)",
        ["e1", "agent-1", "2026-05-29T16:00:00Z", "assistant", "assistant"],
    )
    con.execute(
        "INSERT INTO events (event_id, session_id, ts, type, role) VALUES (?,?,?,?,?)",
        ["e2", "agent-1", "2026-05-29T16:00:01Z", "assistant", "assistant"],
    )
    con.close()
    result = runner.invoke(app, ["session", "parent-1", "--with-subagents"])
    assert result.exit_code == 0, result.output
    assert "LSP" in result.stdout


def test_session_without_subagents_hides_child_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    _seed(tmp_path / "corpus.db")
    result = runner.invoke(app, ["session", "parent-1"])
    assert result.exit_code == 0
    assert "LSP" not in result.stdout
