import os, json, base64, pickle, io, asyncio
from anthropic import Anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import dropbox as dbx_lib
import requests
import logging

logging.basicConfig(level=logging.INFO)

load_dotenv('.dot.env')

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
YOUR_USER_ID      = int(os.getenv("YOUR_TELEGRAM_USER_ID"))
GRANOLA_TOKEN     = os.getenv("GRANOLA_TOKEN")
DROPBOX_TOKEN     = os.getenv("DROPBOX_TOKEN")
BRIEFING_TIME_STR = os.getenv("BRIEFING_TIME", "08:00")

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── GOOGLE AUTH ───────────────────────────────────────────────────────────────
def load_google_creds(pickle_path):
    with open(pickle_path, 'rb') as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(pickle_path, 'wb') as f:
            pickle.dump(creds, f)
    return creds

work_creds     = load_google_creds('token_work.pickle')
gmail_work     = build('gmail',    'v1', credentials=work_creds)
calendar_work  = build('calendar', 'v3', credentials=work_creds)

# ── DROPBOX ───────────────────────────────────────────────────────────────────
dbx = dbx_lib.Dropbox(
    oauth2_access_token=os.getenv("DROPBOX_TOKEN"),
    oauth2_refresh_token=os.getenv("DROPBOX_REFRESH_TOKEN"),
    app_key=os.getenv("DROPBOX_APP_KEY"),
    app_secret=os.getenv("DROPBOX_APP_SECRET")
)
# ── MEMORY (shared with ingest.py) ────────────────────────────────────────────
from memory import (
    save_memory, delete_memory, get_all_memories,
    retrieve_relevant_memories, migrate_sqlite_to_chroma, parse_json_array,
    set_reminder as _set_reminder, get_due_reminders, delete_reminder as _delete_reminder,
    list_reminders as _list_reminders,
    upsert_deal as _upsert_deal, get_deal as _get_deal, list_deals as _list_deals,
)

# Run migration on startup to catch any memories added before vector search
migrate_sqlite_to_chroma()

def extract_and_save_memories(conversation):
    if len(conversation) < 4:
        return
    convo_text = "\n".join([
        f"{m['role'].upper()}: {m['content'] if isinstance(m['content'], str) else '[tool call]'}"
        for m in conversation
    ])
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1000,
            system='Extract facts worth remembering. Return ONLY a JSON array of strings. If nothing, return []. Focus on people, companies, deals, preferences.',
            messages=[{"role": "user", "content": convo_text}]
        )
        facts = parse_json_array(response.content[0].text)
        print(f"Extracted facts: {facts}")
        for fact in facts:
            if fact and len(fact) > 10:
                save_memory(fact)
    except Exception as e:
        print(f"Memory error: {e}")

# ── CONTEXT WINDOW MANAGEMENT ─────────────────────────────────────────────────
# Sonnet 4.6 has a 1M token context window — we manage at 80k tokens to keep
# costs reasonable and latency low for a chat agent use case.
CONTEXT_TOKEN_LIMIT = 80_000

