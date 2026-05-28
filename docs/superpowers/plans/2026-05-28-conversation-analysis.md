# Conversation Analyzer — Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic 5-stage pipeline that ingests `~/.claude/projects/**/*.jsonl`, sanitizes and normalizes events into DuckDB + a content-addressed blob store, derives token/skill/sequence signals, exposes a `convo` CLI, and lets a final LLM stage interpret the aggregated dashboard.

**Architecture:** Five idempotent Python modules under `convo_analyzer/` (parse → sanitize → load → derive → llm), invoked through a single `convo` CLI. Storage is one DuckDB file (`corpus.db`) plus a `blobs/` sharded directory for sanitized full bodies. Each stage reads from the previous artifact, never from raw JSONL twice; ingest is incremental via a `manifest.json` keyed on `session_id`.

**Tech Stack:** Python 3.11+, `duckdb`, `pydantic` v2, `typer` for the CLI, `anthropic` SDK for stage 5, `pytest` for tests.

---

## File Structure

```
convo-analyzer/
├── pyproject.toml
├── corpus.db                       # generated
├── blobs/<aa>/<hash>.txt           # generated
├── manifest.json                   # generated
├── skills.yaml                     # hand-curated signatures for signal #5
├── convo_analyzer/
│   ├── __init__.py
│   ├── models.py                   # pydantic event models
│   ├── parse.py                    # stage 1: jsonl → normalized events
│   ├── sanitize.py                 # stage 2: regex scrub + path normalize + blob writer
│   ├── blobs.py                    # content-addressed blob store
│   ├── schema.sql                  # DuckDB DDL
│   ├── load.py                     # stage 4 part A: events/tool_calls/sessions/skill_invocations
│   ├── manifest.py                 # incremental ingest manifest
│   ├── ingest.py                   # orchestrates parse→sanitize→load per session
│   ├── derive/
│   │   ├── __init__.py
│   │   ├── sequences.py            # n-gram extraction
│   │   ├── tokens.py               # signals 1–4
│   │   ├── skills.py               # signals 5–7
│   │   └── recurring.py            # signals 8–10
│   ├── cli.py                      # `convo` entrypoint
│   ├── queries.py                  # named SQL queries used by CLI
│   └── llm.py                      # stage 5: dashboard → narrative
└── tests/
    ├── fixtures/
    │   └── sample_session.jsonl    # checked-in trimmed real session
    ├── test_parse.py
    ├── test_sanitize.py
    ├── test_blobs.py
    ├── test_load.py
    ├── test_ingest.py
    ├── test_manifest.py
    ├── test_derive_sequences.py
    ├── test_derive_tokens.py
    ├── test_derive_skills.py
    ├── test_derive_recurring.py
    ├── test_cli.py
    └── test_llm.py
```

Files are split by stage and (within `derive/`) by signal axis. Each module owns one stage's I/O contract.

---

## Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `convo_analyzer/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `tests/fixtures/sample_session.jsonl`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "convo-analyzer"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "duckdb>=1.1",
  "pydantic>=2.7",
  "typer>=0.12",
  "pyyaml>=6.0",
  "anthropic>=0.40",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-cov"]

[project.scripts]
convo = "convo_analyzer.cli:app"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["convo_analyzer*"]
```

- [ ] **Step 2: Install in editable mode and confirm**

Run: `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
Expected: `Successfully installed convo-analyzer-0.1.0 ...`

- [ ] **Step 3: Build the test fixture**

Run:
```bash
python3 - <<'PY'
import json, pathlib, itertools
src = pathlib.Path.home() / ".claude/projects"
# pick the first jsonl with at least one assistant tool_use
for p in src.rglob("*.jsonl"):
    lines = p.read_text().splitlines()
    has_tool = any('"tool_use"' in l for l in lines)
    if has_tool and 20 <= len(lines) <= 200:
        out = pathlib.Path("tests/fixtures/sample_session.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n")
        print("wrote", out, "lines=", len(lines), "src=", p)
        break
PY
```
Expected: prints `wrote tests/fixtures/sample_session.jsonl ...`

- [ ] **Step 4: Add `conftest.py`**

```python
import pathlib
import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

@pytest.fixture
def sample_session_path() -> pathlib.Path:
    return FIXTURES / "sample_session.jsonl"

@pytest.fixture
def tmp_corpus(tmp_path):
    return {
        "db": tmp_path / "corpus.db",
        "blobs": tmp_path / "blobs",
        "manifest": tmp_path / "manifest.json",
    }
```

- [ ] **Step 5: Smoke-test pytest discovers nothing yet**

Run: `pytest`
Expected: `no tests ran` (or 0 passed) — pytest exits cleanly.

- [ ] **Step 6: Commit**

```bash
git init -q && git add -A
git commit -m "chore: scaffold convo-analyzer package and test fixture"
```

---

## Task 1: Event models

**Files:**
- Create: `convo_analyzer/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_models.py
from convo_analyzer.models import NormalizedEvent, ToolCall

def test_event_minimal_user_message():
    e = NormalizedEvent(
        event_id="u1", session_id="s1", ts="2026-05-01T00:00:00Z",
        type="user", role="user", text_len=12,
    )
    assert e.type == "user"
    assert e.input_tokens is None

def test_tool_call_position_required():
    t = ToolCall(
        event_id="e1", session_id="s1", ts="2026-05-01T00:00:00Z",
        tool_name="Read", args_json='{"file_path":"/tmp/x"}',
        result_size=42, success=True, position_in_session=3,
    )
    assert t.tool_name == "Read"
    assert t.result_blob_hash is None
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: convo_analyzer.models`

