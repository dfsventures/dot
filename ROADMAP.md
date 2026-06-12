# Roadmap

All five initial items shipped on 2026-06-11. The roadmap is currently clear.

---

## Shipped

### Follow-up reminders ✓

Reminders stored in `dot.db` with a `due_at` timestamp. A JobQueue job checks every 60 seconds and delivers due reminders as Telegram messages. Set naturally: "remind me to follow up with X in two weeks." Three tools: `set_reminder`, `list_reminders`, `delete_reminder`.

### Morning briefing ✓

Daily proactive Telegram message at a configurable time (`BRIEFING_TIME` in `.dot.env`, default `08:00` Toronto). Content: today's calendar, unread emails from the last 24h, reminders due today. Runs via PTB's `job_queue.run_daily` — no extra cron entry needed.

### Gmail drafts ✓

`create_gmail_draft` tool creates a draft for Joey to review and send — never auto-sends. Reads thread context first so the draft has full background. Requires `gmail.compose` OAuth scope (re-run `auth_work.py` after deleting `token_work.pickle`).

### Structured deal tracking ✓

Lightweight CRM in `dot.db`: company, stage (`sourcing` → `first_call` → `due_diligence` → `passed` / `invested`), last touchpoint, next action, notes. Three tools: `update_deal`, `get_deal_info`, `list_deals`. Queryable in natural language.

### Voice messages ✓

Telegram voice notes transcribed locally with Whisper (`tiny` model, ~75 MB, CPU-only, no API cost). Transcript echoed back in italics then passed to the existing agent loop. Requires `ffmpeg` system package and `openai-whisper` Python package.
