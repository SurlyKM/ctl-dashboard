"""Pull activities and daily wellness from Garmin Connect.

Two data sources (hybrid):

  1. Garmin Personal API (official, API key). Everything it covers:
     activities, resting HR, stress, Body Battery, sleep, HRV, VO2 max,
     fitness age. Auth: GARMIN_PERSONAL_API_KEY, exchanged for a 24h bearer
     token at the start of each run.

  2. garminconnect (unofficial, token login). ONLY for the three things the
     Personal API does not expose: training readiness, training status and
     load balance. Auth: GARMIN_TOKEN_B64 from scripts/setup_token.py.
     FAIL SOFT: if this side breaks, those fields are left out (blank on
     the dashboard), the rest of the sync still lands, and a Discord alert
     fires once when it breaks and once when it recovers.

Privacy model: WHITELIST ONLY. Every field written to the repo is named
explicitly below. Anything Garmin returns that is not whitelisted
(GPS, location names, activity names, owner names, device IDs, user IDs)
never touches disk. If you want a new field on the dashboard, add it
here deliberately.

Local testing (nothing touches docs/data):
    python scripts/sync_garmin.py --debug          fetch and print, write nothing
    python scripts/sync_garmin.py --out /tmp/new   write all outputs to /tmp/new
Then compare against the live site data:
    python scripts/compare_sync.py /tmp/new
"""

import argparse
import base64
import datetime as dt
import getpass
import io
import json
import os
import sys
import tarfile
from pathlib import Path

from garmin_personal_api import (
    GarminPersonalApiClient,
    GarminPersonalApiError,
    PersonalApiAuthenticator,
    resolve_api_key,
)

REPO_DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"

LOOKBACK_DAYS = 200         # how much history to retain in activities.json / daily.json
SYNC_DAYS = 7               # how far back each normal sync fetches
STATUS_LOOKBACK_DAYS = 3    # readiness/status: walk back this many days to find data
VO2_LOOKBACK_DAYS = 28      # maxmet: days per request
VO2_MAX_AGE_DAYS = 180      # maxmet: give up looking for a value older than this
VO2_HISTORY_DAYS = 400      # how long to keep VO2 max history

# Per-request day limits from the Personal API spec (HRV has no stated
# limit, so it is chunked conservatively)
SLEEP_MAX_DAYS = 31
HRV_MAX_DAYS = 28
SUMMARY_MAX_DAYS = 365


def _today_local() -> dt.date:
    """Return today's date in the configured local timezone.

    Reads TIMEZONE environment variable (e.g. 'Australia/Sydney').
    Falls back to the machine's date if not set or invalid.
    """
    tz_name = os.environ.get("TIMEZONE", "Australia/Sydney")
    try:
        import zoneinfo
        return dt.datetime.now(zoneinfo.ZoneInfo(tz_name)).date()
    except Exception:
        return dt.date.today()


def _chunks(start: dt.date, end: dt.date, size: int):
    """Yield inclusive (start, end) windows of at most `size` days."""
    cur = start
    while cur <= end:
        stop = min(cur + dt.timedelta(days=size - 1), end)
        yield cur, stop
        cur = stop + dt.timedelta(days=1)


# ===========================================================================
# Source 1: Personal API
# ===========================================================================
def personal_api_client() -> GarminPersonalApiClient:
    """Exchange the API key for a bearer token and return a client.

    resolve_api_key() checks GARMIN_PERSONAL_API_KEY, then the key file
    ~/.config/garmin/connect-personal-api-key (handy locally, keeps the key
    out of shell history). This side is required: if it fails the run
    fails, and GitHub's failed-workflow email is the signal.
    """
    try:
        api_key = resolve_api_key()
    except GarminPersonalApiError:
        # Running by hand in a terminal: ask, like the probe scripts did.
        # Never prompts in Actions (no TTY there), so a missing secret
        # still fails the run loudly.
        if not sys.stdin.isatty() or os.environ.get("GITHUB_ACTIONS") == "true":
            raise
        api_key = getpass.getpass("Personal API key: ").strip()
    with PersonalApiAuthenticator() as auth:
        token = auth.obtain_token(api_key)
    print("Personal API: token obtained")
    return GarminPersonalApiClient(token.access_token)