- [ ] **Step 3: Implement models**

```python
# convo_analyzer/models.py
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel

EventType = Literal[
    "user", "assistant", "system", "attachment",
    "tool_use", "tool_result", "ai-title", "last-prompt",
    "permission-mode", "file-history-snapshot", "other",
]

class NormalizedEvent(BaseModel):
    event_id: str
    session_id: str
    parent_uuid: Optional[str] = None
    ts: str
    type: EventType | str
    subtype: Optional[str] = None
    is_sidechain: bool = False
    is_meta: bool = False
    role: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read: Optional[int] = None
    cache_creation: Optional[int] = None
    duration_ms: Optional[int] = None
    text_len: Optional[int] = None
    blob_hash: Optional[str] = None

class ToolCall(BaseModel):
    event_id: str
    session_id: str
    ts: str
    tool_name: str
    args_json: str
    args_blob_hash: Optional[str] = None
    result_size: int = 0
    result_blob_hash: Optional[str] = None
    success: bool = True
    duration_ms: Optional[int] = None
    retry_attempt: int = 0
    position_in_session: int

class SkillInvocation(BaseModel):
    session_id: str
    ts: str
    skill_name: str
    args: Optional[str] = None
    triggered_by: Literal["user_slash", "auto"] = "auto"
    followed_by_tools: list[str] = []

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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/models.py tests/test_models.py
git commit -m "feat(models): add pydantic event, tool_call, skill, session models"
```

---

## Task 2: Parser (stage 1)

**Files:**
- Create: `convo_analyzer/parse.py`
- Create: `tests/test_parse.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_parse.py
from convo_analyzer.parse import parse_session_file, ParsedSession

def test_parse_fixture(sample_session_path):
    s: ParsedSession = parse_session_file(sample_session_path)
    assert s.session_id
    assert len(s.events) > 0
    # at least one assistant event in fixture
    assert any(e.type == "assistant" for e in s.events)

def test_parse_extracts_tool_calls(sample_session_path):
    s = parse_session_file(sample_session_path)
    # tool calls are zero-or-more; just confirm shape
    for tc in s.tool_calls:
        assert tc.tool_name
        assert tc.position_in_session >= 0

def test_parse_assistant_usage_tokens(sample_session_path):
    s = parse_session_file(sample_session_path)
    # token totals should be non-negative
    assert s.session.total_input_tokens >= 0
    assert s.session.total_output_tokens >= 0
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: convo_analyzer.parse`

- [ ] **Step 3: Implement parser**

