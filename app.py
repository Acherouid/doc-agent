import glob
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import pymupdf
import streamlit as st

import funnel
from agent import GEMINI_BASE_URL, make_client, pdf_to_text, run

REPO_URL = "https://github.com/Acherouid/doc-agent"
MAX_MB, MAX_PAGES = 2, 2

TEXT = {
    "fr": {
        "page_title": "doc-agent : traitement de factures",
        "how": "Comment ça marche",
        "steps": (
            "1. **Extraction** : le modèle lit la facture et renvoie des champs structurés.\n"
            "2. **Contrôle** : le code vérifie HT + TVA = TTC, le format de l'ICE (15 chiffres) et le numéro de facture.\n"
            "3. **Nouvel essai** : les erreurs sont renvoyées une fois au modèle pour qu'il se corrige.\n"
            "4. **Aiguillage** : facture conforme → approuvée automatiquement. Toujours en erreur → vérification humaine."
        ),
        "code": "Code source sur GitHub",
        "testset": "Jeu de test : 12 factures marocaines synthétiques (dont 3 volontairement erronées).",
        "title": "Agent de traitement de factures",
        "caption": "Extraire → contrôler → réessayer → approuver, ou transmettre à une personne",
        "choose": "Choisissez une facture d'exemple (résultat instantané)",
        "upload": f"…ou téléversez votre propre PDF (max {MAX_PAGES} pages, {MAX_MB} Mo)",
        "process": "Traiter la facture",
        "too_big": f"Le fichier dépasse {MAX_MB} Mo.",
        "too_many_pages": "Le PDF compte {n} pages ; la limite est de " + str(MAX_PAGES) + ".",
        "no_text": "Presque aucun texte trouvé. Il s'agit probablement d'un PDF scanné ; l'OCR n'est pas encore dans cette version.",
        "spinner": "Agent en cours d'exécution…",
        "failed": "L'agent a échoué : ",
        "result": "Résultat : ",
        "ok": "✅ auto_approved : tous les contrôles sont passés",
        "review": "⚠️ needs_human_review : l'agent n'est pas sûr de lui, une personne doit vérifier",
        "attempts": "Tentatives",
        "time": "Temps de traitement",
        "errors": "Erreurs de contrôle",
        "fields": "Champs extraits (modifiables)",
        "supplier": "Fournisseur",
        "number": "Numéro de facture",
        "date": "Date (AAAA-MM-JJ)",
        "currency": "Devise",
        "approve": "Valider les données corrigées",
        "confirm": "Confirmer les données",
        "saved": "Données validées. Téléchargez-les ci-dessous.",
        "download": "Télécharger (JSON)",
        "own_title": "Vos propres factures",
        "own_help": "Comment l'analyse de votre PDF est-elle payée ?",
        "mode_byok": "Ma clé Gemini (gratuite)",
        "mode_code": "Code d'invitation",
        "key_label": "Votre clé API Gemini",
        "key_help": "Clé gratuite en 2 minutes sur Google AI Studio. Elle reste dans votre session et n'est jamais enregistrée.",
        "code_label": "Code d'invitation",
        "privacy": (
            "⚠️ Le texte de la facture est envoyé à l'API Gemini de Google. Utilisez des factures anonymisées "
            "ou non sensibles : avec une clé gratuite, Google peut réutiliser les données. Le fichier n'est pas "
            "conservé sur ce serveur (seuls un compteur d'usage et l'empreinte du fichier sont enregistrés)."
        ),
        "need_key": "Saisissez votre clé API Gemini.",
        "need_code": "Saisissez votre code d'invitation.",
        "invalid_code": "Code d'invitation invalide.",
        "quota_exhausted": "Ce code a utilisé tous ses documents. Utilisez votre propre clé Gemini ou contactez-moi.",
        "daily_cap": "La limite quotidienne de la démo est atteinte. Réessayez demain ou utilisez votre propre clé Gemini.",
        "ledger_off": "Les codes d'invitation ne sont pas activés sur cette instance. Utilisez votre propre clé Gemini.",
        "remaining": "Documents restants avec ce code : {n}",
        "precomputed": "Résultat pré-calculé pour cet exemple (aucun appel API en direct).",
        "no_cache": "Résultat pré-calculé introuvable pour cet exemple.",
        "auth_failed": "La clé API a été refusée. Vérifiez-la.",
        "rate_limited": "Quota de la clé atteint. Réessayez dans une minute.",
        "code_expired": "Ce code a expiré. Demandez-en un nouveau ci-dessous.",
        "too_many_attempts": "Trop de codes invalides. Rechargez la page ou contactez-moi.",
        "db_error": "Le service de codes d'invitation est momentanément indisponible. Utilisez votre propre clé Gemini ou réessayez plus tard.",
        "no_code": "Pas encore de code ? Demandez-en un",
        "req_intro": "Laissez vos coordonnées : je vous envoie un code d'essai avec quelques factures offertes, généralement sous 24 h.",
        "req_name": "Nom",
        "req_firm": "Cabinet / société (facultatif)",
        "req_email": "Email",
        "req_phone": "WhatsApp / téléphone",
        "req_message": "Message (facultatif)",
        "req_submit": "Demander un code",
        "req_need_name": "Indiquez votre nom.",
        "req_need_contact": "Indiquez un email ou un numéro WhatsApp.",
        "req_bad_email": "Adresse email invalide.",
        "req_failed": "Envoi impossible pour le moment. Écrivez-moi sur WhatsApp.",
        "req_sent": "Merci ! Je vous envoie votre code dès que possible, généralement sous 24 h.",
        "req_wa": "Ou écrivez-moi directement sur WhatsApp",
        "req_wa_msg": "Bonjour, je souhaite un code d'essai pour la démo doc-agent.",
    },
    "en": {
        "page_title": "doc-agent: invoice processing",
        "how": "How it works",
        "steps": (
            "1. **Extract**: the LLM reads the invoice and returns structured fields.\n"
            "2. **Validate**: code checks HT + TVA = TTC, the ICE format (15 digits) and the invoice number.\n"
            "3. **Retry**: errors go back to the LLM once, so it can correct itself.\n"
            "4. **Route**: clean → auto-approved. Still failing → human review."
        ),
        "code": "Source code on GitHub",
        "testset": "Test set: 12 synthetic Moroccan invoices (3 deliberately faulty).",
        "title": "Invoice-processing agent",
        "caption": "Extract → validate → retry → approve, or send to a human",
        "choose": "Choose a sample invoice (instant result)",
        "upload": f"…or upload your own PDF (max {MAX_PAGES} pages, {MAX_MB} MB)",
        "process": "Process",
        "too_big": f"File is larger than {MAX_MB} MB.",
        "too_many_pages": "PDF has {n} pages; the limit is " + str(MAX_PAGES) + ".",
        "no_text": "Almost no text found. This looks like a scanned PDF; OCR is not in this version yet.",
        "spinner": "Agent running…",
        "failed": "The agent failed: ",
        "result": "Result: ",
        "ok": "✅ auto_approved: all checks passed",
        "review": "⚠️ needs_human_review: the agent is not confident, so a person must check it",
        "attempts": "Attempts",
        "time": "Processing time",
        "errors": "Validation errors",
        "fields": "Extracted fields (editable)",
        "supplier": "Supplier",
        "number": "Invoice number",
        "date": "Date (YYYY-MM-DD)",
        "currency": "Currency",
        "approve": "Approve corrected data",
        "confirm": "Confirm data",
        "saved": "Data approved. Download it below.",
        "download": "Download (JSON)",
        "own_title": "Your own invoices",
        "own_help": "How is the analysis of your PDF paid for?",
        "mode_byok": "My Gemini key (free)",
        "mode_code": "Invite code",
        "key_label": "Your Gemini API key",
        "key_help": "Free key in 2 minutes from Google AI Studio. It stays in your session and is never stored.",
        "code_label": "Invite code",
        "privacy": (
            "⚠️ The invoice text is sent to Google's Gemini API. Use anonymized or non-sensitive invoices: "
            "with a free key, Google may reuse the data. The file is not kept on this server "
            "(only a usage counter and the file's fingerprint are recorded)."
        ),
        "need_key": "Enter your Gemini API key.",
        "need_code": "Enter your invite code.",
        "invalid_code": "Invalid invite code.",
        "quota_exhausted": "This code has used all its documents. Use your own Gemini key or contact me.",
        "daily_cap": "The demo's daily limit has been reached. Try again tomorrow or use your own Gemini key.",
        "ledger_off": "Invite codes are not enabled on this instance. Use your own Gemini key.",
        "remaining": "Documents left with this code: {n}",
        "precomputed": "Pre-computed result for this sample (no live API call).",
        "no_cache": "Pre-computed result not found for this sample.",
        "auth_failed": "The API key was rejected. Please check it.",
        "rate_limited": "The key's quota is reached. Try again in a minute.",
        "code_expired": "This code has expired. Request a new one below.",
        "too_many_attempts": "Too many invalid codes. Reload the page or contact me.",
        "db_error": "The invite-code service is temporarily unavailable. Use your own Gemini key or try again later.",
        "no_code": "No code yet? Request one",
        "req_intro": "Leave your details and I'll send you a trial code with a few free invoices, usually within 24 hours.",
        "req_name": "Name",
        "req_firm": "Firm / company (optional)",
        "req_email": "Email",
        "req_phone": "WhatsApp / phone",
        "req_message": "Message (optional)",
        "req_submit": "Request a code",
        "req_need_name": "Please enter your name.",
        "req_need_contact": "Please enter an email or a WhatsApp number.",
        "req_bad_email": "Invalid email address.",
        "req_failed": "Could not send right now. Message me on WhatsApp.",
        "req_sent": "Thank you! I'll send your code as soon as possible, usually within 24 hours.",
        "req_wa": "Or message me directly on WhatsApp",
        "req_wa_msg": "Hello, I would like a trial code for the doc-agent demo.",
    },
}

