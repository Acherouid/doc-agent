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

st.set_page_config(page_title="doc-agent: invoice processing", layout="wide")

# ---------- Sidebar: explain the pipeline ----------
with st.sidebar:
    st.header("How it works")
    st.markdown(
        "1. **Extract**: the LLM reads the invoice and returns structured fields.\n"
        "2. **Validate**: code checks HT + TVA = TTC, the ICE format (15 digits) and the invoice number.\n"
        "3. **Retry**: errors go back to the LLM once, so it can correct itself.\n"
        "4. **Route**: clean → auto-approved. Still failing → human review."
    )
    st.markdown(f"[Source code on GitHub]({REPO_URL})")
    st.caption("Test set: 12 synthetic Moroccan invoices (3 deliberately faulty).")

st.title("Invoice-processing agent")
st.caption("Extract → validate → retry → approve, or send to a human")

# ---------- Input ----------
samples = sorted(glob.glob("data/invoices/*.pdf"))
choice = st.selectbox("Choose a sample invoice", samples, format_func=os.path.basename)
upload = st.file_uploader(
    f"…or upload your own PDF (max {MAX_PAGES} pages, {MAX_MB} MB)", type=["pdf"]
)


def safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def text_from_upload(file) -> str:
    raw = file.getvalue()
    if len(raw) > MAX_MB * 1024 * 1024:
        raise ValueError(f"File is larger than {MAX_MB} MB.")
    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        if doc.page_count > MAX_PAGES:
            raise ValueError(f"PDF has {doc.page_count} pages; the limit is {MAX_PAGES}.")
        return "\n".join(page.get_text() for page in doc)


if st.button("Process", type="primary"):
    try:
        if upload is not None:
            text, source = text_from_upload(upload), upload.name
        else:
            text, source = pdf_to_text(choice), os.path.basename(choice)

        if len(text.strip()) < 50:
            st.warning("Almost no text found. This looks like a scanned PDF; OCR is not in this version yet.")
            st.stop()

        with st.spinner("Agent running…"):
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
    except Exception as e:  # API errors, rate limits, etc.
        st.error(f"The agent failed: {e}")
        st.stop()

# ---------- Output + human review ----------
r = st.session_state.get("result")
if r:
    st.divider()
    st.subheader(f"Result: {r['source']}")

    if r["status"] == "auto_approved":
        st.success("✅ auto_approved: all checks passed")
    else:
        st.warning("⚠️ needs_human_review: the agent is not confident, so a person must check it")

    c1, c2 = st.columns(2)
    c1.metric("Attempts", r["attempts"])
    c2.metric("Processing time", f"{r['seconds']:.1f} s")

    if r["errors"]:
        st.markdown("**Validation errors**")
        for err in r["errors"]:
            st.markdown(f"- {err}")

    d, k = r["data"], r["source"]  # k makes widget keys unique per invoice
    with st.form(f"review_{k}"):
        st.markdown("**Extracted fields** (editable)")
        col1, col2 = st.columns(2)
        supplier = col1.text_input("Supplier", d.get("supplier") or "", key=f"sup_{k}")
        ice = col2.text_input("ICE", d.get("ice") or "", key=f"ice_{k}")
        number = col1.text_input("Invoice number", d.get("invoice_number") or "", key=f"num_{k}")
        date = col2.text_input("Date (YYYY-MM-DD)", d.get("date") or "", key=f"date_{k}")
        ht = col1.number_input("Total HT", value=safe_float(d.get("total_ht")), format="%.2f", key=f"ht_{k}")
        tva = col2.number_input("TVA", value=safe_float(d.get("tva")), format="%.2f", key=f"tva_{k}")
        ttc = col1.number_input("Total TTC", value=safe_float(d.get("total_ttc")), format="%.2f", key=f"ttc_{k}")
        currency = col2.text_input("Currency", d.get("currency") or "MAD", key=f"cur_{k}")

        label = "Approve corrected data" if r["status"] != "auto_approved" else "Confirm data"
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
        st.success("Saved to results/approved.jsonl")
        del st.session_state["result"]