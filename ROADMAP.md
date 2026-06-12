# Roadmap

Planned additions to Dot. None of these are implemented yet.

---

## Voice messages (Telegram)

Make it possible to send a voice note to Dot and get a spoken reply, without leaving Telegram.

**How it works:**
1. Add a `voice` message handler to `agent.py` alongside the existing text handler.
2. Download the `.ogg` voice file Telegram sends.
3. Transcribe it locally with [Whisper](https://github.com/openai/whisper) (`tiny` model, ~75 MB, CPU, no API cost).
4. Pass the transcript to the existing agent loop — no other changes needed.
5. Optionally reply with a TTS voice note (OpenAI TTS or ElevenLabs) in addition to the text reply.

**Dependencies to add:**
- `openai-whisper` (or `faster-whisper` for lower latency on CPU)
- `ffmpeg` system package (Whisper requires it to decode audio)

**Trade-offs:**
- Still turn-based (not live interruption), which is fine for a chief-of-staff bot.
- First transcription downloads the Whisper model weights to `~/.cache/whisper`; subsequent calls are local and free.
- TTS on replies is optional — text replies in Telegram are readable even if you started with voice.

---

## Proactive morning briefing

Dot is currently reactive — it only acts when messaged. A daily briefing flips that: every morning at a set time, Dot sends an unprompted Telegram message with today's calendar, any emails flagged as needing a reply, and any follow-ups due that day.

**How it works:**
- A cron job (or systemd timer) fires at a configured time each morning.
- Calls the existing Gmail, Calendar, and reminder tools — no new integrations needed.
- Sends the summary to the user via the Telegram bot.

**Why it earns its place:** transforms Dot from a lookup tool into something that surfaces information before you think to ask for it. The difference between reactive and proactive is the difference between a search engine and a chief-of-staff.

---

## Follow-up reminders

No way currently to say "remind me to follow up with this founder in two weeks" and have Dot actually do it. Investors lose deals by forgetting to reply — this closes that loop.

**How it works:**
- New `/remind` command (or natural-language: "remind me to follow up with X on [date]").
- Adds a `reminders` table to `dot.db` with a due date and note.
- Morning briefing surfaces anything due that day; a separate check can also send an immediate Telegram nudge when a reminder fires.

**Why it earns its place:** the most common reason deals fall through is forgetting to reply. This is a small addition with outsized daily impact.

---

## Gmail drafts (write access, never auto-send)

Gmail is currently read-only. The high-value extension is draft creation: Dot reads the thread, pulls relevant context from memory (last call notes, what you know about the company), and creates a draft you review and send yourself.

**How it works:**
- Extend the Gmail OAuth scope to include `gmail.compose` (requires re-running `auth_work.py`).
- Add a `draft_gmail_reply` tool to `agent.py` that creates a draft via the Gmail API — never sends directly.
- Claude writes the draft using thread context + retrieved memories about the sender/company.

**Constraint:** never auto-send. Investor emails carry real relationship weight; the human stays in the loop on every send.

**Why it earns its place:** reading email surfaces information; drafting replies saves the time-consuming part. The read→draft loop covers most of the inbound email workload.

---

## Structured deal tracking

Memory stores everything as flat facts. An investor workflow has natural pipeline stages — sourcing, first call, due diligence, passed, invested. Adding lightweight deal state on top of the memory layer enables structured queries: "what's in due diligence right now?" or "show me everything I know about Company X."

**How it works:**
- New `deals` table in `dot.db`: company, stage, last touchpoint, next action, notes.
- New tools in `agent.py`: `update_deal`, `get_deal`, `list_deals` (filtered by stage).
- Ingest pipeline tags extracted memories with a deal ID when a company match is found.
- Natural language works: "move Acme to due diligence" or "what do I know about Acme?"

**Why it earns its place:** the memory layer is already accumulating facts about companies and founders — this gives those facts structure and makes the pipeline queryable without adding a separate CRM tool.
