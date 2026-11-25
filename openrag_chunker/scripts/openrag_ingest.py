#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Uploads the corpus PDFs into a running OpenRAG, which parses, chunks, embeds and indexes them.

Uses OpenRAG's public API, the same two calls its Python SDK makes:
POST /api/v1/documents/ingest (one file per task) and GET /api/v1/tasks/<id>.
Progress is kept in <work-dir>/openrag_ingest_state.json, so an interrupted
run resumes; files that failed are retried on the next run.

  export OPENRAG_API_KEY=orag_...        # created in OpenRAG
  python openrag_ingest.py --work-dir ~/openrag-arxiv --openrag-url http://localhost:3000

Afterwards, scripts/openrag_export.py turns OpenRAG's index into the
workload corpus.
"""

import argparse
import asyncio
import datetime
import json
import os
import time
from pathlib import Path


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def timestamp():
    return datetime.datetime.now().isoformat(timespec="seconds")


class TimingLog:
    """Append-only text log: one line per PDF plus a summary after every `batch_size` completed PDFs.

    The per-file time covers upload, OpenRAG's parsing/chunking/embedding and indexing together
    (the API does not report them separately). A batch summary gives the wall-clock time for the
    batch and the sum of the per-file times; with concurrent uploads the sum exceeds the wall-clock.
    """

    def __init__(self, path, batch_size):
        self.path = path
        self.batch_size = batch_size
        self.batch_seconds = []
        self.batch_wall_start = None
        self.batches = 0
        self.run_wall_start = time.monotonic()
        self.run_count = 0
        self.run_seconds = 0.0

    def _write(self, line):
        with self.path.open("a") as f:
            f.write(f"{timestamp()} {line}\n")

    def run_started(self):
        self._write("RUN_START")

    def file_started(self):
        if self.batch_wall_start is None:
            self.batch_wall_start = time.monotonic()

    def file_finished(self, name, status, seconds):
        self._write(f"FILE {name} status={status} ingest_and_chunk_seconds={seconds:.1f}")
        if status != "completed":
            return
        self.batch_seconds.append(seconds)
        self.run_count += 1
        self.run_seconds += seconds
        if len(self.batch_seconds) >= self.batch_size:
            self._summarize(f"BATCH {self.batches + 1} ({self.batch_size} PDFs)")
            self.batches += 1

    def run_finished(self):
        if self.batch_seconds:
            self._summarize(f"PARTIAL BATCH ({len(self.batch_seconds)} PDFs)")
        wall = time.monotonic() - self.run_wall_start
        n = self.run_count
        self._write(f"RUN END pdfs_ingested_and_chunked={n} "
                    f"total_wall_clock_seconds={wall:.1f} total_summed_per_file_seconds={self.run_seconds:.1f} "
                    f"avg_wall_clock_seconds_per_pdf={wall / n if n else 0:.1f} "
                    f"avg_per_file_seconds_per_pdf={self.run_seconds / n if n else 0:.1f}")

    def _summarize(self, label):
        now = time.monotonic()
        wall = now - self.batch_wall_start
        self._write(f"{label} wall_clock_seconds={wall:.1f} "
                    f"summed_per_file_seconds={sum(self.batch_seconds):.1f}")
        self.batch_seconds = []
        self.batch_wall_start = now


class AuthError(Exception):
    pass


class State:
    def __init__(self, path):
        self.path = path
        self.files = json.loads(path.read_text()) if path.exists() else {}

    def done(self, name):
        return self.files.get(name, {}).get("status") == "completed"

    def record(self, name, **entry):
        self.files[name] = {**entry, "at": timestamp()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.files, indent=1, sort_keys=True))
        tmp.replace(self.path)


async def ingest_file(client, args, headers, pdf):
    """Submit one PDF and wait for OpenRAG's task to finish; returns (status, task_id, detail)."""
    with pdf.open("rb") as f:
        r = await client.post(f"{args.openrag_url}/api/v1/documents/ingest", headers=headers,
                              files={"file": (pdf.name, f, "application/pdf")})
    if r.status_code in (401, 403):
        raise AuthError(f"OpenRAG rejected the API key (HTTP {r.status_code}): {r.text[:300]}")
    if r.status_code >= 400:
        return "failed", None, f"upload HTTP {r.status_code}: {r.text[:300]}"
    task_id = r.json()["task_id"]

    deadline = time.monotonic() + args.task_timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(args.poll_interval)
        r = await client.get(f"{args.openrag_url}/api/v1/tasks/{task_id}", headers=headers)
        if r.status_code >= 500:
            continue
        r.raise_for_status()
        task = r.json()
        if task.get("status") in ("completed", "failed"):
            failed = task.get("status") == "failed" or task.get("failed_files", 0) > 0
            detail = None if not failed else json.dumps(task.get("files") or task)[:500]
            return ("failed" if failed else "completed"), task_id, detail
    return "failed", task_id, f"task did not finish within {args.task_timeout} s"


