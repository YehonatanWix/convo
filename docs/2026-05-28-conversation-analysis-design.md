# Conversation Analysis — Design Spec

**Date:** 2026-05-28
**Status:** Draft, approved for plan-writing
**Scope:** Phase B (queryable corpus). Phase C (ongoing digest) is sketched but out of scope for first implementation.

---

## 1. Thesis

Agentic conversation logs are not chat logs; they are structured event streams that mostly answer themselves. The expensive question — "what did Claude do, with what tools, for how long, costing how many tokens, with what outcome" — is a join, not a summary.

An LLM should only be invoked at the very end of the pipeline, on already-aggregated signals, to (a) interpret patterns the scripts surfaced and (b) propose skills or edits. The bulk of the work — parsing, sanitizing, aggregating, sequence mining, anomaly flagging — is deterministic and belongs in scripts plus a columnar store. **The LLM reads the dashboard, not the raw logs.**

## 2. Goals

Help the user improve along three axes by making them queryable:

1. **Token efficiency** — where tokens are spent without proportional value (bloated tool results, redundant reads, compaction churn).
2. **Skill improvement** — which existing skills fire wrongly, get abandoned, or are missed entirely.
3. **Repeated actions → skill candidates** — tool sequences that recur across sessions and projects but never trigger a skill.

Non-goal: real-time / in-session intervention. This is post-hoc analysis.

## 3. Input Data

- **Location:** `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`
- **Scale:** ~511 sessions, ~87 MB total at time of design
- **Format:** JSONL, one event per line. Event types observed:
  - `user`, `assistant` messages (with `usage` block on assistant)
  - tool use / tool result
  - hook events, retries, errors
  - mode changes, snapshots, compaction
  - sidechain events (subagents)
- **Subagents:** sibling `subagents/` dir per session
- **Scope tagging:** every project's sessions are ingested; each row tagged with a `project` slug derived from `cwd`.

## 4. Architecture

```
JSONL files ──▶ [1. Parse]   ──▶ normalized events
                  │
                  ├─▶ [2. Sanitize]  ──▶ scrubbed events + blob store
                  │
                  ├─▶ [3. Derive]    ──▶ tool sequences, skill invocations,
                  │                       correction signals, token curves
                  │
                  └─▶ [4. Load]      ──▶ DuckDB (queryable corpus)
                                            ▲
                                            │ ad-hoc SQL / CLI / Claude-as-analyst
                                            │
                                       [5. LLM stage]  (only for interpretation)
```

Five stages, each a separate script. Each stage is idempotent and can be re-run independently against its input artifacts.

## 5. Storage

**Engine:** DuckDB. Reasons: columnar (great for the aggregation workload), zero-server, single-file, native Parquet ingest, SQL.

**Layout:**
- `corpus.db` — DuckDB file with all structured tables
- `blobs/<first2>/<hash>.txt` — content-addressed full bodies of tool results, large prompts, and message text. Referenced from rows by `blob_hash`. Sanitized before write.
- `manifest.json` — records last ingested timestamp per session_id for incremental re-ingest (forward-compatible with Phase C).

### 5.1 Tables

**`sessions`** — one row per conversation
| column | notes |
|---|---|
| `session_id` | uuid |
| `project` | slug derived from cwd |
| `cwd` | path-normalized (`~` instead of `/Users/...`) |
| `git_branch` | first observed value |
| `started_at`, `ended_at`, `duration_ms` | |
| `message_count` | |
| `total_input_tokens`, `total_output_tokens` | |
| `total_cache_read_tokens`, `total_cache_creation_tokens` | |
| `compaction_count` | |
| `model`, `ai_title` | |

**`events`** — one row per JSONL line (the spine)
| column | notes |
|---|---|
| `event_id` | uuid from source |
| `session_id`, `parent_uuid`, `ts` | |
| `type`, `subtype` | |
| `is_sidechain`, `is_meta`, `role` | |
| `input_tokens`, `output_tokens`, `cache_read`, `cache_creation` | nullable |
| `duration_ms` | nullable |
| `text_len` | length of text payload if any |
| `blob_hash` | nullable, references `blobs/` |

**`tool_calls`** — denormalized for fast analytics
| column | notes |
|---|---|
| `event_id`, `session_id`, `ts` | |
| `tool_name` | |
| `args_json` | truncated to 2 KB inline |
| `args_blob_hash` | full args if truncated |
| `result_size` | bytes |
| `result_blob_hash` | full result |
| `success`, `duration_ms`, `retry_attempt` | |
| `position_in_session` | ordinal, enables sequence joins |

**`skill_invocations`** — separated for axis #2
| column | notes |
|---|---|
| `session_id`, `ts`, `skill_name`, `args` | |
| `triggered_by` | `'user_slash'` or `'auto'` |
| `followed_by_tools` | array, first 5 tool names after invocation |

