from convo_analyzer.models import NormalizedEvent, SessionRow, ToolCall

def test_event_minimal_user_message():
    e = NormalizedEvent(
        event_id="u1", session_id="s1", ts="2026-05-01T00:00:00Z",
        type="user", role="user", text_len=12,
    )
    assert e.type == "user"
    assert e.input_tokens is None

def test_tool_call_position_required():
    t = ToolCall(
        event_id="e1", session_id="s1", ts="2026-05-01T00:00:00Z",
        tool_name="Read", args_json='{"file_path":"/tmp/x"}',
        result_size=42, success=True, position_in_session=3,
    )
    assert t.tool_name == "Read"
    assert t.result_blob_hash is None


def test_session_row_subagent_fields_default():
    s = SessionRow(session_id="x", project="p", cwd="/p")
    assert s.parent_session_id is None
    assert s.is_subagent is False


def test_session_row_subagent_fields_set():
    s = SessionRow(
        session_id="agent-1", project="p", cwd="/p",
        parent_session_id="parent-uuid", is_subagent=True,
    )
    assert s.parent_session_id == "parent-uuid"
    assert s.is_subagent is True
