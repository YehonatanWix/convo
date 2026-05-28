# tests/test_e2e.py
import pathlib
import pytest
from typer.testing import CliRunner
from convo_analyzer.cli import app

runner = CliRunner()

@pytest.mark.skipif(
    not (pathlib.Path.home() / ".claude/projects").exists(),
    reason="no Claude projects dir on this machine",
)
def test_full_pipeline_against_real_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    r = runner.invoke(app, ["ingest"])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(app, ["top-bloat", "--limit", "3"])
    assert r2.exit_code == 0
    r3 = runner.invoke(app, ["recurring-sequences", "--min-sessions", "3", "--limit", "5"])
    assert r3.exit_code == 0
