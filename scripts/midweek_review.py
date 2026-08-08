"""Mid-week adaptive plan review.

Runs Wednesday evening. Compares current recovery state to the Sunday
snapshot saved when the plan was generated. If meaningful change has
occurred, rewrites Thursday-Sunday only. Otherwise exits quietly.

Thresholds for triggering a revision:
  - A one-off week override was added or changed since Sunday
  - TSB moved more than 15 points in either direction
  - Training readiness score changed more than 15 points
  - Force flag set (FORCE_REVIEW=1 env var, for manual testing)

Note on thresholds: a hard Tuesday double day typically moves TSB by
~7 points — below the threshold. A genuine recovery improvement or
unexpected fatigue spike moves it 15+ points. This avoids triggering
a revision every week just from planned hard days.
"""

import json
import os
import datetime as dt
from pathlib import Path

import anthropic

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
MODEL = os.environ.get("TRAINER_MODEL", "claude-sonnet-5")

TSB_THRESHOLD       = 15   # points — raised from 10 to avoid triggering on planned hard days
READINESS_THRESHOLD = 15   # points


def _today_local() -> dt.date:
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(os.environ.get("TIMEZONE", "Australia/Sydney"))
        return dt.datetime.now(tz).date()
    except Exception:
        return dt.date.today()


def load_sunday_snapshot(plan: dict) -> dict:
    """Load the metrics snapshot saved when the plan was generated."""
    snapshot_path = DATA_DIR / "plan_snapshot.json"
    if snapshot_path.exists():
        return json.loads(snapshot_path.read_text())
    return {}


def should_revise(current: dict, snapshot: dict,
                  overrides: list[str] | None = None) -> tuple[bool, str]:
    """Return (should_revise, reason)."""
    force = os.environ.get("FORCE_REVIEW", "0") == "1"
    if force:
        return True, "forced review"

    # A one-off override set after Sunday is a trigger in its own right.
    # Comparing against the snapshot means it fires once, not every run.
    overrides = overrides or []
    if overrides != (snapshot.get("override") or []):
        return True, "one-off week override added or changed since the plan was generated"

    if not snapshot:
        return False, "no Sunday snapshot to compare against"

    current_tsb = current.get("load", {}).get("tsb") or 0
    snapshot_tsb = snapshot.get("tsb") or 0
    tsb_delta = current_tsb - snapshot_tsb

    current_readiness = (current.get("garmin_assessment", {})
                         .get("training_readiness", {}).get("score")) or 0
    snapshot_readiness = snapshot.get("readiness_score") or 0
    readiness_delta = current_readiness - snapshot_readiness

    reasons = []
    if abs(tsb_delta) >= TSB_THRESHOLD:
        direction = "improved" if tsb_delta > 0 else "declined"
        reasons.append(f"TSB {direction} by {abs(tsb_delta):.1f} points ({snapshot_tsb:.1f} → {current_tsb:.1f})")
    if abs(readiness_delta) >= READINESS_THRESHOLD:
        direction = "improved" if readiness_delta > 0 else "declined"
        reasons.append(f"readiness {direction} by {abs(readiness_delta)} ({snapshot_readiness} → {current_readiness})")

    if reasons:
        return True, " and ".join(reasons)
    return False, f"no significant change (TSB delta {tsb_delta:+.1f}, readiness delta {readiness_delta:+d})"


