# tests/test_cli.py
import os
import shutil
from typer.testing import CliRunner
from convo_analyzer.cli import app

runner = CliRunner()

def test_cli_ingest_and_list(tmp_path, sample_session_path, monkeypatch):
    projects = tmp_path / "projects" / "-Users-y-foo"
    projects.mkdir(parents=True)
    shutil.copy(sample_session_path, projects / "x.jsonl")
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    monkeypatch.setenv("CONVO_PROJECTS", str(tmp_path / "projects"))
    r = runner.invoke(app, ["ingest"])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(app, ["top-bloat", "--limit", "5"])
    assert r2.exit_code == 0


def test_agents_guide_mentions_subagents():
    result = runner.invoke(app, ["agents"])
    assert result.exit_code == 0
    assert "parent_session_id" in result.stdout
    assert "subagents" in result.stdout.lower()
    assert "--with-subagents" in result.stdout
