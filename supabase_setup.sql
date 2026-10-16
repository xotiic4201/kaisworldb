-- Kaiana's World: database setup
-- Run once in Supabase: Dashboard → SQL Editor → New query → paste → Run.
-- Safe to run again; nothing here deletes data.

-- ─── 1. Pet Studio storage ───────────────────────────────────────────────────
-- Pets made or uploaded in the lounge are stored here so they survive Render
-- restarts (Render wipes the server's own disk on every redeploy).
create table if not exists public.pet_sprites (
  name        text primary key check (name ~ '^[a-z0-9][a-z0-9_-]{0,23}$'),
  image       text not null,                      -- the picture, base64-encoded
  mime        text not null default 'image/png',
  frames      int  not null default 1      check (frames between 1 and 24),
  fps         int  not null default 6      check (fps between 1 and 24),
  motion      text not null default 'walk' check (motion in ('walk', 'hop', 'fly', 'float', 'follow')),
  speed       text not null default 'normal' check (speed in ('slow', 'normal', 'fast')),
  size        text not null default 'm'    check (size in ('s', 'm', 'l', 'xl')),
  updated_at  timestamptz not null default now()
);

-- ─── 1b. Photo & video uploads ───────────────────────────────────────────────
-- A public storage bucket for photos and videos uploaded in the lounge (feed &
-- vlog). Anyone can view files; only the website's server can add them.
insert into storage.buckets (id, name, public, file_size_limit)
values ('media', 'media', true, 52428800)
on conflict (id) do update set public = true;

-- ─── 2. Lock the tables (strongly recommended) ───────────────────────────────
-- The old site handed out the public "anon" key at /api/config. If row level
-- security is off, anyone who grabbed that key can read and edit every table
-- directly, including the private journal. Turning RLS on with no policies means
-- only the website's server (using the service_role key) can touch the data.
--
-- BEFORE running this part: in Render → your service → Environment, make sure
-- SUPABASE_SERVICE_KEY is set to the "service_role" key from
-- Supabase → Project Settings → API. Without it the site can't read its own data.
-- Then remove the "--" in front of each line below and run it.

-- alter table public.kai_settings        enable row level security;
-- alter table public.threads             enable row level security;
-- alter table public.comments            enable row level security;
-- alter table public.posts               enable row level security;
-- alter table public.vlogs               enable row level security;
-- alter table public.fun_facts           enable row level security;
-- alter table public.live_status         enable row level security;
-- alter table public.chat_messages       enable row level security;
-- alter table public.iwashere_signatures enable row level security;
-- alter table public.journal_entries     enable row level security;
-- alter table public.visitors            enable row level security;
-- alter table public.pet_sprites         enable row level security;