def slim_activity(a: dict) -> dict:
    """Reduce a raw Garmin activity to whitelisted, non-identifying fields.

    Field names are identical between the Personal API's activity search
    and garminconnect's, so this is unchanged from the old sync.
    """
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


def fetch_activities(api: GarminPersonalApiClient, existing: list[dict], full: bool) -> list[dict]:
    """Fetch recent activities and merge into existing history.

    Full LOOKBACK_DAYS window on first run (or --backfill), otherwise the
    last SYNC_DAYS to pick up new activities and Garmin's retroactive load
    recalculations. Dedupes by ID (fresh wins) and trims to LOOKBACK_DAYS.
    """
    today = _today_local()
    fetch_days = LOOKBACK_DAYS if (full or not existing) else SYNC_DAYS
    start = today - dt.timedelta(days=fetch_days)

    # The search endpoint pages; 100 per page keeps it to one or two calls
    raw, offset = [], 0
    while True:
        page = api.search_activities(start=offset, limit=100,
                                     start_date=start, end_date=today) or []
        raw += page
        if len(page) < 100:
            break
        offset += 100

    fresh = [slim_activity(a) for a in raw]
    print(f"Fetched {len(fresh)} activities ({fetch_days}d window)")

    by_id = {a["id"]: a for a in existing}
    for a in fresh:
        by_id[a["id"]] = a

    cutoff = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    merged = [a for a in by_id.values() if (a.get("start") or "") >= cutoff]
    merged.sort(key=lambda x: x["start"] or "")
    print(f"Activities after merge: {len(merged)} (was {len(existing)})")
    return merged


def fetch_daily(api: GarminPersonalApiClient, existing: dict, full: bool) -> dict:
    """Per-day wellness from three range endpoints, merged by date.

    The old sync made 3 calls per day; range endpoints cover the whole
    window in a handful of calls. Window: SYNC_DAYS normally, LOOKBACK_DAYS
    on first run or --backfill. Days in the window with any data replace
    the stored entry; older days are left as they are.

    Output field names are unchanged. Where they now come from:
      resting_hr        v2 daily summary  heartRate.restingValue
      stress_avg        v2 daily summary  stress.avgLevel
      body_battery_high v2 daily summary  bodyBattery.maxValue
      body_battery_low  v2 daily summary  bodyBattery.minValue
      sleep_s           sleep summaries   sleepTimeSeconds
      sleep_score       sleep summaries   sleepScores.overall.value
      hrv_*             HRV summaries     lastNightAvg, weeklyAvg, status, baseline.*
    """
    today = _today_local()
    days = LOOKBACK_DAYS if (full or not existing) else SYNC_DAYS
    start = today - dt.timedelta(days=days)
    fresh: dict[str, dict] = {}

    def entry(day):
        return fresh.setdefault(day, {})

    # --- resting HR, stress, Body Battery ---
    for a, b in _chunks(start, today, SUMMARY_MAX_DAYS):
        resp = api.get_daily_summaries(a, b) or {}
        for s in resp.get("data") or []:
            day = s.get("calendarDate")
            if not day:
                continue
            e = entry(day)
            e["resting_hr"] = (s.get("heartRate") or {}).get("restingValue")
            e["stress_avg"] = (s.get("stress") or {}).get("avgLevel")
            bb = s.get("bodyBattery") or {}
            e["body_battery_high"] = bb.get("maxValue")
            e["body_battery_low"] = bb.get("minValue")

    # --- sleep ---
    for a, b in _chunks(start, today, SLEEP_MAX_DAYS):
        for s in api.get_daily_sleep_summaries(a, b) or []:
            day = s.get("calendarDate")
            if not day:                 # the API appends an undated "tonight" row
                continue
            e = entry(day)
            e["sleep_s"] = s.get("sleepTimeSeconds")
            e["sleep_score"] = ((s.get("sleepScores") or {}).get("overall") or {}).get("value")

    # --- HRV ---
    for a, b in _chunks(start, today, HRV_MAX_DAYS):
        resp = api.get_daily_hrv_summaries(a, b) or {}
        for h in resp.get("hrvSummaries") or []:
            day = h.get("calendarDate")
            if not day:
                continue
            e = entry(day)
            e["hrv_last_night"] = h.get("lastNightAvg")
            e["hrv_weekly_avg"] = h.get("weeklyAvg")
            e["hrv_status"] = h.get("status")
            # Personal "balanced" range, so the coach can compare against
            # the athlete's own baseline rather than population norms
            baseline = h.get("baseline") or {}
            e["hrv_baseline_low"] = baseline.get("balancedLow")
            e["hrv_baseline_high"] = baseline.get("balancedUpper")

    daily = dict(existing)
    for day, e in fresh.items():
        if any(v is not None for v in e.values()):
            daily[day] = e

    cutoff = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    daily = {k: v for k, v in daily.items() if k >= cutoff}
    print(f"Daily wellness: {len(fresh)} days fetched ({days}d window), {len(daily)} stored")
    return daily


