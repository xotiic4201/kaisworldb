import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import FastAPI, Request, Response, HTTPException, Header, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

load_dotenv()

# ─── Environment ───────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY    = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", SUPABASE_ANON_KEY)
KAI_EMAIL            = os.getenv("KAI_EMAIL")
KAI_PASSWORD         = os.getenv("KAI_PASSWORD")
# XOTIIC Owner credentials
XOTIIC_EMAIL         = os.getenv("XOTIIC_EMAIL", "")
XOTIIC_PASSWORD      = os.getenv("XOTIIC_PASSWORD", "")
PORT                 = int(os.getenv("PORT", "8000"))

KAI_LAST_ACTIVE = None

print(f"🔧 SUPABASE_URL: {'SET' if SUPABASE_URL else 'NOT SET'}")
print(f"🔧 KAI_EMAIL: {KAI_EMAIL}")
print(f"🔧 XOTIIC_EMAIL: {'SET' if XOTIIC_EMAIL else 'NOT SET'}")

# ─── Activity Logging System ───────────────────────────────────────────────────
LOG_ENTRIES = []

def add_log(level: str, message: str, details: dict = None):
    """Add entry to activity log - tracks everything"""
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,  # info, warning, error, success
        "message": message,
        "details": details
    }
    LOG_ENTRIES.insert(0, entry)
    # Keep only last 2000 logs
    while len(LOG_ENTRIES) > 2000:
        LOG_ENTRIES.pop()
    print(f"[{level.upper()}] {message}")
    return entry

# ─── Sprite storage ────────────────────────────────────────────────────────────
SPRITES_DIR = os.path.join(os.path.dirname(__file__), "sprites")
os.makedirs(SPRITES_DIR, exist_ok=True)

# ─── Load HTML files ───────────────────────────────────────────────────────────
def load_html(name):
    path = os.path.join(os.path.dirname(__file__), name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"<html><body><h1>{name} not found</h1></body></html>"

_HTML_TEMPLATE   = load_html("index.html")
_LOUNGE_TEMPLATE = load_html("kais_lounge.html")
_XOTIIC_TEMPLATE = load_html("xotiic_dashboard.html")  # Your XOTIIC dashboard HTML file

def inject_config(html: str, admin: bool = False) -> str:
    html = html.replace("'SUPABASE_URL'",         f"'{SUPABASE_URL}'")
    html = html.replace('"SUPABASE_URL"',          f'"{SUPABASE_URL}"')
    html = html.replace("'SUPABASE_ANON_KEY'",     f"'{SUPABASE_ANON_KEY}'")
    html = html.replace('"SUPABASE_ANON_KEY"',     f'"{SUPABASE_ANON_KEY}"')
    html = html.replace("'SUPABASE_SERVICE_KEY'",  f"'{SUPABASE_SERVICE_KEY}'")
    html = html.replace('"SUPABASE_SERVICE_KEY"',  f'"{SUPABASE_SERVICE_KEY}"')
    if admin:
        html = html.replace("</body>", "<script>window.__KAI_ADMIN__ = true;</script>\n</body>")
    return html

# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="KAICORE API", version="4.0.0", docs_url="/api/docs", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/sprites", StaticFiles(directory=SPRITES_DIR), name="sprites")

# ─── Supabase helper ───────────────────────────────────────────────────────────
def sb_headers(service=True):
    key = SUPABASE_SERVICE_KEY if service else SUPABASE_ANON_KEY
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

async def sb_get(table: str, params: str = "select=*&order=created_at.desc"):
    if not SUPABASE_URL:
        return []
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=sb_headers())
    return r.json() if r.is_success else []

async def sb_post(table: str, body: dict):
    if not SUPABASE_URL:
        return None
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=sb_headers(), json=body)
    return r.json() if r.is_success else None

async def sb_patch(table: str, filter_str: str, body: dict):
    if not SUPABASE_URL:
        return None
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.patch(f"{SUPABASE_URL}/rest/v1/{table}?{filter_str}", headers=sb_headers(), json=body)
    return r.json() if r.is_success else None

async def sb_delete(table: str, filter_str: str):
    if not SUPABASE_URL:
        return True
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(f"{SUPABASE_URL}/rest/v1/{table}?{filter_str}", headers=sb_headers())
    return r.is_success

# ─── Auth helpers ──────────────────────────────────────────────────────────────
async def require_kai(authorization: Optional[str] = Header(None)):
    if not authorization or authorization != "Bearer kai_authenticated":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

