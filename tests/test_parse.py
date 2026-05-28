from convo_analyzer.parse import parse_session_file, ParsedSession

def test_parse_fixture(sample_session_path):
    s: ParsedSession = parse_session_file(sample_session_path)
    assert s.session_id
    assert len(s.events) > 0
    assert any(e.type == "assistant" for e in s.events)

def test_parse_extracts_tool_calls(sample_session_path):
    s = parse_session_file(sample_session_path)
    for tc in s.tool_calls:
        assert tc.tool_name
        assert tc.position_in_session >= 0

def test_parse_assistant_usage_tokens(sample_session_path):
    s = parse_session_file(sample_session_path)
    assert s.session.total_input_tokens >= 0
    assert s.session.total_output_tokens >= 0
