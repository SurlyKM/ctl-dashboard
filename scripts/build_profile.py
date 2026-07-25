"""
Athlete profile builder.
=========================
Run this once to set up your athlete profile. It produces two outputs:

  1. docs/data/athlete_profile.json  — public, committed to the repo.
     Contains goals, equipment and general preferences only. No schedule.

  2. Printed to screen: the ATHLETE_PROFILE_PRIVATE secret value.
     Copy this into GitHub repo Settings -> Secrets -> ATHLETE_PROFILE_PRIVATE.
     Contains your specific schedule, constraints and time slots.
     Never stored in the repo.

    python scripts/build_profile.py
"""

import json
import sys


def ask(prompt, default=None, multiline=False):
    if default:
        prompt += f" [{default}]"
    prompt += ": "
    if multiline:
        print(prompt)
        print("  (enter each item on a new line, blank line to finish)")
        lines = []
        while True:
            line = input("  > ").strip()
            if not line:
                break
            lines.append(line)
        return lines or ([default] if default else [])
    val = input(prompt).strip()
    return val if val else default


def ask_choice(prompt, choices, default=None):
    print(f"\n{prompt}")
    for i, c in enumerate(choices, 1):
        marker = " *" if c == default else ""
        print(f"  {i}. {c}{marker}")
    while True:
        raw = input("Choice (number): ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print("  Enter a number from the list.")


def ask_multi(prompt, options):
    print(f"\n{prompt}")
    selected = []
    for label, val in options:
        if input(f"  {label}? (y/n): ").strip().lower() == "y":
            selected.append(val)
    return selected


def main():
    print()
    print("=" * 58)
    print("  Athlete profile builder")
    print("=" * 58)
    print()
    print("Builds two outputs:")
    print("  1. Public profile  — committed to the repo (no schedule)")
    print("  2. Private profile — paste into GitHub secret (full detail)")
    print()

    # --- PUBLIC PROFILE ---
    pub = {}

    print("--- Goals ---")
    pub["goal_primary"] = ask_choice("Primary training goal", [
        "Improve cycling performance",
        "Improve running performance",
        "Get stronger",
        "General fitness",
        "Train for a specific event",
        "Improve swimming",
    ])
    if pub["goal_primary"] == "Train for a specific event":
        pub["event"] = ask("Event name and date (e.g. Gran Fondo Oct 2026)")
    sec = ask("Secondary goal (or leave blank)", default="")
    pub["goal_secondary"] = sec if sec else None

    print("\n--- Experience ---")
    pub["experience_years"] = ask_choice("Years of consistent training", [
        "Less than 1", "1-2", "3-5", "5+", "10+"
    ])

    print("\n--- Equipment ---")
    pub["gym_access"] = ask_choice("Gym access", [
        "Full commercial gym", "Home gym (basic)",
        "Home gym (well-equipped)", "No gym",
    ])
    pub["bike_types"] = ask_multi("Bikes you own", [
        ("Road bike", "Road bike"), ("MTB", "MTB"),
        ("Gravel bike", "Gravel bike"), ("Indoor trainer / Zwift", "Indoor trainer / Zwift"),
    ])
    pub["pool_access"] = input("\n  Pool access? (y/n): ").strip().lower() == "y"

    print("\n--- Training volume ---")
    pub["gym_days_per_week"] = ask_choice("Gym days per week", ["1", "2", "2-3", "3", "3-4", "4+"])
    pub["ride_days_per_week"] = ask_choice("Ride days per week", ["1-2", "3-4", "5-6", "6-7"])
    pub["session_max_min"] = ask_choice("Max session length for structured workouts", [
        "30-40 min", "45-50 min", "60 min", "75 min", "90 min"
    ])
    pub["activities"] = ask_multi("Activities", [
        ("Road cycling", "road cycling"), ("MTB", "mtb"),
        ("Gravel", "gravel"), ("Indoor / Zwift", "indoor cycling"),
        ("Swimming", "swimming"), ("Gym / strength", "gym"),
        ("Yoga", "yoga"), ("Running", "running"), ("Hiking / walking", "hiking"),
    ])

    print("\n--- General preferences ---")
    print("Things you enjoy or want more of (no schedule detail here):")
    pub["preferences"] = ask("Preferences", multiline=True)

    if "swimming" in pub["activities"]:
        print("\n--- Swimming ---")
        pub["swim_target_meters"] = ask("Typical swim distance (metres)", default="1000")
        pub["swim_notes"] = ask("Swim notes", default="Structured sets preferred, not continuous laps")

    # --- PRIVATE PROFILE ---
    priv = {}

    print("\n" + "=" * 58)
    print("  Private schedule (goes in GitHub secret, not the repo)")
    print("=" * 58)

    print("\n--- Available time slots ---")
    priv["available_slots"] = ask_multi("When can you train", [
        ("Before work (~1 hr)", "before work ~1 hr"),
        ("Lunch (~1 hr)", "lunch ~1 hr"),
        ("After work short (<1 hr)", "after work short"),
        ("After work flexible (longer ok)", "after work flexible"),
        ("Weekends — longer sessions fine", "weekends long rides ok"),
        ("Fridays — more time available", "Friday flexible"),
    ])

    print("\n--- Committed sessions (non-negotiable recurring sessions) ---")
    print("e.g. 'Tuesday evening: hard group MTB ride'")
    priv["committed_sessions"] = ask("Committed sessions", multiline=True)

    print("\n--- Constraints ---")
    print("e.g. 'Wednesday must always be easy after Tuesday'")
    print("     'MTB only in dry weather'")
    priv["constraints"] = ask("Constraints", multiline=True)

    priv["gym_session_format"] = "Name exercises explicitly. Format: ExerciseName 3x(15-18/12-15/8-12). Label as Workout A or Workout B and alternate across the week."
    priv["swim_session_format"] = "Warmup: 200m easy. Main: intervals with rest periods. Cooldown: 100m easy. Total ~1000m."

    # --- Review and save ---
    print("\n" + "=" * 58)
    print("  Public profile (docs/data/athlete_profile.json)")
    print("=" * 58)
    print(json.dumps(pub, indent=2))

    print("\n" + "=" * 58)
    print("  Private profile (ATHLETE_PROFILE_PRIVATE secret)")
    print("=" * 58)
    print(json.dumps(priv, indent=2))

    print()
    confirm = input("Save public profile and print secret? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    from pathlib import Path
    out = Path(__file__).resolve().parent.parent / "docs" / "data" / "athlete_profile.json"
    out.write_text(json.dumps(pub, indent=2))
    print(f"\nPublic profile saved to {out}")

    print("\n" + "=" * 58)
    print("  ATHLETE_PROFILE_PRIVATE — paste this into GitHub secret")
    print("=" * 58)
    print(json.dumps(priv))
    print()
    print("Repo -> Settings -> Secrets and variables -> Actions")
    print("-> New repository secret -> Name: ATHLETE_PROFILE_PRIVATE")
    print("-> Paste the single line above as the value -> Save")


if __name__ == "__main__":
    main()
