#!/usr/bin/env python3
"""
tiktok_bot.py  (v3.1)
Signed TikTok engagement bot with restored argument support and error handling.
"""
from __future__ import annotations

import argparse, json, math, random, re, threading, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

from config import (
    CAMPAIGNS_FILE, STATE_FILE, WORKING_PROXY, GEO_FILE,
    REQUEST_TIMEOUT, DEFAULT_OVERSHOOT, DEFAULT_WINDOW_HOURS,
    DEFAULT_LIKE_RATE, DEFAULT_SHARE_RATE, MIN_RPS, MAX_RPS, DEVICE_POOL,
)
from check_proxies import load_lines, parse_proxy

# --- Signing setup --------------------------------------------------------
try:
    from SignerPy import sign as sp_sign
    HAVE_SIGNER = True
except Exception:
    HAVE_SIGNER = False

try:
    from SignerPy.hosts import host as sp_hosts
    HOSTS = [f"https://{h}" for h in sp_hosts()]
except Exception:
    HOSTS = [
        "https://api16-normal-c-useast1a.tiktokv.com",
        "https://api22-normal-c-useast2a.tiktokv.com",
        "https://api19-core-useast5.us.tiktokv.com",
        "https://api16-normal-c-alisg.tiktokv.com",
        "https://api16-normal-no1a.tiktokv.eu",
    ]

STATS_PATH = "/aweme/v1/aweme/stats/"
MIN_BODY = 4
DUTY = 0.65

_SIGN_MODE: Optional[str] = None


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
    out = []
    for e in raw:
        c = Campaign(
            name=e.get("name") or e.get("url", "unnamed"),
            cap_views=int(e.get("cap_views", 0)),
            url=e.get("url", ""), id=str(e.get("id", "")),
            overshoot=float(e.get("overshoot", DEFAULT_OVERSHOOT)),
            window_hours=float(e.get("window_hours", DEFAULT_WINDOW_HOURS)),
            like_rate=float(e.get("like_rate", DEFAULT_LIKE_RATE)),
            share_rate=float(e.get("share_rate", DEFAULT_SHARE_RATE)),
            enabled=bool(e.get("enabled", True)),
        )
        if c.cap_views > 0:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
def rand_device_id() -> int:
    return random.randint(10**18, 10**19 - 1)


def rand_hex(n: int) -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def rand_iid() -> int:
    return random.randint(7000000000000000000, 7999999999999999999)


def build_proxy_url(cfg) -> str:
    auth = f"{cfg.username}:{cfg.password or ''}@" if cfg.username else ""
    s = {"socks4": "socks4a", "socks5": "socks5h", "https": "http"}.get(cfg.scheme, cfg.scheme)
    return f"{s}://{auth}{cfg.host}:{cfg.port}"


def load_proxy_pool(residential_only: bool) -> list[dict]:
    geo = {}
    if residential_only and GEO_FILE.exists():
        try:
            geo = json.loads(GEO_FILE.read_text(encoding="utf-8"))
        except Exception:
            geo = {}
    pool = []
    for line in load_lines(WORKING_PROXY):
        cfg = parse_proxy(line)
        if not cfg:
            continue
        if residential_only:
            if geo.get(f"{cfg.scheme}://{cfg.host}:{cfg.port}", {}).get("is_datacenter", True):
                continue
        u = build_proxy_url(cfg)
        pool.append({"http": u, "https": u})
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
        "aid": 1988, "channel": "googleplay",
        "device_type": dev["device_type"], "device_brand": dev["device_brand"],
        "device_id": rand_device_id(),
        "os_version": dev["os_version"], "os_api": dev["os_api"],
        "version_code": 250103, "app_name": "musical_ly",
        "device_platform": "android",
        "openudid": rand_hex(16), "iid": rand_iid(),
        "item_id": item_id,
        "play_delta": 1,
        "digg_delta": 1 if random.random() < like_rate else 0,
        "share_delta": 1 if random.random() < share_rate else 0,
    }


def _sign(params: dict, qs: str) -> dict:
    global _SIGN_MODE
    if not HAVE_SIGNER:
        return {}
    for mode, arg in (("str", qs), ("dict", params)):
        if _SIGN_MODE and mode != _SIGN_MODE:
            continue
        try:
            sig = sp_sign(params=arg, payload=None, version=8404)
            _SIGN_MODE = mode
            return {k: v for k, v in sig.items() if isinstance(v, str)}
        except Exception:
            continue
    return {}


