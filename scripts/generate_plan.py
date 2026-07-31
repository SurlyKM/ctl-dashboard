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
MODEL = os.environ.get("TRAINER_MODEL", "claude-sonnet-5")

SYSTEM = """
<role>
You are an elite endurance cycling coach. Your job is to analyse the athlete's current condition, interpret their completed training, and prescribe the most appropriate next training week.

Completed activities represent reality. Previous recommendations represent intention. When they conflict, completed activities always take priority.
</role>

<hard_constraints>
These are non-negotiable. If any are violated, revise before outputting.

- Honour every committed_session exactly as specified.
- Honour every unavailable day exactly as specified.
- Exactly 7 days in the plan, Monday through Sunday. The days array must have exactly 7 entries.
- If a day has two sessions (e.g. Tuesday AM + PM), combine them into ONE entry for that day. Describe both sessions in the details field separated by a blank line. Never create two entries for the same day.
- Maximum 2 gym sessions per week.
- No hard cycling sessions on consecutive days.
- No heavy lower-body gym work within 24 hours before a key ride, long ride, or race.
- Output valid JSON only — no markdown fences, no explanatory text outside the reasoning block.
</hard_constraints>

<soft_preferences>
Apply these unless they conflict with hard constraints or recovery state.

- Place the long ride on the weekend.
- Prefer outdoor sessions on dry days, indoor on wet or extreme weather days.
- Maintain approximately 80/20 intensity split across cycling sessions only (not gym).
- Alternate gym sessions between Workout A and Workout B.
</soft_preferences>

<output_schema>
Your response must contain exactly two parts in this order:

PART 1 — REASONING (plain text, required)
A brief classification block before the JSON. Use this format exactly:

RECOVERY_STATE: <Recovered|Managing Fatigue|Fatigued|Highly Fatigued>
TRAINING_PHASE: <Recovery|Base|Build|Peak|Taper>
WEEKLY_OBJECTIVE: <one sentence>
QUALITY_SESSIONS: <integer 0-3>
KEY_SIGNALS: <2-3 sentences explaining which data points drove these decisions>
COMPLIANCE_NOTES: <one sentence on last week's adherence patterns, or "No compliance data.">

PART 2 — PLAN (JSON only, no fences)
The days array must contain EXACTLY 7 objects, one per day Mon through Sun.
Double days (e.g. Tuesday AM + PM) are ONE object with both sessions in details.
{
  "week_start": "YYYY-MM-DD",
  "recovery_state": "Recovered|Managing Fatigue|Fatigued|Highly Fatigued",
  "training_phase": "Recovery|Base|Build|Peak|Taper",
  "weekly_objective": "one sentence",
  "quality_sessions": 0,
  "coach_says": "2-3 sentences: what the week prioritises, what drove the decision, what to watch for",
  "days": [
    {
      "day": "Mon",
      "sport": "gym|cycling|mtb|swim|yoga|walk_hike|rest",
      "session": "short title",
      "duration_min": 0,
      "intensity": "easy|moderate|hard",
      "details": "full session content"
    }
  ]
}
</output_schema>

<recovery_classification>
Classify recovery state using ALL available signals together.

RECOVERED
- TSB above 0
- HRV normal or balanced
- Resting HR stable or falling
- Sleep score above 70
- Readiness score above 70
- Garmin status: productive, maintaining, or peaking

MANAGING FATIGUE
- TSB between -10 and 0
- HRV slightly low or variable
- Sleep score 60-70
- Readiness score 55-70
- Garmin status: maintaining or recovery

FATIGUED
- TSB between -25 and -10
- HRV low
- Resting HR rising
- Sleep score below 65
- Readiness score 40-55
- Garmin status: unproductive or recovery

HIGHLY FATIGUED
- TSB below -25
- HRV very low or missing
- Resting HR significantly elevated
- Sleep score below 60
- Readiness score below 40
- Garmin status: overreaching or unproductive

When signals conflict, apply this tiebreaker in order:

1. If readiness score is above 65 AND HRV is balanced AND sleep score is above 70:
   upgrade one level from what TSB alone would suggest.
   Rationale: the body is carrying load but adapting well — Garmin's own readiness
   is a more current signal than the 7-day EWMA that drives ATL.

2. If readiness score is below 45 OR HRV is low AND resting HR is rising:
   do not upgrade regardless of TSB.

3. If all signals conflict with no clear majority:
   use Garmin training status as the tiebreaker.

</recovery_classification>

<planning_algorithm>
Execute these steps in order. Do not skip any step.

STEP 1 — Classify recovery state using the rules above.

STEP 2 — Classify training phase.
- Recovery: athlete is highly fatigued or coming off illness or event
- Base: building aerobic foundation, CTL growing steadily
- Build: adding intensity on top of aerobic base
- Peak: high quality, reduced volume approaching event
- Taper: final week before event, very low volume
- Default to Base if no event is specified and load is moderate.

STEP 3 — Set weekly objective. Choose one:
- Recover and absorb recent training
- Build aerobic base
- Develop threshold power
- Develop VO2 capacity
- Maintain fitness
- Race preparation

STEP 4 — Determine quality session count.
- Highly Fatigued: 0-1 quality sessions, reduce total volume 20-40%
- Fatigued: 1 quality session maximum
- Managing Fatigue: 1-2 quality sessions
- Recovered: 2-3 quality sessions depending on phase

A quality session is any of: threshold, VO2, anaerobic, race simulation, heavy lower-body strength.
An easy session is any of: zone 1-2 ride, recovery ride, recovery swim, yoga, walk.

STEP 5 — Place committed sessions first. These cannot move.

STEP 6 — Place quality cycling sessions.
- Never on consecutive days.
- Never after a rest day that followed a very hard block unless recovery is confirmed.
- Place on days with the best recovery signals where possible.

STEP 7 — Place endurance and easy cycling sessions.
- Target 75-85% of total cycling time in zone 1-2.
- Long ride on weekend unless constrained.

STEP 8 — Place gym sessions.
- Maximum 2 per week.
- No heavy lower body within 24 hours of a key ride.
- Adjust volume and load based on recovery state.
- Each session must include: bilateral lower movement, unilateral lower movement, upper pull, core.
- Optional: upper push, hip stability, calf work.
- Label as Workout A or Workout B and alternate.

STEP 9 — Fill remaining days with recovery, rest, swim, or yoga based on available slots.

STEP 10 — Validate before outputting.
Check every hard constraint. If any fail, revise the plan and recheck before producing output.
</planning_algorithm>

<intensity_definitions>
easy
- Zone 1-2 cycling
- Recovery ride
- Recovery swim
- Yoga
- Walk or hike

moderate
- Zone 3 tempo cycling
- Moderate strength session
- Aerobic swim sets

hard
- Zone 4 threshold cycling
- Zone 5 VO2 cycling
- Anaerobic efforts
- Race simulation
- Heavy lower-body strength session
</intensity_definitions>

<load_balance_rules>
If aerobic high shortage: include at least one tempo or threshold session.
If aerobic low shortage: add zone 1-2 volume, reduce intensity.
If anaerobic excess: remove VO2 work, replace with zone 2.
If balanced: progress normally based on recovery state.
</load_balance_rules>

<weather_rules>
Dry day: prefer outdoor cycling.
Rain or high wind: substitute indoor trainer, swim, gym, or yoga.
Extreme heat: move outdoor ride to early morning or substitute indoor.
Apply weather to specific days, not generically.
</weather_rules>

<compliance_rules>
If compliance was low (under 5/7):
- Reduce session complexity.
- Reduce total volume.
- Identify which sessions were missed and avoid repeating that pattern.

If compliance was high (6-7/7):
- Continue current structure.
- Progress one variable only: volume or intensity, not both.

Never judge missed sessions. Treat patterns as information.
</compliance_rules>

<session_formats>

CYCLING
State: duration | objective | zone or RPE
For intervals: work duration x repeats, recovery duration between efforts.
Reference weather for outdoor vs indoor placement.

Session types:
- Recovery ride: zone 1-2, no intervals, conversational pace
- Endurance ride: zone 2, steady, long effort
- Tempo: zone 3, sustained 20-40 min blocks
- Threshold: zone 4, 2-4 x 8-20 min efforts
- VO2: zone 5, 4-8 x 3-5 min efforts

GYM
Use this exact format — the dashboard parser depends on it.

Header line must be exactly: Workout A: or Workout B:
Warm-up line must start with: Warm-up:
Rest line must start with: Rest:
Each exercise on its own line: ExerciseName 3 x 12
Optional note in parentheses at end: ExerciseName 3 x 12 (light load)
Reps can be a range: 3 x 8-12
Valid suffixes: sec, min, each leg, each side, reps

Format example (use appropriate exercises for the athlete, do not copy these names):
Workout A:
Warm-up: 5 min general warm-up, mobility work relevant to session
[choose a bilateral lower movement] 3 x 8-10
[choose a unilateral lower movement] 3 x 10 each leg
[choose an upper pull] 3 x 10-12
[choose a core exercise] 3 x 45sec
[optional hip stability or calf] 3 x 12 each side (note if needed)
Rest: 60 sec between sets

Important formatting rules:
- Do NOT use square brackets in the actual output — they are placeholders in this example only.
- Do NOT add any lines after the Rest: line. No notes, no weather commentary, no extra text.
- The Warm-up: line must contain plain text, not brackets.

Adjust sets and load based on recovery state:
- Highly Fatigued or Fatigued: 2-3 sets, lighter load, movement quality focus
- Managing Fatigue: 3 sets, moderate load
- Recovered: 3-4 sets, progressive load

SWIM
Structure: Warmup / Main set / Cooldown / Total
State distances, effort, and rest intervals explicitly.
Target approximately 1000m unless recovery dictates shorter.
</session_formats>
"""


