"""Build an evidence packet + prompt for deep-dive analysis of one skill."""
from __future__ import annotations
import json
import os
import pathlib
import duckdb

from .sanitize import scrub

PROMPT_TEMPLATE = """You are investigating failure modes of the `{skill}` skill in my
Claude Code conversation history. The pipeline has already located every invocation
of this skill and gathered the user messages that followed each one.

## The skill itself

{skill_locations}

Read the skill's SKILL.md before judging — proposed improvements should be
edits you could apply to that exact file.

## Failure mode definition

A failure mode is defined as: the skill was invoked to do a task, then within the
next few messages the user interrupted or had to redirect/correct the skill's
guidance. Examples include corrections ("no", "stop", "actually", "wait", "don't"),
restating the task differently, asking for a do-over, or providing missing context
that the skill should have asked for itself.

## Evidence packet

The candidates are in:
  {packet_path}

Each entry is one skill invocation, with:
- `session_id`, `invocation_ts`, `project`
- `jsonl_path` — absolute path to the raw session file
- `invocation_text` — the user message (slash-command) or assistant tool_use that
  invoked the skill, full text
- `followups` — the next up to {window} user (non-meta) messages after the
  invocation, full sanitized text (no regex prefilter applied; you decide what
  counts as a failure mode)

Some followups will be wrappers, not real user input — `/exit`, `/clear`, other
slash-only commands, or `<local-command-caveat>...` blocks. Skip those when
judging failure modes; they're harness noise, not interruptions.

Total candidates: {n_candidates}

## Tools available to you (all via Bash, run from `{cwd}`)

  convo session <session_id>    # printable timeline for one session
  convo sql "<SELECT ...>"      # read-only DuckDB queries (events, tool_calls, ...)
  convo blob <hash>             # fetch a stored full body
  jq / grep / Read on `jsonl_path` for the raw conversation

The `events` table has a `text_head` column (first 240 chars of any user/assistant
text). The full bodies live in the raw JSONL.

## Your task

### Step 1 — Triage via subagents (do NOT do this yourself)

Reading every candidate and raw JSONL in *your* context window would bloat it.
Instead, dispatch triage subagents in batches of {batch_size} candidates each.
Run batches in parallel where possible (one Agent tool call per batch, all in a
single message).

For each batch, pass the subagent:
- The exact slice of candidates from `{packet_path}` (their full JSON entries —
  copy them inline so the subagent doesn't need to read the file)
- The failure-mode definition (above)
- Permission to open `jsonl_path` with Read / Bash+jq when followups are ambiguous

Tell the subagent to return ONLY a JSON array, no prose, with this exact schema
per candidate:

```json
{{
  "session_id": "...",
  "verdict": "REAL_FAILURE" | "NOT_FAILURE" | "UNCLEAR",
  "one_line_reason": "...",          // <= 120 chars, why this verdict
  "key_quote": "...",                // <= 200 chars, verbatim user text that drove the verdict (empty for NOT_FAILURE)
  "proximate_cause": "..."           // <= 80 chars, for REAL_FAILURE only: short tag like "missing-clarifying-question", "wrong-format", "wrong-subroutine" — your best guess, used for clustering. Empty otherwise.
}}
```

Use this exact dispatch prompt template for each triage subagent (fill in the
batch slice):

> You are triaging candidates for failure modes of the `{skill}` skill. A
> failure mode = the user interrupted, corrected, or redirected the skill
> within the next few messages after invocation. NOT a failure mode: routine
> continuation, user thanked the assistant, user moved to an unrelated task,
> followup is harness noise (`/exit`, `/clear`, `<local-command-caveat>`).
>
> Candidates (full JSON):
>
> ```json
> <PASTE BATCH HERE>
> ```
>
> For each candidate: read its `followups`. If ambiguous, open `jsonl_path`
> and read surrounding events. Return ONLY a JSON array matching this schema
> (no prose, no markdown fence): {{schema described above}}. One object per
> input candidate, same order.

Collect every subagent's JSON output. Keep only REAL_FAILURE + UNCLEAR verdicts
in your own context — discard NOT_FAILURE entries entirely.

### Step 2 — Cluster

Group the surviving verdicts by `proximate_cause`. Merge near-duplicate tags.
One failure mode per cluster. For UNCLEAR cases, decide now based on the
subagent's `key_quote` + `one_line_reason`; only re-open raw JSONLs yourself if
a cluster's evidence is too thin to act on.

### Step 3 — Report

For each failure mode (skip clusters with <2 occurrences):
- Name and short description
- Count + 2-3 representative `session_id`s with the subagent's `key_quote`
- Why the skill failed (root cause, not symptom)
- Concrete proposed improvement to the skill: SKILL.md edit, new trigger,
  missing pre-flight question, etc. Specific enough that I can apply it.

Be terse. Quote evidence from `key_quote` fields. Do not re-read candidate
followups yourself — trust the subagents' triage unless a cluster looks wrong.
"""