st.set_page_config(page_title="doc-agent", layout="wide")

with st.sidebar:
    lang = st.radio("Langue / Language", ["fr", "en"], format_func=lambda x: {"fr": "Français", "en": "English"}[x])
    t = TEXT[lang]
    st.header(t["how"])
    st.markdown(t["steps"])
    st.markdown(f"[{t['code']}]({REPO_URL})")
    st.caption(t["testset"])

st.title(t["title"])
st.caption(t["caption"])

samples = sorted(glob.glob("data/invoices/*.pdf"))
choice = st.selectbox(t["choose"], samples, format_func=os.path.basename)
upload = st.file_uploader(t["upload"], type=["pdf"])

def request_access_ui():
    """'Request a code' form (stored in Supabase) plus an optional WhatsApp shortcut."""
    st.caption(t["req_intro"])
    if funnel.ledger_enabled():
        if st.session_state.get("req_sent"):
            st.success(t["req_sent"])
        else:
            with st.form("access_request"):
                name = st.text_input(t["req_name"])
                firm = st.text_input(t["req_firm"])
                email = st.text_input(t["req_email"])
                phone = st.text_input(t["req_phone"])
                message = st.text_area(t["req_message"], max_chars=500)
                send = st.form_submit_button(t["req_submit"])
            if send:
                err = funnel.submit_access_request(name, firm, email, phone, message, lang)
                if err:
                    st.error(t[err])
                else:
                    st.session_state["req_sent"] = True
                    st.rerun()
    wa = "".join(ch for ch in str(funnel.secret("CONTACT_WHATSAPP") or "") if ch.isdigit())
    if wa:
        st.markdown(f"[💬 {t['req_wa']}](https://wa.me/{wa}?text={quote(t['req_wa_msg'])})")


