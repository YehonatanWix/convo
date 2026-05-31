from __future__ import annotations
import os
import pathlib
import typer
import duckdb

from .ingest import ingest_all
from .load import open_db
from .derive.sequences import build_sequences
from .derive.tokens import build_token_signals
from .derive.skills import build_skill_signals
from .derive.recurring import build_recurring_signals
from . import queries

app = typer.Typer(help="Conversation analyzer.")


def _print_table(headers: list[str], rows: list[tuple]) -> None:
    if not rows:
        typer.echo("(no rows)")
        return
    widths = [len(h) for h in headers]
    str_rows = [[_fmt(c) for c in r] for r in rows]
    for r in str_rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    typer.echo(fmt.format(*headers))
    typer.echo("  ".join("-" * w for w in widths))
    for r in str_rows:
        typer.echo(fmt.format(*r))


def _fmt(c) -> str:
    if c is None:
        return "-"
    if isinstance(c, float):
        return f"{c:.2f}"
    return str(c)

def _root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CONVO_ROOT", "."))

def _db_path() -> pathlib.Path:
    return _root() / "corpus.db"

def _projects() -> pathlib.Path:
    return pathlib.Path(os.environ.get(
        "CONVO_PROJECTS", str(pathlib.Path.home() / ".claude/projects")
    ))

def _ingest_progress(evt: dict) -> None:
    e = evt["event"]
    if e == "start":
        typer.echo(f"scanning {evt['total']} sessions...", err=True)
    elif e == "load":
        typer.echo(
            f"  [{evt['i']}/{evt['total']}] load {evt['session_id'][:8]} "
            f"({evt['project']}, {evt['events']} events, {evt['tool_calls']} tools)",
            err=True,
        )
    elif e == "skip":
        typer.echo(f"  [{evt['i']}/{evt['total']}] skip {evt['session_id'][:8]} (unchanged)", err=True)
    elif e == "done":
        typer.echo(
            f"ingested {evt['ingested']} new, skipped {evt['skipped']} unchanged "
            f"(of {evt['total']} total)",
            err=True,
        )


def _derive_step(name: str, fn) -> None:
    typer.echo(f"deriving: {name}...", err=True)
    fn()


@app.command()
def ingest() -> None:
    """Parse, sanitize, and load all JSONL sessions, then derive all signals."""
    root = _root()
    ingest_all(
        projects_root=_projects(),
        db_path=_db_path(),
        blobs_path=root / "blobs",
        manifest_path=root / "manifest.json",
        on_progress=_ingest_progress,
    )
    con = open_db(_db_path())
    _derive_step("tool_sequences (n-grams)", lambda: build_sequences(con))
    _derive_step("token signals", lambda: build_token_signals(con))
    skills_yaml = pathlib.Path("skills.yaml")
    if skills_yaml.exists():
        _derive_step("skill signals", lambda: build_skill_signals(con, skills_yaml))
    else:
        typer.echo("skipping skill signals (no skills.yaml)", err=True)
    _derive_step("recurring signals", lambda: build_recurring_signals(con))
    typer.echo("done.", err=True)

@app.command("top-bloat")
def top_bloat(limit: int = 20) -> None:
    """Tool calls with biggest result-size vs next-turn output-token ratio."""
    con = duckdb.connect(str(_db_path()))
    rows = con.execute(queries.TOP_BLOAT, [limit]).fetchall()
    _print_table(
        ["session_id", "tool", "result_bytes", "next_out_tokens", "ratio"],
        rows,
    )

@app.command("recurring-sequences")
def recurring_sequences(min_sessions: int = 5, min_projects: int = 3, limit: int = 20) -> None:
    """Tool n-grams recurring across many sessions/projects with no skill match."""
    con = duckdb.connect(str(_db_path()))
    rows = con.execute(queries.RECURRING, [min_sessions, min_projects, limit]).fetchall()
    _print_table(["sequence", "n", "sessions", "projects"], rows)

@app.command("skill-health")
def skill_health(name: str) -> None:
    con = duckdb.connect(str(_db_path()))
    row = con.execute(queries.SKILL_HEALTH, [name, name, name]).fetchone()
    typer.echo(f"invocations={row[0]} abandoned={row[1]} avg_turnaround_tokens={row[2]}")