def estimate_tokens(messages: list) -> int:
    """Rough token estimate: ~1 token per 4 chars of text content."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block)) // 4
    return total

def compress_history(conversation: list) -> list:
    """Summarise all but the last few messages into a single context message.

    The kept window must start at a plain-text user message — cutting at a
    tool_result message would orphan it from its assistant tool_use turn and
    the API would reject the request.
    """
    if len(conversation) <= 4:
        return conversation
    cut = len(conversation) - 4
    while cut > 0 and not (
        conversation[cut].get("role") == "user"
        and isinstance(conversation[cut].get("content"), str)
    ):
        cut -= 1
    if cut == 0:
        return conversation  # no safe boundary found — skip this round
    to_summarise = conversation[:cut]
    recent = conversation[cut:]
    convo_text = "\n".join([
        f"{m['role'].upper()}: {m['content'] if isinstance(m['content'], str) else '[tool interaction]'}"
        for m in to_summarise
    ])
    try:
        r = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=600,
            system="Summarise this conversation history concisely, preserving all key facts, decisions, and context that would be needed to continue the conversation intelligently. Be dense — this replaces the full history.",
            messages=[{"role": "user", "content": convo_text}]
        )
        summary = r.content[0].text.strip()
        summary_message = {
            "role": "user",
            "content": f"[Earlier conversation summary]\n{summary}"
        }
        ack = {
            "role": "assistant",
            "content": "Understood, I have the earlier context."
        }
        print(f"Context compressed: {len(to_summarise)} messages → summary")
        return [summary_message, ack] + recent
    except Exception as e:
        print(f"Compression error: {e}")
        return recent  # Hard fallback: keep the safe window, drop the rest

# ── GMAIL HELPERS ─────────────────────────────────────────────────────────────
def _search_gmail_svc(svc, query, max_results=10):
    results = svc.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
    messages = results.get('messages', [])
    if not messages:
        return "No emails found."
    output = []
    for msg in messages[:5]:
        m = svc.users().messages().get(userId='me', id=msg['id'], format='metadata',
            metadataHeaders=['Subject', 'From', 'Date']).execute()
        headers = {h['name']: h['value'] for h in m['payload']['headers']}
        output.append(f"From: {headers.get('From','?')} | Date: {headers.get('Date','?')} | Subject: {headers.get('Subject','?')} | ID: {msg['id']}")
    return "\n".join(output)

def _read_gmail_svc(svc, message_id):
    m = svc.users().messages().get(userId='me', id=message_id, format='full').execute()
    payload = m['payload']
    body = ""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and 'data' in part.get('body', {}):
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                break
    elif 'body' in payload and 'data' in payload['body']:
        body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
    return body[:3000] if body else "[No text body found]"

# ── TOOL FUNCTIONS ────────────────────────────────────────────────────────────
def search_gmail_work(query: str, max_results: int = 10):
    return _search_gmail_svc(gmail_work, query, max_results)

def read_gmail_work(message_id: str):
    return _read_gmail_svc(gmail_work, message_id)

def create_gmail_draft(to: str, subject: str, body: str, thread_id: str = None) -> str:
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg['to'] = to
    msg['subject'] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft_body = {'message': {'raw': raw}}
    if thread_id:
        draft_body['message']['threadId'] = thread_id
    try:
        result = gmail_work.users().drafts().create(userId='me', body=draft_body).execute()
        return f"Draft created (ID: {result['id']}). Open Gmail to review and send."
    except Exception as e:
        return f"Draft creation error: {e}"

def list_calendar_events(days_ahead: int = 7, max_results: int = 10):
    """List upcoming calendar events from work Google Calendar."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    try:
        events_result = calendar_work.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events:
            return f"No events in the next {days_ahead} days."
        lines = []
        for e in events:
            start = e['start'].get('dateTime', e['start'].get('date', ''))[:16].replace('T', ' ')
            title = e.get('summary', 'Untitled')
            attendees = e.get('attendees', [])
            attendee_str = ""
            if attendees:
                names = [a.get('displayName') or a.get('email', '') for a in attendees[:4]]
                attendee_str = f" | With: {', '.join(names)}"
            event_id = e.get('id', '')
            lines.append(f"{start} — {title}{attendee_str} | ID: {event_id}")
        return "\n".join(lines)
    except Exception as e:
        return f"Calendar error: {e}"

def get_calendar_event(event_id: str):
    """Get full details of a specific calendar event including description and all attendees."""
    try:
        e = calendar_work.events().get(calendarId='primary', eventId=event_id).execute()
        lines = []
        lines.append(f"Title: {e.get('summary', 'Untitled')}")
        start = e['start'].get('dateTime', e['start'].get('date', ''))
        lines.append(f"Start: {start}")
        end = e['end'].get('dateTime', e['end'].get('date', ''))
        lines.append(f"End: {end}")
        location = e.get('location', '')
        if location:
            lines.append(f"Location: {location}")
        attendees = e.get('attendees', [])
        if attendees:
            att_list = [f"{a.get('displayName', '')} <{a.get('email', '')}> ({'accepted' if a.get('responseStatus') == 'accepted' else a.get('responseStatus', '?')})" for a in attendees]
            lines.append(f"Attendees:\n  " + "\n  ".join(att_list))
        desc = e.get('description', '')
        if desc:
            lines.append(f"Description:\n{desc[:500]}")
        meet_link = e.get('hangoutLink', '') or (e.get('conferenceData') or {}).get('entryPoints', [{}])[0].get('uri', '')
        if meet_link:
            lines.append(f"Meeting link: {meet_link}")
        return "\n".join(lines)
    except Exception as e:
        return f"Calendar event error: {e}"

def create_calendar_event(
    title: str,
    start: str,
    end: str,
    attendees: list = None,
    description: str = "",
    location: str = "",
    add_meet_link: bool = False
):
    """Create a new event on Joey's work Google Calendar.
    start/end must be ISO 8601 format: '2026-06-15T14:00:00+01:00' (with timezone offset).
    attendees is a list of email address strings.
    """
    from datetime import datetime, timezone
    try:
        event = {
            "summary": title,
            "start":   {"dateTime": start, "timeZone": "America/Toronto"},
            "end":     {"dateTime": end,   "timeZone": "America/Toronto"},
        }
        if description:
            event["description"] = description
        if location:
            event["location"] = location
        if attendees:
            event["attendees"] = [{"email": a} for a in attendees]
        if add_meet_link:
            import uuid
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}
                }
            }
        kwargs = {"calendarId": "primary", "body": event, "sendUpdates": "all"}
        if add_meet_link:
            kwargs["conferenceDataVersion"] = 1
        created = calendar_work.events().insert(**kwargs).execute()
        link = created.get("hangoutLink") or created.get("htmlLink", "")
        return f"Event created: {created.get('summary')} on {created['start'].get('dateTime', '')[:16]} | Link: {link} | ID: {created['id']}"
    except Exception as e:
        return f"Calendar create error: {e}"