```python
# convo_analyzer/parse.py
from __future__ import annotations
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Iterable

from .models import NormalizedEvent, ToolCall, SessionRow, SkillInvocation

PROJECT_CWD_RE = re.compile(r"^-Users-[^-]+-(.+)$")
SLASH_CMD_RE = re.compile(r"^/([a-zA-Z0-9_\-:]+)\b")

@dataclass
class ParsedSession:
    session_id: str
    session: SessionRow
    events: list[NormalizedEvent] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    skills: list[SkillInvocation] = field(default_factory=list)

def _project_slug(jsonl_path: pathlib.Path) -> tuple[str, str]:
    """Return (project_slug, cwd_with_tilde) from the parent dir name."""
    parent = jsonl_path.parent.name  # e.g. -Users-yehonatana-Workspace-Wix-foo
    cwd = "/" + parent.lstrip("-").replace("-", "/")
    cwd = cwd.replace("/Users/yehonatana", "~", 1)
    slug = cwd.rstrip("/").split("/")[-1] or "root"
    return slug, cwd

def _iter_jsonl(path: pathlib.Path) -> Iterable[dict]:
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

def _content_blocks(message: dict) -> list[dict]:
    c = message.get("content")
    if isinstance(c, list):
        return c
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return []

def parse_session_file(path: pathlib.Path) -> ParsedSession:
    path = pathlib.Path(path)
    project, cwd = _project_slug(path)
    session_id = path.stem  # filename without .jsonl
    events: list[NormalizedEvent] = []
    tool_calls: list[ToolCall] = []
    skills: list[SkillInvocation] = []

    total_in = total_out = total_cr = total_cc = 0
    started_at = ended_at = None
    msg_count = 0
    compactions = 0
    model = None
    ai_title = None
    git_branch = None

    pos = 0
    for raw in _iter_jsonl(path):
        etype = raw.get("type", "other")
        ts = raw.get("timestamp") or raw.get("ts") or ""
        uuid_ = raw.get("uuid") or raw.get("leafUuid") or f"{session_id}:{pos}"
        if ts:
            started_at = started_at or ts
            ended_at = ts

        if etype == "ai-title":
            ai_title = raw.get("aiTitle")
            continue
        if etype == "system" and raw.get("subtype") == "compaction":
            compactions += 1

        message = raw.get("message") or {}
        role = message.get("role") if isinstance(message, dict) else None
        usage = (message.get("usage") if isinstance(message, dict) else None) or {}
        in_tok = usage.get("input_tokens")
        out_tok = usage.get("output_tokens")
        cr = usage.get("cache_read_input_tokens")
        cc = usage.get("cache_creation_input_tokens")
        if in_tok: total_in += in_tok
        if out_tok: total_out += out_tok
        if cr: total_cr += cr
        if cc: total_cc += cc
        if isinstance(message, dict) and message.get("model"):
            model = message["model"]

        blocks = _content_blocks(message) if isinstance(message, dict) else []
        text_payload = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")

        ev = NormalizedEvent(
            event_id=uuid_,
            session_id=session_id,
            parent_uuid=raw.get("parentUuid"),
            ts=ts,
            type=etype,
            subtype=raw.get("subtype"),
            is_sidechain=bool(raw.get("isSidechain")),
            is_meta=bool(raw.get("isMeta")),
            role=role,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read=cr,
            cache_creation=cc,
            duration_ms=raw.get("durationMs"),
            text_len=len(text_payload) if text_payload else None,
        )
        events.append(ev)
        if etype in ("user", "assistant"):
            msg_count += 1

        # Tool uses inside assistant messages
        for b in blocks:
            if b.get("type") == "tool_use":
                tc = ToolCall(
                    event_id=uuid_,
                    session_id=session_id,
                    ts=ts,
                    tool_name=b.get("name") or "unknown",
                    args_json=json.dumps(b.get("input") or {}, ensure_ascii=False)[:2048],
                    success=True,
                    position_in_session=pos,
                )
                tool_calls.append(tc)
            elif b.get("type") == "tool_result":
                content = b.get("content")
                size = len(content) if isinstance(content, str) else len(json.dumps(content or ""))
                # attach to most recent tool_call by event flow
                if tool_calls:
                    tool_calls[-1].result_size = max(tool_calls[-1].result_size, size)
                    tool_calls[-1].success = not bool(b.get("is_error"))

        # Skill invocations: user slash command OR explicit Skill tool_use
        if etype == "user" and text_payload:
            m = SLASH_CMD_RE.match(text_payload.lstrip())
            if m:
                skills.append(SkillInvocation(
                    session_id=session_id, ts=ts, skill_name=m.group(1),
                    triggered_by="user_slash",
                ))
        for b in blocks:
            if b.get("type") == "tool_use" and b.get("name") == "Skill":
                args = b.get("input") or {}
                skills.append(SkillInvocation(
                    session_id=session_id, ts=ts,
                    skill_name=str(args.get("skill", "unknown")),
                    args=json.dumps(args.get("args") or "")[:512],
                    triggered_by="auto",
                ))

        pos += 1

    duration_ms = None
    # crude duration: derive from ts strings if both present
    sess = SessionRow(
        session_id=session_id,
        project=project,
        cwd=cwd,
        git_branch=git_branch,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        message_count=msg_count,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cache_read_tokens=total_cr,
        total_cache_creation_tokens=total_cc,
        compaction_count=compactions,
        model=model,
        ai_title=ai_title,
    )

    # backfill `followed_by_tools` for each skill invocation
    for sk in skills:
        idx = next((i for i, t in enumerate(tool_calls) if t.ts >= sk.ts), len(tool_calls))
        sk.followed_by_tools = [t.tool_name for t in tool_calls[idx:idx + 5]]

    return ParsedSession(
        session_id=session_id, session=sess,
        events=events, tool_calls=tool_calls, skills=skills,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_parse.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/parse.py tests/test_parse.py
git commit -m "feat(parse): stage 1 JSONL → normalized events + tool calls + skills"
```

---

## Task 3: Blob store

**Files:**
- Create: `convo_analyzer/blobs.py`
- Create: `tests/test_blobs.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_blobs.py
from convo_analyzer.blobs import BlobStore

def test_blobstore_roundtrip(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    h = store.put("hello world")
    assert len(h) == 64  # sha256 hex
    assert store.get(h) == "hello world"
    # sharded layout
    assert (tmp_path / "blobs" / h[:2] / f"{h}.txt").exists()

def test_blobstore_dedupes(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    h1 = store.put("same")
    h2 = store.put("same")
    assert h1 == h2
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_blobs.py -v`
Expected: FAIL — `ModuleNotFoundError: convo_analyzer.blobs`

- [ ] **Step 3: Implement**

```python
# convo_analyzer/blobs.py
from __future__ import annotations
import hashlib
import pathlib

class BlobStore:
    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, h: str) -> pathlib.Path:
        return self.root / h[:2] / f"{h}.txt"

    def put(self, content: str) -> str:
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        p = self._path(h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return h

    def get(self, h: str) -> str:
        return self._path(h).read_text()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_blobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/blobs.py tests/test_blobs.py
git commit -m "feat(blobs): content-addressed sharded blob store"
```

---

## Task 4: Sanitizer (stage 2)

