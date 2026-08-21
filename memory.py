"""
Shared memory layer for Dot — SQLite + ChromaDB vector store.
Used by both agent.py (Telegram bot) and ingest.py (Dropbox ingestion).
"""

import os, sqlite3, json

import chromadb
from sentence_transformers import SentenceTransformer

# Anchor data paths to this file's directory so behavior doesn't depend on CWD
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_BASE_DIR, "dot.db")
CHROMA_PATH = os.path.join(_BASE_DIR, "chroma_db")

# ── DATABASE ──────────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
# WAL mode: memories/dot.db is shared between the bot process and the cron
# ingest process (check_same_thread=False above). doc_cache writes (WS-11) add
# 15-50 KB writes on top of existing traffic, which widens the window for
# "database is locked" under the default rollback-journal mode. WAL lets
# readers and a single writer proceed concurrently. Additive and reversible —
# back out with PRAGMA journal_mode=DELETE if this ever misbehaves.
conn.execute("PRAGMA journal_mode=WAL")
conn.executescript("""
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        embedding TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ingested_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        dropbox_path TEXT NOT NULL,
        status TEXT DEFAULT 'processed',
        memory_count INTEGER DEFAULT 0,
        ingested_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT NOT NULL UNIQUE,
        stage TEXT NOT NULL DEFAULT 'sourcing',
        last_touchpoint TEXT DEFAULT '',
        next_action TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS procedures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger TEXT NOT NULL,
        procedure TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        last_used_at TEXT
    );
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
    CREATE TABLE IF NOT EXISTS prep_log (
        event_id     TEXT NOT NULL,
        occurrence   TEXT NOT NULL DEFAULT '',   -- event start ISO — a recurring series preps per occurrence
        prepped_at   TEXT DEFAULT (datetime('now')),
        outcome      TEXT DEFAULT 'sent',        -- 'sent' | 'skipped' | 'muted'
        PRIMARY KEY (event_id, occurrence)
    );
    CREATE TABLE IF NOT EXISTS prep_mutes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern    TEXT NOT NULL,                -- event id, or a case-insensitive substring of the title
        reason     TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
""")
conn.commit()

# ── VECTOR STORE (ChromaDB + sentence-transformers) ───────────────────────────
_embedder  = SentenceTransformer('all-MiniLM-L6-v2')
_chroma    = chromadb.PersistentClient(path=CHROMA_PATH)
_col       = _chroma.get_or_create_collection("memories")
_proc_col  = _chroma.get_or_create_collection("procedures")

def _embed(text: str) -> list:
    return _embedder.encode(text).tolist()

def save_memory(content: str, tags: str = ""):
    cur = conn.execute("INSERT INTO memories (content, tags) VALUES (?, ?)", (content, tags))
    conn.commit()
    mem_id = str(cur.lastrowid)
    try:
        _col.add(
            ids=[mem_id],
            embeddings=[_embed(content)],
            documents=[content],
            metadatas=[{"tags": tags}]
        )
    except Exception as e:
        print(f"ChromaDB write error: {e}")

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

def was_prepped(event_id: str, occurrence: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM prep_log WHERE event_id = ? AND occurrence = ?", (event_id, occurrence)
    ).fetchone() is not None

def mark_prepped(event_id: str, occurrence: str, outcome: str = "sent"):
    conn.execute(
        "INSERT OR REPLACE INTO prep_log (event_id, occurrence, outcome) VALUES (?, ?, ?)",
        (event_id, occurrence, outcome))
    # Housekeeping: keep prep_log from growing without bound.
    conn.execute("DELETE FROM prep_log WHERE prepped_at < datetime('now', '-90 days')")
    conn.commit()

def add_prep_mute(pattern: str, reason: str = "") -> str:
    conn.execute(
        "INSERT INTO prep_mutes (pattern, reason) VALUES (?, ?)", (pattern, reason)
    )
    conn.commit()
    return pattern

def list_prep_mutes() -> list:
    rows = conn.execute(
        "SELECT id, pattern, reason, created_at FROM prep_mutes ORDER BY id DESC"
    ).fetchall()
    return [{"id": r[0], "pattern": r[1], "reason": r[2], "created_at": r[3]} for r in rows]

