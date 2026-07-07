# Dot — Implementation Plan

Written by Felix (review of commit 43e5510, 2026-07-07). Approved by Joey via coordinator.
Executor: Alvin. Do not commit as part of these workstreams — Joey handles commits.

## Findings register (from the 2026-07-07 review)

| # | Severity | Summary | Addressed by |
|---|---|---|---|
| F-1 | High | Prompt-injection → exfiltration via calendar invites (`sendUpdates="all"` emails arbitrary attendees); only guard is a prompt instruction (agent.py:677) | WS-2 |
| F-2 | High | README line 143 claims "Gmail scope is read-only"; `auth_work.py:5` grants `gmail.compose`, which can send mail | WS-1 |
| F-3 | High | `requirements.txt` missing `uvicorn` and `jinja2`; fresh install per README yields a broken web viewer | WS-1 |
| F-4 | Medium | Web viewer: binds `0.0.0.0` (web.service:9), raw password stored as cookie value (web.py:129), non-constant-time compares (web.py:33, web.py:124), no login rate limit | WS-6 |
| F-5 | Medium | Reminder due-checks use server-local time (`memory.py:139`, agent.py:1056-1061) while everything else pins `America/Toronto` | WS-4 |
| F-6 | Medium | `_read_gmail_svc` (agent.py:156-167) misses nested multiparts — emails with attachments read as "[No text body found]" | WS-3 |
| F-7 | Low | `search_gmail_work` hardcodes `messages[:5]` (agent.py:149) despite schema advertising `max_results` default 10 | WS-3 |
| F-8 | Low | `/memories`–`/forget` numbering relies on unstable `ORDER BY created_at DESC` ties (memory.py:89) | WS-6 |
| F-9 | Low | Ingestion: duplicate facts on crash-retry; file stuck in inbox if move fails after `mark_ingested` (ingest.py:477-489); `_prepped_events` unbounded (agent.py:822) | WS-6 |
| F-10 | Low | README briefing paragraph (line 132) omits stale-deals + news sections; voice replies lose reply context on non-text targets (agent.py:926) | WS-1 (docs part) |

## Confirmed product decisions (Joey, 2026-07-07)

- **D-1:** Code-level confirmation gate for calendar writes involving attendees (create with attendees, update adding attendees, delete of events that have attendees). Gmail drafts stay prompt-guarded only — drafts never send, so no gate.
- **D-2:** Web viewer binds to the machine's Tailscale interface IP only (not `0.0.0.0`, not LAN).
- **D-3:** Google Drive integration uses the `drive.readonly` scope, mirroring the read-only Dropbox pattern.

## Conventions to follow (observed in the codebase)

- Tool functions are plain sync functions in `agent.py` that return strings and never raise — errors are returned as `"Xxx error: {e}"` strings (see `search_dropbox`, agent.py:403-426).
- Module-level singletons and globals; `logging` for the bot, bare `print` in `ingest.py`; inline imports inside functions for rarely-used modules.
- Tool schemas live in the `TOOLS` list; every tool is registered in `TOOL_FUNCTIONS` and mentioned in `BASE_SYSTEM`.
- Session/DB paths are anchored to the module directory, never CWD.

---

## WS-1 — Setup truth: requirements + README corrections

**Goal:** A fresh install following the README actually works, and the README stops making a false security claim.

**Confirmed decisions:** none needed.

**Steps**

1. `requirements.txt` — add two pinned lines (pin to whatever `venv/bin/pip show uvicorn jinja2` reports on the production box, so the manifest matches reality):
   ```
   uvicorn==<prod version>
   jinja2==<prod version>
   ```
   Rationale: `web.service:9` execs `venv/bin/uvicorn`, and `web.py:23` uses `Jinja2Templates`; neither package is a FastAPI dependency (F-3).
