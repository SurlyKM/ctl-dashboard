# ctl-dashboard

A personal fitness dashboard that pulls training data automatically and generates a weekly training plan using a AI. Static site hosted on GitHub Pages, no backend required.

## What it does

- Syncs activity and wellness data from Garmin Connect several times a day
- Computes training load metrics (CTL, ATL, TSB)
- Pulls a 7-day weather forecast and summarises it for the coach
- Generates a personalised 7-day training plan every Sunday morning using any compatible AI API
- Optionally posts the plan to a Discord channel
- Dashboard and plan viewable in the browser on any device

## Privacy

No personally identifying information is stored or displayed. GPS tracks, location names, activity titles and device IDs are never extracted. The site has a noindex tag. Schedule and constraint details are stored in a GitHub encrypted secret, not in the repo.

## Requirements

- Python 3.12+
- Garmin Connect account
- Any compatible AI API key
- GitHub account (for Actions and Pages)

## Setup

**1. Garmin token**

Run on your local machine to generate an auth token without storing your password:

```
python scripts/setup_token.py
```

**2. Athlete profile**

Run the profile builder. It produces a public profile committed to the repo, and a private JSON blob to paste into GitHub secrets:

```
python scripts/build_profile.py
```

**3. GitHub secrets**

Add these in repo Settings → Secrets and variables → Actions:

| Secret | Description |
|---|---|
| `GARMIN_TOKEN_B64` | Output of `setup_token.py` |
| `ANTHROPIC_API_KEY` | Your API key |
| `ATHLETE_PROFILE_PRIVATE` | Output of `build_profile.py` (schedule and constraints) |
| `LOCATION_LAT` | Latitude for weather forecast |
| `LOCATION_LON` | Longitude for weather forecast |
| `DISCORD_WEBHOOK_URL` | Optional — posts weekly plan to a channel |
| `TRAINER_MODEL` | Optional — override the default model |

**4. GitHub Pages**

Settings → Pages → deploy from branch `main`, folder `/docs`.

**5. First run**

Trigger the sync and plan workflows manually from the Actions tab to seed data before the scheduled runs kick in.

## Local development

```
pip install -r requirements.txt
python scripts/sync_garmin.py
python scripts/compute_metrics.py
python -m http.server -d docs 8000
```

## How it works

```
Garmin Connect ──(4x daily)──> sync_garmin.py ──> docs/data/*.json
                                                  │
                                          compute_metrics.py
                                          (CTL / ATL / TSB)
                                                  │
Sun 8pm ──> generate_plan.py ──> weather forecast
                         │            + Garmin readiness
                         │            + athlete profile (private)
                         │                    │
                         └──────> AI API ──> plan.json ──> Discord (optional)
                                                  │
                              GitHub Pages serves docs/ (dashboard + plan)
```