SYSTEM = """
<role>
You are an elite endurance cycling coach doing a mid-week plan revision.
The athlete is partway through their week. Your job is to revise ONLY the remaining days
(Thursday onwards) based on how they have actually responded to training so far.

Completed days are fixed. Do not change Mon, Tue, or Wed entries.
</role>

<hard_constraints>
These apply to the revised plan exactly as they do to the original.

- Keep completed days (Mon-Wed) exactly as they appear in the original plan.
- Revise Thursday through Sunday only.
- Exactly 7 days total in the output — Mon through Sun.
- If a day has two sessions, combine into ONE entry with both in details.
- Maximum 2 gym sessions across the full week (count sessions already done Mon-Wed).
- No hard cycling sessions on consecutive days.
- No heavy lower-body gym work within 24 hours before a key ride or long ride.
- Honour all committed_sessions and constraints from the athlete profile.
- week_override, if present, applies to this week only and outranks committed_sessions
  and constraints wherever they conflict. Never schedule a session it rules out.
- Output valid JSON only — no markdown fences, no explanatory text outside the reasoning block.
</hard_constraints>

<output_schema>
Your response must contain exactly two parts in this order:

PART 1 — REASONING (plain text, required)
REVISION_TRIGGERED_BY: <what changed since Sunday>
RECOVERY_DIRECTION: <improving|declining|stable>
CHANGES_MADE: <bullet list of what you changed Thu-Sun and why>
CHANGES_NOT_MADE: <what you considered but left unchanged and why>

PART 2 — REVISED PLAN (JSON only, no fences)
Same schema as original plan. Include all 7 days.
{
  "week_start": "YYYY-MM-DD",
  "recovery_state": "Recovered|Managing Fatigue|Fatigued|Highly Fatigued",
  "training_phase": "Recovery|Base|Build|Peak|Taper",
  "weekly_objective": "one sentence",
  "quality_sessions": 0,
  "coach_says": "2-3 sentences explaining what changed mid-week and why",
  "revised": true,
  "days": [ ... exactly 7 entries Mon-Sun ... ]
}
</output_schema>

<revision_rules>
If recovery has IMPROVED (TSB risen, readiness up, HRV balanced, sleep good):
- Consider upgrading one Thu-Sun session from easy to moderate.
- Only add a quality session if TSB is above -15 AND readiness is above 70.
- Do not upgrade more than one session.
- Do not upgrade if anaerobic load is already above target.

If recovery has DECLINED (TSB dropped further, readiness down, HRV low):
- Downgrade the next hard session to easy or rest.
- Protect the weekend long ride if it was planned — shorten rather than cancel.
- Add a rest or yoga day if the athlete needs it.
- Never add intensity when recovery is declining.

If STABLE:
- Keep Thu-Sun exactly as planned.
- Only change session placement if weather has changed significantly.

Always:
- Respect committed_sessions — these cannot be moved or downgraded, unless week_override
  rules that day or session out, in which case the override wins.
- If week_override is the reason for this revision, say so in coach_says.
- Apply the same gym formatting rules as the original plan.
- Apply weather to specific days.
</revision_rules>

<gym_format>
Use this exact format — the dashboard parser depends on it.

Header: Workout A: or Workout B:
Warm-up line starts with: Warm-up:
Rest line starts with: Rest:
Each exercise: ExerciseName 3 x 12
Optional note: ExerciseName 3 x 12 (note)
Reps can be range: 3 x 8-12
Valid suffixes: sec, min, each leg, each side, reps

Do NOT use square brackets in output.
Do NOT add any lines after the Rest: line.
</gym_format>
"""


