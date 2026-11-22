"""Compare a new sync's output against the live site data.

Fetches the current docs/data files straight from GitHub (so a stale local
copy on Kali doesn't matter) and reports every field that differs.

Usage:
    python scripts/sync_garmin.py --out /tmp/newsync      # fresh 200-day pull
    python scripts/compare_sync.py /tmp/newsync
    python scripts/compare_sync.py /tmp/newsync --live docs/data   # compare to a local folder instead

What to expect:
  - activities and daily values should match apart from tiny float noise,
    and recent days Garmin has updated since the live sync ran.
  - training_status: VO2 max and fitness age now come from the Personal API,
    and readiness/status may come from an earlier day (status_date,
    readiness_date), so small differences there are normal.
"""

import argparse
import json
import urllib.request
from pathlib import Path

LIVE_URL = "https://raw.githubusercontent.com/SurlyKM/ctl-dashboard/main/docs/data/"
FILES = ["activities.json", "daily.json", "training_status.json"]
TOLERANCE = 0.01      # relative; ignores float rounding noise
SHOW = 8              # example differences printed per field


def load_live(source: str, name: str):
    if source.startswith("http"):
        with urllib.request.urlopen(source + name, timeout=30) as r:
            return json.loads(r.read())
    p = Path(source) / name
    return json.loads(p.read_text()) if p.exists() else None


def same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= TOLERANCE * max(abs(a), abs(b), 1)
    return a == b


def compare_records(label, old: dict, new: dict):
    """old/new: {key: {field: value}}. Reports per-field differences."""
    both = sorted(set(old) & set(new))
    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(new) - set(old))
    print(f"\n=== {label} ===")
    print(f"  compared {len(both)} | only in live {len(only_old)} | only in new {len(only_new)}")
    if only_old:
        print(f"    only in live (first {SHOW}): {only_old[:SHOW]}")
    if only_new:
        print(f"    only in new (first {SHOW}): {only_new[:SHOW]}")

    diffs = {}
    for k in both:
        for f in set(old[k]) | set(new[k]):
            a, b = old[k].get(f), new[k].get(f)
            if not same(a, b):
                diffs.setdefault(f, []).append((k, a, b))
    if not diffs:
        print("  all compared fields match")
        return
    for f, rows in sorted(diffs.items()):
        print(f"  {f}: {len(rows)} of {len(both)} differ")
        for k, a, b in rows[:SHOW]:
            print(f"      {k}: live={a!r} new={b!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new_dir", type=Path)
    ap.add_argument("--live", default=LIVE_URL, help="URL or folder of the current data")
    args = ap.parse_args()

    live = {n: load_live(args.live, n) for n in FILES}
    new = {n: json.loads((args.new_dir / n).read_text()) for n in FILES}

    compare_records("activities (by id)",
                    {str(a["id"]): a for a in live["activities.json"] or []},
                    {str(a["id"]): a for a in new["activities.json"]})
    compare_records("daily (by date)", live["daily.json"] or {}, new["daily.json"])

    print("\n=== training_status ===")
    old_ts, new_ts = live["training_status.json"] or {}, new["training_status.json"]
    for f in sorted(set(old_ts) | set(new_ts)):
        a, b = old_ts.get(f), new_ts.get(f)
        mark = "  " if same(a, b) else "≠ "
        print(f"  {mark}{f}: live={a!r} new={b!r}")


if __name__ == "__main__":
    main()
