# tests/test_derive_sequences.py
import json
from convo_analyzer.load import open_db
from convo_analyzer.derive.sequences import build_sequences

def _seed(con, calls):
    for i, (sid, tool) in enumerate(calls):
        con.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [f"e{i}", sid, f"2026-01-01T00:00:{i:02d}Z", tool,
             "{}", None, 0, None, True, None, 0, i],
        )

def test_build_sequences_counts_bigrams(tmp_corpus):
    con = open_db(tmp_corpus["db"])
    _seed(con, [("s1","Read"),("s1","Edit"),("s1","Read"),("s1","Edit")])
    build_sequences(con, ns=(2,))
    rows = con.execute(
        "select sequence, count from tool_sequences where n=2 order by count desc"
    ).fetchall()
    seqs = {tuple(json.loads(r[0])): r[1] for r in rows}
    assert seqs.get(("Read","Edit")) == 2
