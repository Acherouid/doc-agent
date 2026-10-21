"""Access funnel for the public demo: cached samples, invite codes, access requests, usage ledger.

Everything that touches Supabase is optional at runtime: if it is not configured,
invite codes and the request form are disabled and the rest of the app keeps working.
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import streamlit as st

SAMPLES_CACHE = "data/samples_cache.json"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # 'Z' form: safe inside URL query strings


def secret(name, default=None):
    """Read from st.secrets first, then from the environment."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


# ---------------------------------------------------------------------------
# Tier 0: pre-computed samples (zero API cost)
# ---------------------------------------------------------------------------
@st.cache_data
def load_sample_cache() -> dict:
    try:
        with open(SAMPLES_CACHE, encoding="utf-8") as f:
            return json.load(f).get("samples", {})
    except FileNotFoundError:
        return {}


# ---------------------------------------------------------------------------
# Supabase access
# ---------------------------------------------------------------------------
def valid_supabase_url(url) -> bool:
    """True for a project API URL like https://<project-id>.supabase.co (no path, not a dashboard link)."""
    p = urlparse(str(url or ""))
    return (
        p.scheme in ("https", "http")
        and bool(p.netloc)
        and not p.path.strip("/")
        and p.netloc.lower() not in ("supabase.com", "www.supabase.com", "app.supabase.com")
    )


def _rows(resp):
    """Rows of a Supabase response; raises if the server answered with something that is not rows."""
    if not isinstance(resp.data, list):
        raise RuntimeError("Unexpected response from Supabase (check SUPABASE_URL and the service key)")
    return resp.data


@st.cache_resource
def _db():
    url, key = secret("SUPABASE_URL"), secret("SUPABASE_SERVICE_KEY")
    if not (url and key):
        return None
    if not valid_supabase_url(url):
        print("[funnel] SUPABASE_URL must look like https://<project-id>.supabase.co; "
              "invite codes are disabled until it is fixed.", file=sys.stderr)
        return None
    from supabase import create_client
    return create_client(url, key)


def ledger_enabled() -> bool:
    return _db() is not None


def _parse_ts(value: str) -> datetime:
    """Parse a Supabase timestamptz (UTC) to an aware datetime, whatever the fraction digits."""
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Tier 2: invite codes + quota
# ---------------------------------------------------------------------------
def norm_code(code: str) -> str:
    return (code or "").strip().upper()


def get_invite(code: str):
    found = _rows(_db().table("invite_codes").select("*").eq("code", norm_code(code)).limit(1).execute())
    return found[0] if found else None


def docs_used_by_code(code: str) -> int:
    r = (_db().table("usage_ledger").select("id", count="exact")
         .eq("tier", "sponsored").eq("code", norm_code(code)).execute())
    _rows(r)  # a non-row answer must not look like "0 documents used"
    return r.count or 0


def docs_used_today() -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).strftime(TS_FORMAT)
    r = (_db().table("usage_ledger").select("id", count="exact")
         .eq("tier", "sponsored").gte("created_at", start).execute())
    _rows(r)
    return r.count or 0


def check_sponsored(code: str):
    """Returns (error_key | None, remaining_docs). error_key is a TEXT key in app.py."""
    if not ledger_enabled() or not secret("LLM_API_KEY"):
        return "ledger_off", 0
    inv = get_invite(code)
    if inv is None or not inv.get("active"):
        return "invalid_code", 0
    exp = inv.get("expires_at")
    if exp and _parse_ts(exp) < datetime.now(timezone.utc):
        return "code_expired", 0
    remaining = int(inv["quota"]) - docs_used_by_code(code)
    if remaining <= 0:
        return "quota_exhausted", 0
    if docs_used_today() >= int(secret("DAILY_CAP", 40)):
        return "daily_cap", remaining
    return None, remaining


def log_usage(tier: str, code, doc_hash: str, attempts: int, status) -> None:
    """Never raises: a ledger problem must not break the demo. Stores no invoice content."""
    if not ledger_enabled():
        return
    try:
        _db().table("usage_ledger").insert({
            "tier": tier,
            "code": norm_code(code) if code else None,
            "doc_hash": doc_hash,
            "attempts": attempts,
            "status": status,
        }).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# "Request a code" form
# ---------------------------------------------------------------------------
def request_error(name: str, email: str, phone: str):
    """Returns a TEXT key describing the problem, or None if the request is valid."""
    if not (name or "").strip():
        return "req_need_name"
    email, phone = (email or "").strip(), (phone or "").strip()
    if not email and not phone:
        return "req_need_contact"
    if email and not EMAIL_RE.match(email):
        return "req_bad_email"
    return None


def submit_access_request(name, firm, email, phone, message, lang):
    """Stores a request. Returns a TEXT key on error, None on success.
    A repeat request from the same email/phone within 7 days is accepted silently (no duplicate row)."""
    err = request_error(name, email, phone)
    if err:
        return err
    if not ledger_enabled():
        return "ledger_off"
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(TS_FORMAT)
        q = _db().table("access_requests").select("id").gte("created_at", since)
        q = q.eq("email", email) if email else q.eq("phone", phone)
        if _rows(q.limit(1).execute()):
            return None
        _db().table("access_requests").insert({
            "name": name.strip()[:120],
            "firm": (firm or "").strip()[:120] or None,
            "email": email or None,
            "phone": phone or None,
            "message": (message or "").strip()[:500] or None,
            "lang": lang,
        }).execute()
    except Exception:
        return "req_failed"
    return None
