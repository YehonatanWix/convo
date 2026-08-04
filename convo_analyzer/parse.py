from __future__ import annotations
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import NormalizedEvent, ToolCall, SessionRow, SkillInvocation

PROJECT_CWD_RE = re.compile(r"^-Users-[^-]+-(.+)$")
SLASH_CMD_RE = re.compile(r"^/([a-zA-Z0-9_\-:]+)\b")

SUBAGENT_DIRNAME = "subagents"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

@dataclass
class ParsedSession:
    session_id: str
    session: SessionRow
    events: list[NormalizedEvent] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    skills: list[SkillInvocation] = field(default_factory=list)

def _is_subagent_path(jsonl_path: pathlib.Path) -> bool:
    parents = jsonl_path.parents
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
    project_dir = jsonl_path.parents[2] if _is_subagent_path(jsonl_path) else jsonl_path.parent
    parent = project_dir.name
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
    parent_session_id = _subagent_parent_id(path)
    is_subagent = parent_session_id is not None
    session_id = path.stem
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
            text_head=text_payload[:240] if text_payload else None,
        )
        events.append(ev)
        if etype in ("user", "assistant"):
            msg_count += 1

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
                if tool_calls:
                    tool_calls[-1].result_size = max(tool_calls[-1].result_size, size)
                    tool_calls[-1].success = not bool(b.get("is_error"))

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
        parent_session_id=parent_session_id,
        is_subagent=is_subagent,
    )

    for sk in skills:
        idx = next((i for i, t in enumerate(tool_calls) if t.ts >= sk.ts), len(tool_calls))
        sk.followed_by_tools = [t.tool_name for t in tool_calls[idx:idx + 5]]

    return ParsedSession(
        session_id=session_id, session=sess,
        events=events, tool_calls=tool_calls, skills=skills,
    )
