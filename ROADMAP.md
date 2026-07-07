# Roadmap

## Planned

---

## Shipped

### Google Drive access ✓ — 2026-07-07

`search_drive` and `read_drive_file` tools mirror the Dropbox pair using the `drive.readonly` scope. Supports Google Docs, Sheets, and Slides (exported as text), plus PDF, DOCX, TXT, MD, and CSV files stored in Drive. Useful for documents and shared files that live in Drive rather than Dropbox.

### Follow-up reminders ✓ — 2026-06-11

Reminders stored in `dot.db` with a `due_at` timestamp. A JobQueue job checks every 60 seconds and delivers due reminders as Telegram messages. Set naturally: "remind me to follow up with X in two weeks." Three tools: `set_reminder`, `list_reminders`, `delete_reminder`.

### Morning briefing ✓ — 2026-06-11

Daily proactive Telegram message at a configurable time (`BRIEFING_TIME` in `.dot.env`, default `08:00` Toronto). Content: today's calendar, unread emails, reminders due today, stale deal alerts, and top news headlines via web search. Formatted by Claude for readability.

### Gmail drafts ✓ — 2026-06-11

`create_gmail_draft` tool creates a draft for Joey to review and send — never auto-sends. Reads thread context first so the draft has full background. Requires `gmail.compose` OAuth scope (re-run `auth_work.py` after deleting `token_work.pickle`).

### Structured deal tracking ✓ — 2026-06-11

Lightweight CRM in `dot.db`: company, stage (`sourcing` → `first_call` → `due_diligence` → `passed` / `invested`), last touchpoint, next action, notes. Three tools: `update_deal`, `get_deal_info`, `list_deals`. Queryable in natural language.

### Voice messages ✓ — 2026-06-11

Telegram voice notes transcribed locally with Whisper (`tiny` model, ~75 MB, CPU-only, no API cost). Transcript echoed back in italics then passed to the existing agent loop. Requires `ffmpeg` system package and `openai-whisper` Python package.

### Stale deal alerts in morning briefing ✓ — 2026-06-24

Morning briefing now includes a "Needs attention" section for deals in active stages with no update in 14+ days. One SQL query on the deals table; section is omitted entirely when the pipeline is current.

### Meeting prep brief ✓ — 2026-06-24

JobQueue job runs every 5 minutes. When a calendar event with external attendees is 25–35 minutes away, Dot sends a prep brief pulling from Granola (previous call notes) and Gmail (recent threads). Deduped by event ID across the session.

### WhatsApp forwarding via `/log` ✓ — 2026-06-24

`/log <text>` accepts a pasted note or forwarded WhatsApp conversation. Claude extracts self-contained facts and saves them to memory. Fills the gap for communication that happens outside email.

### Deal + memory auto-linking in ingest ✓ — 2026-06-24

When `ingest.py` extracts facts from a document, it checks each fact against active deal company names and tags matching memories with `deal:<company>`. Makes `get_deal_info` progressively richer as documents are ingested.

### Named conversation sessions ✓ — 2026-06-26

`/switch <name>` saves the current conversation and loads (or creates) a named session stored in `sessions/<name>.json`. `/sessions` lists all conversations with message counts. Existing `session.json` auto-migrates to `sessions/default.json` on first startup. Typing `/` in Telegram now shows all commands and descriptions (registered via `set_my_commands` on startup).

### Web conversation viewer ✓ — 2026-06-26

`web.py` is a FastAPI app (port 8080) that shows all conversation sessions in a read-only chat UI. Password-protected via `WEB_SECRET` in `.dot.env`. Auto-refreshes every 5 seconds. Designed for access over Tailscale — private, no open ports, no firewall rules. Run as a separate `web.service` systemd unit.

### Remote restart + reply context + briefing fixes ✓ — 2026-07-01

**`/restart` command:** sends a confirmation message then calls `sys.exit(0)`; systemd's `Restart=always` brings the bot back up in ~10 seconds. No sudo, no terminal needed — deploy code changes from anywhere.

**Telegram reply context:** when you use Telegram's reply feature on a message, the quoted text is prepended to your input so Dot knows what you're referencing without having to search back through the conversation.

**Web search `container_id` fix:** the `web_search_20260209` tool runs server-side in an Anthropic container. The response includes a `container_id` that must be echoed on all subsequent calls in the same turn — missing it caused a 400 on every search. All three API loops (main agent, morning briefing, meeting prep) now thread `container_id` through correctly.

**History rollback on error:** if an API call fails mid-turn, `conversation_history` is restored from the last clean on-disk save. Previously, a failed turn left a partial assistant message in memory; the next user message would cause Claude to continue the half-written thought before answering.

**Briefing prompt hardening:** instructions are now numbered and labelled non-negotiable. The first rule requires the response to start with the first section header — no preamble. The news rule explicitly forbids including stories from previous days and prescribes "Nothing notable today" when nothing is fresh.
