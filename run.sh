#!/usr/bin/env bash
# Full local pipeline: harvest -> validate -> classify -> run bot.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY=python3

command -v "$PY" >/dev/null || { echo "python3 missing"; exit 1; }
"$PY" -c "import requests" 2>/dev/null || "$PY" -m pip install -r requirements.txt

echo "[*] harvesting proxies for 90s..."
timeout 90 "$PY" harvester.py || true

echo "[*] validating proxies..."
"$PY" check_proxies.py -i free-proxy-list.txt -o working-proxies.txt -t 8 -w 64 || true
"$PY" geo_resolve.py || true

echo "[*] plan:"
"$PY" plan.py

echo "[*] running bot (Ctrl-C to stop)..."
exec "$PY" tiktok_bot.py "$@"