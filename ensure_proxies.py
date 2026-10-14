#!/usr/bin/env python3
"""
ensure_proxies.py
-----------------
Blocks until working-proxies.txt holds at least --min usable proxies.

Cycle: bounded harvester run -> validate -> geo classify -> count. Repeats until
the minimum is met or --max-wait is exceeded. Then the run slice can proceed.

Usage:
    python ensure_proxies.py
    python ensure_proxies.py --min 80 --max-wait 20
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.chdir(BASE)

import check_proxies   # noqa: E402
import geo_resolve     # noqa: E402

RAW = BASE / "free-proxy-list.txt"
WORKING = BASE / "working-proxies.txt"

HARVEST_WINDOW = 70      # seconds per harvest attempt
CHECK_WORKERS = 96
CHECK_TIMEOUT = 8.0


def count_working() -> int:
    if not WORKING.exists():
        return 0
    return sum(1 for l in WORKING.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip())


def harvest_once(seconds: int) -> None:
    RAW.touch(exist_ok=True)
    try:
        subprocess.run([sys.executable, "harvester.py"], cwd=BASE, timeout=seconds)
    except subprocess.TimeoutExpired:
        pass  # harvester loops forever; the timeout is expected


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=40)
    ap.add_argument("--max-wait", type=float, default=12, help="minutes before giving up")
    ap.add_argument("--harvest-window", type=int, default=HARVEST_WINDOW)
    args = ap.parse_args()

    deadline = time.time() + args.max_wait * 60
    attempt = 0

    while True:
        have = count_working()
        if have >= args.min:
            print(f"[ok] {have} working proxies (>= {args.min})")
            return 0
        if time.time() >= deadline:
            print(f"[warn] only {have}/{args.min} proxies after {args.max_wait:.0f} min - proceeding anyway")
            return 0

        attempt += 1
        print(f"[*] attempt {attempt}: have {have}/{args.min} - harvesting for {args.harvest_window}s...")
        harvest_once(args.harvest_window)

        print("[*] validating proxies...")
        check_proxies.run(RAW, WORKING, CHECK_TIMEOUT, CHECK_WORKERS)

        try:
            geo_resolve.main()
        except Exception as e:
            print(f"[warn] geo_resolve failed: {e}")


if __name__ == "__main__":
    raise SystemExit(main())