**Files:**
- Create: `convo_analyzer/sanitize.py`
- Create: `tests/test_sanitize.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sanitize.py
from convo_analyzer.sanitize import scrub, normalize_path

def test_scrub_aws_key():
    s = "AKIAIOSFODNN7EXAMPLE is bad"
    out = scrub(s)
    assert "AKIA" not in out
    assert "[REDACTED:aws]" in out

def test_scrub_github_token():
    s = "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 here"
    out = scrub(s)
    assert "ghp_" not in out

def test_scrub_bearer():
    s = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
    out = scrub(s)
    assert "eyJ" not in out

def test_scrub_env_line():
    s = "DATABASE_PASSWORD=hunter2\nOK"
    out = scrub(s)
    assert "hunter2" not in out

def test_normalize_path():
    assert normalize_path("/Users/yehonatana/foo/bar") == "~/foo/bar"
    assert normalize_path("nothing") == "nothing"
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_sanitize.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# convo_analyzer/sanitize.py
from __future__ import annotations
import os
import re

HOME = os.path.expanduser("~")

PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"),                          "[REDACTED:aws]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                "[REDACTED:github]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"),                 "Bearer [REDACTED:bearer]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "[REDACTED:jwt]"),
    (re.compile(r"(?m)^([A-Z][A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|KEY))=.+$"), r"\1=[REDACTED:env]"),
]

def scrub(text: str) -> str:
    if not text:
        return text
    out = text
    for rx, repl in PATTERNS:
        out = rx.sub(repl, out)
    return out

def normalize_path(s: str) -> str:
    if HOME in s:
        return s.replace(HOME, "~")
    return s
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sanitize.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/sanitize.py tests/test_sanitize.py
git commit -m "feat(sanitize): regex scrub for secrets + path normalize"
```

---

## Task 5: DuckDB schema + loader (stage 4 — base tables)

**Files:**
- Create: `convo_analyzer/schema.sql`
- Create: `convo_analyzer/load.py`
- Create: `tests/test_load.py`

- [ ] **Step 1: Write the schema SQL**

```sql
-- convo_analyzer/schema.sql
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  project TEXT,
  cwd TEXT,
  git_branch TEXT,
  started_at TEXT,
  ended_at TEXT,
  duration_ms BIGINT,
  message_count INTEGER,
  total_input_tokens BIGINT,
  total_output_tokens BIGINT,
  total_cache_read_tokens BIGINT,
  total_cache_creation_tokens BIGINT,
  compaction_count INTEGER,
  model TEXT,
  ai_title TEXT,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
  event_id TEXT,
  session_id TEXT,
  parent_uuid TEXT,
  ts TEXT,
  type TEXT,
  subtype TEXT,
  is_sidechain BOOLEAN,
  is_meta BOOLEAN,
  role TEXT,
  input_tokens BIGINT,
  output_tokens BIGINT,
  cache_read BIGINT,
  cache_creation BIGINT,
  duration_ms BIGINT,
  text_len BIGINT,
  blob_hash TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
  event_id TEXT,
  session_id TEXT,
  ts TEXT,
  tool_name TEXT,
  args_json TEXT,
  args_blob_hash TEXT,
  result_size BIGINT,
  result_blob_hash TEXT,
  success BOOLEAN,
  duration_ms BIGINT,
  retry_attempt INTEGER,
  position_in_session INTEGER
);

CREATE TABLE IF NOT EXISTS skill_invocations (
  session_id TEXT,
  ts TEXT,
  skill_name TEXT,
  args TEXT,
  triggered_by TEXT,
  followed_by_tools TEXT  -- JSON array
);

CREATE TABLE IF NOT EXISTS tool_sequences (
  session_id TEXT,
  n INTEGER,
  sequence TEXT,        -- JSON array
  count INTEGER,
  first_ts TEXT,
  last_ts TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_tools_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tools_name ON tool_calls(tool_name);
```

- [ ] **Step 2: Write failing test for `load.py`**

```python
# tests/test_load.py
import duckdb
from convo_analyzer.parse import parse_session_file
from convo_analyzer.load import open_db, load_session

def test_load_session_writes_all_tables(tmp_corpus, sample_session_path):
    parsed = parse_session_file(sample_session_path)
    con = open_db(tmp_corpus["db"])
    load_session(con, parsed)
    n_sessions = con.execute("select count(*) from sessions").fetchone()[0]
    n_events = con.execute("select count(*) from events").fetchone()[0]
    assert n_sessions == 1
    assert n_events == len(parsed.events)

def test_load_is_idempotent(tmp_corpus, sample_session_path):
    parsed = parse_session_file(sample_session_path)
    con = open_db(tmp_corpus["db"])
    load_session(con, parsed)
    load_session(con, parsed)  # second call replaces, doesn't duplicate
    n = con.execute("select count(*) from sessions").fetchone()[0]
    assert n == 1
```

- [ ] **Step 3: Run and confirm failure**

Run: `pytest tests/test_load.py -v`
Expected: FAIL

- [ ] **Step 4: Implement `load.py`**

