"""
KAICORE — FastAPI Backend
Deploy to Render.com
"""

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
import os
import bcrypt
import uuid
from supabase import create_client, Client
from dotenv import load_dotenv
import secrets

load_dotenv()

# ─── Supabase Client ───────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # use service key for full access
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="KAICORE API", version="1.0.0")

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://kaicore.vercel.app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000", "http://localhost:5500", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory session store (use Redis in prod) ───────────────────────────────
sessions: dict = {}

KAI_USERNAME = "kaiana"
KAI_PASSWORD = os.getenv("KAI_PASSWORD", "nirf8jf4f84jf84nff48fn8g338nff8nnfie8eei639204ksmdf")
JOURNAL_PASSWORD = os.getenv("JOURNAL_PASSWORD", "kai")

# ─── Auth helpers ──────────────────────────────────────────────────────────────
def get_session(request: Request) -> Optional[dict]:
    token = request.cookies.get("kai_session")
    if token and token in sessions:
        return sessions[token]
    return None

def require_kai(request: Request):
    session = get_session(request)
    if not session or not session.get("is_kai"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return session

# ─── Models ────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class ThreadCreate(BaseModel):
    content: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None

class ThreadUpdate(BaseModel):
    content: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    is_hidden: Optional[bool] = None

class CommentCreate(BaseModel):
    thread_id: int
    parent_id: Optional[int] = None
    author_name: str = "anonymous"
    author_color: str = "#FF69B4"
    content: str

class CommentVote(BaseModel):
    vote_type: str  # "up" or "down"

class VlogCreate(BaseModel):
    title: str
    description: Optional[str] = None
    video_url: str
    thumbnail_url: Optional[str] = None
    duration: Optional[str] = None

class VlogCommentCreate(BaseModel):
    vlog_id: int
    author_name: str = "anonymous"
    author_color: str = "#FF69B4"
    content: str

class JournalEntry(BaseModel):
    date: str
    mood: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    image_url: Optional[str] = None
    journal_password: str

class ThemeUpdate(BaseModel):
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    glossiness: Optional[int] = None
    border_radius: Optional[int] = None
    scanline_opacity: Optional[int] = None
    pet_frequency: Optional[int] = None
    bubble_size: Optional[int] = None
    preset_name: Optional[str] = None

class FunFactUpdate(BaseModel):
    facts: List[dict]  # [{slot_number, label, fact_text}]

class ProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    pronouns: Optional[str] = None
    location: Optional[str] = None
    zodiac: Optional[str] = None
    bio: Optional[str] = None
    mood_emoji: Optional[str] = None
    profile_pic_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    spotify_url: Optional[str] = None
    instagram_url: Optional[str] = None
    tiktok_url: Optional[str] = None

class PetCreate(BaseModel):
    name: str
    pixel_data: Optional[dict] = None
    sound_url: Optional[str] = None
    behavior_type: str = "cat"
    movement: str = "ground"
    personality: str = "playful"

class JournalPasswordCheck(BaseModel):
    journal_password: str

# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "KAICORE API is live 🎀"}


# ── Auth ───────────────────────────────────────────────────────────────────────
@app.post("/api/login")
def login(body: LoginRequest, response: Response):
    if body.username != KAI_USERNAME or body.password != KAI_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = secrets.token_hex(32)
    sessions[token] = {"username": body.username, "is_kai": True}
    
    response.set_cookie(
        key="kai_session",
        value=token,
        httponly=True,
        samesite="none",
        secure=True,
        max_age=86400 * 7  # 7 days
    )
    return {"success": True, "message": "Welcome back, Kai 🎀"}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("kai_session")
    if token and token in sessions:
        del sessions[token]
    response.delete_cookie("kai_session")
    return {"success": True}


@app.get("/api/me")
def me(request: Request):
    session = get_session(request)
    if session:
        return {"logged_in": True, "username": session["username"], "is_kai": session["is_kai"]}
    return {"logged_in": False}


# ── Threads ────────────────────────────────────────────────────────────────────
@app.get("/api/threads")
def get_threads(page: int = 1, limit: int = 20):
    offset = (page - 1) * limit
    result = supabase.table("threads")\
        .select("*, comments(count)")\
        .eq("is_hidden", False)\
        .order("created_at", desc=True)\
        .range(offset, offset + limit - 1)\
        .execute()
    
    count_result = supabase.table("threads").select("id", count="exact").eq("is_hidden", False).execute()
    
    return {
        "threads": result.data,
        "total": count_result.count,
        "page": page,
        "limit": limit
    }


@app.get("/api/threads/all")
def get_all_threads_admin(request: Request):
    require_kai(request)
    result = supabase.table("threads")\
        .select("*")\
        .order("created_at", desc=True)\
        .execute()
    return result.data


@app.post("/api/threads")
def create_thread(body: ThreadCreate, request: Request):
    require_kai(request)
    result = supabase.table("threads").insert({
        "content": body.content,
        "image_url": body.image_url,
        "video_url": body.video_url,
        "user_id": 1,
        "is_hidden": False
    }).execute()
    return result.data[0]


@app.put("/api/threads/{thread_id}")
def update_thread(thread_id: int, body: ThreadUpdate, request: Request):
    require_kai(request)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    updates["updated_at"] = datetime.utcnow().isoformat()
    result = supabase.table("threads").update(updates).eq("id", thread_id).execute()
    return result.data[0]


@app.delete("/api/threads/{thread_id}")
def delete_thread(thread_id: int, request: Request):
    require_kai(request)
    supabase.table("comments").delete().eq("thread_id", thread_id).execute()
    supabase.table("threads").delete().eq("id", thread_id).execute()
    return {"success": True}


# ── Comments ───────────────────────────────────────────────────────────────────
@app.get("/api/threads/{thread_id}/comments")
def get_comments(thread_id: int):
    result = supabase.table("comments")\
        .select("*")\
        .eq("thread_id", thread_id)\
        .order("is_pinned", desc=True)\
        .order("created_at", desc=False)\
        .execute()
    return result.data


@app.post("/api/comments")
def create_comment(body: CommentCreate):
    result = supabase.table("comments").insert({
        "thread_id": body.thread_id,
        "parent_id": body.parent_id,
        "author_name": body.author_name[:50],
        "author_color": body.author_color,
        "content": body.content[:500],
        "upvotes": 0,
        "downvotes": 0,
        "is_pinned": False
    }).execute()
    return result.data[0]


@app.post("/api/comments/{comment_id}/vote")
def vote_comment(comment_id: int, body: CommentVote):
    comment = supabase.table("comments").select("upvotes,downvotes").eq("id", comment_id).single().execute()
    if not comment.data:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    field = "upvotes" if body.vote_type == "up" else "downvotes"
    new_val = comment.data[field] + 1
    result = supabase.table("comments").update({field: new_val}).eq("id", comment_id).execute()
    return result.data[0]


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, request: Request):
    require_kai(request)
    supabase.table("comments").delete().eq("id", comment_id).execute()
    return {"success": True}


