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
| F-11 | High | Background-job failures are invisible: `error_handler` (agent.py:1045) only replies when `isinstance(update, Update)`; JobQueue jobs pass `update=None`, so briefing/meeting-prep/reminder exceptions are log-only | WS-7 |
| F-12 | High | Meeting prep marks an event prepped (`_prepped_events.add`, agent.py:1166) *before* the send; any transient Claude/Telegram failure permanently blacklists that meeting for the process lifetime — no retry | WS-8 |
| F-13 | High | Ingestion plain-text path has no zero-facts guard (ingest.py:472-485); an empty/failed extraction moves the file to `Processed/` with zero facts and never reaches `Failed/`. Same gap in xlsx/csv small-sheet fallbacks | WS-9 |
| F-14 | Medium | Briefing data-gathering runs outside the try (agent.py:1059-1067) over raw `conn.execute`; loop-cap-hit yields empty text → nothing sent (agent.py:1106,1124-1127); no retry/fallback | WS-7 |
| F-15 | Low | `deal:<company>` tag (ingest.py:479) is never read; `get_deal_info` reads only the deals table. ROADMAP claim "progressively richer" was false — resolved by making it real (WS-9, D-5) | WS-9 |
| F-16 | Low | No downtime catch-up for meeting prep: a restart/delayed tick during the 25-35 min window means the meeting is never prepped (reminders self-heal via `due_at <= now`; prep does not) | WS-8 (partial) |
| F-17 | High | Main chat agent is never told the current date/time anywhere: `BASE_SYSTEM` (agent.py:669-689, frozen/cached) has none, and `build_user_content` (agent.py:700-705, the per-turn injection point, called on every message at agent.py:857) only added `<relevant_memories>`. Claude had to guess "today" from its own internal sense of time — for direct date questions, relative scheduling ("in two weeks"), and computing `set_reminder`'s `due_at` (schema at agent.py:601-606 names a format but never a reference date). Found 2026-07-21 investigating "Dot doesn't know today's date" | Fixed inline, 2026-07-21 (see below) |

**F-17 fix (applied 2026-07-21, not a formal workstream — small, additive, same pattern as memories injection):** `build_user_content` now prepends `<current_datetime>{day}, {month} {date}, {year} {HH:MM} America/Toronto</current_datetime>` ahead of `<relevant_memories>`, computed fresh per turn via `datetime.now(ZoneInfo("America/Toronto"))` (inline import, matching codebase convention). Kept in the user turn rather than `BASE_SYSTEM` deliberately — the system block is cache-frozen for the whole session (see "Prompt caching that actually hits" in README.md), so a session spanning midnight would otherwise carry a stale date forever.

## Confirmed product decisions (Joey, 2026-07-07)

- **D-1:** Code-level confirmation gate for calendar writes involving attendees (create with attendees, update adding attendees, delete of events that have attendees). Gmail drafts stay prompt-guarded only — drafts never send, so no gate.
- **D-2:** Web viewer binds to the machine's Tailscale interface IP only (not `0.0.0.0`, not LAN).
- **D-3:** Google Drive integration uses the `drive.readonly` scope, mirroring the read-only Dropbox pattern.

## Confirmed product decisions (Joey, 2026-07-21)

- **D-4:** Dot proactively DMs Joey when a background job fails or loses work: a briefing that crashes or produces no content, a meeting-prep brief that fails, or a Dot Dump file that lands in `Failed/`. Messages are terse and throttled (not one per tick on a stuck retry).
- **D-5:** The `deal:<company>` tag ingest.py writes is made real, not dropped — `get_deal_info` is extended to surface ingested-document facts tagged for that company, delivering the "progressively richer" behavior the roadmap already (prematurely) claimed.
- **D-6:** The full remaining backlog — WS-7, WS-8, WS-9 (new, reliability), plus the already-approved WS-4, WS-6, WS-2, WS-3, WS-5 — is in scope for this execution pass, not deferred.

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

## WS-4 — Reminder timezone correctness (F-5) — ✅ Done, 2026-07-21

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
7. ~~`agent.py:822` — bound `_prepped_events`...~~ **Superseded by WS-9's sibling WS-8 (2026-07-21) — do not apply this item.** WS-8 rewrites `_prepped_events` handling entirely (bounding it is a side effect of fixing the mark-before-send bug, F-12); applying this item separately would collide with WS-8's edit to the same lines.

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

