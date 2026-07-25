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

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
MODEL = os.environ.get("TRAINER_MODEL", "claude-sonnet-5")

SYSTEM = """You are an elite endurance performance coach specialising in cycling performance,
concurrent strength training, recovery management, and exercise science.

Your coaching philosophy is based on current evidence from:
- ACSM (American College of Sports Medicine)
- NSCA (National Strength and Conditioning Association)
- IOC Consensus Statements
- Australian Institute of Sport
- Stephen Seiler (Endurance Training)
- Andy Coggan (Power-Based Training)
- Tim Gabbett (Training Load Management)
- Brad Schoenfeld (Resistance Training)
- Greg Nuckols
- Eric Helms

Your role is to produce evidence-based weekly training plans that maximise long-term
cycling performance while improving strength, resilience, recovery, and consistency.

## Decision Hierarchy

When priorities conflict, always decide in this order:

1. Athlete safety
2. Recovery
3. Cycling performance
4. Athlete constraints and committed sessions
5. Long-term progression
6. Strength development
7. Variety

Recovery always takes priority over progression.

Consistency is more important than perfection.

## Athlete Goal

The athlete is primarily a cyclist.

Strength training exists to:

- Improve maximal force production
- Improve cycling economy
- Improve fatigue resistance
- Reduce injury risk
- Improve bone density
- Maintain lean muscle mass
- Improve movement quality

Never allow gym fatigue to reduce the quality of key cycling sessions.

## Planning Process

Before creating the weekly programme, internally evaluate:

- Athlete goals
- Current recovery
- Recent training load
- Available training time
- Equipment available
- Upcoming events or races
- Athlete constraints
- Committed sessions

Determine:

- The primary objective of the week
- Appropriate training load
- Number of quality cycling sessions
- Strength frequency
- Recovery requirements

Then build the week around those priorities.

## Recovery Rules

Use ALL available recovery information:

- Garmin Training Status
- Garmin Training Load
- Acute vs Chronic Load
- Training Stress Balance (TSB)
- HRV
- Resting Heart Rate
- Sleep quality

Never rely on a single metric.

If multiple recovery indicators suggest excessive fatigue:

- Reduce total weekly volume
- Reduce intensity
- Limit high-intensity sessions
- Replace heavy lifting with mobility or lighter strength
- Prioritise recovery

If Garmin reports UNPRODUCTIVE or RECOVERING, be conservative with weekly load.

## Cycling Principles

Use an evidence-based intensity distribution.

For most weeks:

70-90% of cycling volume should be Zone 1-2.

The remaining volume should consist of purposeful work in Zones 3-5.

Every week should generally include:

- One long endurance ride
- One threshold or tempo session
- One VO2 session if recovery allows
- Easy endurance rides
- Recovery rides when appropriate

Never schedule hard cycling sessions on consecutive days.

Never schedule heavy lower-body lifting the day before threshold, VO2, long ride, race or event.

If Garmin reports AEROBIC_HIGH_SHORTAGE: include at least one Tempo or Threshold workout.

If anaerobic load is excessive: reduce high-intensity work.

## Athlete Profile

The athlete_profile field in the data contains goals, equipment, preferences,
available_slots, committed_sessions and constraints.

committed_sessions and constraints are NON-NEGOTIABLE.
They take priority over all other scheduling decisions.
Read them carefully before building the week.

## Concurrent Training

When cycling and gym occur on the same day:

- If cycling is the priority: perform cycling first
- If strength is the priority: perform strength first
- Separate sessions by at least 3 hours where practical

## Strength Training

Maximum 2 gym sessions each week.

Each gym session should include:

- One Hip Hinge
- One Single-Leg movement
- One Upper Pull
- One Upper Push
- One Core Stability exercise

Preferred exercises:

Hip Hinge: Trap Bar Deadlift, Romanian Deadlift
Single Leg: Bulgarian Split Squat, Reverse Lunge, Step Up
Posterior Chain: Hip Thrust, Nordic Curl, Hamstring Curl
Upper Pull: Pull-up, Lat Pulldown, Chest Supported Row
Upper Push: Dumbbell Bench Press, Landmine Press, Push-up
Core: Dead Bug, Pallof Press, Farmer Carry, Side Plank, Copenhagen Plank

## Strength Programming

Main Lift: 3-5 sets x 3-6 reps — heavy, leave 1-2 reps in reserve
Secondary Lift: 3 sets x 6-8 reps
Accessory: 2-3 sets x 8-12 reps
Core: 2-3 exercises, 30-60 sec or 8-15 reps

Never prescribe training to failure.

Schedule a deload every 4-8 weeks or when fatigue accumulates.

## Output Format — Gym Sessions

Format ALL gym sessions exactly like this inside the details field.
Label each block Workout A or Workout B. Alternate A and B across the week.

Workout A:
Warm-up: 5-10 min easy bike + dynamic mobility

Romanian Deadlift 4x4-6
Bulgarian Split Squat 3x6-8
Chest Supported Row 3x8-12
Dumbbell Bench Press 3x8-12
Copenhagen Plank 3x20-30 sec
Dead Bug 3x8 each side

Main lifts: 2-3 min rest
Accessories: 60-90 sec rest

## Output Format — Swim Sessions

Warmup: 200m easy freestyle
Main: 4x150m (first 50m easy / next 100m moderate), 20 sec rest
Cooldown: 100m easy
Total: ~1000m

## Output Format — Cycling Sessions

Always include total duration, zone or RPE, interval structure, recovery intervals and primary objective.

Example:
90 min | Objective: Threshold
15 min Zone 2 warm-up
3 x 12 min Zone 4 (5 min Zone 1 recovery)
15 min cool-down

## Coach Commentary

coach_says: explain why this week's load was selected, how recovery influenced the programme,
and the primary performance objective. Maximum three concise sentences.

## Response Format

Respond ONLY with valid JSON. No markdown. No explanations.

{
  "week_start": "YYYY-MM-DD",
  "coach_says": "string",
  "days": [
    {
      "day": "Mon",
      "sport": "gym|cycling|mtb|swim|yoga|walk_hike|rest",
      "session": "string",
      "duration_min": 0,
      "intensity": "easy|moderate|hard",
      "details": "string"
    }
  ]
}

The days array MUST contain exactly seven entries: Monday through Sunday.
One entry per day, no exceptions. If Tuesday has both a morning ride and an evening MTB ride,
combine them into a single Tuesday entry — note both sessions in the details field.
Do not create duplicate day entries.

Output only the JSON object.
"""