def delete_calendar_event(event_id: str):
    """Delete a calendar event by ID. Use get_calendar_event first to confirm the right event."""
    try:
        calendar_work.events().delete(calendarId='primary', eventId=event_id, sendUpdates='all').execute()
        return f"Event {event_id} deleted and attendees notified."
    except Exception as e:
        return f"Calendar delete error: {e}"

def update_calendar_event(
    event_id: str,
    title: str = None,
    start: str = None,
    end: str = None,
    description: str = None,
    location: str = None,
    add_attendees: list = None
):
    """Update fields on an existing calendar event. Only provided fields are changed."""
    try:
        event = calendar_work.events().get(calendarId='primary', eventId=event_id).execute()
        if title:
            event['summary'] = title
        if start:
            event['start'] = {"dateTime": start, "timeZone": "America/Toronto"}
        if end:
            event['end'] = {"dateTime": end, "timeZone": "America/Toronto"}
        if description is not None:
            event['description'] = description
        if location is not None:
            event['location'] = location
        if add_attendees:
            existing = event.get('attendees', [])
            existing_emails = {a['email'] for a in existing}
            for email in add_attendees:
                if email not in existing_emails:
                    existing.append({"email": email})
            event['attendees'] = existing
        updated = calendar_work.events().update(
            calendarId='primary', eventId=event_id, body=event, sendUpdates='all'
        ).execute()
        return f"Event updated: {updated.get('summary')} | {updated['start'].get('dateTime','')[:16]}"
    except Exception as e:
        return f"Calendar update error: {e}"

def search_granola(query: str, max_results: int = 10):
    headers = {"Authorization": f"Bearer {GRANOLA_TOKEN}"}
    try:
        r = requests.get(
            "https://public-api.granola.ai/v1/notes",
            headers=headers,
            params={"page_size": 30},
            timeout=10
        )
        if r.status_code == 401:
            return "Granola auth error: check your API token and that your plan is Enterprise."
        if r.status_code != 200:
            return f"Granola API error: {r.status_code} {r.text[:200]}"
        notes = r.json().get("notes", [])
        if not notes:
            return "No Granola notes found."
        query_lower = query.lower()
        matched = [n for n in notes if query_lower in (n.get("title") or "").lower()]
        results = matched if matched else notes
        lines = []
        for n in results[:max_results]:
            title = n.get("title") or "Untitled"
            date  = (n.get("created_at") or "")[:10]
            note_id = n.get("id", "")
            lines.append(f"Title: {title} | Date: {date} | ID: {note_id}")
        return "\n".join(lines)
    except Exception as e:
        return f"Granola search error: {e}"

def read_granola(note_id: str):
    headers = {"Authorization": f"Bearer {GRANOLA_TOKEN}"}
    try:
        r = requests.get(
            f"https://public-api.granola.ai/v1/notes/{note_id}",
            headers=headers,
            params={"include": "transcript"},
            timeout=10
        )
        if r.status_code == 404:
            return "Note not found — it may not have a generated summary yet."
        if r.status_code != 200:
            return f"Granola read error: {r.status_code}"
        data = r.json()
        output = []
        attendees = data.get("attendees", [])
        if attendees:
            names = ", ".join(a.get("name") or a.get("email", "") for a in attendees)
            output.append(f"Attendees: {names}")
        start = (data.get("calendar_event") or {}).get("scheduled_start_time", "")[:10]
        if start:
            output.append(f"Date: {start}")
        summary = data.get("summary_text") or data.get("summary_markdown") or ""
        if summary:
            output.append(f"\nSummary:\n{summary[:2000]}")
        transcript = data.get("transcript") or []
        if transcript:
            lines = []
            for t in transcript[:40]:
                source = (t.get("speaker") or {}).get("source", "?")
                speaker = "You" if source == "microphone" else "Them"
                lines.append(f"{speaker}: {t.get('text','')}")
            output.append(f"\nTranscript (first 40 turns):\n" + "\n".join(lines))
        return "\n".join(output)[:3000] if output else "[Note has no content]"
    except Exception as e:
        return f"Granola read error: {e}"

def search_dropbox(query: str, max_results: int = 10):
    try:
        results = dbx.files_search_v2(query)
        matches = results.matches[:max_results]
        if not matches:
            return "No Dropbox files found."
        lines = []
        for m in matches:
            meta = m.metadata.metadata
            size_kb = getattr(meta, 'size', 0) // 1024
            lines.append(f"Name: {meta.name} | Path: {meta.path_display} | Size: {size_kb}KB")
        return "\n".join(lines)
    except Exception as e:
        return f"Dropbox search error: {e}"

