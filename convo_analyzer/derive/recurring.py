# convo_analyzer/derive/recurring.py
from __future__ import annotations
import json
import re
import duckdb

CORRECTION_RE = re.compile(r"^\s*(no|don't|stop|wait|actually)\b", re.I)

DDL = """
CREATE TABLE IF NOT EXISTS signal_recurring_sequences (
  sequence TEXT, n INTEGER, sessions INTEGER, projects INTEGER
);
CREATE TABLE IF NOT EXISTS signal_correction_clusters (
  preceding_tools TEXT, occurrences INTEGER
);
CREATE TABLE IF NOT EXISTS signal_repeated_path_fixes (
  session_id TEXT, before TEXT, after TEXT
);
"""

def build_recurring_signals(
    con: duckdb.DuckDBPyConnection,
    min_sessions: int = 5,
    min_projects: int = 3,
) -> None:
    con.execute(DDL)
    for t in ("signal_recurring_sequences","signal_correction_clusters","signal_repeated_path_fixes"):
        con.execute(f"DELETE FROM {t}")

    con.execute("""
        INSERT INTO signal_recurring_sequences
        SELECT ts.sequence, ts.n,
               COUNT(DISTINCT ts.session_id) AS sessions,
               COUNT(DISTINCT s.project)     AS projects
        FROM tool_sequences ts
        JOIN sessions s USING (session_id)
        GROUP BY ts.sequence, ts.n
        HAVING sessions >= ? AND projects >= ?
    """, [min_sessions, min_projects])

    # correction clusters: user msgs matching the correction regex, grouped by
    # the 3 preceding tool calls in the session.
    corrections = con.execute("""
        SELECT session_id, ts, text_head FROM events
        WHERE role='user' AND is_meta=FALSE AND text_head IS NOT NULL
    """).fetchall()
    counts: dict[tuple[str, ...], int] = {}
    for sid, ts, head in corrections:
        if not CORRECTION_RE.match(head or ""):
            continue
        preceding = con.execute("""
            SELECT tool_name FROM tool_calls
            WHERE session_id=? AND ts < ?
            ORDER BY position_in_session DESC LIMIT 3
        """, [sid, ts]).fetchall()
        key = tuple(t[0] for t in reversed(preceding))
        counts[key] = counts.get(key, 0) + 1
    for key, c in counts.items():
        con.execute(
            "INSERT INTO signal_correction_clusters VALUES (?,?)",
            [json.dumps(list(key)), c],
        )