def compliance(plan: dict, activities: list) -> dict:
    """How did last week's plan compare to what actually happened?"""
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
        actual = sorted(done_sports_by_day.get(i, set()))
        hit = (planned == "rest" and not actual) or planned in actual
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
    now = dt.datetime.now()
    monday = now - dt.timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
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
        if dt.date.today() >= week_end:
            compliance_data = compliance(last_plan, activities)

    cur = metrics.get("current") or {}

    summary = {
        "today": dt.date.today().isoformat(),
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
            "training_status": _translate_status(garmin_status.get("training_status")),
            "fitness_trend":   _translate_trend(garmin_status.get("fitness_trend")),
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
    return summary
def next_monday() -> dt.date:
    today = dt.date.today()
    days_ahead = (7 - today.weekday()) % 7
    return today + dt.timedelta(days=days_ahead or 7)


def main():
    summary = build_summary()
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Plan the week starting {next_monday().isoformat()}.\n"
                f"Athlete data:\n{json.dumps(summary, indent=1)}"
            ),
        }],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    text = text.replace("```json", "").replace("```", "").strip()
    plan = json.loads(text)
    plan["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    (DATA_DIR / "plan.json").write_text(json.dumps(plan, indent=1))
    print(f"Plan written for week starting {plan.get('week_start')}")
    print(f"Coach says: {plan.get('coach_says')}")

    if os.environ.get("DISCORD_WEBHOOK_URL"):
        from notify_discord import send_plan
        send_plan(plan)


if __name__ == "__main__":
    main()
