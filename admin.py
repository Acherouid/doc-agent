"""Admin CLI for invite codes and access requests (run it on your own machine).

    python admin.py check                          # verify the Supabase connection and the three tables
    python admin.py requests                       # new requests from the app's form
    python admin.py new --request 3 --docs 5       # create a code for request #3, print the message to send
    python admin.py new "Cabinet Alami" --contact +212600000000 --docs 5 --days 14 --lang fr
    python admin.py list                           # codes with usage, expiry, status
    python admin.py add-docs DEMO-XXXXXX 5         # give a code 5 more documents
    python admin.py revoke DEMO-XXXXXX             # disable a code immediately
    python admin.py mark 3 declined                # mark a request as sent / declined

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY, read from the environment / .env
or from .streamlit/secrets.toml. SUPABASE_URL must be the project API URL
(https://<project-id>.supabase.co), not a dashboard link. APP_URL (optional)
is used in the message.
"""
import argparse
import os
import secrets
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid typos when reading a code aloud
URL_HELP = "SUPABASE_URL must be the project API URL, like https://<project-id>.supabase.co (no path, not a dashboard link)."


def new_code() -> str:
    return "DEMO-" + "".join(secrets.choice(ALPHABET) for _ in range(6))


def valid_supabase_url(url) -> bool:
    p = urlparse(str(url or ""))
    return (
        p.scheme in ("https", "http")
        and bool(p.netloc)
        and not p.path.strip("/")
        and p.netloc.lower() not in ("supabase.com", "www.supabase.com", "app.supabase.com")
    )


def rows(resp):
    """Rows of a Supabase response, or exit with a clear message if the response is not a list of rows."""
    if not isinstance(resp.data, list):
        sys.exit("Unexpected response from Supabase (got text instead of rows). " + URL_HELP)
    return resp.data


def invite_message(lang: str, label: str, code: str, docs: int, expires: datetime, url: str) -> str:
    date = expires.strftime("%d/%m/%Y")
    if lang == "en":
        return (
            f"Hello {label},\n\n"
            f"Here is your access code for the doc-agent demo (invoice processing): {code}\n"
            f"It lets you test {docs} of your own PDF invoices until {date}.\n\n"
            f"1. Open {url}\n"
            f"2. Upload a PDF, choose \"Invite code\" and enter the code.\n\n"
            "Tip: use anonymized or non-sensitive invoices. I'd love to hear what you think."
        )
    return (
        f"Bonjour {label},\n\n"
        f"Voici votre code d'accès à la démo doc-agent (traitement de factures) : {code}\n"
        f"Il permet de tester {docs} de vos propres factures PDF jusqu'au {date}.\n\n"
        f"1. Ouvrez {url}\n"
        "2. Téléversez un PDF, choisissez « Code d'invitation » et saisissez le code.\n\n"
        "Conseil : utilisez des factures anonymisées ou non sensibles. "
        "N'hésitez pas à me dire ce que vous en pensez."
    )


def _setting(name):
    value = os.environ.get(name)
    if value:
        return value
    try:
        import tomllib
        with open(".streamlit/secrets.toml", "rb") as f:
            return tomllib.load(f).get(name)
    except Exception:
        return None


def db():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    url, key = _setting("SUPABASE_URL"), _setting("SUPABASE_SERVICE_KEY")
    if not (url and key):
        sys.exit("SUPABASE_URL / SUPABASE_SERVICE_KEY not found (environment, .env or .streamlit/secrets.toml).")
    if not valid_supabase_url(url):
        sys.exit(f"Bad SUPABASE_URL: {url}\n{URL_HELP}")
    from supabase import create_client
    return create_client(url, key)


def cmd_check(_args):
    d = db()
    ok = True
    for table in ("usage_ledger", "invite_codes", "access_requests"):
        try:
            r = d.table(table).select("*", count="exact").limit(1).execute()
            rows(r)
            print(f"OK    {table}: {r.count} rows")
        except Exception as e:
            ok = False
            print(f"FAIL  {table}: {e}")
    sys.exit(0 if ok else 1)