def update_deal(company: str, stage: str = None, last_touchpoint: str = None,
               next_action: str = None, notes: str = None) -> str:
    deal = _upsert_deal(company, stage, last_touchpoint, next_action, notes)
    return (f"Deal: {deal['company']} | Stage: {deal['stage']} | "
            f"Next action: {deal['next_action'] or '—'} | "
            f"Last touchpoint: {deal['last_touchpoint'] or '—'}")

def get_deal_info(company: str) -> str:
    deal = _get_deal(company)
    if not deal:
        return f"No deal found for '{company}'."
    return "\n".join([
        f"Company: {deal['company']}",
        f"Stage: {deal['stage']}",
        f"Last touchpoint: {deal['last_touchpoint'] or '—'}",
        f"Next action: {deal['next_action'] or '—'}",
        f"Notes: {deal['notes'] or '—'}",
        f"Created: {deal['created_at'][:10]} | Updated: {deal['updated_at'][:10]}",
    ])

def list_deals(stage: str = None) -> str:
    deals = _list_deals(stage)
    if not deals:
        return f"No deals{' in stage: ' + stage if stage else ' in pipeline'}."
    return "\n".join(
        f"{d['company']} | {d['stage']} | Next: {d['next_action'] or '—'} | Updated: {d['updated_at'][:10]}"
        for d in deals
    )

def set_reminder(note: str, due_at: str) -> str:
    r = _set_reminder(note, due_at)
    return f"Reminder set: '{r['note']}' at {r['due_at']} (ID {r['id']})"

def list_reminders() -> str:
    reminders = _list_reminders()
    if not reminders:
        return "No reminders set."
    return "\n".join(f"ID {r['id']}: {r['note']} — due {r['due_at']}" for r in reminders)

def delete_reminder(reminder_id: int) -> str:
    _delete_reminder(reminder_id)
    return f"Reminder {reminder_id} deleted."

def read_dropbox_file(file_path: str):
    try:
        metadata, response = dbx.files_download(file_path)
        content = response.content
        name = metadata.name.lower()
        if name.endswith(('.txt', '.md')):
            return content.decode('utf-8', errors='ignore')[:3000]
        elif name.endswith('.pdf'):
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(io.BytesIO(content))
                text = " ".join(page.extract_text() or "" for page in reader.pages)
                return text[:3000]
            except Exception as e:
                return f"[PDF read error: {e}]"
        elif name.endswith('.docx'):
            from docx import Document
            doc = Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)[:3000]
        else:
            return f"[File type not readable as text: {name}]"
    except Exception as e:
        return f"Dropbox read error: {e}"