def delete_prep_mute(mute_id: int) -> bool:
    row = conn.execute("SELECT id FROM prep_mutes WHERE id = ?", (mute_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM prep_mutes WHERE id = ?", (mute_id,))
    conn.commit()
    return True

def is_prep_muted(event_id: str, title: str) -> bool:
    """True if any mute row matches this event by id or by case-insensitive title substring."""
    t = (title or "").lower()
    for (pattern,) in conn.execute("SELECT pattern FROM prep_mutes").fetchall():
        p = pattern.lower()
        if p == event_id.lower() or (len(p) >= 3 and p in t):
            return True
    return False

def delete_memory(content: str) -> bool:
    """Delete a memory from SQLite AND ChromaDB. Returns True if found."""
    rows = conn.execute("SELECT id FROM memories WHERE content = ?", (content,)).fetchall()
    if not rows:
        return False
    conn.execute("DELETE FROM memories WHERE content = ?", (content,))
    conn.commit()
    try:
        _col.delete(ids=[str(r[0]) for r in rows])
    except Exception as e:
        print(f"ChromaDB delete error: {e}")
    return True

def get_all_memories() -> list:
    rows = conn.execute("SELECT content FROM memories ORDER BY id DESC").fetchall()
    return [r[0] for r in rows]

def retrieve_relevant_memories(query: str, k: int = 15) -> list:
    """Vector similarity search via ChromaDB. Falls back to recency if chroma is empty."""
    try:
        count = _col.count()
        if count == 0:
            return get_all_memories()[:k]
        results = _col.query(
            query_embeddings=[_embed(query)],
            n_results=min(k, count)
        )
        return results["documents"][0] if results["documents"] else get_all_memories()[:k]
    except Exception as e:
        print(f"ChromaDB query error: {e}")
        return get_all_memories()[:k]

# ── PROCEDURAL MEMORY (how a past task was handled, separate from facts) ──────
def save_procedure(trigger: str, procedure: str):
    cur = conn.execute(
        "INSERT INTO procedures (trigger, procedure) VALUES (?, ?)", (trigger, procedure)
    )
    conn.commit()
    proc_id = str(cur.lastrowid)
    try:
        _proc_col.add(
            ids=[proc_id],
            embeddings=[_embed(trigger)],
            documents=[procedure],
            metadatas=[{"trigger": trigger}],
        )
    except Exception as e:
        print(f"ChromaDB procedure write error: {e}")

def get_all_procedures() -> list:
    rows = conn.execute(
        "SELECT id, trigger, procedure FROM procedures ORDER BY id DESC"
    ).fetchall()
    return [{"id": r[0], "trigger": r[1], "procedure": r[2]} for r in rows]

def delete_procedure(procedure_id: int) -> bool:
    row = conn.execute("SELECT id FROM procedures WHERE id = ?", (procedure_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM procedures WHERE id = ?", (procedure_id,))
    conn.commit()
    try:
        _proc_col.delete(ids=[str(procedure_id)])
    except Exception as e:
        print(f"ChromaDB procedure delete error: {e}")
    return True

def retrieve_relevant_procedures(query: str, k: int = 3) -> list:
    """Vector similarity search against procedure triggers via ChromaDB.
    Returns the procedure text (not the trigger). Falls back to recency if chroma is empty."""
    try:
        count = _proc_col.count()
        if count == 0:
            return [p["procedure"] for p in get_all_procedures()[:k]]
        results = _proc_col.query(
            query_embeddings=[_embed(query)],
            n_results=min(k, count),
        )
        if results["ids"] and results["ids"][0]:
            ids = results["ids"][0]
            conn.execute(
                f"UPDATE procedures SET last_used_at = datetime('now') "
                f"WHERE id IN ({','.join('?' * len(ids))})",
                [int(i) for i in ids],
            )
            conn.commit()
        return results["documents"][0] if results["documents"] else []
    except Exception as e:
        print(f"ChromaDB procedure query error: {e}")
        return [p["procedure"] for p in get_all_procedures()[:k]]

def migrate_sqlite_to_chroma():
    """Sync all existing SQLite memories (and procedures) into ChromaDB.
    Safe to run multiple times — skips IDs already in ChromaDB."""
    rows = conn.execute("SELECT id, content, tags FROM memories").fetchall()
    existing_ids = set(_col.get()["ids"])
    to_add = [(str(r[0]), r[1], r[2]) for r in rows if str(r[0]) not in existing_ids]
    if to_add:
        print(f"Migrating {len(to_add)} memories to ChromaDB...")
        batch = 500
        for i in range(0, len(to_add), batch):
            chunk = to_add[i:i+batch]
            _col.add(
                ids=[c[0] for c in chunk],
                embeddings=[_embed(c[1]) for c in chunk],
                documents=[c[1] for c in chunk],
                metadatas=[{"tags": c[2]} for c in chunk]
            )
            print(f"  {min(i+batch, len(to_add))}/{len(to_add)}")
        print("Memory migration complete.")

    proc_rows = conn.execute("SELECT id, trigger, procedure FROM procedures").fetchall()
    existing_proc_ids = set(_proc_col.get()["ids"])
    proc_to_add = [(str(r[0]), r[1], r[2]) for r in proc_rows if str(r[0]) not in existing_proc_ids]
    if proc_to_add:
        print(f"Migrating {len(proc_to_add)} procedures to ChromaDB...")
        batch = 500
        for i in range(0, len(proc_to_add), batch):
            chunk = proc_to_add[i:i+batch]
            _proc_col.add(
                ids=[c[0] for c in chunk],
                embeddings=[_embed(c[1]) for c in chunk],
                documents=[c[2] for c in chunk],
                metadatas=[{"trigger": c[1]} for c in chunk]
            )
            print(f"  {min(i+batch, len(proc_to_add))}/{len(proc_to_add)}")
        print("Procedure migration complete.")

# ── DEAL TRACKING ─────────────────────────────────────────────────────────────
def get_deal(company: str) -> dict | None:
    row = conn.execute(
        "SELECT id, company, stage, last_touchpoint, next_action, notes, created_at, updated_at "
        "FROM deals WHERE company = ? COLLATE NOCASE",
        (company,)
    ).fetchone()
    if not row:
        return None
    return {
        'id': row[0], 'company': row[1], 'stage': row[2],
        'last_touchpoint': row[3], 'next_action': row[4], 'notes': row[5],
        'created_at': row[6], 'updated_at': row[7],
    }

def upsert_deal(company: str, stage: str = None, last_touchpoint: str = None,
                next_action: str = None, notes: str = None) -> dict:
    existing = get_deal(company)
    if existing:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        existing_notes = existing.get('notes') or ''
        if last_touchpoint and existing_notes is not None:
            stamp = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
            appended = f"[{stamp}] {last_touchpoint}"
            notes = (existing_notes + "\n" + appended).strip() if existing_notes else appended
        fields, values = [], []
        for col, val in [('stage', stage), ('last_touchpoint', last_touchpoint),
                         ('next_action', next_action), ('notes', notes)]:
            if val is not None:
                fields.append(f"{col} = ?")
                values.append(val)
        if fields:
            fields.append("updated_at = datetime('now')")
            conn.execute(
                f"UPDATE deals SET {', '.join(fields)} WHERE company = ? COLLATE NOCASE",
                values + [company]
            )
            conn.commit()
    else:
        conn.execute(
            "INSERT INTO deals (company, stage, last_touchpoint, next_action, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (company, stage or 'sourcing', last_touchpoint or '', next_action or '', notes or '')
        )
        conn.commit()
    return get_deal(company)

def get_stale_deals(days: int = 14) -> list:
    """Deals in active stages with no update in the last `days` days."""
    rows = conn.execute(
        "SELECT id, company, stage, last_touchpoint, next_action, updated_at "
        "FROM deals "
        "WHERE updated_at <= datetime('now', ?) "
        "AND stage NOT IN ('passed', 'invested') "
        "ORDER BY updated_at ASC",
        (f'-{days} days',)
    ).fetchall()
    return [
        {'id': r[0], 'company': r[1], 'stage': r[2],
         'last_touchpoint': r[3], 'next_action': r[4], 'updated_at': r[5]}
        for r in rows
    ]

def list_deals(stage: str = None) -> list:
    if stage:
        rows = conn.execute(
            "SELECT id, company, stage, last_touchpoint, next_action, notes, created_at, updated_at "
            "FROM deals WHERE stage = ? COLLATE NOCASE ORDER BY updated_at DESC",
            (stage,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, company, stage, last_touchpoint, next_action, notes, created_at, updated_at "
            "FROM deals ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {'id': r[0], 'company': r[1], 'stage': r[2], 'last_touchpoint': r[3],
         'next_action': r[4], 'notes': r[5], 'created_at': r[6], 'updated_at': r[7]}
        for r in rows
    ]

# ── CLAUDE RESPONSE HELPERS ─────────────────────────────────────────────────────
def response_text(response) -> str:
    """First text block's content. Sonnet 5 runs adaptive thinking by default, so
    content[0] is often a ThinkingBlock, not the TextBlock — never index content[0] directly."""
    return next((b.text for b in response.content if b.type == "text"), "")

def response_text_checked(response, label: str) -> str:
    """response_text(), but loud when the model returned no text at all — a thinking-only or
    max_tokens-truncated response looks identical to a legitimately empty answer (F-34)."""
    text = response_text(response)
    if not text.strip():
        import logging
        logging.error(
            f"{label}: no text block in response (stop_reason={getattr(response, 'stop_reason', '?')}) "
            f"— treating as failure, not as an empty result")
    return text

# ── JSON PARSING ──────────────────────────────────────────────────────────────
def parse_json_array(raw: str) -> list:
    """Parse a JSON array from model output, tolerating markdown code fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