def fetch_vo2_and_fitness_age(api: GarminPersonalApiClient) -> dict:
    """Newest VO2 max per sport, plus fitness age.

    Both sports are stored; the dashboard and VO2 history use generic
    (what Garmin Connect shows), with cycling kept for reference.

    maxmet/daily returns one entry per day with optional 'generic' and
    'cycling' blocks; each sport keeps its own newest value because they
    update on different days.
    """
    today = _today_local()
    out = {"vo2max_generic": None, "vo2max_cycling": None, "fitness_age": None}

    try:
        # Walk back in 28-day windows, newest first, until both sports have
        # a value. Garmin's old training status returned the most recent
        # value however old; cycling VO2 only updates on qualifying rides,
        # so after a break it can be weeks old. Usually this is one call.
        end = today
        oldest = today - dt.timedelta(days=VO2_MAX_AGE_DAYS)
        while end > oldest and None in (out["vo2max_generic"], out["vo2max_cycling"]):
            start = max(end - dt.timedelta(days=VO2_LOOKBACK_DAYS - 1), oldest)
            rows = api.get_daily_vo2_max(start, end) or []
            for sport in ("generic", "cycling"):
                if out[f"vo2max_{sport}"] is not None:
                    continue
                dated = [(r[sport].get("calendarDate") or "", r[sport].get("vo2MaxPreciseValue"))
                         for r in rows if isinstance(r.get(sport), dict)]
                dated = [d for d in dated if d[1] is not None]
                if dated:
                    newest_date, value = max(dated)
                    out[f"vo2max_{sport}"] = value
                    out[f"vo2max_{sport}_date"] = newest_date
            end = start - dt.timedelta(days=1)
    except Exception as e:
        print(f"vo2max fetch failed: {e}")

    try:
        fa = (api.get_fitness_age(today) or {}).get("fitnessAge")
        out["fitness_age"] = round(fa) if fa is not None else None
    except Exception as e:
        print(f"fitness_age fetch failed: {e}")

    print(f"VO2max: generic {out['vo2max_generic']} ({out.get('vo2max_generic_date')}) "
          f"cycling {out['vo2max_cycling']} ({out.get('vo2max_cycling_date')}) | "
          f"fitness age {out['fitness_age']}")
    return out


# ===========================================================================
# Source 2: garminconnect (readiness, training status, load balance only)
# ===========================================================================
def garminconnect_login():
    """Token-only login, unchanged from the old sync. Returns a client.

    Raises on any failure so the caller can fail soft.
    """
    from garminconnect import Garmin

    tokenstore = Path(os.path.expanduser("~/.garminconnect"))
    blob = os.environ.get("GARMIN_TOKEN_B64")
    if blob:
        tokenstore.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(blob)), mode="r:gz") as tar:
            for member in tar.getmembers():
                # Refuse path traversal; we only ever wrote flat filenames
                if member.isfile() and "/" not in member.name and ".." not in member.name:
                    tar.extract(member, tokenstore, filter="data")
        print("garminconnect: token store restored from GARMIN_TOKEN_B64")
    elif not tokenstore.exists():
        raise RuntimeError("no GARMIN_TOKEN_B64 and no ~/.garminconnect token store")

    client = Garmin()
    client.login(str(tokenstore))
    print("garminconnect: logged in")
    return client


