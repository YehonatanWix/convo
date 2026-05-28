# convo_analyzer/llm.py
from __future__ import annotations
import json
import pathlib
import duckdb

PROMPT = """You are reading an aggregated dashboard of a user's Claude Code conversation history.

For each section, surface 3 concrete observations and at most 2 proposed actions (new skill,
edited skill, deleted skill, or change to CLAUDE.md). Be terse.

SECTIONS:
{dashboard}
"""

DASHBOARD_QUERIES = {
    "top_bloat": "SELECT * FROM signal_bloat ORDER BY ratio DESC LIMIT 20",
    "top_recurring": "SELECT * FROM signal_recurring_sequences ORDER BY sessions DESC LIMIT 20",
    "skill_eligible_missed": "SELECT * FROM signal_skill_eligible_missed LIMIT 20",
    "skill_abandoned": "SELECT * FROM signal_skill_abandoned LIMIT 20",
    "redundant_reads": "SELECT * FROM signal_redundant_reads ORDER BY count DESC LIMIT 20",
}

def build_dashboard(db_path: pathlib.Path) -> dict:
    con = duckdb.connect(str(db_path))
    out: dict = {}
    for name, sql in DASHBOARD_QUERIES.items():
        try:
            out[name] = [list(r) for r in con.execute(sql).fetchall()]
        except duckdb.Error:
            out[name] = []
    return out

def interpret(
    db_path: pathlib.Path,
    client=None,
    model: str = "claude-opus-4-7",
) -> str:
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    dashboard = build_dashboard(db_path)
    prompt = PROMPT.format(dashboard=json.dumps(dashboard, indent=2)[:40000])
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text