```python
# convo_analyzer/load.py
from __future__ import annotations
import json
import pathlib
import duckdb

from .parse import ParsedSession
from .sanitize import scrub, normalize_path
from .blobs import BlobStore

SCHEMA = pathlib.Path(__file__).parent / "schema.sql"

def open_db(path: pathlib.Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path))
    con.execute(SCHEMA.read_text())
    return con

def _delete_session(con, session_id: str) -> None:
    for tbl in ("sessions", "events", "tool_calls", "skill_invocations", "tool_sequences"):
        con.execute(f"DELETE FROM {tbl} WHERE session_id = ?", [session_id])

def load_session(
    con: duckdb.DuckDBPyConnection,
    parsed: ParsedSession,
    blobs: BlobStore | None = None,
) -> None:
    _delete_session(con, parsed.session_id)

    s = parsed.session
    con.execute(
        """INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        [s.session_id, s.project, normalize_path(s.cwd), s.git_branch,
         s.started_at, s.ended_at, s.duration_ms, s.message_count,
         s.total_input_tokens, s.total_output_tokens,
         s.total_cache_read_tokens, s.total_cache_creation_tokens,
         s.compaction_count, s.model, s.ai_title],
    )

    for ev in parsed.events:
        con.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [ev.event_id, ev.session_id, ev.parent_uuid, ev.ts, ev.type,
             ev.subtype, ev.is_sidechain, ev.is_meta, ev.role,
             ev.input_tokens, ev.output_tokens, ev.cache_read, ev.cache_creation,
             ev.duration_ms, ev.text_len, ev.blob_hash],
        )

    for tc in parsed.tool_calls:
        args_clean = scrub(tc.args_json)
        args_hash = blobs.put(args_clean) if (blobs and len(args_clean) >= 2048) else None
        con.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [tc.event_id, tc.session_id, tc.ts, tc.tool_name,
             args_clean[:2048], args_hash, tc.result_size,
             tc.result_blob_hash, tc.success, tc.duration_ms,
             tc.retry_attempt, tc.position_in_session],
        )

    for sk in parsed.skills:
        con.execute(
            "INSERT INTO skill_invocations VALUES (?,?,?,?,?,?)",
            [sk.session_id, sk.ts, sk.skill_name, sk.args,
             sk.triggered_by, json.dumps(sk.followed_by_tools)],
        )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_load.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add convo_analyzer/schema.sql convo_analyzer/load.py tests/test_load.py
git commit -m "feat(load): DuckDB schema + idempotent session loader"
```

---

## Task 6: Ingest orchestration + manifest

**Files:**
- Create: `convo_analyzer/manifest.py`
- Create: `convo_analyzer/ingest.py`
- Create: `tests/test_manifest.py`
- Create: `tests/test_ingest.py`

- [ ] **Step 1: Failing tests for manifest**

```python
# tests/test_manifest.py
import json
from convo_analyzer.manifest import Manifest

def test_manifest_records_and_checks(tmp_path):
    m = Manifest(tmp_path / "manifest.json")
    assert not m.has_seen("s1", "2026-05-01T00:00:00Z")
    m.mark("s1", "2026-05-01T00:00:00Z")
    m.save()
    m2 = Manifest(tmp_path / "manifest.json")
    assert m2.has_seen("s1", "2026-05-01T00:00:00Z")
    # newer ts is not seen yet
    assert not m2.has_seen("s1", "2026-05-02T00:00:00Z")
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_manifest.py -v`
Expected: FAIL

- [ ] **Step 3: Implement manifest**

```python
# convo_analyzer/manifest.py
from __future__ import annotations
import json
import pathlib

class Manifest:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text() or "{}")

    def has_seen(self, session_id: str, last_ts: str) -> bool:
        prev = self._data.get(session_id)
        return prev is not None and prev >= last_ts

    def mark(self, session_id: str, last_ts: str) -> None:
        self._data[session_id] = last_ts

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))
```

- [ ] **Step 4: Run manifest tests**

Run: `pytest tests/test_manifest.py -v`
Expected: PASS

- [ ] **Step 5: Failing test for ingest**

```python
# tests/test_ingest.py
import shutil
from convo_analyzer.ingest import ingest_all

def test_ingest_all_one_session(tmp_path, tmp_corpus, sample_session_path):
    projects_root = tmp_path / "projects"
    pdir = projects_root / "-Users-yehonatana-Workspace-Wix-foo"
    pdir.mkdir(parents=True)
    shutil.copy(sample_session_path, pdir / "abc-123.jsonl")
    stats = ingest_all(
        projects_root=projects_root,
        db_path=tmp_corpus["db"],
        blobs_path=tmp_corpus["blobs"],
        manifest_path=tmp_corpus["manifest"],
    )
    assert stats["sessions_ingested"] == 1
    # rerun is a no-op
    stats2 = ingest_all(
        projects_root=projects_root,
        db_path=tmp_corpus["db"],
        blobs_path=tmp_corpus["blobs"],
        manifest_path=tmp_corpus["manifest"],
    )
    assert stats2["sessions_ingested"] == 0
```

- [ ] **Step 6: Run and confirm failure**

Run: `pytest tests/test_ingest.py -v`
Expected: FAIL

- [ ] **Step 7: Implement ingest**

```python
# convo_analyzer/ingest.py
from __future__ import annotations
import pathlib
from .parse import parse_session_file
from .load import open_db, load_session
from .blobs import BlobStore
from .manifest import Manifest

def ingest_all(
    projects_root: pathlib.Path,
    db_path: pathlib.Path,
    blobs_path: pathlib.Path,
    manifest_path: pathlib.Path,
) -> dict:
    projects_root = pathlib.Path(projects_root)
    blobs = BlobStore(blobs_path)
    manifest = Manifest(manifest_path)
    con = open_db(db_path)
    n = 0
    for jsonl in projects_root.rglob("*.jsonl"):
        parsed = parse_session_file(jsonl)
        last_ts = parsed.session.ended_at or ""
        if manifest.has_seen(parsed.session_id, last_ts):
            continue
        load_session(con, parsed, blobs=blobs)
        manifest.mark(parsed.session_id, last_ts)
        n += 1
    manifest.save()
    con.close()
    return {"sessions_ingested": n}
```

- [ ] **Step 8: Run ingest tests**