def send_once(host: str, params: dict, proxy, force_sign: bool = True):
    qs = urlencode(params)
    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": random.choice(DEVICE_POOL)["ua"],
        "accept-encoding": "gzip",
        "connection": "close",
    }
    if force_sign:
        headers.update(_sign(params, qs))

    r = requests.post(f"{host}{STATS_PATH}?{qs}", headers=headers,
                      proxies=proxy, timeout=REQUEST_TIMEOUT)

    if r.status_code >= 400:
        return "hardblock", f"HTTP {r.status_code}"
    body = r.content
    if len(body) < MIN_BODY:
        return "softblock", "empty body"
    try:
        data = r.json()
    except Exception:
        return "softblock", "non-json body"
    code = data.get("status_code")
    if code == 0:
        return "ok", None
    return "softblock", f"status_code={code}"


# ---------------------------------------------------------------------------
def test_hosts() -> None:
    print("[*] host connectivity test:")
    for h in HOSTS:
        try:
            r = requests.post(f"{h}{STATS_PATH}", headers={
                "user-agent": DEVICE_POOL[0]["ua"],
                "content-type": "application/x-www-form-urlencoded"
            }, timeout=8)
            print(f"    {h.split('/')[2]:<40} HTTP {r.status_code} body_len={len(r.content)}")
        except Exception as e:
            print(f"    {h.split('/')[2]:<40} FAIL {type(e).__name__}")


# ---------------------------------------------------------------------------
class Pacer:
    def __init__(self, rps, shape="flat", duration=None, jitter=0.35):
        self.base_rps = max(MIN_RPS, min(rps, MAX_RPS))
        self.shape, self.duration, self.jitter = shape, duration, jitter
        self.start = time.time()
        self._next = self.start
        self._lock = threading.Lock()

    def _env(self, e):
        if self.shape != "bell" or not self.duration:
            return 1.0
        x = min(max(e / self.duration, 0.0), 1.0)
        return max(0.12, 0.5 - 0.5 * math.cos(2 * math.pi * x))

    def acquire(self):
        with self._lock:
            now = time.time()
            interval = (1.0 / self.base_rps) / self._env(now - self.start)
            self._next = max(self._next, now) + interval * random.uniform(1 - self.jitter, 1 + self.jitter)
            wait = max(0.0, self._next - now)
        if wait:
            time.sleep(wait)


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
def worker(camp, shared, lock, pacer, pool, use_proxy, debug, hosts):
    while not shared["stop"]:
        pacer.acquire()
        if shared["stop"]:
            break
        params = make_params(camp.item_id, camp.like_rate, camp.share_rate)
        proxy = random.choice(pool) if (use_proxy and pool) else None
        host = random.choice(hosts)
        try:
            kind, detail = send_once(host, params, proxy)
        except Exception as e:
            kind, detail = "err", type(e).__name__

        with lock:
            if kind == "ok":
                shared["views"] += 1
                if params["digg_delta"]:
                    shared["likes"] += 1
                if params["share_delta"]:
                    shared["shares"] += 1
            else:
                shared[kind] = shared.get(kind, 0) + 1
                if debug and shared["_dbg"] < 6:
                    shared["_dbg"] += 1
                    print(f"    [dbg] {kind}: {detail} @ {host.split('//')[1][:26]}")
            shared["sent"] += 1