@app.command()
def session(
    session_id: str,
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Include housekeeping rows (permission-mode, snapshots, etc.)."
    ),
    tools_only: bool = typer.Option(
        False, "--tools-only", help="Show only tool-call rows."
    ),
) -> None:
    """Print a timeline of one session: flow, tokens, and pointers to full content."""
    con = duckdb.connect(str(_db_path()))
    rows = con.execute(queries.SESSION_TIMELINE, [session_id]).fetchall()

    table: list[tuple] = []
    for ts, etype, role, tool, out_tok, dur_ms, tlen, head, blob in rows:
        if tools_only and not tool:
            continue
        if not verbose and not tool and etype in queries.SESSION_TIMELINE_NOISE_TYPES:
            continue
        snippet = " ".join((head or "").split())
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."
        table.append((
            (ts or "")[11:19] or "-",                      # HH:MM:SS
            tool or etype,
            role or "-",
            out_tok or "-",
            dur_ms or "-",
            tlen or 0,
            (blob or "-")[:12],
            snippet or "-",
        ))

    if not table:
        typer.echo("(no rows)")
        return
    _print_table(
        ["time", "event", "role", "out_tok", "dur_ms", "len", "blob", "head"],
        table,
    )

@app.command()
def sql(query: str, limit: int = 200) -> None:
    """Run a read-only SQL query against corpus.db and print a table."""
    q = query.strip().rstrip(";")
    lowered = q.lower()
    forbidden = ("insert ", "update ", "delete ", "drop ", "alter ", "create ", "attach ")
    if any(tok in lowered for tok in forbidden):
        typer.echo("error: only read-only queries are allowed", err=True)
        raise typer.Exit(2)
    if "limit" not in lowered:
        q = f"{q} LIMIT {limit}"
    con = duckdb.connect(str(_db_path()), read_only=True)
    cur = con.execute(q)
    headers = [d[0] for d in cur.description]
    _print_table(headers, cur.fetchall())


@app.command()
def schema() -> None:
    """List tables and their columns."""
    con = duckdb.connect(str(_db_path()), read_only=True)
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' ORDER BY table_name"
    ).fetchall()]
    for t in tables:
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name=? ORDER BY ordinal_position", [t],
        ).fetchall()
        typer.echo(f"\n{t}")
        for name, dtype in cols:
            typer.echo(f"  {name:<32} {dtype}")


@app.command()
def blob(blob_hash: str) -> None:
    """Print the contents of a blob by hash."""
    from .blobs import BlobStore
    store = BlobStore(_root() / "blobs")
    typer.echo(store.get(blob_hash))


@app.command("investigate-skill")
def investigate_skill(
    skill_name: str,
    window: int = typer.Option(5, "--window", help="User messages to capture after each invocation."),
    batch_size: int = typer.Option(5, "--batch-size", help="Candidates per triage subagent batch."),
    print_only: bool = typer.Option(False, "--print", help="Don't launch claude, just print the command."),
) -> None:
    """Find failure modes for a skill: launch Claude Code to analyze every invocation."""
    from .investigate import write_investigation
    out = write_investigation(
        db_path=_db_path(),
        out_dir=_root() / "analysis",
        skill=skill_name,
        projects_root=_projects(),
        window=window,
        batch_size=batch_size,
    )
    typer.echo(f"wrote {out['packet_path']} ({out['n_candidates']} invocations)", err=True)
    typer.echo(f"wrote {out['prompt_path']}", err=True)
    if out["n_candidates"] == 0:
        typer.echo(f"no invocations of '{skill_name}' found in corpus", err=True)
        raise typer.Exit(1)
    if print_only:
        typer.echo("\nrun:  claude \"$(cat " + str(out["prompt_path"]) + ")\"")
        return
    os.execvp("claude", ["claude", out["prompt"]])


@app.command()
def agents() -> None:
    """Print a guide that teaches a fresh agent how to use this tool."""
    typer.echo(AGENT_GUIDE)