## WS-7 — Make background-job failures visible + briefing reliability (F-11, F-14)

**Goal:** No background job (briefing, meeting prep, reminders) can fail silently again. Every exception surfaces to Joey on Telegram, and the morning briefing always sends *something* — a fallback of the raw data if Claude formatting fails or produces nothing.

**Confirmed decisions:** D-4 (proactive failure notifications). Judgment call flagged: briefing tool-loop cap raised 5→8 to match meeting prep; reversal is one number.

**Steps — all in `agent.py`**

1. Add a shared owner-notify helper near the other job helpers (e.g. just above `error_handler`, ~line 1042). It must never raise (a failed notify can't crash the job):
   ```python
   async def _notify_owner(context, text: str):
       try:
           await context.bot.send_message(chat_id=YOUR_USER_ID, text=text[:4000])
       except Exception:
           logging.exception("Failed to notify owner")
   ```
2. Upgrade `error_handler` (agent.py:1043-1049) so job-triggered errors (where `update` is not an `Update`) still reach Joey — this is the foundational F-11 fix and catches every job exception generically:
   ```python
   async def error_handler(update, context):
       logging.error(f"Exception: {context.error}")
       if isinstance(update, Update) and update.effective_message:
           try:
               await update.effective_message.reply_text(f"⚠️ Error: {context.error}")
           except Exception:
               pass
       else:
           await _notify_owner(context, f"⚠️ Background job error: {context.error}")
   ```
3. `send_morning_briefing` (agent.py:1052-1129) — move **all** data-gathering inside the try and add a fallback + explicit notify. Restructure so the raw sections are assembled first (they're cheap strings), the try wraps both gather and format, and any failure path still sends the raw briefing:
   - Pull lines 1059-1067 (the `cal`/`email`/`due_today`/`stale` gathering and the `reminders_str`/`stale_str` assembly) **inside** the `try` at 1096.
   - Build a plain-text fallback string from those raw sections (`today_label`, `cal`, `email`, `reminders_str`, `stale_str`) before the Claude call, so it's available in every failure branch. Initialize `fallback = "Briefing data unavailable."` before the try, in case data-gathering itself raises before the real fallback is built.
   - After the tool loop, replace the `if text:` block (1125-1127) with:
     ```python
     if not text:
         text = fallback  # loop cap hit / no content — send raw data, not nothing
     for i in range(0, len(text), 4000):
         await context.bot.send_message(chat_id=YOUR_USER_ID, text=text[i:i+4000])
     ```
   - In the `except` (1128-1129), after logging, send the fallback and notify:
     ```python
     except Exception as e:
         logging.exception("Morning briefing error")
         await _notify_owner(context, f"⚠️ Briefing formatting failed ({e}). Raw briefing below.")
         for i in range(0, len(fallback), 4000):
             await context.bot.send_message(chat_id=YOUR_USER_ID, text=fallback[i:i+4000])
     ```
   - Raise the loop cap at line 1106 from `< 5` to `< 8`.
4. Reminder + meeting-prep except blocks (agent.py:1226-1227, 1236-1237): these already `logging.error`; the upgraded `error_handler` does **not** catch them because they're caught locally. Leave the local catch (it preserves the loop) but add a `_notify_owner` call alongside the log so Joey learns of persistent failures. For reminders, to avoid per-tick spam on a stuck reminder, only notify when the same reminder id has failed before (a small module-level `_reminder_fail_counts` dict, notify on the 3rd consecutive failure). Flag this throttle as a judgment call.

**Acceptance checklist**

- [ ] Temporarily raise an exception inside `send_morning_briefing`'s data-gather (e.g. rename `get_stale_deals`); confirm Joey receives a `⚠️ Background job error` DM (via error_handler) or the fallback+notify, not silence.
- [ ] Simulate empty model output (force `text=""`); confirm the raw fallback briefing is sent, not nothing.
- [ ] A raised exception in `check_meeting_prep`'s calendar fetch produces a Telegram DM to Joey.
- [ ] Normal briefing on a healthy day is unchanged (formatted, single message set).
- [ ] `venv/bin/python -c "import agent"` imports clean.

**UX impact:** additive — Joey now receives failure DMs and always gets a briefing (formatted normally, raw on failure). No change on healthy days.
**Cost impact:** none (reuses existing Telegram sends; fallback path makes *fewer* API calls, not more).
**Effort:** ~half a day including forced-failure testing via `/restart`.

---

## WS-8 — Meeting-prep mark-before-send fix + bounded dedupe (F-12, F-16; supersedes WS-6 6c item 7)

**Goal:** A transient Claude/Telegram failure no longer permanently blacklists a meeting. An event is only recorded as prepped *after* a successful send; failures retry on the next 5-minute tick within the window. A slow-but-successful Claude call spanning two ticks does not double-send. `_prepped_events` is bounded so it can't grow unboundedly.

**Confirmed decisions:** Reordering to mark-after-send is the core fix. Judgment call flagged: an in-flight guard prevents duplicate sends across overlapping/adjacent ticks without relying on APScheduler's `max_instances` behavior. Accepted limitation (unchanged from old plan): a `/restart` mid-window can re-send one brief — harmless for a single user.

**Steps — all in `agent.py`**

1. Replace the `_prepped_events: set` global (agent.py:822) with a bounded, timestamped structure plus an in-flight set:
   ```python
   _prepped_events: dict = {}   # event_id -> datetime added (successful sends only)
   _prepping_now:  set = set()  # event_ids currently being prepped (in-flight guard)
   ```
2. At the top of `check_meeting_prep` (after computing `now`, ~1138), prune entries older than 2 hours so the dict can't grow without bound:
   ```python
   cutoff = now - timedelta(hours=2)
   for eid in [k for k, t in _prepped_events.items() if t < cutoff]:
       del _prepped_events[eid]
   ```
3. Rewrite the per-event guard and the `add` placement:
   - Line 1156 becomes: `if event_id in _prepped_events or event_id in _prepping_now: continue`
   - **Delete** the premature `_prepped_events.add(event_id)` at line 1166.
   - Keep the `if not external: continue` (1168-1169) — a no-external event should just be skipped every tick; it never sends, so it must not be recorded as "prepped" (recording it is harmless but pointless; simplest is to leave it unrecorded).
   - Immediately before the Claude call (before line 1194), mark in-flight: `_prepping_now.add(event_id)`.
   - On successful send (inside the `if text:` at 1223-1225, after the send): `_prepped_events[event_id] = now`.
   - In a `finally` for the prep try/except (1194-1227): `_prepping_now.discard(event_id)` — so a failed prep leaves the event un-prepped and eligible for retry next tick, while the in-flight guard is always cleared.
   - In the `except` (1226-1227): keep the log, add `await _notify_owner(context, f"⚠️ Couldn't prep '{title}' — will retry.")` per D-4. To avoid per-tick spam across the 10-minute window, only notify once per event (track a `_prep_notified` set, or reuse a fail-count as in WS-7 step 4) — flag as judgment call.
4. Interaction note for the executor: this **replaces** WS-6 section 6c item 7 entirely. Do not also apply that item.

**Slow-call-spanning-two-ticks reasoning (for the reviewer):** with the in-flight guard, tick N adds `event_id` to `_prepping_now` before the (blocking, via `asyncio.to_thread`) Claude call. If tick N+1 fires while N is still awaiting, `event_id in _prepping_now` is true → skipped, no duplicate. On success N records it in `_prepped_events`; on failure the `finally` clears in-flight and N+1 (still in window) retries. This is correct regardless of whether PTB serializes the job.

**Acceptance checklist**

- [ ] Force `run_tool`/send to fail on the first tick for a windowed external meeting; confirm the event is NOT in `_prepped_events`, a retry-notice DM arrives once, and the next tick re-attempts and (on success) sends the brief.
- [ ] On a successful prep, the event lands in `_prepped_events` and is not re-sent on subsequent ticks in the window.
- [ ] `_prepped_events` entries older than 2h are pruned (unit-check with a back-dated timestamp).
- [ ] A meeting with no external attendees is silently skipped every tick, no send, no error.

**UX impact:** additive — meetings that previously got silently skipped now get prepped reliably; one retry-notice DM on transient failure (D-4). No change to successful preps.
**Cost impact:** negligible — a failed prep may retry once or twice within the 10-minute window (2-3 extra Claude calls at most, only on failure days).
**Effort:** ~half a day including forced-failure testing.

---

## WS-9 — Ingestion: no silent document loss + Failed/ notifications + deal-tag resolution (F-13, F-15)

**Goal:** A document can never move to `Processed/` with zero facts saved — a zero-facts result routes to `Failed/` (mirroring the native-PDF path) so it's retryable and visible. Joey is DMed when a file lands in `Failed/`. The `deal:<company>` tag is made real per D-5.

**Confirmed decisions:** D-4 (notify on `Failed/`). D-5 (make the deal link real — `get_deal_info` surfaces ingested-document facts).

**Steps**

1. `ingest.py` `run()` — close the plain-text zero-facts gap (F-13). After `facts = extract_facts_with_claude(text, entry.name)` (474) and before `fact_count = len(facts)` (475), add the same guard the native-PDF path already uses:
   ```python
   if not facts:
       print(f"  No facts extracted from text. Moving to Failed.")
       move_file(entry.path_display, FAILED_FOLDER, entry.name)
       continue
   ```
2. `ingest.py` `run()` — close the xlsx/csv zero-facts gap. After the structured branch computes `fact_count` (452-455), before `mark_ingested` (485), add:
   ```python
   if fact_count == 0:
       print(f"  Structured ingestion produced 0 memories. Moving to Failed.")
       move_file(entry.path_display, FAILED_FOLDER, entry.name)
       continue
   ```
   (This also covers a small-sheet Claude failure returning `[]`, since those flow up as `fact_count == 0`.)
3. `ingest.py` — add a terse Telegram notifier (no new dependency; `requests` and `.dot.env` are already loaded). Near the config block (~line 40):
   ```python
   TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
   TELEGRAM_CHAT_ID = os.getenv("YOUR_TELEGRAM_USER_ID")

   def notify_owner(text: str):
       if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
           return
       try:
           requests.post(
               f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
               json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10,
           )
       except Exception as e:
           print(f"  Telegram notify failed: {e}")
   ```
   Call `notify_owner(f"⚠️ Dot Dump: couldn't process {entry.name} — moved to Failed/")` at each `Failed/` move in `run()` (the two new guards above, the existing native-PDF/no-text branches at 465-470, and the outer `except`'s move at 494). Cap notifications per run (e.g. the first 5 failures, then a summary line) so a bad batch can't spam — flag as judgment call.
4. `agent.py` `get_deal_info` (435-446) — D-5: make the `deal:` tag real. After the existing deal block, append memories tagged for this company:
   ```python
   from memory import conn as _mem_conn
   rows = _mem_conn.execute(
       "SELECT content FROM memories WHERE tags LIKE ? ORDER BY id DESC LIMIT 10",
       (f"%deal:{deal['company']}%",),
   ).fetchall()
   if rows:
       lines.append("\nFrom ingested documents:")
       lines.extend(f"- {r[0]}" for r in rows)
   ```
   Then update ROADMAP.md to remove the ⚠ correction added 2026-07-21 (the claim is now true) and note the ship date.

**Acceptance checklist**

- [ ] Drop a `.txt`/`.docx` that yields no extractable facts (or force `extract_facts_with_claude` to return `[]`); confirm the file lands in `Failed/`, `mark_ingested` did NOT run for it, and Joey gets a `Failed/` DM.
- [ ] Same for a small xlsx/csv whose Claude pass returns nothing.
- [ ] A healthy document still processes to `Processed/` with facts, no DM.
- [ ] A file in `Failed/` is retried on the next cron run (not marked ingested) — verifies the fix composes with WS-6's move/mark reorder.
- [ ] `get_deal_info("<company>")` surfaces ingested memories for a company with tagged facts; ROADMAP.md correction removed.

**UX impact:** additive — documents that used to vanish now reliably reach `Failed/` for retry, and Joey is told. `get_deal_info` gets richer as ingestion happens, as originally promised.
**Cost impact:** none — Telegram sends are free; no extra Claude calls (zero-facts files are moved, not re-processed within a run). WS-6's dedupe guard prevents retry from duplicating facts.
**Effort:** ~half a day; deal-tag step adds ~1 hour and one manual re-check against a known company.

---

## Execution order

WS-7 → WS-8 → WS-9 first (2026-07-21 reliability pass — WS-7 is foundational since it's what makes every other job's failures visible; do not reorder). Then the remaining approved backlog: WS-4 → WS-6 (ingestion idempotency half only — 6c item 7 is superseded by WS-8, skip it) → WS-2 → WS-3 → WS-5. WS-2 should merge before WS-5 so the new Drive tools land on top of the gated `run_tool`. WS-1/3/4 are independent of each other. Each workstream is independently shippable; test on the production box via `/restart` after each.