def build_review_message(current: dict, plan: dict, snapshot: dict, reason: str,
                         overrides: list[str] | None = None) -> str:
    """Build a structured user message for the mid-week review.

    Uses the same XML structure as generate_plan.py for consistency.
    """
    today = _today_local()
    week_start = dt.date.fromisoformat(plan["week_start"])
    days_done = max(0, (today - week_start).days)

    # Pull the same fields generate_plan uses
    load = current.get("load", {})
    rec = current.get("recovery", {})
    ga = current.get("garmin_assessment", {})
    tr = ga.get("training_readiness", {})
    factors = tr.get("factors", {})
    p = current.get("athlete_profile", {})
    lb_raw = ga.get("load_balance", {})
    weather = current.get("weather_forecast_7day", "")

    # Load balance gaps
    from generate_plan import _load_balance_with_gap
    lb = _load_balance_with_gap(lb_raw)

    constraints = p.get("constraints", [])
    committed = p.get("committed_sessions", [])

    # Compliance: which days were completed and what was done
    from generate_plan import compliance
    from pathlib import Path as _Path
    import json as _json
    activities = _json.loads((_Path(str(DATA_DIR)) / "activities.json").read_text())
    comp = compliance(plan, activities)

    parts = []

    parts.append(f"<task>Mid-week review for week starting {plan['week_start']}. Today is {today.isoformat()} ({days_done} days in, {7 - days_done} days remaining).</task>")
    parts.append("")

    parts.append(f"<revision_trigger>{reason}</revision_trigger>")
    parts.append("")

    # What changed since Sunday
    parts.append("<snapshot_comparison>")
    parts.append(f"  Sunday snapshot: TSB={snapshot.get('tsb')}  CTL={snapshot.get('ctl')}  ATL={snapshot.get('atl')}  readiness={snapshot.get('readiness_score')} ({snapshot.get('readiness_level')})")
    parts.append(f"  Now (Wednesday): TSB={load.get('tsb')}  CTL={load.get('ctl')}  ATL={load.get('atl')}  readiness={tr.get('score')} ({tr.get('level')})")
    tsb_delta = (load.get('tsb') or 0) - (snapshot.get('tsb') or 0)
    readiness_delta = (tr.get('score') or 0) - (snapshot.get('readiness_score') or 0)
    parts.append(f"  TSB delta: {tsb_delta:+.1f}  Readiness delta: {readiness_delta:+d}")
    parts.append("</snapshot_comparison>")
    parts.append("")

    # Current recovery signals
    parts.append("<current_recovery>")
    parts.append(f"  HRV last night: {rec.get('hrv_last_night')} ms  Status: {rec.get('hrv_status')}")
    parts.append(f"  Resting HR trend (14 days): {rec.get('resting_hr_trend')}")
    parts.append(f"  Sleep score 7d avg: {rec.get('sleep_score_7d_avg')}  Sleep hours: {rec.get('sleep_hours_7d_avg')}")
    parts.append(f"  Garmin training status: {ga.get('training_status')}")
    if factors:
        parts.append("  Readiness factors:")
        for fname, fval in factors.items():
            parts.append(f"    {fname}: {fval}")
    parts.append("</current_recovery>")
    parts.append("")

    # Load balance
    tsb_val = load.get('tsb') or 0
    parts.append("<load_balance>")
    for zone, data in lb.items():
        if zone == "feedback":
            parts.append(f"  Feedback: {data}")
            if tsb_val < -30:
                parts.append(f"  Override: TSB {tsb_val} is below -30 — load balance feedback overridden by TSB constraint.")
        else:
            gap_note = "within range" if data['gap'] == 0 else (f"{data['gap']:+d} vs min" if data['gap'] < 0 else f"+{data['gap']} above max")
            parts.append(f"  {zone}: actual={data['actual']}  target={data['target_min']}-{data['target_max']}  ({gap_note})")
    parts.append("</load_balance>")
    parts.append("")

    # Compliance so far this week
    parts.append("<compliance_this_week>")
    if comp and comp.get("detail"):
        for d in comp["detail"][:days_done]:
            actual = ", ".join(d.get("actual") or ["nothing recorded"])
            parts.append(f"  {d['day']}: planned={d['planned']}  actual={actual}  matched={d['matched']}")
    parts.append("</compliance_this_week>")
    parts.append("")

    # Athlete constraints
    parts.append("<athlete_constraints>")
    parts.append("  <committed_sessions>")
    for c in committed:
        parts.append(f"    - {c}")
    if not committed:
        parts.append("    None.")
    parts.append("  </committed_sessions>")
    if overrides:
        parts.append("  <week_override>")
        parts.append("    THIS WEEK ONLY. Non-negotiable. Takes precedence over")
        parts.append("    committed_sessions and constraints where they conflict.")
        for o in overrides:
            parts.append(f"    - {o}")
        parts.append("  </week_override>")
    parts.append("  <constraints>")
    for c in constraints:
        parts.append(f"    - {c}")
    if not constraints:
        parts.append("    None.")
    parts.append("  </constraints>")
    parts.append("</athlete_constraints>")
    parts.append("")

    # Original plan
    parts.append("<original_plan>")
    parts.append(_json.dumps(plan.get("days", []), indent=2))
    parts.append("</original_plan>")
    parts.append("")

    if weather:
        parts.append(f"<weather_forecast_remaining_days>")
        parts.append(f"  {weather}")
        parts.append(f"</weather_forecast_remaining_days>")

    return "\n".join(parts)