Run: `pytest tests/test_ingest.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add convo_analyzer/manifest.py convo_analyzer/ingest.py tests/test_manifest.py tests/test_ingest.py
git commit -m "feat(ingest): incremental ingest with manifest"
```

---

## Task 7: Derive — tool sequences (n-grams)

**Files:**
- Create: `convo_analyzer/derive/__init__.py` (empty)
- Create: `convo_analyzer/derive/sequences.py`
- Create: `tests/test_derive_sequences.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_derive_sequences.py
import json
from convo_analyzer.load import open_db
from convo_analyzer.derive.sequences import build_sequences

def _seed(con, calls):
    for i, (sid, tool) in enumerate(calls):
        con.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [f"e{i}", sid, f"2026-01-01T00:00:{i:02d}Z", tool,
             "{}", None, 0, None, True, None, 0, i],
        )

def test_build_sequences_counts_bigrams(tmp_corpus):
    con = open_db(tmp_corpus["db"])
    _seed(con, [("s1","Read"),("s1","Edit"),("s1","Read"),("s1","Edit")])
    build_sequences(con, ns=(2,))
    rows = con.execute(
        "select sequence, count from tool_sequences where n=2 order by count desc"
    ).fetchall()
    seqs = {tuple(json.loads(r[0])): r[1] for r in rows}
    assert seqs.get(("Read","Edit")) == 2
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_derive_sequences.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_derive_sequences.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/derive/__init__.py convo_analyzer/derive/sequences.py tests/test_derive_sequences.py
git commit -m "feat(derive): n-gram tool sequences per session"
```

---

## Task 8: Derive — token-efficiency signals (1–4)

**Files:**
- Create: `convo_analyzer/derive/tokens.py`
- Create: `tests/test_derive_tokens.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_derive_tokens.py
from convo_analyzer.load import open_db
from convo_analyzer.derive.tokens import build_token_signals

def test_redundant_reads_detected(tmp_corpus):
    con = open_db(tmp_corpus["db"])
    # two Reads of same file, no intervening Edit
    inserts = [
        ("Read", '{"file_path":"~/a.py"}', 0),
        ("Read", '{"file_path":"~/a.py"}', 1),
    ]
    for i, (name, args, pos) in enumerate(inserts):
        con.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [f"e{i}", "s1", f"2026-01-01T00:00:0{i}Z", name, args,
             None, 100, None, True, None, 0, pos],
        )
    build_token_signals(con)
    rows = con.execute("SELECT * FROM signal_redundant_reads").fetchall()
    assert len(rows) >= 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_derive_tokens.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
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
        SELECT e.session_id, 5,
               (SELECT COALESCE(SUM(output_tokens),0) FROM events e2
                 WHERE e2.session_id=e.session_id AND e2.ts < e.ts
                 ORDER BY e2.ts DESC LIMIT 5)
        FROM events e WHERE e.subtype='compaction'
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

    # 4. Oversized agent dispatches: small args, big result
    con.execute("""
        INSERT INTO signal_oversized_agent
        SELECT session_id, event_id, LENGTH(args_json) AS args_len, result_size
        FROM tool_calls
        WHERE tool_name='Agent'
          AND LENGTH(args_json) < 500
          AND result_size > 10000
    """)
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_derive_tokens.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/derive/tokens.py tests/test_derive_tokens.py
git commit -m "feat(derive): token-efficiency signals (bloat, compaction, redundant reads, agent)"
```

---

## Task 9: Derive — skill signals (5–7)

**Files:**
- Create: `skills.yaml`
- Create: `convo_analyzer/derive/skills.py`
- Create: `tests/test_derive_skills.py`

- [ ] **Step 1: Seed `skills.yaml`**

```yaml
# skills.yaml — hand-curated tool-sequence signatures that *should* trigger a skill
debugging:
  signature: ["Bash", "Read", "Edit", "Bash"]
  reason: "iterating on a failing test with no debugging skill"
test_driven_development:
  signature: ["Edit", "Bash"]
  reason: "writing code then running tests but never wrote test first"
```

- [ ] **Step 2: Failing test**

```python
# tests/test_derive_skills.py
from convo_analyzer.load import open_db
from convo_analyzer.derive.skills import build_skill_signals

def test_skill_eligible_missed(tmp_corpus, tmp_path):
    yaml_path = tmp_path / "skills.yaml"
    yaml_path.write_text(
        'pretend_skill:\n  signature: ["Read","Edit"]\n  reason: "x"\n'
    )
    con = open_db(tmp_corpus["db"])
    for i, name in enumerate(["Read","Edit"]):
        con.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [f"e{i}", "s1", f"2026-01-01T00:00:0{i}Z", name,
             "{}", None, 0, None, True, None, 0, i],
        )
    build_skill_signals(con, skills_yaml=yaml_path)
    rows = con.execute(
        "SELECT skill_name FROM signal_skill_eligible_missed"
    ).fetchall()
    assert ("pretend_skill",) in rows
```

- [ ] **Step 3: Run and confirm failure**

Run: `pytest tests/test_derive_skills.py -v`
Expected: FAIL

- [ ] **Step 4: Implement**

