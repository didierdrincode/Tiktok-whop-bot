#!/usr/bin/env python3
"""
tiktok_bot.py
-------------
Multimetric TikTok engagement bot for Whop Content Rewards clipping campaigns.

Design:
  * Every hit sends play_delta=1 (a view) and, at a randomised ratio, digg_delta
    (like) and share_delta (share) IN THE SAME REQUEST, so engagement scales with
    views instead of leaving a zero-engagement signature.
  * A global rate limiter paces the whole fleet to a target req/s derived from
    target_views / window_hours, with optional bell-curve shaping and jitter.
  * Targets overshoot the campaign cap by `overshoot` (default 1.30) to avoid the
    "snapped exactly to the max payout" pattern brand audits look for.
  * Progress persists to state.json so scheduled slices accumulate over hours.

Reuses YOUR proxy tooling unchanged:
  * check_proxies.py  -> load_lines / parse_proxy
  * working-proxies.txt, proxy-geo.json

Usage:
    python tiktok_bot.py                      # run all enabled campaigns, full window
    python tiktok_bot.py --runtime 20         # 20-minute slice (cron mode)
    python tiktok_bot.py --campaign "YoungLA" # one campaign by name
    python tiktok_bot.py --dry                # show the plan, send nothing
    python tiktok_bot.py --shape bell         # organic ramp-up / taper-down
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from config import (
    API_URL, CAMPAIGNS_FILE, STATE_FILE, WORKING_PROXY, GEO_FILE,
    REQUEST_TIMEOUT, DEFAULT_WORKERS, DEFAULT_OVERSHOOT, DEFAULT_WINDOW_HOURS,
    DEFAULT_LIKE_RATE, DEFAULT_SHARE_RATE, MIN_RPS, MAX_RPS, DEVICE_POOL,
)
from check_proxies import load_lines, parse_proxy


# ---------------------------------------------------------------------------
# CAMPAIGN MODEL
# ---------------------------------------------------------------------------
@dataclass
class Campaign:
    name: str
    cap_views: int
    url: str = ""
    id: str = ""
    overshoot: float = DEFAULT_OVERSHOOT
    window_hours: float = DEFAULT_WINDOW_HOURS
    like_rate: float = DEFAULT_LIKE_RATE
    share_rate: float = DEFAULT_SHARE_RATE
    enabled: bool = True
    item_id: str = ""

    @property
    def target(self) -> int:
        return int(round(self.cap_views * self.overshoot))


def load_campaigns(path: Path) -> list[Campaign]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("campaigns", [])
    camps: list[Campaign] = []
    for entry in raw:
        c = Campaign(
            name=entry.get("name") or entry.get("url", "unnamed"),
            cap_views=int(entry.get("cap_views", 0)),
            url=entry.get("url", ""),
            id=str(entry.get("id", "")),
            overshoot=float(entry.get("overshoot", DEFAULT_OVERSHOOT)),
            window_hours=float(entry.get("window_hours", DEFAULT_WINDOW_HOURS)),
            like_rate=float(entry.get("like_rate", DEFAULT_LIKE_RATE)),
            share_rate=float(entry.get("share_rate", DEFAULT_SHARE_RATE)),
            enabled=bool(entry.get("enabled", True)),
        )
        if c.cap_views > 0:
            camps.append(c)
    return camps


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def rand_device_id() -> int:
    return random.randint(10**18, 10**19 - 1)


def rand_hex(n: int) -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def rand_iid() -> int:
    return random.randint(7000000000000000000, 7999999999999999999)


def build_proxy_url(cfg) -> str:
    auth = f"{cfg.username}:{cfg.password or ''}@" if cfg.username else ""
    scheme = cfg.scheme
    if scheme == "socks4":
        scheme = "socks4a"
    elif scheme == "socks5":
        scheme = "socks5h"
    elif scheme == "https":
        scheme = "http"
    return f"{scheme}://{auth}{cfg.host}:{cfg.port}"


def load_proxy_pool(residential_only: bool) -> list[dict]:
    geo = {}
    if residential_only and GEO_FILE.exists():
        try:
            geo = json.loads(GEO_FILE.read_text(encoding="utf-8"))
        except Exception:
            geo = {}
    pool: list[dict] = []
    for line in load_lines(WORKING_PROXY):
        cfg = parse_proxy(line)
        if not cfg:
            continue
        if residential_only:
            key = f"{cfg.scheme}://{cfg.host}:{cfg.port}"
            if geo.get(key, {}).get("is_datacenter", True):
                continue
        url = build_proxy_url(cfg)
        pool.append({"http": url, "https": url})
    return pool


def resolve_item_id(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    if raw.isdigit() and 15 <= len(raw) <= 20:
        return raw
    m = re.search(r"/video/(\d{15,20})", raw) or re.search(r"(\d{18,19})", raw)
    if m:
        return m.group(1)
    try:
        r = requests.head(raw, allow_redirects=True, timeout=10,
                          headers={"User-Agent": DEVICE_POOL[0]["ua"]})
        m = re.search(r"/video/(\d{15,20})", r.url) or re.search(r"(\d{18,19})", r.url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def make_params(item_id: str, like_rate: float, share_rate: float) -> dict:
    dev = random.choice(DEVICE_POOL)
    return {
        "aid": 1988,
        "channel": "googleplay",
        "device_type": dev["device_type"],
        "device_brand": dev["device_brand"],
        "device_id": rand_device_id(),
        "os_version": dev["os_version"],
        "os_api": dev["os_api"],
        "version_code": 250103,
        "app_name": "musical_ly",
        "device_platform": "android",
        "openudid": rand_hex(16),
        "iid": rand_iid(),
        "item_id": item_id,
        "play_delta": 1,
        "digg_delta": 1 if random.random() < like_rate else 0,
        "share_delta": 1 if random.random() < share_rate else 0,
    }


def headers_for() -> dict:
    return {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": random.choice(DEVICE_POOL)["ua"],
        "accept-encoding": "gzip",
    }


# ---------------------------------------------------------------------------
# GLOBAL PACER
# ---------------------------------------------------------------------------
class Pacer:
    """Thread-safe global rate limiter with jitter + optional bell envelope."""

    def __init__(self, rps: float, shape: str = "flat", duration: Optional[float] = None,
                 jitter: float = 0.35):
        self.base_rps = max(MIN_RPS, min(rps, MAX_RPS))
        self.shape = shape
        self.duration = duration
        self.jitter = jitter
        self.start = time.time()
        self._next = self.start
        self._lock = threading.Lock()

    def _envelope(self, elapsed: float) -> float:
        if self.shape != "bell" or not self.duration:
            return 1.0
        x = min(max(elapsed / self.duration, 0.0), 1.0)
        return max(0.12, 0.5 - 0.5 * math.cos(2 * math.pi * x))

    def acquire(self) -> None:
        with self._lock:
            now = time.time()
            env = self._envelope(now - self.start)
            interval = (1.0 / self.base_rps) / env
            self._next = max(self._next, now) + interval * random.uniform(1 - self.jitter, 1 + self.jitter)
            wait = max(0.0, self._next - now)
        if wait:
            time.sleep(wait)


# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"videos": {}}


def save_state(state: dict) -> None:
    state["updated"] = time.time()
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ---------------------------------------------------------------------------
# WORKER
# ---------------------------------------------------------------------------
def worker(camp: Campaign, shared: dict, lock: threading.Lock,
           pacer: Pacer, pool: Optional[list[dict]], use_proxy: bool) -> None:
    while not shared["stop"]:
        pacer.acquire()
        if shared["stop"]:
            break
        proxy = random.choice(pool) if (use_proxy and pool) else None
        params = make_params(camp.item_id, camp.like_rate, camp.share_rate)
        try:
            r = requests.post(API_URL, headers=headers_for(), params=params,
                              proxies=proxy, timeout=REQUEST_TIMEOUT)
            code = r.json().get("status_code")
            with lock:
                if code == 0:
                    shared["views"] += 1
                    if params["digg_delta"]:
                        shared["likes"] += 1
                    if params["share_delta"]:
                        shared["shares"] += 1
                else:
                    shared["bad"] += 1
        except Exception:
            with lock:
                shared["err"] += 1
        finally:
            with lock:
                shared["sent"] += 1


# ---------------------------------------------------------------------------
# CAMPAIGN RUNNER
# ---------------------------------------------------------------------------
def run_campaign(camp: Campaign, state: dict, pool, use_proxy: bool,
                 runtime_s: Optional[float], shape: str, rps_override: Optional[float],
                 workers_override: Optional[int], dry: bool) -> None:
    item_id = camp.item_id
    target = camp.target
    st = state["videos"].setdefault(item_id, {"ok": 0, "likes": 0, "shares": 0,
                                              "target": target, "started": time.time()})
    st["target"] = target
    remaining = max(0, target - st["ok"])

    window_s = max(camp.window_hours * 3600, 60)
    elapsed = time.time() - st.get("started", time.time())
    window_left = max(window_s - elapsed, runtime_s or 60, 60)
    rps = rps_override or max(MIN_RPS, min(remaining / window_left, MAX_RPS))

    print(f"\n=== {camp.name} ===")
    print(f"    item_id    : {item_id}")
    print(f"    cap        : {camp.cap_views:,}  x{camp.overshoot:.2f} overshoot -> target {target:,}")
    print(f"    progress   : {st['ok']:,}/{target:,}  (remaining {remaining:,})")
    print(f"    pacing     : {rps:.3f} req/s over ~{window_left/3600:.2f}h window  shape={shape}")
    print(f"    ratios     : like {camp.like_rate:.0%}  share {camp.share_rate:.0%}")

    if remaining <= 0:
        print("    -> already complete, skipping")
        return
    if dry:
        est = remaining / rps / 60
        print(f"    -> dry: would send {remaining:,} hits (~{est:.1f} min at this rate)")
        return

    shared = {"views": 0, "likes": 0, "shares": 0, "bad": 0, "err": 0, "sent": 0, "stop": False}
    lock = threading.Lock()
    pacer = Pacer(rps, shape=shape, duration=runtime_s)

    workers_n = workers_override or max(6, min(int(rps * REQUEST_TIMEOUT) + 6, 96))
    if rps * REQUEST_TIMEOUT > workers_n:
        print(f"    [!] {workers_n} workers may not sustain {rps:.2f} req/s (latency-bound). Consider --workers.")

    stop_at = (time.time() + runtime_s) if runtime_s else None
    threads = [threading.Thread(target=worker, args=(camp, shared, lock, pacer, pool, use_proxy),
                                daemon=True) for _ in range(workers_n)]
    for t in threads:
        t.start()

    last_save = time.time()
    try:
        while True:
            time.sleep(0.5)
            with lock:
                done = shared["views"]
                snap = dict(shared)
            if done >= remaining:
                break
            if stop_at and time.time() >= stop_at:
                break
            if time.time() - last_save > 10:
                with lock:
                    st["ok"] += shared["views"] - st.get("_flushed", 0)
                st["_flushed"] = shared["views"]
                save_state(state)
                last_save = time.time()
    except KeyboardInterrupt:
        print("\n[*] Ctrl-C - stopping cleanly...")
    finally:
        shared["stop"] = True
        for t in threads:
            t.join(timeout=15)
        with lock:
            st["ok"] += shared["views"]
            st["likes"] += shared["likes"]
            st["shares"] += shared["shares"]
        st.pop("_flushed", None)
        save_state(state)
        print(f"    [+] this run: +{shared['views']:,} views  +{shared['likes']:,} likes  "
              f"+{shared['shares']:,} shares  (bad {shared['bad']}  err {shared['err']})")
        print(f"    [+] total now: {st['ok']:,}/{target:,}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Multimetric TikTok engagement bot (Whop-safe pacing).")
    ap.add_argument("--campaign", help="run only the campaign whose name contains this")
    ap.add_argument("--runtime", type=float, default=0, help="minutes to run per slice (0 = until target)")
    ap.add_argument("--window", type=float, default=0, help="override window_hours for all campaigns")
    ap.add_argument("--rps", type=float, default=0, help="override requests/sec for all campaigns")
    ap.add_argument("--workers", type=int, default=0, help="override worker thread count")
    ap.add_argument("--shape", choices=["flat", "bell"], default="flat", help="traffic envelope")
    ap.add_argument("--no-proxy", action="store_true", help="blast direct, ignore proxy pool")
    ap.add_argument("--residential-only", action="store_true", help="filter datacenter IPs")
    ap.add_argument("--dry", action="store_true", help="plan only, send nothing")
    ap.add_argument("--reset", action="store_true", help="clear saved state first")
    args = ap.parse_args()

    if not CAMPAIGNS_FILE.exists():
        print(f"[-] Missing {CAMPAIGNS_FILE.name}. See campaigns.json example.")
        return 1

    if args.reset and STATE_FILE.exists():
        STATE_FILE.unlink()
        print("[*] state.json reset")

    camps = [c for c in load_campaigns(CAMPAIGNS_FILE) if c.enabled]
    if args.campaign:
        needle = args.campaign.lower()
        camps = [c for c in camps if needle in c.name.lower()]

    # resolve ids
    for c in camps:
        c.item_id = resolve_item_id(c.url or c.id)
    camps = [c for c in camps if c.item_id]
    if not camps:
        print("[-] No resolvable campaigns (check url/id in campaigns.json)")
        return 1

    if args.window:
        for c in camps:
            c.window_hours = args.window

    # proxy pool
    pool: Optional[list[dict]] = None
    use_proxy = not args.no_proxy
    if use_proxy:
        pool = load_proxy_pool(args.residential_only)
        if pool:
            print(f"[*] Loaded {len(pool)} proxies"
                  f"{' (residential only)' if args.residential_only else ''} from {WORKING_PROXY.name}")
        else:
            print(f"[!] No proxies in {WORKING_PROXY.name} -> direct mode")
            use_proxy = False
    else:
        print("[*] Direct mode (no proxy)")

    state = load_state()
    runtime_s = args.runtime * 60 if args.runtime else None
    rps_override = args.rps or None
    workers_override = args.workers or None

    for camp in camps:
        run_campaign(camp, state, pool, use_proxy, runtime_s, args.shape,
                     rps_override, workers_override, args.dry)

    print("\n[+] Done. State saved to", STATE_FILE.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())