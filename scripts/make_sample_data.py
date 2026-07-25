"""Generate plausible fake data so you can develop the dashboard locally
before wiring Garmin. Run:  python scripts/make_sample_data.py
Then serve the site:        python -m http.server -d docs 8000
"""

import json
import random
import datetime as dt
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
random.seed(42)

SPORTS = [
    ("road_biking", 75, 150), ("mountain_biking", 100, 145),
    ("strength_training", 50, 115), ("lap_swimming", 40, 130),
    ("walking", 35, 95),
]


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    activities, daily = [], {}
    for i in range(60, -1, -1):
        day = today - dt.timedelta(days=i)
        daily[day.isoformat()] = {
            "resting_hr": random.randint(46, 54),
            "stress_avg": random.randint(20, 45),
            "body_battery_high": random.randint(70, 100),
            "body_battery_low": random.randint(10, 35),
            "sleep_s": random.randint(21600, 30600),
            "sleep_score": random.randint(60, 92),
            "hrv_last_night": random.randint(42, 62),
            "hrv_weekly_avg": 52,
            "hrv_status": random.choice(["BALANCED", "BALANCED", "LOW"]),
        }
        if random.random() < 0.75:  # ~5 sessions a week
            t, dur, hr = random.choice(SPORTS)
            dur_s = dur * 60 * random.uniform(0.7, 1.4)
            activities.append({
                "id": 10000 + i, "type": t,
                "start": f"{day.isoformat()}T{random.randint(6, 18):02d}:15:00",
                "duration_s": round(dur_s),
                "distance_m": round(dur_s * random.uniform(1, 8)),
                "elev_gain_m": random.randint(0, 900),
                "avg_hr": hr + random.randint(-8, 8),
                "max_hr": hr + random.randint(20, 35),
                "avg_power": None, "norm_power": None,
                "calories": round(dur_s / 4),
                "training_load": round(dur_s / 60 * random.uniform(0.8, 2.2)),
                "aerobic_te": round(random.uniform(1.5, 4.5), 1),
            })
    (DATA_DIR / "activities.json").write_text(json.dumps(activities, indent=1))
    (DATA_DIR / "daily.json").write_text(json.dumps(daily, indent=1, sort_keys=True))
    (DATA_DIR / "meta.json").write_text(json.dumps(
        {"synced_at": dt.datetime.now(dt.timezone.utc).isoformat()}))

    plan = {
        "week_start": (today - dt.timedelta(days=today.weekday())).isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "coach_says": "Form is slightly negative and midweek sleep dipped, so this week trades one hard ride for an easy swim and keeps two quality sessions.",
        "days": [
            {"day": "Mon", "sport": "gym", "session": "Upper push + core", "duration_min": 45, "intensity": "moderate", "details": "Bench 4x6, OHP 3x8, dips 3x10, plank circuit"},
            {"day": "Tue", "sport": "cycling", "session": "Tempo intervals", "duration_min": 75, "intensity": "hard", "details": "2x15 min at tempo, 10 min easy between"},
            {"day": "Wed", "sport": "swim", "session": "Technique swim", "duration_min": 40, "intensity": "easy", "details": "8x50 drill focus, 4x100 steady"},
            {"day": "Thu", "sport": "gym", "session": "Lower + posterior chain", "duration_min": 50, "intensity": "moderate", "details": "Squat 4x5, RDL 3x8, lunges 3x10, calf raises"},
            {"day": "Fri", "sport": "rest", "session": "Rest or short walk", "duration_min": 30, "intensity": "easy", "details": "Optional 30 min easy walk"},
            {"day": "Sat", "sport": "mtb", "session": "Endurance MTB", "duration_min": 120, "intensity": "moderate", "details": "2 h zone 2, keep HR under control on climbs"},
            {"day": "Sun", "sport": "cycling", "session": "Long ride", "duration_min": 150, "intensity": "moderate", "details": "2.5 h endurance, fuel every 45 min"},
        ],
    }
    (DATA_DIR / "plan.json").write_text(json.dumps(plan, indent=1))
    print("Sample data written to docs/data/. Now run compute_metrics.py")


if __name__ == "__main__":
    main()
