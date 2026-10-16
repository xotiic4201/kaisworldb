import asyncio
import base64
import hmac
import json
import os
import re
import secrets
import struct
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

load_dotenv()

# ─── Environment ───────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY    = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", SUPABASE_ANON_KEY)
KAI_EMAIL            = os.getenv("KAI_EMAIL", "")
KAI_PASSWORD         = os.getenv("KAI_PASSWORD", "")
XOTIIC_EMAIL         = os.getenv("XOTIIC_EMAIL", "")
XOTIIC_PASSWORD      = os.getenv("XOTIIC_PASSWORD", "")
PORT                 = int(os.getenv("PORT", "8000"))
ENABLE_API_DOCS      = os.getenv("ENABLE_API_DOCS", "") == "1"

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SPRITES_DIR = os.path.join(BASE_DIR, "sprites")
ASSETS_DIR  = os.path.join(BASE_DIR, "assets")
os.makedirs(SPRITES_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

KAI_LAST_ACTIVE: Optional[datetime] = None
AUTH_SESSIONS: dict = {}

print(f"🔧 SUPABASE_URL: {'SET' if SUPABASE_URL else 'NOT SET'}")
print(f"🔧 Kai login: {'SET' if KAI_EMAIL and KAI_PASSWORD else 'NOT SET'}")
print(f"🔧 XOTIIC login: {'SET' if XOTIIC_EMAIL and XOTIIC_PASSWORD else 'NOT SET'}")

# ─── Activity Logging System ───────────────────────────────────────────────────
LOG_ENTRIES = []

def add_log(level: str, message: str, details: dict = None):
    """Add entry to activity log - tracks everything"""
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        "details": details
    }
    LOG_ENTRIES.insert(0, entry)
    while len(LOG_ENTRIES) > 2000:
        LOG_ENTRIES.pop()
    print(f"[{level.upper()}] {message}")
    return entry

# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="KAICORE API", version="5.0.0",
    docs_url="/api/docs" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.mount("/sprites", StaticFiles(directory=SPRITES_DIR), name="sprites")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# ─── Security headers & request size cap ───────────────────────────────────────
MAX_BODY_BYTES = 6 * 1024 * 1024
MAX_MEDIA_BYTES = 50 * 1024 * 1024   # Supabase's free plan caps single uploads at 50MB

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob: https: http:",
    "media-src 'self' blob: https: http:",
    "frame-src https://www.youtube-nocookie.com https://www.youtube.com https://open.spotify.com",
    "connect-src 'self' https://kaisworldb.onrender.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

@app.middleware("http")
async def security_headers(request: Request, call_next):
    length = request.headers.get("content-length", "")
    limit = MAX_MEDIA_BYTES + 1024 * 1024 if request.url.path == "/api/media/upload" else MAX_BODY_BYTES
    if length.isdigit() and int(length) > limit:
        return JSONResponse({"detail": "That upload is too big."}, status_code=413)
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("X-Frame-Options", "DENY")
    # The lounge needs the camera and mic to go live from the website.
    h.setdefault("Permissions-Policy", "camera=(self), microphone=(self), display-capture=(self), geolocation=()")
    if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if h.get("content-type", "").startswith("text/html"):
        h["Content-Security-Policy"] = CSP
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/sprites/img/"):
        h.setdefault("Cache-Control", "no-store")
    return response

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

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ─── Input validation helpers ──────────────────────────────────────────────────
ID_RE    = re.compile(r"^[A-Za-z0-9-]{1,64}$")
URL_RE   = re.compile(r"^https?://[^\s\"'<>`\\]+$", re.IGNORECASE)
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

def check_id(value: str) -> str:
    """IDs are pasted into PostgREST filters, so only allow plain id characters."""
    if not ID_RE.match(value or ""):
        raise HTTPException(status_code=400, detail="That id doesn't look right.")
    return value

def clean_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 1000 or not URL_RE.match(value):
        raise ValueError("must be a full link that starts with https://")
    return value

def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.replace("\x00", "").strip()
    return value or None

# ─── Rate limiting ─────────────────────────────────────────────────────────────
RATE_BUCKETS: dict = {}

def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    return request.client.host if request.client else "unknown"

def hit(key: str, limit: int, window: float) -> bool:
    """Record one hit for key. Returns False once the key is over its limit for the window."""
    now = time.monotonic()
    if len(RATE_BUCKETS) > 20000:
        for k in [k for k, q in RATE_BUCKETS.items() if not q or now - q[-1] > 3600]:
            del RATE_BUCKETS[k]
        if len(RATE_BUCKETS) > 20000:
            RATE_BUCKETS.clear()
    q = RATE_BUCKETS.setdefault(key, deque())
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True

def rate_limit(request: Request, action: str, limit: int, window: float):
    if not hit(f"{action}:{client_ip(request)}", limit, window):
        raise HTTPException(status_code=429, detail="Whoa, slow down a little ♡ try again in a minute.")

# ─── Auth helpers ──────────────────────────────────────────────────────────────
LOGIN_FAILS: dict = {}
GLOBAL_LOGIN_FAILS: deque = deque()
LOGIN_WINDOW = 15 * 60

def _recent(q: deque) -> deque:
    now = time.monotonic()
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    return q

def login_blocked(request: Request) -> bool:
    per_ip = _recent(LOGIN_FAILS.setdefault(client_ip(request), deque()))
    return len(per_ip) >= 8 or len(_recent(GLOBAL_LOGIN_FAILS)) >= 100

