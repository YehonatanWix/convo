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
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS parent_session_id TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_subagent BOOLEAN DEFAULT FALSE;

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
  text_head TEXT,
  blob_hash TEXT
);
ALTER TABLE events ADD COLUMN IF NOT EXISTS text_head TEXT;

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