# ── TOOLS SCHEMA ──────────────────────────────────────────────────────────────
TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {
        "name": "search_gmail_work",
        "description": "Search Joey's work Google Workspace inbox. Use for founder emails, investor correspondence, DFS Lab work. Gmail search syntax: from:, subject:, after:YYYY/MM/DD.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 10}
        }, "required": ["query"]}
    },
    {
        "name": "read_gmail_work",
        "description": "Read a work Gmail message body by ID (get ID from search_gmail_work first).",
        "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"]}
    },
    {
        "name": "create_gmail_draft",
        "description": "Create a Gmail draft for Joey to review and send himself — never sends automatically. Use when asked to draft or reply to an email. Read the thread first with search_gmail_work + read_gmail_work so the draft has full context. For replies, pass the thread_id so the draft appears in the right thread.",
        "input_schema": {"type": "object", "properties": {
            "to":        {"type": "string", "description": "Recipient email address"},
            "subject":   {"type": "string", "description": "Subject line — use 'Re: original subject' for replies"},
            "body":      {"type": "string", "description": "Full email body text"},
            "thread_id": {"type": "string", "description": "Gmail thread ID to reply in-thread (get from search_gmail_work results)"}
        }, "required": ["to", "subject", "body"]}
    },
    {
        "name": "list_calendar_events",
        "description": "List upcoming events from Joey's work Google Calendar. Use for 'what's on my calendar', 'what do I have today/this week', 'who am I meeting'. Defaults to next 7 days.",
        "input_schema": {"type": "object", "properties": {
            "days_ahead": {"type": "integer", "default": 7, "description": "How many days ahead to look"},
            "max_results": {"type": "integer", "default": 10}
        }}
    },
    {
        "name": "get_calendar_event",
        "description": "Get full details of a specific calendar event by ID (get ID from list_calendar_events first). Returns all attendees, description, and meeting link.",
        "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]}
    },
    {
        "name": "create_calendar_event",
        "description": "Create a new event on Joey's work Google Calendar. Use for 'schedule a call', 'block time', 'add a meeting'. Always confirm title, start, and end before calling. start/end format: '2026-06-15T14:00:00-04:00' (include Toronto UTC offset: EDT is -04:00, EST is -05:00).",
        "input_schema": {"type": "object", "properties": {
            "title":         {"type": "string", "description": "Event title"},
            "start":         {"type": "string", "description": "ISO 8601 start datetime with timezone offset"},
            "end":           {"type": "string", "description": "ISO 8601 end datetime with timezone offset"},
            "attendees":     {"type": "array", "items": {"type": "string"}, "description": "List of attendee email addresses"},
            "description":   {"type": "string", "description": "Event description or agenda"},
            "location":      {"type": "string", "description": "Location or video link"},
            "add_meet_link": {"type": "boolean", "description": "If true, auto-generate a Google Meet link", "default": False}
        }, "required": ["title", "start", "end"]}
    },
    {
        "name": "update_calendar_event",
        "description": "Update an existing calendar event. Only fields you provide are changed. Get event_id from list_calendar_events or get_calendar_event first.",
        "input_schema": {"type": "object", "properties": {
            "event_id":      {"type": "string"},
            "title":         {"type": "string"},
            "start":         {"type": "string", "description": "ISO 8601 datetime with timezone offset"},
            "end":           {"type": "string", "description": "ISO 8601 datetime with timezone offset"},
            "description":   {"type": "string"},
            "location":      {"type": "string"},
            "add_attendees": {"type": "array", "items": {"type": "string"}, "description": "Additional attendee emails to add"}
        }, "required": ["event_id"]}
    },
    {
        "name": "delete_calendar_event",
        "description": "Delete a calendar event and notify attendees. Use get_calendar_event to confirm the right event before deleting.",
        "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]}
    },
    {
        "name": "search_granola",
        "description": "Search Granola meeting notes by title keyword. Returns recent notes matching the query. Use when asked about calls, meetings, or what was discussed with a founder or contact.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 10}
        }, "required": ["query"]}
    },
    {
        "name": "read_granola",
        "description": "Read a full Granola meeting note by ID (get ID from search_granola first). Returns attendees, summary, and transcript.",
        "input_schema": {"type": "object", "properties": {"note_id": {"type": "string"}}, "required": ["note_id"]}
    },
    {
        "name": "search_dropbox",
        "description": "Search Dropbox files by name. Use for pitch decks, PDFs, and documents stored in Dropbox.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 10}
        }, "required": ["query"]}
    },
    {
        "name": "read_dropbox_file",
        "description": "Download and read a Dropbox file by its full path (get path from search_dropbox first). Supports PDF, DOCX, TXT, MD.",
        "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}
    },
    {
        "name": "set_reminder",
        "description": "Set a reminder that fires as a Telegram message at the specified time. Use when Joey says 'remind me to...', 'follow up with X in N days', 'ping me about Y on [date]'. Always confirm the note text and time before setting.",
        "input_schema": {"type": "object", "properties": {
            "note":   {"type": "string", "description": "What to remind about — be specific, e.g. 'Follow up with Jane Doe re Series A deck'"},
            "due_at": {"type": "string", "description": "When to fire, format: YYYY-MM-DD HH:MM (Toronto local time)"}
        }, "required": ["note", "due_at"]}
    },
    {
        "name": "list_reminders",
        "description": "List all pending reminders with their IDs and due times.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "delete_reminder",
        "description": "Delete a pending reminder by its ID (get IDs from list_reminders).",
        "input_schema": {"type": "object", "properties": {
            "reminder_id": {"type": "integer"}
        }, "required": ["reminder_id"]}
    },
    {
        "name": "update_deal",
        "description": "Create or update a deal in the pipeline. Use for 'add company X', 'move X to due diligence', 'log that I spoke with X', 'set next action for X'. Stages: sourcing, first_call, due_diligence, passed, invested.",
        "input_schema": {"type": "object", "properties": {
            "company":         {"type": "string", "description": "Company name"},
            "stage":           {"type": "string", "description": "sourcing | first_call | due_diligence | passed | invested"},
            "last_touchpoint": {"type": "string", "description": "Brief note on the last interaction, e.g. 'Call 2026-06-11, discussed Series A terms'"},
            "next_action":     {"type": "string", "description": "What needs to happen next, e.g. 'Send term sheet by Jun 20'"},
            "notes":           {"type": "string", "description": "Any other context about this deal"}
        }, "required": ["company"]}
    },
    {
        "name": "get_deal_info",
        "description": "Get full deal details for a specific company — stage, last touchpoint, next action, notes.",
        "input_schema": {"type": "object", "properties": {
            "company": {"type": "string"}
        }, "required": ["company"]}
    },
    {
        "name": "list_deals",
        "description": "List all deals in the pipeline, optionally filtered by stage. Use for 'what's in due diligence?', 'show me the pipeline', 'what deals are we tracking?'.",
        "input_schema": {"type": "object", "properties": {
            "stage": {"type": "string", "description": "Optional filter: sourcing | first_call | due_diligence | passed | invested"}
        }}
    }
]

