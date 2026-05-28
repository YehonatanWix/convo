# tests/test_derive_tokens.py
from convo_analyzer.load import open_db
from convo_analyzer.derive.tokens import build_token_signals

def test_redundant_reads_detected(tmp_corpus):
    con = open_db(tmp_corpus["db"])
    # two Reads of same file, no intervening Edit
    inserts = [
        ("Read", '{"file_path":"~/a.py"}', 0),
        ("Read", '{"file_path":"~/a.py"}', 1),
    ]
    for i, (name, args, pos) in enumerate(inserts):
        con.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [f"e{i}", "s1", f"2026-01-01T00:00:0{i}Z", name, args,
             None, 100, None, True, None, 0, pos],
        )
    build_token_signals(con)
    rows = con.execute("SELECT * FROM signal_redundant_reads").fetchall()
    assert len(rows) >= 1
