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
SELECT
    e.ts,
    e.type,
    e.role,
    tc.tool_name,
    e.output_tokens,
    e.duration_ms,
    e.text_len,
    e.text_head,
    COALESCE(e.blob_hash, tc.result_blob_hash, tc.args_blob_hash) AS blob_hash
FROM events e
LEFT JOIN tool_calls tc USING (event_id, session_id)
WHERE e.session_id = ?
ORDER BY e.ts
"""

# Housekeeping event types that carry no analytical content; hidden unless --verbose.
SESSION_TIMELINE_NOISE_TYPES = (
    "permission-mode",
    "file-history-snapshot",
    "last-prompt",
    "attachment",
)

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
