# tests/test_llm.py
from convo_analyzer.llm import build_dashboard, interpret
from convo_analyzer.load import open_db

class _FakeClient:
    def __init__(self): self.messages = self
    def create(self, **kw):
        class R: content=[type("B",(),{"text":"OK"})()]
        return R()

def test_build_dashboard_empty(tmp_corpus):
    open_db(tmp_corpus["db"])  # create tables
    d = build_dashboard(tmp_corpus["db"])
    assert "top_bloat" in d
    assert isinstance(d["top_bloat"], list)

def test_interpret_returns_text(tmp_corpus, monkeypatch):
    open_db(tmp_corpus["db"])
    out = interpret(tmp_corpus["db"], client=_FakeClient(), model="claude-opus-4-7")
    assert out == "OK"
