"""Pull activities and daily wellness from Garmin Connect.

Privacy model: WHITELIST ONLY. Every field written to the repo is named
explicitly below. Anything Garmin returns that is not whitelisted
(GPS polylines, location names, activity names, device IDs, user IDs)
never touches disk. If you want a new field on the dashboard, add it
here deliberately.
"""

import base64
import io
import json
import os
import tarfile
import datetime as dt
from pathlib import Path

from garminconnect import Garmin

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"


def _today_local() -> dt.date:
    """Return today's date in the configured local timezone.

    Reads TIMEZONE environment variable (e.g. 'Australia/Sydney').
    Falls back to UTC if not set or invalid.
    Set this as a GitHub Actions secret or repo variable.
    """
    tz_name = os.environ.get("TIMEZONE", "Australia/Sydney")
    try:
        import zoneinfo
        return dt.datetime.now(zoneinfo.ZoneInfo(tz_name)).date()
    except Exception:
        return dt.date.today()
LOOKBACK_DAYS = 200         # how much history to retain in activities.json
SYNC_DAYS = 7               # how far back each sync fetches from Garmin
DAILY_REFRESH_DAYS = 2      # always re-fetch the last N days of wellness data


def login() -> Garmin:
    """Token-only login. No password ever reaches this script.

    Auth comes from GARMIN_TOKEN_B64: a base64 tar.gz of the garth token
    directory, produced locally by scripts/setup_token.py. Garth refreshes
    the short-lived OAuth2 access token from the long-lived OAuth1 token
    on each run, so nothing needs writing back between runs.

    Locally, an existing ~/.garminconnect token store is used if present.
    """
    tokenstore = Path(os.path.expanduser("~/.garminconnect"))
    blob = os.environ.get("GARMIN_TOKEN_B64")

    if blob:
        tokenstore.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(blob)), mode="r:gz") as tar:
            for member in tar.getmembers():
                # Refuse path traversal; we only ever wrote flat filenames
                if member.isfile() and "/" not in member.name and ".." not in member.name:
                    tar.extract(member, tokenstore, filter="data")
        print("Token store restored from GARMIN_TOKEN_B64")
    elif not tokenstore.exists():
        raise SystemExit(
            "No Garmin token found.\n"
            "Set the GARMIN_TOKEN_B64 secret, or run scripts/setup_token.py locally."
        )

    client = Garmin()
    client.login(str(tokenstore))
    print("Logged in to Garmin Connect")
    return client