def run_campaign(camp, state, pool, use_proxy, runtime_s, shape, rps_override,
                 workers_override, duty, dry, debug, hosts):
    item_id = camp.item_id
    target = camp.target
    st = state["videos"].setdefault(item_id, {"ok": 0, "likes": 0, "shares": 0,
                                              "target": target, "started": time.time()})
    st["target"] = target
    remaining = max(0, target - st["ok"])

    window_s = max(camp.window_hours * 3600, 60)
    elapsed = time.time() - st.get("started", time.time())
    window_left = max(window_s - elapsed, runtime_s or 60, 60)
    rps = rps_override or max(MIN_RPS, min((remaining / window_left) / duty, MAX_RPS))

    print(f"\n=== {camp.name} ===")
    print(f"    item_id  : {item_id}")
    print(f"    cap      : {camp.cap_views:,} x{camp.overshoot:.2f} -> target {target:,}")
    print(f"    progress : {st['ok']:,}/{target:,} (remaining {remaining:,})")
    print(f"    pacing   : {rps:.3f} req/s  shape={shape}  signer={'on' if HAVE_SIGNER else 'OFF'}")

    if remaining <= 0:
        print("    -> complete")
        return
    if dry:
        print(f"    -> dry: {remaining:,} hits (~{remaining/rps/60:.1f} min)")
        return

    shared = {"views": 0, "likes": 0, "shares": 0, "softblock": 0,
              "hardblock": 0, "err": 0, "sent": 0, "stop": False, "_dbg": 0}
    lock = threading.Lock()
    pacer = Pacer(rps, shape=shape, duration=runtime_s)
    workers_n = workers_override or max(6, min(int(rps * REQUEST_TIMEOUT) + 6, 96))
    stop_at = (time.time() + runtime_s) if runtime_s else None

    threads = [threading.Thread(target=worker, args=(camp, shared, lock, pacer, pool,
                                                     use_proxy, debug, hosts), daemon=True)
               for _ in range(workers_n)]
    for t in threads:
        t.start()

    last = time.time()
    try:
        while True:
            time.sleep(0.5)
            with lock:
                done = shared["views"]
            if done >= remaining:
                break
            if stop_at and time.time() >= stop_at:
                break
            if time.time() - last > 10:
                with lock:
                    st["ok"] += shared["views"] - st.get("_flushed", 0)
                st["_flushed"] = shared["views"]
                save_state(state)
                last = time.time()
    except KeyboardInterrupt:
        print("\n[*] Ctrl-C - stopping...")
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
        print(f"    [+] run: +{shared['views']:,} views (soft {shared['softblock']} "
              f"hard {shared['hardblock']} err {shared['err']})")
        print(f"    [+] total: {st['ok']:,}/{target:,}")


# ---------------------------------------------------------------------------
def bisect(item_id: str, pool: Optional[list[dict]]) -> int:
    if not HAVE_SIGNER:
        print("[!] SignerPy missing -> signing disabled. pip install SignerPy")

    targets = [("direct", None)]
    if pool:
        for _ in range(min(3, len(pool))):
            targets.append(("proxy", random.choice(pool)))

    print("[*] bisect: path x host x signing")
    for label, proxy in targets:
        for host in HOSTS[:4]:
            for use_sign in (True, False):
                p = make_params(item_id, 0, 0)
                try:
                    kind, detail = send_once(host, p, proxy, force_sign=use_sign)
                except Exception as e:
                    kind, detail = "err", type(e).__name__
                tag = "sign" if use_sign else "raw "
                print(f"    {label:6} {tag}  {host.split('//')[1][:34]:<36} -> {kind} {detail or ''}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign")
    ap.add_argument("--runtime", type=float, default=0)
    ap.add_argument("--window", type=float, default=0)
    ap.add_argument("--rps", type=float, default=0)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--shape", choices=["flat", "bell"], default="flat")
    ap.add_argument("--duty", type=float, default=DUTY)
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--residential-only", action="store_true")
    ap.add_argument("--ensure-proxies", action="store_true")
    ap.add_argument("--bisect", action="store_true")
    ap.add_argument("--test-hosts", action="store_true")  # Restored argument definition
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.test_hosts:
        test_hosts()
        return 0

    camps = [c for c in load_campaigns(CAMPAIGNS_FILE) if c.enabled]
    if args.campaign:
        n = args.campaign.lower()
        camps = [c for c in camps if n in c.name.lower()]
    for c in camps:
        c.item_id = resolve_item_id(c.url or c.id)
    camps = [c for c in camps if c.item_id]
    if not camps:
        print("[-] No resolvable campaigns")
        return 1

    if args.reset and STATE_FILE.exists():
        STATE_FILE.unlink()
    if args.ensure_proxies and not args.no_proxy:
        try:
            import ensure_proxies
            ensure_proxies.main()
        except SystemExit:
            pass
    if args.window:
        for c in camps:
            c.window_hours = args.window

    pool, use_proxy = None, not args.no_proxy
    if use_proxy:
        pool = load_proxy_pool(args.residential_only)
        if pool:
            print(f"[*] Loaded {len(pool)} proxies")
        else:
            use_proxy = False

    if args.bisect:
        return bisect(camps[0].item_id, pool)

    state = load_state()
    runtime_s = args.runtime * 60 if args.runtime else None
    for camp in camps:
        run_campaign(camp, state, pool, use_proxy, runtime_s, args.shape,
                     args.rps or None, args.workers or None, args.duty, args.dry,
                     args.debug, HOSTS)
    print("\n[+] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())