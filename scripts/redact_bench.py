"""Timing harness for loglens.redact (BRIEF-DEVWS-027). Not a test; CI never
runs it and nothing here asserts a time.

It writes a seeded synthetic log of ordinary lines (no secrets) into a fresh
temp dir, times redact() over every line and loglens end to end (in-process
main(), report to a second temp dir), and prints the median of the runs.
Both temp dirs are removed afterwards; the log is never committed.

    python3 -I scripts/redact_bench.py [--mb 20] [--runs 3] [--seed 27]
    python3 -I scripts/redact_bench.py --keep DIR   # write the log to DIR only
"""
import argparse
import io
import os
import random
import statistics
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loglens.cli import main as loglens_main  # noqa: E402
from loglens.redact import redact  # noqa: E402

LEVELS = ["INFO"] * 12 + ["DEBUG"] * 6 + ["WARNING"] * 2 + ["ERROR"]
MODULES = ["api.handlers", "db.pool", "worker.queue", "auth.views",
           "cache.redis", "http.client", "scheduler", "billing.sync"]
PATHS = ["/api/v1/orders", "/api/v1/users/{n}", "/healthz", "/static/app.js",
         "/api/v2/search", "/login", "/api/v1/items/{n}/reviews"]
TEMPLATES = [
    "GET {path} 200 in {ms}ms",
    "POST {path} 201 in {ms}ms bytes={n}",
    "request_id={hex} user=user{n} status={code} latency_ms={ms}",
    "connected to db host=10.0.{a}.{b} port=5432 pool_size={small}",
    "job {hex} finished in {sec}s, {n} rows written",
    "cache miss for key orders:{n}, fetching from origin",
    "retrying upstream call attempt={small} of 5 after {ms}ms",
    "worker-{small} picked task {hex} from queue default",
    "slow query took {ms}ms: SELECT id, name FROM items WHERE id = ?",
    "user user{n} logged in from 192.168.{a}.{b}",
    "scheduled run at 2026-10-14T{hh}:{mm}:00Z skipped, previous still running",
    "config reloaded, {small} handlers registered",
    "Timeout while reading from upstream after {sec}s",
    "processed batch {n} of {n2} ({pct}%)",
    "invoice {n} synced, amount={n2}.{b} EUR",
]


def ordinary_line(rng, index):
    stamp = (f"2026-10-14T{index // 360000 % 24:02d}:{index // 6000 % 60:02d}:"
             f"{index // 100 % 60:02d}.{rng.randrange(1000):03d}Z")
    message = rng.choice(TEMPLATES).format(
        path=rng.choice(PATHS).format(n=rng.randrange(1, 99999)),
        ms=rng.randrange(1, 5000), n=rng.randrange(1, 10 ** 6),
        n2=rng.randrange(1, 10 ** 6), hex=f"{rng.getrandbits(48):012x}",
        code=rng.choice((200, 200, 200, 204, 301, 404, 500)),
        a=rng.randrange(256), b=rng.randrange(256), small=rng.randrange(1, 64),
        sec=f"{rng.random() * 30:.2f}", hh=f"{rng.randrange(24):02d}",
        mm=f"{rng.randrange(60):02d}", pct=rng.randrange(101))
    return f"{stamp} {rng.choice(LEVELS)} [{rng.choice(MODULES)}] {message}"


def write_log(path, megabytes, seed):
    rng = random.Random(seed)
    target = megabytes * 1024 * 1024
    size, index, lines = 0, 0, []
    while size < target:
        line = ordinary_line(rng, index)
        lines.append(line)
        size += len(line) + 1
        index += 1
    path.write_text("".join(line + "\n" for line in lines), encoding="ascii")
    return lines


def time_redact(lines):
    start = time.perf_counter()
    for line in lines:
        redact(line)
    return time.perf_counter() - start


def time_end_to_end(log_path):
    with tempfile.TemporaryDirectory() as out_dir:
        out = os.path.join(out_dir, "report.txt")
        sink = io.StringIO()
        start = time.perf_counter()
        with redirect_stdout(sink), redirect_stderr(sink):
            code = loglens_main([str(log_path), "--output", out])
        elapsed = time.perf_counter() - start
        # 0/1/4 are ok/warnings/errors found; anything else is a failed run.
        if code not in (0, 1, 4) or not os.path.getsize(out):
            raise SystemExit(f"loglens exited {code}")
    return elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mb", type=int, default=20)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=27)
    parser.add_argument("--keep", metavar="DIR",
                        help="only write the log as DIR/ordinary.log and exit")
    args = parser.parse_args()
    if args.keep:
        path = Path(args.keep) / "ordinary.log"
        lines = write_log(path, args.mb, args.seed)
        print(f"wrote {path} ({len(lines)} lines)")
        return 0
    with tempfile.TemporaryDirectory() as log_dir:
        log_path = Path(log_dir) / "ordinary.log"
        lines = write_log(log_path, args.mb, args.seed)
        print(f"log: {log_path.stat().st_size} bytes, {len(lines)} lines, "
              f"seed {args.seed}, python {sys.version.split()[0]}")
        redact_runs = [time_redact(lines) for _ in range(args.runs)]
        e2e_runs = [time_end_to_end(log_path) for _ in range(args.runs)]
    for name, runs in (("redact() alone", redact_runs),
                       ("loglens end to end", e2e_runs)):
        shown = ", ".join(f"{r:.2f}" for r in runs)
        print(f"{name}: median {statistics.median(runs):.2f} s  (runs: {shown})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