@app.put("/api/comments/{comment_id}/pin")
def pin_comment(comment_id: int, request: Request):
    require_kai(request)
    comment = supabase.table("comments").select("is_pinned").eq("id", comment_id).single().execute()
    new_pin = not comment.data["is_pinned"]
    result = supabase.table("comments").update({"is_pinned": new_pin}).eq("id", comment_id).execute()
    return result.data[0]


# ── Vlogs ──────────────────────────────────────────────────────────────────────
@app.get("/api/vlogs")
def get_vlogs():
    result = supabase.table("vlogs")\
        .select("*")\
        .eq("is_hidden", False)\
        .order("created_at", desc=True)\
        .execute()
    return result.data


@app.post("/api/vlogs")
def create_vlog(body: VlogCreate, request: Request):
    require_kai(request)
    result = supabase.table("vlogs").insert({
        "user_id": 1,
        **body.dict()
    }).execute()
    return result.data[0]


@app.delete("/api/vlogs/{vlog_id}")
def delete_vlog(vlog_id: int, request: Request):
    require_kai(request)
    supabase.table("vlog_comments").delete().eq("vlog_id", vlog_id).execute()
    supabase.table("vlogs").delete().eq("id", vlog_id).execute()
    return {"success": True}


@app.get("/api/vlogs/{vlog_id}/comments")
def get_vlog_comments(vlog_id: int):
    result = supabase.table("vlog_comments")\
        .select("*")\
        .eq("vlog_id", vlog_id)\
        .order("created_at")\
        .execute()
    return result.data


