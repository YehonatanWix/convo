# tests/test_derive_recurring.py
import json
from convo_analyzer.load import open_db
from convo_analyzer.derive.recurring import build_recurring_signals

def test_recurring_sequence_threshold(tmp_corpus):
    con = open_db(tmp_corpus["db"])
    for sid in ("s1","s2","s3","s4","s5"):
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            [sid,"proj"+sid[-1],"~/x",None,None,None,None,0,0,0,0,0,0,None,None])
        con.execute("INSERT INTO tool_sequences VALUES (?,?,?,?,?,?)",
            [sid, 2, json.dumps(["Read","Edit"]), 1, "2026","2026"])
    build_recurring_signals(con)
    rows = con.execute("SELECT * FROM signal_recurring_sequences").fetchall()
    assert any("Read" in r[0] for r in rows)