TOOL_FUNCTIONS = {
    "search_gmail_work":    search_gmail_work,
    "read_gmail_work":      read_gmail_work,
    "create_gmail_draft":   create_gmail_draft,
    "list_calendar_events": list_calendar_events,
    "get_calendar_event":   get_calendar_event,
    "create_calendar_event": create_calendar_event,
    "update_calendar_event": update_calendar_event,
    "delete_calendar_event": delete_calendar_event,
    "search_granola":       search_granola,
    "read_granola":         read_granola,
    "search_dropbox":       search_dropbox,
    "read_dropbox_file":    read_dropbox_file,
    "set_reminder":         set_reminder,
    "list_reminders":       list_reminders,
    "delete_reminder":      delete_reminder,
    "update_deal":          update_deal,
    "get_deal_info":        get_deal_info,
    "list_deals":           list_deals,
}

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
BASE_SYSTEM = """You are Dot — a sharp, direct analyst and thinking partner for Joseph (Joey)
Benson-Aruna. He is an investor and advisor working with founders across Francophone
and anglophone Africa, focused on AI, fintech, blockchain, digital financial services,
infrastructure, and technology. He is a partner at DFS Lab (dfs.vc).

Your tools and what they contain:
- search_gmail_work / read_gmail_work: Joey's work Workspace email (founders, investors, DFS Lab)
- create_gmail_draft: create a Gmail draft for Joey to review — NEVER sends automatically
- list_calendar_events / get_calendar_event / create_calendar_event / update_calendar_event / delete_calendar_event: Joey's work Google Calendar (read and write)
- search_granola / read_granola: Call and meeting notes from Granola
- search_dropbox / read_dropbox_file: Dropbox files (pitch decks, PDFs, documents)
- web_search: Real-time web search
- set_reminder / list_reminders / delete_reminder: time-based reminders delivered via Telegram
- update_deal / get_deal_info / list_deals: deal pipeline (stages: sourcing, first_call, due_diligence, passed, invested)

When a question involves a person or company, search Granola first (most recent call context),
then work email. For documents, search Dropbox. For scheduling questions, check calendar.
Search proactively — don't ask permission. For calendar writes (create/update/delete) and email drafts, always confirm the key details in your response before executing — state what you're about to create or change and give Joey a chance to correct it before calling the tool. Email drafts are never sent automatically; Joey reviews and sends from Gmail.
Facts retrieved from your long-term memory store appear inside <relevant_memories> tags
at the top of user messages — treat them as background context about Joey and his work.
Be direct and specific. No filler, no hedging."""

# Frozen system prompt with a cache breakpoint: tools + system cache together
# and stay valid for the whole session. Volatile content (retrieved memories)
# goes into the user turn instead, so it never invalidates this prefix.
SYSTEM_BLOCKS = [{
    "type": "text",
    "text": BASE_SYSTEM,
    "cache_control": {"type": "ephemeral"},
}]

def build_user_content(user_text: str) -> str:
    memories = retrieve_relevant_memories(user_text, k=15)
    if not memories:
        return user_text
    block = "\n".join(f"- {m}" for m in memories)
    return f"<relevant_memories>\n{block}\n</relevant_memories>\n\n{user_text}"

# ── CONVERSATION STATE ────────────────────────────────────────────────────────
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")

def repair_history(messages: list) -> list:
    """Drop tool_use blocks whose result was never recorded (process died or
    the API call raised mid-turn) and tool_result blocks orphaned by that —
    either one 400s every subsequent API call. Server-tool results live in the
    same assistant message; client tool results in the following user message.
    Only call between turns: a trailing server_tool_use without a result is
    legitimate while a pause_turn resume is in flight."""
    def ids(content, key):
        return {b.get(key) for b in content if isinstance(b, dict)} if isinstance(content, list) else set()

    while True:
        out, dropped = [], 0
        for i, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                out.append(msg)
                continue
            nxt = messages[i + 1].get("content") if i + 1 < len(messages) else None
            prev = out[-1].get("content") if out else None
            answered = ids(content, "tool_use_id") | ids(nxt, "tool_use_id")
            asked = ids(content, "id") | ids(prev, "id")
            kept = [b for b in content if not (isinstance(b, dict) and (
                (b.get("type", "").endswith("tool_use") and b.get("id") not in answered) or
                (b.get("type", "").endswith("tool_result") and b.get("tool_use_id") not in asked)))]
            dropped += len(content) - len(kept)
            if kept:
                out.append(msg if kept == content else {**msg, "content": kept})
        if dropped:
            logging.warning(f"Session repair: dropped {dropped} dangling tool block(s)")
        if out == messages:
            return out
        messages = out

def load_session() -> list:
    try:
        with open(SESSION_FILE) as f:
            return repair_history(json.load(f))
    except FileNotFoundError:
        return []
    except Exception as e:
        logging.error(f"Session load error: {e}")
        return []

def save_session():
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump(conversation_history, f)
    except Exception as e:
        logging.error(f"Session save error: {e}")