def _extract_user_messages(jsonl_path: pathlib.Path) -> list[dict]:
    """Yield each non-meta user message with its full text, in order."""
    out: list[dict] = []
    if not jsonl_path.exists():
        return out
    with jsonl_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if raw.get("type") != "user" or raw.get("isMeta"):
                continue
            msg = raw.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                text = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            elif isinstance(content, str):
                text = content
            else:
                text = ""
            if not text.strip():
                continue
            out.append({"ts": raw.get("timestamp") or "", "text": text})
    return out


def _extract_invocation_text(jsonl_path: pathlib.Path, ts: str) -> str:
    """Find the event at `ts` that invoked the skill and return its text/args."""
    if not jsonl_path.exists():
        return ""
    with jsonl_path.open() as fh:
        for line in fh:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if raw.get("timestamp") != ts:
                continue
            msg = raw.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                parts = []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        parts.append(b.get("text", ""))
                    elif b.get("type") == "tool_use" and b.get("name") == "Skill":
                        parts.append("[Skill tool_use] " + json.dumps(b.get("input") or {}))
                return "\n".join(parts)
            if isinstance(content, str):
                return content
    return ""


def skill_name_from_path(skill_path: pathlib.Path) -> str:
    """Derive the canonical skill name from a SKILL.md path.

    Prefers the `name:` field in the YAML frontmatter; falls back to the
    parent directory name.
    """
    parent = skill_path.parent.name
    try:
        text = skill_path.read_text()
    except OSError:
        return parent
    if not text.startswith("---"):
        return parent
    _, _, rest = text.partition("---\n")
    front, _, _ = rest.partition("\n---")
    for line in front.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return parent


def build_investigation(
    db_path: pathlib.Path,
    skill: str,
    projects_root: pathlib.Path,
    window: int = 5,
) -> list[dict]:
    """Return one record per invocation of `skill`, with full message text.

    Matches both bare (`brainstorming`) and plugin-namespaced
    (`superpowers:brainstorming`) forms of the skill name.
    """
    suffix = skill.split(":", 1)[-1]
    con = duckdb.connect(str(db_path), read_only=True)
    invs = con.execute("""
        SELECT si.session_id, si.ts, s.project, s.cwd
        FROM skill_invocations si
        JOIN sessions s USING (session_id)
        WHERE si.skill_name = ?
           OR si.skill_name = ?
           OR si.skill_name LIKE '%:' || ?
        ORDER BY si.ts
    """, [skill, suffix, suffix]).fetchall()

    records: list[dict] = []
    for sid, ts, project, cwd in invs:
        jsonl = _jsonl_path_for(projects_root, cwd, sid)
        invocation_text = scrub(_extract_invocation_text(jsonl, ts))
        all_user = _extract_user_messages(jsonl)
        after = [m for m in all_user if m["ts"] > ts][:window]
        followups = [{"ts": m["ts"], "text": scrub(m["text"])} for m in after]
        # If the very next user action is /exit, the session ended with no
        # opportunity for a failure mode to manifest. Drop the candidate.
        if followups and _is_exit(followups[0]["text"]):
            continue
        records.append({
            "session_id": sid,
            "invocation_ts": ts,
            "project": project,
            "jsonl_path": str(jsonl),
            "invocation_text": invocation_text,
            "followups": followups,
        })
    return records


def _is_exit(text: str) -> bool:
    return "<command-name>/exit</command-name>" in (text or "")


def _jsonl_path_for(projects_root: pathlib.Path, cwd: str, session_id: str) -> pathlib.Path:
    """Reverse the encoded-cwd directory naming used by Claude Code."""
    raw_cwd = cwd.replace("~", str(pathlib.Path.home()), 1) if cwd.startswith("~") else cwd
    encoded = raw_cwd.replace("/", "-")
    return pathlib.Path(projects_root) / encoded / f"{session_id}.jsonl"


def write_investigation(
    db_path: pathlib.Path,
    out_dir: pathlib.Path,
    skill_path: pathlib.Path,
    projects_root: pathlib.Path,
    window: int = 5,
    batch_size: int = 5,
) -> dict:
    skill_path = pathlib.Path(skill_path).expanduser().resolve()
    skill = skill_name_from_path(skill_path)
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = skill.replace(":", "-").replace("/", "-")
    packet_path = out_dir / f"skill-{safe}-candidates.json"
    prompt_path = out_dir / f"skill-{safe}-prompt.md"

    records = build_investigation(db_path, skill, projects_root, window=window)
    packet_path.write_text(json.dumps(records, indent=2))

    skill_locations = f"The skill's SKILL.md file:\n  - {skill_path}"

    prompt = PROMPT_TEMPLATE.format(
        skill=skill,
        packet_path=packet_path.resolve(),
        window=window,
        n_candidates=len(records),
        cwd=os.getcwd(),
        skill_locations=skill_locations,
        batch_size=batch_size,
    )
    prompt_path.write_text(prompt)
    return {
        "skill": skill,
        "skill_path": skill_path,
        "packet_path": packet_path,
        "prompt_path": prompt_path,
        "prompt": prompt,
        "n_candidates": len(records),
    }
