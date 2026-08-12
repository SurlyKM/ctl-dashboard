# ctl-dashboard

A personal cycling fitness dashboard that syncs Garmin Connect data, computes training load metrics, and generates AI-powered weekly training plans. Static site hosted on GitHub Pages, no backend required.

## What it does

- Syncs activity and wellness data from Garmin Connect (incremental -- only fetches the last 7 days, merges into 200-day rolling history)
- Computes training load metrics (CTL, ATL, TSB) using the Banister impulse-response model
- Tracks training readiness, HRV, sleep, body battery, stress, resting HR and VO2 max
- Pulls a 7-day weather forecast and summarises it for outdoor session placement
- Generates a personalised 7-day training plan every Sunday using Claude
- Runs a mid-week adaptive review on Wednesday, revising Thu-Sun if recovery state has shifted significantly
- Optionally posts the plan to a Discord channel
- Dashboard and plan viewable in the browser on any device

## Dashboard

- **Training load chart** with 12-week / 6-month toggle showing daily load, CTL (fitness) and ATL (fatigue)
- **Metric cards** for HRV, sleep, body battery, stress, form (TSB), fitness (CTL), fatigue (ATL), weekly hours, resting HR and VO2 max -- each expandable with sparkline history
- **Activity mix** (4-week breakdown by sport)
- **VO2 max trend** (proxy derived from activity heart rate data)
- **Garmin assessment** including training readiness score with factor breakdown
- **Monthly load balance** (aerobic low / aerobic high / anaerobic vs Garmin targets)
- **Four colour themes** (forest green, slate blue, warm neutral, monochrome) with dark mode support

## Plan

- **Collapsible day cards** with intensity pills, session details and done/today status
- **Gym sessions** parsed into structured exercise blocks (Workout A / Workout B format)
- **Swim sessions** parsed into warmup / main / cooldown phases
- **Compliance tracking** (planned vs actual, colour-coded)
- **Coach rationale** explaining the week's objectives based on recovery data
- **Mid-week revision** when TSB or readiness shifts beyond threshold

## Privacy

No personally identifying information is stored or displayed. GPS tracks, location names, activity titles and device IDs are never extracted. The site has a noindex tag. Schedule and constraint details are stored in a GitHub encrypted secret, not in the repo.

## Requirements

- Python 3.12+
- Garmin Connect account
- Anthropic API key
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

**3. GitHub secrets and variables**

Add these in repo Settings > Secrets and variables > Actions:

| Secret | Description |
|---|---|
| `GARMIN_TOKEN_B64` | Output of `setup_token.py` |
| `ANTHROPIC_API_KEY` | Your API key |
| `ATHLETE_PROFILE_PRIVATE` | Output of `build_profile.py` (schedule and constraints) |
| `LOCATION_LAT` | Latitude for weather forecast |
| `LOCATION_LON` | Longitude for weather forecast |
| `DISCORD_WEBHOOK_URL` | Optional -- posts weekly plan to a channel |

| Variable | Description |
|---|---|
| `TIMEZONE` | Your local timezone e.g. `Australia/Sydney` (default) |
| `TRAINER_MODEL` | Optional -- override the default AI model |
| `PLAN_OVERRIDE` | Optional -- one-off weekly constraint as JSON (self-expiring) |

**4. GitHub Pages**

