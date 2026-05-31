# Subagent Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `convo` recognize subagent conversations (e.g. those spawned by `/code-review`) as children of their parent session so analyses like "how many LSP calls did this conversation make" include subagent activity.

**Architecture:** Subagent JSONLs live at `<projects-root>/<project-dir>/<parent-session-id>/subagents/agent-*.jsonl`. They are already discovered by `rglob("*.jsonl")` but ingested as orphan sessions under a fake project named `"subagents"`. We add path-aware parsing in `_project_slug` that detects this layout, inherits the parent's project, and stores `parent_session_id` + `is_subagent` on the `sessions` row. CLI surfaces (`convo session`, `convo agents` guide) and a new `convo subagents` command read those columns.

**Tech Stack:** Python 3.11+, DuckDB, Typer, Pydantic, pytest.

---

## File Structure

- `convo_analyzer/parse.py` — path detection + new fields on `ParsedSession` / `SessionRow`.
- `convo_analyzer/models.py` — add `parent_session_id` and `is_subagent` to `SessionRow`.
- `convo_analyzer/schema.sql` — add two columns to `sessions` via `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.
- `convo_analyzer/load.py` — extend the `INSERT INTO sessions` column list.
- `convo_analyzer/queries.py` — add `SESSION_TIMELINE_WITH_SUBAGENTS` query and `SUBAGENTS_FOR_PARENT` query.
- `convo_analyzer/cli.py` — `--with-subagents` flag on `session`; new `subagents` command; update `agents` guide text.
- `tests/fixtures/subagent_session/` — minimal nested fixture: one parent JSONL and one `subagents/agent-*.jsonl` child.
- `tests/test_parse_subagent.py` — parsing-level assertions.
- `tests/test_load_subagent.py` — DB-level assertions on `parent_session_id` / `is_subagent`.
- `tests/test_cli_subagent.py` — CLI surface assertions.

The change is additive — no existing column or row is reshaped, so the existing manifest stays valid and re-ingest is incremental.

---

### Task 1: Add `parent_session_id` + `is_subagent` to the `SessionRow` model

**Files:**
- Modify: `convo_analyzer/models.py:55-71`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
from convo_analyzer.models import SessionRow

def test_session_row_subagent_fields_default():
    s = SessionRow(session_id="x", project="p", cwd="/p")
    assert s.parent_session_id is None
    assert s.is_subagent is False

def test_session_row_subagent_fields_set():
    s = SessionRow(
        session_id="agent-1", project="p", cwd="/p",
        parent_session_id="parent-uuid", is_subagent=True,
    )
    assert s.parent_session_id == "parent-uuid"
    assert s.is_subagent is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `pydantic.ValidationError: Extra inputs are not permitted` (or `AttributeError`) on the new fields.

- [ ] **Step 3: Add the fields to `SessionRow`**

In `convo_analyzer/models.py`, edit the `SessionRow` class (currently ends at the `ai_title` line). Add two new fields immediately after `ai_title`:

```python
class SessionRow(BaseModel):
    session_id: str
    project: str
    cwd: str
    git_branch: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_ms: Optional[int] = None
    message_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    compaction_count: int = 0
    model: Optional[str] = None
    ai_title: Optional[str] = None
    parent_session_id: Optional[str] = None
    is_subagent: bool = False
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS, all tests green.

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/models.py tests/test_models.py
git commit -m "feat(models): add parent_session_id + is_subagent to SessionRow"
```

---

### Task 2: Extend the `sessions` table schema

**Files:**
- Modify: `convo_analyzer/schema.sql:1-19`
- Test: `tests/test_load.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_load.py`:

```python
import duckdb
from convo_analyzer.load import open_db

