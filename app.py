import os
import io
import uuid
import mimetypes
from datetime import datetime, timedelta, timezone
from typing import Optional

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
FRONTEND_URL         = os.getenv("FRONTEND_URL", "*")
PORT                 = int(os.getenv("PORT", "8000"))

# Track Kai's online status (heartbeat)
KAI_LAST_ACTIVE = None

print(f"🔧 Environment check:")
print(f"  SUPABASE_URL: {SUPABASE_URL[:30] if SUPABASE_URL else 'NOT SET'}...")
print(f"  SUPABASE_ANON_KEY: {SUPABASE_ANON_KEY[:20] if SUPABASE_ANON_KEY else 'NOT SET'}...")
print(f"  KAI_EMAIL: {KAI_EMAIL}")
print(f"  PORT: {PORT}")

# ─── Sprite storage directory ──────────────────────────────────────────────────
SPRITES_DIR = os.path.join(os.path.dirname(__file__), "sprites")
os.makedirs(SPRITES_DIR, exist_ok=True)

# ─── Load frontend HTML files ─────────────────────────────────────────────────
_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
try:
    with open(_HTML_PATH, "r", encoding="utf-8") as _f:
        _HTML_TEMPLATE = _f.read()
    print(f"✓ Loaded index.html ({len(_HTML_TEMPLATE)} chars)")
except FileNotFoundError:
    _HTML_TEMPLATE = """<!DOCTYPE html><html><head><title>KAICORE</title></head><body><h1>KAICORE</h1><p>index.html not found</p></body></html>"""
    print("✗ index.html NOT FOUND")

_LOUNGE_PATH = os.path.join(os.path.dirname(__file__), "kais_lounge.html")
try:
    with open(_LOUNGE_PATH, "r", encoding="utf-8") as _f:
        _LOUNGE_TEMPLATE = _f.read()
    print(f"✓ Loaded kais_lounge.html ({len(_LOUNGE_TEMPLATE)} chars)")
except FileNotFoundError:
    _LOUNGE_TEMPLATE = """<!DOCTYPE html><html><head><title>KAI'S LOUNGE</title></head><body><h1>KAI'S LOUNGE</h1><p>kais_lounge.html not found</p></body></html>"""
    print("✗ kais_lounge.html NOT FOUND")

def inject_config(html: str, admin: bool = False) -> str:
    """Inject real Supabase credentials into an HTML template."""
    html = html.replace("'SUPABASE_URL'", f"'{SUPABASE_URL}'")
    html = html.replace('"SUPABASE_URL"', f'"{SUPABASE_URL}"')
    html = html.replace("'SUPABASE_ANON_KEY'", f"'{SUPABASE_ANON_KEY}'")
    html = html.replace('"SUPABASE_ANON_KEY"', f'"{SUPABASE_ANON_KEY}"')
    html = html.replace("'SUPABASE_SERVICE_KEY'", f"'{SUPABASE_SERVICE_KEY}'")
    html = html.replace('"SUPABASE_SERVICE_KEY"', f'"{SUPABASE_SERVICE_KEY}"')
    
    if admin:
        html = html.replace("</body>", "<script>window.__KAI_ADMIN__ = true;</script>\n</body>")
    
    return html

# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="KAICORE API",
    description="Backend for KAICORE — Kai's personal digital space",
    version="3.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve sprites as static files
app.mount("/sprites", StaticFiles(directory=SPRITES_DIR), name="sprites")

# ─── Models ────────────────────────────────────────────────────────────────────
class TrackPayload(BaseModel):
    visitor_id: str
    page: str = "home"
    referrer: Optional[str] = None
    ua: Optional[str] = None

class LoginPayload(BaseModel):
    email: str
    password: str

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    """Serve the main KAICORE frontend."""
    return HTMLResponse(content=inject_config(_HTML_TEMPLATE, admin=False))

@app.get("/kai", response_class=HTMLResponse, include_in_schema=False)
async def serve_kai_admin():
    """Kai's private admin entry point."""
    return HTMLResponse(content=inject_config(_HTML_TEMPLATE, admin=True))

@app.get("/lounge", response_class=HTMLResponse, include_in_schema=False)
async def serve_kais_lounge():
    """Kai's Lounge — full standalone admin panel."""
    return HTMLResponse(content=inject_config(_LOUNGE_TEMPLATE, admin=False))

