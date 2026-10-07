import json, os, re, sys
from typing import Optional, TypedDict
import pymupdf
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError, field_validator
from langgraph.graph import StateGraph, START, END

# load_dotenv()
# client = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])
# MODEL = os.environ["LLM_MODEL"]
# MAX_ATTEMPTS = 2

load_dotenv(override=True)
BASE_URL = os.environ["LLM_BASE_URL"]
client = OpenAI(base_url=BASE_URL, api_key=os.environ["LLM_API_KEY"])
MODEL = os.environ["LLM_MODEL"]
MAX_ATTEMPTS = 2
if "googleapis.com" in BASE_URL:
    PROVIDER = "gemini"
elif "11434" in BASE_URL or "localhost" in BASE_URL:
    PROVIDER = "ollama"
else:
    PROVIDER = "other"
print(f"[LLM] provider={PROVIDER} model={MODEL} url={BASE_URL}", file=sys.stderr)



class Invoice(BaseModel):
    supplier: str
    ice: Optional[str] = None
    invoice_number: Optional[str] = None
    date: str
    total_ht: float
    tva: float
    total_ttc: float
    currency: str = "MAD"

class State(TypedDict, total=False):
    text: str
    data: dict
    errors: list[str]
    attempts: int
    status: str

# PROMPT = """You extract data from invoices (French or English).
# Return ONLY a JSON object with keys: supplier, ice, invoice_number, date (YYYY-MM-DD),
# total_ht, tva, total_ttc, currency. Use null if a field is absent. Numbers without spaces or currency symbols.
# {feedback}
# INVOICE TEXT:
# {text}"""


PROMPT = """You extract data from invoices (French or English).
Return ONLY a JSON object with keys: supplier, ice, invoice_number, date, total_ht, tva, total_ttc, currency.

Formatting:
- date: YYYY-MM-DD (printed dates are DD/MM/YYYY).
- total_ht, tva, total_ttc: JSON numbers with a dot as decimal separator, no spaces, no currency symbol
  (the printed "7 826,50 MAD" becomes 7826.50).
- ice: the SUPPLIER's (issuer's) ICE, not the client's ("Facturé à") ICE.

Rules:
- Apart from that formatting, copy every value EXACTLY as printed. Never calculate, correct, complete or guess a value.
- If the invoice contains an inconsistency (totals that don't add up, an ICE with the wrong number of digits,
  a missing invoice number), still report what is printed. Another system checks it.
- Use null only if the field is truly absent from the text.
{feedback}
INVOICE TEXT:
{text}"""

@field_validator("total_ht", "tva", "total_ttc", mode="before")
@classmethod
def parse_french_amount(cls, v):
    if isinstance(v, str):
        cleaned = v.replace("MAD", "").replace("DH", "").strip()
        parsed = _parse_amount(cleaned)   # defined further down in the file, that's fine at runtime
        return parsed if parsed is not None else v
    return v

def pdf_to_text(path: str) -> str:
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)

def extract(state: State) -> State:
    feedback = ""
    if state.get("errors"):
        # feedback = "Your previous answer had these problems: " + "; ".join(state["errors"]) + ". Re-read the invoice and fix them."
        feedback = (
    "A validation check flagged these issues: " + "; ".join(state["errors"]) + ". "
    "Re-read the invoice text. Change a value ONLY if you misread it. "
    "If the invoice itself prints these values, return exactly what is printed, even if it looks wrong."
)
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},  # remove if your provider rejects it
        messages=[{"role": "user", "content": PROMPT.format(feedback=feedback, text=state["text"][:12000])}],
    )
    raw = resp.choices[0].message.content
    try:
        data = Invoice(**json.loads(raw)).model_dump()
        errors = []
    except (json.JSONDecodeError, ValidationError) as e:
        data, errors = {}, [f"invalid output: {str(e)[:200]}"]
    return {"data": data, "errors": errors, "attempts": state.get("attempts", 0) + 1}

def _compact(s: str) -> str:
    return re.sub(r"[\s\u00a0]", "", s).casefold()

def _parse_amount(tok: str):
    t = tok.replace("\u00a0", "").replace(" ", "")
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in t:
        t = t.replace(",", ".") if len(t.split(",")[-1]) <= 2 else t.replace(",", "")
    elif "." in t and len(t.split(".")[-1]) == 3:
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None

def _amounts_in(text: str) -> list[float]:
    toks = re.findall(r"\d[\d \u00a0.,]*\d|\d", text)
    return [v for v in (_parse_amount(t) for t in toks) if v is not None]

def validate(state: State) -> State:
    d, errors = state.get("data") or {}, list(state.get("errors", []))
    if d:
        if abs(d["total_ht"] + d["tva"] - d["total_ttc"]) > 0.05:
            errors.append("total_ht + tva does not equal total_ttc")
        if d.get("ice") and not re.fullmatch(r"\d{15}", d["ice"]):
            errors.append("ICE must be exactly 15 digits")
        if not d.get("invoice_number"):
            errors.append("invoice_number is missing")

        text = state.get("text", "")
        for f in ("ice", "invoice_number"):
            if d.get(f) and _compact(str(d[f])) not in _compact(text):
                errors.append(f"{f} '{d[f]}' does not appear in the invoice text")
        amounts = _amounts_in(text)
        for f in ("total_ht", "tva", "total_ttc"):
            if not any(abs(a - d[f]) <= 0.05 for a in amounts):
                errors.append(f"{f} {d[f]} does not appear in the invoice text")
    return {"errors": errors}

def route(state: State) -> str:
    if not state["errors"]:
        return "approve"
    return "extract" if state["attempts"] < MAX_ATTEMPTS else "review"

graph = StateGraph(State)
graph.add_node("extract", extract)
graph.add_node("validate", validate)
graph.add_node("approve", lambda s: {"status": "auto_approved"})
graph.add_node("review", lambda s: {"status": "needs_human_review"})
graph.add_edge(START, "extract")
graph.add_edge("extract", "validate")
graph.add_conditional_edges("validate", route, {"extract": "extract", "approve": "approve", "review": "review"})
graph.add_edge("approve", END)
graph.add_edge("review", END)
app = graph.compile()

if __name__ == "__main__":
    import sys
    result = app.invoke({"text": pdf_to_text(sys.argv[1])})
    print(json.dumps({"llm": f"{PROVIDER}/{MODEL}", **{k: result.get(k) for k in ("status", "attempts", "errors", "data")}}, indent=2, ensure_ascii=False))