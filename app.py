import os
from datetime import datetime, timedelta, timezone
from typing import Optional
import json
import uuid

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ─── Environment ───────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", SUPABASE_ANON_KEY)
KAI_EMAIL = os.getenv("KAI_EMAIL", "")
KAI_PASSWORD = os.getenv("KAI_PASSWORD", "")
PORT = int(os.getenv("PORT", "8000"))

# ─── Load main website HTML ────────────────────────────────────────────────────
_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
try:
    with open(_HTML_PATH, "r", encoding="utf-8") as _f:
        _HTML_TEMPLATE = _f.read()
    print(f"✓ Loaded index.html ({len(_HTML_TEMPLATE)} chars)")
except FileNotFoundError:
    _HTML_TEMPLATE = "<h1>index.html not found — place it next to main.py</h1>"
    print("✗ index.html NOT FOUND")

def render_main_html(admin: bool = False) -> str:
    """Inject Supabase credentials into main website HTML."""
    html = _HTML_TEMPLATE
    # Replace placeholders with actual values
    html = html.replace("'SUPABASE_URL'", f"'{SUPABASE_URL}'")
    html = html.replace('"SUPABASE_URL"', f'"{SUPABASE_URL}"')
    html = html.replace("'SUPABASE_ANON_KEY'", f"'{SUPABASE_ANON_KEY}'")
    html = html.replace('"SUPABASE_ANON_KEY"', f'"{SUPABASE_ANON_KEY}"')
    html = html.replace("'https://kaisworldb.onrender.com'", f"'https://{os.getenv('RENDER_EXTERNAL_URL', 'localhost').replace('https://', '')}'")
    
    if admin:
        html = html.replace("</body>", "<script>window.__KAI_ADMIN__ = true;</script>\n</body>")
    return html

# ─── Supabase REST helper ──────────────────────────────────────────────────────
import httpx

async def supabase_query(
    table: str,
    *,
    method: str = "GET",
    params: dict = None,
    body: dict = None,
    select: str = "*",
    use_service_key: bool = True,
) -> dict:
    """Thin async wrapper around the Supabase REST API."""
    key = SUPABASE_SERVICE_KEY if use_service_key else SUPABASE_ANON_KEY
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    query_params = {"select": select}
    if params:
        query_params.update(params)

    async with httpx.AsyncClient(timeout=30) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers, params=query_params)
        elif method == "POST":
            resp = await client.post(url, headers=headers, params=query_params, json=body)
        elif method == "PATCH":
            resp = await client.patch(url, headers=headers, params=query_params, json=body)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers, params=query_params)
        else:
            resp = await client.request(method, url, headers=headers, params=query_params, json=body)
    
    try:
        data = resp.json()
    except Exception:
        data = {}
    return {"data": data, "status": resp.status_code, "ok": resp.is_success}

async def verify_kai_token(authorization: Optional[str] = Header(None)) -> bool:
    """Verify a Supabase JWT passed as Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.split(" ", 1)[1]
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {token}",
            },
        )
    return resp.is_success

# ─── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(title="KAICORE API", description="Backend for KAICORE", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Models ────────────────────────────────────────────────────────────────────
class TrackPayload(BaseModel):
    visitor_id: str
    page: str = "home"
    referrer: Optional[str] = None
    ua: Optional[str] = None

class ThreadCreate(BaseModel):
    content: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None

class VlogCreate(BaseModel):
    title: str
    video_url: str
    description: Optional[str] = None

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    profile_pic_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    mood_emoji: Optional[str] = None
    location: Optional[str] = None
    zodiac: Optional[str] = None
    bio: Optional[str] = None
    kai_online_status: bool = False
    social_instagram: Optional[str] = None
    social_tiktok: Optional[str] = None
    social_spotify: Optional[str] = None

class FunFactUpdate(BaseModel):
    slot_number: int
    fact_text: str

class CustomPetCreate(BaseModel):
    name: str
    pixel_data: list
    personality: str = "playful"

class LiveStatusUpdate(BaseModel):
    is_live: bool = False
    stream_url: Optional[str] = None
    offline_image_url: Optional[str] = None

class CommentCreate(BaseModel):
    thread_id: str
    content: str
    author_name: str = "Anonymous"
    parent_id: Optional[str] = None

class SignatureCreate(BaseModel):
    name: str
    message: str

class JournalEntryCreate(BaseModel):
    date: str
    mood: Optional[str] = None
    title: Optional[str] = None
    content: str

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    """Serve the main KAICORE frontend."""
    return HTMLResponse(content=render_main_html(admin=False))

@app.get("/kai", response_class=HTMLResponse, include_in_schema=False)
async def serve_kai_admin():
    """Serve Kai's admin entry point."""
    return HTMLResponse(content=render_main_html(admin=True))

