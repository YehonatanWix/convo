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

1. **Triage**: for each candidate, read its `followups` and decide:
   - REAL_FAILURE — the user interrupted/redirected the skill
   - NOT_FAILURE — the follow-up is unrelated, a routine continuation, or the
     skill clearly succeeded (e.g. user said "great", "thanks", or moved on to a
     new unrelated task)
   - UNCLEAR — need to read the raw JSONL to decide
   For UNCLEAR cases, open the `jsonl_path` and read the surrounding events.

2. **Cluster** the REAL_FAILURE cases by the underlying problem (e.g. "skill ran
   without asking a clarifying question", "skill produced output in wrong format",
   "skill picked the wrong sub-routine"). One failure mode per cluster.

3. **For each failure mode**, report:
   - Name and short description
   - Count + 2-3 representative `session_id`s with brief quoted evidence
   - Why the skill failed (root cause, not symptom)
   - Concrete proposed improvement to the skill: SKILL.md edit, new trigger,
     missing pre-flight question, etc. Be specific enough that I can apply it.

Be terse. Quote evidence. Skip categories with <2 occurrences.
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


def build_investigation(
    db_path: pathlib.Path,
    skill: str,
    projects_root: pathlib.Path,
    window: int = 5,
) -> list[dict]:
    """Return one record per invocation of `skill`, with full message text."""
    con = duckdb.connect(str(db_path), read_only=True)
    invs = con.execute("""
        SELECT si.session_id, si.ts, s.project, s.cwd
        FROM skill_invocations si
        JOIN sessions s USING (session_id)
        WHERE si.skill_name = ?
        ORDER BY si.ts
    """, [skill]).fetchall()

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


def find_skill_files(
    skill_name: str,
    extra_roots: list[pathlib.Path] | None = None,
) -> list[pathlib.Path]:
    """Find SKILL.md files for `skill_name` under ~/.claude plus any extra roots.

    Plugin-namespaced names like `superpowers:brainstorming` match the suffix
    (`brainstorming`). Multiple matches are possible (e.g. user skill + plugin
    skill + project-local skill of the same name).

    `extra_roots` are typically project directories — we search both `<root>` and
    `<root>/.claude` so callers can pass either form.
    """
    suffix = skill_name.split(":", 1)[-1]
    roots: list[pathlib.Path] = [pathlib.Path.home() / ".claude"]
    for r in extra_roots or []:
        r = pathlib.Path(r).expanduser()
        roots.append(r)
        if (r / ".claude").exists():
            roots.append(r / ".claude")
    found: set[pathlib.Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("SKILL.md"):
            if p.parent.name == suffix:
                found.add(p)
    return sorted(found)


def _jsonl_path_for(projects_root: pathlib.Path, cwd: str, session_id: str) -> pathlib.Path:
    """Reverse the encoded-cwd directory naming used by Claude Code."""
    raw_cwd = cwd.replace("~", str(pathlib.Path.home()), 1) if cwd.startswith("~") else cwd
    encoded = raw_cwd.replace("/", "-")
    return pathlib.Path(projects_root) / encoded / f"{session_id}.jsonl"


def write_investigation(
    db_path: pathlib.Path,
    out_dir: pathlib.Path,
    skill: str,
    projects_root: pathlib.Path,
    window: int = 5,
) -> dict:
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_path = out_dir / f"skill-{skill}-candidates.json"
    prompt_path = out_dir / f"skill-{skill}-prompt.md"

    records = build_investigation(db_path, skill, projects_root, window=window)
    packet_path.write_text(json.dumps(records, indent=2))

    candidate_cwds: list[pathlib.Path] = []
    seen_cwds: set[str] = set()
    for r in records:
        # records carry jsonl_path under ~/.claude/projects; the original cwd is
        # encoded in its parent dir name. Reconstruct it.
        encoded = pathlib.Path(r["jsonl_path"]).parent.name
        raw = "/" + encoded.lstrip("-").replace("-", "/")
        if raw not in seen_cwds:
            seen_cwds.add(raw)
            candidate_cwds.append(pathlib.Path(raw))
    skill_paths = find_skill_files(skill, extra_roots=candidate_cwds)
    if skill_paths:
        skill_locations = "The skill's SKILL.md file(s):\n" + "\n".join(
            f"  - {p}" for p in skill_paths
        )
    else:
        skill_locations = (
            f"No SKILL.md found for `{skill}` under ~/.claude. The skill may be "
            "user-typed slash text without a backing file; treat it as an alias."
        )

    prompt = PROMPT_TEMPLATE.format(
        skill=skill,
        packet_path=packet_path.resolve(),
        window=window,
        n_candidates=len(records),
        cwd=os.getcwd(),
        skill_locations=skill_locations,
    )
    prompt_path.write_text(prompt)
    return {
        "packet_path": packet_path,
        "prompt_path": prompt_path,
        "prompt": prompt,
        "n_candidates": len(records),
    }
