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
