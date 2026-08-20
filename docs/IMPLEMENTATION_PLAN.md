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
| F-18 | High | `/restart` (`cmd_restart`) only ever called `sys.exit(0)` and relied on systemd to relaunch — it never pulled from git, so pushed commits never reached the running bot. `ROADMAP.md`'s "Remote restart" entry falsely claimed it "deploy[s] code changes from anywhere." Found 2026-07-21 investigating why the F-17 fix didn't take effect after a push + `/restart` | Fixed inline, 2026-07-21 (see below) |
| F-19 | Medium | `read_granola` (agent.py:401) crashes with `TypeError: 'NoneType' object is not subscriptable` when a note's `calendar_event.scheduled_start_time` is present but explicitly `null` (ad-hoc notes not linked to a calendar event) — `.get(key, default)` only supplies the default when the key is *missing*, not when it's null; every sibling field in the same function correctly uses the `x.get(key) or default` guard instead. The crash is swallowed by the function's own `except` and surfaces as an opaque "Granola read error" instead of the note content. Found 2026-07-22 investigating "bug reading notes from Granola" | Fixed inline, 2026-07-22 (see below) |
| F-20 | Low | Not a code bug — operational gap. `token_work.pickle` predates the `drive.readonly` scope WS-5 added to `auth_work.py`'s `SCOPES`; OAuth tokens only carry scopes consented to at issuance, so Drive calls (`agent.py:35-38` share one token across Gmail/Calendar/Drive) 403 with "insufficient authentication scopes" until the token is regenerated. README's "if upgrading" note (line 227) only mentioned the earlier `gmail.compose` bump, not this one. Found 2026-07-22 investigating "Drive integration throwing an auth error" | Docs corrected inline, 2026-07-22 (see below); remediation is a manual step on the server, not a code change |

**F-17 fix (applied 2026-07-21, not a formal workstream — small, additive, same pattern as memories injection):** `build_user_content` now prepends `<current_datetime>{day}, {month} {date}, {year} {HH:MM} America/Toronto</current_datetime>` ahead of `<relevant_memories>`, computed fresh per turn via `datetime.now(ZoneInfo("America/Toronto"))` (inline import, matching codebase convention). Kept in the user turn rather than `BASE_SYSTEM` deliberately — the system block is cache-frozen for the whole session (see "Prompt caching that actually hits" in README.md), so a session spanning midnight would otherwise carry a stale date forever.