@app.post("/api/vlog-comments")
def create_vlog_comment(body: VlogCommentCreate):
    result = supabase.table("vlog_comments").insert({
        "vlog_id": body.vlog_id,
        "author_name": body.author_name[:50],
        "author_color": body.author_color,
        "content": body.content[:500]
    }).execute()
    return result.data[0]


# ── On This Day ────────────────────────────────────────────────────────────────
@app.get("/api/onthisday")
def on_this_day():
    today = datetime.utcnow()
    month = today.month
    day = today.day
    current_year = today.year
    
    result = supabase.table("threads")\
        .select("*")\
        .eq("is_hidden", False)\
        .execute()
    
    flashbacks = []
    for thread in result.data:
        created = datetime.fromisoformat(thread["created_at"].replace("Z", "+00:00"))
        if created.month == month and created.day == day and created.year < current_year:
            flashbacks.append(thread)
    
    flashbacks.sort(key=lambda x: x["created_at"], reverse=True)
    return flashbacks


# ── Journal (private) ──────────────────────────────────────────────────────────
@app.post("/api/journal/auth")
def journal_auth(body: JournalPasswordCheck):
    if body.journal_password != JOURNAL_PASSWORD:
        raise HTTPException(status_code=401, detail="nice try! 🐱")
    return {"success": True}


@app.get("/api/journal")
def get_journal(request: Request, journal_password: str):
    require_kai(request)
    if journal_password != JOURNAL_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    result = supabase.table("journal_entries")\
        .select("*")\
        .order("date", desc=True)\
        .execute()
    return result.data


@app.post("/api/journal")
def create_journal_entry(body: JournalEntry, request: Request):
    require_kai(request)
    if body.journal_password != JOURNAL_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    result = supabase.table("journal_entries").insert({
        "user_id": 1,
        "date": body.date,
        "mood": body.mood,
        "title": body.title,
        "content": body.content,
        "image_url": body.image_url
    }).execute()
    return result.data[0]


@app.delete("/api/journal/{entry_id}")
def delete_journal_entry(entry_id: int, request: Request):
    require_kai(request)
    supabase.table("journal_entries").delete().eq("id", entry_id).execute()
    return {"success": True}


# ── Theme ──────────────────────────────────────────────────────────────────────
@app.get("/api/theme")
def get_theme():
    result = supabase.table("theme_settings").select("*").eq("id", 1).single().execute()
    return result.data


@app.put("/api/theme")
def update_theme(body: ThemeUpdate, request: Request):
    require_kai(request)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    updates["updated_at"] = datetime.utcnow().isoformat()
    result = supabase.table("theme_settings").update(updates).eq("id", 1).execute()
    return result.data[0]


# ── Profile ────────────────────────────────────────────────────────────────────
@app.get("/api/profile")
def get_profile():
    result = supabase.table("profile").select("*").eq("id", 1).single().execute()
    return result.data


@app.put("/api/profile")
def update_profile(body: ProfileUpdate, request: Request):
    require_kai(request)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    updates["updated_at"] = datetime.utcnow().isoformat()
    result = supabase.table("profile").update(updates).eq("id", 1).execute()
    return result.data[0]


# ── Fun Facts ──────────────────────────────────────────────────────────────────
@app.get("/api/funfacts")
def get_funfacts():
    result = supabase.table("fun_facts").select("*").order("slot_number").execute()
    return result.data


@app.put("/api/funfacts")
def update_funfacts(body: FunFactUpdate, request: Request):
    require_kai(request)
    for fact in body.facts:
        supabase.table("fun_facts").upsert({
            "slot_number": fact["slot_number"],
            "label": fact.get("label"),
            "fact_text": fact.get("fact_text"),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
    return {"success": True}


# ── Pets ───────────────────────────────────────────────────────────────────────
@app.get("/api/pets")
def get_pets():
    result = supabase.table("custom_pets").select("*").eq("is_active", True).execute()
    return result.data


@app.post("/api/pets")
def create_pet(body: PetCreate, request: Request):
    require_kai(request)
    count = supabase.table("custom_pets").select("id", count="exact").execute()
    if count.count >= 10:
        raise HTTPException(status_code=400, detail="Max 10 custom pets reached")
    result = supabase.table("custom_pets").insert(body.dict()).execute()
    return result.data[0]


@app.delete("/api/pets/{pet_id}")
def delete_pet(pet_id: int, request: Request):
    require_kai(request)
    supabase.table("custom_pets").delete().eq("id", pet_id).execute()
    return {"success": True}


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
