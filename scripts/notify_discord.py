"""Post the weekly plan to a Discord channel via webhook.

Setup: in Discord, channel settings -> Integrations -> Webhooks -> New,
copy the URL into the DISCORD_WEBHOOK_URL repo secret. Nothing runs
until that secret exists.
"""

import os
import requests

INTENSITY_DOTS = {"easy": "🟢", "moderate": "🟡", "hard": "🔴"}


def send_plan(plan: dict):
    lines = []
    for d in plan.get("days", []):
        dot = INTENSITY_DOTS.get(d.get("intensity"), "⚪")
        dur = f" ({d['duration_min']} min)" if d.get("duration_min") else ""
        lines.append(f"{dot} **{d['day']}** {d['session']}{dur}")
    embed = {
        "title": f"Training plan, week of {plan.get('week_start')}",
        "description": "\n".join(lines),
        "fields": [{"name": "Coach says", "value": plan.get("coach_says", "")}],
        "color": 0x1D9E75,
    }
    resp = requests.post(
        os.environ["DISCORD_WEBHOOK_URL"],
        json={"embeds": [embed]},
        timeout=15,
    )
    resp.raise_for_status()
    print("Plan posted to Discord")