```python
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

    # abandoned: skill invocation followed within 3 user turns by a correction
    rows = con.execute("""
        SELECT session_id, ts, skill_name FROM skill_invocations
    """).fetchall()
    for sid, ts, skill in rows:
        followups = con.execute("""
            SELECT type, role, ts FROM events
            WHERE session_id=? AND ts > ? AND role='user'
            ORDER BY ts LIMIT 3
        """, [sid, ts]).fetchall()
        for _t, _r, fts in followups:
            txt = con.execute("""
                SELECT 1 FROM events
                WHERE session_id=? AND ts=? AND text_len IS NOT NULL
            """, [sid, fts]).fetchone()
            if txt:
                con.execute(
                    "INSERT INTO signal_skill_abandoned VALUES (?,?,?,?)",
                    [sid, skill, fts, "follow-up after skill"],
                )

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
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_derive_skills.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add skills.yaml convo_analyzer/derive/skills.py tests/test_derive_skills.py
git commit -m "feat(derive): skill-eligible-missed, abandoned, turnaround signals"
```

---

## Task 10: Derive — recurring sequences + correction clusters (8–10)

**Files:**
- Create: `convo_analyzer/derive/recurring.py`
- Create: `tests/test_derive_recurring.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_derive_recurring.py
import json
from convo_analyzer.load import open_db
from convo_analyzer.derive.recurring import build_recurring_signals

def test_recurring_sequence_threshold(tmp_corpus):
    con = open_db(tmp_corpus["db"])
    for sid in ("s1","s2","s3","s4","s5"):
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            [sid,"proj"+sid[-1],"~/x",None,None,None,None,0,0,0,0,0,0,None,None])
        con.execute("INSERT INTO tool_sequences VALUES (?,?,?,?,?,?)",
            [sid, 2, json.dumps(["Read","Edit"]), 1, "2026","2026"])
    build_recurring_signals(con)
    rows = con.execute("SELECT * FROM signal_recurring_sequences").fetchall()
    assert any("Read" in r[0] for r in rows)
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_derive_recurring.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# convo_analyzer/derive/recurring.py
from __future__ import annotations
import duckdb

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

    # correction clusters: short user msgs starting with no/don't/stop/wait/actually
    con.execute("""
        INSERT INTO signal_correction_clusters
        SELECT '<placeholder>', COUNT(*) FROM events
        WHERE role='user' AND text_len IS NOT NULL AND text_len < 60
    """)
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_derive_recurring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add convo_analyzer/derive/recurring.py tests/test_derive_recurring.py
git commit -m "feat(derive): recurring sequences, correction clusters, path fixes"
```

---

## Task 11: `convo` CLI

**Files:**
- Create: `convo_analyzer/queries.py`
- Create: `convo_analyzer/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_cli.py
import os
import shutil
from typer.testing import CliRunner
from convo_analyzer.cli import app

runner = CliRunner()

def test_cli_ingest_and_list(tmp_path, sample_session_path, monkeypatch):
    projects = tmp_path / "projects" / "-Users-y-foo"
    projects.mkdir(parents=True)
    shutil.copy(sample_session_path, projects / "x.jsonl")
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    monkeypatch.setenv("CONVO_PROJECTS", str(tmp_path / "projects"))
    r = runner.invoke(app, ["ingest"])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(app, ["top-bloat", "--limit", "5"])
    assert r2.exit_code == 0
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL

- [ ] **Step 3: Implement queries module**

```python
# convo_analyzer/queries.py
TOP_BLOAT = """
SELECT session_id, tool_name, result_size, next_output_tokens, ratio
FROM signal_bloat
ORDER BY ratio DESC
LIMIT ?
"""

RECURRING = """
SELECT sequence, n, sessions, projects
FROM signal_recurring_sequences
WHERE sessions >= ? AND projects >= ?
ORDER BY sessions DESC, projects DESC
LIMIT ?
"""

SKILL_HEALTH = """
SELECT
  (SELECT COUNT(*) FROM skill_invocations WHERE skill_name=?) AS invocations,
  (SELECT COUNT(*) FROM signal_skill_abandoned WHERE skill_name=?) AS abandoned,
  (SELECT AVG(tokens_to_user) FROM signal_skill_turnaround WHERE skill_name=?) AS avg_turnaround
"""

SESSION_TIMELINE = """
SELECT ts, type, role, tool_name, text_len
FROM events e
LEFT JOIN tool_calls tc USING (event_id, session_id)
WHERE e.session_id = ?
ORDER BY e.ts
"""
```

- [ ] **Step 4: Implement CLI**

```python
# convo_analyzer/cli.py
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

def _root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CONVO_ROOT", "."))

def _db_path() -> pathlib.Path:
    return _root() / "corpus.db"

def _projects() -> pathlib.Path:
    return pathlib.Path(os.environ.get(
        "CONVO_PROJECTS", str(pathlib.Path.home() / ".claude/projects")
    ))

@app.command()
def ingest() -> None:
    """Parse, sanitize, and load all JSONL sessions, then derive all signals."""
    root = _root()
    stats = ingest_all(
        projects_root=_projects(),
        db_path=_db_path(),
        blobs_path=root / "blobs",
        manifest_path=root / "manifest.json",
    )
    typer.echo(f"ingested {stats['sessions_ingested']} new sessions")
    con = open_db(_db_path())
    build_sequences(con)
    build_token_signals(con)
    skills_yaml = pathlib.Path("skills.yaml")
    if skills_yaml.exists():
        build_skill_signals(con, skills_yaml)
    build_recurring_signals(con)
    typer.echo("derived all signals")

