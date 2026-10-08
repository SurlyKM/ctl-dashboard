"""One place for the Claude call shared by the weekly plan and mid-week review.

- Structured outputs: the response is guaranteed to match PLAN_SCHEMA, so
  there's no fence-stripping or brace-hunting. validate_plan() in
  generate_plan.py still checks the things a schema can't (exactly 7 days).
- Streaming: thinking is always on for current models and counts toward
  max_tokens, so the limit is generous and the request streams to avoid
  HTTP timeouts.
- Refusal fallback: if the model's safety classifier declines (rare for
  this content), the API retries server-side on Anthropic's recommended
  fallback model instead of failing the job.
"""

import json
import os

import anthropic

# `or` rather than a .get() default: an unset Actions variable arrives as ""
MODEL = os.environ.get("TRAINER_MODEL") or "claude-opus-5-5"
EFFORT = os.environ.get("TRAINER_EFFORT") or "high"
MAX_TOKENS = 32000

SPORTS = ["gym", "cycling", "mtb", "swim", "yoga", "walk_hike", "rest"]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["week_start", "training_phase", "weekly_objective",
                 "quality_sessions", "coach_says", "decision_log", "days"],
    "properties": {
        "week_start": {"type": "string", "description": "Monday of the plan week, YYYY-MM-DD"},
        "training_phase": {"type": "string", "enum": ["Recovery", "Base", "Build", "Peak", "Taper"]},
        "weekly_objective": {"type": "string", "description": "One sentence"},
        "quality_sessions": {"type": "integer", "description": "Number of hard sessions in the week"},
        "coach_says": {"type": "string", "description": "2-3 sentences for the athlete"},
        "decision_log": {"type": "string", "description": "Short bullet summary of the key decisions and the data behind each, for the workflow log"},
        "days": {
            "type": "array",
            "description": "Exactly 7 entries, Mon through Sun",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["day", "sport", "session", "duration_min",
                             "intensity", "details", "fallback"],
                "properties": {
                    "day": {"type": "string", "enum": DAY_NAMES},
                    "sport": {"type": "string", "enum": SPORTS},
                    "session": {"type": "string", "description": "Short title"},
                    "duration_min": {"type": "integer"},
                    "intensity": {"type": "string", "enum": ["easy", "moderate", "hard"]},
                    "details": {"type": "string"},
                    "fallback": {"type": "string", "description": "What to do instead if recovery signals are poor on the day; empty string for easy/rest days"},
                },
            },
        },
    },
}


def call_coach(system: str, user_msg: str) -> dict:
    """Send one planning request and return the parsed plan object.

    Raises RuntimeError on refusal or truncation so the workflow fails
    loudly and the previous plan stays live.
    """
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": PLAN_SCHEMA},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()

    print(f"Model: {msg.model} | stop: {msg.stop_reason} | "
          f"tokens in/out: {msg.usage.input_tokens}/{msg.usage.output_tokens}")

    if msg.stop_reason == "refusal":
        details = getattr(msg, "stop_details", None)
        raise RuntimeError(f"Model declined the request: {details}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError(f"Response hit max_tokens ({MAX_TOKENS}) and is truncated")

    # If a fallback happened, only text after the last switch point belongs
    # to the model that finished the answer
    blocks = list(msg.content)
    last_switch = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    text = "".join(b.text for b in blocks[last_switch + 1:] if b.type == "text").strip()
    if not text:
        raise RuntimeError(f"Empty response (stop reason {msg.stop_reason})")
    return json.loads(text)