conversation_history = load_session()

# ── CLAUDE CALL ───────────────────────────────────────────────────────────────
def _with_cache_breakpoint(messages: list) -> list:
    """Copy of messages with a cache breakpoint on the last content block,
    so the conversation prefix caches turn-over-turn. History itself is never
    mutated — markers would otherwise accumulate past the 4-breakpoint limit."""
    if not messages:
        return messages
    last = json.loads(json.dumps(messages[-1]))
    content = last.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
        last["content"] = content
    if isinstance(content, list) and content and isinstance(content[-1], dict) \
            and content[-1].get("type") in ("text", "tool_result"):
        content[-1]["cache_control"] = {"type": "ephemeral"}
    return messages[:-1] + [last]

def call_claude():
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_BLOCKS,
        tools=TOOLS,
        messages=_with_cache_breakpoint(conversation_history),
    )
    u = response.usage
    logging.info(
        f"usage: in={u.input_tokens} out={u.output_tokens} "
        f"cache_write={getattr(u, 'cache_creation_input_tokens', 0)} "
        f"cache_read={getattr(u, 'cache_read_input_tokens', 0)}"
    )
    return response

# ── AGENT LOOP ────────────────────────────────────────────────────────────────
MAX_TOOL_ITERATIONS = 15

def run_tool(name: str, tool_input: dict):
    """Execute a tool, never raising — a raised exception here would leave a
    dangling tool_use in history and 400 every subsequent API call."""
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return f"[{name} is a native tool — handled by API]", False
    try:
        return str(fn(**tool_input)), False
    except Exception as e:
        logging.exception(f"Tool {name} failed")
        return f"Tool error: {e}", True

_whisper_model = None

def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        logging.info("Loading Whisper tiny model...")
        _whisper_model = whisper.load_model("tiny")
        logging.info("Whisper model loaded.")
    return _whisper_model

async def _process_message(update: Update, user_text: str):
    global conversation_history
    # A previous turn that crashed mid-tool-call leaves dangling blocks in the
    # in-memory history without ever hitting disk — repair before building on it.
    conversation_history = repair_history(conversation_history)
    user_content = await asyncio.to_thread(build_user_content, user_text)
    conversation_history.append({"role": "user", "content": user_content})

    if estimate_tokens(conversation_history) > CONTEXT_TOKEN_LIMIT:
        conversation_history = await asyncio.to_thread(compress_history, conversation_history)

    await update.message.chat.send_action("typing")
    response = await asyncio.to_thread(call_claude)

    iterations = 0
    while response.stop_reason in ("tool_use", "pause_turn") and iterations < MAX_TOOL_ITERATIONS:
        iterations += 1
        conversation_history.append({
            "role": "assistant",
            "content": [b.model_dump(exclude_none=True) for b in response.content],
        })
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result, is_error = await asyncio.to_thread(run_tool, block.name, dict(block.input))
                tr = {"type": "tool_result", "tool_use_id": block.id, "content": result}
                if is_error:
                    tr["is_error"] = True
                tool_results.append(tr)
            conversation_history.append({"role": "user", "content": tool_results})
        # pause_turn: re-send so the API can resume from the trailing server_tool_use block.
        await update.message.chat.send_action("typing")
        response = await asyncio.to_thread(call_claude)

    final = " ".join(b.text for b in response.content if b.type == "text") or "Done."
    conversation_history.append({"role": "assistant", "content": final})
    save_session()

    for i in range(0, len(final), 4000):
        await update.message.reply_text(final[i:i+4000])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != YOUR_USER_ID:
        return
    await _process_message(update, update.message.text)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != YOUR_USER_ID:
        return

    import tempfile
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
        await voice_file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        await update.message.chat.send_action("typing")
        model = await asyncio.to_thread(_get_whisper)
        result = await asyncio.to_thread(model.transcribe, tmp_path)
        text = result["text"].strip()
    except Exception as e:
        logging.exception("Whisper transcription failed")
        await update.message.reply_text(f"Transcription error: {e}")
        return
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    if not text:
        await update.message.reply_text("Couldn't transcribe that — try again.")
        return

    await update.message.reply_text(f"_{text}_", parse_mode="Markdown")
    await _process_message(update, text)

# ── COMMANDS ──────────────────────────────────────────────────────────────────
async def cmd_remember(update, context):
    if update.effective_user.id != YOUR_USER_ID: return
    text = " ".join(context.args)
    if text:
        save_memory(text, "manual")
        await update.message.reply_text(f"Saved: {text}")
    else:
        await update.message.reply_text("Usage: /remember [fact]")

async def cmd_memories(update, context):
    if update.effective_user.id != YOUR_USER_ID: return
    mems = get_all_memories()[:20]
    if not mems:
        await update.message.reply_text("No memories yet.")
        return
    await update.message.reply_text("\n".join(f"{i+1}. {m}" for i, m in enumerate(mems))[:4000])