async def main_async(args):
    import httpx

    api_key = args.api_key or os.environ.get("OPENRAG_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENRAG_API_KEY (an OpenRAG API key) or pass --api-key")
    headers = {"X-API-Key": api_key}

    pdfs = sorted((args.work_dir / "pdfs").glob("*.pdf"))[: args.count]
    if not pdfs:
        raise SystemExit(f"No PDFs in {args.work_dir / 'pdfs'}; run build_corpus.py download first")
    state = State(args.work_dir / "openrag_ingest_state.json")
    todo = [p for p in pdfs if not state.done(p.name)]
    log(f"{len(pdfs) - len(todo)} of {len(pdfs)} PDFs already ingested; uploading {len(todo)} "
        f"to {args.openrag_url}, {args.max_in_flight} at a time")

    timing = TimingLog(args.timing_log, args.batch_size)
    timing.run_started()
    semaphore = asyncio.Semaphore(args.max_in_flight)
    finished = 0

    async def worker(client, pdf):
        nonlocal finished
        async with semaphore:
            started = time.monotonic()
            timing.file_started()
            try:
                status, task_id, detail = await ingest_file(client, args, headers, pdf)
            except httpx.HTTPError as e:
                status, task_id, detail = "failed", None, f"{type(e).__name__}: {e}"
            seconds = round(time.monotonic() - started, 1)
            state.record(pdf.name, status=status, task_id=task_id, error=detail, seconds=seconds)
            timing.file_finished(pdf.name, status, seconds)
            finished += 1
            log(f"[{finished}/{len(todo)}] {pdf.name}: {status}" + (f" ({detail})" if detail else ""))

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            try:
                await asyncio.gather(*(worker(client, pdf) for pdf in todo))
            except AuthError as e:
                raise SystemExit(str(e)) from None
    finally:
        timing.run_finished()

    failed = sorted(p.name for p in pdfs if not state.done(p.name))
    log(f"Done: {len(pdfs) - len(failed)} of {len(pdfs)} PDFs ingested"
        + (f"; {len(failed)} failed, rerun to retry them: {', '.join(failed[:10])}" if failed else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("~/openrag-arxiv"))
    parser.add_argument("--openrag-url", default=os.environ.get("OPENRAG_URL", "http://localhost:3000"),
                        help="OpenRAG's URL, as for its SDK (default: $OPENRAG_URL or http://localhost:3000)")
    parser.add_argument("--api-key", help="default: $OPENRAG_API_KEY")
    parser.add_argument("--count", type=int, default=1000, help="number of PDFs")
    parser.add_argument("--max-in-flight", type=int, default=2,
                        help="concurrent ingest tasks; OpenRAG's Docling step runs 2 at a time")
    parser.add_argument("--task-timeout", type=float, default=3600, help="seconds to wait for one file")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--timing-log", type=Path,
                        help="append-only timing log (default: <work-dir>/openrag_ingest_timing.log)")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="write a wall-clock and summed-time summary after this many completed PDFs")
    args = parser.parse_args()
    args.work_dir = args.work_dir.expanduser()
    args.timing_log = (args.timing_log or args.work_dir / "openrag_ingest_timing.log").expanduser()
    args.openrag_url = args.openrag_url.rstrip("/")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