@app.command()
def interpret(
    print_only: bool = typer.Option(
        False, "--print", help="Print the command instead of exec'ing claude."
    ),
) -> None:
    """Build the dashboard and launch a Claude Code session to analyze it."""
    from .llm import write_analysis_inputs
    out = write_analysis_inputs(_db_path(), _root() / "analysis")
    typer.echo(f"wrote {out['dashboard_path']}")
    typer.echo(f"wrote {out['prompt_path']}")
    if print_only:
        typer.echo("\nrun:  claude \"$(cat " + str(out["prompt_path"]) + ")\"")
        return
    os.execvp("claude", ["claude", out["prompt"]])

AGENT_GUIDE = """\
# convo: agent guide

You are investigating a corpus of Claude Code conversations. The `convo` CLI
exposes a DuckDB warehouse (`corpus.db`) of parsed sessions, plus blobs for
large message/tool payloads. Use it to find patterns, failure modes, and
opportunities for improving skills, prompts, or tooling.

## First run

Before anything else, check whether `corpus.db` exists in `CONVO_ROOT` (default
`.`). If it's missing — or you suspect it's stale — run `convo ingest`. This
parses every JSONL session under `CONVO_PROJECTS` (default
`~/.claude/projects`), loads it into DuckDB, and builds all derived signals
(`signal_*` views, tool sequences, skill signals). Ingest is incremental:
unchanged sessions are skipped, so it's safe to re-run.

## Workflow

1. `convo schema` — list tables and columns. Always run this first (after ingest).
2. `convo sql "SELECT ..."` — read-only SQL against `corpus.db`. Auto-appends
   LIMIT if missing. Use it to slice the data however you need.
3. `convo session <session_id>` — print a timeline of one session.
4. `convo blob <hash>` — read a full message/tool payload by hash. Event and
   tool_call rows reference blobs via `blob_hash`, `args_blob_hash`,
   `result_blob_hash`. Heads are stored inline (`text_head`); use blobs when
   you need the full content.
5. `convo top-bloat`, `convo recurring-sequences`, `convo skill-health <name>`
   — prebuilt analyses for common questions.
6. `convo investigate-skill <name>` — deeper failure-mode hunt for one skill.

## Key tables

- `sessions` — one row per session: project, timing, token totals, model,
  compaction_count, ai_title.
- `events` — every message/turn. Important columns: `ts`, `type`, `role`,
  `input_tokens`, `output_tokens`, `text_len`, `text_head`, `blob_hash`.
- `tool_calls` — every tool invocation, with `tool_name`, `args_json`,
  `result_size`, `result_blob_hash`, `success`, `duration_ms`,
  `position_in_session`. Join via `(event_id, session_id)`.
- `skill_invocations` — when a skill was invoked, plus `followed_by_tools`
  (JSON array) showing what the agent did next.
- `tool_sequences` — n-grams of consecutive tool calls per session.
- `signal_*` views — derived signals: `signal_bloat`, `signal_recurring_sequences`,
  `signal_skill_abandoned`, `signal_skill_turnaround`, plus token signals.

## Investigation tips

- Start broad: `convo sql "SELECT project, COUNT(*) FROM sessions GROUP BY 1"`.
- Sample before reading blobs — blobs are large. Filter to interesting
  sessions first, then drill in.
- Tool args/results above a size threshold live in blobs; small content is
  inline in `text_head` / `args_json`.
- To trace a single conversation: `convo session <id>` for the timeline, then
  `convo blob <hash>` for any payload you want in full.
- For skill questions, `skill_invocations.followed_by_tools` tells you what
  Claude actually did after the skill triggered — useful for spotting
  abandoned or misused skills.
- Cross-session patterns: `tool_sequences` and `signal_recurring_sequences`.
- Token waste: `signal_bloat` (big tool results that didn't drive much output).

## Environment

- `CONVO_ROOT` — defaults to `.`; holds `corpus.db`, `blobs/`, `manifest.json`,
  `analysis/`.
- `CONVO_PROJECTS` — JSONL source dir (default `~/.claude/projects`).
- SQL is read-only; writes are refused.

Begin by running `convo schema`, then form a hypothesis and query for it.
"""

if __name__ == "__main__":
    app()