# ══════════════════════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/track")
async def track_visit(payload: TrackPayload, request: Request):
    """Track visitor page views."""
    if not SUPABASE_URL:
        return JSONResponse({"ok": True, "note": "Supabase not configured"})
    
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)
    
    result = await supabase_query(
        "visitors",
        method="POST",
        body={
            "visitor_id": payload.visitor_id[:64],
            "page": (payload.page or "home")[:64],
            "referrer": (payload.referrer or "")[:500] or None,
            "ua": (payload.ua or "")[:500] or None,
            "ip": (ip or "")[:64] or None,
        },
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/stats")
async def get_stats(authorization: Optional[str] = Header(None)):
    """Get site statistics (requires Kai auth)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if not SUPABASE_URL:
        return JSONResponse({"error": "Supabase not configured"}, status_code=503)
    
    # Get counts from various tables
    threads = await supabase_query("threads", select="id", use_service_key=True)
    visitors = await supabase_query("visitors", select="id", use_service_key=True)
    comments = await supabase_query("comments", select="id", use_service_key=True)
    vlogs = await supabase_query("vlogs", select="id", use_service_key=True)
    signatures = await supabase_query("iwashere_signatures", select="id", use_service_key=True)
    pets = await supabase_query("custom_pets", select="id", use_service_key=True)
    
    # Get today's visitors
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_visitors = 0
    if isinstance(visitors.get("data"), list):
        today_visitors = sum(1 for v in visitors["data"] if str(v.get("created_at", "")).startswith(today))
    
    # Get active visitors (last 5 minutes)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    active = 0
    if isinstance(visitors.get("data"), list):
        active = sum(1 for v in visitors["data"] if str(v.get("created_at", "")) >= cutoff)
    
    def count(result):
        d = result.get("data") or []
        return len(d) if isinstance(d, list) else 0
    
    return JSONResponse({
        "threads": count(threads),
        "visitors": count(visitors),
        "today": today_visitors,
        "active": active,
        "comments": count(comments),
        "vlogs": count(vlogs),
        "signatures": count(signatures),
        "pets": count(pets),
    })

@app.get("/api/threads")
async def get_threads(limit: int = 50):
    """Get all threads."""
    if not SUPABASE_URL:
        return JSONResponse([], status_code=503)
    
    result = await supabase_query(
        "threads",
        params={"order": "created_at.desc", "limit": str(limit)},
        use_service_key=False,
    )
    return JSONResponse(result.get("data", []))

@app.post("/api/threads")
async def create_thread(thread: ThreadCreate, authorization: Optional[str] = Header(None)):
    """Create a new thread (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "threads",
        method="POST",
        body=thread.dict(),
        use_service_key=True,
    )
    return JSONResponse(result.get("data", {}))

@app.delete("/api/threads/{thread_id}")
async def delete_thread(thread_id: str, authorization: Optional[str] = Header(None)):
    """Delete a thread (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "threads",
        method="DELETE",
        params={"id": f"eq.{thread_id}"},
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/vlogs")
async def get_vlogs():
    """Get all vlogs."""
    if not SUPABASE_URL:
        return JSONResponse([], status_code=503)
    
    result = await supabase_query(
        "vlogs",
        params={"order": "created_at.desc"},
        use_service_key=False,
    )
    return JSONResponse(result.get("data", []))

@app.post("/api/vlogs")
async def create_vlog(vlog: VlogCreate, authorization: Optional[str] = Header(None)):
    """Create a new vlog (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "vlogs",
        method="POST",
        body=vlog.dict(),
        use_service_key=True,
    )
    return JSONResponse(result.get("data", {}))

@app.delete("/api/vlogs/{vlog_id}")
async def delete_vlog(vlog_id: str, authorization: Optional[str] = Header(None)):
    """Delete a vlog (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "vlogs",
        method="DELETE",
        params={"id": f"eq.{vlog_id}"},
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/profile")
async def get_profile():
    """Get Kai's profile settings."""
    if not SUPABASE_URL:
        return JSONResponse({}, status_code=503)
    
    result = await supabase_query("kai_settings", limit=1, use_service_key=False)
    data = result.get("data", [])
    return JSONResponse(data[0] if data else {})

@app.put("/api/profile")
async def update_profile(profile: ProfileUpdate, authorization: Optional[str] = Header(None)):
    """Update Kai's profile (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Check if profile exists
    existing = await supabase_query("kai_settings", limit=1, use_service_key=True)
    if existing.get("data") and len(existing["data"]) > 0:
        result = await supabase_query(
            "kai_settings",
            method="PATCH",
            params={"id": f"eq.{existing['data'][0]['id']}"},
            body=profile.dict(exclude_none=True),
            use_service_key=True,
        )
    else:
        result = await supabase_query(
            "kai_settings",
            method="POST",
            body=profile.dict(exclude_none=True),
            use_service_key=True,
        )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/fun-facts")
async def get_fun_facts():
    """Get fun facts about Kai."""
    if not SUPABASE_URL:
        return JSONResponse([], status_code=503)
    
    result = await supabase_query(
        "fun_facts",
        params={"order": "slot_number.asc"},
        use_service_key=False,
    )
    return JSONResponse(result.get("data", []))

@app.put("/api/fun-facts")
async def update_fun_facts(facts: list[FunFactUpdate], authorization: Optional[str] = Header(None)):
    """Update fun facts (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    for fact in facts:
        await supabase_query(
            "fun_facts",
            method="POST",
            body={"slot_number": fact.slot_number, "fact_text": fact.fact_text},
            params={"on_conflict": "slot_number"},
            use_service_key=True,
        )
    return JSONResponse({"ok": True})

@app.get("/api/comments/{thread_id}")
async def get_comments(thread_id: str):
    """Get comments for a thread."""
    if not SUPABASE_URL:
        return JSONResponse([], status_code=503)
    
    result = await supabase_query(
        "comments",
        params={"thread_id": f"eq.{thread_id}", "order": "created_at.asc"},
        use_service_key=False,
    )
    return JSONResponse(result.get("data", []))

@app.post("/api/comments")
async def create_comment(comment: CommentCreate):
    """Create a new comment."""
    result = await supabase_query(
        "comments",
        method="POST",
        body=comment.dict(),
        use_service_key=True,
    )
    return JSONResponse(result.get("data", {}))

@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: str, authorization: Optional[str] = Header(None)):
    """Delete a comment (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "comments",
        method="DELETE",
        params={"id": f"eq.{comment_id}"},
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.post("/api/comments/{comment_id}/vote")
async def vote_comment(comment_id: str, direction: str):
    """Upvote or downvote a comment."""
    # First get current votes
    current = await supabase_query(
        "comments",
        params={"id": f"eq.{comment_id}", "select": "upvotes,downvotes"},
        use_service_key=True,
    )
    if current.get("data") and len(current["data"]) > 0:
        curr = current["data"][0]
        if direction == "up":
            update = {"upvotes": (curr.get("upvotes") or 0) + 1}
        else:
            update = {"downvotes": (curr.get("downvotes") or 0) + 1}
        
        await supabase_query(
            "comments",
            method="PATCH",
            params={"id": f"eq.{comment_id}"},
            body=update,
            use_service_key=True,
        )
    return JSONResponse({"ok": True})

@app.get("/api/signatures")
async def get_signatures():
    """Get guestbook signatures."""
    if not SUPABASE_URL:
        return JSONResponse([], status_code=503)
    
    result = await supabase_query(
        "iwashere_signatures",
        params={"order": "created_at.desc"},
        use_service_key=False,
    )
    return JSONResponse(result.get("data", []))

@app.post("/api/signatures")
async def create_signature(signature: SignatureCreate, request: Request):
    """Add a signature to the guestbook."""
    visitor_id = request.headers.get("X-Visitor-ID", str(uuid.uuid4()))
    result = await supabase_query(
        "iwashere_signatures",
        method="POST",
        body={"name": signature.name, "message": signature.message, "visitor_id": visitor_id},
        use_service_key=True,
    )
    return JSONResponse(result.get("data", {}))

@app.delete("/api/signatures/{signature_id}")
async def delete_signature(signature_id: str, authorization: Optional[str] = Header(None)):
    """Delete a signature (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "iwashere_signatures",
        method="DELETE",
        params={"id": f"eq.{signature_id}"},
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/live-status")
async def get_live_status():
    """Get current live stream status."""
    if not SUPABASE_URL:
        return JSONResponse({"is_live": False}, status_code=503)
    
    result = await supabase_query("live_status", limit=1, use_service_key=False)
    data = result.get("data", [])
    return JSONResponse(data[0] if data else {"is_live": False})

@app.put("/api/live-status")
async def update_live_status(status: LiveStatusUpdate, authorization: Optional[str] = Header(None)):
    """Update live stream status (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    existing = await supabase_query("live_status", limit=1, use_service_key=True)
    if existing.get("data") and len(existing["data"]) > 0:
        result = await supabase_query(
            "live_status",
            method="PATCH",
            params={"id": f"eq.{existing['data'][0]['id']}"},
            body=status.dict(),
            use_service_key=True,
        )
    else:
        result = await supabase_query(
            "live_status",
            method="POST",
            body=status.dict(),
            use_service_key=True,
        )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/chat-messages")
async def get_chat_messages(limit: int = 50):
    """Get live chat messages."""
    if not SUPABASE_URL:
        return JSONResponse([], status_code=503)
    
    result = await supabase_query(
        "live_chat",
        params={"order": "created_at.asc", "limit": str(limit)},
        use_service_key=False,
    )
    return JSONResponse(result.get("data", []))

@app.post("/api/chat-messages")
async def send_chat_message(request: Request):
    """Send a chat message."""
    body = await request.json()
    result = await supabase_query(
        "live_chat",
        method="POST",
        body={
            "author_name": body.get("author_name", "Anonymous")[:50],
            "message": body.get("message", "")[:500],
            "session_id": body.get("session_id", str(uuid.uuid4())),
        },
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.delete("/api/chat-messages")
async def clear_chat(authorization: Optional[str] = Header(None)):
    """Clear all chat messages (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "live_chat",
        method="DELETE",
        params={"not": {"id": "eq.null"}},
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/custom-pets")
async def get_custom_pets():
    """Get all custom pets."""
    if not SUPABASE_URL:
        return JSONResponse([], status_code=503)
    
    result = await supabase_query(
        "custom_pets",
        params={"order": "created_at.desc"},
        use_service_key=False,
    )
    return JSONResponse(result.get("data", []))

@app.post("/api/custom-pets")
async def create_custom_pet(pet: CustomPetCreate, authorization: Optional[str] = Header(None)):
    """Create a custom pet (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "custom_pets",
        method="POST",
        body=pet.dict(),
        use_service_key=True,
    )
    return JSONResponse(result.get("data", {}))

@app.delete("/api/custom-pets/{pet_id}")
async def delete_custom_pet(pet_id: str, authorization: Optional[str] = Header(None)):
    """Delete a custom pet (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "custom_pets",
        method="DELETE",
        params={"id": f"eq.{pet_id}"},
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/journal-entries")
async def get_journal_entries():
    """Get journal entries."""
    if not SUPABASE_URL:
        return JSONResponse([], status_code=503)
    
    result = await supabase_query(
        "journal_entries",
        params={"order": "created_at.desc"},
        use_service_key=False,
    )
    return JSONResponse(result.get("data", []))

@app.post("/api/journal-entries")
async def create_journal_entry(entry: JournalEntryCreate, authorization: Optional[str] = Header(None)):
    """Create a journal entry (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "journal_entries",
        method="POST",
        body=entry.dict(),
        use_service_key=True,
    )
    return JSONResponse(result.get("data", {}))

@app.delete("/api/journal-entries/{entry_id}")
async def delete_journal_entry(entry_id: str, authorization: Optional[str] = Header(None)):
    """Delete a journal entry (Kai only)."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    result = await supabase_query(
        "journal_entries",
        method="DELETE",
        params={"id": f"eq.{entry_id}"},
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})

@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return JSONResponse({
        "status": "ok",
        "service": "KAICORE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "supabase": bool(SUPABASE_URL),
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
