# Roadmap

## Planned

**Phase 1 — deferred until the two-week trial reports.** Phase 0 (below, shipped 2026-08-21) closed
the correctness gate; nothing in Phase 1 starts until the trial's `feedback` table (via `/wrong`)
says what actually matters. Full list and promotion triggers in `docs/IMPLEMENTATION_PLAN.md`'s
Phase 1 table: the deal-pipeline decision (use it, redesign it, or remove it — a product question,
not a bug), removing the now-dead `get_stale_deals`, a general test suite (only after WS-19 proves
the pattern), morning briefing v2 and reminders v2 (only if Joey misses them), and ingestion tuning
(only if `/wrong` points at document reads).

**Until then: two weeks of use with no development.**

---

## Removed

### Follow-up reminders ✗ — removed 2026-08-20 (`12cb981`)

`set_reminder` / `list_reminders` / `delete_reminder`, the 60-second JobQueue checker, and the
`reminders` table were removed from the codebase. The table itself is left in place on any existing
`dot.db` (not dropped), so no data was lost.

### Morning briefing ✗ — removed 2026-08-20 (`12cb981`)

The daily 08:00 proactive digest (calendar, unread email, reminders due, stale deal alerts, news via
web search) was removed, along with the "Stale deal alerts in morning briefing" section that shipped
2026-06-24. `get_stale_deals` in `memory.py` is now dead code with no callers.

> Both entries were listed under "Shipped" here until 2026-08-20, days after the code was deleted.
> Corrected during the 2026-08-20 evening review (F-39). The removed briefing is also the feature
> that produced the captured four-redundant-drafts output analysed in F-33 — do not rebuild it on
> spec.

---

## Shipped

### Phase 0 correctness gate ✓ — 2026-08-21 (WS-14 to WS-19)

Six workstreams, planned in `docs/IMPLEMENTATION_PLAN.md`'s 2026-08-20 evening review (findings
F-29 to F-41) and shipped as one commit each. Nothing here is a new feature — every item removes a
wrong output, prevents silent data loss, or captures evidence, per that review's explicit Phase-0
rule. The trigger was Joey stopping use on Aug 7 after telling Dot three times to stop prepping a
personal event and being told "noted" each time while it kept firing anyway.

- **WS-14** — Meeting prep now fires only on real, timed, external meetings: a real
  "starts-within-window" check (Google Calendar's `timeMin`/`timeMax` is an overlap filter, not a
  starts-within filter — an all-day event previously matched every 5-minute tick for its whole
  duration), a persistent `prep_mutes` table + `mute_meeting_prep` tool so "stop prepping X" actually
  sticks and survives restarts, and an output-side SKIP gate where the prep call itself can decline
  to send and auto-mute the event. This is the fix for the Aug 7 failure — verified live: the model
  now calls `mute_meeting_prep` in the same turn Joey says to stop, and a personal-event prompt gets
  back exactly `SKIP`.
- **WS-15** — Explicit `thinking` parameter on every one-shot Claude call (adaptive for the
  interactive main-loop and prep calls, disabled for pure extraction) plus real headroom in
  `max_tokens`, so reasoning can no longer squeeze out the visible answer. A truncated or
  thinking-only response now logs loudly (`response_text_checked`) instead of silently producing
  zero facts or wiping conversation history.
- **WS-16** — A relevance floor on memory retrieval (measured live: conversational/instructional
  turns like "ok thanks" used to inject the full top-15 regardless of fit; now inject nothing), plus
  a separate `bulk_records` Chroma collection for tabular spreadsheet rows (7,197 rows across two
  files, not passively injected) reachable via a new `search_deal_database` tool.
- **WS-17** — `doc_cache` now stores the complete document parse instead of the old 15,000-char
  ingest truncation, so the paging marker on long documents works again. Backfilled the 16 rows that
  were stuck at exactly 15,000 chars (one grew to 250,206).