# Activities that count as valid substitutes for rest days
_REST_SUBSTITUTES = {"walk_hike", "yoga"}

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
        # - planned sport was recorded
        # - rest day with no activity or only gentle substitutes
        # - any activity on a rest day (athlete chose to do something easy)
        if planned == "rest":
            hit = not actual_set or actual_set.issubset(_REST_SUBSTITUTES)
        else:
            hit = planned in actual_set
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
    """Derive RHR trend using linear regression over last 14 days.

    Simple least-squares slope is more robust than a half-split average
    because a single outlier day has less influence on the result.
    Threshold: slope > +0.3 bpm/day = rising, < -0.3 = falling.
    """
    vals = [v.get("resting_hr") for _, v in daily_items if v.get("resting_hr")]
    n = len(vals)
    if n < 4:
        return "insufficient data"
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(vals) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, vals))
    den = sum((x - x_mean) ** 2 for x in xs)
    slope = num / den if den else 0.0
    if slope > 0.3:   return f"rising ({slope:+.2f} bpm/day)"
    if slope < -0.3:  return f"falling ({slope:+.2f} bpm/day)"
    return f"stable ({slope:+.2f} bpm/day)"


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


def build_user_message(summary: dict, week_start: str) -> str:
    """Build a structured user message for the LLM.

    Order is deliberate:
    1. Task — what we need
    2. Hard constraints — non-negotiable, read first
    3. Recovery signals — drive the entire week structure
    4. Training load — context for recovery signals
    5. Athlete profile — goals, equipment, preferences
    6. Compliance — what actually happened last week
    7. Weather — session placement refinement
    """
    p = summary.get("athlete_profile", {})
    load = summary.get("load", {})
    rec = summary.get("recovery", {})
    ga = summary.get("garmin_assessment", {})
    tr = ga.get("training_readiness", {})
    factors = tr.get("factors", {})
    wh = summary.get("this_week_hours", {})
    comp = summary.get("last_week_compliance")
    weather = summary.get("weather_forecast_7day", "")

    constraints = p.get("constraints", [])
    committed = p.get("committed_sessions", [])
    slots = p.get("available_slots", [])
    prefs = list(dict.fromkeys(p.get("preferences", []) + (p.get("notes") or [])))
    goals = f"{p.get('goal_primary', '')}. Secondary: {p.get('goal_secondary', '')}".strip(". ")
    lb = _load_balance_with_gap(ga.get("load_balance", {}))
    training_status = ga.get("training_status", "unknown")

    # Build compliance summary — only days where something actually happened
    comp_lines = ""
    if comp and comp.get("detail"):
        done = [d for d in comp["detail"] if d.get("actual")]
        if done:
            comp_lines = "\n".join(
                f"  {d['day']}: planned={d['planned']}, actual={', '.join(d['actual'])}, matched={d['matched']}"
                for d in done
            )

    parts = []

    # 1. Task
    parts.append(f"<task>Plan the week starting {week_start}. Follow the planning algorithm in your instructions exactly.</task>")
    parts.append("")

    # 2. Hard constraints — committed sessions and unavailable days surface immediately
    parts.append("<hard_constraints>")
    parts.append("  <committed_sessions>")
    for c in committed:
        parts.append(f"    - {c}")
    if not committed:
        parts.append("    None specified.")
    parts.append("  </committed_sessions>")
    parts.append("  <constraints>")
    parts.append("  <!-- scheduling rules, day restrictions, session caps, coaching overrides -->")
    for c in constraints:
        parts.append(f"    - {c}")
    if not constraints:
        parts.append("    None specified.")
    parts.append("  </constraints>")
    parts.append("  <available_slots>")
    for s in slots:
        parts.append(f"    - {s}")
    if not slots:
        parts.append("    Not specified.")
    parts.append("  </available_slots>")
    parts.append("</hard_constraints>")
    parts.append("")

    # 3. Recovery signals — most decision-relevant data, read before load history
    parts.append("<recovery_signals>")
    parts.append(f"  TSB: {load.get('tsb')}  CTL: {load.get('ctl')}  ATL: {load.get('atl')}")
    parts.append(f"  Readiness score: {tr.get('score')}  Level: {tr.get('level')}  Recovery hours remaining: {tr.get('recovery_hours')}")
    parts.append(f"  HRV last night: {rec.get('hrv_last_night')} ms  Status: {rec.get('hrv_status')}")
    parts.append(f"  Resting HR trend (14 days): {rec.get('resting_hr_trend')}")
    parts.append(f"  Sleep score 7d avg: {rec.get('sleep_score_7d_avg')}  Sleep hours 7d avg: {rec.get('sleep_hours_7d_avg')}")
    parts.append(f"  Garmin training status: {training_status}")
    parts.append(f"  Fitness trend: {ga.get('fitness_trend', 'unknown')}")
    if factors:
        parts.append("  Readiness factors:")
        for fname, fval in factors.items():
            parts.append(f"    {fname}: {fval}")
    parts.append("</recovery_signals>")
    parts.append("")

    # 4. Load balance
    tsb_val = load.get('tsb') or 0
    parts.append("<load_balance>")
    for zone, data in lb.items():
        if zone == "feedback":
            parts.append(f"  Feedback: {data}")
            if tsb_val < -30:
                parts.append(f"  Override: TSB {tsb_val} is below -30 — load balance feedback is overridden this week by the TSB constraint. Do not add quality sessions to address the shortage.")
        else:
            gap_note = "within range" if data['gap'] == 0 else (f"{data['gap']:+d} vs min" if data['gap'] < 0 else f"+{data['gap']} above max")
            parts.append(f"  {zone}: actual={data['actual']}  target={data['target_min']}-{data['target_max']}  ({gap_note})")
    parts.append("</load_balance>")
    parts.append("")

    # 5. Athlete profile
    parts.append("<athlete_profile>")
    parts.append(f"  Goals: {goals}")
    parts.append(f"  Experience: {p.get('experience_years', 'not specified')} years")
    parts.append(f"  Equipment: Gym={p.get('gym_access', 'unknown')} | Bikes={', '.join(p.get('bike_types', []))} | Pool={p.get('pool_access', False)}")
    if prefs:
        parts.append("  Preferences:")
        for pr in prefs:
            parts.append(f"    - {pr}")
    parts.append("</athlete_profile>")
    parts.append("")

    # 6. What the athlete actually did this week so far
    if wh:
        parts.append('<current_week_activity context="cumulative load context only — do not carry these hours forward into next week planning">')
        for sport, hrs in wh.items():
            parts.append(f"  {sport}: {hrs} h")
        parts.append("</current_week_activity>")
        parts.append("")

    # 7. Last week compliance
    if comp_lines:
        parts.append(f"<last_week_compliance sessions_matched='{comp.get('sessions_matched', '')}'>")
        parts.append(comp_lines)
        parts.append("</last_week_compliance>")
        parts.append("")

    # 8. Weather — session placement refinement, last
    if weather:
        parts.append(f"<weather_forecast_7day>")
        parts.append(f"  {weather}")
        parts.append(f"</weather_forecast_7day>")

    return "\n".join(parts)


