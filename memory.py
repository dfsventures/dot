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
""")
conn.commit()

# ── VECTOR STORE (ChromaDB + sentence-transformers) ───────────────────────────
_embedder = SentenceTransformer('all-MiniLM-L6-v2')
_chroma   = chromadb.PersistentClient(path=CHROMA_PATH)
_col      = _chroma.get_or_create_collection("memories")

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
    rows = conn.execute("SELECT content FROM memories ORDER BY created_at DESC").fetchall()
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

def migrate_sqlite_to_chroma():
    """Sync all existing SQLite memories into ChromaDB.
    Safe to run multiple times — skips IDs already in ChromaDB."""
    rows = conn.execute("SELECT id, content, tags FROM memories").fetchall()
    if not rows:
        return
    existing_ids = set(_col.get()["ids"])
    to_add = [(str(r[0]), r[1], r[2]) for r in rows if str(r[0]) not in existing_ids]
    if not to_add:
        return
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
    print("Migration complete.")

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
