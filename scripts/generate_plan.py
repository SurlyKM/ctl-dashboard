"""Sunday job: summarise recent training + recovery, ask the coach model
for a 7-day plan, save it, optionally notify Discord.

The model only ever sees aggregates already stored in the repo plus the
private athlete profile secret.
"""

import json
import os
import datetime as dt
from pathlib import Path

from coach_api import MODEL, SPORTS, DAY_NAMES, call_coach
from fetch_weather import get_forecast_table

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"


def _today_local() -> dt.date:
    """Return today in configured local timezone, not UTC."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(os.environ.get("TIMEZONE") or "Australia/Sydney")
        return dt.datetime.now(tz).date()
    except Exception:
        return dt.date.today()


# Shared by the weekly plan and the mid-week review so the two never drift
COACHING_RULES = """
<priorities>
1. Safety and recovery come first.
2. week_override, committed_sessions and constraints are non-negotiable.
3. Cycling performance is the primary goal.
4. Strength supports cycling and should not compete with it.

week_override, when present, applies to this week only. It describes a temporary change such as travel, illness or restricted equipment, and it outranks the athlete's usual pattern wherever the two conflict. Do not schedule a session the override rules out, even if it appears in committed_sessions. Say in coach_says how the override shaped the week.
</priorities>

<intensity_distribution>
Aim for roughly 80% of the week's training time at low intensity (zone 1-2). Count only the work portion of intervals as high intensity; warm-ups, recoveries and cool-downs are low. A session's intensity label describes its hardest part.

Garmin's monthly load balance is a secondary input. When its feedback conflicts with this distribution, close the gap by choosing the type of the quality sessions you already have (for example, make one of them tempo or threshold), not by adding more hard sessions.
</intensity_distribution>

<load_and_fatigue>
Load figures are Garmin training load (EPOC based), not TSS. Garmin undercounts long low-intensity rides, so absolute TSS-style thresholds don't transfer. Judge form relative to fitness instead: form_pct = TSB / CTL x 100, provided in the data.
- form_pct below -30, Garmin status unproductive or overreaching, or readiness below 40: at most one quality session, reduce total volume by roughly 20-30%.
- form_pct between -30 and -5: normal productive training.
- form_pct above +15 for more than a few days: the athlete is fresh; this is a good week for quality, unless it is a planned taper.
- Week-to-week load: avoid jumps of more than about 10% over the recent 4-week average, except when returning from a recovery week.
- Recovery weeks: after roughly three build weeks (see load_history), or when recovery signals have been poor for several days, plan a recovery week with about 30-40% less load. Keep one short session with some intensity so the athlete stays sharp.
- When a goal event is given, periodise toward it: build, then a 7-10 day taper before the event.
</load_and_fatigue>

<recovery_signals>
Read the signals together and weigh trends over single nights:
- HRV: compare last night and the 7-day average to the athlete's baseline range. A 7-day average below baseline, or status unbalanced/low, means accumulated fatigue.
- Resting HR: 5+ bpm above the 14-day average is a warning sign, especially alongside low HRV or poor sleep.
- Readiness and sleep: supporting evidence, not decisive alone.
The plan is written days ahead, so give every hard or key session a fallback: what to do instead if signals are poor that morning (for example, "ride the full duration in zone 2"). Leave fallback empty for easy and rest days.
</recovery_signals>

<scheduling>
- Never schedule hard sessions on consecutive days.
- Keep hard days hard and easy days easy. Put strength work on the same day as a hard ride (after it, ideally 6+ hours later) where the athlete's slots allow. Otherwise put it on a day at least 48 hours before the next key ride.
- No heavy lower-body work in the 48 hours before a key ride, race or long ride.
- Use the dated weather table: outdoor rides on dry days, indoor or alternative sessions on wet or very windy days.
- Use compliance data. A missed session is information: a repeatedly missed slot points to a schedule problem, not low motivation; adjust rather than repeat.
</scheduling>

