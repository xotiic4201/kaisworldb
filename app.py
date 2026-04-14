import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ─── Environment ───────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY    = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", SUPABASE_ANON_KEY)
KAI_EMAIL            = os.getenv("KAI_EMAIL", "")
KAI_PASSWORD         = os.getenv("KAI_PASSWORD", "")
FRONTEND_URL         = os.getenv("FRONTEND_URL", "*")
PORT                 = int(os.getenv("PORT", "8000"))

# ─── Load frontend HTML once at startup ────────────────────────────────────────
_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
try:
    with open(_HTML_PATH, "r", encoding="utf-8") as _f:
        _HTML_TEMPLATE = _f.read()
    print(f"✓ Loaded index.html ({len(_HTML_TEMPLATE)} chars)")
except FileNotFoundError:
    _HTML_TEMPLATE = "<h1>index.html not found — place it next to main.py</h1>"
    print("✗ index.html NOT FOUND")


def render_html(admin: bool = False) -> str:
    """Inject real Supabase credentials into the HTML template."""
    html = _HTML_TEMPLATE

    # Replace placeholder strings with real values from env
    html = html.replace("'SUPABASE_URL'",      f"'{SUPABASE_URL}'")
    html = html.replace('"SUPABASE_URL"',       f'"{SUPABASE_URL}"')
    html = html.replace("'SUPABASE_ANON_KEY'",  f"'{SUPABASE_ANON_KEY}'")
    html = html.replace('"SUPABASE_ANON_KEY"',  f'"{SUPABASE_ANON_KEY}"')

    # Legacy placeholder names from original file
    html = html.replace("'SUPABASE_SERVICE_KEY'", f"'{SUPABASE_ANON_KEY}'")
    html = html.replace('"SUPABASE_SERVICE_KEY"',  f'"{SUPABASE_ANON_KEY}"')

    if admin:
        # Inject admin flag right before </body> so it runs last
        html = html.replace(
            "</body>",
            "<script>window.__KAI_ADMIN__ = true;</script>\n</body>"
        )

    return html


# ─── Supabase REST helper (no SDK needed) ──────────────────────────────────────
import httpx

async def supabase_query(
    table: str,
    *,
    method: str = "GET",
    params: dict = None,
    body: dict = None,
    select: str = "*",
    use_service_key: bool = False,
) -> dict:
    """Thin async wrapper around the Supabase REST API."""
    key   = SUPABASE_SERVICE_KEY if use_service_key else SUPABASE_ANON_KEY
    url   = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }
    query_params = {"select": select}
    if params:
        query_params.update(params)

    async with httpx.AsyncClient(timeout=10) as client:
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


async def supabase_rpc(function_name: str, payload: dict = None) -> dict:
    """Call a Supabase database function."""
    key = SUPABASE_SERVICE_KEY
    url = f"{SUPABASE_URL}/rest/v1/rpc/{function_name}"
    headers = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, json=payload or {})
    try:
        return {"data": resp.json(), "ok": resp.is_success}
    except Exception:
        return {"data": None, "ok": False}