# ══════════════════════════════════════════════════════════════════════════════
#  AUTHENTICATION
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/login")
async def auth_login(payload: LoginPayload):
    """Login using Render env credentials."""
    if payload.email == KAI_EMAIL and payload.password == KAI_PASSWORD:
        return {
            "access_token": "kai_authenticated",
            "token_type": "bearer",
            "expires_in": 86400
        }
    raise HTTPException(status_code=401, detail="Invalid credentials")

async def verify_kai_token(authorization: Optional[str] = Header(None)) -> bool:
    """Verify if request is from authenticated Kai."""
    return authorization is not None and authorization == "Bearer kai_authenticated"

# ══════════════════════════════════════════════════════════════════════════════
#  KAI ONLINE STATUS (Heartbeat)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/kai/heartbeat")
async def kai_heartbeat(authorization: Optional[str] = Header(None)):
    """Track when Kai is active in the lounge."""
    global KAI_LAST_ACTIVE
    if not await verify_kai_token(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    KAI_LAST_ACTIVE = datetime.now(timezone.utc)
    return JSONResponse({"status": "online"})

@app.get("/api/kai/status")
async def kai_status():
    """Check if Kai is currently online (active in last 5 minutes)."""
    global KAI_LAST_ACTIVE
    if KAI_LAST_ACTIVE and (datetime.now(timezone.utc) - KAI_LAST_ACTIVE).seconds < 300:
        return JSONResponse({"online": True, "last_active": KAI_LAST_ACTIVE.isoformat()})
    return JSONResponse({"online": False})

# ══════════════════════════════════════════════════════════════════════════════
#  SUPABASE PROXY (for lounge page)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/supabase/{table}")
async def supabase_proxy(table: str, request: Request, authorization: Optional[str] = Header(None)):
    """Proxy for Supabase queries (authenticated)."""
    if not await verify_kai_token(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if not SUPABASE_URL:
        return JSONResponse({"data": [], "error": "Supabase not configured"})
    
    try:
        body = await request.json()
    except:
        body = {}
    
    method = body.get('method', 'GET')
    query_body = body.get('body')
    select = body.get('select', '*')
    filter_param = body.get('filter', None)
    
    key = SUPABASE_SERVICE_KEY
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        if method == 'GET':
            url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}"
            if filter_param:
                url += f"&{filter_param}"
            resp = await client.get(url, headers=headers)
            
        elif method == 'POST':
            url = f"{SUPABASE_URL}/rest/v1/{table}"
            resp = await client.post(url, headers=headers, json=query_body)
            
        elif method == 'PATCH':
            if not filter_param:
                return JSONResponse({"data": [], "error": "PATCH requires filter"})
            url = f"{SUPABASE_URL}/rest/v1/{table}?{filter_param}"
            resp = await client.patch(url, headers=headers, json=query_body)
            
        elif method == 'DELETE':
            if not filter_param:
                return JSONResponse({"data": [], "error": "DELETE requires filter"})
            url = f"{SUPABASE_URL}/rest/v1/{table}?{filter_param}"
            resp = await client.delete(url, headers=headers)
            
        else:
            return JSONResponse({"data": [], "error": f"Invalid method: {method}"})
    
    try:
        data = resp.json()
    except:
        data = []
    
    return JSONResponse({
        "data": data if isinstance(data, list) else ([data] if data else []),
        "error": None if resp.is_success else str(data)
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
    """Upload a sprite sheet directly to the server."""
    if not await verify_kai_token(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if sprite_type not in ALLOWED_SPRITE_TYPES:
        raise HTTPException(status_code=400, detail=f"sprite_type must be one of {ALLOWED_SPRITE_TYPES}")

    content_type = file.content_type or "image/png"
    if content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail="Only PNG, GIF, WebP, JPEG allowed")

    ext = content_type.split("/")[-1].replace("jpeg", "jpg")
    filename = f"{sprite_type}.{ext}"
    dest = os.path.join(SPRITES_DIR, filename)

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")

    with open(dest, "wb") as f:
        f.write(data)

    sprite_url = f"/sprites/{filename}"
    print(f"✓ Sprite uploaded: {filename} ({len(data)} bytes)")

    return JSONResponse({"ok": True, "url": sprite_url, "filename": filename})

@app.get("/api/sprites/list")
async def list_sprites():
    """List all uploaded sprites and their URLs."""
    sprites = {}
    if os.path.exists(SPRITES_DIR):
        for fname in os.listdir(SPRITES_DIR):
            for stype in ALLOWED_SPRITE_TYPES:
                if fname.startswith(stype + "."):
                    sprites[stype] = f"/sprites/{fname}"
    return JSONResponse({"sprites": sprites})

# ══════════════════════════════════════════════════════════════════════════════
#  VISITOR TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/track")
async def track_visit(payload: TrackPayload, request: Request):
    """Log a page view."""
    if not SUPABASE_URL:
        return JSONResponse({"ok": True, "note": "Supabase not configured"})

    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)
    
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/visitors",
            headers=headers,
            json={
                "visitor_id": payload.visitor_id[:64],
                "page": (payload.page or "home")[:64],
                "referrer": (payload.referrer or "")[:200] or None,
                "ua": (payload.ua or "")[:300] or None,
                "ip": (ip or "")[:64] or None,
            }
        )
    
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
#  STATS (Kai only)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def get_stats(authorization: Optional[str] = Header(None)):
    """Return visitor + content stats. Requires Kai's auth token."""
    if not await verify_kai_token(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not SUPABASE_URL:
        return JSONResponse({
            "total": 0, "today": 0, "active": 0,
            "threads": 0, "comments": 0, "vlogs": 0, "signatures": 0,
            "pages": []
        })

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
    }
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    
    async with httpx.AsyncClient(timeout=15) as client:
        # Get visitors
        v_resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/visitors?select=visitor_id,page,created_at",
            headers=headers
        )
        visitors = v_resp.json() if v_resp.is_success else []
        if not isinstance(visitors, list):
            visitors = []
        
        # Get counts
        threads_resp = await client.get(f"{SUPABASE_URL}/rest/v1/threads?select=id", headers=headers)
        comments_resp = await client.get(f"{SUPABASE_URL}/rest/v1/comments?select=id", headers=headers)
        vlogs_resp = await client.get(f"{SUPABASE_URL}/rest/v1/vlogs?select=id", headers=headers)
        sigs_resp = await client.get(f"{SUPABASE_URL}/rest/v1/iwashere_signatures?select=id", headers=headers)
    
    unique_visitors = len(set(v.get("visitor_id", "") for v in visitors))
    today_visits = sum(1 for v in visitors if (v.get("created_at") or "").startswith(today))
    active_count = sum(1 for v in visitors if (v.get("created_at") or "") >= five_min_ago)
    
    page_counts = {}
    for v in visitors:
        pg = v.get("page") or "home"
        page_counts[pg] = page_counts.get(pg, 0) + 1
    pages_sorted = [{"label": pg, "count": ct} for pg, ct in sorted(page_counts.items(), key=lambda x: -x[1])][:10]
    
    def get_count(resp):
        if resp.is_success:
            data = resp.json()
            return len(data) if isinstance(data, list) else 0
        return 0
    
    return JSONResponse({
        "total": unique_visitors,
        "today": today_visits,
        "active": active_count,
        "threads": get_count(threads_resp),
        "comments": get_count(comments_resp),
        "vlogs": get_count(vlogs_resp),
        "signatures": get_count(sigs_resp),
        "pages": pages_sorted,
    })

# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH & CONFIG
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    sprite_files = os.listdir(SPRITES_DIR) if os.path.exists(SPRITES_DIR) else []
    return JSONResponse({
        "status": "ok",
        "service": "KAICORE",
        "version": "3.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "supabase": bool(SUPABASE_URL),
        "sprites": sprite_files,
    })

@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "supabase_url": SUPABASE_URL,
        "supabase_anon": SUPABASE_ANON_KEY,
    })

@app.get("/debug/env")
async def debug_env():
    """Debug endpoint to verify environment variables."""
    return {
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_anon_key_set": bool(SUPABASE_ANON_KEY),
        "kai_email": KAI_EMAIL,
        "kai_password_length": len(KAI_PASSWORD) if KAI_PASSWORD else 0,
        "sprites_dir_exists": os.path.exists(SPRITES_DIR),
        "sprites_count": len(os.listdir(SPRITES_DIR)) if os.path.exists(SPRITES_DIR) else 0,
    }

# ─── Dev server ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
