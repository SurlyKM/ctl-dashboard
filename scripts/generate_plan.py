"""Sunday job: summarise recent training + recovery, ask the Trainer LLM
for a 7-day plan, save it, optionally notify Discord.

The model only ever sees aggregates already stored in the repo, so it
receives no more information than the public dashboard shows.
"""

import json
import os
import datetime as dt
from pathlib import Path

import anthropic
from fetch_weather import get_weather_summary

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"


def _today_local() -> dt.date:
    """Return today in configured local timezone, not UTC."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(os.environ.get("TIMEZONE", "Australia/Sydney"))
        return dt.datetime.now(tz).date()
    except Exception:
        return dt.date.today()
MODEL = os.environ.get("TRAINER_MODEL", "claude-fable-5")

SYSTEM = """
<role>
You are an elite cycling and strength coach. Analyse the athlete data provided and write a 7-day training plan grounded in current exercise science evidence.
</role>

<priorities>
1. Safety and recovery — always first
2. Honour week_override, committed_sessions and constraints — non-negotiable
3. Cycling performance — primary goal
4. Strength — supports cycling, never competes with it
5. 80/20 rule — roughly 80% easy, 20% hard across the week

week_override, when present, applies to this week only. It describes a temporary change in circumstances such as travel, illness or restricted equipment, and it takes precedence over the athlete's usual pattern wherever the two conflict. Do not schedule a session the override rules out, even if it appears in committed_sessions. Say in coach_says how the override shaped the week.
</priorities>

<decision_rules>
- TSB below -30 OR Garmin UNPRODUCTIVE: one quality session max, reduce total volume
- AEROBIC_HIGH_SHORTAGE: include at least one tempo or threshold ride this week
- Excess anaerobic load: reduce high-intensity work
- Never schedule hard sessions on consecutive days
- Never schedule heavy lower-body the day before a key ride, race, or long endurance session
- Use all recovery signals together — readiness score, HRV, resting HR trend, sleep, TSB
- Weather forecast: use it to place outdoor sessions on dry days and substitute indoor/yoga on wet days
</decision_rules>

<planning_approach>
Before writing the plan, reason through:
- What is the athlete's primary objective this week given their recovery state?
- How many quality sessions are appropriate given TSB, readiness, and Garmin status?
- Which days should be hard, which easy, which rest?
- How does the weather affect session placement?
- What does the compliance data (if present) tell you about what is and isn't working?

Select sessions, exercises, intervals and distances based on evidence and the athlete's current state — not a fixed template. A deeply fatigued athlete needs different gym work than a fresh one. A cyclist with aerobic high shortage needs different intervals than one who is peaking.
</planning_approach>

<gym_format>
Label sessions Workout A or Workout B. Alternate across the week. Maximum 2 gym sessions per week.
Select exercises based on the week's objectives and recovery state:
- Prioritise posterior chain, single-leg strength, hip stability, and core
- Deload weeks: reduce load and sets, keep movement quality
- Normal weeks: follow progressive overload principles
- Avoid heavy quad-dominant work the day before a key ride

Format each exercise as: ExerciseName SetsxReps (or SetsxDuration for holds)
Include a warm-up line and rest period guidance.
Add a brief objective note at the end explaining the session focus.
</gym_format>

<swim_format>
Structure: Warmup / Main set / Cooldown / Total distance
Select set structure based on the week's objectives:
- Recovery weeks: easy continuous or low-intensity drills
- Base weeks: aerobic sets with moderate rest
- Quality weeks: threshold or sprint sets
State distances, effort level, and rest intervals explicitly.
Target ~1000m unless recovery dictates shorter.
</swim_format>

<cycling_format>
State: Total duration | Primary objective | Zone or RPE target
Structure intervals explicitly with work duration, recovery duration, and number of repeats.
Select session type based on the athlete's needs:
- Recovery: Zone 1-2, conversational pace, no intervals
- Base: Zone 2 steady, long and easy
- Tempo: Zone 3, sustained effort
- Threshold: Zone 4, hard but controlled
- VO2: Zone 5, short hard efforts
Reference weather where relevant (outdoor vs indoor).
</cycling_format>

<response_schema>
Respond ONLY with valid JSON. No markdown fences. No explanatory text.
Exactly 7 entries Monday through Sunday. One entry per day — if Tuesday has AM and PM sessions, combine them into one entry and describe both in details.

{
  "week_start": "YYYY-MM-DD",
  "coach_says": "2-3 sentences explaining the week's primary objective, what the data drove the decision, and what to watch for",
  "days": [
    {
      "day": "Mon",
      "sport": "gym|cycling|mtb|swim|yoga|walk_hike|rest",
      "session": "short title",
      "duration_min": 0,
      "intensity": "easy|moderate|hard",
      "details": "full session content using the formats above"
    }
  ]
}
</response_schema>
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

