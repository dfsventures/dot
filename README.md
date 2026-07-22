# Dot

A personal AI chief-of-staff that lives in Telegram. Dot is a single-user agent built on Claude that can search and read work email, manage a Google Calendar, recall meeting notes from Granola, search and read Dropbox files, search the web, and remember facts across conversations — backed by a long-term memory store that is continuously fed by an automated document-ingestion pipeline.

Built for an investor workflow (pitch decks, founder calls, deal tracking), but the architecture is general: a Telegram front end, a Claude agent loop with tools, and a SQLite + ChromaDB memory layer shared between the chat agent and a background ingestion job.

## Architecture

```
                       ┌─────────────────────────────┐
  Telegram  ◄────────► │  agent.py                   │
  (single user)        │  Claude agent loop + tools  │
                       └──────┬──────────────┬───────┘
                              │              │
            Gmail / Calendar  │              │  memory.py
            Granola API       │              │  SQLite (dot.db)
            Dropbox API       │              │  + ChromaDB vectors
            Web search        │              │
                              │              ▲
                       ┌──────┴──────────────┴───────┐
  Dropbox "/Dot Dump" ►│  ingest.py (cron, 15 min)   │
  drop a file in,      │  extract → Claude distills  │
  facts come out       │  facts → memory store       │
                       └─────────────────────────────┘

  Browser (Tailscale) ◄──────────────────────────────►  web.py
                             read-only session viewer       (port 8080)
```

Four modules:

- **`agent.py`** — the Telegram bot and agent loop. Receives a message, retrieves relevant memories, calls Claude with a tool belt, executes tool calls in a loop until Claude produces a final answer, and replies.
- **`ingest.py`** — a cron-driven pipeline that watches a Dropbox folder. Any document dropped there gets downloaded, parsed, distilled into facts by Claude, and saved to memory. Processed files are moved to `Processed/`, failures to `Failed/`.
- **`memory.py`** — the shared memory layer: SQLite for durable storage, ChromaDB + sentence-transformers (`all-MiniLM-L6-v2`, local, no API cost) for vector similarity recall.
- **`web.py`** — a read-only FastAPI conversation viewer (port 8080). Shows all named sessions in a sidebar, renders messages as a chat UI, and auto-refreshes every 5 seconds. Password-protected; designed for remote access via Tailscale.

## What the agent can do

| Tool | Capability |
|---|---|
| `search_gmail_work` / `read_gmail_work` | Search and read work Gmail |
| `create_gmail_draft` | Draft a reply or new email — never auto-sends; Joey reviews and sends from Gmail |
| `list/get/create/update/delete_calendar_event` | Full read-write Google Calendar management, including attendee invites and auto-generated Meet links |
| `search_granola` / `read_granola` | Search meeting notes and read summaries + transcripts from Granola |
| `search_dropbox` / `read_dropbox_file` | Find and read Dropbox files (PDF, DOCX, TXT, MD) |
| `search_drive` / `read_drive_file` | Find and read Google Drive files (Google Docs/Sheets/Slides exported as text, plus PDF, DOCX, TXT, MD, CSV) — read-only |
| `web_search` | Anthropic's native server-side web search |
| `set_reminder` / `list_reminders` / `delete_reminder` | Time-based reminders delivered as Telegram messages |
| `update_deal` / `get_deal_info` / `list_deals` | Lightweight deal pipeline: sourcing → first_call → due_diligence → passed / invested |
| Voice messages | Send a voice note; Whisper transcribes it locally (CPU, no API cost) and passes the text to the agent |

Plus Telegram commands (type `/` to see the full menu in the chat):

- `/restart` — restart the bot remotely; systemd brings it back up in ~10 seconds
- `/confirm` — execute a held calendar change (calendar writes that would email attendees are held until you explicitly confirm)
- `/cancel` — discard a held calendar change
- `/switch <name>` — switch to a named conversation (e.g. `/switch fundraising`); saves the current session and loads or creates the named one
- `/sessions` — list all conversations with message counts
- `/remember <fact>` — save a memory manually
- `/memories` — list recent memories
- `/forget <n>` — delete a memory by number
- `/newsession` — clear the current conversation (after auto-extracting facts worth keeping)
- `/log <text>` — extract and save facts from a pasted note or WhatsApp conversation

