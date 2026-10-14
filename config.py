"""
config.py
---------
Central configuration. Every knob lives here.
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --- TikTok mobile API (view/like/share delta endpoint) ---
API_URL = "https://api19-core-useast5.us.tiktokv.com/aweme/v1/aweme/stats/"

# --- files ---
CAMPAIGNS_FILE = BASE_DIR / "campaigns.json"
STATE_FILE     = BASE_DIR / "state.json"
WORKING_PROXY  = BASE_DIR / "working-proxies.txt"
RAW_PROXY      = BASE_DIR / "free-proxy-list.txt"
GEO_FILE       = BASE_DIR / "proxy-geo.json"

# --- request tuning ---
REQUEST_TIMEOUT = 12.0
DEFAULT_WORKERS = 24

# --- defaults (overridable per-campaign in campaigns.json) ---
DEFAULT_OVERSHOOT    = 1.30   # drive to 130% of the campaign cap
DEFAULT_WINDOW_HOURS = 6.0    # spread the target over this many hours
DEFAULT_LIKE_RATE    = 0.08   # ~8% of views also fire a like
DEFAULT_SHARE_RATE   = 0.03   # ~3% of views also fire a share

# --- pacing safety bounds ---
MIN_RPS = 0.02
MAX_RPS = 400.0

# coherent Android fingerprints (device_type / os / UA kept consistent)
DEVICE_POOL = [
    {
        "device_type": "SM-N976N", "device_brand": "samsung",
        "os_version": "7.1.2", "os_api": "25",
        "ua": "com.zhiliaoapp.musically/2022501030 (Linux; U; Android 7.1.2; fr; SM-N976N; Build/QP1A.190711.020;tt-ok/3.12.13.1)",
    },
    {
        "device_type": "SM-S908E", "device_brand": "samsung",
        "os_version": "11", "os_api": "30",
        "ua": "com.zhiliaoapp.musically/2022501030 (Linux; U; Android 11; en; SM-S908E; Build/RP1A.200720.012;tt-ok/3.12.13.1)",
    },
    {
        "device_type": "Pixel 6", "device_brand": "Google",
        "os_version": "12", "os_api": "31",
        "ua": "com.zhiliaoapp.musically/2022501030 (Linux; U; Android 12; en; Pixel 6; Build/SP2A.220505.002;tt-ok/3.12.13.1)",
    },
    {
        "device_type": "moto g(30)", "device_brand": "motorola",
        "os_version": "11", "os_api": "30",
        "ua": "com.zhiliaoapp.musically/2022501030 (Linux; U; Android 11; en; moto g(30); Build/RPMS31.Q1;tt-ok/3.12.13.1)",
    },
]