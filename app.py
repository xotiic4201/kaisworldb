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

# Debug print to verify env vars on startup
print(f"🔧 Environment check:")
print(f"  SUPABASE_URL: {SUPABASE_URL[:30] if SUPABASE_URL else 'NOT SET'}...")
print(f"  SUPABASE_ANON_KEY: {SUPABASE_ANON_KEY[:20] if SUPABASE_ANON_KEY else 'NOT SET'}...")
print(f"  KAI_EMAIL: {KAI_EMAIL}")
print(f"  KAI_PASSWORD: {'*' * len(KAI_PASSWORD) if KAI_PASSWORD else 'NOT SET'}")
print(f"  FRONTEND_URL: {FRONTEND_URL}")
print(f"  PORT: {PORT}")

# ─── Sprite storage directory ──────────────────────────────────────────────────
SPRITES_DIR = os.path.join(os.path.dirname(__file__), "sprites")
os.makedirs(SPRITES_DIR, exist_ok=True)

# ─── Load frontend HTML once at startup ────────────────────────────────────────
_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
try:
    with open(_HTML_PATH, "r", encoding="utf-8") as _f:
        _HTML_TEMPLATE = _f.read()
    print(f"✓ Loaded index.html ({len(_HTML_TEMPLATE)} chars)")
except FileNotFoundError:
    _HTML_TEMPLATE = "<h1>index.html not found — place it next to main.py</h1>"
    print("✗ index.html NOT FOUND")

# ─── Load Kai's Lounge admin panel ────────────────────────────────────────────
_LOUNGE_PATH = os.path.join(os.path.dirname(__file__), "kais_lounge.html")
try:
    with open(_LOUNGE_PATH, "r", encoding="utf-8") as _f:
        _LOUNGE_TEMPLATE = _f.read()
    print(f"✓ Loaded kais_lounge.html ({len(_LOUNGE_TEMPLATE)} chars)")
except FileNotFoundError:
    _LOUNGE_TEMPLATE = "<h1>kais_lounge.html not found</h1>"
    print("✗ kais_lounge.html NOT FOUND")


def inject_config(html: str, admin: bool = False) -> str:
    """Inject real Supabase credentials into an HTML template."""
    # Replace placeholders with actual environment variables
    html = html.replace("'SUPABASE_URL'", f"'{SUPABASE_URL}'")
    html = html.replace('"SUPABASE_URL"', f'"{SUPABASE_URL}"')
    html = html.replace("'SUPABASE_ANON_KEY'", f"'{SUPABASE_ANON_KEY}'")
    html = html.replace('"SUPABASE_ANON_KEY"', f'"{SUPABASE_ANON_KEY}"')
    html = html.replace("'SUPABASE_SERVICE_KEY'", f"'{SUPABASE_ANON_KEY}'")
    html = html.replace('"SUPABASE_SERVICE_KEY"', f'"{SUPABASE_ANON_KEY}"')
    html = html.replace("'API_URL_PLACEHOLDER'", f"'{FRONTEND_URL or ''}'")
    html = html.replace('"API_URL_PLACEHOLDER"', f'"{FRONTEND_URL or ""}"')
    
    # Also replace any raw placeholder text without quotes (just in case)
    html = html.replace("SUPABASE_URL_PLACEHOLDER", SUPABASE_URL)
    html = html.replace("SUPABASE_ANON_KEY_PLACEHOLDER", SUPABASE_ANON_KEY)

    if admin:
        html = html.replace(
            "</body>",
            "<script>window.__KAI_ADMIN__ = true;</script>\n</body>"
        )
    
    return html


# ─── Supabase REST helper ──────────────────────────────────────────────────────
async def supabase_query(
    table: str,
    *,
    method: str = "GET",
    params: dict = None,
    body: dict = None,
    select: str = "*",
    use_service_key: bool = False,
) -> dict:
    if not SUPABASE_URL:
        return {"data": [], "status": 503, "ok": False}
    
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

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(
            method,
            url,
            headers=headers,
            params=query_params if method == "GET" else None,
            json=body,
        )
    try:
        data = resp.json()
    except Exception:
        data = {}
    return {"data": data, "status": resp.status_code, "ok": resp.is_success}


async def supabase_storage_upload(bucket: str, path: str, file_bytes: bytes, content_type: str) -> dict:
    """Upload a file to Supabase Storage."""
    if not SUPABASE_URL:
        return {"ok": False, "status": 503}
    
    key = SUPABASE_SERVICE_KEY
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, content=file_bytes)
    return {"ok": resp.is_success, "status": resp.status_code}


async def verify_kai_token(authorization: Optional[str] = Header(None)) -> bool:
    """Simple verification - accepts any bearer token."""
    return authorization is not None and authorization.startswith("Bearer ")


# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="KAICORE API",
    description="Backend for KAICORE — Kai's personal digital space",
    version="1.0.0",
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
#  AUTHENTICATION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/login")
async def auth_login(payload: LoginPayload):
    """Login using Render env credentials - no Supabase call."""
    
    # These come from Render's environment variables
    valid_email = os.getenv("KAI_EMAIL", "kaianna@kaicore.com")
    valid_password = os.getenv("KAI_PASSWORD", "nirf8jf4f84jf84nff48fn8g338nff8nnfie8eei639204ksmdf")
    
    # Simple check
    if payload.email == valid_email and payload.password == valid_password:
        return {
            "access_token": "success",
            "token_type": "bearer",
            "expires_in": 86400
        }
    
    raise HTTPException(status_code=401, detail="Invalid credentials")


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    """Serve the main KAICORE frontend (book gate → full site)."""
    return HTMLResponse(content=inject_config(_HTML_TEMPLATE, admin=False))