def test_sessions_table_has_subagent_columns(tmp_path):
    db = tmp_path / "corpus.db"
    con = open_db(db)
    cols = {r[0] for r in con.execute("PRAGMA table_info('sessions')").fetchall()}
    assert "parent_session_id" in cols
    assert "is_subagent" in cols
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_load.py::test_sessions_table_has_subagent_columns -v`
Expected: FAIL — `AssertionError` because columns are missing.

- [ ] **Step 3: Add the columns via `ALTER TABLE`**

In `convo_analyzer/schema.sql`, after the `CREATE TABLE IF NOT EXISTS sessions (...)` block (around line 19, before the `CREATE TABLE IF NOT EXISTS events` block), add:

```sql
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS parent_session_id TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_subagent BOOLEAN DEFAULT FALSE;
```

This pattern matches the existing `ALTER TABLE events ADD COLUMN IF NOT EXISTS text_head TEXT;` migration style in the same file, keeping existing corpus.db files compatible without a manual rebuild.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_load.py -v`
Expected: PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/schema.sql tests/test_load.py
git commit -m "feat(schema): add parent_session_id + is_subagent columns to sessions"
```

---

### Task 3: Detect subagent paths in `_project_slug`

**Files:**
- Modify: `convo_analyzer/parse.py:21-26`
- Test: `tests/test_parse_subagent.py` (new)

Subagent files live at `<projects-root>/<project-dir>/<parent-session-id>/subagents/agent-*.jsonl`. The parent directory chain is `subagents` then `<parent-session-id>` (a UUID) then `<project-dir>` (a slug like `-Users-yehonatana-Workspace-Wix-responsive-editor-packages`). For regular sessions, the parent directory chain is just `<project-dir>`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_subagent.py`:

```python
import pathlib
from convo_analyzer.parse import _project_slug, _subagent_parent_id

PROJECT_DIR = "-Users-yehonatana-Workspace-Wix-responsive-editor-packages"
PARENT_ID = "29dd51ef-83f4-4dc3-b87f-5cc005f5a1d7"

def test_regular_session_project_slug(tmp_path):
    proj = tmp_path / PROJECT_DIR
    proj.mkdir()
    f = proj / "abc.jsonl"
    f.write_text("")
    slug, cwd = _project_slug(f)
    assert slug == "responsive-editor-packages"
    assert "Workspace/Wix" in cwd

def test_subagent_session_project_slug_inherits_parent(tmp_path):
    proj = tmp_path / PROJECT_DIR / PARENT_ID / "subagents"
    proj.mkdir(parents=True)
    f = proj / "agent-a478ac3907e21b2ff.jsonl"
    f.write_text("")
    slug, cwd = _project_slug(f)
    assert slug == "responsive-editor-packages"
    assert "Workspace/Wix" in cwd

def test_subagent_parent_id_detection(tmp_path):
    proj = tmp_path / PROJECT_DIR / PARENT_ID / "subagents"
    proj.mkdir(parents=True)
    f = proj / "agent-a478ac3907e21b2ff.jsonl"
    f.write_text("")
    assert _subagent_parent_id(f) == PARENT_ID

def test_subagent_parent_id_none_for_regular(tmp_path):
    proj = tmp_path / PROJECT_DIR
    proj.mkdir()
    f = proj / "abc.jsonl"
    f.write_text("")
    assert _subagent_parent_id(f) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_parse_subagent.py -v`
Expected: FAIL — `ImportError: cannot import name '_subagent_parent_id'` and the subagent slug assertion will fail because the current code returns `"subagents"`.

- [ ] **Step 3: Implement detection in `parse.py`**

Replace the existing `_project_slug` function in `convo_analyzer/parse.py:21-26` and add a new `_subagent_parent_id` helper. The full replacement (lines 21–26):