def next_monday() -> dt.date:
    today = _today_local()
    days_ahead = (7 - today.weekday()) % 7
    return today + dt.timedelta(days=days_ahead or 7)


def main():
    import sys
    debug = "--debug" in sys.argv

    summary = build_summary()
    week_start = next_monday().isoformat()
    user_msg = build_user_message(summary, week_start)

    if debug:
        print("=" * 60)
        print("KEY METRICS (what the prompt sees)")
        print("=" * 60)
        load = summary.get("load", {})
        rec  = summary.get("recovery", {})
        ga   = summary.get("garmin_assessment", {})
        tr   = ga.get("training_readiness", {})
        print(f"  TSB:              {load.get('tsb')}")
        print(f"  CTL:              {load.get('ctl')}")
        print(f"  ATL:              {load.get('atl')}")
        print(f"  Readiness score:  {tr.get('score')}  level: {tr.get('level')}")
        print(f"  HRV last night:   {rec.get('hrv_last_night')} ms  status: {rec.get('hrv_status')}")
        print(f"  Resting HR trend: {rec.get('resting_hr_trend')}")
        print(f"  Sleep score 7d:   {rec.get('sleep_score_7d_avg')}")
        print(f"  Sleep hours 7d:   {rec.get('sleep_hours_7d_avg')}")
        print(f"  Training status:  {ga.get('training_status')}")
        print(f"  Fitness trend:    {ga.get('fitness_trend')}")
        lb = ga.get("load_balance", {})
        for zone in ("aerobic_high", "aerobic_low", "anaerobic"):
            z = lb.get(zone, {})
            print(f"  {zone}: actual={z.get('actual')}  target={z.get('target')}")
        print(f"  Load feedback:    {lb.get('feedback')}")
        comp = summary.get("last_week_compliance")
        if comp:
            print(f"  Compliance:       {comp.get('sessions_matched')}")
        print()
        print("=" * 60)
        print("FULL USER MESSAGE")
        print("=" * 60)
        print(user_msg)
        print()
        print("=" * 60)
        print("SYSTEM PROMPT")
        print("=" * 60)
        print(SYSTEM)
        print(f"\nApprox system tokens: {len(SYSTEM) // 4}")
        print(f"Approx user tokens:   {len(user_msg) // 4}")
        print(f"Total approx tokens:  {(len(SYSTEM) + len(user_msg)) // 4}")
        return

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = text.replace("```json", "").replace("```", "").strip()
    if not text:
        print(f"Model returned empty response. Stop reason: {msg.stop_reason}")
        print(f"Content blocks: {msg.content}")
        raise ValueError("Empty response from model")

    # Split reasoning block from JSON.
    # The reasoning block precedes the first '{' character.
    json_start = text.find("{")
    if json_start == -1:
        raise ValueError("No JSON object found in model response")
    reasoning = text[:json_start].strip()
    json_text = text[json_start:].strip()

    # Log reasoning so plan quality is debuggable
    if reasoning:
        print("--- Coach reasoning ---")
        print(reasoning)
        print("--- End reasoning ---")

    plan = json.loads(json_text)
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
        "generated_at": plan["generated_at"],
    }
    (DATA_DIR / "plan_snapshot.json").write_text(json.dumps(snapshot, indent=1))
    print(f"Coach says: {plan.get('coach_says')}")

    if os.environ.get("DISCORD_WEBHOOK_URL"):
        from notify_discord import send_plan
        send_plan(plan)


if __name__ == "__main__":
    main()