# ─── Auth helper ───────────────────────────────────────────────────────────────
async def verify_kai_token(authorization: Optional[str] = Header(None)) -> bool:
    """Verify a Supabase JWT passed as Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.split(" ", 1)[1]
    # Verify with Supabase auth API
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey":        SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {token}",
            },
        )
    return resp.is_success


# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="KAICORE API",
    description="Backend for KAICORE — Kai's personal digital space",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to FRONTEND_URL once domain is set
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


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    """Serve the main KAICORE frontend (book gate → full site)."""
    return HTMLResponse(content=render_html(admin=False))


@app.get("/kai", response_class=HTMLResponse, include_in_schema=False)
async def serve_kai_admin():
    """
    Kai's private admin entry point.
    Bypasses the book gate and opens the login page directly.
    Credentials are in Render env vars — no hardcoding.
    """
    return HTMLResponse(content=render_html(admin=True))


# ══════════════════════════════════════════════════════════════════════════════
#  API — VISITOR TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/track")
async def track_visit(payload: TrackPayload, request: Request):
    """
    Log a page view to Supabase `visitors` table.
    Called by the frontend JS on every page load.

    Required Supabase table (run in SQL Editor):
    ─────────────────────────────────────────────
    create table if not exists visitors (
      id          uuid default gen_random_uuid() primary key,
      visitor_id  text not null,
      page        text,
      referrer    text,
      ua          text,
      ip          text,
      created_at  timestamptz default now()
    );
    alter table visitors enable row level security;
    create policy "insert visitors" on visitors for insert with check (true);
    create policy "service read"    on visitors for select using (true);
    ─────────────────────────────────────────────
    """
    if not SUPABASE_URL:
        return JSONResponse({"ok": True, "note": "Supabase not configured"})

    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)

    result = await supabase_query(
        "visitors",
        method="POST",
        body={
            "visitor_id": payload.visitor_id[:64],
            "page":       (payload.page or "home")[:64],
            "referrer":   (payload.referrer or "")[:200] or None,
            "ua":         (payload.ua or "")[:300] or None,
            "ip":         (ip or "")[:64] or None,
        },
        use_service_key=True,
    )
    return JSONResponse({"ok": result["ok"]})


# ══════════════════════════════════════════════════════════════════════════════
#  API — STATS  (Kai only)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def get_stats(authorization: Optional[str] = Header(None)):
    """
    Return visitor + content stats for Kai's dashboard.
    Requires a valid Supabase Bearer token (only Kai has one).
    """
    is_kai = await verify_kai_token(authorization)
    if not is_kai:
        raise HTTPException(status_code=401, detail="Unauthorized — Kai only")

    if not SUPABASE_URL:
        return JSONResponse({"error": "Supabase not configured"}, status_code=503)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Fetch all visitors ──────────────────────────────────────────────────
    v_result = await supabase_query(
        "visitors",
        select="visitor_id,page,created_at,ip",
        use_service_key=True,
    )
    visitors = v_result.get("data") or []
    if not isinstance(visitors, list):
        visitors = []

    unique_visitors = len(set(v.get("visitor_id", "") for v in visitors))
    today_visits    = sum(
        1 for v in visitors
        if (v.get("created_at") or "").startswith(today)
    )

    # Active in last 5 minutes (rough estimate)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    active_count = sum(
        1 for v in visitors
        if (v.get("created_at") or "") >= cutoff
    )

    # ── Page breakdown ──────────────────────────────────────────────────────
    page_counts: dict[str, int] = {}
    for v in visitors:
        pg = v.get("page") or "home"
        page_counts[pg] = page_counts.get(pg, 0) + 1
    pages_sorted = [
        {"label": pg, "count": ct}
        for pg, ct in sorted(page_counts.items(), key=lambda x: -x[1])
    ]

    # ── Content counts ──────────────────────────────────────────────────────
    threads_r    = await supabase_query("threads",             select="id", use_service_key=True)
    comments_r   = await supabase_query("comments",            select="id", use_service_key=True)
    vlogs_r      = await supabase_query("vlogs",               select="id", use_service_key=True)
    sigs_r       = await supabase_query("iwashere_signatures", select="id", use_service_key=True)

    def count(result):
        d = result.get("data") or []
        return len(d) if isinstance(d, list) else 0

    return JSONResponse({
        "total":        unique_visitors,
        "today":        today_visits,
        "active":       active_count,
        "threads":      count(threads_r),
        "comments":     count(comments_r),
        "vlogs":        count(vlogs_r),
        "signatures":   count(sigs_r),
        "pages":        pages_sorted[:10],
    })


# ══════════════════════════════════════════════════════════════════════════════
#  API — CONFIG  (public, non-secret)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/config")
async def get_config():
    """
    Returns the Supabase URL and anon key so the frontend can
    be served as a static file separately if needed.
    The anon key is public-safe — it enforces RLS on Supabase.
    """
    return JSONResponse({
        "supabase_url":  SUPABASE_URL,
        "supabase_anon": SUPABASE_ANON_KEY,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return JSONResponse({
        "status":    "ok",
        "service":   "KAICORE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "supabase":  bool(SUPABASE_URL),
    })


# ─── Dev server ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
