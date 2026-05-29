# Structured failure-mode report + HTML view

**Date:** 2026-05-29
**Status:** Approved, ready for implementation planning

## Problem

`convo investigate-skill <name>` launches a Claude Code session that triages every
invocation of a skill, clusters the failures, and produces a failure-mode report.
Today that report is **prose printed into the conversation** — ephemeral, and a human
cannot easily separate the *real* improvement points from weak or noisy ones.

We want the agent to **save the report in a structured form** and render it as a
**read-only HTML page** a human can scan to judge real-vs-noise.

## Goals

- Agent persists the report as structured data, not prose.
- A human opens a self-contained dark-themed HTML page and, at a glance, can tell a
  strong improvement point from a weak one.
- Rendering is deterministic and unit-testable; the agent never writes markup.

## Non-goals

- No interactivity / no persisted human verdicts (read-only view only).
- No tracking of failure modes across runs.
- No new wiring into `interpret`'s dashboard yet (renderer is built generic in shape
  but only `investigate-skill` uses it — YAGNI).

## User-facing flow

1. `convo investigate-skill <name>` launches `claude` (unchanged).
2. The agent triages → clusters (unchanged through Step 2).
3. **Step 3 changes:** instead of prose, the agent writes a structured
   `analysis/skill-<name>-report.json`, then runs `convo report <name>`.
4. `convo report <name>` reads the JSON and writes
   `analysis/skill-<name>-report.html` — a self-contained, dark, two-pane page.
5. The human opens the HTML in a browser.

## UI: two-pane, dark

- **Left rail:** one row per failure mode, sorted strongest-first. Each row: count
  badge (color = evidence strength: red=strong, orange=moderate, grey=weak), mode
  name, and a "strong/moderate/weak evidence" sublabel. A header strip shows skill
  name, totals (invocations · real failures · modes), and generation date.
- **Detail pane** (selected mode): name; tags (evidence strength · occurrences;
  proximate-cause); **Root cause**; **Why root not symptom**; **Evidence** as
  representative user quotes, each with session id + date + a copyable
  `convo session <id>` command; **Proposed improvement** as a before → after diff of
  the SKILL.md text.
- **Dismissed section:** a collapsed area listing weak / UNCLEAR clusters the agent
  considered but did not promote, with a one-line reason each — so the human sees
  what was filtered out.

Mockups validated in the brainstorming visual companion (two-pane, dark theme).

## Components

| Unit | Responsibility |
|---|---|
| `convo_analyzer/investigate.py` (edit) | Rewrite Step 3 of `PROMPT_TEMPLATE`: emit the report JSON to the fixed path, then run `convo report <name>`. Add the JSON schema and a confidence rubric to the prompt text. |
| `convo_analyzer/report.py` (new) | `load_report(json_path) -> dict` (validate required keys), `render_html(report) -> str` (self-contained dark two-pane HTML with data inlined). No side effects beyond reading the given JSON. No network, no DB. |
| `convo_analyzer/cli.py` (edit) | New `convo report <name>` command: read `analysis/skill-<name>-report.json`, write `analysis/skill-<name>-report.html`. |
| `tests/test_report.py` (new) | Unit tests: validation, render contains required fields, dismissed section, before/after diff, pure-addition (empty `before`), malformed/missing JSON errors. |

## Schema: `analysis/skill-<name>-report.json`

```jsonc
{
  "skill": "code-review",
  "generated": "2026-05-29",
  "totals": { "invocations": 27, "real_failures": 9, "modes": 4 },
  "modes": [
    {
      "name": "No severity / blocking labels",
      "confidence": "strong | moderate | weak",   // agent-assigned per rubric
      "count": 8,
      "proximate_cause": "missing-output-spec",     // clustering tag
      "root_cause": "Output lists findings flat with no severity marking…",
      "why_root_not_symptom": "The redirects are about ranking, not wording…",
      "evidence": [
        { "quote": "which of these are actually blocking?",
          "session_id": "4a91…", "ts": "2026-04-12",
          "open": "convo session 4a91…" }
      ],
      "fix": {
        "summary": "Add a severity-tagging output spec.",
        "skill_path": "/Users/…/code-review/SKILL.md",
        "before": "",                 // current excerpt, or empty if pure addition
        "after":  "<proposed text>"
      }
    }
  ],
  "dismissed": [
    { "name": "Over-long preamble", "count": 1, "reason": "single occurrence, weak signal" }
  ]
}
```

### Required vs optional

- **Required top-level:** `skill`, `generated`, `totals`, `modes`.
- **Required per mode:** `name`, `confidence`, `count`, `root_cause`, `evidence`, `fix`.
- **Optional:** `proximate_cause`, `why_root_not_symptom`, `fix.before`, `dismissed`.
  Renderer handles empties gracefully (e.g. empty `before` → "pure addition").

## Confidence rubric (added to the prompt)

The agent assigns `confidence` per mode:

- **strong** — ≥4 occurrences with unambiguous corrective quotes.
- **moderate** — 2–3 occurrences, or ≥4 with some hedged/ambiguous evidence.
- **weak** — 2 occurrences with hedged or UNCLEAR-derived evidence.

Confidence drives badge color and the strongest-first sort order. Clusters below the
"weak" bar are not promoted to `modes`; they go to `dismissed`.

## Rendering decisions

- **Self-contained:** renderer inlines the JSON into a `<script>` block and ships the
  dark two-pane CSS/JS in one `.html` file — portable, no server required.
- **Raw session link:** page is static, so "open" is a copyable
  `convo session <id>` command string plus the session id — not a hyperlink.
- **before/after:** rendered as a two-column diff inside the `fix` box; empty `before`
  renders as a pure addition.
- **dismissed:** collapsed section so it does not compete with the promoted modes.

## Error handling

- `convo report` errors clearly when the JSON file is missing, is not valid JSON, or
  is missing a required top-level key.
- Optional fields render gracefully when absent or empty.
- Renderer performs no network or DB access — it reads only the one JSON file.

## Testing

- TDD for `report.py`: write `tests/test_report.py` first.
  - `load_report` rejects missing required keys with a clear error; accepts a valid doc.
  - `render_html` output contains: each mode name, the totals, evidence quotes,
    the `convo session` command, the before/after, and the dismissed entries.
  - Empty `before` renders a pure-addition fix without error.
  - Malformed JSON → clear error from the CLI command.