async def require_xotiic(authorization: Optional[str] = Header(None)):
    """Special auth for XOTIIC owner panel"""
    if not authorization or authorization != "Bearer xotiic_authenticated":
        raise HTTPException(status_code=401, detail="Unauthorized - XOTIIC access only")
    return True

async def is_kai(authorization: Optional[str] = Header(None)) -> bool:
    return authorization == "Bearer kai_authenticated"

async def is_xotiic(authorization: Optional[str] = Header(None)) -> bool:
    return authorization == "Bearer xotiic_authenticated"

# ─── Models ────────────────────────────────────────────────────────────────────
class LoginPayload(BaseModel):
    email: str
    password: str

class TrackPayload(BaseModel):
    visitor_id: str
    page: str = "home"
    referrer: Optional[str] = None
    ua: Optional[str] = None

class ThreadPayload(BaseModel):
    content: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None

class PostPayload(BaseModel):
    caption: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    like_count: int = 0

class CommentPayload(BaseModel):
    thread_id: str
    parent_id: Optional[str] = None
    author_name: Optional[str] = "anonymous"
    author_color: Optional[str] = "#FF69B4"
    content: str

class VlogPayload(BaseModel):
    title: str
    video_url: str
    description: Optional[str] = None

class ProfilePayload(BaseModel):
    name: Optional[str] = None
    mood_emoji: Optional[str] = None
    location: Optional[str] = None
    zodiac: Optional[str] = None
    profile_pic_url: Optional[str] = None
    bio: Optional[str] = None
    kai_online_status: Optional[bool] = None
    social_instagram: Optional[str] = None
    social_tiktok: Optional[str] = None
    social_spotify: Optional[str] = None

class FactsPayload(BaseModel):
    slot_number: int
    fact_text: str

class LivePayload(BaseModel):
    is_live: bool = False
    stream_url: Optional[str] = None
    offline_image_url: Optional[str] = None

class SignaturePayload(BaseModel):
    name: str
    message: str
    visitor_id: Optional[str] = None

class ChatPayload(BaseModel):
    author_name: str = "Anonymous"
    message: str
    session_id: Optional[str] = None

class JournalPayload(BaseModel):
    date: Optional[str] = None
    mood: Optional[str] = None
    title: Optional[str] = None
    content: str

class VotePayload(BaseModel):
    direction: str  # "up" or "down"

class LogPayload(BaseModel):
    level: str
    message: str
    details: Optional[dict] = None

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    add_log("info", "Main page served")
    return HTMLResponse(content=inject_config(_HTML_TEMPLATE, admin=False))

@app.get("/kai", response_class=HTMLResponse, include_in_schema=False)
async def serve_kai_admin():
    add_log("info", "Kai admin page served")
    return HTMLResponse(content=inject_config(_HTML_TEMPLATE, admin=True))

@app.get("/lounge", response_class=HTMLResponse, include_in_schema=False)
async def serve_lounge():
    add_log("info", "Lounge page served")
    return HTMLResponse(content=inject_config(_LOUNGE_TEMPLATE, admin=False))

