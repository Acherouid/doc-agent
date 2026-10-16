import glob
import json
import os
import time
from datetime import datetime, timezone

import pymupdf
import streamlit as st

from agent import app as agent_app, pdf_to_text

REPO_URL = "https://github.com/Acherouid/doc-agent"
MAX_MB, MAX_PAGES = 2, 2
os.makedirs("results", exist_ok=True)

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
        "choose": "Choisissez une facture d'exemple",
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
        "saved": "Enregistré dans results/approved.jsonl",
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
        "choose": "Choose a sample invoice",
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
        "saved": "Saved to results/approved.jsonl",
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


def safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def text_from_upload(file) -> str:
    raw = file.getvalue()
    if len(raw) > MAX_MB * 1024 * 1024:
        raise ValueError(t["too_big"])
    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        if doc.page_count > MAX_PAGES:
            raise ValueError(t["too_many_pages"].format(n=doc.page_count))
        return "\n".join(page.get_text() for page in doc)


if st.button(t["process"], type="primary"):
    try:
        if upload is not None:
            text, source = text_from_upload(upload), upload.name
        else:
            text, source = pdf_to_text(choice), os.path.basename(choice)

        if len(text.strip()) < 50:
            st.warning(t["no_text"])
            st.stop()

        with st.spinner(t["spinner"]):
            t0 = time.perf_counter()
            result = agent_app.invoke({"text": text})
            seconds = time.perf_counter() - t0

        st.session_state["result"] = {
            "source": source,
            "status": result.get("status"),
            "attempts": result.get("attempts"),
            "errors": result.get("errors", []),
            "data": result.get("data") or {},
            "seconds": seconds,
        }
    except ValueError as e:
        st.error(str(e))
        st.stop()
    except Exception as e:
        st.error(f"{t['failed']}{e}")
        st.stop()

r = st.session_state.get("result")
if r:
    st.divider()
    st.subheader(f"{t['result']}{r['source']}")

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
        record = {
            "source": k,
            "agent_status": r["status"],
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "data": {
                "supplier": supplier, "ice": ice or None, "invoice_number": number or None,
                "date": date, "total_ht": ht, "tva": tva, "total_ttc": ttc, "currency": currency,
            },
        }
        with open("results/approved.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        st.success(t["saved"])
        del st.session_state["result"]