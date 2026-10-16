import asyncio
import glob
import json
import time

from agent import app, pdf_to_text

FILES = sorted(glob.glob("data/invoices/*.pdf"))
CONCURRENCY = 4


def run_sync():
    return [app.invoke({"text": pdf_to_text(f)}) for f in FILES]


async def run_async():
    sem = asyncio.Semaphore(CONCURRENCY)

    async def run_one(path):
        async with sem:
            # app.invoke is blocking; to_thread lets several run at once
            return await asyncio.to_thread(app.invoke, {"text": pdf_to_text(path)})

    return await asyncio.gather(*(run_one(f) for f in FILES))


if __name__ == "__main__":
    t = time.perf_counter(); run_sync(); sync_s = time.perf_counter() - t
    t = time.perf_counter(); asyncio.run(run_async()); async_s = time.perf_counter() - t

    summary = {
        "invoices": len(FILES),
        "concurrency": CONCURRENCY,
        "sync_seconds": round(sync_s, 1),
        "async_seconds": round(async_s, 1),
        "speedup": round(sync_s / async_s, 1),
    }
    print(json.dumps(summary, indent=2))
    with open("results/batch_timing.json", "w") as f:
        json.dump(summary, f, indent=2)