def main():
    import sys
    debug = "--debug" in sys.argv

    from generate_plan import build_summary, load_week_override
    current = build_summary()

    plan_path = DATA_DIR / "plan.json"
    if not plan_path.exists():
        print("No plan found — skipping mid-week review")
        return

    plan = json.loads(plan_path.read_text())
    snapshot = load_sunday_snapshot(plan)
    overrides = load_week_override(plan["week_start"])

    should, reason = should_revise(current, snapshot, overrides)
    print(f"Mid-week review: {reason}")

    if debug:
        print("\n" + "=" * 60)
        print("SNAPSHOT vs NOW")
        print("=" * 60)
        print(f"  Snapshot TSB:      {snapshot.get('tsb')}  readiness: {snapshot.get('readiness_score')}")
        load = current.get("load", {})
        tr = current.get("garmin_assessment", {}).get("training_readiness", {})
        print(f"  Current  TSB:      {load.get('tsb')}  readiness: {tr.get('score')}")
        print(f"  Snapshot override: {snapshot.get('override') or []}")
        print(f"  Current  override: {overrides}")
        print(f"  Would revise:      {should}")
        print(f"  Reason:            {reason}")
        print("\n" + "=" * 60)
        print("FULL USER MESSAGE")
        print("=" * 60)
        msg = build_review_message(current, plan, snapshot, reason, overrides)
        print(msg)
        print("\n" + "=" * 60)
        print("SYSTEM PROMPT")
        print("=" * 60)
        print(SYSTEM)
        print(f"\nApprox system tokens: {len(SYSTEM) // 4}")
        print(f"Approx user tokens:   {len(msg) // 4}")
        return

    if not should:
        print("No revision needed — plan stands as-is")
        return

    print(f"Revising plan: {reason}")

    msg = build_review_message(current, plan, snapshot, reason, overrides)
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": msg}],
    )

    text = "".join(b.text for b in response.content if b.type == "text")
    text = text.replace("```json", "").replace("```", "").strip()

    if not text:
        print(f"Model returned empty response. Stop reason: {response.stop_reason}")
        return

    # Split reasoning from JSON
    json_start = text.find("{")
    if json_start == -1:
        print("No JSON found in response — keeping original plan")
        print(text[:500])
        return

    reasoning = text[:json_start].strip()
    json_text = text[json_start:].strip()

    if reasoning:
        print("--- Coach reasoning ---")
        print(reasoning)
        print("--- End reasoning ---")

    revised = json.loads(json_text)
    revised["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    revised["midweek_revision"] = True
    plan_path.write_text(json.dumps(revised, indent=1))

    # Fold the override into the snapshot so a re-run in the same week does
    # not revise again for the same reason.
    snapshot_path = DATA_DIR / "plan_snapshot.json"
    if snapshot_path.exists():
        snapshot["override"] = overrides
        snapshot_path.write_text(json.dumps(snapshot, indent=1))

    print(f"Plan revised: {revised.get('coach_says', '')[:120]}")

    if os.environ.get("DISCORD_WEBHOOK_URL"):
        from notify_discord import send_plan
        send_plan(revised)


if __name__ == "__main__":
    main()