def slim_activity(a: dict) -> dict:
    """Reduce a raw Garmin activity to whitelisted, non-identifying fields."""
    return {
        "id": a.get("activityId"),
        "type": (a.get("activityType") or {}).get("typeKey"),
        "start": a.get("startTimeLocal"),           # local datetime, no timezone/location info
        "duration_s": a.get("duration"),
        "distance_m": a.get("distance"),
        "elev_gain_m": a.get("elevationGain"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "avg_power": a.get("avgPower"),
        "norm_power": a.get("normPower"),
        "calories": a.get("calories"),
        "training_load": a.get("activityTrainingLoad"),  # Garmin's EPOC-based load
        "aerobic_te": a.get("aerobicTrainingEffect"),
    }


def fetch_activities(client: Garmin, existing: list[dict]) -> list[dict]:
    """Fetch recent activities and merge into existing history.

    On first run (no existing data), pulls the full LOOKBACK_DAYS window.
    On subsequent runs, only fetches the last SYNC_DAYS to pick up new
    activities and any retroactive Garmin adjustments (e.g. training load
    recalculations). Deduplicates by activity ID, preferring the freshly
    fetched version. Trims entries older than LOOKBACK_DAYS.
    """
    today = _today_local()

    # Full backfill if no existing data, otherwise just the sync window
    fetch_days = LOOKBACK_DAYS if not existing else SYNC_DAYS
    start = today - dt.timedelta(days=fetch_days)

    raw = client.get_activities_by_date(start.isoformat(), today.isoformat())
    fresh = [slim_activity(a) for a in raw]
    print(f"Fetched {len(fresh)} activities ({fetch_days}d window)")

    # Merge: index existing by ID, then overlay fresh (fresh wins on duplicates)
    by_id = {a["id"]: a for a in existing}
    for a in fresh:
        by_id[a["id"]] = a

    # Trim anything older than the retention window
    cutoff = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    merged = [a for a in by_id.values() if (a.get("start") or "") >= cutoff]
    merged.sort(key=lambda x: x["start"] or "")

    print(f"Activities after merge: {len(merged)} (was {len(existing)})")
    return merged


def fetch_daily(client: Garmin, existing: dict) -> dict:
    """Fetch per-day wellness. Only fetches days we don't already have,
    plus the most recent days (which keep updating during the day)."""
    today = _today_local()
    daily = dict(existing)
    for i in range(LOOKBACK_DAYS + 1):
        day = today - dt.timedelta(days=i)
        key = day.isoformat()
        if key in daily and i >= DAILY_REFRESH_DAYS:
            continue
        entry = {}
        try:
            stats = client.get_stats(key) or {}
            entry["resting_hr"] = stats.get("restingHeartRate")
            entry["stress_avg"] = stats.get("averageStressLevel")
            entry["body_battery_high"] = stats.get("bodyBatteryHighestValue")
            entry["body_battery_low"] = stats.get("bodyBatteryLowestValue")
        except Exception as e:
            print(f"stats {key}: {e}")
        try:
            sleep = (client.get_sleep_data(key) or {}).get("dailySleepDTO") or {}
            entry["sleep_s"] = sleep.get("sleepTimeSeconds")
            scores = sleep.get("sleepScores") or {}
            entry["sleep_score"] = (scores.get("overall") or {}).get("value")
        except Exception as e:
            print(f"sleep {key}: {e}")
        try:
            hrv = (client.get_hrv_data(key) or {}).get("hrvSummary") or {}
            entry["hrv_last_night"] = hrv.get("lastNightAvg")
            entry["hrv_weekly_avg"] = hrv.get("weeklyAvg")
            entry["hrv_status"] = hrv.get("status")
        except Exception as e:
            print(f"hrv {key}: {e}")
        if any(v is not None for v in entry.values()):
            daily[key] = entry
    # Drop days that have aged out of the lookback window
    cutoff = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    daily = {k: v for k, v in daily.items() if k >= cutoff}
    print(f"Daily wellness covers {len(daily)} days")
    return daily


def fetch_training_status(client: Garmin) -> dict:
    """Pull Garmin's own training status, load balance and VO2 max.

    Privacy: strips userId, deviceId, deviceName, imageURL and raw timestamps.
    Only performance and recovery metrics are stored.
    """
    today = _today_local().isoformat()
    try:
        raw = client.get_training_status(today) or {}
    except Exception as e:
        print(f"training_status fetch failed: {e}")
        return {}

    out = {}

    # VO2 max -- use precise value, no user/device identifiers
    vo2 = raw.get("mostRecentVO2Max") or {}
    generic = vo2.get("generic") or {}
    cycling = vo2.get("cycling") or {}
    out["vo2max_generic"] = generic.get("vo2MaxPreciseValue")
    out["vo2max_cycling"] = cycling.get("vo2MaxPreciseValue")
    out["fitness_age"] = generic.get("fitnessAge")

    # Training status -- whitelist only, no userId, deviceId, deviceName, imageURL, timestamp
    # Only store fields that are actually used by generate_plan.py or the dashboard
    status_map = (raw.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
    if status_map:
        s = next(iter(status_map.values()))
        out["training_status"] = s.get("trainingStatusFeedbackPhrase")
        out["fitness_trend"] = s.get("fitnessTrend")

    # Load balance -- no deviceId, deviceName, imageURL
    balance_map = (raw.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap") or {}
    if balance_map:
        b = next(iter(balance_map.values()))
        out["load_aerobic_low"] = round(b.get("monthlyLoadAerobicLow") or 0)
        out["load_aerobic_low_target"] = [b.get("monthlyLoadAerobicLowTargetMin"), b.get("monthlyLoadAerobicLowTargetMax")]
        out["load_aerobic_high"] = round(b.get("monthlyLoadAerobicHigh") or 0)
        out["load_aerobic_high_target"] = [b.get("monthlyLoadAerobicHighTargetMin"), b.get("monthlyLoadAerobicHighTargetMax")]
        out["load_anaerobic"] = round(b.get("monthlyLoadAnaerobic") or 0)
        out["load_anaerobic_target"] = [b.get("monthlyLoadAnaerobicTargetMin"), b.get("monthlyLoadAnaerobicTargetMax")]
        out["load_balance_feedback"] = b.get("trainingBalanceFeedbackPhrase")

    # Training readiness -- the most interpretable single recovery signal
    try:
        tr_list = client.get_training_readiness(today) or []
        # Use the most recent entry (first in list)
        tr = tr_list[0] if tr_list else {}
        def _tr_feedback(code):
            m = {"VERY_GOOD": "very good", "GOOD": "good", "MODERATE": "moderate",
                 "POOR": "poor", "VERY_POOR": "very poor"}
            return m.get(code, (code or "").lower().replace("_", " "))
        recovery_min = tr.get("recoveryTime") or 0
        out["readiness_score"]            = tr.get("score")
        out["readiness_level"]            = (tr.get("level") or "").lower()
        out["readiness_feedback"]         = (tr.get("feedbackShort") or "").lower().replace("_", " ")
        out["readiness_recovery_hours"]   = round(recovery_min / 60) if recovery_min else None
        out["readiness_hrv_factor"]       = _tr_feedback(tr.get("hrvFactorFeedback"))
        out["readiness_acwr_factor"]      = _tr_feedback(tr.get("acwrFactorFeedback"))
        out["readiness_sleep_factor"]     = _tr_feedback(tr.get("sleepScoreFactorFeedback"))
        out["readiness_sleep_history"]    = _tr_feedback(tr.get("sleepHistoryFactorFeedback"))
        out["readiness_stress_history"]   = _tr_feedback(tr.get("stressHistoryFactorFeedback"))
        out["readiness_recovery_factor"]  = _tr_feedback(tr.get("recoveryTimeFactorFeedback"))
        out["readiness_hrv_weekly_avg"]   = tr.get("hrvWeeklyAverage")
        print(f"Training readiness: {out.get('readiness_score')} ({out.get('readiness_level')})")
    except Exception as e:
        print(f"training_readiness fetch failed: {e}")

    print(f"Training status: {out.get('training_status')} | VO2max: {out.get('vo2max_cycling')}")
    return out


VO2_HISTORY_DAYS = 400      # how long to keep VO2 max history


def update_vo2_history(status: dict) -> None:
    """Append today's VO2 max to a rolling history file.

    Garmin only exposes the current value, so history has to be
    accumulated one sync at a time. Cycling VO2 is preferred, falling
    back to the generic value, matching what the dashboard displays.
    """
    value = status.get("vo2max_cycling") or status.get("vo2max_generic")
    if not value:
        return
    path = DATA_DIR / "vo2_history.json"
    history = json.loads(path.read_text()) if path.exists() else {}
    today = _today_local()
    history[today.isoformat()] = round(float(value), 1)
    cutoff = (today - dt.timedelta(days=VO2_HISTORY_DAYS)).isoformat()
    history = {k: v for k, v in history.items() if k >= cutoff}
    path.write_text(json.dumps(history, indent=1, sort_keys=True))
    print(f"VO2 history: {len(history)} days stored, today {history[today.isoformat()]}")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = login()

    # Load existing activities for incremental merge
    acts_path = DATA_DIR / "activities.json"
    existing_acts = json.loads(acts_path.read_text()) if acts_path.exists() else []
    activities = fetch_activities(client, existing_acts)
    acts_path.write_text(json.dumps(activities, indent=1))

    daily_path = DATA_DIR / "daily.json"
    existing = json.loads(daily_path.read_text()) if daily_path.exists() else {}
    daily = fetch_daily(client, existing)
    daily_path.write_text(json.dumps(daily, indent=1, sort_keys=True))

    training_status = fetch_training_status(client)
    (DATA_DIR / "training_status.json").write_text(json.dumps(training_status, indent=1))
    update_vo2_history(training_status)

    meta = {"synced_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    (DATA_DIR / "meta.json").write_text(json.dumps(meta))


if __name__ == "__main__":
    main()
