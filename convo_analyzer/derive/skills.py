# convo_analyzer/derive/skills.py
from __future__ import annotations
import pathlib
import re
import yaml
import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS signal_skill_eligible_missed (
  session_id TEXT, skill_name TEXT, position INTEGER
);
CREATE TABLE IF NOT EXISTS signal_skill_abandoned (
  session_id TEXT, skill_name TEXT, ts TEXT, correction TEXT
);
CREATE TABLE IF NOT EXISTS signal_skill_turnaround (
  session_id TEXT, skill_name TEXT, ts TEXT, tokens_to_user BIGINT
);
"""

CORRECTION_RE = re.compile(r"^\s*(no\b|don't\b|stop\b|wait\b|actually\b)", re.I)

def build_skill_signals(con: duckdb.DuckDBPyConnection, skills_yaml: pathlib.Path) -> None:
    con.execute(DDL)
    for t in ("signal_skill_eligible_missed","signal_skill_abandoned","signal_skill_turnaround"):
        con.execute(f"DELETE FROM {t}")
    cfg = yaml.safe_load(pathlib.Path(skills_yaml).read_text()) or {}

    sessions = [r[0] for r in con.execute(
        "SELECT DISTINCT session_id FROM tool_calls"
    ).fetchall()]
    for sid in sessions:
        tools = [r[0] for r in con.execute(
            "SELECT tool_name FROM tool_calls WHERE session_id=? ORDER BY position_in_session",
            [sid],
        ).fetchall()]
        invoked = {r[0] for r in con.execute(
            "SELECT skill_name FROM skill_invocations WHERE session_id=?", [sid],
        ).fetchall()}
        for name, spec in cfg.items():
            sig = spec.get("signature") or []
            if not sig or name in invoked:
                continue
            for i in range(len(tools) - len(sig) + 1):
                if tools[i:i+len(sig)] == sig:
                    con.execute(
                        "INSERT INTO signal_skill_eligible_missed VALUES (?,?,?)",
                        [sid, name, i],
                    )
                    break

    # abandoned: skill invocation followed within 3 user turns by a correction.
    # Only flag if the user message text actually matches the correction regex.
    rows = con.execute(
        "SELECT session_id, ts, skill_name FROM skill_invocations"
    ).fetchall()
    for sid, ts, skill in rows:
        followups = con.execute("""
            SELECT ts, text_head FROM events
            WHERE session_id=? AND ts > ? AND role='user'
              AND is_meta=FALSE AND text_head IS NOT NULL
            ORDER BY ts LIMIT 3
        """, [sid, ts]).fetchall()
        for fts, head in followups:
            m = CORRECTION_RE.match(head or "")
            if m:
                con.execute(
                    "INSERT INTO signal_skill_abandoned VALUES (?,?,?,?)",
                    [sid, skill, fts, (head or "")[:120]],
                )
                break  # one hit per invocation is enough

    # turnaround: tokens between skill invocation and next user msg
    con.execute("""
        INSERT INTO signal_skill_turnaround
        SELECT si.session_id, si.skill_name, si.ts,
               COALESCE((
                 SELECT SUM(output_tokens) FROM events e
                 WHERE e.session_id=si.session_id
                   AND e.ts > si.ts
                   AND e.ts < COALESCE((
                     SELECT MIN(ts) FROM events e2
                     WHERE e2.session_id=si.session_id AND e2.role='user' AND e2.ts > si.ts
                   ), '9999')
               ), 0)
        FROM skill_invocations si
    """)
