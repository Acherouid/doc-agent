-- Run once in Supabase: SQL Editor -> New query -> paste -> Run.

-- 1) Usage ledger: one row per processed document (never the document itself).
create table if not exists public.usage_ledger (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  tier        text not null check (tier in ('sponsored', 'byok')),
  code        text,          -- invite code (sponsored only)
  doc_hash    text,          -- SHA-256 of the uploaded file, never its content
  attempts    int,
  status      text
);
create index if not exists usage_ledger_tier_created on public.usage_ledger (tier, created_at);
create index if not exists usage_ledger_code on public.usage_ledger (code);

-- 2) Invite codes you hand out (created with: python admin.py new ...).
create table if not exists public.invite_codes (
  code        text primary key,                 -- stored in UPPERCASE
  label       text not null,                    -- firm / person, for your own tracking
  contact     text,                             -- email or phone you sent it to
  quota       int  not null default 5 check (quota >= 0),
  expires_at  timestamptz,                      -- null = never expires
  active      boolean not null default true,    -- false = revoked
  created_at  timestamptz not null default now()
);

-- 3) Requests coming from the "request a code" form in the app.
create table if not exists public.access_requests (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  name        text not null,
  firm        text,
  email       text,
  phone       text,
  message     text,
  lang        text,
  status      text not null default 'new' check (status in ('new', 'sent', 'declined'))
);
create index if not exists access_requests_status on public.access_requests (status, created_at);

-- No policies on purpose: the public anon key cannot read or write any of these tables.
-- The app and admin.py use the service_role key (kept in secrets only), which bypasses RLS.
alter table public.usage_ledger    enable row level security;
alter table public.invite_codes    enable row level security;
alter table public.access_requests enable row level security;
