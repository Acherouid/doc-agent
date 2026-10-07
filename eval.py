"""Evaluate the invoice agent against data/ground_truth.json.

Usage:
    python eval.py                       # all PDFs in data/invoices
    python eval.py --delay 15            # wait 15 s between invoices (free-tier friendly)
    python eval.py --resume              # keep successful rows from the output file, re-run only failed ones
    python eval.py --out results/eval_v2.json
"""
import argparse
import json
import time
from pathlib import Path

import openai

import agent  # loads .env, builds the LangGraph app

INVOICE_DIR = Path("data/invoices")
GROUND_TRUTH = Path("data/ground_truth.json")
FIELDS = ["supplier", "ice", "invoice_number", "date", "total_ht", "tva", "total_ttc", "currency"]
TOLERANCE = 0.05

FAULTY_KEYS = ("faulty", "is_faulty", "has_error", "has_errors", "defective", "should_review", "expected_error")
WRAPPER_KEYS = ("expected", "fields", "data", "truth")
META_KEYS = set(FAULTY_KEYS) | set(WRAPPER_KEYS) | {"file", "filename", "name", "notes", "note", "defect", "defects"}

RETRYABLE = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
    openai.APITimeoutError,
)


# ---------- ground truth loading ----------
def load_ground_truth(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        out = {}
        for entry in raw:
            name = entry.get("file") or entry.get("filename") or entry.get("name")
            if name:
                out[Path(name).stem] = entry
        return out
    return {Path(k).stem: v for k, v in raw.items()}


def split_entry(entry: dict):
    """Return (expected_fields, is_faulty)."""
    is_faulty = any(bool(entry.get(k)) for k in FAULTY_KEYS)
    expected = next((entry[k] for k in WRAPPER_KEYS if isinstance(entry.get(k), dict)), None)
    if expected is None:
        expected = {k: v for k, v in entry.items() if k not in META_KEYS}
    expected = dict(expected)
    if "supplier_name" in expected and "supplier" not in expected:
        expected["supplier"] = expected["supplier_name"]
    return expected, is_faulty


# ---------- comparison ----------
def norm_str(x) -> str:
    return " ".join(str(x).split()).casefold()


def field_matches(expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - float(expected)) <= TOLERANCE
        except (TypeError, ValueError):
            return False
    return norm_str(expected) == norm_str(actual)


def compare(expected: dict, actual: dict):
    correct, total, wrong = 0, 0, []
    for f in FIELDS:
        if f not in expected:
            continue
        total += 1
        if field_matches(expected[f], (actual or {}).get(f)):
            correct += 1
        else:
            wrong.append({"field": f, "expected": expected[f], "got": (actual or {}).get(f)})
    return correct, total, wrong


# ---------- running the agent ----------
def invoke_with_retry(text: str, tries: int = 4, wait: int = 20):
    for i in range(tries):
        try:
            return agent.app.invoke({"text": text})
        except RETRYABLE as e:
            if i == tries - 1:
                raise
            pause = wait * (2 ** i)
            print(f"    {type(e).__name__}; retrying in {pause}s ({i + 1}/{tries - 1})")
            time.sleep(pause)


def run_one(pdf: Path, expected: dict, is_faulty: bool) -> dict:
    start = time.time()
    try:
        result = invoke_with_retry(agent.pdf_to_text(str(pdf)))
        status = result.get("status", "unknown")
        attempts = result.get("attempts")
        data = result.get("data") or {}
        errors = result.get("errors") or []
        crash = None
    except Exception as e:
        status, attempts, data, errors = "error", None, {}, []
        crash = f"{type(e).__name__}: {str(e)[:200]}"

    correct, total, wrong = compare(expected, data)
    evaluated = crash is None
    return {
        "file": pdf.name,
        "evaluated": evaluated,
        "status": status,
        "attempts": attempts,
        "fields_correct": correct if evaluated else None,
        "fields_total": total,
        "wrong_fields": wrong if evaluated else [],
        "agent_errors": errors,
        "crash": crash,
        "faulty": is_faulty,
        "routed_correctly": (status == "needs_human_review") if (is_faulty and evaluated) else None,
        "seconds": round(time.time() - start, 1),
    }


# ---------- main ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="results/eval_v1.json")
    parser.add_argument("--delay", type=float, default=8.0, help="seconds to pause between invoices")
    parser.add_argument("--resume", action="store_true", help="re-run only invoices that crashed in the --out file")
    args = parser.parse_args()
    out = Path(args.out)

    truth = load_ground_truth(GROUND_TRUTH)
    pdfs = sorted(INVOICE_DIR.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDFs found in {INVOICE_DIR}")

    done = {}
    if args.resume and out.exists():
        for r in json.loads(out.read_text(encoding="utf-8")).get("results", []):
            if r.get("evaluated"):
                done[r["file"]] = r
        print(f"Resuming: keeping {len(done)} successful rows from {out}")

    rows = []
    first_call = True
    for pdf in pdfs:
        entry = truth.get(pdf.stem)
        if entry is None:
            print(f"[skip] no ground truth for {pdf.name}")
            continue
        if pdf.name in done:
            rows.append(done[pdf.name])
            continue
        if not first_call:
            time.sleep(args.delay)
        first_call = False
        expected, is_faulty = split_entry(entry)
        row = run_one(pdf, expected, is_faulty)
        rows.append(row)
        print(f"  done {pdf.name} ({row['status']}, {row['seconds']}s)")
        if row["crash"]:
            print(f"    {row['crash'][:120]}")

    # ---------- table ----------
    w = max(len(r["file"]) for r in rows)
    print()
    print(f"{'file':<{w}} | {'status':<18} | {'correct':<9} | attempts")
    print("-" * (w + 45))
    for r in rows:
        frac = f"{r['fields_correct']}/{r['fields_total']}" if r["evaluated"] else "-"
        att = r["attempts"] if r["evaluated"] else "-"
        print(f"{r['file']:<{w}} | {r['status']:<18} | {frac:<9} | {att}")

    # ---------- summary (evaluated invoices only) ----------
    ok_rows = [r for r in rows if r["evaluated"]]
    crashed = [r for r in rows if not r["evaluated"]]
    total_correct = sum(r["fields_correct"] for r in ok_rows)
    total_fields = sum(r["fields_total"] for r in ok_rows)
    accuracy = 100 * total_correct / total_fields if total_fields else 0.0
    approved = sum(1 for r in ok_rows if r["status"] == "auto_approved")
    approved_pct = 100 * approved / len(ok_rows) if ok_rows else 0.0

    print()
    print(f"Evaluated invoices     : {len(ok_rows)}/{len(rows)}"
          + (f"  ({len(crashed)} crashed, NOT counted below)" if crashed else ""))
    print(f"Overall field accuracy : {accuracy:.1f}% ({total_correct}/{total_fields})")
    print(f"Auto-approved          : {approved_pct:.1f}% ({approved}/{len(ok_rows)})")

    faulty = [r for r in rows if r["faulty"]]
    print()
    if faulty:
        print("Faulty invoices routing:")
        for r in faulty:
            if not r["evaluated"]:
                print(f"  [N/A ] {r['file']} -> not evaluated (API error)")
            else:
                mark = "OK  " if r["routed_correctly"] else "MISS"
                print(f"  [{mark}] {r['file']} -> {r['status']}")
        ok = sum(1 for r in faulty if r["routed_correctly"])
        n_eval = sum(1 for r in faulty if r["evaluated"])
        print(f"  Correctly routed: {ok}/{n_eval} evaluated ({len(faulty)} faulty in total)")
    else:
        print("No invoice is flagged as faulty in ground_truth.json.")

    wrongly_flagged = [r for r in ok_rows if not r["faulty"] and r["status"] == "needs_human_review"]
    if wrongly_flagged:
        print(f"\nClean invoices sent to review unnecessarily: {len(wrongly_flagged)}")
        for r in wrongly_flagged:
            print(f"  {r['file']}")

    if crashed:
        print(f"\n{len(crashed)} invoice(s) hit API errors. Wait a few minutes (or until your quota resets), "
              f"then run: python eval.py --resume --out {out}")

    # ---------- save ----------
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "llm": f"{getattr(agent, 'PROVIDER', '?')}/{agent.MODEL}",
        "summary": {
            "invoices": len(rows),
            "evaluated": len(ok_rows),
            "crashed": len(crashed),
            "field_accuracy_pct": round(accuracy, 2),
            "fields_correct": total_correct,
            "fields_total": total_fields,
            "auto_approved_pct": round(approved_pct, 2),
            "faulty_total": len(faulty),
            "faulty_evaluated": sum(1 for r in faulty if r["evaluated"]),
            "faulty_routed_correctly": sum(1 for r in faulty if r["routed_correctly"]),
            "clean_sent_to_review": len(wrongly_flagged),
        },
        "results": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()