<strength>
Evidence for cyclists (e.g. Rønnestad and colleagues) favours heavy strength training over light, high-rep work for cycling economy and sprint power.
- Sessions: 2 per week in base and build phases, 1 maintenance session when peaking or in a recovery week. Label them Workout A and Workout B and alternate.
- Include at least one heavy lower-body compound lift per session (back or half squat, leg press, Bulgarian split squat, Romanian deadlift, hip thrust): 3-4 sets of 4-8 reps at 1-3 reps in reserve. Add trunk and hip stability work.
- Progress load week to week when recovered. In recovery weeks cut sets by about half and keep the load.
</strength>

<session_formats>
The dashboard parses details, so follow these formats. Use newline characters between lines.

Gym:
Workout A:
Warm-up: <what to do>
<Exercise name> <sets>x<reps> (<optional note, e.g. load or RIR>)
... one exercise per line; holds as e.g. Side Plank 3x30s/side
Rest: <rest guidance>
Objective: <one sentence>

Swim (target ~1000 m unless recovery dictates shorter):
Warmup: <distance and content>
Main set: <sets with distances, effort and rest>
Cooldown: <distance>
Total: <distance>
Objective: <one sentence>

Cycling: first line "Total duration | Primary objective | Zone or RPE target", then the structure with work duration, recovery duration and repeats for any intervals, and a fuelling note for rides over 90 minutes. Session types: recovery (Z1-2, no intervals), base (Z2 steady), tempo (Z3), threshold (Z4), VO2 (Z5).

One entry per day. If a day has two sessions (e.g. AM ride + PM gym), combine them in one entry and describe both in details.
</session_formats>
"""

SYSTEM = """
<role>
You are an experienced cycling and strength coach writing next week's 7-day plan for one athlete, grounded in current exercise-science evidence and the athlete's actual data.
</role>
""" + COACHING_RULES + """
<planning_approach>
Before writing, work out: the primary objective for the week given recovery state and load history; the training phase; how many quality sessions fit; which days are hard, easy and rest; how the weather affects placement; and what last week's compliance shows. Choose sessions, exercises, intervals and distances for this athlete's current state rather than from a fixed template.
</planning_approach>