- **WS-18** — Daily WAL-safe backups of `dot.db` + `sessions/` (offsite copy to Dropbox), tested with
  a real backup run and a real restore rehearsal into a scratch directory; an outage-scoped alert so
  a credit/auth failure DMs once instead of the 320-calls/zero-messages pattern from mid-August; and
  `/wrong` for logging bad outputs during the trial (no API call, so it works even when credits are
  out).
- **WS-19** — `tests/test_gate.py` (`venv/bin/python -m pytest tests/ -q`) — the repo's first test
  file, covering only the Phase-0 logic above: event classification, mute matching, the relevance
  floor, empty-response detection, and the paging-marker formula. 15 tests, all passing.

### Document read cache + verbatim deck reads ✓ — 2026-08-20 (WS-10 to WS-13)

A `doc_cache` table in `dot.db` keyed on stable file identity (Dropbox file `id` + `content_hash`,
Drive `fileId` + `version`) so a document parsed once is not re-parsed on every live read.
`read_dropbox_file`/`read_drive_file` check the cache before downloading, backfill it on any miss,
and take an optional `offset` argument to page past the 3,000-char return cap. Ingestion populates
the cache for free on its existing text-extraction path. Image-only PDFs (designed decks with no
text layer) get a full markdown transcription: at ingest time for anything dropped in `/Dot Dump`,
or live — one-shot, cached thereafter, ~20-60s and ~$0.10-0.20 the first time — for any image-only
PDF read via `read_dropbox_file`/`read_drive_file` that was never ingested (most of Joey's Dropbox,
and all of Drive, since Drive files are never ingested). Reviewed and planned in
`docs/IMPLEMENTATION_PLAN.md` (WS-10 to WS-13, findings F-21 to F-27); all three product decisions
(D-7 full transcription at ingest, D-8 offset paging, D-9 gated live vision fallback) confirmed by
Joey. WS-13 additionally covers Drive reads (`read_drive_file`) with the same live-vision fallback
as Dropbox, even though the workstream's own steps only spelled out the Dropbox path in detail —
the workstream's stated goal ("most of Joey's Dropbox and all of Drive") and F-25's observation
that Drive is never ingested made the Drive gap worth closing in the same pass.

### Google Drive access ✓ — 2026-07-07

`search_drive` and `read_drive_file` tools mirror the Dropbox pair using the `drive.readonly` scope. Supports Google Docs, Sheets, and Slides (exported as text), plus PDF, DOCX, TXT, MD, and CSV files stored in Drive. Useful for documents and shared files that live in Drive rather than Dropbox.

### Gmail drafts ✓ — 2026-06-11

`create_gmail_draft` tool creates a draft for Joey to review and send — never auto-sends. Reads thread context first so the draft has full background. Requires `gmail.compose` OAuth scope (re-run `auth_work.py` after deleting `token_work.pickle`).

### Structured deal tracking ✓ — 2026-06-11

Lightweight CRM in `dot.db`: company, stage (`sourcing` → `first_call` → `due_diligence` → `passed` / `invested`), last touchpoint, next action, notes. Three tools: `update_deal`, `get_deal_info`, `list_deals`. Queryable in natural language.

> **Annotation (2026-08-20, F-38):** `SELECT COUNT(*) FROM deals` returns **0**. The tools work but
> have never been used, so everything built on top of them — including the fact-tagging below — has
> been inert since it shipped. Whether to use, redesign, or remove this is a product question
> deferred to Phase 1.

### Voice messages ✓ — 2026-06-11

Telegram voice notes transcribed locally with Whisper (`tiny` model, ~75 MB, CPU-only, no API cost). Transcript echoed back in italics then passed to the existing agent loop. Requires `ffmpeg` system package and `openai-whisper` Python package.

### Meeting prep brief ✓ — 2026-06-24

JobQueue job runs every 5 minutes. When a calendar event with external attendees is 25–35 minutes away, Dot sends a prep brief pulling from Granola (previous call notes) and Gmail (recent threads). Deduped by event ID across the session.