def compliance(plan: dict, activities: list) -> dict:
    """How did last week's plan compare to what actually happened?

    week_start in the plan JSON is the Monday of the plan week.
    Day index 0 = Monday, 1 = Tuesday ... 6 = Sunday.
    """
    if not plan:
        return {}
    week_start = dt.date.fromisoformat(plan["week_start"])

    done_sports_by_day = {}
    for a in activities:
        if not a.get("start"):
            continue
        d = dt.date.fromisoformat(a["start"][:10])
        offset = (d - week_start).days
        if 0 <= offset < 7:
            done_sports_by_day.setdefault(offset, set()).add(a.get("sport"))

    results = []
    for i, day in enumerate(plan.get("days", [])):
        planned = day.get("sport")
        actual_set = done_sports_by_day.get(i, set())
        actual = sorted(actual_set)
        # Count as done if:
        # - planned sport was recorded, or an accepted equivalent (MTB / road)
        # - rest day with no activity or only gentle substitutes
        if planned == "rest":
            hit = not actual_set or actual_set.issubset(_REST_SUBSTITUTES)
        else:
            accepted = _SPORT_EQUIVALENTS.get(planned, {planned})
            hit = bool(accepted & actual_set)
        results.append({"day": day.get("day"), "planned": planned,
                        "actual": actual, "matched": hit})
    matched = sum(1 for r in results if r["matched"])
    return {"sessions_matched": f"{matched}/7", "detail": results}


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