Settings > Pages > deploy from branch `main`, folder `/docs`.

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
Garmin Connect ──(incremental sync)──> sync_garmin.py ──> docs/data/*.json
                                              │
                                      compute_metrics.py
                                      (CTL / ATL / TSB)
                                              │
Sun evening ──> generate_plan.py ──> weather forecast
                     │             + Garmin readiness
                     │             + athlete profile (private)
                     │             + compliance data
                     │                    │
                     └──────> Claude ──> plan.json ──> Discord (optional)
                                              │
Wed evening ──> midweek_review.py ──> revise Thu-Sun if needed
                                              │
                          GitHub Pages serves docs/ (dashboard + plan)
```

## Key design decisions

- **Incremental sync**: each run fetches only 7 days from Garmin and merges into the existing 200-day activity history, avoiding redundant API calls
- **EWMA convergence**: 200 days of history gives the 42-day CTL time constant over 4x its period to converge, producing accurate TSB without artificial seeding
- **Whitelist-only data model**: every field written to disk is explicitly named in `sync_garmin.py` -- nothing from Garmin passes through implicitly
- **Structured prompts**: the AI plan generator uses XML-structured user messages with recovery signals surfaced first, hard constraints separated from preferences, and a strict JSON output schema
- **Mid-week adaptation**: threshold-based (TSB delta >= 10 or readiness delta >= 15) to avoid unnecessary revisions while catching genuine recovery shifts
- **Parser-coupled gym format**: the plan output format is tightly coupled to the frontend parser -- exercise format, header conventions and rest line placement all follow documented rules

## Setting up your own

This is a personal dashboard, not a SaaS product. If you want to run your own instance, you'll need to fork the repo and customise a few things to match your training.

### 1. Fork and clone

Fork this repo, clone it locally, and install dependencies:

```
pip install -r requirements.txt
```

### 2. Garmin token

Run `scripts/setup_token.py` on your local machine. It logs into Garmin interactively, captures the auth token, and prints a base64 blob. Paste that into the `GARMIN_TOKEN_B64` GitHub secret. Your password is used once and never stored. Re-run roughly once a year when the token expires.

### 3. Athlete profile

Run `scripts/build_profile.py`. It walks you through an interactive questionnaire and produces two outputs:

- **Public profile** (`docs/data/athlete_profile.json`) -- goals, equipment, general preferences. This gets committed to the repo. Keep it general -- no schedule details, no constraints.
- **Private profile** (printed to screen) -- your specific schedule, committed sessions, time slots and constraints. Paste this as the `ATHLETE_PROFILE_PRIVATE` GitHub secret. This is where non-negotiable sessions like "Tuesday evening group ride" and constraints like "Wednesday must be easy" live. It never enters the repo.

The split matters. The public profile is visible in the repo and feeds the dashboard. The private profile contains information you don't want public and also guards against prompt injection -- constraints in a secret can't be tampered with via a pull request.

### 4. Customise the AI coach

The system prompt lives in `generate_plan.py` in the `SYSTEM` variable. You'll likely want to adjust:

- **Sport types**: the `sport` enum in the response schema (`gym|cycling|mtb|swim|yoga|walk_hike|rest`). Add or remove sports to match what you do. If you add a new sport, also add it to `SPORT_GROUPS` in `compute_metrics.py` so load calculations group it correctly, and to `SPORT_LABELS` in `shared.js` so the dashboard displays it.
- **Decision rules**: the TSB thresholds, intensity rules and recovery logic. These are tuned for cycling-focused training. A runner or triathlete would want different rules.
- **Session formats**: the `<gym_format>`, `<swim_format>` and `<cycling_format>` sections define how the AI structures each session type. These are tightly coupled to the parsers in `plan.js` -- if you change the format the AI outputs, you need to update the parser too.
- **Priorities**: the `<priorities>` section controls how the AI weighs competing demands. "Cycling performance" as the primary goal only makes sense if that's your goal.

### 5. Understand the format coupling

The plan page parser in `plan.js` expects specific formatting from the AI:

- **Gym**: header must be `Workout A:` or `Workout B:` with colon. Exercises as `ExerciseName 3x12`. Warm-up and rest on separate lines with `Warm-up:` and `Rest:` prefixes. Parenthetical notes like `(light load, 2-3 RIR)` are parsed and displayed as sub-text.
- **Swim**: phases as `Warmup:`, `Main:`, `Cooldown:` at the start of lines.
- **Cycling/other**: rendered as prose paragraphs.

If you change what the AI outputs, test it against the parser locally. Broken formatting won't crash anything, it'll just render as plain text instead of the structured layout.

### 6. GitHub secrets and variables

Set up the secrets and variables listed in the Setup section above. At minimum you need `GARMIN_TOKEN_B64`, `ANTHROPIC_API_KEY` and `ATHLETE_PROFILE_PRIVATE`. Weather requires `LOCATION_LAT` and `LOCATION_LON`. Set `TIMEZONE` as a repository variable if you're not in Australia/Sydney.

### 7. GitHub Pages and Actions

Enable GitHub Pages (Settings > Pages > branch `main`, folder `/docs`). The GitHub Actions workflows handle the rest:

- Sync runs several times daily
- Plan generation runs Sunday evening
- Mid-week review runs Wednesday evening

Trigger both the sync and plan workflows manually from the Actions tab on first setup to seed the data files.

### 8. Local testing

Always test changes locally before pushing:

```
python scripts/sync_garmin.py
python scripts/compute_metrics.py
python -m http.server -d docs 8000
```

The `--debug` flag on `generate_plan.py` and `midweek_review.py` exits before making any API call or writing any file -- use it to inspect what would be sent to the AI without spending tokens or overwriting your plan.

### 9. Things to watch for

- **PLAN_OVERRIDE**: set as a repo variable (not a secret) for one-off weekly constraints. JSON format with `week` (Monday as `YYYY-MM-DD`) and `notes` array. Self-expires by week tag. Avoid curly quotes (iOS autocorrect) and `<` characters (sits inside XML).
- **Commute activities**: short cycling activities with low load values are legitimate data, not artifacts. Don't filter them out.
- **VO2 max steps**: Garmin recalculates VO2 max infrequently, so the trend line has a step-function pattern. This is expected.
- **First few weeks**: if you start with less than ~120 days of history, CTL will read low as the EWMA converges. The 200-day retention window avoids this for ongoing use, but the very first weeks after setup will show suppressed CTL values.