**`tool_sequences`** — precomputed n-grams (n = 2..5) per session
| column | notes |
|---|---|
| `session_id` | |
| `n` | length |
| `sequence` | array of tool names |
| `count` | times this n-gram occurs in the session |
| `first_ts`, `last_ts` | |

## 6. Sanitization (Stage 2)

Runs once during ingest, before any blob is written.

- Regex-strip: AWS keys, GitHub tokens (`ghp_`, `gho_`, `ghs_`), bearer tokens, common `.env` line patterns, JWT-shaped strings.
- Truncate `args_json` and message text to a configurable size before hashing — same secret across sessions should not produce a stable hash usable as a sidechannel.
- Path normalization: replace `/Users/yehonatana/` with `~` consistently in all paths.
- Optional `--paranoid` flag: an extra LLM-based PII pass on blobs. Off by default.

Property: the on-disk store is already clean. No "raw vs scrubbed" duality.

## 7. Derived Signals (Stage 3)

Each is a deterministic script writing to a table or column. These do the analytical work *before* the LLM is invoked.

### 7.1 Token efficiency
1. **Tool result bloat ratio** — `result_size / max(output_tokens_in_next_turn, 1)`. High = pulled a huge result and barely used it.
2. **Compaction proximity tokens** — tokens in the last N turns before a compaction event. Identifies budget killers.
3. **Redundant reads** — same file `Read` twice in one session without an intervening `Edit`.
4. **Oversized agent dispatches** — `Agent` calls with short prompt but huge return blob and no follow-up edits.

### 7.2 Skill improvement
5. **Skill-eligible-but-missed** — tool sequences matching a curated `skills.yaml` signature but no `Skill` invocation present.
6. **Skill-fired-then-abandoned** — `Skill` invocation followed within 3 turns by a user correction (`^(no|don't|stop|wait|actually)`).
7. **Skill turnaround cost** — tokens between `Skill` invocation and next user message. Lets you rank skills by overhead.

### 7.3 Repeated actions → skill candidates
8. **Cross-session recurring sequences** — group `tool_sequences` by `sequence`, count distinct `session_id`. Threshold: ≥5 sessions across ≥3 projects with no existing skill match → candidate.
9. **User-correction clusters** — short user messages matching the correction regex, indexed with preceding 3 tool calls. Same correction across many sessions = missing guardrail.
10. **Repeated path-fixing / convention violations** — same `cd` mistakes, same `yarn` vs `npm` corrections, same import-style edits. Detect via Levenshtein on consecutive `Edit` `old_string`s.

Each signal lands in either a new column on an existing table or a dedicated derived table (e.g. `signal_bloat`, `signal_recurring_sequences`).

## 8. Query Interface (Stage 4)

Three layers, increasing in cognitive cost:

1. **`convo` CLI** — thin wrapper over DuckDB with named queries:
   - `convo top-bloat --since 30d`
   - `convo recurring-sequences --min-sessions 5 --no-skill`
   - `convo skill-health <name>`
   - `convo session <id>` — pretty-print a single session timeline
2. **Raw SQL** — `duckdb corpus.db`
3. **Claude-as-analyst** — future Claude sessions invoke `convo` from Bash; the corpus becomes a tool, not a context dump.

## 9. LLM Stage (Stage 5)

Small, deliberate, last. The LLM writes the narrative — it does not read raw conversations.

Inputs:
- Top 20 of each derived signal.
- 5–10 representative blobs (fetched by hash, fully sanitized).
- Per-axis prompt template (e.g. "here are top 20 recurring sequences with no skill — for each, propose: is this skill-worthy? proposed name? trigger pattern?").

Cost ceiling: a full pass should be <50K input tokens. Section 7 already did the reduction; the LLM interprets a pre-built dashboard.

## 10. Phase C Roadmap (out of scope for first implementation)

The B→C path is small once B exists:
- `ingested_at` on every table; ingest becomes incremental.
- `digests` table snapshotting key metrics weekly.
- `convo digest --week` diffs latest week vs prior 4-week baseline, runs the LLM stage only on deltas.
- Optionally wire to cron and write the digest to a markdown file (e.g. Obsidian vault).

## 11. Open Questions

- Final choice of language for the scripts (Python with `duckdb` + `pydantic` is the obvious default; Node also viable since the rest of the user's tooling is JS-heavy).
- Whether `skills.yaml` (signature mapping for signal #5) is hand-curated or bootstrapped from observed `Skill` invocations.
- Whether to track Claude Code version (`version` field is present on events) as a dimension — useful if behaviors drift across releases.

## 12. Non-Goals

- Real-time intervention during a live session.
- Multi-user / shared corpus.
- A web UI. CLI + SQL is the interface.
- Replacing memory or CLAUDE.md — this is observational, not prescriptive.
