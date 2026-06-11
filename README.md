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
```

Three modules:

- **`agent.py`** — the Telegram bot and agent loop. Receives a message, retrieves relevant memories, calls Claude with a tool belt, executes tool calls in a loop until Claude produces a final answer, and replies.
- **`ingest.py`** — a cron-driven pipeline that watches a Dropbox folder. Any document dropped there gets downloaded, parsed, distilled into facts by Claude, and saved to memory. Processed files are moved to `Processed/`, failures to `Failed/`.
- **`memory.py`** — the shared memory layer: SQLite for durable storage, ChromaDB + sentence-transformers (`all-MiniLM-L6-v2`, local, no API cost) for vector similarity recall.

## What the agent can do

| Tool | Capability |
|---|---|
| `search_gmail_work` / `read_gmail_work` | Search and read work Gmail (read-only scope) |
| `list/get/create/update/delete_calendar_event` | Full read-write Google Calendar management, including attendee invites and auto-generated Meet links |
| `search_granola` / `read_granola` | Search meeting notes and read summaries + transcripts from Granola |
| `search_dropbox` / `read_dropbox_file` | Find and read Dropbox files (PDF, DOCX, TXT, MD) |
| `web_search` | Anthropic's native server-side web search |

Plus Telegram commands:

- `/remember <fact>` — save a memory manually
- `/memories` — list recent memories
- `/forget <n>` — delete a memory by number
- `/newsession` — clear the conversation (after auto-extracting facts worth keeping from it)

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

### Session persistence + self-repair

Conversation history persists to `session.json` so the bot survives restarts. The tricky part: if the process dies mid-tool-call, history is left with a `tool_use` block that has no `tool_result` (or vice versa), and the API rejects *every* subsequent request with a 400. `repair_history()` scans for dangling tool blocks and drops them — on load, and again before each turn, since a crashed turn can leave the in-memory history broken without ever touching disk.

### Context window management

History is token-estimated (~4 chars/token) and compressed once it exceeds an 80k budget: everything but the recent window is summarised by Claude into a single dense context message. The cut point must land on a plain-text user message — cutting at a tool result would orphan it from its `tool_use` turn and 400 the request.

### Agent loop edge cases

- Tool execution never raises — a raised exception would leave a dangling `tool_use` in history (same 400-forever failure mode). Errors return as `is_error` tool results instead.
- `pause_turn` (server-side web search hitting its iteration limit) is handled by simply re-sending; the API resumes from the trailing server tool block.
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

## Security model

- **Single-user lockdown**: every handler checks the sender's Telegram user ID against `YOUR_TELEGRAM_USER_ID` and silently ignores anyone else.
- **All secrets live in `.dot.env`** (gitignored). See `.env.example` for the full list.
- Google OAuth tokens (`token_work.pickle`, `credentials.json`), the memory database, vector index, and conversation history are all gitignored — they contain personal data.
- Gmail scope is read-only; Calendar is read-write by design.

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

Optional: install Ghostscript (`apt install ghostscript`) for oversized-PDF compression during ingestion.

Note: the first launch downloads the ~80 MB embedding model from the Hugging Face Hub (see [Embedding model](#embedding-model)) — it needs internet access and takes a minute, then loads from local cache thereafter.

### 2. Configure secrets

```bash
cp .env.example .dot.env
# fill in each value
```

- **Telegram**: create a bot with [@BotFather](https://t.me/BotFather) → `TELEGRAM_TOKEN`. Get your numeric user ID from [@userinfobot](https://t.me/userinfobot) → `YOUR_TELEGRAM_USER_ID`.
- **Anthropic**: API key from [console.anthropic.com](https://console.anthropic.com).
- **Granola**: API token (the public API requires an Enterprise plan).

### 3. Google OAuth

1. In Google Cloud Console, create an OAuth client (Desktop app) with the Gmail and Calendar APIs enabled, and download it as `credentials.json` into the project root.
2. Run the one-time auth flow (opens a browser):

```bash
venv/bin/python auth_work.py
```

This saves `token_work.pickle`; the agent refreshes it automatically thereafter.

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

And schedule ingestion with cron:

```cron
*/15 * * * * /path/to/dot/venv/bin/python /path/to/dot/ingest.py > /path/to/dot/ingest.log 2>&1
```

Drop files into the `/Dot Dump` folder in Dropbox; within 15 minutes their facts are in memory and the file moves to `/Dot Dump/Processed`.

## Customising

The agent's persona, priorities, and tool-routing heuristics live in `BASE_SYSTEM` in `agent.py`, and the fact-extraction behaviour in `EXTRACTION_SYSTEM` in `ingest.py`. Both are written for an Africa-focused investor workflow — edit them to fit yours.
