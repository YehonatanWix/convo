from __future__ import annotations
import json
import pathlib
import duckdb

PROMPT_TEMPLATE = """You are reading an aggregated dashboard of my Claude Code conversation history.
The numbers were precomputed by a deterministic pipeline (`convo-analyzer`) over every session
in `~/.claude/projects/`. Your job is to interpret — not to re-derive — the signals.

The dashboard is at: {dashboard_path}

For each section in the dashboard, surface:
- 3 concrete observations (what the data actually shows)
- at most 2 proposed actions (new skill, edited skill, deleted skill, or change to CLAUDE.md)

Be terse. Reference specific tool names, sequences, and session_ids from the data.
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


def write_analysis_inputs(db_path: pathlib.Path, out_dir: pathlib.Path) -> dict:
    """Build the dashboard and write dashboard.json + prompt.md.

    Returns paths to both files plus the prompt string for piping to `claude`.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = out_dir / "dashboard.json"
    prompt_path = out_dir / "analysis-prompt.md"

    dashboard = build_dashboard(db_path)
    dashboard_path.write_text(json.dumps(dashboard, indent=2))
    prompt = PROMPT_TEMPLATE.format(dashboard_path=dashboard_path.resolve())
    prompt_path.write_text(prompt)
    return {
        "dashboard_path": dashboard_path,
        "prompt_path": prompt_path,
        "prompt": prompt,
    }
