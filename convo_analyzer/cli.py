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
def session(session_id: str) -> None:
    con = duckdb.connect(str(_db_path()))
    for ts, etype, role, tool, tlen in con.execute(
        queries.SESSION_TIMELINE, [session_id]
    ).fetchall():
        typer.echo(f"{ts} {etype:<10} {role or '-':<10} {tool or '-':<20} len={tlen or 0}")

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

if __name__ == "__main__":
    app()