> **Correction (2026-08-20):** as shipped, this is unreliable and is the reason usage stopped on
> Aug 7. Google Calendar's `timeMin`/`timeMax` is an *overlap* filter, not "starts within," so an
> all-day event matches every 5-minute tick for its whole duration; the "external attendee" check
> only tests work-domain email, so a family member on a personal address counts the same as a
> founder; the dedup entries expire after 2 hours, so long events re-fire all day; and nothing said
> in Telegram reaches the job at all — it reads no table, session, or memory, so "Got it, noted"
> was never backed by anything. Findings F-29 to F-32; fixed by WS-14.

### WhatsApp forwarding via `/log` ✓ — 2026-06-24

`/log <text>` accepts a pasted note or forwarded WhatsApp conversation. Claude extracts self-contained facts and saves them to memory. Fills the gap for communication that happens outside email.

### Deal + memory auto-linking in ingest ✓ — 2026-06-24 (tag made real 2026-07-21, WS-9)

When `ingest.py` extracts facts from a document, it checks each fact against active deal company names and tags matching memories with `deal:<company>`. `get_deal_info` now surfaces the 10 most recent ingested-document facts tagged for that company under a "From ingested documents" section, so deal lookups get progressively richer as documents are ingested — closing the gap flagged in the 2026-07-07 review (F-15).

> **Annotation (2026-08-20, F-38):** the mechanism is correct but has never fired. With `deals`
> empty, `list_deals()` returns nothing for `ingest.py` to match against, so none of the 211
> ingested files has ever received a `deal:<company>` tag. "Progressively richer" remains
> aspirational, now for a data reason rather than a code one.

### Named conversation sessions ✓ — 2026-06-26

`/switch <name>` saves the current conversation and loads (or creates) a named session stored in `sessions/<name>.json`. `/sessions` lists all conversations with message counts. Existing `session.json` auto-migrates to `sessions/default.json` on first startup. Typing `/` in Telegram now shows all commands and descriptions (registered via `set_my_commands` on startup).

### Web conversation viewer ✓ — 2026-06-26

`web.py` is a FastAPI app (port 8080) that shows all conversation sessions in a read-only chat UI. Password-protected via `WEB_SECRET` in `.dot.env`. Auto-refreshes every 5 seconds. Designed for access over Tailscale — private, no open ports, no firewall rules. Run as a separate `web.service` systemd unit.

### Remote restart + reply context + briefing fixes ✓ — 2026-07-01

**`/restart` command:** runs `git pull --ff-only` in the repo, replies with the pull result, then calls `sys.exit(0)`; systemd's `Restart=always` brings the bot back up in ~10 seconds. No sudo, no terminal needed — deploy code changes from anywhere.

> **Correction (2026-07-21):** until this date, `/restart` only called `sys.exit(0)` — it never pulled from git, so it restarted whatever code was already on disk and did *not* deploy pushed commits. The `git pull --ff-only` step above was added to make the original claim true.

**Telegram reply context:** when you use Telegram's reply feature on a message, the quoted text is prepended to your input so Dot knows what you're referencing without having to search back through the conversation.

**Web search `container_id` fix:** the `web_search_20260209` tool runs server-side in an Anthropic container. The response includes a `container_id` that must be echoed on all subsequent calls in the same turn — missing it caused a 400 on every search. All three API loops (main agent, morning briefing, meeting prep) now thread `container_id` through correctly.

**History rollback on error:** if an API call fails mid-turn, `conversation_history` is restored from the last clean on-disk save. Previously, a failed turn left a partial assistant message in memory; the next user message would cause Claude to continue the half-written thought before answering.

**Briefing prompt hardening:** instructions are now numbered and labelled non-negotiable. The first rule requires the response to start with the first section header — no preamble. The news rule explicitly forbids including stories from previous days and prescribes "Nothing notable today" when nothing is fresh.
