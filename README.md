# tiktok-whop-bot

Multimetric TikTok engagement bot tuned for Whop Content Rewards payouts.

Sends views + likes + shares in one request at organic ratios, paces traffic over
hours, and overshoots the campaign cap so audits don't see a snapped-to-ceiling spike.

## Setup
1. Drop your existing `check_proxies.py`, `harvester.py`, `geo_resolve.py` in this folder.
2. `pip install -r requirements.txt`
3. Edit `campaigns.json` with your videos, caps and overshoot factors.
4. `python plan.py --test` to confirm resolution + a live request.
5. `./run.sh` (or `python tiktok_bot.py`).

## Cron mode
`python tiktok_bot.py --runtime 20` runs a 20-minute slice and resumes next time.
The included GitHub Actions workflow does this every 30 minutes on free CI.

## Knobs
| flag | meaning |
|---|---|
| `--runtime N` | minutes per slice (0 = run to target) |
| `--window H` | override hours to spread the target over |
| `--rps R` | hard override requests/sec |
| `--shape bell` | ramp up and taper down like real traffic |
| `--overshoot` | per-campaign in campaigns.json |
| `--residential-only` | drop datacenter IPs |
| `--dry` | plan only |
| `--reset` | clear saved progress |

## Why the design
- `play_delta` + `digg_delta` + `share_delta` in one POST = no zero-engagement signature.
- Rate from target/window = organic velocity, not a burst.
- 1.25-1.40x overshoot = defeats cap clustering.
- State file = multi-hour curve assembled from short runs.