@app.get("/xotiic", response_class=HTMLResponse, include_in_schema=False)
async def serve_xotiic_dashboard():
    """Special XOTIIC owner dashboard endpoint"""
    add_log("info", "XOTIIC dashboard page served")
    if os.path.exists(os.path.join(os.path.dirname(__file__), "xotiic_dashboard.html")):
        with open(os.path.join(os.path.dirname(__file__), "xotiic_dashboard.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    # Fallback if file doesn't exist
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head><title>XOTIIC Dashboard</title></head>
    <body>
        <h1>XOTIIC Dashboard</h1>
        <p>Please save the XOTIIC dashboard HTML file as 'xotiic_dashboard.html' in the same directory.</p>
    </body>
    </html>
    """)

# ══════════════════════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/login")
async def auth_login(payload: LoginPayload):
    if payload.email == KAI_EMAIL and payload.password == KAI_PASSWORD:
        add_log("success", f"Kai login successful: {payload.email}")
        return {"access_token": "kai_authenticated", "token_type": "bearer", "expires_in": 86400}
    add_log("warning", f"Failed Kai login attempt: {payload.email}")
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.post("/api/xotiic/login")
async def xotiic_login(payload: LoginPayload):
    """Special login endpoint for /xotiic dashboard"""
    print(f"XOTIIC Login attempt: {payload.email}")
    print(f"Expected: {XOTIIC_EMAIL}")
    if payload.email == XOTIIC_EMAIL and payload.password == XOTIIC_PASSWORD:
        print("XOTIIC login SUCCESS")
        return {"access_token": "xotiic_authenticated", "token_type": "bearer", "expires_in": 86400, "role": "owner"}
    print("XOTIIC login FAILED")
    raise HTTPException(status_code=401, detail="Invalid XOTIIC credentials")

# ══════════════════════════════════════════════════════════════════════════════
#  XOTIIC LOGGING ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/xotiic/logs")
async def get_xotiic_logs(authorization: Optional[str] = Header(None)):
    """Get all activity logs - XOTIIC only"""
    await require_xotiic(authorization)
    return JSONResponse({"logs": LOG_ENTRIES})

@app.post("/api/xotiic/log")
async def add_xotiic_log(payload: LogPayload, authorization: Optional[str] = Header(None)):
    """Add a log entry from frontend"""
    await require_xotiic(authorization)
    add_log(payload.level, payload.message, payload.details)
    return JSONResponse({"ok": True})

@app.get("/api/xotiic/stats")
async def get_xotiic_stats(authorization: Optional[str] = Header(None)):
    """Get detailed stats for XOTIIC dashboard"""
    await require_xotiic(authorization)
    
    # Get all data counts
    threads = await sb_get("threads", "select=id")
    posts = await sb_get("posts", "select=id")
    vlogs = await sb_get("vlogs", "select=id")
    comments = await sb_get("comments", "select=id")
    signatures = await sb_get("iwashere_signatures", "select=id")
    journal = await sb_get("journal_entries", "select=id")
    visitors = await sb_get("visitors", "select=visitor_id,created_at")
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_visitors = len(set(v.get("visitor_id", "") for v in visitors if v.get("created_at", "").startswith(today))) if isinstance(visitors, list) else 0
    
    return JSONResponse({
        "total_threads": len(threads) if isinstance(threads, list) else 0,
        "total_posts": len(posts) if isinstance(posts, list) else 0,
        "total_vlogs": len(vlogs) if isinstance(vlogs, list) else 0,
        "total_comments": len(comments) if isinstance(comments, list) else 0,
        "total_signatures": len(signatures) if isinstance(signatures, list) else 0,
        "total_journal": len(journal) if isinstance(journal, list) else 0,
        "total_visitors": len(set(v.get("visitor_id", "") for v in visitors)) if isinstance(visitors, list) else 0,
        "today_visitors": today_visitors,
        "log_count": len(LOG_ENTRIES)
    })

# ══════════════════════════════════════════════════════════════════════════════
#  KAI ONLINE STATUS (heartbeat-based)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/kai/heartbeat")
async def kai_heartbeat(authorization: Optional[str] = Header(None)):
    """Track when Kai is active in the lounge."""
    global KAI_LAST_ACTIVE
    await require_kai(authorization)
    KAI_LAST_ACTIVE = datetime.now(timezone.utc)
    add_log("info", "Kai heartbeat received")
    return JSONResponse({"status": "online"})

@app.get("/api/kai/status")
async def kai_status():
    global KAI_LAST_ACTIVE
    if KAI_LAST_ACTIVE:
        diff = (datetime.now(timezone.utc) - KAI_LAST_ACTIVE).total_seconds()
        if diff < 300:
            return JSONResponse({"online": True, "last_active": KAI_LAST_ACTIVE.isoformat()})
    return JSONResponse({"online": False})

# ══════════════════════════════════════════════════════════════════════════════
#  PROFILE  (kai_settings table)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/profile")
async def get_profile():
    rows = await sb_get("kai_settings", "select=*&limit=1")
    if isinstance(rows, list) and rows:
        return JSONResponse(rows[0])
    return JSONResponse({})

@app.patch("/api/profile")
async def update_profile(payload: ProfilePayload, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    body = {k: v for k, v in payload.model_dump().items() if v is not None}
    body["updated_at"] = datetime.now(timezone.utc).isoformat()
    rows = await sb_get("kai_settings", "select=id&limit=1")
    if isinstance(rows, list) and rows:
        await sb_patch("kai_settings", f"id=eq.{rows[0]['id']}", body)
    else:
        body.setdefault("name", "KAI")
        await sb_post("kai_settings", body)
    add_log("info", "Profile updated")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  THREADS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/threads")
async def get_threads():
    data = await sb_get("threads", "select=*&order=created_at.desc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/threads")
async def create_thread(payload: ThreadPayload, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    row = await sb_post("threads", {
        "content": payload.content,
        "image_url": payload.image_url,
        "video_url": payload.video_url,
        "view_count": 0,
        "comment_count": 0,
    })
    add_log("info", f"Thread created: {payload.content[:50]}...")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/threads/{thread_id}")
async def delete_thread(thread_id: str, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    await sb_delete("threads", f"id=eq.{thread_id}")
    add_log("success", f"Thread deleted: {thread_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  COMMENTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/comments")
async def get_comments(thread_id: str):
    data = await sb_get("comments", f"select=*&thread_id=eq.{thread_id}&order=created_at.asc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/comments")
async def create_comment(payload: CommentPayload):
    row = await sb_post("comments", {
        "thread_id": payload.thread_id,
        "parent_id": payload.parent_id,
        "author_name": payload.author_name or "anonymous",
        "author_color": payload.author_color or "#FF69B4",
        "content": payload.content,
        "upvotes": 0,
        "downvotes": 0,
    })
    # Increment comment_count on thread
    rows = await sb_get("threads", f"select=comment_count&id=eq.{payload.thread_id}")
    if isinstance(rows, list) and rows:
        cnt = (rows[0].get("comment_count") or 0) + 1
        await sb_patch("threads", f"id=eq.{payload.thread_id}", {"comment_count": cnt})
    add_log("info", f"Comment added to thread {payload.thread_id}")
    return JSONResponse({"ok": True, "data": row})

@app.post("/api/comments/{comment_id}/vote")
async def vote_comment(comment_id: str, payload: VotePayload):
    rows = await sb_get("comments", f"select=upvotes,downvotes&id=eq.{comment_id}")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Comment not found")
    row = rows[0]
    if payload.direction == "up":
        await sb_patch("comments", f"id=eq.{comment_id}", {"upvotes": (row.get("upvotes") or 0) + 1})
    else:
        await sb_patch("comments", f"id=eq.{comment_id}", {"downvotes": (row.get("downvotes") or 0) + 1})
    return JSONResponse({"ok": True})

@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: str, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    await sb_delete("comments", f"id=eq.{comment_id}")
    add_log("success", f"Comment deleted: {comment_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  POSTS (feed)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/posts")
async def get_posts():
    data = await sb_get("posts", "select=*&order=created_at.desc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/posts")
async def create_post(payload: PostPayload, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    row = await sb_post("posts", {
        "caption": payload.caption,
        "image_url": payload.image_url,
        "video_url": payload.video_url,
        "like_count": 0,
    })
    add_log("info", f"Post created: {payload.caption[:50] if payload.caption else 'No caption'}...")
    return JSONResponse({"ok": True, "data": row})

@app.post("/api/posts/{post_id}/like")
async def like_post(post_id: str):
    rows = await sb_get("posts", f"select=like_count&id=eq.{post_id}")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Post not found")
    new_count = (rows[0].get("like_count") or 0) + 1
    await sb_patch("posts", f"id=eq.{post_id}", {"like_count": new_count})
    add_log("info", f"Post liked: {post_id} (now {new_count} likes)")
    return JSONResponse({"ok": True, "like_count": new_count})

@app.delete("/api/posts/{post_id}")
async def delete_post(post_id: str, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    await sb_delete("posts", f"id=eq.{post_id}")
    add_log("success", f"Post deleted: {post_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  VLOGS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/vlogs")
async def get_vlogs():
    data = await sb_get("vlogs", "select=*&order=created_at.desc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/vlogs")
async def create_vlog(payload: VlogPayload, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    row = await sb_post("vlogs", {
        "title": payload.title,
        "video_url": payload.video_url,
        "description": payload.description,
    })
    add_log("info", f"Vlog created: {payload.title}")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/vlogs/{vlog_id}")
async def delete_vlog(vlog_id: str, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    await sb_delete("vlogs", f"id=eq.{vlog_id}")
    add_log("success", f"Vlog deleted: {vlog_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  FUN FACTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/facts")
async def get_facts():
    data = await sb_get("fun_facts", "select=*&order=slot_number.asc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/facts")
async def upsert_facts(payload: List[FactsPayload], authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    for item in payload:
        slot = item.slot_number
        text = item.fact_text
        existing = await sb_get("fun_facts", f"select=id&slot_number=eq.{slot}&limit=1")
        if isinstance(existing, list) and existing:
            await sb_patch("fun_facts", f"slot_number=eq.{slot}", {"fact_text": text, "updated_at": datetime.now(timezone.utc).isoformat()})
        else:
            await sb_post("fun_facts", {"slot_number": slot, "fact_text": text})
    add_log("info", f"Updated {len(payload)} fun facts")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  LIVE STATUS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/live")
async def get_live():
    rows = await sb_get("live_status", "select=*&limit=1")
    if isinstance(rows, list) and rows:
        return JSONResponse(rows[0])
    return JSONResponse({"is_live": False, "stream_url": None, "offline_image_url": None})

@app.patch("/api/live")
async def update_live(payload: LivePayload, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    body = {
        "is_live": payload.is_live,
        "stream_url": payload.stream_url,
        "offline_image_url": payload.offline_image_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    rows = await sb_get("live_status", "select=id&limit=1")
    if isinstance(rows, list) and rows:
        await sb_patch("live_status", f"id=eq.{rows[0]['id']}", body)
    else:
        await sb_post("live_status", body)
    add_log("success", f"Live status updated: {'LIVE' if payload.is_live else 'OFFLINE'}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  CHAT
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/chat")
async def get_chat():
    data = await sb_get("chat_messages", "select=*&order=created_at.asc&limit=50")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/chat")
async def post_chat(payload: ChatPayload):
    row = await sb_post("chat_messages", {
        "author_name": payload.author_name[:50],
        "message": payload.message[:500],
        "session_id": payload.session_id,
    })
    add_log("info", f"Chat message from {payload.author_name}")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/chat")
async def clear_chat(authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    if SUPABASE_URL:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.delete(
                f"{SUPABASE_URL}/rest/v1/chat_messages?id=neq.00000000-0000-0000-0000-000000000000",
                headers=sb_headers()
            )
    add_log("warning", "Chat history cleared")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  SIGNATURES (I Was Here)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/signatures")
async def get_signatures():
    data = await sb_get("iwashere_signatures", "select=*&order=created_at.desc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/signatures")
async def create_signature(payload: SignaturePayload):
    row = await sb_post("iwashere_signatures", {
        "name": payload.name[:100],
        "message": payload.message[:500],
        "visitor_id": payload.visitor_id,
    })
    add_log("info", f"New signature from {payload.name}")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/signatures/{sig_id}")
async def delete_signature(sig_id: str, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    await sb_delete("iwashere_signatures", f"id=eq.{sig_id}")
    add_log("success", f"Signature deleted: {sig_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  JOURNAL (Kai-only)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/journal")
async def get_journal(authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    data = await sb_get("journal_entries", "select=*&order=created_at.desc")
    add_log("info", "Journal accessed")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/journal")
async def create_journal(payload: JournalPayload, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    row = await sb_post("journal_entries", {
        "date": payload.date,
        "mood": payload.mood,
        "title": payload.title,
        "content": payload.content,
    })
    add_log("info", f"Journal entry created: {payload.title}")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/journal/{entry_id}")
async def delete_journal(entry_id: str, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    await sb_delete("journal_entries", f"id=eq.{entry_id}")
    add_log("success", f"Journal entry deleted: {entry_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  VISITOR TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/track")
async def track_visit(payload: TrackPayload, request: Request):
    if not SUPABASE_URL:
        return JSONResponse({"ok": True})
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)
    await sb_post("visitors", {
        "visitor_id": payload.visitor_id[:64],
        "page": (payload.page or "home")[:64],
        "referrer": (payload.referrer or "")[:200] or None,
        "ua": (payload.ua or "")[:300] or None,
        "ip": (ip or "")[:64] or None,
    })
    add_log("info", f"Visitor tracked: {payload.page} - {payload.visitor_id[:20]}...")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  STATS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def get_stats():
    if not SUPABASE_URL:
        return JSONResponse({"total": 0, "today": 0, "active": 0, "threads": 0, "comments": 0, "vlogs": 0, "signatures": 0, "pages": []})

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    visitors     = await sb_get("visitors",            "select=visitor_id,page,created_at")
    threads_data = await sb_get("threads",             "select=id")
    comments_data= await sb_get("comments",            "select=id")
    vlogs_data   = await sb_get("vlogs",               "select=id")
    sigs_data    = await sb_get("iwashere_signatures", "select=id")
    posts_data   = await sb_get("posts",               "select=id")

    if not isinstance(visitors, list): visitors = []

    unique = len(set(v.get("visitor_id", "") for v in visitors))
    today_count = len(set(v.get("visitor_id", "") for v in visitors if v.get("created_at", "").startswith(today)))
    active = len(set(v.get("visitor_id", "") for v in visitors if v.get("created_at", "") >= five_min_ago))

    page_counts = {}
    for v in visitors:
        pg = v.get("page") or "home"
        page_counts[pg] = page_counts.get(pg, 0) + 1
    pages = [{"label": k, "count": v} for k,v in sorted(page_counts.items(), key=lambda x:-x[1])][:10]

    def cnt(d): return len(d) if isinstance(d, list) else 0

    return JSONResponse({
        "total": unique, "today": today_count, "active": active,
        "threads": cnt(threads_data), "comments": cnt(comments_data),
        "vlogs": cnt(vlogs_data), "signatures": cnt(sigs_data),
        "posts": cnt(posts_data), "pages": pages,
    })

# ══════════════════════════════════════════════════════════════════════════════
#  SPRITE UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

ALLOWED_SPRITE_TYPES = {"cat", "bird", "bat", "dog", "custom"}
ALLOWED_MIME = {"image/png", "image/gif", "image/webp", "image/jpeg"}

@app.post("/api/sprites/upload")
async def upload_sprite(
    sprite_type: str = Form(...),
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    await require_kai(authorization)
    if sprite_type not in ALLOWED_SPRITE_TYPES:
        raise HTTPException(status_code=400, detail=f"sprite_type must be one of {ALLOWED_SPRITE_TYPES}")
    content_type = file.content_type or "image/png"
    if content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail="Only PNG, GIF, WebP, JPEG allowed")
    ext = content_type.split("/")[-1].replace("jpeg", "jpg")
    filename = f"{sprite_type}.{ext}"
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")
    with open(os.path.join(SPRITES_DIR, filename), "wb") as f:
        f.write(data)
    add_log("info", f"Sprite uploaded: {sprite_type}")
    return JSONResponse({"ok": True, "url": f"/sprites/{filename}", "filename": filename})

@app.get("/api/sprites/list")
async def list_sprites():
    sprites = {}
    if os.path.exists(SPRITES_DIR):
        for fname in os.listdir(SPRITES_DIR):
            for stype in ALLOWED_SPRITE_TYPES:
                if fname.startswith(stype + "."):
                    sprites[stype] = f"/sprites/{fname}"
    return JSONResponse({"sprites": sprites})

# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE GENERIC PROXY (for lounge)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/supabase/{table}")
async def supabase_proxy(table: str, request: Request, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    if not SUPABASE_URL:
        return JSONResponse({"data": [], "error": "Supabase not configured"})
    try:
        body = await request.json()
    except:
        body = {}
    method      = body.get("method", "GET")
    query_body  = body.get("body")
    select      = body.get("select", "*")
    filter_p    = body.get("filter", None)
    key = SUPABASE_SERVICE_KEY
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=30) as c:
        if method == "GET":
            url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}"
            if filter_p: url += f"&{filter_p}"
            resp = await c.get(url, headers=headers)
        elif method == "POST":
            resp = await c.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers, json=query_body)
        elif method == "PATCH":
            if not filter_p: return JSONResponse({"data": [], "error": "PATCH requires filter"})
            resp = await c.patch(f"{SUPABASE_URL}/rest/v1/{table}?{filter_p}", headers=headers, json=query_body)
        elif method == "DELETE":
            if not filter_p: return JSONResponse({"data": [], "error": "DELETE requires filter"})
            resp = await c.delete(f"{SUPABASE_URL}/rest/v1/{table}?{filter_p}", headers=headers)
        else:
            return JSONResponse({"data": [], "error": "Invalid method"})
    try: data = resp.json()
    except: data = []
    return JSONResponse({
        "data": data if isinstance(data, list) else ([data] if data else []),
        "error": None if resp.is_success else str(data)
    })

# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok", "service": "KAICORE", "version": "4.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "supabase": bool(SUPABASE_URL),
        "xotiic_enabled": bool(XOTIIC_EMAIL and XOTIIC_PASSWORD)
    })

@app.get("/api/config")
async def get_config():
    return JSONResponse({"supabase_url": SUPABASE_URL, "supabase_anon": SUPABASE_ANON_KEY})

# ─── Dev server ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=True)