def _candidate_dates():
    """Today, then back STATUS_LOOKBACK_DAYS days.

    Asking only for 'today' was why training status kept blanking: early
    runs come before the watch has synced that day, and when travelling,
    'today' in TIMEZONE can be a date Garmin has nothing for yet.
    """
    today = _today_local()
    return [(today - dt.timedelta(days=i)).isoformat() for i in range(STATUS_LOOKBACK_DAYS)]


def fetch_garmin_assessment(client) -> dict:
    """Training status, load balance and readiness via garminconnect.

    Privacy: strips userId, deviceId, deviceName, imageURL and raw timestamps.
    Only performance and recovery metrics are stored. Returns {} for any
    block with no data in the lookback window.
    """
    out = {}

    # --- training status + load balance (one call) ---
    for day in _candidate_dates():
        raw = client.get_training_status(day) or {}
        status_map = (raw.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
        balance_map = (raw.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap") or {}
        if not (status_map or balance_map):
            continue
        out["status_date"] = day
        if status_map:
            s = next(iter(status_map.values()))
            out["training_status"] = s.get("trainingStatusFeedbackPhrase")
            out["fitness_trend"] = s.get("fitnessTrend")
        if balance_map:
            b = next(iter(balance_map.values()))
            out["load_aerobic_low"] = round(b.get("monthlyLoadAerobicLow") or 0)
            out["load_aerobic_low_target"] = [b.get("monthlyLoadAerobicLowTargetMin"), b.get("monthlyLoadAerobicLowTargetMax")]
            out["load_aerobic_high"] = round(b.get("monthlyLoadAerobicHigh") or 0)
            out["load_aerobic_high_target"] = [b.get("monthlyLoadAerobicHighTargetMin"), b.get("monthlyLoadAerobicHighTargetMax")]
            out["load_anaerobic"] = round(b.get("monthlyLoadAnaerobic") or 0)
            out["load_anaerobic_target"] = [b.get("monthlyLoadAnaerobicTargetMin"), b.get("monthlyLoadAnaerobicTargetMax")]
            out["load_balance_feedback"] = b.get("trainingBalanceFeedbackPhrase")
        break

    # --- training readiness ---
    def _tr_feedback(code):
        m = {"VERY_GOOD": "very good", "GOOD": "good", "MODERATE": "moderate",
             "POOR": "poor", "VERY_POOR": "very poor"}
        return m.get(code, (code or "").lower().replace("_", " "))

    for day in _candidate_dates():
        tr_list = client.get_training_readiness(day) or []
        tr = tr_list[0] if tr_list else {}       # most recent entry first
        if tr.get("score") is None:
            continue
        recovery_min = tr.get("recoveryTime") or 0
        out["readiness_date"]             = day
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
        break

    print(f"garminconnect: status {out.get('training_status')} ({out.get('status_date')}) | "
          f"readiness {out.get('readiness_score')} ({out.get('readiness_date')})")
    return out


def garminconnect_side() -> tuple[dict, str | None]:
    """Run the whole garminconnect side, never raising.

    Returns (fields, error). error is None on success, or a short reason.
    'No data in the lookback window' counts as a failure too, because
    with a 3-day lookback that means something is wrong.
    """
    try:
        client = garminconnect_login()
        fields = fetch_garmin_assessment(client)
    except BaseException as e:      # SystemExit from library internals included
        if isinstance(e, KeyboardInterrupt):
            raise
        return {}, f"{type(e).__name__}: {e}"[:300]
    if not fields:
        return {}, f"no readiness or training status in the last {STATUS_LOOKBACK_DAYS} days"
    return fields, None


# ===========================================================================
# Outputs
# ===========================================================================
def update_vo2_history(data_dir: Path, status: dict) -> dict:
    """Append today's VO2 max to the rolling history. Generic first,
    matching the dashboard and Garmin Connect; cycling only if there's no
    generic value. Returns the history dict."""
    path = data_dir / "vo2_history.json"
    history = json.loads(path.read_text()) if path.exists() else {}
    value = status.get("vo2max_generic") or status.get("vo2max_cycling")
    if value:
        today = _today_local()
        history[today.isoformat()] = round(float(value), 1)
        cutoff = (today - dt.timedelta(days=VO2_HISTORY_DAYS)).isoformat()
        history = {k: v for k, v in history.items() if k >= cutoff}
    return history


def alert_on_change(prev_ok, now_ok: bool, error: str | None):
    """Discord alert only when garminconnect flips between working and
    broken, so a dead login doesn't post six times a day. Only fires in
    GitHub Actions, never during local testing."""
    if os.environ.get("GITHUB_ACTIONS") != "true" or prev_ok == now_ok:
        return
    if prev_ok is None and now_ok:
        return                      # first run under the new sync, nothing to report
    try:
        from notify_discord import send_alert
        if now_ok:
            send_alert("Garmin sync recovered",
                       "garminconnect is working again. Readiness, training status "
                       "and load balance are back on the dashboard.", ok=True)
        else:
            send_alert("Garmin sync: garminconnect failing",
                       f"Readiness, training status and load balance are blank until "
                       f"this is fixed. Everything else synced normally.\n\n`{error}`\n\n"
                       f"If it's an auth error, re-run scripts/setup_token.py and update "
                       f"the GARMIN_TOKEN_B64 secret.", ok=False)
    except Exception as e:
        print(f"Discord alert failed: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--debug", action="store_true",
                    help="fetch everything and print a summary; write nothing")
    ap.add_argument("--out", type=Path,
                    help="write outputs here instead of docs/data (merge base is this folder)")
    ap.add_argument("--backfill", action="store_true",
                    help=f"fetch the full {LOOKBACK_DAYS}-day window, filling any gaps")
    args = ap.parse_args()

    data_dir = args.out or REPO_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    def load(name, default):
        p = data_dir / name
        return json.loads(p.read_text()) if p.exists() else default

    # --- Personal API (required) ---
    with personal_api_client() as api:
        activities = fetch_activities(api, load("activities.json", []), args.backfill)
        daily = fetch_daily(api, load("daily.json", {}), args.backfill)
        status = fetch_vo2_and_fitness_age(api)

    # --- garminconnect (optional, fail soft) ---
    gc_fields, gc_error = garminconnect_side()
    if gc_error:
        print(f"garminconnect FAILED (fields left blank): {gc_error}")
    status.update(gc_fields)

    if args.debug:
        print("\n--debug: nothing written. Summary:")
        print(f"  activities: {len(activities)}, newest {activities[-1]['start'] if activities else None}")
        newest = max(daily) if daily else None
        print(f"  daily: {len(daily)} days, newest {newest}: {daily.get(newest)}")
        print(f"  training_status: {json.dumps(status, indent=1)}")
        return

    meta_prev = load("meta.json", {})
    (data_dir / "activities.json").write_text(json.dumps(activities, indent=1))
    (data_dir / "daily.json").write_text(json.dumps(daily, indent=1, sort_keys=True))
    (data_dir / "training_status.json").write_text(json.dumps(status, indent=1))
    history = update_vo2_history(data_dir, status)
    (data_dir / "vo2_history.json").write_text(json.dumps(history, indent=1, sort_keys=True))

    now_ok = gc_error is None
    meta = {
        "synced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "garminconnect_ok": now_ok,
    }
    if gc_error:
        # Public repo: commit only the error type. The full message can hold
        # request URLs containing your Garmin display name, so it goes only
        # to the Actions log (above) and the private Discord alert.
        meta["garminconnect_error"] = gc_error.split(":")[0]
    (data_dir / "meta.json").write_text(json.dumps(meta))
    alert_on_change(meta_prev.get("garminconnect_ok"), now_ok, gc_error)
    print(f"Wrote outputs to {data_dir}")


if __name__ == "__main__":
    main()