<output>
Return the plan in the required JSON schema. days has exactly 7 entries, Mon through Sun. coach_says is 2-3 sentences for the athlete: the week's objective, the data that drove it, and what to watch for. decision_log is a short bullet summary of your key decisions for the workflow log.
</output>
"""


def load_week_override(week_start: str) -> list[str]:
    """One-off constraints for a single week, from the PLAN_OVERRIDE variable.

    Value is JSON: {"week": "YYYY-MM-DD", "notes": ["...", "..."]}
    The week field is the Monday the override applies to. If it does not
    match the week being planned the override is ignored, so a stale value
    expires by itself and never has to be deleted.
    """
    raw = (os.environ.get("PLAN_OVERRIDE") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Warning: PLAN_OVERRIDE is not valid JSON, ignoring: {e}")
        return []
    if not isinstance(data, dict):
        print("Warning: PLAN_OVERRIDE must be a JSON object, ignoring")
        return []

    week = str(data.get("week", "")).strip()
    if week != week_start:
        print(f"PLAN_OVERRIDE tagged '{week}', planning {week_start}, ignored")
        return []

    notes = data.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    notes = [str(n).strip() for n in notes if str(n).strip()]
    if notes:
        print(f"PLAN_OVERRIDE active for {week}: {len(notes)} note(s)")
        for n in notes:
            print(f"  - {n}")
    else:
        print(f"PLAN_OVERRIDE tagged {week} but has no notes, ignored")
    return notes


# Activities that count as valid substitutes for rest days
_REST_SUBSTITUTES = {"walk_hike", "yoga"}

# Rides that satisfy each other. Garmin types MTB and road separately, but a
# planned ride is a planned ride, so either surface counts for either day.
# Kept in step with SPORT_EQUIV in docs/plan.js.
_SPORT_EQUIVALENTS = {
    "cycling": {"cycling", "mtb"},
    "mtb":     {"mtb", "cycling"},
}

def compliance(plan: dict, activities: list, today: dt.date | None = None) -> dict:
    """How does the plan compare with what actually happened?

    Every day is reported, including misses (the old version only passed
    days with an activity, so skipped sessions were invisible to the coach).
    status is one of: done, missed, rest_ok, today, upcoming.
    unplanned lists sports recorded that day that the plan didn't call for.
    """
    if not plan:
        return {}
    today = today or _today_local()
    week_start = dt.date.fromisoformat(plan["week_start"])

    sports_by_day: dict[int, set] = {}
    load_by_day: dict[int, float] = {}
    for a in activities:
        if not a.get("start"):
            continue
        offset = (dt.date.fromisoformat(a["start"][:10]) - week_start).days
        if 0 <= offset < 7:
            sports_by_day.setdefault(offset, set()).add(a.get("sport"))
            load_by_day[offset] = load_by_day.get(offset, 0) + (a.get("load") or 0)

    results = []
    for i, day in enumerate(plan.get("days", [])):
        date = week_start + dt.timedelta(days=i)
        planned = day.get("sport")
        actual_set = sports_by_day.get(i, set())
        if planned == "rest":
            accepted = set()
            hit = date < today and actual_set.issubset(_REST_SUBSTITUTES)
        else:
            accepted = _SPORT_EQUIVALENTS.get(planned, {planned})
            hit = bool(accepted & actual_set)
        unplanned = sorted(actual_set - accepted - _REST_SUBSTITUTES)

        if hit:
            status = "rest_ok" if planned == "rest" else "done"
        elif date > today:
            status = "upcoming"
        elif date == today:
            status = "today"
        else:
            status = "missed"
        results.append({
            "day": day.get("day"), "date": date.isoformat(),
            "planned": planned, "planned_intensity": day.get("intensity"),
            "planned_session": day.get("session"),
            "actual": sorted(actual_set), "load": round(load_by_day.get(i, 0)),
            "unplanned": unplanned, "matched": hit, "status": status,
        })
    elapsed = [r for r in results if r["status"] not in ("upcoming", "today")]
    matched = sum(1 for r in elapsed if r["matched"])
    return {"sessions_matched": f"{matched}/{len(elapsed)} elapsed days", "detail": results}


def _avg(vals):
    vals = [v for v in vals if v]
    return round(sum(vals) / len(vals), 1) if vals else None


def _translate_status(raw: str | None) -> str:
    """Convert Garmin internal training status codes to plain English."""
    mapping = {
        "PRODUCTIVE":       "productive — fitness is improving",
        "MAINTAINING":      "maintaining — load is sustaining current fitness",
        "MAINTAINING_1":    "maintaining — load is sustaining current fitness",
        "MAINTAINING_2":    "maintaining — load is sustaining current fitness",
        "PRODUCTIVE_1":     "productive — fitness is improving",
        "PRODUCTIVE_2":     "productive — fitness is improving",
        "RECOVERY_1":       "recovery — deliberately reduced load",
        "RECOVERY_2":       "recovery — deliberately reduced load",
        "RECOVERY":         "recovery — deliberately reduced load",
        "RECOVERY_ACTIVE":  "active recovery",
        "UNPRODUCTIVE_1":   "unproductive — training load not producing fitness gains",
        "UNPRODUCTIVE_2":   "unproductive — training load not producing fitness gains",
        "UNPRODUCTIVE_3":   "unproductive — high fatigue with no fitness improvement, reduce load",
        "OVERREACHING":     "overreaching — dangerously high acute load, significant rest required",
        "DETRAINING":       "detraining — insufficient load to maintain fitness",
        "PEAKING":          "peaking — well-positioned for performance",
    }
    return mapping.get(raw or "", raw or "unknown")


def _translate_trend(code: int | None) -> str:
    """Convert Garmin fitness trend integer to plain English."""
    mapping = {1: "declining", 2: "stable", 3: "improving"}
    return mapping.get(code, "unknown")  # type: ignore[arg-type]


def _translate_feedback(raw: str | None) -> str:
    """Convert Garmin load balance feedback codes to plain English."""
    mapping = {
        "AEROBIC_HIGH_SHORTAGE":   "aerobic high shortage — not enough tempo/threshold work, increase quality sessions",
        "AEROBIC_LOW_SHORTAGE":    "aerobic low shortage — not enough easy volume, add zone 1-2 riding",
        "ANAEROBIC_SHORTAGE":      "anaerobic shortage — not enough high-intensity work",
        "AEROBIC_HIGH_EXCESS":     "aerobic high excess — too much tempo/threshold, reduce intensity",
        "AEROBIC_LOW_EXCESS":      "aerobic low excess — too much easy volume",
        "ANAEROBIC_EXCESS":        "anaerobic excess — too many hard efforts, reduce high-intensity work",
        "BALANCED":                "balanced — load distribution is within target ranges",
    }
    return mapping.get(raw or "", raw or "unknown")


def _resting_hr_trend(daily_items: list) -> str:
    """Derive a simple trend from the last 14 days of resting HR."""
    vals = [v.get("resting_hr") for _, v in daily_items if v.get("resting_hr")]
    if len(vals) < 4:
        return "insufficient data"
    mid = len(vals) // 2
    first_half = sum(vals[:mid]) / mid
    second_half = sum(vals[mid:]) / (len(vals) - mid)
    diff = second_half - first_half
    if diff > 2:   return "rising"
    if diff < -2:  return "falling"
    return "stable"


def _avg_n(vals, nd=1):
    """Mean of the non-null values, or None."""
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), nd) if vals else None


def load_athlete_profile() -> dict:
    profile_path = DATA_DIR / "athlete_profile.json"
    profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
    private_raw = os.environ.get("ATHLETE_PROFILE_PRIVATE", "").strip()
    if private_raw:
        try:
            profile.update(json.loads(private_raw))
        except json.JSONDecodeError as e:
            print(f"Warning: ATHLETE_PROFILE_PRIVATE is not valid JSON: {e}")
    return profile


def build_summary(weather_dates: list[dt.date] | None = None) -> dict:
    """Everything the coach sees, as a plain dict.

    weather_dates: the days to include in the forecast table (the plan week
    for the Sunday job, the remaining days for the mid-week review).
    """
    metrics    = json.loads((DATA_DIR / "metrics.json").read_text())
    daily      = json.loads((DATA_DIR / "daily.json").read_text())
    activities = json.loads((DATA_DIR / "activities.json").read_text())
    status_path = DATA_DIR / "training_status.json"
    garmin_status = json.loads(status_path.read_text()) if status_path.exists() else {}
    today = _today_local()

    days_sorted = sorted(daily.items())
    last7, last14, last60 = days_sorted[-7:], days_sorted[-14:], days_sorted[-60:]
    latest = days_sorted[-1][1] if days_sorted else {}

    # Hours by sport in the current Monday-based week (on Sunday: the week
    # that is just finishing)
    monday = today - dt.timedelta(days=today.weekday())
    week_hours: dict = {}
    for a in activities:
        start = (a.get("start") or "")[:10]
        if start and start >= monday.isoformat():
            sport = a.get("sport", "other")
            week_hours[sport] = round(week_hours.get(sport, 0) + (a.get("duration_s") or 0) / 3600, 1)

    # Compliance for the plan currently on file, once its week is (nearly)
    # over. The Sunday job runs on the plan's last day, so the old check
    # (today >= week_start + 7) never passed and the coach never saw it.
    plan_path = DATA_DIR / "plan.json"
    last_plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
    compliance_data = None
    if last_plan.get("week_start"):
        plan_start = dt.date.fromisoformat(last_plan["week_start"])
        if plan_start + dt.timedelta(days=6) <= today < plan_start + dt.timedelta(days=14):
            compliance_data = compliance(last_plan, activities, today)

    # Load trend
    series = metrics.get("series") or []
    cur = metrics.get("current") or {}

    def ctl_ago(n):
        return series[-1 - n]["ctl"] if len(series) > n else None
    ctl = cur.get("ctl")
    tsb = cur.get("tsb")
    form_pct = round(tsb / ctl * 100) if ctl and tsb is not None else None

    # HRV and resting HR relative to the athlete's own baseline
    def hrv_vals(rows):
        return [v.get("hrv_last_night") for _, v in rows]

    summary = {
        "today": today.isoformat(),
        "athlete_profile": load_athlete_profile(),
        "load": {
            "ctl": ctl, "atl": cur.get("atl"), "tsb": tsb, "form_pct": form_pct,
            "ctl_7d_ago": ctl_ago(7), "ctl_28d_ago": ctl_ago(28),
        },
        "load_history": metrics.get("weekly") or [],
        "this_week_hours": week_hours,
        "recovery": {
            "sleep_score_7d_avg": _avg([v.get("sleep_score") for _, v in last7]),
            "sleep_hours_7d_avg": _avg([(v.get("sleep_s") or 0) / 3600 for _, v in last7]),
            "hrv_last_night":     latest.get("hrv_last_night"),
            "hrv_7d_avg":         _avg_n(hrv_vals(last7)),
            "hrv_60d_avg":        _avg_n(hrv_vals(last60)),
            "hrv_baseline_low":   latest.get("hrv_baseline_low"),
            "hrv_baseline_high":  latest.get("hrv_baseline_high"),
            "hrv_status":         latest.get("hrv_status"),
            "resting_hr_last":    latest.get("resting_hr"),
            "resting_hr_14d_avg": _avg_n([v.get("resting_hr") for _, v in last14]),
            "resting_hr_trend":   _resting_hr_trend(last14),
        },
        "garmin_assessment": {
            "training_status":  _translate_status(garmin_status.get("training_status")),
            "fitness_trend":    _translate_trend(garmin_status.get("fitness_trend")),
            "training_readiness": {
                "score":              garmin_status.get("readiness_score"),
                "level":              garmin_status.get("readiness_level"),
                "feedback":           garmin_status.get("readiness_feedback"),
                "recovery_hours":     garmin_status.get("readiness_recovery_hours"),
                "factors": {
                    "hrv":            garmin_status.get("readiness_hrv_factor"),
                    "acwr":           garmin_status.get("readiness_acwr_factor"),
                    "stress_history": garmin_status.get("readiness_stress_history"),
                    "sleep_history":  garmin_status.get("readiness_sleep_history"),
                    "sleep_tonight":  garmin_status.get("readiness_sleep_factor"),
                    "recovery_time":  garmin_status.get("readiness_recovery_factor"),
                },
            },
            "load_balance": {
                "aerobic_high": {"actual": garmin_status.get("load_aerobic_high"), "target": garmin_status.get("load_aerobic_high_target")},
                "aerobic_low":  {"actual": garmin_status.get("load_aerobic_low"),  "target": garmin_status.get("load_aerobic_low_target")},
                "anaerobic":    {"actual": garmin_status.get("load_anaerobic"),     "target": garmin_status.get("load_anaerobic_target")},
                "feedback":     _translate_feedback(garmin_status.get("load_balance_feedback")),
            },
        },
    }
    if compliance_data:
        summary["last_week_compliance"] = compliance_data
    weather = get_forecast_table(weather_dates or [])
    if weather:
        summary["weather_forecast"] = weather
    return summary


def _load_balance_with_gap(load_balance: dict) -> dict:
    """Add gap field to each load balance zone for easier LLM reasoning."""
    out = {}
    for zone in ("aerobic_high", "aerobic_low", "anaerobic"):
        entry = load_balance.get(zone, {})
        actual = entry.get("actual")
        target = entry.get("target") or [None, None]
        if actual is not None and target[0] is not None:
            if actual < target[0]:
                gap = actual - target[0]   # negative = below min
            elif actual > target[1]:
                gap = actual - target[1]   # positive = above max
            else:
                gap = 0                    # within range
            out[zone] = {"actual": actual, "target_min": target[0], "target_max": target[1], "gap": gap}
    out["feedback"] = load_balance.get("feedback", "unknown")
    return out


def _v(value, suffix: str = "") -> str:
    """Format a value for the prompt; missing data reads as 'unknown', not 'None'."""
    return "unknown" if value is None or value == "" else f"{value}{suffix}"


def compliance_lines(comp: dict) -> list[str]:
    lines = []
    for d in comp.get("detail", []):
        actual = ", ".join(d["actual"]) or "nothing recorded"
        extra = f", unplanned={', '.join(d['unplanned'])}" if d.get("unplanned") else ""
        lines.append(f"    {d['day']} {d['date']}: planned={d['planned']} ({_v(d.get('planned_intensity'))}) "
                     f"\"{d.get('planned_session') or ''}\", actual={actual}, load={d['load']}, "
                     f"status={d['status']}{extra}")
    return lines


def athlete_profile_xml(p: dict, overrides: list[str] | None) -> list[str]:
    """Profile block shared with the mid-week review."""
    goals = f"{p.get('goal_primary', '')}. Secondary: {p.get('goal_secondary', '')}".strip(". ")
    prefs = list(dict.fromkeys((p.get("preferences") or []) + (p.get("notes") or [])))
    parts = ["<athlete_profile>",
             f"  <goals>{goals}</goals>"]
    if p.get("goal_event"):
        parts.append(f"  <goal_event date='{_v(p.get('goal_event_date'))}'>{p['goal_event']}</goal_event>")
    parts += [
        f"  <experience>{_v(p.get('experience_years'))} years</experience>",
        f"  <equipment>Gym: {_v(p.get('gym_access'))} | Bikes: {', '.join(p.get('bike_types', [])) or 'unknown'} | Pool: {p.get('pool_access', False)}</equipment>",
    ]

    def block(tag, items):
        parts.append(f"  <{tag}>")
        parts.extend(f"    - {x}" for x in items)
        if not items:
            parts.append("    None.")
        parts.append(f"  </{tag}>")
    block("committed_sessions", p.get("committed_sessions") or [])
    if overrides:
        parts.append("  <week_override>")
        parts.append("    This week only. Takes precedence over committed_sessions and constraints where they conflict.")
        parts.extend(f"    - {o}" for o in overrides)
        parts.append("  </week_override>")
    block("constraints", p.get("constraints") or [])
    block("available_slots", p.get("available_slots") or [])
    if prefs:
        block("preferences", prefs)
    parts.append("</athlete_profile>")
    return parts


def training_data_xml(summary: dict) -> list[str]:
    """Load, recovery and Garmin blocks shared with the mid-week review."""
    load = summary.get("load", {})
    rec = summary.get("recovery", {})
    ga = summary.get("garmin_assessment", {})
    tr = ga.get("training_readiness", {})
    lb = _load_balance_with_gap(ga.get("load_balance", {}))

    parts = ["  <load scale='Garmin training load (EPOC based), not TSS'>",
             f"    <current ctl='{_v(load.get('ctl'))}' atl='{_v(load.get('atl'))}' tsb='{_v(load.get('tsb'))}' form_pct='{_v(load.get('form_pct'), '%')}' />",
             f"    <fitness_trend ctl_7d_ago='{_v(load.get('ctl_7d_ago'))}' ctl_28d_ago='{_v(load.get('ctl_28d_ago'))}' />",
             "  </load>",
             "  <load_history note='ISO weeks, most recent last; the final week may be incomplete'>"]
    for w in summary.get("load_history", [])[-6:]:
        hrs = ", ".join(f"{k} {v}h" for k, v in sorted(w.get("hours", {}).items()))
        total = round(sum(w.get("hours", {}).values()), 1)
        parts.append(f"    <week id='{w.get('week')}' load='{w.get('load')}' hours='{total}'>{hrs}</week>")
    parts.append("  </load_history>")

    parts.append("  <current_week_so_far>")
    for sport, hrs in (summary.get("this_week_hours") or {}).items():
        parts.append(f"    <sport name='{sport}' hours='{hrs}' />")
    parts.append("  </current_week_so_far>")

    baseline = (f"{rec['hrv_baseline_low']}-{rec['hrv_baseline_high']} ms (Garmin)"
                if rec.get("hrv_baseline_low") and rec.get("hrv_baseline_high")
                else f"~{_v(rec.get('hrv_60d_avg'), ' ms')} (60-day average)")
    parts += [
        "  <recovery>",
        f"    <hrv last_night='{_v(rec.get('hrv_last_night'), ' ms')}' avg_7d='{_v(rec.get('hrv_7d_avg'), ' ms')}' baseline='{baseline}' status='{_v(rec.get('hrv_status'))}' />",
        f"    <resting_hr last='{_v(rec.get('resting_hr_last'), ' bpm')}' avg_14d='{_v(rec.get('resting_hr_14d_avg'), ' bpm')}' trend='{_v(rec.get('resting_hr_trend'))}' />",
        f"    <sleep score_7d_avg='{_v(rec.get('sleep_score_7d_avg'))}' hours_7d_avg='{_v(rec.get('sleep_hours_7d_avg'))}' />",
        "  </recovery>",
        "  <garmin_assessment>",
        f"    <training_status>{_v(ga.get('training_status'))}</training_status>",
        f"    <fitness_trend>{_v(ga.get('fitness_trend'))}</fitness_trend>",
    ]
    if tr.get("score") is not None:
        parts.append(f"    <readiness score='{tr.get('score')}' level='{_v(tr.get('level'))}' recovery_hours='{_v(tr.get('recovery_hours'))}'>")
        for fname, fval in (tr.get("factors") or {}).items():
            parts.append(f"      <factor name='{fname}'>{_v(fval)}</factor>")
        parts.append("    </readiness>")
    parts.append("    <load_balance period='last 4 weeks'>")
    for zone, data in lb.items():
        if zone == "feedback":
            parts.append(f"      <feedback>{data}</feedback>")
        else:
            parts.append(f"      <{zone} actual='{data['actual']}' target_min='{data['target_min']}' target_max='{data['target_max']}' gap='{data['gap']}' />")
    parts.append("    </load_balance>")
    parts.append("  </garmin_assessment>")
    return parts


def build_user_message(summary: dict, week_start: str,
                       overrides: list[str] | None = None) -> str:
    """Build a structured XML user message for the LLM."""
    p = summary.get("athlete_profile", {})
    comp = summary.get("last_week_compliance")
    weather = summary.get("weather_forecast", "")
    week_end = (dt.date.fromisoformat(week_start) + dt.timedelta(days=6)).isoformat()

    parts = [f"<task>Today is {summary.get('today')}. Plan the week Monday {week_start} to Sunday {week_end}.</task>", ""]
    parts += athlete_profile_xml(p, overrides)
    parts += ["", "<training_data>"]
    parts += training_data_xml(summary)
    if comp:
        parts.append(f"  <last_week_compliance week_start='{comp['detail'][0]['date']}' matched='{comp.get('sessions_matched', '')}'>")
        parts += compliance_lines(comp)
        parts.append("  </last_week_compliance>")
    parts.append("</training_data>")
    parts.append("")
    if weather:
        parts += ["<weather_forecast>", weather, "</weather_forecast>"]
    else:
        parts.append("<weather_forecast>unavailable</weather_forecast>")
    return "\n".join(parts)


def next_monday() -> dt.date:
    """Monday of the week being planned.

    The job is scheduled for Sunday evening, but GitHub cron can start runs
    hours late. If it slips past midnight into Monday, plan the week that
    is starting today rather than the one after it.
    """
    today = _today_local()
    if today.weekday() == 0:
        return today
    return today + dt.timedelta(days=7 - today.weekday())


VALID_SPORTS = set(SPORTS)
VALID_INTENSITY = {"easy", "moderate", "hard"}


def validate_plan(plan: dict, week_start: str) -> dict:
    """Check the plan's shape before it overwrites the live one.

    Structured outputs guarantee the schema, but not that there are exactly
    seven days. Raises ValueError on anything the dashboard can't render, so
    a bad response fails the workflow loudly and the previous plan stays up.
    """
    days = plan.get("days")
    if not isinstance(days, list) or len(days) != 7:
        raise ValueError(f"Plan must have exactly 7 days, got {len(days) if isinstance(days, list) else days!r}")
    for i, d in enumerate(days):
        if not isinstance(d, dict) or not d.get("session"):
            raise ValueError(f"Day {i} is missing a session: {d!r}")
        if d.get("sport") not in VALID_SPORTS:
            raise ValueError(f"Day {i} has unknown sport {d.get('sport')!r}")
        if d.get("intensity") not in VALID_INTENSITY:
            d["intensity"] = "easy"
        d["day"] = DAY_NAMES[i]
        try:
            d["duration_min"] = int(d.get("duration_min") or 0)
        except (TypeError, ValueError):
            d["duration_min"] = 0
        d["details"] = str(d.get("details") or "")
        d["fallback"] = str(d.get("fallback") or "")
    if plan.get("week_start") != week_start:
        print(f"Model returned week_start {plan.get('week_start')!r}, correcting to {week_start}")
        plan["week_start"] = week_start
    return plan


def print_decision_log(plan: dict) -> None:
    """Show the coach's reasoning summary in the Actions log; not published."""
    log = plan.pop("decision_log", "")
    if log:
        print("--- Coach decisions ---")
        print(log)
        print("--- End decisions ---")


