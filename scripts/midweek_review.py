"""Mid-week adaptive plan review.

Runs Wednesday evening, after the day's training has synced. Looks for
things the Sunday plan didn't expect and, if it finds any, rewrites
Thursday-Sunday. Otherwise exits quietly.

Triggers (any one):
  - A one-off week override was added or changed since Sunday
  - A planned session was missed (Mon to yesterday)
  - Substantial unplanned training was recorded
  - HRV status turned unbalanced/low since the plan was written
  - Training readiness is below READINESS_FLOOR
  - Resting HR is RHR_RISE_BPM or more above its 14-day average
  - FORCE_REVIEW=1 (manual testing)

Why not "TSB moved 15 points since Sunday"? The planned Tuesday double
alone moves TSB that much, so the old trigger mostly fired on fatigue the
plan had already accounted for, and revised about half of all weeks.
Absolute signals and deviations from the plan are what the Sunday plan
couldn't know about.
"""

import json
import os
import sys
import datetime as dt
from pathlib import Path

from coach_api import MODEL, call_coach
from generate_plan import (
    COACHING_RULES, athlete_profile_xml, build_summary, compliance,
    compliance_lines, load_week_override, print_decision_log,
    training_data_xml, validate_plan, _today_local,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"

READINESS_FLOOR = 40          # absolute; a planned hard day rarely takes it this low
RHR_RISE_BPM = 5              # last night vs 14-day average
UNPLANNED_LOAD_MIN = 60       # ignore commutes and short spins
HRV_WARNING = {"UNBALANCED", "LOW", "POOR"}


def load_sunday_snapshot() -> dict:
    """Load the metrics snapshot saved when the plan was generated."""
    snapshot_path = DATA_DIR / "plan_snapshot.json"
    if snapshot_path.exists():
        return json.loads(snapshot_path.read_text())
    return {}


def find_triggers(current: dict, snapshot: dict, comp: dict,
                  overrides: list[str]) -> list[str]:
    """Everything that justifies a revision, as human-readable reasons."""
    if os.environ.get("FORCE_REVIEW", "0") == "1":
        return ["forced review"]

    reasons = []
    # Compared against the snapshot, so a new override fires once, not every run
    if overrides != (snapshot.get("override") or []):
        reasons.append("one-off week override added or changed since the plan was generated")

    for d in comp.get("detail", []):
        if d["status"] == "missed" and d["planned"] != "rest":
            reasons.append(f"missed {d['day']} {d['planned']} session \"{d.get('planned_session')}\"")
        if d["unplanned"] and d["load"] >= UNPLANNED_LOAD_MIN:
            reasons.append(f"unplanned {', '.join(d['unplanned'])} on {d['day']} (day load {d['load']})")

    rec = current.get("recovery", {})
    hrv_now = (rec.get("hrv_status") or "").upper()
    hrv_then = (snapshot.get("hrv_status") or "").upper()
    if hrv_now in HRV_WARNING and hrv_then not in HRV_WARNING:
        reasons.append(f"HRV status changed to {hrv_now.lower()} (was {hrv_then.lower() or 'unknown'} on Sunday)")

    readiness = current.get("garmin_assessment", {}).get("training_readiness", {}).get("score")
    if readiness is not None and readiness < READINESS_FLOOR:
        reasons.append(f"training readiness {readiness} is below {READINESS_FLOOR}")

    rhr, rhr_avg = rec.get("resting_hr_last"), rec.get("resting_hr_14d_avg")
    if rhr and rhr_avg and rhr - rhr_avg >= RHR_RISE_BPM:
        reasons.append(f"resting HR {rhr} bpm is {rhr - rhr_avg:.0f} above its 14-day average")
    return reasons


SYSTEM = """
<role>
You are an experienced cycling and strength coach doing a mid-week revision of this athlete's 7-day plan. Something happened that the original plan didn't anticipate (see revision_trigger). Revise only the remaining days, and change as little as the situation requires.
</role>
""" + COACHING_RULES + """
<revision_rules>
- Days marked fixed are complete or in progress. Return them exactly as they appear in the original plan.
- Return all 7 days, Mon through Sun.
- Count completed sessions when applying weekly limits (for example, gym sessions).
- A missed key session is not automatically rescheduled. Move it only if it fits the rules (no back-to-back hard days, recovery signals fine, no clash with committed sessions); otherwise drop it and say so.
- After unplanned hard training, treat it as a hard day when spacing the remaining sessions.
- When recovery signals are poor: downgrade the next hard session (its fallback is a good starting point), shorten rather than cancel a planned long ride, and add rest or yoga if needed. Never add intensity while signals are declining.
- When the trigger turns out not to warrant changes, keep the remaining days as planned and say why.
- Committed sessions cannot be moved or downgraded unless week_override rules them out.
- Re-check placement against the weather for the remaining days.
</revision_rules>

<output>
Return the full revised plan in the required JSON schema. coach_says is 2-3 sentences for the athlete explaining what changed mid-week and why (or why nothing did). decision_log is a short bullet summary of what you changed, what you considered and left alone, and why.
</output>
"""


def build_review_message(current: dict, plan: dict, snapshot: dict,
                         reasons: list[str], comp: dict, days_fixed: int,
                         overrides: list[str] | None = None) -> str:
    today = _today_local()
    load = current.get("load", {})
    tr = current.get("garmin_assessment", {}).get("training_readiness", {})
    weather = current.get("weather_forecast", "")

    parts = [
        f"<task>Mid-week review of the week starting {plan['week_start']}. Today is "
        f"{today.isoformat()} ({today.strftime('%A')}). Days 1-{days_fixed} are fixed; "
        f"revise days {days_fixed + 1}-7.</task>",
        "",
        "<revision_trigger>",
        *[f"  - {r}" for r in reasons],
        "</revision_trigger>",
        "",
        "<since_plan_was_written note='context only; planned training moves these'>",
        f"  When planned: TSB={snapshot.get('tsb')} readiness={snapshot.get('readiness_score')} HRV status={snapshot.get('hrv_status')}",
        f"  Now: TSB={load.get('tsb')} readiness={tr.get('score')} HRV status={current.get('recovery', {}).get('hrv_status')}",
        "</since_plan_was_written>",
        "",
    ]
    parts += athlete_profile_xml(current.get("athlete_profile", {}), overrides)
    parts += ["", "<training_data>"]
    parts += training_data_xml(current)
    parts.append(f"  <compliance_this_week matched='{comp.get('sessions_matched', '')}'>")
    parts += compliance_lines(comp)
    parts.append("  </compliance_this_week>")
    parts += ["</training_data>", ""]

    days = []
    for i, d in enumerate(plan.get("days", [])):
        days.append({**d, "fixed": i < days_fixed})
    parts += ["<original_plan>", json.dumps(days, indent=1), "</original_plan>", ""]
    if weather:
        parts += ["<weather_forecast_remaining_days>", weather, "</weather_forecast_remaining_days>"]
    return "\n".join(parts)


def main():
    debug = "--debug" in sys.argv

    plan_path = DATA_DIR / "plan.json"
    if not plan_path.exists():
        print("No plan found — skipping mid-week review")
        return
    plan = json.loads(plan_path.read_text())
    week_start = dt.date.fromisoformat(plan["week_start"])
    today = _today_local()
    week_end = week_start + dt.timedelta(days=6)
    if not week_start <= today <= week_end:
        print(f"Plan on file is for {plan['week_start']}, not this week — skipping")
        return

    # Runs in the evening, so today counts as done
    days_fixed = min(7, (today - week_start).days + 1)
    remaining = [today + dt.timedelta(days=i) for i in range(1, (week_end - today).days + 1)]

    current = build_summary(remaining)
    activities = json.loads((DATA_DIR / "activities.json").read_text())
    comp = compliance(plan, activities, today)
    snapshot = load_sunday_snapshot()
    overrides = load_week_override(plan["week_start"])

    reasons = find_triggers(current, snapshot, comp, overrides)
    # One data-driven revision per week. A re-run (manual, or a retried job)
    # only acts on a new override or a forced review.
    if snapshot.get("reviewed_week") == plan["week_start"]:
        reasons = [r for r in reasons if r.startswith(("forced", "one-off"))]
    print("Mid-week review triggers: " + ("; ".join(reasons) if reasons else "none"))

    if debug:
        msg = build_review_message(current, plan, snapshot, reasons or ["(none — debug run)"],
                                   comp, days_fixed, overrides)
        print("=" * 60 + "\nUSER MESSAGE\n" + "=" * 60)
        print(msg)
        print(f"\nModel: {MODEL}")
        print(f"Would revise:         {bool(reasons)}")
        print(f"Approx system tokens: {len(SYSTEM) // 4}")
        print(f"Approx user tokens:   {len(msg) // 4}")
        return

    if not reasons:
        print("No revision needed — plan stands as-is")
        return

    msg = build_review_message(current, plan, snapshot, reasons, comp, days_fixed, overrides)
    try:
        revised = validate_plan(call_coach(SYSTEM, msg), plan["week_start"])
    except (ValueError, RuntimeError) as e:
        print(f"Revised plan rejected ({e}) — keeping original plan")
        return
    print_decision_log(revised)

    # Fixed days are restored from the original in case the model edited them
    for i in range(days_fixed):
        revised["days"][i] = plan["days"][i]
    revised["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    revised["midweek_revision"] = True
    plan_path.write_text(json.dumps(revised, indent=1))

    # Fold the current state into the snapshot so a re-run in the same week
    # doesn't revise again for the same reason
    snapshot.update({
        "override": overrides,
        "hrv_status": current.get("recovery", {}).get("hrv_status"),
        "reviewed_week": plan["week_start"],
    })
    (DATA_DIR / "plan_snapshot.json").write_text(json.dumps(snapshot, indent=1))

    print(f"Plan revised: {revised.get('coach_says', '')[:160]}")

    if os.environ.get("DISCORD_WEBHOOK_URL"):
        from notify_discord import send_plan
        send_plan(revised)


if __name__ == "__main__":
    main()