2. `README.md` line 143 — replace:
   > Gmail scope is read-only; Calendar is read-write by design.

   with:
   > Gmail scopes are `gmail.readonly` + `gmail.compose` (needed for draft creation — note `gmail.compose` technically permits sending, but Dot's code only ever calls `drafts().create`, never send). Calendar is read-write by design.

   (F-2. The "never sends" guarantee is code-enforced, not scope-enforced — say so honestly.)
3. `README.md` line 132 (morning briefing paragraph) — extend the content list to match the code (agent.py:1063-1091): "today's calendar events, unread emails from the last 24 hours, reminders due that day, stale-deal alerts (active deals untouched for 14+ days), and today's news headlines via web search." (F-10.)

**Acceptance checklist**

- [ ] `python3 -m venv /tmp/dot-test && /tmp/dot-test/bin/pip install -r requirements.txt` succeeds, and `/tmp/dot-test/bin/python -c "import uvicorn, jinja2"` works.
- [ ] `grep -n "read-only" README.md` no longer claims the Gmail scope is read-only.
- [ ] README briefing paragraph mentions stale deals and news.

**UX impact:** none — documentation and install manifest only.
**Cost impact:** none.
**Effort:** ~30 minutes.

---

## WS-2 — Confirmation gate for calendar writes with attendees (F-1, D-1)

**Goal:** A prompt-injected instruction (from an email, document, or web page) can no longer cause Dot to email third parties via calendar invites/updates/cancellations without Joey tapping an explicit `/confirm`. Attendee-less calendar writes stay frictionless; Gmail drafts are unchanged.

**Confirmed decisions:** D-1 (gate in code, not prompt; drafts stay prompt-guarded).

**Design:** single-user bot → a single module-level pending-action slot. Gated tool calls do not execute; they stash the call and return a tool result telling Claude the action is pending Joey's `/confirm`. `/confirm` executes the real function and replies with its result; `/cancel` discards. A new gated call overwrites any stale pending one.

**Steps — all in `agent.py`**

1. Near `_prepped_events` (around line 822), add module state and the gate predicate:
   ```python
   _pending_action = None  # {"name": str, "input": dict, "summary": str}
   _GATED_TOOLS = {"create_calendar_event", "update_calendar_event", "delete_calendar_event"}

   def _needs_confirmation(name: str, tool_input: dict) -> bool:
       """True when the call would email third parties (sendUpdates='all')."""
       if name == "create_calendar_event":
           return bool(tool_input.get("attendees"))
       if name == "update_calendar_event":
           return bool(tool_input.get("add_attendees"))
       if name == "delete_calendar_event":
           try:
               e = calendar_work.events().get(
                   calendarId='primary', eventId=tool_input.get("event_id", "")
               ).execute()
               return bool(e.get('attendees'))
           except Exception:
               return True  # can't verify — fail safe, require confirmation
       return False
   ```
2. In `run_tool` (agent.py:809-819), intercept before executing:
   ```python
   def run_tool(name: str, tool_input: dict):
       global _pending_action
       fn = TOOL_FUNCTIONS.get(name)
       if not fn:
           return f"[{name} is a native tool — handled by API]", False
       if name in _GATED_TOOLS and _needs_confirmation(name, tool_input):
           summary = f"{name}({json.dumps(tool_input, default=str)[:500]})"
           _pending_action = {"name": name, "input": tool_input, "summary": summary}
           return (
               "PENDING CONFIRMATION — this calendar change notifies attendees by email, "
               "so it was NOT executed. Tell Joey exactly what is pending and that he must "
               f"send /confirm to execute it or /cancel to discard it. Pending: {summary}"
           ), False
       try:
           return str(fn(**tool_input)), False
       except Exception as e:
           logging.exception(f"Tool {name} failed")
           return f"Tool error: {e}", True
   ```
3. Add the two command handlers (follow the existing command style, e.g. `cmd_restart` at agent.py:993):
   ```python
   async def cmd_confirm(update, context):
       global _pending_action
       if update.effective_user.id != YOUR_USER_ID: return
       if not _pending_action:
           await update.message.reply_text("Nothing pending.")
           return
       action, _pending_action = _pending_action, None
       fn = TOOL_FUNCTIONS[action["name"]]
       result = await asyncio.to_thread(lambda: str(fn(**action["input"])))
       await update.message.reply_text(result[:4000])

   async def cmd_cancel(update, context):
       global _pending_action
       if update.effective_user.id != YOUR_USER_ID: return
       if _pending_action:
           await update.message.reply_text(f"Cancelled: {_pending_action['summary']}")
           _pending_action = None
       else:
           await update.message.reply_text("Nothing pending.")
   ```
4. Register both in `main()` (`app.add_handler(CommandHandler("confirm", cmd_confirm))`, same for `cancel`) and add them to `set_my_commands` in `_post_init` (agent.py:1240-1251).
5. `BASE_SYSTEM` (agent.py:660-680): update the calendar guidance sentence to say that calendar changes involving attendees are held for Joey's `/confirm` automatically, so Dot should state the pending details and ask him to confirm — and should not retry the tool call while one is pending.
6. Note: `check_meeting_prep` and the briefing only expose read tools (`_PREP_TOOL_NAMES`, agent.py:824; briefing uses web_search only), so no gate work needed there — verify this stays true.
7. `README.md`: add `/confirm` and `/cancel` to the commands list, and a sentence under "Security model" that attendee-affecting calendar writes require an explicit `/confirm` in code.

**Acceptance checklist**

- [ ] "Schedule a call with alice@example.com tomorrow at 2pm" → Dot describes the event and says it is pending; no event exists on the calendar; `/confirm` creates it and replies with the event link.
- [ ] `/cancel` discards a pending action; `/confirm` afterwards says "Nothing pending."
- [ ] "Block 2 hours for deep work Friday morning" (no attendees) executes immediately, no confirmation step.
- [ ] Deleting an attendee-less event executes immediately; deleting an event with attendees requires `/confirm`.
- [ ] `create_gmail_draft` behavior unchanged (no gate).
- [ ] A gated tool result never raises (preserves the no-dangling-`tool_use` invariant, agent.py:809-819).

**UX impact:** additive — one extra `/confirm` tap, only for calendar actions that email other people. All other flows unchanged.
**Cost impact:** none (one extra Calendar API `get` per gated delete; free).
**Effort:** ~half a day including manual Telegram testing.

---

## WS-3 — Gmail read fixes: nested multiparts + honor max_results (F-6, F-7)

**Goal:** Emails with attachments (multipart/mixed wrapping multipart/alternative) become readable, and `search_gmail_work` returns as many results as the schema promises.

**Confirmed decisions:** none needed.

**Steps — all in `agent.py`**

1. Add a recursive body extractor above `_read_gmail_svc` (agent.py:156):
   ```python
   def _walk_payload_for_text(payload, mime="text/plain"):
       """Depth-first search of a Gmail payload tree for the first body of the given mime type."""
       if payload.get('mimeType') == mime and payload.get('body', {}).get('data'):
           return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
       for part in payload.get('parts', []):
           text = _walk_payload_for_text(part, mime)
           if text:
               return text
       return ""
   ```
2. Rewrite `_read_gmail_svc` to use it, with an HTML fallback:
   ```python
   def _read_gmail_svc(svc, message_id):
       m = svc.users().messages().get(userId='me', id=message_id, format='full').execute()
       body = _walk_payload_for_text(m['payload'])
       if not body:
           html = _walk_payload_for_text(m['payload'], mime="text/html")
           if html:
               body = re.sub(r'<[^>]+>', ' ', html)  # crude tag strip — good enough for reading
       return body[:3000] if body.strip() else "[No text body found]"
   ```
   (`re` is already imported at agent.py:1.)
3. `_search_gmail_svc` (agent.py:149): change `for msg in messages[:5]:` to `for msg in messages[:max_results]:`. The morning briefing call site passes `5` explicitly (agent.py:1060), so briefing behavior is unchanged.

**Acceptance checklist**

- [ ] `read_gmail_work` on a real email that has an attachment returns the body text, not "[No text body found]".
- [ ] An HTML-only email returns readable (tag-stripped) text.
- [ ] "Search my email for X, show me 10" surfaces up to 10 results.
- [ ] Morning briefing email section still shows at most 5 items.

**UX impact:** invisible improvement — previously-unreadable emails now readable.
**Cost impact:** slightly more tokens when 6-10 search results are returned; negligible.
**Effort:** ~2 hours including testing against real mailbox messages.

---

## WS-4 — Reminder timezone correctness (F-5)

**Goal:** Reminders fire at the intended Toronto time regardless of the server's OS timezone, matching how the briefing scheduler already works (agent.py:1268-1274). Must land before any VPS migration.

**Confirmed decisions:** none needed (technical call, flagged: reversal is trivial — the change only affects the "now" side of comparisons, stored data is untouched).

**Steps**

1. `memory.py` — `get_due_reminders` (line 137-143), keep the inline-import convention:
   ```python
   def get_due_reminders() -> list:
       from datetime import datetime
       from zoneinfo import ZoneInfo
       now = datetime.now(ZoneInfo("America/Toronto")).strftime('%Y-%m-%d %H:%M')
       ...
   ```
   Stored `due_at` strings are already Toronto-local (per the tool schema, agent.py:596), so no data migration — additive only.
2. `agent.py` — `send_morning_briefing` (lines 1053-1057): replace `date.today()` with Toronto-pinned dates:
   ```python
   from datetime import datetime
   from zoneinfo import ZoneInfo
   _today = datetime.now(ZoneInfo("America/Toronto")).date()
   today_label = _today.strftime("%A, %B %-d")
   today_str   = _today.strftime("%Y-%m-%d")
   ```
3. `README.md` Reminders section (line 128): note that due-times are stored and evaluated in Toronto time explicitly, so the host OS timezone no longer matters.

**Acceptance checklist**

- [ ] With the server TZ temporarily set to UTC (`TZ=UTC venv/bin/python -c "from memory import get_due_reminders; print(get_due_reminders())"`), a reminder due 1 minute ago Toronto-time is returned as due; one due in 3 hours Toronto-time is not.
- [ ] Reminder set via chat still fires within ~60s of its Toronto due time on the production box.

**UX impact:** none today (server is already Toronto-local); prevents silent 4-5 hour drift on a future VPS.
**Cost impact:** none.
**Effort:** ~1 hour.

---

## WS-5 — Google Drive read-only tools (roadmap item, D-3)

**Goal:** `search_drive` + `read_drive_file` tools mirroring the Dropbox pair, using the `drive.readonly` scope. Closes the last open ROADMAP item.

**Confirmed decisions:** D-3 (`drive.readonly`, not read-write).

**Steps**

1. `auth_work.py:3-7` — add `'https://www.googleapis.com/auth/drive.readonly'` to `SCOPES`. Also enable the Drive API on the existing Google Cloud project (manual, Joey/console).
2. Manual re-auth (Joey, on the server): delete `token_work.pickle`, run `venv/bin/python auth_work.py`. The old token 403s on Drive calls — same upgrade path already documented in README line 223.
3. `agent.py` — service client next to the existing builds (agent.py:35-37):
   ```python
   drive_work = build('drive', 'v3', credentials=work_creds)
   ```
4. `agent.py` — tool functions, following the Dropbox functions' shape (string returns, never raise):
   ```python
   def search_drive(query: str, max_results: int = 10):
       try:
           safe_q = query.replace("'", "\\'")
           results = drive_work.files().list(
               q=f"name contains '{safe_q}' and trashed = false",
               pageSize=max_results, orderBy="modifiedTime desc",
               fields="files(id, name, mimeType, modifiedTime)"
           ).execute()
           files = results.get('files', [])
           if not files:
               return "No Drive files found."
           return "\n".join(
               f"Name: {f['name']} | Type: {f['mimeType'].split('.')[-1]} | "
               f"Modified: {f.get('modifiedTime', '')[:10]} | ID: {f['id']}"
               for f in files
           )
       except Exception as e:
           return f"Drive search error: {e}"

   _DRIVE_EXPORTS = {
       'application/vnd.google-apps.document':     'text/plain',
       'application/vnd.google-apps.spreadsheet':  'text/csv',
       'application/vnd.google-apps.presentation': 'text/plain',
   }

   def read_drive_file(file_id: str):
       try:
           meta = drive_work.files().get(fileId=file_id, fields="name, mimeType").execute()
           name, mime = meta['name'].lower(), meta['mimeType']
           if mime in _DRIVE_EXPORTS:
               content = drive_work.files().export(fileId=file_id, mimeType=_DRIVE_EXPORTS[mime]).execute()
               return content.decode('utf-8', errors='ignore')[:3000] if isinstance(content, bytes) else str(content)[:3000]
           content = drive_work.files().get_media(fileId=file_id).execute()
           if name.endswith(('.txt', '.md', '.csv')):
               return content.decode('utf-8', errors='ignore')[:3000]
           elif name.endswith('.pdf'):
               import PyPDF2
               reader = PyPDF2.PdfReader(io.BytesIO(content))
               return " ".join(page.extract_text() or "" for page in reader.pages)[:3000]
           elif name.endswith('.docx'):
               from docx import Document
               doc = Document(io.BytesIO(content))
               return "\n".join(p.text for p in doc.paragraphs)[:3000]
           return f"[File type not readable as text: {meta['name']}]"
       except Exception as e:
           return f"Drive read error: {e}"
   ```
5. `agent.py` — `TOOLS` entries (mirror the Dropbox schemas at agent.py:578-590):
   - `search_drive`: description "Search Joey's Google Drive by file name. Use for documents and shared files that live in Drive rather than Dropbox."; properties `query` (required), `max_results` (integer, default 10).
   - `read_drive_file`: description "Read a Google Drive file by ID (get ID from search_drive first). Supports Google Docs/Sheets/Slides (exported as text) plus PDF, DOCX, TXT, MD, CSV."; property `file_id` (required).
6. `agent.py` — register both in `TOOL_FUNCTIONS` (agent.py:638-657) and add one line to the tool list in `BASE_SYSTEM` (agent.py:665-673): `- search_drive / read_drive_file: Joey's Google Drive (read-only)`.
7. Docs, on ship: add the tool row to the README capability table (line 39-49); update README line 143's scope sentence (as rewritten in WS-1) to include `drive.readonly`; move the "Google Drive access" entry in `ROADMAP.md` from Planned to Shipped with the ship date.

**Acceptance checklist**

- [ ] "Find the <known doc name> in my Drive" returns the file with an ID.
- [ ] Reading a native Google Doc returns its text; reading a PDF in Drive returns extracted text; an unsupported binary returns the `[File type not readable...]` marker, not an exception.
- [ ] `venv/bin/python -c "import agent"` fails cleanly (403 guidance) if the token predates the new scope — and the README re-auth note covers it.
- [ ] Attempting a Drive write is impossible: only `files().list/get/export/get_media` are called; scope is `drive.readonly`.
- [ ] ROADMAP.md updated (Planned → Shipped).

**UX impact:** additive — new capability, no existing flow touched.
**Cost impact:** none new — Drive API is free at this scale; tool results are capped at 3,000 chars like Dropbox reads, so token cost matches existing document reads.
**Effort:** ~half a day plus the one-time re-auth.

---

## WS-6 (optional cleanup batch) — Web hardening, /forget ordering, ingestion idempotency (F-4, F-8, F-9, D-2)

**Goal:** Close the remaining low/medium findings in one pass.

**Confirmed decisions:** D-2 (Tailscale-interface bind).

### 6a. Web viewer hardening (F-4, D-2)

1. `web.service:9` — replace `--host 0.0.0.0` with the machine's Tailscale IP (get it with `tailscale ip -4`; Tailscale IPs are stable per node). Add `After=tailscaled.service` to `[Unit]` so the bind address exists at start; `Restart=always`/`RestartSec=5` already covers races. Document the concrete IP placeholder in README's web-viewer section ("use the IP from `tailscale ip -4`"). This makes the README's "no open ports" claim true.
2. `web.py` — stop storing the raw password in the cookie; issue a random per-process token and use constant-time compares:
   ```python
   import secrets
   _SESSION_TOKEN = secrets.token_urlsafe(32)
   ```
   - `_check_auth` (web.py:26-34): cookie path compares against `_SESSION_TOKEN`; Bearer-header path compares against `WEB_SECRET`; both via `secrets.compare_digest`.
   - `do_login` (web.py:119-130): password check via `secrets.compare_digest(password, WEB_SECRET)`; on success `response.set_cookie("dot_token", _SESSION_TOKEN, httponly=True, samesite="lax")`.
   - Known trade-off (flagged): a viewer restart invalidates the cookie → Joey re-enters the password. Acceptable for a single-user tool; reversal is one line.
3. Optional micro-hardening: in `do_login`, `import asyncio; await asyncio.sleep(0.5)` on a wrong password — a free brute-force damper. Skip anything fancier; Tailscale is the real perimeter.

### 6b. Stable memory numbering (F-8)

4. `memory.py:89` — `get_all_memories`: change ordering to `ORDER BY id DESC` (insertion order is what `created_at DESC` was approximating; `id` breaks same-second ties deterministically, so `/memories` and `/forget` always agree).
5. Known limitation to leave as-is (flagged, not a change): `delete_memory` (memory.py:75-86) removes all rows with identical content — acceptable; true duplicates carry no information.

### 6c. Ingestion idempotency (F-9)

6. `ingest.py` `run()` (lines 442-496) — two ordering fixes:
   - **Skip re-saving facts for a file that already has memories** (crash-retry dedupe, additive — no deletes). Before the save loops, add:
     ```python
     tag_prefix = f"source:{entry.name}"
     prior = conn.execute(
         "SELECT COUNT(*) FROM memories WHERE tags LIKE ?", (f"{tag_prefix}%",)
     ).fetchone()[0]
     if prior:
         print(f"  {prior} memories already exist for this file — skipping fact save (crash retry)")
     ```
     and only save facts when `prior == 0`. Applies to the text/PDF path; pass an equivalent guard into `ingest_structured_xlsx`/`ingest_structured_csv` (same check at the top of each, returning 0 with a printed notice).
   - **Reorder move/mark**: currently save → `mark_ingested` → `move_file` (ingest.py:485-489). Change to save → `move_file` → `mark_ingested`. If the move fails, the file is *not* marked, so the next run retries it (and the dedupe guard above prevents duplicate facts). If the process dies after a successful move but before marking, the file is gone from the inbox so `already_ingested` is never consulted — harmless.
7. `agent.py:822` — bound `_prepped_events`: store `{event_id: added_at_datetime}` instead of a set and, at the top of `check_meeting_prep`, drop entries older than 2 hours. Also flag (accepted, no fix): a `/restart` inside a meeting's 25-35-minute window can re-send one prep brief — harmless for a single user.

**Acceptance checklist**

- [ ] `curl http://<LAN-IP>:8080/` from another LAN device times out / refuses; `http://<tailscale-IP>:8080/` serves the login page.
- [ ] Browser cookies contain a random token, not the `WEB_SECRET` value; Bearer-token API access with `WEB_SECRET` still works (`curl -H "Authorization: Bearer $WEB_SECRET" http://<ts-ip>:8080/api/sessions`).
- [ ] `/memories` then `/forget 3` deletes exactly the item shown as #3, including when several memories share a `created_at` second.
- [ ] Re-running `ingest.py` after a simulated crash (kill it between fact-save and move) does not duplicate memories, and the file still ends up in `Processed/`.
- [ ] A file whose move fails is retried on the next cron run instead of sitting in the inbox marked ingested.

**UX impact:** one re-login after each web-viewer restart (flagged above); everything else invisible. LAN access to the viewer goes away by design (D-2).
**Cost impact:** none.
**Effort:** ~half a day total.

---

## Execution order

WS-1 → WS-2 → WS-3 → WS-4 → WS-5, then WS-6 when convenient. WS-1/3/4 are independent of each other; WS-2 should merge before WS-5 so the new Drive tools land on top of the gated `run_tool`. Each workstream is independently shippable; test on the production box via `/restart` after each.