**Telegram reply context:** when you use Telegram's reply feature on a specific message, the quoted message text is automatically prepended to your input so Dot knows what you're referencing.

## Design details

These are the parts that took real debugging to get right.

### Memory: SQLite + ChromaDB, retrieval injected per-turn

Every memory lives in SQLite (source of truth) and is embedded into a persistent ChromaDB collection. On each user message, the top-k (15) most similar memories are retrieved and injected into the user turn inside `<relevant_memories>` tags — *not* into the system prompt, deliberately (see caching below). A startup migration (`migrate_sqlite_to_chroma`) backfills any SQLite rows missing from the vector index, so the two stores can never permanently drift.

### Embedding model

All vector embeddings come from [`all-MiniLM-L6-v2`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) via sentence-transformers — a ~80 MB model producing 384-dimensional vectors, running entirely on CPU. It embeds a memory in milliseconds on a modest desktop chip, needs no GPU, no API key, and no Hugging Face account, which is why retrieval costs nothing per query. The trade-off is retrieval quality a notch below large hosted embedding models — fine for a personal memory store of thousands of facts, where the top-15 recall feeds a model that can judge relevance itself.

On first launch, sentence-transformers downloads the weights from the Hugging Face Hub and caches them locally (`~/.cache/huggingface`), so the first start needs internet access and takes a minute. You may see a *"sending unauthenticated requests to the HF Hub"* warning — it's harmless; anonymous downloads of public models are fine, and subsequent starts load from the cache. The loaded model accounts for most of the bot's ~1.1 GB memory footprint.

**Swapping the model:** embeddings from different models aren't comparable, so if you change `SentenceTransformer('all-MiniLM-L6-v2')` in `memory.py`, the existing ChromaDB index becomes garbage. The recovery is built in: delete the `chroma_db/` directory and restart — `migrate_sqlite_to_chroma()` re-embeds every memory from SQLite (the source of truth) into a fresh index on startup.

### Prompt caching that actually hits

The system prompt and tool definitions are frozen with a `cache_control` breakpoint, so they cache for the whole session. Volatile content (retrieved memories) goes into user turns where it can't invalidate that prefix. A second breakpoint is placed on the last content block of each request — on a *copy* of the message, never the stored history, because markers accumulating in history would blow past the API's 4-breakpoint limit. Result: conversation prefixes cache turn-over-turn; the logs report `cache_read` tokens on every call as a health check.

### Named sessions + web viewer

Each conversation lives in `sessions/<name>.json`. `/switch <name>` saves the current history and loads or creates the target session; the first startup migrates the old `session.json` to `sessions/default.json` automatically. Sessions are independent — prompt caching warms separately per session since each has its own history prefix.

`web.py` reads the `sessions/` directory and serves a password-protected chat UI at port 8080. It's a companion process (`web.service`) that runs alongside the bot with no coupling — the bot writes files, the viewer reads them. Designed for remote access via [Tailscale](https://tailscale.com), which creates a private encrypted link between your devices without opening any firewall ports.

### Session persistence + self-repair

Conversation history persists to `sessions/<name>.json` so the bot survives restarts. The tricky part: if the process dies mid-tool-call, history is left with a `tool_use` block that has no `tool_result` (or vice versa), and the API rejects *every* subsequent request with a 400. `repair_history()` scans for dangling tool blocks and drops them — on load, and again before each turn, since a crashed turn can leave the in-memory history broken without ever touching disk.

### Context window management

History is token-estimated (~4 chars/token) and compressed once it exceeds an 80k budget: everything but the recent window is summarised by Claude into a single dense context message. The cut point must land on a plain-text user message — cutting at a tool result would orphan it from its `tool_use` turn and 400 the request.

### Agent loop edge cases

- Tool execution never raises — a raised exception would leave a dangling `tool_use` in history (same 400-forever failure mode). Errors return as `is_error` tool results instead.
- `pause_turn` (server-side web search) is handled by re-sending with the `container_id` from the response — required by the API to resume in the same search container. Missing this causes a 400 on every web search follow-up.
- If any API call fails mid-turn, `conversation_history` is rolled back to the last clean on-disk save. Without this, a failed turn leaves a half-written assistant message in memory; on the next user message Claude would continue it mid-thought before answering.
- A 15-iteration cap prevents runaway tool loops.
- Replies are chunked at 4,000 chars for Telegram's message limit.