```python
SUBAGENT_DIRNAME = "subagents"
# A parent-session UUID directory looks like an 8-4-4-4-12 hex UUID.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

def _is_subagent_path(jsonl_path: pathlib.Path) -> bool:
    parents = jsonl_path.parents
    # parents[0] = subagents/, parents[1] = <parent-session-id>/, parents[2] = <project-dir>/
    if len(parents) < 3:
        return False
    return (
        parents[0].name == SUBAGENT_DIRNAME
        and bool(_UUID_RE.match(parents[1].name))
    )

def _subagent_parent_id(jsonl_path: pathlib.Path) -> Optional[str]:
    if _is_subagent_path(jsonl_path):
        return jsonl_path.parents[1].name
    return None

def _project_slug(jsonl_path: pathlib.Path) -> tuple[str, str]:
    # For subagent files (nested under <project-dir>/<parent-id>/subagents/),
    # walk up two extra levels so we inherit the real project directory name.
    project_dir = jsonl_path.parents[2] if _is_subagent_path(jsonl_path) else jsonl_path.parent
    parent = project_dir.name
    cwd = "/" + parent.lstrip("-").replace("-", "/")
    cwd = cwd.replace("/Users/yehonatana", "~", 1)
    slug = cwd.rstrip("/").split("/")[-1] or "root"
    return slug, cwd
```

Also add `from typing import Optional` to the imports at the top of `parse.py` if not already present (the file already imports from `typing`, so just extend the import line — confirm before editing).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_parse_subagent.py tests/test_parse.py -v`
Expected: PASS for all four new tests AND existing parse tests still pass (no regression on regular sessions).

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/parse.py tests/test_parse_subagent.py
git commit -m "feat(parse): detect subagent JSONL paths and inherit parent project"
```

---

### Task 4: Populate `parent_session_id` + `is_subagent` during parse

**Files:**
- Modify: `convo_analyzer/parse.py:47-49,156-172`
- Test: `tests/test_parse_subagent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parse_subagent.py`:

```python
from convo_analyzer.parse import parse_session_file

PARENT_ID = "29dd51ef-83f4-4dc3-b87f-5cc005f5a1d7"
PROJECT_DIR = "-Users-yehonatana-Workspace-Wix-responsive-editor-packages"

def _minimal_jsonl(path: pathlib.Path) -> None:
    # One valid event line so parse_session_file produces a SessionRow.
    path.write_text(
        '{"type":"user","timestamp":"2026-05-29T16:00:00Z","uuid":"e1",'
        '"message":{"role":"user","content":"hi"}}\n'
    )

def test_parse_marks_subagent(tmp_path):
    p = tmp_path / PROJECT_DIR / PARENT_ID / "subagents"
    p.mkdir(parents=True)
    f = p / "agent-a478ac3907e21b2ff.jsonl"
    _minimal_jsonl(f)
    parsed = parse_session_file(f)
    assert parsed.session.is_subagent is True
    assert parsed.session.parent_session_id == PARENT_ID
    assert parsed.session.project == "responsive-editor-packages"

def test_parse_marks_regular_session(tmp_path):
    p = tmp_path / PROJECT_DIR
    p.mkdir()
    f = p / "abc12345-0000-0000-0000-000000000000.jsonl"
    _minimal_jsonl(f)
    parsed = parse_session_file(f)
    assert parsed.session.is_subagent is False
    assert parsed.session.parent_session_id is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_parse_subagent.py -v`
Expected: FAIL — `AttributeError` on `is_subagent` is gone (model has field from Task 1), but `parsed.session.is_subagent` is `False` for the subagent case because parse doesn't set it yet.

- [ ] **Step 3: Set the fields in `parse_session_file`**

In `convo_analyzer/parse.py`, in `parse_session_file`, right after the line `project, cwd = _project_slug(path)` (line 49), add:

```python
    parent_session_id = _subagent_parent_id(path)
    is_subagent = parent_session_id is not None
```

Then in the `SessionRow(...)` constructor near the bottom of the function (currently lines 156–172), add the two new fields after `ai_title=ai_title`:

```python
        parent_session_id=parent_session_id,
        is_subagent=is_subagent,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_parse_subagent.py tests/test_parse.py -v`
Expected: PASS for all subagent tests AND existing parse tests still green.

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/parse.py tests/test_parse_subagent.py
git commit -m "feat(parse): populate parent_session_id and is_subagent on SessionRow"
```

---

### Task 5: Persist new fields in `load_session`

**Files:**
- Modify: `convo_analyzer/load.py:29-37`
- Test: `tests/test_load_subagent.py` (new)

The current `INSERT INTO sessions` uses positional placeholders (`VALUES (?,?,?,…CURRENT_TIMESTAMP)`). We add two more placeholders for the new columns. The `ALTER TABLE … ADD COLUMN` from Task 2 appends columns to the end of the table, so the order is: existing 15 columns, `parent_session_id`, `is_subagent`, `ingested_at`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_load_subagent.py`:

```python
import duckdb
from convo_analyzer.load import open_db, load_session
from convo_analyzer.models import SessionRow
from convo_analyzer.parse import ParsedSession

def _row(session_id: str, parent_session_id=None, is_subagent=False) -> ParsedSession:
    return ParsedSession(
        session_id=session_id,
        session=SessionRow(
            session_id=session_id,
            project="responsive-editor-packages",
            cwd="~/Workspace/Wix/responsive-editor-packages",
            parent_session_id=parent_session_id,
            is_subagent=is_subagent,
        ),
        events=[], tool_calls=[], skills=[],
    )

def test_load_persists_subagent_fields(tmp_path):
    con = open_db(tmp_path / "corpus.db")
    load_session(con, _row("parent-1"))
    load_session(con, _row("agent-1", parent_session_id="parent-1", is_subagent=True))

    rows = con.execute(
        "SELECT session_id, parent_session_id, is_subagent FROM sessions ORDER BY session_id"
    ).fetchall()
    assert rows == [
        ("agent-1", "parent-1", True),
        ("parent-1", None, False),
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_load_subagent.py -v`
Expected: FAIL — `duckdb.BinderException` ("table sessions has 18 columns but 16 values were supplied") or the values land in the wrong columns.

- [ ] **Step 3: Update the `INSERT` to include the new columns**

In `convo_analyzer/load.py`, replace the `con.execute("""INSERT INTO sessions VALUES …""")` block (lines 30–37) with a column-named insert that's explicit about ordering (more robust than positional):

```python
    con.execute(
        """INSERT INTO sessions (
            session_id, project, cwd, git_branch,
            started_at, ended_at, duration_ms, message_count,
            total_input_tokens, total_output_tokens,
            total_cache_read_tokens, total_cache_creation_tokens,
            compaction_count, model, ai_title,
            parent_session_id, is_subagent, ingested_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        [s.session_id, s.project, normalize_path(s.cwd), s.git_branch,
         s.started_at, s.ended_at, s.duration_ms, s.message_count,
         s.total_input_tokens, s.total_output_tokens,
         s.total_cache_read_tokens, s.total_cache_creation_tokens,
         s.compaction_count, s.model, s.ai_title,
         s.parent_session_id, s.is_subagent],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_load_subagent.py tests/test_load.py tests/test_ingest.py -v`
Expected: PASS, including the new subagent test and existing load/ingest tests.

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/load.py tests/test_load_subagent.py
git commit -m "feat(load): persist parent_session_id + is_subagent into sessions table"
```

---

### Task 6: Add `convo subagents <parent_session_id>` command

**Files:**
- Modify: `convo_analyzer/queries.py` (append)
- Modify: `convo_analyzer/cli.py` (append a new `@app.command()`)
- Test: `tests/test_cli_subagent.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_subagent.py`:

```python
from typer.testing import CliRunner
from convo_analyzer.cli import app
from convo_analyzer.load import open_db, load_session
from convo_analyzer.models import SessionRow
from convo_analyzer.parse import ParsedSession
from convo_analyzer.models import ToolCall

runner = CliRunner()

def _seed(db_path):
    con = open_db(db_path)
    parent = ParsedSession(
        session_id="parent-1",
        session=SessionRow(session_id="parent-1", project="p", cwd="/p"),
        events=[], tool_calls=[], skills=[],
    )
    child = ParsedSession(
        session_id="agent-1",
        session=SessionRow(
            session_id="agent-1", project="p", cwd="/p",
            parent_session_id="parent-1", is_subagent=True,
        ),
        events=[],
        tool_calls=[
            ToolCall(event_id="e1", session_id="agent-1", ts="2026-05-29T16:00:00Z",
                     tool_name="LSP", args_json="{}", position_in_session=0),
            ToolCall(event_id="e2", session_id="agent-1", ts="2026-05-29T16:00:01Z",
                     tool_name="Bash", args_json="{}", position_in_session=1),
        ],
        skills=[],
    )
    load_session(con, parent)
    load_session(con, child)
    con.close()

