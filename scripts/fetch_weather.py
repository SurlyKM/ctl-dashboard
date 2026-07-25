"""Fetch a 7-day weather forecast from Open-Meteo (free, no API key).

Reads LOCATION_LAT and LOCATION_LON from environment variables.
Falls back gracefully if not set or if the request fails.
Uses Haiku to summarise the forecast into a plain-English note.
"""

import json
import os
import urllib.request
import datetime as dt

import anthropic


def fetch_forecast(lat: float, lon: float) -> dict | None:
    """Pull daily forecast from Open-Meteo — no API key required."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=weathercode,precipitation_probability_max,temperature_2m_max,temperature_2m_min,windspeed_10m_max"
        f"&timezone=Australia%2FSydney"
        f"&forecast_days=7"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"Weather fetch failed: {e}")
        return None


WMO_CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "showers", 81: "showers", 82: "heavy showers",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}


def summarise_forecast(raw: dict) -> str:
    """Ask Haiku to turn the raw forecast into a plain-English weekly summary."""
    daily = raw.get("daily", {})
    dates   = daily.get("time", [])
    codes   = daily.get("weathercode", [])
    precip  = daily.get("precipitation_probability_max", [])
    t_max   = daily.get("temperature_2m_max", [])
    wind    = daily.get("windspeed_10m_max", [])

    lines = []
    for i, date in enumerate(dates):
        day = dt.date.fromisoformat(date).strftime("%a %d %b")
        condition = WMO_CODES.get(codes[i] if i < len(codes) else 0, "unknown")
        rain_pct  = precip[i] if i < len(precip) else "?"
        temp      = t_max[i]  if i < len(t_max)  else "?"
        w         = wind[i]   if i < len(wind)    else "?"
        lines.append(f"{day}: {condition}, rain {rain_pct}%, max {temp}°C, wind {w} km/h")

    forecast_text = "\n".join(lines)

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                "Summarise this 7-day forecast for a cyclist in 2-3 plain sentences. "
                "Note which days suit outdoor MTB or road riding and which are wet or windy. "
                "No markdown, no bold, no bullet points. Plain text only.\n\n"
                + forecast_text
            )
        }]
    )
    return msg.content[0].text.strip()


def get_weather_summary() -> str | None:
    """Main entry point — returns a plain-English summary or None if unavailable."""
    lat = os.environ.get("LOCATION_LAT")
    lon = os.environ.get("LOCATION_LON")
    if not lat or not lon:
        print("LOCATION_LAT/LON not set, skipping weather")
        return None
    try:
        lat, lon = float(lat), float(lon)
    except ValueError:
        print("Invalid LOCATION_LAT/LON values")
        return None

    raw = fetch_forecast(lat, lon)
    if not raw:
        return None

    summary = summarise_forecast(raw)
    print(f"Weather summary: {summary}")
    return summary


if __name__ == "__main__":
    get_weather_summary()