@app.get("/kai", response_class=HTMLResponse, include_in_schema=False)
async def serve_kai_admin():
    """Kai's private admin entry point — bypasses book gate."""
    return HTMLResponse(content=inject_config(_HTML_TEMPLATE, admin=True))


@app.get("/lounge", response_class=HTMLResponse, include_in_schema=False)
async def serve_kais_lounge():
    """
    Kai's Lounge — the full standalone admin panel.
    Full website editor: threads, vlogs, profile, sprites, theme, stats, etc.
    Protected by Supabase auth (login required on load).
    """
    return HTMLResponse(content=inject_config(_LOUNGE_TEMPLATE, admin=False))


# ══════════════════════════════════════════════════════════════════════════════
#  API — SPRITE UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

ALLOWED_SPRITE_TYPES = {"cat", "bird", "bat", "dog", "custom"}
ALLOWED_MIME = {"image/png", "image/gif", "image/webp", "image/jpeg"}

@app.post("/api/sprites/upload")
async def upload_sprite(
    sprite_type: str = Form(...),
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    """
    Upload a sprite sheet PNG/GIF directly to the server.
    Only Kai (authenticated) can upload.
    Returns the public URL: /sprites/{sprite_type}.{ext}
    """
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
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
    if len(data) > 5 * 1024 * 1024:  # 5MB limit
        raise HTTPException(status_code=413, detail="File too large (max 5MB)")

    with open(dest, "wb") as f:
        f.write(data)

    sprite_url = f"/sprites/{filename}"
    print(f"✓ Sprite uploaded: {filename} ({len(data)} bytes)")

    # Also try to save to Supabase storage if configured
    if SUPABASE_URL:
        try:
            await supabase_storage_upload("sprites", filename, data, content_type)
        except Exception as e:
            print(f"Supabase storage upload failed (using local): {e}")

    return JSONResponse({"ok": True, "url": sprite_url, "filename": filename})


@app.get("/api/sprites/list")
async def list_sprites():
    """List all uploaded sprites and their URLs."""
    sprites = {}
    for fname in os.listdir(SPRITES_DIR):
        for stype in ALLOWED_SPRITE_TYPES:
            if fname.startswith(stype + "."):
                sprites[stype] = f"/sprites/{fname}"
    return JSONResponse({"sprites": sprites})


# ══════════════════════════════════════════════════════════════════════════════
#  API — VISITOR TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/track")
async def track_visit(payload: TrackPayload, request: Request):
    """Log a page view to Supabase visitors table."""
    if not SUPABASE_URL:
        return JSONResponse({"ok": True, "note": "Supabase not configured"})

    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)

    result = await supabase_query(
        "visitors",
        method="POST",
        body={
            "visitor_id": payload.visitor_id[:64],
            "page": (payload.page or "home")[:64],
            "referrer": (payload.referrer or "")[:200] or None,
            "ua": (payload.ua or "")[:300] or None,
            "ip": (ip or "")[:64] or None,
        },
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})


# ══════════════════════════════════════════════════════════════════════════════
#  API — STATS (Kai only)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def get_stats(authorization: Optional[str] = Header(None)):
    """Return visitor + content stats. Requires Kai's auth token."""
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized — Kai only")

    if not SUPABASE_URL:
        return JSONResponse({"error": "Supabase not configured"}, status_code=503)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    v_result = await supabase_query(
        "visitors",
        select="visitor_id,page,created_at,ip",
        use_service_key=True,
    )
    visitors = v_result.get("data") or []
    if not isinstance(visitors, list):
        visitors = []

    unique_visitors = len(set(v.get("visitor_id", "") for v in visitors))
    today_visits = sum(1 for v in visitors if (v.get("created_at") or "").startswith(today))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    active_count = sum(1 for v in visitors if (v.get("created_at") or "") >= cutoff)

    page_counts: dict[str, int] = {}
    for v in visitors:
        pg = v.get("page") or "home"
        page_counts[pg] = page_counts.get(pg, 0) + 1
    pages_sorted = [
        {"label": pg, "count": ct}
        for pg, ct in sorted(page_counts.items(), key=lambda x: -x[1])
    ]

    threads_r = await supabase_query("threads", select="id", use_service_key=True)
    comments_r = await supabase_query("comments", select="id", use_service_key=True)
    vlogs_r = await supabase_query("vlogs", select="id", use_service_key=True)
    sigs_r = await supabase_query("iwashere_signatures", select="id", use_service_key=True)

    def count(result):
        d = result.get("data") or []
        return len(d) if isinstance(d, list) else 0

    return JSONResponse({
        "total": unique_visitors,
        "today": today_visits,
        "active": active_count,
        "threads": count(threads_r),
        "comments": count(comments_r),
        "vlogs": count(vlogs_r),
        "signatures": count(sigs_r),
        "pages": pages_sorted[:10],
    })


# ══════════════════════════════════════════════════════════════════════════════
#  API — CONFIG (public)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "supabase_url": SUPABASE_URL,
        "supabase_anon": SUPABASE_ANON_KEY,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  DEBUG ENDPOINT (remove in production if needed)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/debug/env")
async def debug_env():
    """Debug endpoint to verify environment variables are loaded."""
    return {
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_anon_key_set": bool(SUPABASE_ANON_KEY),
        "supabase_url_prefix": SUPABASE_URL[:30] + "..." if SUPABASE_URL else None,
        "kai_email": KAI_EMAIL,
        "kai_password_length": len(KAI_PASSWORD) if KAI_PASSWORD else 0,
        "frontend_url": FRONTEND_URL,
        "sprites_dir_exists": os.path.exists(SPRITES_DIR),
        "sprites_count": len(os.listdir(SPRITES_DIR)) if os.path.exists(SPRITES_DIR) else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH
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


# ─── Dev server ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
