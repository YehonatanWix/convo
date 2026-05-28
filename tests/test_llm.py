from convo_analyzer.llm import build_dashboard, write_analysis_inputs
from convo_analyzer.load import open_db


def test_build_dashboard_empty(tmp_corpus):
    open_db(tmp_corpus["db"])  # create tables
    d = build_dashboard(tmp_corpus["db"])
    assert "top_bloat" in d
    assert isinstance(d["top_bloat"], list)


def test_write_analysis_inputs(tmp_corpus, tmp_path):
    open_db(tmp_corpus["db"])
    out = write_analysis_inputs(tmp_corpus["db"], tmp_path / "analysis")
    assert out["dashboard_path"].exists()
    assert out["prompt_path"].exists()
    assert str(out["dashboard_path"]) in out["prompt"]
