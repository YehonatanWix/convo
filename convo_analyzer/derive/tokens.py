# convo_analyzer/derive/tokens.py
from __future__ import annotations
import json
import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS signal_bloat (
  session_id TEXT, event_id TEXT, tool_name TEXT,
  result_size BIGINT, next_output_tokens BIGINT, ratio DOUBLE
);
CREATE TABLE IF NOT EXISTS signal_compaction_proximity (
  session_id TEXT, window_turns INTEGER, tokens BIGINT
);
CREATE TABLE IF NOT EXISTS signal_redundant_reads (
  session_id TEXT, file_path TEXT, count INTEGER
);
CREATE TABLE IF NOT EXISTS signal_oversized_agent (
  session_id TEXT, event_id TEXT, args_len INTEGER, result_size BIGINT
);
"""

def build_token_signals(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)
    for t in ("signal_bloat","signal_compaction_proximity",
              "signal_redundant_reads","signal_oversized_agent"):
        con.execute(f"DELETE FROM {t}")

    # 1. Bloat ratio: result_size vs next assistant output_tokens in same session
    con.execute("""
        INSERT INTO signal_bloat
        SELECT tc.session_id, tc.event_id, tc.tool_name, tc.result_size,
               COALESCE(nxt.output_tokens, 0) AS next_out,
               tc.result_size * 1.0 / GREATEST(COALESCE(nxt.output_tokens, 1), 1) AS ratio
        FROM tool_calls tc
        LEFT JOIN events nxt
          ON nxt.session_id = tc.session_id
         AND nxt.type='assistant'
         AND nxt.ts > tc.ts
        QUALIFY ROW_NUMBER() OVER (PARTITION BY tc.event_id ORDER BY nxt.ts) = 1
    """)

    # 2. Compaction proximity (tokens in last 5 turns before a compaction)
    con.execute("""
        INSERT INTO signal_compaction_proximity
        WITH preceding AS (
            SELECT e.session_id AS comp_sid,
                   e.ts AS comp_ts,
                   e2.output_tokens,
                   ROW_NUMBER() OVER (
                     PARTITION BY e.session_id, e.ts ORDER BY e2.ts DESC
                   ) AS rn
            FROM events e
            JOIN events e2
              ON e2.session_id = e.session_id AND e2.ts < e.ts
            WHERE e.subtype IN ('compact_boundary', 'compaction')
        )
        SELECT comp_sid, 5, COALESCE(SUM(output_tokens), 0)
        FROM preceding
        WHERE rn <= 5
        GROUP BY comp_sid, comp_ts
    """)

    # 3. Redundant reads
    rows = con.execute("""
        SELECT session_id, tool_name, args_json, position_in_session
        FROM tool_calls
        WHERE tool_name IN ('Read','Edit')
        ORDER BY session_id, position_in_session
    """).fetchall()
    last_read: dict = {}
    last_edit: dict = {}
    rr_counts: dict[tuple[str,str], int] = {}
    for sid, name, args, pos in rows:
        try:
            fp = json.loads(args).get("file_path")
        except Exception:
            fp = None
        if not fp:
            continue
        key = (sid, fp)
        if name == "Read":
            if key in last_read and last_edit.get(key, -1) < last_read[key]:
                rr_counts[key] = rr_counts.get(key, 1) + 1
            last_read[key] = pos
        elif name == "Edit":
            last_edit[key] = pos
    for (sid, fp), c in rr_counts.items():
        con.execute("INSERT INTO signal_redundant_reads VALUES (?,?,?)", [sid, fp, c])

    # 4. Oversized agent dispatches: short-ish args, much-bigger result.
    # Note: args_json is truncated to 2048 in parse.py, so the args_len signal
    # is a lower bound on the real dispatch prompt size.
    con.execute("""
        INSERT INTO signal_oversized_agent
        SELECT session_id, event_id, LENGTH(args_json) AS args_len, result_size
        FROM tool_calls
        WHERE tool_name='Agent'
          AND result_size > GREATEST(LENGTH(args_json) * 5, 5000)
    """)
