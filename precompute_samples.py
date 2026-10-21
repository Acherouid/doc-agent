"""Run each sample invoice through the agent ONCE and store the results.

    python precompute_samples.py

Writes data/samples_cache.json (commit it). The Streamlit app replays these
results for the sample invoices, so visitors cost you nothing there.
Re-run it whenever you change the prompt, the validators, or the sample PDFs.
"""
import glob
import hashlib
import json
import os
import time
from datetime import datetime, timezone

from agent import llm_from_env, pdf_to_text, run

client, model = llm_from_env()
samples = {}

for path in sorted(glob.glob("data/invoices/*.pdf")):
    name = os.path.basename(path)
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    t0 = time.perf_counter()
    res = run(pdf_to_text(path), client, model)
    seconds = time.perf_counter() - t0
    samples[name] = {
        "sha256": sha,
        "result": {
            "status": res.get("status"),
            "attempts": res.get("attempts"),
            "errors": res.get("errors", []),
            "data": res.get("data") or {},
            "seconds": seconds,
        },
    }
    print(f"{name}: {res.get('status')} ({res.get('attempts')} attempt(s), {seconds:.1f}s)")

out = {"model": model, "generated_at": datetime.now(timezone.utc).isoformat(), "samples": samples}
with open("data/samples_cache.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\nSaved {len(samples)} results to data/samples_cache.json")