def test_subagents_lists_children(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    _seed(tmp_path / "corpus.db")
    result = runner.invoke(app, ["subagents", "parent-1"])
    assert result.exit_code == 0
    assert "agent-1" in result.stdout
    # Tool count column present
    assert "2" in result.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cli_subagent.py -v`
Expected: FAIL — `No such command 'subagents'`.

- [ ] **Step 3: Add the query**

Append to `convo_analyzer/queries.py`:

```python
SUBAGENTS_FOR_PARENT = """
SELECT
    s.session_id,
    s.started_at,
    s.ended_at,
    s.ai_title,
    (SELECT COUNT(*) FROM tool_calls tc WHERE tc.session_id = s.session_id) AS tool_calls
FROM sessions s
WHERE s.parent_session_id = ?
ORDER BY s.started_at
"""
```

- [ ] **Step 4: Add the CLI command**

Append to `convo_analyzer/cli.py` (after the existing `session` command, before the `sql` command — match the style of other `@app.command()` blocks already in the file):

```python
@app.command()
def subagents(parent_session_id: str) -> None:
    """List subagent sessions spawned by a parent session."""
    con = duckdb.connect(str(_db_path()))
    rows = con.execute(queries.SUBAGENTS_FOR_PARENT, [parent_session_id]).fetchall()
    if not rows:
        typer.echo("(no subagents)")
        return
    _print_table(
        ["session_id", "started_at", "ended_at", "ai_title", "tool_calls"],
        rows,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_cli_subagent.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add convo_analyzer/queries.py convo_analyzer/cli.py tests/test_cli_subagent.py
git commit -m "feat(cli): add 'convo subagents <parent_id>' command"
```

---

### Task 7: Add `--with-subagents` to `convo session`

**Files:**
- Modify: `convo_analyzer/queries.py` (append `SESSION_TIMELINE_WITH_SUBAGENTS`)
- Modify: `convo_analyzer/cli.py:122-162` (the existing `session` command)
- Test: `tests/test_cli_subagent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_subagent.py`:

```python
def test_session_with_subagents_includes_child_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    _seed(tmp_path / "corpus.db")
    result = runner.invoke(app, ["session", "parent-1", "--with-subagents"])
    assert result.exit_code == 0
    # The child's LSP tool call should appear when --with-subagents is set.
    assert "LSP" in result.stdout

def test_session_without_subagents_hides_child_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    _seed(tmp_path / "corpus.db")
    result = runner.invoke(app, ["session", "parent-1"])
    assert result.exit_code == 0
    assert "LSP" not in result.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli_subagent.py -v`
Expected: FAIL — `No such option: --with-subagents` for the first test; the second test may already pass.

- [ ] **Step 3: Add the rollup query**

Append to `convo_analyzer/queries.py`:

```python
SESSION_TIMELINE_WITH_SUBAGENTS = """
SELECT
    e.ts,
    e.type,
    e.role,
    tc.tool_name,
    e.output_tokens,
    e.duration_ms,
    e.text_len,
    e.text_head,
    COALESCE(e.blob_hash, tc.result_blob_hash, tc.args_blob_hash) AS blob_hash,
    e.session_id
FROM events e
LEFT JOIN tool_calls tc USING (event_id, session_id)
WHERE e.session_id = ?
   OR e.session_id IN (SELECT session_id FROM sessions WHERE parent_session_id = ?)
ORDER BY e.ts
"""
```

- [ ] **Step 4: Wire the flag into the `session` command**

In `convo_analyzer/cli.py`, modify the `session` function signature (around line 123) to add a third option:

```python
@app.command()
def session(
    session_id: str,
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Include housekeeping rows (permission-mode, snapshots, etc.)."
    ),
    tools_only: bool = typer.Option(
        False, "--tools-only", help="Show only tool-call rows."
    ),
    with_subagents: bool = typer.Option(
        False, "--with-subagents", help="Include events from subagent sessions spawned by this session."
    ),
) -> None:
    """Print a timeline of one session: flow, tokens, and pointers to full content."""
    con = duckdb.connect(str(_db_path()))
    if with_subagents:
        rows = con.execute(
            queries.SESSION_TIMELINE_WITH_SUBAGENTS, [session_id, session_id]
        ).fetchall()
    else:
        rows = con.execute(queries.SESSION_TIMELINE, [session_id]).fetchall()
```

The existing row-unpacking loop (`for ts, etype, role, tool, ...`) needs to handle one extra trailing column when `with_subagents` is set. Replace the unpacking line:

```python
    for row in rows:
        if with_subagents:
            ts, etype, role, tool, out_tok, dur_ms, tlen, head, blob, src_session = row
        else:
            ts, etype, role, tool, out_tok, dur_ms, tlen, head, blob = row
            src_session = session_id
```

And extend the `table.append(...)` tuple + headers to include a `from` column when `with_subagents` is set. Replace the `_print_table(...)` call at the end of `session` with:

```python
    if with_subagents:
        # Re-attach the src_session marker; rebuild table rows.
        # (handled inline in the loop above by appending src_session into the tuple)
        _print_table(
            ["time", "event", "role", "out_tok", "dur_ms", "len", "blob", "from", "head"],
            table,
        )
    else:
        _print_table(
            ["time", "event", "role", "out_tok", "dur_ms", "len", "blob", "head"],
            table,
        )
```

And inside the loop, when `with_subagents` is True, append `src_session[:8]` between `blob` and `snippet`:

```python
        cols = [
            (ts or "")[11:19] or "-",
            tool or etype,
            role or "-",
            out_tok or "-",
            dur_ms or "-",
            tlen or 0,
            (blob or "-")[:12],
        ]
        if with_subagents:
            cols.append(src_session[:8] if src_session else "-")
        cols.append(snippet or "-")
        table.append(tuple(cols))
```

Remove the old single `table.append((...))` block — it's now replaced by the `cols` construction above.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_cli_subagent.py tests/test_cli.py -v`
Expected: PASS for the two new tests AND existing CLI tests stay green.

- [ ] **Step 6: Commit**

```bash
git add convo_analyzer/queries.py convo_analyzer/cli.py tests/test_cli_subagent.py
git commit -m "feat(cli): support --with-subagents on 'convo session'"
```

---

### Task 8: Update the `convo agents` guide text

**Files:**
- Modify: `convo_analyzer/cli.py:261-318` (the `agents` command's docstring/text)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
from typer.testing import CliRunner
from convo_analyzer.cli import app

runner = CliRunner()

def test_agents_guide_mentions_subagents():
    result = runner.invoke(app, ["agents"])
    assert result.exit_code == 0
    assert "parent_session_id" in result.stdout
    assert "subagents" in result.stdout.lower()
    assert "--with-subagents" in result.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — the guide does not yet mention the subagent concepts.

- [ ] **Step 3: Extend the guide text**

In `convo_analyzer/cli.py`, in the `agents` command's emitted text (the multi-line string starting around line 261 and continuing through ~line 318), add a new section after the `## Key tables` block. Insert this block immediately before the `## Investigation tips` heading:

```text
## Subagents

Slash commands like `/code-review` dispatch a subagent — a separate Claude
conversation that runs the actual work and pipes its final output back into
the parent session as `<local-command-stdout>`. Their JSONLs live at
`<projects-root>/<project-dir>/<parent-session-id>/subagents/agent-*.jsonl`
and are ingested as their own `sessions` rows with:

- `is_subagent = TRUE`
- `parent_session_id = <parent session_id>`
- `project` inherited from the parent (not `"subagents"`)

If you query a parent session's tool calls directly, you will NOT see
subagent activity. To roll subagent activity up:

- `convo subagents <parent_session_id>` — list child subagents.
- `convo session <parent_session_id> --with-subagents` — timeline that
  includes events from all children.
- In SQL, join via `parent_session_id`:

  ```sql
  SELECT tool_name, COUNT(*)
  FROM tool_calls tc
  JOIN sessions s USING (session_id)
  WHERE s.session_id = :id OR s.parent_session_id = :id
  GROUP BY 1 ORDER BY 2 DESC;
  ```
```

Also update the `## Key tables` section's `sessions` bullet to mention the new columns. Replace the existing bullet:

```text
- `sessions` — one row per session: project, timing, token totals, model,
  compaction_count, ai_title.
```

with:

```text
- `sessions` — one row per session: project, timing, token totals, model,
  compaction_count, ai_title, plus `is_subagent` and `parent_session_id`
  (NULL for top-level sessions; set for subagent conversations).
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/cli.py tests/test_cli.py
git commit -m "docs(cli): teach the agents guide about subagent sessions"
```

---

### Task 9: End-to-end ingest test with a nested subagent fixture

**Files:**
- Create: `tests/fixtures/nested_project/-Users-test-project/parent-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl`
- Create: `tests/fixtures/nested_project/-Users-test-project/parent-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/subagents/agent-1234567890abcdef.jsonl`
- Modify: `tests/test_ingest.py`

- [ ] **Step 1: Create the fixture files**

Create `tests/fixtures/nested_project/-Users-test-project/parent-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl` with:

```jsonl
{"type":"user","timestamp":"2026-05-29T16:00:00Z","uuid":"p1","message":{"role":"user","content":"/code-review http://example/pr/1"}}
{"type":"user","timestamp":"2026-05-29T16:02:00Z","uuid":"p2","message":{"role":"user","content":"<local-command-stdout>review result</local-command-stdout>"}}
```

Note: the parent session's filename stem (the UUID portion) must match the directory name of its subagents folder. Here both are `parent-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee` — except: the directory used to detect subagents must match the UUID regex `^[0-9a-f]{8}-...`. Adjust the path to use a plain UUID like `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee` (drop the `parent-` prefix from both the JSONL stem and the dirname so they stay consistent and pass the regex).

Final paths:
- `tests/fixtures/nested_project/-Users-test-project/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl`
- `tests/fixtures/nested_project/-Users-test-project/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/subagents/agent-1234567890abcdef.jsonl`

Create `tests/fixtures/nested_project/-Users-test-project/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/subagents/agent-1234567890abcdef.jsonl` with:

```jsonl
{"type":"assistant","timestamp":"2026-05-29T16:01:00Z","uuid":"a1","message":{"role":"assistant","content":[{"type":"tool_use","id":"tu1","name":"LSP","input":{"action":"refs"}}]}}
{"type":"user","timestamp":"2026-05-29T16:01:01Z","uuid":"a2","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu1","content":"ok"}]}}
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_ingest.py`:

```python
import pathlib
from convo_analyzer.ingest import ingest_all

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "nested_project"

def test_ingest_links_subagents(tmp_corpus):
    result = ingest_all(
        projects_root=FIXTURES,
        db_path=tmp_corpus["db"],
        blobs_path=tmp_corpus["blobs"],
        manifest_path=tmp_corpus["manifest"],
    )
    assert result["sessions_ingested"] == 2

    import duckdb
    con = duckdb.connect(str(tmp_corpus["db"]))
    rows = con.execute(
        "SELECT session_id, project, is_subagent, parent_session_id "
        "FROM sessions ORDER BY is_subagent"
    ).fetchall()
    # Parent first (is_subagent=False), then child (is_subagent=True).
    parent_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert rows[0] == (parent_id, "project", False, None)
    assert rows[1][2] is True
    assert rows[1][3] == parent_id
    assert rows[1][1] == "project"  # inherited, not "subagents"

    # LSP tool call from the child should be queryable via the parent join.
    n = con.execute("""
        SELECT COUNT(*) FROM tool_calls tc
        JOIN sessions s USING (session_id)
        WHERE (s.session_id = ? OR s.parent_session_id = ?)
          AND tc.tool_name = 'LSP'
    """, [parent_id, parent_id]).fetchone()[0]
    assert n == 1
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `pytest tests/test_ingest.py -v`
Expected: PASS — this is a green-bar test that exercises the full ingestion path with the cumulative changes from Tasks 1–8.

If the test fails, debug by inspecting which prior task's behavior is missing — do NOT add new code in this task; instead go back and fix the relevant earlier task.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/nested_project tests/test_ingest.py
git commit -m "test(ingest): cover parent + subagent end-to-end linkage"
```

---

### Task 10: Re-ingest the live corpus and verify on a real session

**Files:** none (operational task)

This is a manual verification step against the user's real `corpus.db`. It produces no code changes but is required because the schema migration (`ALTER TABLE … ADD COLUMN`) only fires when the existing DB is opened — the new columns will be NULL for sessions ingested before Task 2 landed.

- [ ] **Step 1: Re-ingest**

Run from the project root:

```bash
source .venv/bin/activate && convo ingest 2>&1 | tail -5
```

Expected: ingest completes with the usual summary. New columns are added by `open_db` at startup; existing rows have NULL `parent_session_id` and FALSE `is_subagent` initially.

- [ ] **Step 2: Rebuild subagent linkage for already-ingested sessions**

Subagent rows already in the DB were inserted with the old code and won't have their flags set. Force a re-ingest by clearing the manifest and re-running:

```bash
rm manifest.json && convo ingest 2>&1 | tail -5
```

Expected: every session re-ingests (no "skip" lines). After this, parent/child linkage is correct for the full corpus.

- [ ] **Step 3: Verify against the known case**

Run:

```bash
convo sql "SELECT session_id, parent_session_id, is_subagent FROM sessions WHERE session_id = 'agent-a478ac3907e21b2ff' OR session_id = '29dd51ef-83f4-4dc3-b87f-5cc005f5a1d7'"
```

Expected output:

```
session_id                            parent_session_id                     is_subagent
------------------------------------  ------------------------------------  -----------
29dd51ef-83f4-4dc3-b87f-5cc005f5a1d7  -                                     False
agent-a478ac3907e21b2ff                29dd51ef-83f4-4dc3-b87f-5cc005f5a1d7  True
```

- [ ] **Step 4: Verify the LSP rollup**

Run:

```bash
convo sql "SELECT tool_name, COUNT(*) FROM tool_calls tc JOIN sessions s USING (session_id) WHERE s.session_id='29dd51ef-83f4-4dc3-b87f-5cc005f5a1d7' OR s.parent_session_id='29dd51ef-83f4-4dc3-b87f-5cc005f5a1d7' GROUP BY 1 ORDER BY 2 DESC"
```

Expected: `LSP` appears with count `8`, matching the manual grep count from the JSONL.

- [ ] **Step 5: Commit the manifest reset note (if applicable)**

No code changes from this task. If the team needs to document the manifest-reset step (e.g. in a README), do it now:

```bash
# Optional: only if a README/CHANGELOG exists.
git status
# If nothing to commit, skip.
```

---

## Notes for the engineer

- **Why "additive" matters:** the manifest layer (`manifest.json`) keys sessions by `session_id`. We are not renaming or moving session IDs, so all existing manifest entries remain valid and re-ingest stays incremental for users who don't need backfilled linkage.
- **Why use `ALTER TABLE … ADD COLUMN IF NOT EXISTS`:** the codebase already uses this idiom (see `schema.sql` line 36 for `text_head`). It lets a brand-new `corpus.db` and a long-lived one both reach the same shape without a migration script.
- **The UUID regex matters:** the directory immediately above `subagents/` must be a UUID to count as a parent-session ID. This prevents false positives (e.g. someone naming a directory `subagents` somewhere unrelated). If the runtime starts using a different ID shape for parent sessions in the future, update `_UUID_RE`.
- **The `convo session --with-subagents` flag intentionally does NOT recurse into grandchildren.** As of writing, Claude Code only spawns one level of subagent. If nested dispatches appear later, the query becomes a recursive CTE — but YAGNI: do not pre-build it.