async def login_failed(request: Request, who: str):
    LOGIN_FAILS.setdefault(client_ip(request), deque()).append(time.monotonic())
    GLOBAL_LOGIN_FAILS.append(time.monotonic())
    add_log("warning", f"Failed {who} login attempt", {"ip": client_ip(request)})
    await asyncio.sleep(0.5)

def creds_match(email: str, password: str, want_email: str, want_password: str) -> bool:
    # An unset account must never match an empty login.
    if not want_email or not want_password:
        return False
    email_ok = hmac.compare_digest(email.strip().lower().encode(), want_email.strip().lower().encode())
    pass_ok = hmac.compare_digest(password.encode(), want_password.encode())
    return email_ok and pass_ok

def _session_has_role(authorization: str, *roles: str) -> bool:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    session = AUTH_SESSIONS.get(token)
    if not session:
        return False
    role, expires_at = session
    if expires_at <= datetime.now(timezone.utc):
        AUTH_SESSIONS.pop(token, None)
        return False
    return role in roles

def _issue_session(role: str) -> str:
    now = datetime.now(timezone.utc)
    for tok in [t for t, (_, exp) in AUTH_SESSIONS.items() if exp <= now]:
        del AUTH_SESSIONS[tok]
    while len(AUTH_SESSIONS) >= 200:
        AUTH_SESSIONS.pop(next(iter(AUTH_SESSIONS)))
    token = secrets.token_urlsafe(32)
    AUTH_SESSIONS[token] = (role, now + timedelta(hours=24))
    return token

async def require_kai(authorization: Optional[str] = Header(None)):
    if not authorization or not _session_has_role(authorization, "kai"):
        raise HTTPException(status_code=401, detail="Please log in again.")
    return True

async def require_xotiic(authorization: Optional[str] = Header(None)):
    if not authorization or not _session_has_role(authorization, "xotiic"):
        raise HTTPException(status_code=401, detail="Unauthorized - XOTIIC access only")
    return True

async def require_kai_or_xotiic(authorization: Optional[str] = Header(None)):
    if not authorization or not _session_has_role(authorization, "kai", "xotiic"):
        raise HTTPException(status_code=401, detail="Please log in again.")
    return True

# ─── Models ────────────────────────────────────────────────────────────────────
class LoginPayload(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)

class TrackPayload(BaseModel):
    visitor_id: str = Field(max_length=64)
    page: str = Field("home", max_length=64)
    referrer: Optional[str] = Field(None, max_length=2000)
    ua: Optional[str] = Field(None, max_length=2000)

