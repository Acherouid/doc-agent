"""Offline test of the refactor: fake LLM client, no network, no real key."""
import json
import sys
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else ".")

from agent import run, make_client  # noqa: E402
import funnel  # noqa: E402

INVOICE_TEXT = (
    "Facture N° F-2026-001\nSupplier SARL Atlas\nICE 001234567890123\n"
    "Date 15/10/2026\nTotal HT 1 000,00 MAD\nTVA 200,00 MAD\nTotal TTC 1 200,00 MAD\n"
)

GOOD = {"supplier": "SARL Atlas", "ice": "001234567890123", "invoice_number": "F-2026-001",
        "date": "2026-10-15", "total_ht": 1000.0, "tva": 200.0, "total_ttc": 1200.0, "currency": "MAD"}
BAD = {**GOOD, "total_ttc": 1300.0}  # does not add up, never fixes itself


class FakeClient:
    def __init__(self, payload):
        self.calls = 0
        self.models = []
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.calls += 1
                outer.models.append(kw["model"])
                msg = SimpleNamespace(content=json.dumps(payload))
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        self.chat = SimpleNamespace(completions=_Completions())


# 1) explicit client + model are really used
c = FakeClient(GOOD)
res = run(INVOICE_TEXT, c, "model-x")
assert res["status"] == "auto_approved", res
assert c.calls == 1 and c.models == ["model-x"], (c.calls, c.models)

# 2) faulty invoice: retried once, then routed to human review
c = FakeClient(BAD)
res = run(INVOICE_TEXT, c, "model-y")
assert res["status"] == "needs_human_review", res
assert res["attempts"] == 2 and c.calls == 2, (res["attempts"], c.calls)

# 3) two different clients do not leak into each other (per-request keys)
a, b = FakeClient(GOOD), FakeClient(GOOD)
run(INVOICE_TEXT, a, "m1")
run(INVOICE_TEXT, b, "m2")
assert a.calls == 1 and b.calls == 1 and a.models == ["m1"] and b.models == ["m2"]

# 4) make_client builds a real OpenAI client with the visitor's key and Gemini URL
cl = make_client("k-123")
assert "generativelanguage.googleapis.com" in str(cl.base_url) and cl.api_key == "k-123"

# 5) funnel helpers (the Supabase checks only run when Supabase is NOT configured locally)
assert funnel.norm_code("  firm-alpha ") == "FIRM-ALPHA"
if not funnel.ledger_enabled():
    assert funnel.check_sponsored("ANY") == ("ledger_off", 0)
    funnel.log_usage("byok", None, "h", 1, "auto_approved")  # must not raise
    print("ledger disabled: graceful fallback OK")

# 6) invite codes and request validation (no database needed)
import re  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import admin  # noqa: E402

codes = {admin.new_code() for _ in range(2000)}
assert len(codes) > 1990, "codes should be random and practically collision-free"
assert all(re.fullmatch(r"DEMO-[A-HJ-NP-Z2-9]{6}", c) for c in codes)

exp = datetime(2026, 11, 1, tzinfo=timezone.utc)
msg_fr = admin.invite_message("fr", "Cabinet X", "DEMO-ABC234", 5, exp, "https://app.example")
msg_en = admin.invite_message("en", "Firm X", "DEMO-ABC234", 5, exp, "https://app.example")
assert "DEMO-ABC234" in msg_fr and "01/11/2026" in msg_fr and "https://app.example" in msg_fr
assert "DEMO-ABC234" in msg_en and "Invite code" in msg_en

assert funnel.request_error("", "a@b.co", "") == "req_need_name"
assert funnel.request_error("Ali", "", "") == "req_need_contact"
assert funnel.request_error("Ali", "not-an-email", "") == "req_bad_email"
assert funnel.request_error("Ali", "ali@cabinet.ma", "") is None
assert funnel.request_error("Ali", "", "+212600000000") is None
assert funnel._parse_ts("2026-11-01T12:30:45.12345+00:00") == datetime(2026, 11, 1, 12, 30, 45, tzinfo=timezone.utc)

# 7) Supabase misconfiguration must fail loudly, never look like an empty result
for good in ("https://abcdefgh.supabase.co", "https://abcdefgh.supabase.co/", "http://localhost:54321"):
    assert funnel.valid_supabase_url(good) and admin.valid_supabase_url(good), good
for bad in ("https://supabase.com/dashboard/project/abcdefgh/settings/general",
            "https://abcdefgh.supabase.co/rest/v1", "https://supabase.com", "", None, "abcdefgh.supabase.co"):
    assert not funnel.valid_supabase_url(bad) and not admin.valid_supabase_url(bad), bad

assert funnel._rows(SimpleNamespace(data=[{"a": 1}])) == [{"a": 1}]
assert funnel._rows(SimpleNamespace(data=[])) == []
try:
    funnel._rows(SimpleNamespace(data="<!DOCTYPE html><html>"))
    raise AssertionError("funnel._rows accepted an HTML string")
except RuntimeError:
    pass
try:
    admin.rows(SimpleNamespace(data="<!DOCTYPE html><html>"))
    raise AssertionError("admin.rows accepted an HTML string")
except SystemExit:
    pass

print("ALL OK")