# Access for uploaded PDFs: visitor's own key, or a sponsored invite code.
mode = api_key = code = None
if upload is not None:
    st.markdown(f"**{t['own_title']}**")
    mode = st.radio(t["own_help"], ["byok", "code"], format_func=lambda m: t[f"mode_{m}"], horizontal=True)
    if mode == "byok":
        api_key = st.text_input(t["key_label"], type="password", help=t["key_help"])
    else:
        code = st.text_input(t["code_label"])
        if code.strip():
            try:
                err, remaining = funnel.check_sponsored(code)
                if err is None:
                    st.caption(t["remaining"].format(n=remaining))
            except Exception:
                pass  # a lookup problem must not break the page; the Process button re-checks
        with st.expander(t["no_code"]):
            request_access_ui()
    st.warning(t["privacy"])


def safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def text_from_bytes(raw: bytes) -> str:
    if len(raw) > MAX_MB * 1024 * 1024:
        raise ValueError(t["too_big"])
    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        if doc.page_count > MAX_PAGES:
            raise ValueError(t["too_many_pages"].format(n=doc.page_count))
        return "\n".join(page.get_text() for page in doc)


def llm_for_upload():
    """Returns (client, model, tier, code) or raises ValueError with a translated message."""
    if mode == "byok":
        key = (api_key or "").strip()
        if not key:
            raise ValueError(t["need_key"])
        model = funnel.secret("BYOK_MODEL") or funnel.secret("LLM_MODEL")
        return make_client(key, GEMINI_BASE_URL), model, "byok", None
    if not (code or "").strip():
        raise ValueError(t["need_code"])
    if st.session_state.get("bad_codes", 0) >= 5:
        raise ValueError(t["too_many_attempts"])
    try:
        err, _ = funnel.check_sponsored(code)
    except Exception as e:
        print(f"[funnel] check_sponsored failed: {e}", file=sys.stderr)  # details go to the server logs only
        raise ValueError(t["db_error"])
    if err:
        if err == "invalid_code":
            st.session_state["bad_codes"] = st.session_state.get("bad_codes", 0) + 1
        raise ValueError(t[err])
    client = make_client(funnel.secret("LLM_API_KEY"), funnel.secret("LLM_BASE_URL"))
    return client, funnel.secret("LLM_MODEL"), "sponsored", code