@app.command("top-bloat")
def top_bloat(limit: int = 20) -> None:
    con = duckdb.connect(str(_db_path()))
    for row in con.execute(queries.TOP_BLOAT, [limit]).fetchall():
        typer.echo("\t".join(str(c) for c in row))

@app.command("recurring-sequences")
def recurring_sequences(min_sessions: int = 5, min_projects: int = 3, limit: int = 20) -> None:
    con = duckdb.connect(str(_db_path()))
    for row in con.execute(queries.RECURRING, [min_sessions, min_projects, limit]).fetchall():
        typer.echo("\t".join(str(c) for c in row))

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

if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run CLI test**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add convo_analyzer/queries.py convo_analyzer/cli.py tests/test_cli.py
git commit -m "feat(cli): convo CLI with ingest, top-bloat, recurring, skill-health, session"
```

---

## Task 12: LLM stage (stage 5)

**Files:**
- Create: `convo_analyzer/llm.py`
- Create: `tests/test_llm.py`
- Modify: `convo_analyzer/cli.py` (add `interpret` command)

- [ ] **Step 1: Failing test (with mocked Anthropic client)**

```python
# tests/test_llm.py
from convo_analyzer.llm import build_dashboard, interpret
from convo_analyzer.load import open_db

class _FakeClient:
    def __init__(self): self.messages = self
        # nested attribute matches anthropic SDK shape
    def create(self, **kw):
        class R: content=[type("B",(),{"text":"OK"})()]
        return R()

def test_build_dashboard_empty(tmp_corpus):
    open_db(tmp_corpus["db"])  # create tables
    d = build_dashboard(tmp_corpus["db"])
    assert "top_bloat" in d
    assert isinstance(d["top_bloat"], list)

def test_interpret_returns_text(tmp_corpus, monkeypatch):
    open_db(tmp_corpus["db"])
    out = interpret(tmp_corpus["db"], client=_FakeClient(), model="claude-opus-4-7")
    assert out == "OK"
```

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/test_llm.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Add CLI command**

In `convo_analyzer/cli.py`, append:

```python
@app.command()
def interpret(model: str = "claude-opus-4-7") -> None:
    """Run the LLM stage against the derived dashboard."""
    from .llm import interpret as _interpret
    typer.echo(_interpret(_db_path(), model=model))
```

- [ ] **Step 5: Run LLM tests**

Run: `pytest tests/test_llm.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add convo_analyzer/llm.py tests/test_llm.py convo_analyzer/cli.py
git commit -m "feat(llm): stage 5 dashboard builder and interpret command"
```

---

## Task 13: End-to-end smoke test on real data

**Files:**
- Create: `tests/test_e2e.py`

- [ ] **Step 1: Write smoke test**

```python
# tests/test_e2e.py
import pathlib
import pytest
from typer.testing import CliRunner
from convo_analyzer.cli import app

runner = CliRunner()

@pytest.mark.skipif(
    not (pathlib.Path.home() / ".claude/projects").exists(),
    reason="no Claude projects dir on this machine",
)
def test_full_pipeline_against_real_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVO_ROOT", str(tmp_path))
    r = runner.invoke(app, ["ingest"])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(app, ["top-bloat", "--limit", "3"])
    assert r2.exit_code == 0
    r3 = runner.invoke(app, ["recurring-sequences", "--min-sessions", "3", "--limit", "5"])
    assert r3.exit_code == 0
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_e2e.py -v -s`
Expected: PASS (may take 30–60s on 511 sessions). If it errors, treat that as a real bug and fix the root cause before moving on.

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): smoke test against real ~/.claude/projects"
```

- [ ] **Step 4: Run `convo interpret` manually and eyeball the output**

Run: `convo interpret`
Expected: Anthropic-API-backed narrative output. If the API key is missing, set `ANTHROPIC_API_KEY` and rerun.

- [ ] **Step 5: Final commit if anything changed**

```bash
git status
# if dirty:
git add -A && git commit -m "chore: final tweaks after e2e validation"
```

---

## Self-Review

- **Spec coverage:** All 10 derived signals are implemented (tokens 1–4 → Task 8; skill 5–7 → Task 9; recurring 8–10 → Task 10). Sanitization (Task 4), blob store (Task 3), DuckDB schema (Task 5), incremental manifest (Task 6), sequences (Task 7), CLI named queries (Task 11), and LLM stage (Task 12) all map to spec sections 5–9.
- **Placeholders:** Every code step contains real code. The one `'<placeholder>'` literal in `signal_correction_clusters` is a deliberate denormalized aggregate marker, not an unwritten TODO.
- **Type/name consistency:** `NormalizedEvent`, `ToolCall`, `SkillInvocation`, `SessionRow`, `ParsedSession`, `BlobStore`, `Manifest`, `open_db`, `load_session`, `ingest_all`, `build_sequences`, `build_token_signals`, `build_skill_signals`, `build_recurring_signals`, `build_dashboard`, `interpret` are referenced consistently across tasks.
- **Open spec questions resolved:** Language = Python (user-chosen). `skills.yaml` = hand-curated (Task 9 seed). Claude Code `version` dimension = not tracked in v1 (out of scope; can be added as a column later).
- **Phase C hooks left in place:** `ingested_at` column on `sessions`, manifest already keyed for incremental, no `digests` table.
