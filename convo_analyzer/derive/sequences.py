# convo_analyzer/derive/sequences.py
from __future__ import annotations
import json
from collections import Counter
import duckdb

def build_sequences(con: duckdb.DuckDBPyConnection, ns=(2,3,4,5)) -> None:
    con.execute("DELETE FROM tool_sequences")
    sessions = [r[0] for r in con.execute(
        "SELECT DISTINCT session_id FROM tool_calls"
    ).fetchall()]
    for sid in sessions:
        rows = con.execute(
            "SELECT tool_name, ts FROM tool_calls WHERE session_id=? "
            "ORDER BY position_in_session", [sid],
        ).fetchall()
        names = [r[0] for r in rows]
        tss = [r[1] for r in rows]
        for n in ns:
            counts: Counter = Counter()
            firsts: dict = {}
            lasts: dict = {}
            for i in range(len(names) - n + 1):
                key = tuple(names[i:i+n])
                counts[key] += 1
                firsts.setdefault(key, tss[i])
                lasts[key] = tss[i+n-1]
            for seq, c in counts.items():
                con.execute(
                    "INSERT INTO tool_sequences VALUES (?,?,?,?,?,?)",
                    [sid, n, json.dumps(list(seq)), c, firsts[seq], lasts[seq]],
                )