def friendly_error(e: Exception) -> str:
    msg = str(e)
    key = (api_key or "").strip()
    if key:
        msg = msg.replace(key, "***")
    status = getattr(e, "status_code", None)
    if status == 429:
        return t["rate_limited"]
    if status in (400, 401, 403) and "key" in msg.lower():
        return t["auth_failed"]
    return f"{t['failed']}{msg[:300]}"


if st.button(t["process"], type="primary"):
    st.session_state.pop("approved", None)
    try:
        if upload is not None:
            client, model, tier, used_code = llm_for_upload()
            raw = upload.getvalue()
            text = text_from_bytes(raw)
            if len(text.strip()) < 50:
                st.warning(t["no_text"])
                st.stop()

            doc_hash = funnel.sha256_bytes(raw)
            with st.spinner(t["spinner"]):
                t0 = time.perf_counter()
                try:
                    result = run(text, client, model)
                except Exception:
                    funnel.log_usage(tier, used_code, doc_hash, 0, "error")
                    raise
                seconds = time.perf_counter() - t0
            funnel.log_usage(tier, used_code, doc_hash, result.get("attempts") or 0, result.get("status"))

            st.session_state["result"] = {
                "source": upload.name,
                "status": result.get("status"),
                "attempts": result.get("attempts"),
                "errors": result.get("errors", []),
                "data": result.get("data") or {},
                "seconds": seconds,
                "precomputed": False,
            }
        else:
            name = os.path.basename(choice)
            entry = funnel.load_sample_cache().get(name)
            if not entry or entry.get("sha256") != funnel.sha256_file(choice):
                raise ValueError(t["no_cache"])
            with st.spinner(t["spinner"]):
                time.sleep(1.0)  # short pause so the replay feels like a run; the notice below says it is pre-computed
            st.session_state["result"] = {**entry["result"], "source": name, "precomputed": True}
    except ValueError as e:
        st.error(str(e))
        st.stop()
    except Exception as e:
        st.error(friendly_error(e))
        st.stop()

r = st.session_state.get("result")
if r:
    st.divider()
    st.subheader(f"{t['result']}{r['source']}")
    if r.get("precomputed"):
        st.info(t["precomputed"])

    if r["status"] == "auto_approved":
        st.success(t["ok"])
    else:
        st.warning(t["review"])

    c1, c2 = st.columns(2)
    c1.metric(t["attempts"], r["attempts"])
    c2.metric(t["time"], f"{r['seconds']:.1f} s")

    if r["errors"]:
        st.markdown(f"**{t['errors']}**")
        for err in r["errors"]:
            st.markdown(f"- {err}")

    d, k = r["data"], r["source"]
    with st.form(f"review_{k}"):
        st.markdown(f"**{t['fields']}**")
        col1, col2 = st.columns(2)
        supplier = col1.text_input(t["supplier"], d.get("supplier") or "", key=f"sup_{k}")
        ice = col2.text_input("ICE", d.get("ice") or "", key=f"ice_{k}")
        number = col1.text_input(t["number"], d.get("invoice_number") or "", key=f"num_{k}")
        date = col2.text_input(t["date"], d.get("date") or "", key=f"date_{k}")
        ht = col1.number_input("Total HT", value=safe_float(d.get("total_ht")), format="%.2f", key=f"ht_{k}")
        tva = col2.number_input("TVA", value=safe_float(d.get("tva")), format="%.2f", key=f"tva_{k}")
        ttc = col1.number_input("Total TTC", value=safe_float(d.get("total_ttc")), format="%.2f", key=f"ttc_{k}")
        currency = col2.text_input(t["currency"], d.get("currency") or "MAD", key=f"cur_{k}")

        label = t["approve"] if r["status"] != "auto_approved" else t["confirm"]
        submitted = st.form_submit_button(label)

    if submitted:
        st.session_state["approved"] = {
            "source": k,
            "agent_status": r["status"],
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "data": {
                "supplier": supplier, "ice": ice or None, "invoice_number": number or None,
                "date": date, "total_ht": ht, "tva": tva, "total_ttc": ttc, "currency": currency,
            },
        }

    approved = st.session_state.get("approved")
    if approved and approved["source"] == k:
        st.success(t["saved"])
        st.download_button(
            t["download"],
            json.dumps(approved, ensure_ascii=False, indent=2),
            file_name=f"{os.path.splitext(k)[0]}.json",
            mime="application/json",
        )