### Ingestion pipeline

Supported types: PDF, DOCX, PPTX, XLSX/XLS, CSV, TXT, MD.

- **Text-layer documents** are parsed locally, then Claude distills 5–20 self-contained facts per document via a structured extraction prompt.
- **Image-based PDFs** (designed pitch decks with no text layer) are detected and sent to Claude *natively* as base64 documents — Claude reads the pages visually. Oversized PDFs are downsampled with Ghostscript (`/ebook`, ~150dpi) to fit under the API's ~32MB request limit (24MB raw, since base64 inflates ~33%); the 100-page API limit is also checked.
- **Large spreadsheets/CSVs** (50+ rows) skip Claude entirely and ingest row-by-row — `Header: value | Header: value` per row — which is far richer than a summary and costs nothing. Small sheets still go through Claude.
- Every memory is tagged with `source:<filename>`; ingested files are tracked in SQLite so re-runs are idempotent.
- An `flock`-based single-instance lock prevents a cron run and a manual run from contending on the database.

### Memory extraction from conversations

On `/newsession`, the outgoing conversation is run through Claude with an extraction prompt and any durable facts (people, companies, deals, preferences) are saved before the history is cleared.

### Voice messages

A `voice` message handler runs alongside the text handler. When a voice note arrives, the `.ogg` file is downloaded from Telegram, transcribed locally with [Whisper](https://github.com/openai/whisper) (`tiny` model, ~75 MB, CPU-only, no API cost), and the transcript is echoed back in italics before being passed to the existing agent loop unchanged — no other code path changes. Whisper is lazy-loaded on the first voice message so it doesn't add ~1 GB of PyTorch to startup memory.

**Prerequisite:** `sudo apt install ffmpeg` — Whisper needs it to decode audio. The first voice message also downloads the Whisper model weights (~75 MB) to `~/.cache/whisper`; subsequent loads are instant.

### Reminders

Reminders are stored in a `reminders` table in `dot.db` with a note and a `due_at` timestamp in Toronto local time. A `JobQueue` job runs every 60 seconds, checks for any reminders whose `due_at` has passed, sends them as Telegram messages, and deletes them. Three tools — `set_reminder`, `list_reminders`, `delete_reminder` — let Claude set and manage them from natural language: "remind me to follow up with X in two weeks" resolves to a specific `YYYY-MM-DD HH:MM` timestamp and a confirmation. Due-times are stored and evaluated in Toronto time (`America/Toronto`) explicitly — the host OS timezone does not affect when reminders fire.

### Morning briefing

Every morning at a configurable time (default `08:00` Toronto; set `BRIEFING_TIME=HH:MM` in `.dot.env`), Dot sends an unprompted Telegram message with today's calendar events, unread emails from the last 24 hours, reminders due that day, stale-deal alerts (active deals untouched for 14+ days), and today's news headlines via web search. It calls the existing tool functions directly — no extra API call — and runs via PTB's `job_queue.run_daily`, so no extra cron entry is needed.

### Deal tracking

A `deals` table in `dot.db` holds a lightweight CRM: company name (unique), pipeline stage, last touchpoint, next action, and notes. Stages follow a fixed vocabulary: `sourcing`, `first_call`, `due_diligence`, `passed`, `invested`. Three tools — `update_deal`, `get_deal_info`, `list_deals` — make the pipeline queryable in natural language: "add Acme to the pipeline", "move Acme to due diligence, next action is send term sheet by June 20", "what's in due diligence right now?". `update_deal` is an upsert — it creates the deal if it doesn't exist and only updates the fields you specify if it does.

## Security model

- **Single-user lockdown**: every handler checks the sender's Telegram user ID against `YOUR_TELEGRAM_USER_ID` and silently ignores anyone else.
- **All secrets live in `.dot.env`** (gitignored). See `.env.example` for the full list.
- Google OAuth tokens (`token_work.pickle`, `credentials.json`), the memory database, vector index, and conversation history are all gitignored — they contain personal data.
- Gmail scopes are `gmail.readonly` + `gmail.compose` (needed for draft creation — note `gmail.compose` technically permits sending, but Dot's code only ever calls `drafts().create`, never send). Calendar is read-write by design. Drive scope is `drive.readonly` — no writes are possible.
- **Attendee-affecting calendar writes require an explicit `/confirm` in code.** Creating an event with attendees, updating an event to add attendees, or deleting an event that has attendees are intercepted at the tool-execution layer — the Calendar API is never called until Joey sends `/confirm`. This prevents a prompt-injected instruction (from an email, document, or meeting invite) from emailing third parties without Joey's explicit tap.

## Hardware

Dot runs on a used **Dell OptiPlex 7060 micro** sitting on a shelf at home — an Intel i5-8500T (6 cores, 35W low-power variant), 16 GB RAM, 500 GB NVMe SSD. Machines like this go for **$100–150 used** on eBay, and the whole stack barely registers on it: load average sits near zero, and disk usage for the project (databases, vector index, venv, embedding model) is a couple of GB.

What actually drives the requirements:

- **RAM** is the binding constraint. The bot holds the sentence-transformers embedding model and ChromaDB in memory — about **1.1 GB resident** in steady state, and the cron ingestion job briefly loads a second copy while it runs. 4 GB works; 8 GB is comfortable.
- **CPU** barely matters. Embedding with MiniLM on CPU takes milliseconds per memory; everything heavy happens on Anthropic's side. No GPU involved anywhere.
- **Disk**: a few GB. The SQLite database grows by roughly a kilobyte per memory.

Anything always-on works: an old desktop or laptop, an Intel NUC, a Raspberry Pi 5 (8 GB), or a basic **$5–10/month VPS** with 2–4 GB of RAM if you'd rather not run hardware at all. Power draw for a mini PC like this is ~10 W at idle — roughly **$1–2/month** in electricity.

## Running costs

The design keeps recurring costs low on purpose: embeddings are computed locally (free), big spreadsheets are ingested without any model calls, and aggressive prompt caching means most input tokens bill at a tenth of the normal rate.

**Claude API** (the only metered cost) — Dot uses Claude Sonnet 4.6 at $3/M input tokens, $15/M output, $3.75/M cache writes, and $0.30/M cache reads. Real numbers from one evening of active use (23 API calls — a normal back-and-forth session with tool use):

| Usage | Tokens | Cost |
|---|---:|---:|
| Uncached input | 50 | ~$0.00 |
| Cache writes | 102,470 | $0.38 |
| Cache reads | 733,767 | $0.22 |
| Output | 11,169 | $0.17 |
| **Total for the session** | | **~$0.77** |

That's about **3¢ per API call**. A substantive question usually triggers 2–4 calls (the agent loop searches, reads, then answers), so figure **5–15¢ per real question**. Without prompt caching the same session would have cost roughly 3× more — the cache-read column is the system prompt, tools, and conversation history being replayed at 10% price on every turn.

Ingestion costs scale with what you drop in the Dropbox folder:

- **Designed pitch decks** (image-based PDFs sent to Claude natively): roughly **$0.10–0.20 per deck** — page images dominate the input tokens.
- **Text documents** (reports, notes, DOCX): a **cent or two** each.
- **Large spreadsheets/CSVs**: **$0** — 50+ row files are ingested row-by-row with no model call.

A realistic monthly total for daily use — a handful of questions a day plus a few documents a week — lands around **$10–25/month** in API spend. Heavy research days might add a dollar or two.

Everything else is free or already paid for: Telegram bots are free, Google Workspace APIs are free at this scale, the Dropbox API works on a free account, and the embedding model runs locally. The one exception: **Granola's public API requires their Enterprise plan**, so the meeting-notes integration only works if you (or your company) already pay for that — drop those two tools from `agent.py` otherwise.

**Bottom line:** ~$150 once for hardware (or skip it for a cheap VPS), and roughly **$11–27/month** all-in (API + electricity) for a personal agent that's always on.

## Setup

### 1. Install

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Optional system packages:
- `sudo apt install ghostscript` — oversized-PDF compression during ingestion
- `sudo apt install ffmpeg` — required for voice message transcription (Whisper needs it to decode audio)

Note: the first launch downloads the ~80 MB embedding model from the Hugging Face Hub (see [Embedding model](#embedding-model)) — it needs internet access and takes a minute. The first voice message similarly downloads the ~75 MB Whisper model on demand.

### 2. Configure secrets

```bash
cp .env.example .dot.env
# fill in each value
```

- **Telegram**: create a bot with [@BotFather](https://t.me/BotFather) → `TELEGRAM_TOKEN`. Get your numeric user ID from [@userinfobot](https://t.me/userinfobot) → `YOUR_TELEGRAM_USER_ID`.
- **Anthropic**: API key from [console.anthropic.com](https://console.anthropic.com).
- **Granola**: API token (the public API requires an Enterprise plan).
- **Briefing time**: `BRIEFING_TIME=08:00` sets when the morning briefing fires (Toronto local time). Omit to use the default.

### 3. Google OAuth

1. In Google Cloud Console, create an OAuth client (Desktop app) with the Gmail and Calendar APIs enabled, and download it as `credentials.json` into the project root.
2. Run the one-time auth flow (opens a browser):

```bash
venv/bin/python auth_work.py
```

This saves `token_work.pickle`; the agent refreshes it automatically thereafter.

> **If upgrading from an earlier version:** any time a scope is added to `SCOPES` in `auth_work.py` (e.g. `gmail.compose` for draft creation, or `drive.readonly` for Drive search/read), the existing `token_work.pickle` does **not** pick it up automatically — tokens only carry the scopes consented to when they were issued. Delete the existing `token_work.pickle` and re-run `auth_work.py` to pick up any new scope; the old token will cause a 403 ("insufficient authentication scopes") on calls that need the new one. Also confirm the corresponding Google API (Gmail, Calendar, Drive) is enabled in Google Cloud Console for the project — an unenabled API 403s the same way, independent of OAuth scope.

### 4. Dropbox OAuth

1. Create an app in the [Dropbox App Console](https://www.dropbox.com/developers/apps) with `files.metadata.read`, `files.content.read`, and `files.content.write` permissions; put its key/secret in `.dot.env`.
2. Run the one-time flow:

```bash
venv/bin/python dropbox_auth.py
```

Copy the printed access/refresh tokens into `.dot.env`.

### 5. Run

```bash
venv/bin/python agent.py
```

For production, run it under systemd:

```ini
[Unit]
Description=Dot AI Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<you>
WorkingDirectory=/path/to/dot
Environment=PATH=/path/to/dot/venv/bin:/usr/bin:/bin
ExecStart=/path/to/dot/venv/bin/python agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

To also run the web conversation viewer:

```bash
sudo cp web.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable web && sudo systemctl start web
```

Set `WEB_SECRET=<password>` in `.dot.env` before starting. Access the viewer at `http://<your-ip>:8080`. For remote access from anywhere, install [Tailscale](https://tailscale.com) on the server (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`) and on your other devices — no firewall changes needed.

And schedule ingestion with cron:

```cron
*/15 * * * * /path/to/dot/venv/bin/python /path/to/dot/ingest.py > /path/to/dot/ingest.log 2>&1
```

Drop files into the `/Dot Dump` folder in Dropbox; within 15 minutes their facts are in memory and the file moves to `/Dot Dump/Processed`.

## Customising

The agent's persona, priorities, and tool-routing heuristics live in `BASE_SYSTEM` in `agent.py`, and the fact-extraction behaviour in `EXTRACTION_SYSTEM` in `ingest.py`. Both are written for an Africa-focused investor workflow — edit them to fit yours.

## Development workflow (Claude Code agents)

Development on this repo uses two Claude Code subagents, defined in `.claude/agents/` and picked up automatically by any Claude Code session opened in this directory — no per-machine setup needed.

- **Felix** (`felix.md`) — senior staff engineer + technical PM. Reviews code, verifies roadmap/README claims against the actual source, and turns approved work into numbered workstreams in `docs/IMPLEMENTATION_PLAN.md`. Never writes product code; surfaces product decisions instead of making them silently.
- **Alvin** (`alvin.md`) — implementation engineer. Executes workstreams from the plan exactly as written, one commit per workstream, and reports deviations rather than improvising around them.

Typical flow: ask Claude to "have Felix review X" or "plan Y with Felix" → approve the decisions Felix flags → "have Alvin do WS-N" → review and push. Findings are numbered F-1, F-2, … and workstreams WS-1, WS-2, … continuing the existing sequences in `docs/IMPLEMENTATION_PLAN.md`.

Note: Felix's definition pins `model: fable` and Alvin's `model: sonnet`; if a machine's Claude Code account lacks one of those models, override with the `model` field when invoking, or edit the frontmatter locally.