**F-18 (found 2026-07-21, investigating why F-17 didn't take effect after `/restart`):** `/restart` (agent.py `cmd_restart`) only ever called `sys.exit(0)` and relied on systemd to relaunch — it never pulled from git, so pushing a fix to GitHub did not deploy it; `/restart` just restarted whatever code was already on the server's disk. `ROADMAP.md`'s "Remote restart" entry claimed `/restart` lets you "deploy code changes from anywhere," which was false. **Fixed 2026-07-21:** `cmd_restart` now runs `git pull --ff-only` in the repo directory and replies with the result before exiting, so `/restart` genuinely deploys pushed commits going forward. One-time bootstrapping caveat: this fix itself has to reach the server through a manual `git pull` (or an old-style bare `/restart`) once, since the old `/restart` can't pull a fix to itself.

**F-19 fix (applied 2026-07-22):** `read_granola` (agent.py:401) changed from `(data.get("calendar_event") or {}).get("scheduled_start_time", "")[:10]` to `((data.get("calendar_event") or {}).get("scheduled_start_time") or "")[:10]` — matches the `x.get(key) or default` guard already used by every other field in the function (attendees, summary, transcript). Verified against null, missing-key, and normal-value cases.

**F-20 (found 2026-07-22, investigating "Drive integration throwing an auth error"):** no code change — `search_drive`/`read_drive_file` are correct; the deployed `token_work.pickle` simply predates the `drive.readonly` scope, same class of issue the README already warned about for the `gmail.compose` bump but never generalized. **Docs fix applied 2026-07-22:** README's "if upgrading" note (Google OAuth section) now covers any future scope addition generically, and adds the reminder to check the corresponding Google API is enabled in Cloud Console (a separate 403 cause from OAuth scope). **Remediation on the server (manual, not yet done as of this writing):** confirm the Drive API is enabled in Google Cloud Console for the project, delete `token_work.pickle`, re-run `venv/bin/python auth_work.py` — this needs a reachable browser for the `localhost:8081` OAuth redirect (tunnel with `ssh -L 8081:localhost:8081` if the server is headless).

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

---

# 2026-08-20 review — document read cache + verbatim deck reads (pre-implementation)

Scope of the review: the proposed `doc_cache` feature (cache ingest-time parsed text; have
`read_dropbox_file` / `read_drive_file` read from it) and the open question about image-only PDFs.
Every claim below was checked against the source, and against live Dropbox metadata and `dot.db`
on the production box (read-only probes).

## Findings register (2026-08-20 review)

**F-21 (blocking design flaw in the proposal as written):** keying the cache on the Dropbox path
that `ingest.py` sees would produce a cache that can *never* hit. `run()` moves the file to
`Processed/` (ingest.py:555) *before* recording it, and `mark_ingested` stores `entry.path_lower`
— the pre-move inbox path (ingest.py:73-78, 557). Confirmed in `dot.db`: `ingested_files` rows read
`/dot dump/pagrin.pdf`, while `files_search_v2` — the source of the `file_path` argument
`read_dropbox_file` is called with, via `search_dropbox` printing `meta.path_display`
(agent.py:435) — returns `/Dot Dump/Processed/pagrin.pdf`. Two further path hazards: `move_file`
passes `autorename=True` (ingest.py:413), so a name collision silently renames the file; and
`path_lower` vs `path_display` differ in case. **Fix:** key on the Dropbox file `id`, which is
stable across moves and renames (verified: `pagrin.pdf` is `id:xpA2TiOZ9_gAAAAAAAFjKw` in
`Processed/`, and `files_get_metadata` accepts an id *or* a path). Use `content_hash` — not `rev`
— as the version column: it is derived from bytes, so a move or rename provably cannot invalidate
it. Both fields are on every `FileMetadata` (dropbox SDK 12.0.2; `rev`, `content_hash`, `id` all
present), so `list_inbox()` already has everything ingest needs.

**F-22 (real bug, independent of caching):** the live re-read of an image-only PDF does not return
"garbage" — it returns *whitespace with no error marker*. `read_dropbox_file` (agent.py:501-502)
does `" ".join(page.extract_text() or "" for page in reader.pages)[:3000]`, so a 19-page image deck
returns 18 space characters. There is no `[...]` marker, so Claude receives an apparently-successful
empty tool result and will either invent content or tell Joey the file is empty. `read_drive_file`
has the identical bug (agent.py:552). Measured incidence on the 15 most recently ingested decks:
4 of 15 (27%) have a zero-length text layer — CreativAI, Bloccpay, Clusterlab, Lukhu.

**F-23 (premise correction):** "ingest.py parses each new file once via `extract_text()`" is true
only for `.pdf/.docx/.pptx/.txt/.md`. `.xlsx/.xls/.csv` never call `extract_text` — they branch to
`ingest_structured_xlsx` / `ingest_structured_csv` (ingest.py:499-512), and the large-file path
never materialises a single text blob at all (it iterates rows straight into memories,
ingest.py:348-358). So a "write the cache right after `extract_text()`" hook covers neither format.
Related: `read_dropbox_file` cannot read `.pptx/.xlsx/.csv` today at all — it falls through to
`[File type not readable as text: ...]` (agent.py:509-510). A cache-first read is therefore an
*upgrade path* for `.pptx` (ingest can already parse it) at no extra cost; xlsx/csv need a separate
decision and are excluded below.

**F-24 (truncation mismatch is larger than assumed):** `extract_text` caps every format at 15,000
chars (ingest.py:100, 108, 130, 149, 159, 164); the read tools cap at 3,000 on return
(agent.py:496, 502, 508, 545, 548, 552, 556). Measured on real ingested files: the 2025 Africa VC
Exit report has 250,204 chars of text layer (ingest saw 6% of it; a live read shows 1.2%), and
`2026_HustleSasa_BusinessOverview.pdf` has 57,099. Consequences: (a) the cache must store the
*full* parse, not the 15,000-truncated ingest value, or the ingest cap gets baked into every future
read; (b) caching alone does not make a long document readable — the 3,000-char return cap is the
binding limit and needs its own decision (D-8).

**F-25 (Drive is a much weaker case than Dropbox):** `ingest.py` only ever watches Dropbox
(`INBOX_FOLDER = "/Dot Dump"`, ingest.py:51) — no Drive file is ever ingested. A `doc_cache` for
Drive can therefore only be populated by a live parse that already succeeded, so its benefit is
latency/bandwidth only and it does nothing for the image-PDF problem. It is still worth doing
because it is nearly free: `read_drive_file` already calls `files().get(...)` (agent.py:541), so
adding `version, modifiedTime` to the `fields` string costs zero extra API calls. Dropbox, by
contrast, pays a real added call — measured `files_get_metadata` latency 0.18-0.31s per read.
(Use `version` for the revision column, not `md5Checksum`: native Docs/Sheets/Slides have no
checksum.)

**F-26 (decides the open question):** "cache the facts bullets as a lossy stand-in" is, in effect,
already shipped. Ingested facts are tagged `source:<filename>` (ingest.py:544) and are reachable two
ways at conversation time — passive per-turn injection via `retrieve_relevant_memories`, and the
`search_memory` tool (agent.py:480-484, schema at :696, registered at :730). Option (a) would
therefore store a second copy of data the agent can already retrieve, while introducing a new
failure mode: distilled bullets delivered in a slot the model reads as "the document's contents",
which invites Claude to present a summary as if it were verbatim source. Recommendation: **option
(b)**, with the ordering safeguard in F-27.

**F-27 (risk in option (b) as proposed):** asking one Claude call for facts *and* a full
transcription at `max_tokens` 6000-8000 creates a silent-document-loss path of exactly the kind
WS-9 was written to close. If the response is cut at `max_tokens` mid-transcription, the JSON no
longer parses, `parse_json_array` returns `[]` (memory.py:288-299), and ingest.py:521-525 moves the
deck to `Failed/`, losing the facts too. Mitigation is cheap: require facts first, then a
`===TRANSCRIPT===` delimiter, and parse the facts out of the prefix — truncation then costs only
the transcript tail. Do not modify the shared `parse_json_array`; split in `ingest.py` before
calling it.

## Product decisions (Joey, 2026-08-20) — all three confirmed per Felix's recommendation

- **D-7 → (b), full transcription at ingest. Confirmed.**
- **D-8 → (iii), `offset` paging argument on `read_dropbox_file`/`read_drive_file`. Confirmed.**
- **D-9 → yes, gated live vision fallback, shipped last (WS-13). Confirmed.**

Rationale for each retained below as written during review.

- **D-7 (the open question) — recommend option (b), full transcription at ingest.** Rationale in
  F-26. Cost: transcription output is roughly 4,000-6,000 output tokens for a 16-page deck ≈
  $0.04-0.06 at $10/M, on top of the current $0.10-0.20 per image deck — i.e. ~25-30% more for the
  ~27% of decks that are image-only. At the observed intake rate (206 documents since ~mid-June ≈
  80/month, ~22 of them image-only) that is **~$1/month**, inside the existing Anthropic line, no
  new cost line. In exchange, every later re-read of that deck is free and instant. Option (a) is
  cheaper by that ~$1/month and buys nothing that `search_memory` does not already provide.
- **D-8 — the 3,000-char return cap (F-24).** Caching does not change what Joey sees; the cap does.
  Options: **(i)** leave 3,000 as-is (cache is a latency/robustness win only); **(ii)** raise the cap
  for cache hits only, to ~12,000 chars (~3k tokens, which then sits in conversation history and is
  re-read at the 0.1× cache rate every subsequent turn); **(iii)** add an optional `offset` argument
  to `read_dropbox_file` / `read_drive_file` so Claude can page through a long document on demand,
  keeping the default page at 3,000. **Recommendation: (iii)**, with (ii) at 8,000 as a simpler
  fallback if you would rather not touch the tool schema. (iii) keeps the common case cheap and
  makes long reports genuinely readable; it is the only option that helps the 250k-char report.
- **D-9 — live vision fallback for image PDFs that were never ingested (WS-13).** Most of Joey's
  Dropbox never passed through `/Dot Dump`, so the cache cannot help those files. Sending the PDF to
  Claude natively from inside the tool would fix them, at $0.10-0.20 and 20-60s of added latency on
  that conversational turn, cached thereafter. **Recommendation: yes, but ship it last and gate it**
  — only when the local parse yields <200 chars, only under the existing size/page caps, and with
  the result cached so the cost is paid once per file. Say no if you would rather image decks always
  go through `/Dot Dump`.

---

## WS-10 — Stop returning blank text for image-only PDFs (F-22)

**Goal:** a live read of a PDF with no text layer returns an explicit, honest marker instead of a
string of spaces, so Claude never treats an empty extraction as a successful read. Independent of
the cache work and worth shipping on its own.

**Confirmed decisions:** none needed.

**Steps — all in `agent.py`**

1. Add a small shared guard near the read tools (above `read_dropbox_file`, agent.py:490), matching
   the existing "errors are strings, never raise" convention:
   ```python
   _MIN_TEXT_CHARS = 200  # same threshold ingest.py uses to detect an image-only PDF

   def _pdf_text_or_marker(content: bytes, label: str) -> str:
       """Extract a PDF's text layer, or return an explicit marker for image-only PDFs."""
       import PyPDF2
       reader = PyPDF2.PdfReader(io.BytesIO(content))
       text = "\n".join(page.extract_text() or "" for page in reader.pages)
       if len(text.strip()) < _MIN_TEXT_CHARS:
           return (f"[{label}: {len(reader.pages)}-page PDF with no text layer (image-based deck). "
                   f"Nothing could be extracted locally. Check long-term memory for facts already "
                   f"ingested from this file before telling Joey anything about its contents.]")
       return text
   ```
   Note the join changes from `" "` to `"\n"`, matching `extract_text` in ingest.py:100 — page
   boundaries currently collapse into a single space in the agent's copy.
2. `read_dropbox_file` (agent.py:497-504) — replace the inline PyPDF2 block with
   `return _pdf_text_or_marker(content, metadata.name)[:3000]`, keeping the existing
   `except Exception as e: return f"[PDF read error: {e}]"` wrapper.
3. `read_drive_file` (agent.py:549-552) — same substitution using `meta['name']`.
4. `README.md` — under "What the agent can do", note that image-based PDFs are readable via
   ingestion (Dropbox `/Dot Dump`) but not via a live re-read, until WS-12/WS-13 land. Remove this
   line when they do.

**Acceptance checklist**

- [ ] `read_dropbox_file("/Dot Dump/Processed/Bloccpay_June_2026.pdf")` returns the `[...no text
      layer...]` marker, not whitespace (this file measured 0 extractable chars).
- [ ] `read_dropbox_file("/Dot Dump/Processed/pagrin.pdf")` still returns real text (3,025 chars
      measured, so the 200-char threshold is not near the boundary).
- [ ] A text-layer PDF's output now has newlines between pages, not spaces.
- [ ] `read_drive_file` on a scanned PDF in Drive behaves the same way.

**UX impact:** strictly additive — a case that silently produced nothing now says so. No change to
any read that works today.
**Cost impact:** none.
**Effort:** ~1 hour.

---

## WS-11 — `doc_cache`: identity-keyed parsed-document cache (F-21, F-23, F-24, F-25)

**Goal:** a document parsed once is never re-parsed while its bytes are unchanged; `read_dropbox_file`
answers from `dot.db` when the cached copy still matches the live file, and backfills the cache on
any miss. Ingest populates it for free as a side effect of work it already does.

**Confirmed decisions:** D-8 (needed before implementing step 5 — the rest of the workstream is
decision-independent).

**Design:** the key is `(source, file_key)` where `file_key` is the Dropbox file `id` or the Drive
`fileId` — both stable across moves and renames (F-21). The freshness column is `revision`:
`content_hash` for Dropbox, `version` for Drive. A revision mismatch is treated as a plain miss —
re-parse and overwrite — so the worst case of any staleness bug is a wasted parse, never a wrong
answer.

**Steps**

1. `memory.py` — extend the existing `conn.executescript(...)` block (memory.py:18-51) with an
   additive table. No existing table or column is touched:
   ```sql
   CREATE TABLE IF NOT EXISTS doc_cache (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       source TEXT NOT NULL,              -- 'dropbox' | 'drive'
       file_key TEXT NOT NULL,            -- Dropbox file id / Drive fileId — stable across moves
       revision TEXT NOT NULL DEFAULT '', -- Dropbox content_hash / Drive version
       filename TEXT DEFAULT '',
       kind TEXT DEFAULT 'text',          -- 'text' (local parse) | 'vision' (Claude transcription)
       content TEXT NOT NULL,
       char_count INTEGER DEFAULT 0,
       created_at TEXT DEFAULT (datetime('now')),
       updated_at TEXT DEFAULT (datetime('now')),
       last_read_at TEXT,
       UNIQUE (source, file_key)
   );
   ```
   `last_read_at` exists so an eviction policy can be added later without a migration; do not write
   one now (sizing in the cost statement below).
2. `memory.py` — add the two accessors below `save_memory` (memory.py:63-75), same style, never
   raising into callers:
   ```python
   def get_cached_doc(source: str, file_key: str, revision: str) -> str | None:
       """Cached parse for this exact file revision, or None. A revision mismatch is a miss."""
       row = conn.execute(
           "SELECT content FROM doc_cache WHERE source = ? AND file_key = ? AND revision = ?",
           (source, file_key, revision),
       ).fetchone()
       if not row:
           return None
       conn.execute(
           "UPDATE doc_cache SET last_read_at = datetime('now') WHERE source = ? AND file_key = ?",
           (source, file_key),
       )
       conn.commit()
       return row[0]

   def save_cached_doc(source: str, file_key: str, revision: str, filename: str,
                       content: str, kind: str = "text"):
       if not content or content.startswith("["):
           return  # never cache an error marker or an empty parse
       try:
           conn.execute(
               "INSERT INTO doc_cache (source, file_key, revision, filename, kind, content, char_count) "
               "VALUES (?, ?, ?, ?, ?, ?, ?) "
               "ON CONFLICT(source, file_key) DO UPDATE SET "
               "revision=excluded.revision, filename=excluded.filename, kind=excluded.kind, "
               "content=excluded.content, char_count=excluded.char_count, updated_at=datetime('now')",
               (source, file_key, revision, filename, kind, content, len(content)),
           )
           conn.commit()
       except Exception as e:
           print(f"doc_cache write error: {e}")
   ```
   (SQLite 3.46.1 on the box — UPSERT is supported.)
3. `ingest.py` — populate on the text path only (F-23: the xlsx/csv branches have no single text
   blob and `read_dropbox_file` cannot read those types anyway). Import alongside the existing
   memory import (ingest.py:56), then in `run()` after the successful-extraction branch prints
   `Extracted {len(text):,} chars` (ingest.py:532):
   ```python
   save_cached_doc("dropbox", entry.id, entry.content_hash, entry.name, text)
   ```
   `entry` is the `FileMetadata` from `list_inbox()` (ingest.py:424) and carries `id` and
   `content_hash` — verified against dropbox 12.0.2. Cache before the `Processed/` move; the id and
   content hash both survive it.
   **Note the 15,000-char ceiling (F-24):** `extract_text` truncates, so this caches a truncated
   document for large files. Accept it for now — it is exactly what ingest itself read, and a live
   read of an uncached long file will store the full parse via step 4. Flagging as a deliberate,
   cheap-to-reverse call: lifting it means giving `extract_text` a `limit` parameter defaulting to
   15000 and passing `None` for the cache write.
4. `agent.py` `read_dropbox_file` (agent.py:490-512) — metadata first, then cache, then parse:
   ```python
   def read_dropbox_file(file_path: str):
       try:
           meta = dbx.files_get_metadata(file_path)   # ~0.2-0.3s, no bytes transferred
           cached = get_cached_doc("dropbox", meta.id, meta.content_hash)
           if cached:
               return cached[:3000]
           metadata, response = dbx.files_download(file_path)
           content = response.content
           name = metadata.name.lower()
           ...                                        # existing per-type parsing, unchanged
           save_cached_doc("dropbox", meta.id, meta.content_hash, metadata.name, text)
           return text[:3000]
       except Exception as e:
           return f"Dropbox read error: {e}"
   ```
   Restructure the per-type branches to assign `text` and fall through to one shared
   cache-write/return, rather than returning from each branch. Keep `[File type not readable as
   text: ...]` markers returning early and uncached (`save_cached_doc` also guards on `[`).
   Bonus at no cost: with the cache checked before the extension test, a `.pptx` that ingest already
   parsed becomes readable live — update the `read_dropbox_file` tool description (agent.py:654) to
   say "PDF, DOCX, TXT, MD (plus PPTX when previously ingested)".
5. `agent.py` — apply D-8 once decided. If (iii): add `"offset": {"type": "integer", "default": 0}`
   to both tool schemas (agent.py:653-657, 666-670), slice `text[offset:offset+3000]`, and append
   `f"\n[... {len(text) - offset - 3000} more characters — call again with offset={offset+3000}]"`
   when truncated, so Claude knows more exists. Mention paging in `BASE_SYSTEM`'s tool list
   (agent.py:745-746).
6. `agent.py` `read_drive_file` (agent.py:539-559) — same pattern, zero extra API calls (F-25):
   change line 541 to `fields="name, mimeType, version, modifiedTime"`, key on
   `("drive", file_id, meta["version"])`, and write the cache after any successful parse or export.
7. `agent.py` — one judgment call to flag in the commit message: `memory.py`'s connection is
   `check_same_thread=False` and is shared between the bot process and the cron ingest process, with
   `journal_mode=delete` and a 5s busy timeout (verified on the box). Adding 15-50 KB writes widens
   the window for `database is locked` during a cron run. Cheap mitigation, additive and reversible
   with `PRAGMA journal_mode=delete`: set `conn.execute("PRAGMA journal_mode=WAL")` right after the
   connect in memory.py:17. Recommend doing it in this workstream; call it out explicitly so it can
   be backed out on its own.

**Acceptance checklist**

- [ ] Drop a fresh text PDF into `/Dot Dump`, run ingest, then `SELECT source, file_key, revision,
      filename, char_count FROM doc_cache` shows one row keyed by `id:...`.
- [ ] Immediately afterwards, `read_dropbox_file("/Dot Dump/Processed/<name>.pdf")` — the *post-move*
      path — returns the cached text (F-21's core regression test). Confirm with a log line or by
      checking `last_read_at` moved.
- [ ] A file never ingested reads live, then produces a `doc_cache` row; the second read does not
      re-download (verify by timing, or by temporarily logging the cache branch).
- [ ] Edit a cached Dropbox file (append a line), read again — content reflects the edit and
      `updated_at`/`revision` changed.
- [ ] Rename or move a cached file in Dropbox, read at the new path — still a cache hit, no
      duplicate row (this is what `content_hash` + `id` buy).
- [ ] `read_drive_file` on a Google Doc caches and re-serves; editing the doc busts it.
- [ ] `[File type not readable as text: ...]` and error strings never land in `doc_cache`.
- [ ] Bot restarts cleanly on a database that already has the table, and on one that doesn't
      (fresh-install path through `executescript`).

**UX impact:** additive and mostly invisible. Re-reads get faster; long documents behave as they do
today unless D-8 (iii) is taken, in which case Claude gains the ability to page further into a
document it previously could only see the first 3,000 chars of. No existing read regresses: a cache
miss is exactly today's code path.
**Cost impact:** no new cost line. Dropbox adds one metadata call per read (~0.2-0.3s, free tier,
no bytes); Drive adds nothing. Storage: 206 ingested documents at ≤15 KB each ≈ 3 MB against a
current 8 MB `dot.db`; even 1,000 cached documents with transcriptions is ~25 MB. Claude spend is
unchanged by this workstream (it only avoids local re-parsing).
**Effort:** ~half a day, plus ~1 hour for D-8 (iii).

---

## WS-12 — Full deck transcription at ingest for image-only PDFs (D-7, F-26, F-27)

**Goal:** the ~27% of decks with no text layer get a verbatim markdown transcription stored in
`doc_cache` at ingest time, so a live re-read returns the actual deck contents instead of a marker —
at zero marginal cost on every read after the first. Depends on WS-11.

**Confirmed decisions:** D-7 (pending Joey — recommendation: option (b), transcription).

**Steps — all in `ingest.py`**

1. Extend `EXTRACTION_SYSTEM` (ingest.py:170-191) with a transcription contract *after* the existing
   facts contract, ordered so facts are emitted first (F-27):
   ```
   After the JSON array, output a line containing exactly ===TRANSCRIPT=== and then a faithful
   markdown transcription of the document: every slide/page in order, headed "## Slide N", with all
   visible text, numbers, table contents, and chart labels. Describe images only when they carry
   information the text does not. Do not summarise or editorialise.
   ```
   Only the native-PDF call should ask for this; keep the text path's prompt as-is (it already has
   the text locally). Simplest way to avoid drift: define `PDF_EXTRACTION_SYSTEM = EXTRACTION_SYSTEM
   + "\n\n" + TRANSCRIPT_CONTRACT` and pass it only at ingest.py:243.
2. `extract_facts_from_pdf_with_claude` (ingest.py:220-263) — raise `max_tokens` from 2000 to 8000
   (ingest.py:242) and return both parts:
   ```python
   raw = response.content[0].text
   head, _, transcript = raw.partition("===TRANSCRIPT===")
   facts = parse_json_array(head)
   if not facts:
       facts = parse_json_array(raw)   # model ignored the delimiter — behave exactly as before
   facts = [f for f in facts if isinstance(f, str) and len(f) > 10]
   return facts, transcript.strip()
   ```
   Change the signature to `-> tuple[list, str]` and update the two early-return paths
   (ingest.py:229, 235) to `return [], ""`. This is the F-27 safeguard: a response truncated at
   `max_tokens` loses transcript tail only — the facts, already parsed from the prefix, still save
   and the deck still reaches `Processed/`.
3. `run()` (ingest.py:520) — unpack and cache:
   ```python
   facts, transcript = extract_facts_from_pdf_with_claude(content, entry.name)
   if not facts:
       ...  # unchanged Failed/ handling
   if transcript:
       save_cached_doc("dropbox", entry.id, entry.content_hash, entry.name, transcript, kind="vision")
   ```
   Failure to transcribe must never route a deck to `Failed/` — the `if not facts` guard stays the
   only gate.
4. `README.md` — in "Ingestion pipeline", extend the image-based-PDF bullet: Claude now also returns
   a markdown transcription which is cached so later live reads of the deck are free. Update the
   "Designed pitch decks" line in "Running costs" from $0.10-0.20 to $0.15-0.25 per deck.
5. `ROADMAP.md` — move the planned entry added by this review into Shipped when this lands.

**Acceptance checklist**

- [ ] Re-drop a known image-only deck (e.g. `Clusterlab - Pitch deck.pdf`, measured 0 extractable
      chars) into `/Dot Dump`; ingest produces the usual 5-20 facts **and** a `doc_cache` row with
      `kind='vision'` whose length is in the thousands of characters.
- [ ] `read_dropbox_file` on that deck's `Processed/` path returns slide text, not the WS-10 marker.
- [ ] Spot-check the transcription against the actual slides for 2-3 slides — numbers and company
      names must match, not be paraphrased.
- [ ] Force a truncated response (temporarily set `max_tokens=300`) and confirm the deck still gets
      its facts and still reaches `Processed/` — no `Failed/`, no Telegram alert (F-27 regression).
- [ ] A text-layer PDF is unaffected: no second Claude call, no `kind='vision'` row, same fact count
      as before.
- [ ] `parse_json_array` in `memory.py` is unchanged (the delimiter split happens in `ingest.py`).

**UX impact:** additive. Facts extraction behaves exactly as today; the only visible change is that
asking Dot to re-read an image deck now works. Ingest runs ~10-30s longer per image deck (cron job,
invisible to Joey).
**Cost impact:** no new cost line. ~4-6k extra output tokens per image-only PDF ≈ $0.04-0.06 at
$10/M, i.e. ~$1/month at the observed rate of ~22 image decks/month (206 documents ingested since
mid-June; 27% image-only in the recent sample). Every subsequent read of that deck is $0 instead of
a failed read or a fresh vision call.
**Effort:** ~half a day including transcription spot-checks.

---

## WS-13 — Live vision fallback for un-ingested image PDFs (D-9) — ship last, only if approved

**Goal:** an image-only PDF that never passed through `/Dot Dump` (i.e. most of Joey's Dropbox and
all of Drive) becomes readable on demand, once, and is cached thereafter. Depends on WS-10, WS-11,
WS-12.

**Confirmed decisions:** D-9 (pending Joey — recommendation: yes, gated as below).

**Steps**

1. `agent.py` — factor the vision read so `ingest.py` stays the single owner of the prompt. Cleanest
   given the conventions (module-level singletons, inline imports for rare paths): add
   `transcribe_pdf_with_claude(content: bytes, filename: str) -> str` to `ingest.py` next to
   `extract_facts_from_pdf_with_claude`, reusing `PDF_SIZE_CAP`, `compress_pdf`, and the 100-page
   check, and import it lazily inside the tool function in `agent.py` (`from ingest import
   transcribe_pdf_with_claude`). **Verify before implementing:** importing `ingest` from `agent`
   pulls in a second `Anthropic()` client and a second `dropbox.Dropbox()` at module scope
   (ingest.py:42-48) — acceptable inside a lazy function-level import, but confirm it does not
   re-trigger `load_dotenv` side effects or the `.ingest.lock` path (it should not; the lock lives
   under `if __name__ == "__main__"`, ingest.py:576-586). If it does, move the shared helper into a
   new `docread.py` imported by both instead.
2. `agent.py` `read_dropbox_file` — on a local parse that hits the WS-10 marker, and only for PDFs
   under the existing size/page caps, call the transcriber, cache with `kind='vision'`, and return
   it. Prefix the result with a one-line note (`[Transcribed from an image-based PDF by Claude —
   text is a transcription, not the original text layer.]`) so Claude does not quote it as
   byte-exact.
3. Keep it single-shot: never call vision twice for the same file in one turn, and never for a file
   whose `doc_cache` row already exists at the same revision.
4. `README.md` — document the behaviour and its cost under "What the agent can do".

**Acceptance checklist**

- [ ] Reading an image-only PDF that lives *outside* `/Dot Dump` returns real slide text and creates
      a `kind='vision'` `doc_cache` row.
- [ ] The second read of the same file is instant and makes no Claude call.
- [ ] An oversized (>24 MB post-compression) or >100-page PDF returns the WS-10 marker, not an
      exception, and is not cached.
- [ ] A text-layer PDF never triggers a vision call (check the API log for the turn).
- [ ] The turn does not exceed the agent's 15-iteration cap or time out in Telegram.

**UX impact:** additive; the failure case becomes a success case. The cost is latency — 20-60s on
the turn that triggers it — so the tool result note and `BASE_SYSTEM` should be clear that this
happens at most once per file.
**Cost impact:** $0.10-0.20 per *newly* transcribed image PDF, paid once per file ever, only when
Joey actually asks for one. No new cost line. Bounded by the existing size/page caps; if it ever
feels loose, the cheap reversal is to require the file to be under N pages.
**Effort:** ~half a day, most of it in verifying the import-boundary question in step 1.

---

## Execution order (2026-08-20 pass)

WS-10 first — it is an hour, it is decision-independent, and it converts a silent failure into a
visible one. Then WS-11 (needs D-8 for step 5 only; steps 1-4, 6, 7 can land without it). Then WS-12
(needs D-7). WS-13 last and only if D-9 is a yes. Do not start WS-12 before WS-11 — it has nowhere
to store a transcription until `doc_cache` exists.