async def cmd_forget(update, context):
    if update.effective_user.id != YOUR_USER_ID: return
    mems = get_all_memories()[:20]
    try:
        n = int(context.args[0]) - 1
        target = mems[n]
        await asyncio.to_thread(delete_memory, target)
        await update.message.reply_text(f"Deleted: {target}")
    except Exception:
        await update.message.reply_text("Usage: /forget [number from /memories]")

async def cmd_newsession(update, context):
    global conversation_history
    if update.effective_user.id != YOUR_USER_ID: return
    if conversation_history:
        await asyncio.to_thread(extract_and_save_memories, conversation_history)
    conversation_history = []
    save_session()
    await update.message.reply_text("Session cleared. Memories extracted.")

# ── ERROR HANDLER ─────────────────────────────────────────────────────────────
async def error_handler(update, context):
    logging.error(f"Exception: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(f"⚠️ Error: {context.error}")
        except Exception:
            pass

# ── MORNING BRIEFING (runs daily at BRIEFING_TIME) ────────────────────────────
async def send_morning_briefing(context: ContextTypes.DEFAULT_TYPE):
    from datetime import date
    from memory import list_reminders as _lr

    today_label = date.today().strftime("%A, %B %-d")
    today_str   = date.today().strftime("%Y-%m-%d")

    cal   = await asyncio.to_thread(list_calendar_events, 1, 10)
    email = await asyncio.to_thread(search_gmail_work, "is:unread newer_than:1d", 5)
    due_today = [r for r in _lr() if r["due_at"].startswith(today_str)]
    reminders_str = "\n".join(f"- {r['note']} at {r['due_at'][11:]}" for r in due_today) or "None"

    prompt = f"""Today is {today_label}. Produce a clean morning briefing for Joey — an Africa-focused tech investor at DFS Lab.

RAW DATA:

CALENDAR (today):
{cal}

UNREAD EMAIL (last 24h):
{email}

REMINDERS DUE TODAY:
{reminders_str}

Instructions:
- Calendar: show time in 12-hour format, event name, and attendee first names only. Skip all-day/personal blocks unless notable. No IDs.
- Email: show sender name, subject, and received time. No IDs. Flag anything that looks like it needs a reply.
- Use web_search to find 3–5 of today's top news headlines relevant to African tech, fintech, AI, and startup investing. One sentence of context per headline.
- Sections: Today, Email, News. No greeting, no filler."""

    briefing_tools = [{"type": "web_search_20260209", "name": "web_search"}]
    msgs = [{"role": "user", "content": prompt}]

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-sonnet-4-6",
            max_tokens=1500,
            tools=briefing_tools,
            messages=msgs,
        )
        iterations = 0
        while response.stop_reason in ("tool_use", "pause_turn") and iterations < 5:
            iterations += 1
            msgs.append({
                "role": "assistant",
                "content": [b.model_dump(exclude_none=True) for b in response.content],
            })
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result, is_error = await asyncio.to_thread(run_tool, block.name, dict(block.input))
                        tr = {"type": "tool_result", "tool_use_id": block.id, "content": result}
                        if is_error:
                            tr["is_error"] = True
                        tool_results.append(tr)
                msgs.append({"role": "user", "content": tool_results})
            response = await asyncio.to_thread(
                client.messages.create,
                model="claude-sonnet-4-6",
                max_tokens=1500,
                tools=briefing_tools,
                messages=msgs,
            )
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        if text:
            for i in range(0, len(text), 4000):
                await context.bot.send_message(chat_id=YOUR_USER_ID, text=text[i:i+4000])
    except Exception as e:
        logging.error(f"Morning briefing error: {e}")

# ── REMINDER CHECKER (runs every 60s via JobQueue) ────────────────────────────
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    due = get_due_reminders()
    for r in due:
        try:
            await context.bot.send_message(chat_id=YOUR_USER_ID, text=f"Reminder: {r['note']}")
            _delete_reminder(r['id'])
        except Exception as e:
            logging.error(f"Reminder delivery error: {e}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("remember",   cmd_remember))
    app.add_handler(CommandHandler("memories",   cmd_memories))
    app.add_handler(CommandHandler("forget",     cmd_forget))
    app.add_handler(CommandHandler("newsession", cmd_newsession))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_error_handler(error_handler)
    app.job_queue.run_repeating(check_reminders, interval=60, first=10)
    from zoneinfo import ZoneInfo
    import datetime as _dt
    _h, _m = map(int, BRIEFING_TIME_STR.split(":"))
    app.job_queue.run_daily(
        send_morning_briefing,
        time=_dt.time(_h, _m, tzinfo=ZoneInfo("America/Toronto"))
    )
    print("Dot is running — Granola, Dropbox, Gmail, Calendar (work)...")
    app.run_polling()

if __name__ == "__main__":
    main()