def cmd_new(args):
    d = db()
    label, contact, lang = args.label, args.contact, args.lang
    if args.request:
        found = rows(d.table("access_requests").select("*").eq("id", args.request).execute())
        if not found:
            sys.exit(f"Request #{args.request} not found.")
        req = found[0]
        who = req["name"]
        label = label or (f"{req['firm']} ({who})" if req.get("firm") else who)
        contact = contact or req.get("email") or req.get("phone")
        lang = lang or req.get("lang") or "fr"
    if not label:
        sys.exit("Give a label (firm or person) or --request ID.")
    lang = lang or "fr"
    expires = datetime.now(timezone.utc) + timedelta(days=args.days)

    last = None
    for _ in range(5):
        code = new_code()
        try:
            res = d.table("invite_codes").insert({
                "code": code, "label": label, "contact": contact,
                "quota": args.docs, "expires_at": expires.isoformat(),
            }).execute()
        except Exception as e:  # primary-key collision is astronomically unlikely, retry anyway
            last = e
            continue
        if not rows(res):
            sys.exit("The code was not stored (empty response from Supabase). Run: python admin.py check")
        break
    else:
        sys.exit(f"Could not create a code: {last}")

    # Read it back: never hand out a code that is not really in the database.
    if not rows(d.table("invite_codes").select("code").eq("code", code).execute()):
        sys.exit("The code could not be read back from Supabase. Run: python admin.py check")

    if args.request:
        d.table("access_requests").update({"status": "sent"}).eq("id", args.request).execute()

    url = _setting("APP_URL") or "https://<your-app>.streamlit.app"
    print(f"\nCode {code} created for {label}: {args.docs} documents, valid until {expires:%Y-%m-%d}.\n")
    print("--- message to send (WhatsApp / email) ---\n")
    print(invite_message(lang, label, code, args.docs, expires, url))
    print()


def cmd_list(_args):
    d = db()
    codes = rows(d.table("invite_codes").select("*").order("created_at", desc=True).execute())
    used = Counter(r["code"] for r in rows(d.table("usage_ledger").select("code").eq("tier", "sponsored").execute()))
    if not codes:
        print("No codes yet.")
        return
    print(f"{'CODE':<12} {'USED/QUOTA':<11} {'EXPIRES':<11} {'STATE':<8} LABEL")
    now = datetime.now(timezone.utc)
    for c in codes:
        exp = c.get("expires_at")
        exp_dt = datetime.strptime(exp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc) if exp else None
        state = "revoked" if not c["active"] else ("expired" if exp_dt and exp_dt < now else "active")
        print(f"{c['code']:<12} {used[c['code']]}/{c['quota']:<9} {(exp or '-')[:10]:<11} {state:<8} {c['label']}")


def cmd_requests(_args):
    found = rows(db().table("access_requests").select("*").eq("status", "new").order("created_at").execute())
    if not found:
        print("No new requests.")
        return
    for r in found:
        print(f"#{r['id']}  {r['created_at'][:16]}  {r['name']}"
              + (f" - {r['firm']}" if r.get("firm") else "")
              + f"  [{r.get('lang') or '?'}]")
        print(f"     contact: {r.get('email') or '-'}  /  {r.get('phone') or '-'}")
        if r.get("message"):
            print(f"     message: {r['message']}")
    print("\nNext: python admin.py new --request <id> --docs 5")


def cmd_add_docs(args):
    d = db()
    code = args.code.strip().upper()
    found = rows(d.table("invite_codes").select("quota").eq("code", code).execute())
    if not found:
        sys.exit("Unknown code.")
    new_quota = found[0]["quota"] + args.n
    d.table("invite_codes").update({"quota": new_quota}).eq("code", code).execute()
    print(f"{code}: quota is now {new_quota}.")


def cmd_revoke(args):
    code = args.code.strip().upper()
    if not rows(db().table("invite_codes").update({"active": False}).eq("code", code).execute()):
        sys.exit("Unknown code.")
    print(f"{code} revoked.")


def cmd_mark(args):
    if not rows(db().table("access_requests").update({"status": args.status}).eq("id", args.id).execute()):
        sys.exit(f"Request #{args.id} not found.")
    print(f"Request #{args.id} marked {args.status}.")


def main():
    p = argparse.ArgumentParser(description="Manage invite codes and access requests.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="verify the Supabase connection and tables").set_defaults(fn=cmd_check)

    n = sub.add_parser("new", help="create an invite code")
    n.add_argument("label", nargs="?", help="firm or person (optional with --request)")
    n.add_argument("--request", type=int, help="access request id to fulfil")
    n.add_argument("--contact", help="email or phone you will send it to")
    n.add_argument("--docs", type=int, default=5)
    n.add_argument("--days", type=int, default=14)
    n.add_argument("--lang", choices=["fr", "en"])
    n.set_defaults(fn=cmd_new)

    sub.add_parser("list", help="list codes and usage").set_defaults(fn=cmd_list)
    sub.add_parser("requests", help="show new access requests").set_defaults(fn=cmd_requests)

    a = sub.add_parser("add-docs", help="add documents to a code")
    a.add_argument("code")
    a.add_argument("n", type=int)
    a.set_defaults(fn=cmd_add_docs)

    r = sub.add_parser("revoke", help="disable a code")
    r.add_argument("code")
    r.set_defaults(fn=cmd_revoke)

    m = sub.add_parser("mark", help="set a request status")
    m.add_argument("id", type=int)
    m.add_argument("status", choices=["sent", "declined"])
    m.set_defaults(fn=cmd_mark)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