def main():
    import sys
    debug = "--debug" in sys.argv
    week_start_d = next_monday()
    week_start = week_start_d.isoformat()
    summary = build_summary([week_start_d + dt.timedelta(days=i) for i in range(7)])
    overrides = load_week_override(week_start)
    user_msg = build_user_message(summary, week_start, overrides)

    if debug:
        # Inspect the prompt without spending tokens or overwriting plan.json
        print("=" * 60 + "\nUSER MESSAGE\n" + "=" * 60)
        print(user_msg)
        print(f"\nModel: {MODEL}")
        print(f"Approx system tokens: {len(SYSTEM) // 4}")
        print(f"Approx user tokens:   {len(user_msg) // 4}")
        return

    plan = validate_plan(call_coach(SYSTEM, user_msg), week_start)
    print_decision_log(plan)
    plan["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    (DATA_DIR / "plan.json").write_text(json.dumps(plan, indent=1))
    print(f"Plan written for week starting {plan.get('week_start')}")

    # Snapshot of the state the plan was written against, for the mid-week review
    cur = summary.get("load", {})
    rec = summary.get("recovery", {})
    readiness = summary.get("garmin_assessment", {}).get("training_readiness", {})
    snapshot = {
        "tsb": cur.get("tsb"),
        "ctl": cur.get("ctl"),
        "atl": cur.get("atl"),
        "readiness_score": readiness.get("score"),
        "readiness_level": readiness.get("level"),
        "hrv_status": rec.get("hrv_status"),
        "resting_hr_14d_avg": rec.get("resting_hr_14d_avg"),
        "override": overrides,
        "generated_at": plan["generated_at"],
    }
    (DATA_DIR / "plan_snapshot.json").write_text(json.dumps(snapshot, indent=1))
    print(f"Coach says: {plan.get('coach_says')}")

    if os.environ.get("DISCORD_WEBHOOK_URL"):
        from notify_discord import send_plan
        send_plan(plan)


if __name__ == "__main__":
    main()
