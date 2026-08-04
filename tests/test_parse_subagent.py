import pathlib
from convo_analyzer.parse import _project_slug, _subagent_parent_id, parse_session_file

PROJECT_DIR = "-Users-yehonatana-Workspace-Wix-responsive-editor-packages"
PARENT_ID = "29dd51ef-83f4-4dc3-b87f-5cc005f5a1d7"


def test_regular_session_project_slug(tmp_path):
    proj = tmp_path / PROJECT_DIR
    proj.mkdir()
    f = proj / "abc.jsonl"
    f.write_text("")
    slug, cwd = _project_slug(f)
    assert slug == "packages"
    assert "Workspace/Wix" in cwd


def test_subagent_session_project_slug_inherits_parent(tmp_path):
    proj = tmp_path / PROJECT_DIR / PARENT_ID / "subagents"
    proj.mkdir(parents=True)
    f = proj / "agent-a478ac3907e21b2ff.jsonl"
    f.write_text("")
    slug, cwd = _project_slug(f)
    assert slug == "packages"
    assert "Workspace/Wix" in cwd


def test_subagent_parent_id_detection(tmp_path):
    proj = tmp_path / PROJECT_DIR / PARENT_ID / "subagents"
    proj.mkdir(parents=True)
    f = proj / "agent-a478ac3907e21b2ff.jsonl"
    f.write_text("")
    assert _subagent_parent_id(f) == PARENT_ID


def test_subagent_parent_id_none_for_regular(tmp_path):
    proj = tmp_path / PROJECT_DIR
    proj.mkdir()
    f = proj / "abc.jsonl"
    f.write_text("")
    assert _subagent_parent_id(f) is None


def _minimal_jsonl(path: pathlib.Path) -> None:
    path.write_text(
        '{"type":"user","timestamp":"2026-05-29T16:00:00Z","uuid":"e1",'
        '"message":{"role":"user","content":"hi"}}\n'
    )


def test_parse_marks_subagent(tmp_path):
    p = tmp_path / PROJECT_DIR / PARENT_ID / "subagents"
    p.mkdir(parents=True)
    f = p / "agent-a478ac3907e21b2ff.jsonl"
    _minimal_jsonl(f)
    parsed = parse_session_file(f)
    assert parsed.session.is_subagent is True
    assert parsed.session.parent_session_id == PARENT_ID
    assert parsed.session.project == "packages"


def test_parse_marks_regular_session(tmp_path):
    p = tmp_path / PROJECT_DIR
    p.mkdir()
    f = p / "abc12345-0000-0000-0000-000000000000.jsonl"
    _minimal_jsonl(f)
    parsed = parse_session_file(f)
    assert parsed.session.is_subagent is False
    assert parsed.session.parent_session_id is None
