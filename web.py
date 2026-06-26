"""
Read-only web viewer for Dot conversation threads.
Secured by a Bearer token (WEB_SECRET in .dot.env).
Access via Tailscale or SSH tunnel.
"""

import json, os, glob
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".dot.env")

WEB_SECRET = os.getenv("WEB_SECRET", "")
BASE_DIR   = Path(__file__).parent
SESSIONS_DIR = BASE_DIR / "sessions"
SESSION_JSON = BASE_DIR / "session.json"

app = FastAPI(docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _check_auth(request: Request):
    if not WEB_SECRET:
        raise HTTPException(500, "WEB_SECRET not set in .dot.env")
    token = request.cookies.get("dot_token") or ""
    if not token:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
    if token != WEB_SECRET:
        raise HTTPException(403, "Forbidden")


def _list_sessions() -> list[dict]:
    sessions = []
    # Multi-session directory
    if SESSIONS_DIR.exists():
        for f in sorted(SESSIONS_DIR.glob("*.json")):
            try:
                msgs = json.loads(f.read_text())
                sessions.append({
                    "name": f.stem,
                    "message_count": len(msgs),
                    "source": "sessions",
                })
            except Exception:
                pass
    # Legacy single session
    if SESSION_JSON.exists():
        try:
            msgs = json.loads(SESSION_JSON.read_text())
            sessions.append({
                "name": "default",
                "message_count": len(msgs),
                "source": "session.json",
            })
        except Exception:
            pass
    return sessions


def _load_session(name: str) -> list:
    # Check sessions/ directory first
    if SESSIONS_DIR.exists():
        f = SESSIONS_DIR / f"{name}.json"
        if f.exists():
            return json.loads(f.read_text())
    # Fall back to session.json for "default"
    if name == "default" and SESSION_JSON.exists():
        return json.loads(SESSION_JSON.read_text())
    raise HTTPException(404, f"Session '{name}' not found")


def _clean_message(msg: dict) -> dict | None:
    """Return a simplified message dict suitable for the UI, or None to skip."""
    role = msg.get("role", "")
    content = msg.get("content", "")

    if role not in ("user", "assistant"):
        return None

    # Flatten content blocks to plain text
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    parts.append(f"[Tool: {block.get('name', '?')}]")
                elif btype == "tool_result":
                    result = block.get("content", "")
                    if isinstance(result, list):
                        result = " ".join(r.get("text", "") for r in result if isinstance(r, dict))
                    parts.append(f"[Result: {str(result)[:200]}]")
                elif btype == "thinking":
                    continue  # skip internal thinking blocks
        text = "\n".join(p for p in parts if p).strip()
    else:
        text = str(content).strip()

    if not text:
        return None

    return {"role": role, "text": text}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def do_login(request: Request):
    from fastapi.responses import RedirectResponse
    form = await request.form()
    password = form.get("password", "")
    if password != WEB_SECRET:
        return templates.TemplateResponse("login.html", {
            "request": request, "error": "Wrong password"
        })
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("dot_token", WEB_SECRET, httponly=True, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from fastapi.responses import RedirectResponse
    try:
        _check_auth(request)
    except HTTPException:
        return RedirectResponse(url="/login")
    sessions = _list_sessions()
    default = sessions[0]["name"] if sessions else "default"
    return templates.TemplateResponse("index.html", {
        "request": request,
        "sessions": sessions,
        "default_session": default,
    })


@app.get("/api/sessions")
async def api_sessions(_=Depends(_check_auth)):
    return _list_sessions()


@app.get("/api/sessions/{name}")
async def api_session(name: str, _=Depends(_check_auth)):
    raw = _load_session(name)
    messages = [m for m in (_clean_message(msg) for msg in raw) if m]
    return {"name": name, "messages": messages}
