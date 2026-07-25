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
MODEL = os.environ.get("TRAINER_MODEL", "claude-haiku-4-5-20251001")

SYSTEM = """You are a pragmatic, evidence-based coach specialising in endurance
cycling and concurrent strength training.

## Principles
- Cycling performance is the primary goal. Strength work supports cycling.
- 80/20 rule: roughly 80% easy volume, 20% hard. Never two hard sessions back to back.
- When TSB is below -30 or Garmin status is UNPRODUCTIVE: recovery week only —
  one quality session max, cut total volume.
- Garmin load_balance: AEROBIC_HIGH_SHORTAGE means include at least one tempo
  or threshold ride this week. Too much anaerobic means back off hard efforts.
- Gym sessions: posterior chain, single-leg, hip stability, core. Avoid heavy
  quads the day before a hard ride.
- Friday and weekends: longer rides are fine, no time cap.
- Wednesday MUST always be easy or rest (recovers from Tuesday double day).

## Constraints from athlete profile
- Tuesday evening: committed hard MTB group ride. Non-negotiable.
- Tuesday morning: sometimes a hard road ride — making Tuesday a double hard day.
- MTB only in dry weather. Substitute yoga or easy spin if wet.
- Lunch slot: swimming only (~1 km), not gym or riding.
- Max 45-50 min for gym or structured interval sessions.

## Output format — gym sessions
Format ALL gym sessions exactly like this in the details field:

Workout A:
Bench press 3x(15-18 / 12-15 / 8-12)
Lat pulldown 3x(15-18 / 12-15 / 8-12)
Romanian DL 3x(15-18 / 12-15 / 8-12)
Single-leg press 3x(15-18 / 12-15 / 8-12)
Plank 3x45 sec

Perform 3 sets of each exercise. Rep ranges progress across sets:
1st set 15-18 reps, 2nd set 12-15 reps, 3rd set 8-12 reps.
Rest 60-90 sec between sets.

Alternate Workout A and Workout B across the week.

## Output format — swim sessions
Format swim sessions with explicit sets:
Warmup: 200m easy freestyle
Main: 4x150m (first 50m easy / next 100m strong pace), 20 sec rest
Cooldown: 100m backstroke or easy choice
Total: ~1000m

## Output format — cycling sessions
Always state duration, zone (1-5) or RPE, and structure if intervals.
Example: "75 min — 15 min warmup, 3x12 min zone 3 tempo (5 min easy between), 15 min cooldown"

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "week_start": "YYYY-MM-DD",
  "coach_says": "2-3 sentences on the key reasoning for this week",
  "days": [
    {"day": "Mon", "sport": "gym|cycling|mtb|swim|yoga|walk_hike|rest",
     "session": "short title", "duration_min": 0,
     "intensity": "easy|moderate|hard",
     "details": "concrete session content using the formats above"}
  ]
}
The days array must have exactly 7 entries, Monday to Sunday."""


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


def build_summary() -> dict:
    metrics  = json.loads((DATA_DIR / "metrics.json").read_text())
    daily    = json.loads((DATA_DIR / "daily.json").read_text())
    activities = json.loads((DATA_DIR / "activities.json").read_text())

    status_path = DATA_DIR / "training_status.json"
    garmin_status = json.loads(status_path.read_text()) if status_path.exists() else {}

    profile_path = DATA_DIR / "athlete_profile.json"
    athlete_profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
    # Merge private profile from secret if present — overrides/extends the public file
    private_raw = os.environ.get("ATHLETE_PROFILE_PRIVATE", "").strip()
    if private_raw:
        try:
            private = json.loads(private_raw)
            athlete_profile.update(private)
        except json.JSONDecodeError as e:
            print(f"Warning: ATHLETE_PROFILE_PRIVATE is not valid JSON: {e}")

    last14 = sorted(daily.items())[-14:]
    recovery = {
        "sleep_score_avg_7d":  _avg([v.get("sleep_score")          for _, v in last14[-7:]]),
        "sleep_hours_avg_7d":  _avg([(v.get("sleep_s") or 0) / 3600 for _, v in last14[-7:]]),
        "hrv_last_night":      last14[-1][1].get("hrv_last_night") if last14 else None,
        "hrv_status":          last14[-1][1].get("hrv_status")     if last14 else None,
        "resting_hr_trend_14d":[v.get("resting_hr") for _, v in last14],
    }

    plan_path = DATA_DIR / "plan.json"
    last_plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}

    return {
        "today": dt.date.today().isoformat(),
        "athlete_profile": athlete_profile,
        "load_banister": metrics.get("current"),
        "weekly_history": metrics.get("weekly"),
        "recovery": recovery,
        "garmin_assessment": {
            "vo2max":          garmin_status.get("vo2max_cycling"),
            "fitness_age":     garmin_status.get("fitness_age"),
            "training_status": garmin_status.get("training_status"),
            "fitness_trend":   garmin_status.get("fitness_trend"),
            "acwr_ratio":      garmin_status.get("garmin_acwr_ratio"),
            "acwr_status":     garmin_status.get("acwr_status"),
            "load_balance": {
                "aerobic_low":  {"actual": garmin_status.get("load_aerobic_low"),  "target": garmin_status.get("load_aerobic_low_target")},
                "aerobic_high": {"actual": garmin_status.get("load_aerobic_high"), "target": garmin_status.get("load_aerobic_high_target")},
                "anaerobic":    {"actual": garmin_status.get("load_anaerobic"),    "target": garmin_status.get("load_anaerobic_target")},
                "feedback":     garmin_status.get("load_balance_feedback"),
            },
        },
        "last_week_plan_compliance": compliance(last_plan, activities),
    }


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
