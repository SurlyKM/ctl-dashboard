"""Compute training load metrics from synced Garmin data.

Model: the classic Banister impulse-response pair used by TrainingPeaks.
  - Each activity gets a load score. We prefer Garmin's own
    activityTrainingLoad (EPOC-based). If missing (e.g. some gym
    sessions), we fall back to a HR-based TRIMP estimate.
  - CTL "fitness"  = 42-day exponentially weighted average of daily load
  - ATL "fatigue"  =  7-day exponentially weighted average of daily load
  - TSB "form"     = yesterday's CTL - yesterday's ATL
Note: with only ~60 days of history the CTL is still "warming up" for
the first few weeks, so early values read low. That washes out over time.
"""

import json
import math
import datetime as dt
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"

CTL_TC = 42  # time constant, days
ATL_TC = 7

SPORT_GROUPS = {
    "cycling": {"cycling", "road_biking", "virtual_ride", "indoor_cycling", "gravel_cycling"},
    "mtb": {"mountain_biking"},
    "swim": {"lap_swimming", "open_water_swimming", "swimming"},
    "gym": {"strength_training", "fitness_equipment", "indoor_cardio", "hiit"},
    "walk_hike": {"walking", "hiking", "casual_walking"},
    "yoga": {"yoga", "breathwork", "pilates"},
}


def sport_group(type_key: str | None) -> str:
    for group, keys in SPORT_GROUPS.items():
        if type_key in keys:
            return group
    return "other"


def trimp_estimate(activity: dict, resting_hr: float, max_hr: float) -> float:
    """Banister TRIMP: duration * HR reserve fraction * weighting.
    Rough, but only used when Garmin didn't provide a load value."""
    avg_hr = activity.get("avg_hr")
    dur_min = (activity.get("duration_s") or 0) / 60
    if not avg_hr or not dur_min or max_hr <= resting_hr:
        return dur_min * 0.5  # last resort: assume very easy effort
    hrr = max(0.0, min(1.0, (avg_hr - resting_hr) / (max_hr - resting_hr)))
    return dur_min * hrr * 0.64 * math.exp(1.92 * hrr)


def ewma_series(daily_load: dict, dates: list, tc: int, seed: float = 0.0) -> dict:
    k = 1 - math.exp(-1 / tc)
    out, prev = {}, seed
    for d in dates:
        prev = prev + (daily_load.get(d, 0.0) - prev) * k
        out[d] = prev
    return out


def main():
    activities = json.loads((DATA_DIR / "activities.json").read_text())
    daily = json.loads((DATA_DIR / "daily.json").read_text()) if (DATA_DIR / "daily.json").exists() else {}

    # Reference HRs for the TRIMP fallback, derived from the data itself
    max_hr = max((a.get("max_hr") or 0 for a in activities), default=0) or 185
    rhr_values = [v.get("resting_hr") for v in daily.values() if v.get("resting_hr")]
    resting_hr = (sum(rhr_values) / len(rhr_values)) if rhr_values else 55

    daily_load = defaultdict(float)
    for a in activities:
        if not a.get("start"):
            continue
        day = a["start"][:10]
        load = a.get("training_load") or trimp_estimate(a, resting_hr, max_hr)
        a["load"] = round(load, 1)
        a["sport"] = sport_group(a.get("type"))
        daily_load[day] += load

    import zoneinfo as _zi
    try:
        _tz = _zi.ZoneInfo(__import__("os").environ.get("TIMEZONE", "Australia/Sydney"))
        today = dt.datetime.now(_tz).date()
    except Exception:
        today = dt.date.today()
    first = min(daily_load.keys(), default=today.isoformat())
    start = dt.date.fromisoformat(first)
    dates = [(start + dt.timedelta(days=i)).isoformat()
             for i in range((today - start).days + 1)]

    # Seed EWMA with average daily load from first 14 days to reduce warmup bias
    seed_days = [d for d in dates[:14] if d in daily_load]
    seed = sum(daily_load[d] for d in seed_days) / len(seed_days) if seed_days else 0.0

    ctl = ewma_series(daily_load, dates, CTL_TC, seed)
    atl = ewma_series(daily_load, dates, ATL_TC, seed)

    series = []
    for i, d in enumerate(dates):
        prev = dates[i - 1] if i else d
        series.append({
            "date": d,
            "load": round(daily_load.get(d, 0.0), 1),
            "ctl": round(ctl[d], 1),
            "atl": round(atl[d], 1),
            "tsb": round(ctl[prev] - atl[prev], 1),
        })

    # Weekly per-sport aggregates (ISO weeks, last 8)
    weeks = defaultdict(lambda: defaultdict(float))
    for a in activities:
        if not a.get("start"):
            continue
        d = dt.date.fromisoformat(a["start"][:10])
        wk = d.isocalendar()
        key = f"{wk.year}-W{wk.week:02d}"
        weeks[key][a["sport"]] += (a.get("duration_s") or 0) / 3600
        weeks[key]["_load"] += a.get("load", 0)
    weekly = [
        {"week": k, "hours": {s: round(h, 2) for s, h in v.items() if s != "_load"},
         "load": round(v["_load"])}
        for k, v in sorted(weeks.items())
    ][-8:]

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "current": series[-1] if series else None,
        "series": series,
        "weekly": weekly,
        "reference": {"max_hr_observed": max_hr, "resting_hr_avg": round(resting_hr, 1)},
    }
    (DATA_DIR / "metrics.json").write_text(json.dumps(out, indent=1))
    # Rewrite activities with derived load + sport fields for the dashboard
    (DATA_DIR / "activities.json").write_text(json.dumps(activities, indent=1))
    print(f"Metrics written. Current: {out['current']}")


if __name__ == "__main__":
    main()