class ThreadPayload(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    image_url: Optional[str] = None
    video_url: Optional[str] = None

    _urls = field_validator("image_url", "video_url")(clean_url)

    @field_validator("content")
    @classmethod
    def _content(cls, v):
        v = clean_text(v)
        if not v:
            raise ValueError("can't be empty")
        return v

class PostPayload(BaseModel):
    caption: Optional[str] = Field(None, max_length=2200)
    image_url: Optional[str] = None
    video_url: Optional[str] = None

    _urls = field_validator("image_url", "video_url")(clean_url)
    _caption = field_validator("caption")(clean_text)

class CommentPayload(BaseModel):
    thread_id: str = Field(pattern=ID_RE.pattern)
    parent_id: Optional[str] = Field(None, pattern=ID_RE.pattern)
    author_name: Optional[str] = Field("anonymous", max_length=40)
    author_color: Optional[str] = "#FF69B4"
    content: str = Field(min_length=1, max_length=1000)

    @field_validator("author_color")
    @classmethod
    def _color(cls, v):
        return v if v and COLOR_RE.match(v) else "#FF69B4"

class VlogPayload(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    video_url: str
    description: Optional[str] = Field(None, max_length=2000)

    @field_validator("video_url")
    @classmethod
    def _video(cls, v):
        v = clean_url(v)
        if not v:
            raise ValueError("needs a YouTube link")
        return v

class ProfilePayload(BaseModel):
    name: Optional[str] = Field(None, max_length=60)
    mood_emoji: Optional[str] = Field(None, max_length=40)
    location: Optional[str] = Field(None, max_length=80)
    zodiac: Optional[str] = Field(None, max_length=40)
    profile_pic_url: Optional[str] = None
    bio: Optional[str] = Field(None, max_length=1500)
    kai_online_status: Optional[bool] = None
    social_instagram: Optional[str] = None
    social_tiktok: Optional[str] = None
    social_spotify: Optional[str] = None

    _urls = field_validator("profile_pic_url", "social_instagram", "social_tiktok", "social_spotify")(clean_url)
    _texts = field_validator("name", "mood_emoji", "location", "zodiac", "bio")(clean_text)

class FactsPayload(BaseModel):
    slot_number: int = Field(ge=1, le=8)
    fact_text: str = Field("", max_length=300)

class LivePayload(BaseModel):
    is_live: bool = False
    stream_url: Optional[str] = None
    offline_image_url: Optional[str] = None

    _offline = field_validator("offline_image_url")(clean_url)

    @field_validator("stream_url")
    @classmethod
    def _stream(cls, v):
        if v and YT_ID_RE.match(v.strip()):
            return v.strip()
        return clean_url(v)

class SignaturePayload(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    message: str = Field(min_length=1, max_length=500)
    visitor_id: Optional[str] = Field(None, max_length=64)

class ChatPayload(BaseModel):
    author_name: str = Field("Anonymous", max_length=40)
    message: str = Field(min_length=1, max_length=300)
    session_id: Optional[str] = Field(None, max_length=64)

class JournalPayload(BaseModel):
    date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    mood: Optional[str] = Field(None, max_length=40)
    title: Optional[str] = Field(None, max_length=150)
    content: str = Field(min_length=1, max_length=20000)

class VotePayload(BaseModel):
    direction: Literal["up", "down"]

class LogPayload(BaseModel):
    level: str = Field(max_length=20)
    message: str = Field(max_length=500)
    details: Optional[dict] = None

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

def page(name: str):
    path = os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        return Response(f"{name} not found", status_code=404, media_type="text/plain")
    return FileResponse(path, media_type="text/html; charset=utf-8")

@app.get("/", include_in_schema=False)
async def serve_frontend():
    return page("index.html")

@app.get("/kai", include_in_schema=False)
async def serve_kai_admin():
    return RedirectResponse("/lounge", status_code=307)

@app.get("/lounge", include_in_schema=False)
async def serve_lounge():
    return page("kais_lounge.html")

@app.get("/xotiic", include_in_schema=False)
async def serve_xotiic_dashboard():
    return page("xotiic.html")

# ══════════════════════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/login")
async def auth_login(payload: LoginPayload, request: Request):
    if login_blocked(request):
        raise HTTPException(status_code=429, detail="Too many tries. Wait 15 minutes and try again.")
    if creds_match(payload.email, payload.password, KAI_EMAIL, KAI_PASSWORD):
        LOGIN_FAILS.pop(client_ip(request), None)
        add_log("success", "Kai login successful")
        return {"access_token": _issue_session("kai"), "token_type": "bearer", "expires_in": 86400}
    await login_failed(request, "Kai")
    raise HTTPException(status_code=401, detail="That email or password isn't right.")

@app.post("/api/xotiic/login")
async def xotiic_login(payload: LoginPayload, request: Request):
    if login_blocked(request):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    if creds_match(payload.email, payload.password, XOTIIC_EMAIL, XOTIIC_PASSWORD):
        LOGIN_FAILS.pop(client_ip(request), None)
        add_log("success", "XOTIIC login successful")
        return {"access_token": _issue_session("xotiic"), "token_type": "bearer", "expires_in": 86400, "role": "owner"}
    await login_failed(request, "XOTIIC")
    raise HTTPException(status_code=401, detail="Invalid XOTIIC credentials")

@app.post("/api/auth/logout")
async def auth_logout(authorization: Optional[str] = Header(None)):
    if authorization:
        _, _, token = authorization.partition(" ")
        AUTH_SESSIONS.pop(token, None)
    return {"ok": True}

@app.get("/api/auth/check")
async def auth_check(authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    return {"ok": True}

# ══════════════════════════════════════════════════════════════════════════════
#  XOTIIC LOGGING ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/xotiic/logs")
async def get_xotiic_logs(authorization: Optional[str] = Header(None)):
    await require_xotiic(authorization)
    return JSONResponse({"logs": LOG_ENTRIES})

@app.post("/api/xotiic/log")
async def add_xotiic_log(payload: LogPayload, authorization: Optional[str] = Header(None)):
    await require_xotiic(authorization)
    add_log(payload.level, payload.message, payload.details)
    return JSONResponse({"ok": True})

@app.get("/api/xotiic/stats")
async def get_xotiic_stats(authorization: Optional[str] = Header(None)):
    await require_xotiic(authorization)

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
#  KAI ONLINE STATUS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/kai/heartbeat")
async def kai_heartbeat(authorization: Optional[str] = Header(None)):
    global KAI_LAST_ACTIVE
    await require_kai(authorization)
    KAI_LAST_ACTIVE = datetime.now(timezone.utc)
    return JSONResponse({"status": "online"})

@app.get("/api/kai/status")
async def kai_status():
    if KAI_LAST_ACTIVE:
        diff = (datetime.now(timezone.utc) - KAI_LAST_ACTIVE).total_seconds()
        if diff < 300:
            return JSONResponse({"online": True, "last_active": KAI_LAST_ACTIVE.isoformat()})
    return JSONResponse({"online": False})

# ══════════════════════════════════════════════════════════════════════════════
#  PROFILE
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/profile")
async def get_profile():
    rows = await sb_get("kai_settings", "select=*&limit=1")
    if isinstance(rows, list) and rows:
        return JSONResponse(rows[0])
    return JSONResponse({})

@app.patch("/api/profile")
async def update_profile(payload: ProfilePayload, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    # Only fields the client actually sent are touched, so a field can be cleared by sending "".
    body = payload.model_dump(exclude_unset=True)
    body["updated_at"] = now_iso()
    rows = await sb_get("kai_settings", "select=id&limit=1")
    if isinstance(rows, list) and rows:
        await sb_patch("kai_settings", f"id=eq.{check_id(str(rows[0]['id']))}", body)
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

@app.patch("/api/threads/{thread_id}")
async def edit_thread(thread_id: str, payload: ThreadPayload, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    await sb_patch("threads", f"id=eq.{check_id(thread_id)}", {
        "content": payload.content,
        "image_url": payload.image_url,
        "video_url": payload.video_url,
    })
    add_log("info", f"Thread edited: {thread_id}")
    return JSONResponse({"ok": True})

@app.delete("/api/threads/{thread_id}")
async def delete_thread(thread_id: str, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    await sb_delete("threads", f"id=eq.{check_id(thread_id)}")
    add_log("success", f"Thread deleted: {thread_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  COMMENTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/comments")
async def get_comments(thread_id: str):
    if not ID_RE.match(thread_id or ""):
        return JSONResponse({"data": []})
    data = await sb_get("comments", f"select=*&thread_id=eq.{thread_id}&order=created_at.asc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.get("/api/comments/recent")
async def recent_comments(authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    data = await sb_get("comments", "select=*&order=created_at.desc&limit=60")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/comments")
async def create_comment(payload: CommentPayload, request: Request):
    rate_limit(request, "comment", 6, 60)
    content = clean_text(payload.content)
    if not content:
        raise HTTPException(status_code=400, detail="Write something first ♡")
    threads = await sb_get("threads", f"select=id,comment_count&id=eq.{payload.thread_id}&limit=1")
    if SUPABASE_URL and not threads:
        raise HTTPException(status_code=404, detail="That thread is gone.")
    if payload.parent_id:
        parent = await sb_get("comments", f"select=id&id=eq.{payload.parent_id}&thread_id=eq.{payload.thread_id}&limit=1")
        if SUPABASE_URL and not parent:
            raise HTTPException(status_code=404, detail="That comment is gone.")
    row = await sb_post("comments", {
        "thread_id": payload.thread_id,
        "parent_id": payload.parent_id,
        "author_name": clean_text(payload.author_name) or "anonymous",
        "author_color": payload.author_color,
        "content": content,
        "upvotes": 0,
        "downvotes": 0,
    })
    if threads:
        cnt = (threads[0].get("comment_count") or 0) + 1
        await sb_patch("threads", f"id=eq.{payload.thread_id}", {"comment_count": cnt})
    add_log("info", f"Comment added to thread {payload.thread_id}")
    return JSONResponse({"ok": True, "data": row})

@app.post("/api/comments/{comment_id}/vote")
async def vote_comment(comment_id: str, payload: VotePayload, request: Request):
    check_id(comment_id)
    rate_limit(request, "vote", 30, 60)
    rows = await sb_get("comments", f"select=upvotes,downvotes&id=eq.{comment_id}")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Comment not found")
    row = rows[0]
    # One vote per comment per visitor per day; extra clicks are ignored quietly.
    if not hit(f"vote1:{client_ip(request)}:{comment_id}", 1, 86400):
        return JSONResponse({"ok": True, "counted": False})
    if payload.direction == "up":
        await sb_patch("comments", f"id=eq.{comment_id}", {"upvotes": (row.get("upvotes") or 0) + 1})
    else:
        await sb_patch("comments", f"id=eq.{comment_id}", {"downvotes": (row.get("downvotes") or 0) + 1})
    return JSONResponse({"ok": True, "counted": True})

@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: str, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    await sb_delete("comments", f"id=eq.{check_id(comment_id)}")
    add_log("success", f"Comment deleted: {comment_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  POSTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/posts")
async def get_posts():
    data = await sb_get("posts", "select=*&order=created_at.desc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/posts")
async def create_post(payload: PostPayload, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    if not (payload.caption or payload.image_url or payload.video_url):
        raise HTTPException(status_code=400, detail="Add a photo, video or caption first.")
    row = await sb_post("posts", {
        "caption": payload.caption,
        "image_url": payload.image_url,
        "video_url": payload.video_url,
        "like_count": 0,
    })
    add_log("info", f"Post created: {payload.caption[:50] if payload.caption else 'No caption'}...")
    return JSONResponse({"ok": True, "data": row})

@app.post("/api/posts/{post_id}/like")
async def like_post(post_id: str, request: Request):
    check_id(post_id)
    rate_limit(request, "like", 30, 60)
    rows = await sb_get("posts", f"select=like_count&id=eq.{post_id}")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=404, detail="Post not found")
    count = rows[0].get("like_count") or 0
    if not hit(f"like1:{client_ip(request)}:{post_id}", 1, 86400):
        return JSONResponse({"ok": True, "like_count": count, "counted": False})
    count += 1
    await sb_patch("posts", f"id=eq.{post_id}", {"like_count": count})
    return JSONResponse({"ok": True, "like_count": count, "counted": True})

@app.delete("/api/posts/{post_id}")
async def delete_post(post_id: str, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    await sb_delete("posts", f"id=eq.{check_id(post_id)}")
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
        "title": payload.title.strip(),
        "video_url": payload.video_url,
        "description": clean_text(payload.description),
    })
    add_log("info", f"Vlog created: {payload.title}")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/vlogs/{vlog_id}")
async def delete_vlog(vlog_id: str, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    await sb_delete("vlogs", f"id=eq.{check_id(vlog_id)}")
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
    await require_kai_or_xotiic(authorization)
    if len(payload) > 8:
        raise HTTPException(status_code=400, detail="Only 8 facts fit on the page.")
    for item in payload:
        slot = item.slot_number
        text = clean_text(item.fact_text)
        if not text:
            await sb_delete("fun_facts", f"slot_number=eq.{slot}")
            continue
        existing = await sb_get("fun_facts", f"select=id&slot_number=eq.{slot}&limit=1")
        if isinstance(existing, list) and existing:
            await sb_patch("fun_facts", f"slot_number=eq.{slot}", {"fact_text": text, "updated_at": now_iso()})
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
    row = rows[0] if isinstance(rows, list) and rows else {"is_live": False, "stream_url": None, "offline_image_url": None}
    return JSONResponse({**row, "camera": camera_state()})

@app.patch("/api/live")
async def update_live(payload: LivePayload, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    body = {
        "is_live": payload.is_live,
        "stream_url": payload.stream_url,
        "offline_image_url": payload.offline_image_url,
        "updated_at": now_iso(),
    }
    rows = await sb_get("live_status", "select=id&limit=1")
    if isinstance(rows, list) and rows:
        await sb_patch("live_status", f"id=eq.{check_id(str(rows[0]['id']))}", body)
    else:
        await sb_post("live_status", body)
    add_log("success", f"Live status updated: {'LIVE' if payload.is_live else 'OFFLINE'}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  CHAT
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/chat")
async def get_chat():
    data = await sb_get("chat_messages", "select=*&order=created_at.desc&limit=50")
    data = list(reversed(data)) if isinstance(data, list) else []
    return JSONResponse({"data": data})

@app.post("/api/chat")
async def post_chat(payload: ChatPayload, request: Request):
    rate_limit(request, "chat", 12, 60)
    message = clean_text(payload.message)
    if not message:
        raise HTTPException(status_code=400, detail="Say something first ♡")
    row = await sb_post("chat_messages", {
        "author_name": clean_text(payload.author_name) or "Anonymous",
        "message": message,
        "session_id": payload.session_id,
    })
    add_log("info", f"Chat message from {payload.author_name}")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/chat")
async def clear_chat(authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    if SUPABASE_URL:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.delete(
                f"{SUPABASE_URL}/rest/v1/chat_messages?id=neq.00000000-0000-0000-0000-000000000000",
                headers=sb_headers()
            )
    add_log("warning", "Chat history cleared")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  SIGNATURES (guestbook)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/signatures")
async def get_signatures():
    data = await sb_get("iwashere_signatures", "select=*&order=created_at.desc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/signatures")
async def create_signature(payload: SignaturePayload, request: Request):
    rate_limit(request, "signature", 3, 600)
    name, message = clean_text(payload.name), clean_text(payload.message)
    if not name or not message:
        raise HTTPException(status_code=400, detail="Fill in your name and a message ♡")
    row = await sb_post("iwashere_signatures", {
        "name": name,
        "message": message,
        "visitor_id": payload.visitor_id,
    })
    add_log("info", f"New signature from {name}")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/signatures/{sig_id}")
async def delete_signature(sig_id: str, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    await sb_delete("iwashere_signatures", f"id=eq.{check_id(sig_id)}")
    add_log("success", f"Signature deleted: {sig_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  JOURNAL (private)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/journal")
async def get_journal(authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    data = await sb_get("journal_entries", "select=*&order=created_at.desc")
    return JSONResponse({"data": data if isinstance(data, list) else []})

@app.post("/api/journal")
async def create_journal(payload: JournalPayload, authorization: Optional[str] = Header(None)):
    await require_kai(authorization)
    row = await sb_post("journal_entries", {
        "date": payload.date,
        "mood": clean_text(payload.mood),
        "title": clean_text(payload.title),
        "content": payload.content,
    })
    add_log("info", "Journal entry created")
    return JSONResponse({"ok": True, "data": row})

@app.delete("/api/journal/{entry_id}")
async def delete_journal(entry_id: str, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    await sb_delete("journal_entries", f"id=eq.{check_id(entry_id)}")
    add_log("success", f"Journal entry deleted: {entry_id}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  VISITOR TRACKING & STATS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/track")
async def track_visit(payload: TrackPayload, request: Request):
    if not SUPABASE_URL or not hit(f"track:{client_ip(request)}", 60, 60):
        return JSONResponse({"ok": True})
    await sb_post("visitors", {
        "visitor_id": payload.visitor_id[:64],
        "page": (payload.page or "home")[:64],
        "referrer": (payload.referrer or "")[:200] or None,
        "ua": (payload.ua or "")[:300] or None,
        "ip": client_ip(request),
    })
    return JSONResponse({"ok": True})

STATS_CACHE = {"at": 0.0, "data": None}

@app.get("/api/stats")
async def get_stats():
    if not SUPABASE_URL:
        return JSONResponse({"total": 0, "today": 0, "active": 0, "threads": 0, "comments": 0, "vlogs": 0, "signatures": 0, "posts": 0, "pages": []})
    if STATS_CACHE["data"] and time.monotonic() - STATS_CACHE["at"] < 60:
        return JSONResponse(STATS_CACHE["data"])

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
    pages = [{"label": k, "count": v} for k, v in sorted(page_counts.items(), key=lambda x: -x[1])][:10]

    def cnt(d): return len(d) if isinstance(d, list) else 0

    data = {
        "total": unique, "today": today_count, "active": active,
        "threads": cnt(threads_data), "comments": cnt(comments_data),
        "vlogs": cnt(vlogs_data), "signatures": cnt(sigs_data),
        "posts": cnt(posts_data), "pages": pages,
    }
    STATS_CACHE.update(at=time.monotonic(), data=data)
    return JSONResponse(data)

# ══════════════════════════════════════════════════════════════════════════════
#  PET SPRITES
#  Sprites live in the Supabase table `pet_sprites` (see supabase_setup.sql) so
#  they survive restarts. If that table doesn't exist yet they fall back to the
#  local sprites/ folder, which Render wipes on every redeploy.
#  A sprite is either an animated GIF or a PNG sprite sheet: `frames` equal-width
#  frames laid out left to right.
# ══════════════════════════════════════════════════════════════════════════════

SPRITE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
SPRITE_EXTS = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".jpg": "image/jpeg"}
MIME_EXT = {v: k for k, v in SPRITE_EXTS.items()}
MAX_SPRITE_BYTES = 3 * 1024 * 1024
MAX_SPRITES = 40
DEFAULT_MOTION = {"bird": "fly", "bat": "fly", "cat": "walk", "dog": "walk"}
SPRITE_IMG_CACHE: dict = {}

class SpriteMeta(BaseModel):
    frames: int = Field(1, ge=1, le=24)
    fps: int = Field(6, ge=1, le=24)
    motion: Literal["walk", "hop", "fly", "float", "follow"] = "walk"
    speed: Literal["slow", "normal", "fast"] = "normal"
    size: Literal["s", "m", "l", "xl"] = "m"

class SpriteSavePayload(SpriteMeta):
    name: str = Field(pattern=SPRITE_NAME_RE.pattern)
    image: str = Field(max_length=5 * 1024 * 1024)

def sniff_image(data: bytes):
    """Return (mime, width, height) from the file's magic bytes, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return "image/png", w, h
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return "image/gif", w, h
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", 0, 0
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", 0, 0
    return None

def sheet_frames(mime: str, w: int, h: int, frames: int) -> int:
    """Work out (or check) how many frames a sprite sheet holds. 0 means auto-detect."""
    if mime != "image/png" or not w or not h:
        return 1
    if w > 4096 or h > 1024:
        raise HTTPException(status_code=400, detail="That image is too big for a pet (max 4096×1024).")
    if frames <= 0:
        return w // h if w > h and w % h == 0 and w // h <= 24 else 1
    if w % frames:
        raise HTTPException(status_code=400, detail=f"The sheet is {w}px wide, which doesn't split into {frames} equal frames.")
    return frames

def default_meta(name: str) -> dict:
    return SpriteMeta(motion=DEFAULT_MOTION.get(name, "walk")).model_dump()

def disk_sprites() -> dict:
    found = {}
    for fname in sorted(os.listdir(SPRITES_DIR)):
        stem, ext = os.path.splitext(fname)
        ext = ext.lower()
        if ext not in SPRITE_EXTS or not SPRITE_NAME_RE.match(stem):
            continue
        path = os.path.join(SPRITES_DIR, fname)
        meta = default_meta(stem)
        sidecar = os.path.join(SPRITES_DIR, stem + ".json")
        if os.path.exists(sidecar):
            try:
                with open(sidecar, "r", encoding="utf-8") as f:
                    meta = SpriteMeta(**json.load(f)).model_dump()
            except (OSError, ValueError):
                pass
        found[stem] = {"file": fname, "path": path, "mime": SPRITE_EXTS[ext],
                       "version": str(int(os.path.getmtime(path))), **meta}
    return found

def remove_disk_sprite(name: str):
    for fname in os.listdir(SPRITES_DIR):
        stem, ext = os.path.splitext(fname)
        if stem == name and (ext.lower() in SPRITE_EXTS or ext.lower() == ".json"):
            os.remove(os.path.join(SPRITES_DIR, fname))

async def sprite_db(method: str, query: str = "", body=None, prefer: Optional[str] = None):
    """Talk to the pet_sprites table. Returns None when the table isn't usable."""
    if not SUPABASE_URL:
        return None
    headers = sb_headers()
    if prefer:
        headers["Prefer"] = prefer
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.request(method, f"{SUPABASE_URL}/rest/v1/pet_sprites?{query}", headers=headers, json=body)
    except httpx.HTTPError:
        return None
    return r if r.is_success else None

def sprite_item(name: str, url: str, mime: str, meta: dict) -> dict:
    kind = "gif" if mime == "image/gif" else "sheet"
    item = {"name": name, "url": url, "kind": kind, **{k: meta[k] for k in ("frames", "fps", "motion", "speed", "size")}}
    if kind == "gif":
        item["frames"] = 1
    return item

async def all_sprites():
    """Every sprite by name (database wins over disk) and which storage is active."""
    items = {}
    for name, d in disk_sprites().items():
        items[name] = sprite_item(name, f"/sprites/{d['file']}?v={d['version']}", d["mime"], d)
    r = await sprite_db("GET", "select=name,mime,frames,fps,motion,speed,size,updated_at&order=name.asc")
    if r is not None:
        for row in r.json():
            name = row.get("name", "")
            if not SPRITE_NAME_RE.match(name):
                continue
            version = re.sub(r"\D", "", str(row.get("updated_at", "")))[:20]
            meta = {**default_meta(name), **{k: row[k] for k in ("frames", "fps", "motion", "speed", "size") if row.get(k) is not None}}
            items[name] = sprite_item(name, f"/api/sprites/img/{name}?v={version}", row.get("mime", "image/png"), meta)
    return items, ("database" if r is not None else "server-disk")

async def store_sprite(name: str, data: bytes, mime: str, meta: dict) -> str:
    items, _ = await all_sprites()
    if name not in items and len(items) >= MAX_SPRITES:
        raise HTTPException(status_code=400, detail=f"You already have {MAX_SPRITES} pets. Delete one to make room.")
    row = {"name": name, "image": base64.b64encode(data).decode(), "mime": mime, **meta, "updated_at": now_iso()}
    saved = await sprite_db("POST", "on_conflict=name", row, prefer="resolution=merge-duplicates,return=minimal")
    SPRITE_IMG_CACHE.pop(name, None)
    remove_disk_sprite(name)
    if saved is not None:
        return "database"
    with open(os.path.join(SPRITES_DIR, name + MIME_EXT[mime]), "wb") as f:
        f.write(data)
    with open(os.path.join(SPRITES_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return "server-disk"

@app.get("/api/sprites/list")
async def list_sprites():
    items, storage = await all_sprites()
    return JSONResponse({
        "sprites": {name: item["url"] for name, item in items.items()},
        "items": list(items.values()),
        "storage": storage,
    })

@app.get("/api/sprites/img/{name}")
async def sprite_image(name: str, v: str = ""):
    if not SPRITE_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="No such pet")
    cache_headers = {"Cache-Control": "public, max-age=31536000, immutable" if v else "no-cache"}
    cached = SPRITE_IMG_CACHE.get(name)
    if cached and cached[0] == v:
        return Response(cached[2], media_type=cached[1], headers=cache_headers)
    r = await sprite_db("GET", f"select=image,mime&name=eq.{name}&limit=1")
    rows = r.json() if r is not None else []
    if not rows:
        d = disk_sprites().get(name)
        if d:
            return FileResponse(d["path"], media_type=d["mime"])
        raise HTTPException(status_code=404, detail="No such pet")
    mime = rows[0].get("mime") or "image/png"
    data = base64.b64decode(rows[0].get("image") or "")
    if len(SPRITE_IMG_CACHE) > 60:
        SPRITE_IMG_CACHE.clear()
    SPRITE_IMG_CACHE[name] = (v, mime, data)
    return Response(data, media_type=mime, headers=cache_headers)

@app.post("/api/sprites/save")
async def save_sprite(payload: SpriteSavePayload, authorization: Optional[str] = Header(None)):
    """Save a sprite sheet drawn in the Sprite Studio (a PNG data URL)."""
    await require_kai_or_xotiic(authorization)
    prefix = "data:image/png;base64,"
    if not payload.image.startswith(prefix):
        raise HTTPException(status_code=400, detail="The drawing didn't come through as a PNG.")
    try:
        data = base64.b64decode(payload.image[len(prefix):], validate=True)
    except ValueError:
        raise HTTPException(status_code=400, detail="The drawing data is damaged. Try saving again.")
    info = sniff_image(data)
    if not info or info[0] != "image/png":
        raise HTTPException(status_code=400, detail="The drawing didn't come through as a PNG.")
    if len(data) > MAX_SPRITE_BYTES:
        raise HTTPException(status_code=413, detail="That pet is too big (max 3MB).")
    meta = payload.model_dump(exclude={"name", "image"})
    meta["frames"] = sheet_frames(*info, payload.frames)
    storage = await store_sprite(payload.name, data, "image/png", meta)
    add_log("info", f"Sprite saved from studio: {payload.name}")
    return JSONResponse({"ok": True, "name": payload.name, "storage": storage})

@app.post("/api/sprites/upload")
async def upload_sprite(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    sprite_type: Optional[str] = Form(None),
    frames: int = Form(0),
    fps: int = Form(6),
    motion: Optional[str] = Form(None),
    speed: str = Form("normal"),
    size: str = Form("m"),
    authorization: Optional[str] = Header(None),
):
    await require_kai_or_xotiic(authorization)
    name = (name or sprite_type or "").strip().lower()
    if not SPRITE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Give the pet a short name: letters, numbers, - or _ (max 24).")
    chunks, total = [], 0
    while chunk := await file.read(256 * 1024):
        total += len(chunk)
        if total > MAX_SPRITE_BYTES:
            raise HTTPException(status_code=413, detail="That file is too big (max 3MB).")
        chunks.append(chunk)
    data = b"".join(chunks)
    info = sniff_image(data)
    if not info:
        raise HTTPException(status_code=400, detail="That file isn't a PNG, GIF, WebP or JPEG.")
    try:
        meta = SpriteMeta(frames=max(frames, 1), fps=fps, motion=motion or DEFAULT_MOTION.get(name, "walk"),
                          speed=speed, size=size).model_dump()
    except ValueError:
        raise HTTPException(status_code=400, detail="One of the pet settings isn't valid.")
    meta["frames"] = sheet_frames(*info, frames)
    storage = await store_sprite(name, data, info[0], meta)
    add_log("info", f"Sprite uploaded: {name}")
    items, _ = await all_sprites()
    return JSONResponse({"ok": True, "name": name, "url": items.get(name, {}).get("url"), "storage": storage})

@app.patch("/api/sprites/{name}")
async def update_sprite_settings(name: str, payload: SpriteMeta, authorization: Optional[str] = Header(None)):
    """Change how a pet moves without re-uploading the picture."""
    await require_kai_or_xotiic(authorization)
    if not SPRITE_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="No such pet")
    items, _ = await all_sprites()
    if name not in items:
        raise HTTPException(status_code=404, detail="No such pet")
    meta = payload.model_dump(exclude={"frames"})
    updated = await sprite_db("PATCH", f"name=eq.{name}", {**meta, "updated_at": now_iso()}, prefer="return=representation")
    if updated is None or not updated.json():
        d = disk_sprites().get(name)
        if not d:
            raise HTTPException(status_code=404, detail="No such pet")
        with open(os.path.join(SPRITES_DIR, name + ".json"), "w", encoding="utf-8") as f:
            json.dump({**meta, "frames": d["frames"]}, f)
    add_log("info", f"Sprite settings changed: {name}")
    return JSONResponse({"ok": True})

@app.delete("/api/sprites/{name}")
async def delete_sprite(name: str, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    if not SPRITE_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="No such pet")
    await sprite_db("DELETE", f"name=eq.{name}")
    SPRITE_IMG_CACHE.pop(name, None)
    remove_disk_sprite(name)
    add_log("info", f"Sprite deleted: {name}")
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  MEDIA UPLOADS (photos & videos for the feed and vlog)
#  Files go to the public Supabase Storage bucket `media` (see supabase_setup.sql).
#  The browser sends the raw file as the request body.
# ══════════════════════════════════════════════════════════════════════════════

MEDIA_BUCKET = os.getenv("MEDIA_BUCKET", "media")
MEDIA_KINDS = {"feed", "vlog", "thread", "profile", "live"}

def sniff_media(data: bytes):
    """Return (mime, ext) for the photo and video formats browsers can show, or None."""
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc", b"avif"):
            return None
        return ("video/quicktime", "mov") if brand.startswith(b"qt") else ("video/mp4", "mp4")
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm", "webm"
    info = sniff_image(data)
    if info:
        return info[0], MIME_EXT[info[0]].lstrip(".")
    return None

@app.post("/api/media/upload")
async def upload_media(request: Request, kind: str = "feed", authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    if kind not in MEDIA_KINDS:
        raise HTTPException(status_code=400, detail="Unknown upload kind.")
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Uploads need Supabase to be set up.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_MEDIA_BYTES:
            raise HTTPException(status_code=413, detail="That file is over 50MB. Try a shorter or smaller video.")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="The file came through empty. Try again.")
    found = sniff_media(data)
    if not found:
        raise HTTPException(status_code=400, detail="That file type won't play in browsers. Use MP4, MOV, WebM, JPG, PNG, GIF or WebP (iPhone HEIC photos need to be saved as JPG).")
    mime, ext = found
    path = f"{kind}/{datetime.now(timezone.utc):%Y%m}/{uuid.uuid4().hex}.{ext}"
    headers = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
               "Content-Type": mime, "x-upsert": "false", "cache-control": "max-age=31536000"}
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SUPABASE_URL}/storage/v1/object/{MEDIA_BUCKET}/{path}", headers=headers, content=data)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Couldn't reach Supabase Storage. Try again in a minute.")
    if not r.is_success:
        add_log("error", f"Media upload failed: {r.status_code} {r.text[:200]}")
        if "not found" in r.text.lower():
            raise HTTPException(status_code=502, detail="The 'media' storage bucket doesn't exist yet. Run supabase_setup.sql in Supabase.")
        raise HTTPException(status_code=502, detail="Supabase didn't accept the upload. Check SUPABASE_SERVICE_KEY is set on Render.")
    url = f"{SUPABASE_URL}/storage/v1/object/public/{MEDIA_BUCKET}/{path}"
    add_log("info", f"Media uploaded ({kind}, {mime}, {total // 1024}KB)")
    return JSONResponse({"ok": True, "url": url, "type": "video" if mime.startswith("video") else "image"})

# ══════════════════════════════════════════════════════════════════════════════
#  LIVE FROM THE WEBSITE (camera / screen)
#  Kai's browser records short clips (a few seconds each, every clip a complete
#  video file) and posts them here; viewers fetch them in order and play them
#  back to back. Clips are kept in memory, so this needs a single server process.
# ══════════════════════════════════════════════════════════════════════════════

CAMERA = {"session": None, "mime": None, "seq": 0, "segments": deque(maxlen=10), "last": 0.0}
CAMERA_TIMEOUT = 20
CAMERA_MIME_RE = re.compile(r'^video/(webm|mp4)(;\s*codecs="?[A-Za-z0-9.,\s]+"?)?$')
MAX_SEGMENT_BYTES = 5 * 1024 * 1024

class CameraStartPayload(BaseModel):
    mime: str = Field(max_length=100)

def camera_live() -> bool:
    return bool(CAMERA["session"]) and time.monotonic() - CAMERA["last"] < CAMERA_TIMEOUT

def camera_state() -> dict:
    if not camera_live():
        return {"live": False}
    segs = CAMERA["segments"]
    return {"live": True, "session": CAMERA["session"], "mime": CAMERA["mime"],
            "oldest": segs[0][0] if segs else 0, "latest": segs[-1][0] if segs else 0}

@app.post("/api/live/camera/start")
async def camera_start(payload: CameraStartPayload, authorization: Optional[str] = Header(None)):
    global KAI_LAST_ACTIVE
    await require_kai_or_xotiic(authorization)
    if not CAMERA_MIME_RE.match(payload.mime):
        raise HTTPException(status_code=400, detail="This browser records in a format viewers can't play. Try Chrome.")
    CAMERA["segments"].clear()
    CAMERA.update(session=uuid.uuid4().hex[:16], mime=payload.mime, seq=0, last=time.monotonic())
    KAI_LAST_ACTIVE = datetime.now(timezone.utc)
    add_log("success", "Went live from the website")
    return JSONResponse({"ok": True, "session": CAMERA["session"]})

@app.post("/api/live/camera/segment")
async def camera_segment(request: Request, session: str, authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    if not CAMERA["session"] or session != CAMERA["session"]:
        raise HTTPException(status_code=409, detail="This stream was ended. Start it again.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_SEGMENT_BYTES:
            raise HTTPException(status_code=413, detail="Video clip too big.")
        chunks.append(chunk)
    if not total:
        return JSONResponse({"ok": True})
    CAMERA["seq"] += 1
    CAMERA["segments"].append((CAMERA["seq"], b"".join(chunks)))
    CAMERA["last"] = time.monotonic()
    return JSONResponse({"ok": True, "seq": CAMERA["seq"]})

@app.post("/api/live/camera/stop")
async def camera_stop(authorization: Optional[str] = Header(None)):
    await require_kai_or_xotiic(authorization)
    CAMERA.update(session=None, mime=None)
    CAMERA["segments"].clear()
    add_log("info", "Website live stream ended")
    return JSONResponse({"ok": True})

@app.get("/api/live/camera")
async def camera_status():
    return JSONResponse(camera_state())

@app.get("/api/live/camera/{session}/{seq}")
async def camera_clip(session: str, seq: int):
    if not camera_live() or session != CAMERA["session"]:
        raise HTTPException(status_code=404, detail="Stream ended")
    for n, data in CAMERA["segments"]:
        if n == seq:
            return Response(data, media_type=CAMERA["mime"].split(";")[0], headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="Clip not available")

# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "timestamp": now_iso()})

# ─── Dev server ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=True)
