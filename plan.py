#!/usr/bin/env python3
"""
plan.py
-------
Dry-run planner: resolves every campaign, computes overshoot targets and pacing,
shows saved progress, and optionally fires one live test request per campaign.

Usage:
    python plan.py
    python plan.py --test
"""
from __future__ import annotations

import argparse

from config import CAMPAIGNS_FILE, STATE_FILE
from tiktok_bot import load_campaigns, load_state, resolve_item_id, make_params, headers_for
import requests
from config import API_URL, REQUEST_TIMEOUT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="fire one live request per campaign")
    args = ap.parse_args()

    camps = [c for c in load_campaigns(CAMPAIGNS_FILE) if c.enabled]
    state = load_state()
    print(f"{'campaign':<28}{'cap':>10}{'target':>10}{'progress':>14}{'rate/s':>10}")
    print("-" * 72)

    for c in camps:
        c.item_id = resolve_item_id(c.url or c.id) or "UNRESOLVED"
        target = c.target
        st = state["videos"].get(c.item_id, {})
        prog = f"{st.get('ok', 0):,}/{target:,}" if c.item_id != "UNRESOLVED" else "-"
        rate = target / (c.window_hours * 3600)
        print(f"{c.name[:27]:<28}{c.cap_views:>10,}{target:>10,}{prog:>14}{rate:>10.3f}")

        if args.test and c.item_id != "UNRESOLVED":
            try:
                r = requests.post(API_URL, headers=headers_for(),
                                  params=make_params(c.item_id, c.like_rate, c.share_rate),
                                  timeout=REQUEST_TIMEOUT)
                print(f"    test -> status_code={r.json().get('status_code')} (0 = ok)")
            except Exception as e:
                print(f"    test -> error {e}")

    print(f"\nstate: {STATE_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())