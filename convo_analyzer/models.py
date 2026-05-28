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
