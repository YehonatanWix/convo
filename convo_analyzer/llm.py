from __future__ import annotations
import json
import pathlib
import duckdb

PROMPT_TEMPLATE = """You are analyzing my Claude Code conversation history. A deterministic
pipeline (`convo-analyzer`) already parsed every session in `~/.claude/projects/`,
sanitized it, loaded it into DuckDB, and precomputed signal tables. Your job is to
interpret the signals AND drill into the raw data to back claims with evidence.

## Starting point

The summary dashboard is at:
  {dashboard_path}

Read it first to see the top-20 of each precomputed signal.

## Tools available to you (all via Bash)

You can run any of these from `{cwd}`:

  convo schema                       # list all tables and columns in corpus.db
  convo sql "<SELECT ...>"           # run a read-only SQL query, get a formatted table
                                     # (auto-appends LIMIT 200 if missing)
  convo session <session_id>         # print a single session's timeline
  convo blob <hash>                  # print a stored blob (full tool result, etc.)
  convo top-bloat --limit N
  convo recurring-sequences --min-sessions N --min-projects N --limit N
  convo skill-health <skill_name>

Tables you can query with `convo sql`:
  sessions, events, tool_calls, skill_invocations, tool_sequences,
  signal_bloat, signal_compaction_proximity, signal_redundant_reads,
  signal_oversized_agent, signal_skill_eligible_missed, signal_skill_abandoned,
  signal_skill_turnaround, signal_recurring_sequences, signal_correction_clusters,
  signal_repeated_path_fixes

## Raw conversation data

- Raw JSONL sessions: `~/.claude/projects/<encoded-cwd>/<session_id>.jsonl`
  (use Read / Grep / Bash to inspect them directly when a signal needs context)
- Sanitized blobs (large tool results, big prompts): `{blobs_dir}/<aa>/<hash>.txt`
  — `tool_calls.result_blob_hash` and `tool_calls.args_blob_hash` point here.

## Your task

For each major axis below, surface concrete findings backed by data you actually
looked up (not from the dashboard alone):

1. **Token efficiency** — where am I wasting tokens? Drill into top bloat rows,
   redundant reads, compaction-adjacent turns. Look at the actual blobs to judge
   whether the result was *useful*.
2. **Skill health** — which skills fire wrongly or get abandoned? Pull the
   surrounding events for `signal_skill_abandoned` to see what the user said next.
3. **Repeated actions → skill candidates** — top recurring tool n-grams across
   sessions/projects with no skill match. Read a few sessions that exhibit each
   pattern; propose: is this skill-worthy? proposed name? trigger pattern?

For each finding give:
- The signal/query you used (so I can reproduce)
- 1-2 concrete examples (session_id + brief quote/snippet from the raw data)
- At most 2 proposed actions (new skill, edited skill, CLAUDE.md change)

Be terse. Quote evidence. Don't speculate beyond what the data shows.
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
    prompt = PROMPT_TEMPLATE.format(
        dashboard_path=dashboard_path.resolve(),
        blobs_dir=(pathlib.Path(db_path).parent / "blobs").resolve(),
        cwd=pathlib.Path.cwd(),
    )
    prompt_path.write_text(prompt)
    return {
        "dashboard_path": dashboard_path,
        "prompt_path": prompt_path,
        "prompt": prompt,
    }