def build_summary() -> dict:
    metrics    = json.loads((DATA_DIR / "metrics.json").read_text())
    daily      = json.loads((DATA_DIR / "daily.json").read_text())
    activities = json.loads((DATA_DIR / "activities.json").read_text())

    status_path = DATA_DIR / "training_status.json"
    garmin_status = json.loads(status_path.read_text()) if status_path.exists() else {}

    profile_path = DATA_DIR / "athlete_profile.json"
    athlete_profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
    private_raw = os.environ.get("ATHLETE_PROFILE_PRIVATE", "").strip()
    if private_raw:
        try:
            athlete_profile.update(json.loads(private_raw))
        except json.JSONDecodeError as e:
            print(f"Warning: ATHLETE_PROFILE_PRIVATE is not valid JSON: {e}")

    last14 = sorted(daily.items())[-14:]
    last7  = last14[-7:]

    # Current week hours by sport
    import zoneinfo as _zi
    _tz = _zi.ZoneInfo(os.environ.get("TIMEZONE", "Australia/Sydney"))
    now = dt.datetime.now(_tz)
    monday = now - dt.timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    week_hours: dict = {}
    for a in activities:
        if not a.get("start"):
            continue
        try:
            start = dt.datetime.fromisoformat(a["start"])
        except ValueError:
            continue
        if start >= monday:
            sport = a.get("sport", "other")
            week_hours[sport] = round(week_hours.get(sport, 0) + (a.get("duration_s") or 0) / 3600, 1)

    # Only include compliance if the plan covers a fully completed week
    plan_path = DATA_DIR / "plan.json"
    last_plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
    compliance_data = None
    if last_plan.get("week_start"):
        week_end = dt.date.fromisoformat(last_plan["week_start"]) + dt.timedelta(days=7)
        if _today_local() >= week_end:
            compliance_data = compliance(last_plan, activities)

    cur = metrics.get("current") or {}

    summary = {
        "today": _today_local().isoformat(),
        "athlete_profile": athlete_profile,
        "load": {
            "ctl": cur.get("ctl"),
            "atl": cur.get("atl"),
            "tsb": cur.get("tsb"),
        },
        "this_week_hours": week_hours,
        "recovery": {
            "sleep_score_7d_avg": _avg([v.get("sleep_score") for _, v in last7]),
            "sleep_hours_7d_avg": _avg([(v.get("sleep_s") or 0) / 3600 for _, v in last7]),
            "hrv_last_night":     last14[-1][1].get("hrv_last_night") if last14 else None,
            "hrv_status":         last14[-1][1].get("hrv_status") if last14 else None,
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
    weather = get_weather_summary()
    if weather:
        summary["weather_forecast_7day"] = weather
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


def build_user_message(summary: dict, week_start: str,
                       overrides: list[str] | None = None) -> str:
    """Build a structured XML user message for the LLM."""
    p = summary.get("athlete_profile", {})
    load = summary.get("load", {})
    rec = summary.get("recovery", {})
    ga = summary.get("garmin_assessment", {})
    tr = ga.get("training_readiness", {})
    factors = tr.get("factors", {})
    wh = summary.get("this_week_hours", {})
    comp = summary.get("last_week_compliance")
    weather = summary.get("weather_forecast_7day", "")

    # Constraints — most important, first
    constraints = p.get("constraints", [])
    committed = p.get("committed_sessions", [])
    slots = p.get("available_slots", [])
    prefs = list(dict.fromkeys(p.get("preferences", []) + (p.get("notes") or [])))
    goals = f"{p.get('goal_primary', '')}. Secondary: {p.get('goal_secondary', '')}".strip(". ")

    # Load balance with gaps
    lb = _load_balance_with_gap(ga.get("load_balance", {}))
    training_status = ga.get("training_status", "unknown")  # already translated by build_summary

    # Only show completed compliance days
    comp_lines = ""
    if comp and comp.get("detail"):
        done = [d for d in comp["detail"] if d.get("actual")]
        if done:
            comp_lines = "\n".join(
                f"  {d['day']}: planned={d['planned']}, actual={', '.join(d['actual'])}, matched={d['matched']}"
                for d in done
            )

    parts = [f"<task>Plan the week starting {week_start}.</task>", ""]

    parts.append("<athlete_profile>")
    parts.append(f"  <goals>{goals}</goals>")
    parts.append(f"  <experience>{p.get('experience_years', '')} years</experience>")
    parts.append(f"  <equipment>Gym: {p.get('gym_access', '')} | Bikes: {', '.join(p.get('bike_types', []))} | Pool: {p.get('pool_access', False)}</equipment>")
    parts.append("")
    parts.append("  <committed_sessions>")
    for c in committed:
        parts.append(f"    - {c}")
    parts.append("  </committed_sessions>")
    parts.append("")
    if overrides:
        parts.append("  <week_override>")
        parts.append("    THIS WEEK ONLY. Non-negotiable. Takes precedence over")
        parts.append("    committed_sessions and constraints where they conflict.")
        for o in overrides:
            parts.append(f"    - {o}")
        parts.append("  </week_override>")
        parts.append("")
    parts.append("  <constraints>")
    for c in constraints:
        parts.append(f"    - {c}")
    parts.append("  </constraints>")
    parts.append("")
    parts.append("  <available_slots>")
    for s in slots:
        parts.append(f"    - {s}")
    parts.append("  </available_slots>")
    parts.append("")
    if prefs:
        parts.append("  <preferences>")
        for pr in prefs:
            parts.append(f"    - {pr}")
        parts.append("  </preferences>")
    parts.append("</athlete_profile>")
    parts.append("")

    parts.append("<training_data>")
    parts.append(f"  <load tsb='{load.get('tsb')}' ctl='{load.get('ctl')}' atl='{load.get('atl')}' />")
    parts.append("")
    parts.append("  <this_week>")
    for sport, hrs in (wh or {}).items():
        parts.append(f"    <session sport='{sport}' hours='{hrs}' />")
    parts.append("  </this_week>")
    parts.append("")
    parts.append("  <recovery>")
    parts.append(f"    <sleep score_7d_avg='{rec.get('sleep_score_7d_avg')}' hours_7d_avg='{rec.get('sleep_hours_7d_avg')}' />")
    parts.append(f"    <hrv last_night='{rec.get('hrv_last_night')}ms' status='{rec.get('hrv_status')}' />")
    parts.append(f"    <resting_hr trend='{rec.get('resting_hr_trend')}' />")
    parts.append("  </recovery>")
    parts.append("")
    parts.append("  <garmin_assessment>")
    parts.append(f"    <training_status>{training_status}</training_status>")
    parts.append(f"    <fitness_trend>{ga.get('fitness_trend', '')}</fitness_trend>")
    if tr:
        parts.append(f"    <readiness score='{tr.get('score')}' level='{tr.get('level')}' recovery_hours='{tr.get('recovery_hours')}'>")
        for fname, fval in factors.items():
            parts.append(f"      <factor name='{fname}'>{fval}</factor>")
        parts.append("    </readiness>")
    parts.append("    <load_balance>")
    for zone, data in lb.items():
        if zone == "feedback":
            parts.append(f"      <feedback>{data}</feedback>")
        else:
            parts.append(f"      <{zone} actual='{data['actual']}' target_min='{data['target_min']}' target_max='{data['target_max']}' gap='{data['gap']}' />")
    parts.append("    </load_balance>")
    parts.append("  </garmin_assessment>")
    parts.append("")
    if comp_lines:
        parts.append(f"  <last_week_compliance sessions='{comp.get('sessions_matched', '')}'>")
        parts.append(comp_lines)
        parts.append("  </last_week_compliance>")
    parts.append("</training_data>")
    parts.append("")

    if weather:
        parts.append(f"<weather_forecast>{weather}</weather_forecast>")

    return "\n".join(parts)


def next_monday() -> dt.date:
    today = _today_local()
    days_ahead = (7 - today.weekday()) % 7
    return today + dt.timedelta(days=days_ahead or 7)


def main():
    summary = build_summary()
    client = anthropic.Anthropic()
    week_start = next_monday().isoformat()
    overrides = load_week_override(week_start)
    user_msg = build_user_message(summary, week_start, overrides)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = text.replace("```json", "").replace("```", "").strip()
    if not text:
        print(f"Model returned empty response. Stop reason: {msg.stop_reason}")
        print(f"Content blocks: {msg.content}")
        raise ValueError("Empty response from model")
    plan = json.loads(text)
    plan["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    (DATA_DIR / "plan.json").write_text(json.dumps(plan, indent=1))
    print(f"Plan written for week starting {plan.get('week_start')}")

    # Save snapshot of key metrics for mid-week comparison
    cur = summary.get("load", {})
    readiness = summary.get("garmin_assessment", {}).get("training_readiness", {})
    snapshot = {
        "tsb": cur.get("tsb"),
        "ctl": cur.get("ctl"),
        "atl": cur.get("atl"),
        "readiness_score": readiness.get("score"),
        "readiness_level": readiness.get("level"),
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
