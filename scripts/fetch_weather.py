"""Daily weather forecast from Open-Meteo (free, no API key).

Reads LOCATION_LAT and LOCATION_LON from environment variables.
Returns a compact per-day table for exactly the dates being planned, which
goes straight into the coach prompt. (This used to go through a Haiku
summary, which dropped the dates and, because the forecast started on the
Sunday the job runs, covered Sun-Sat instead of the Mon-Sun plan week.)
"""

import datetime as dt
import json
import os
import urllib.parse
import urllib.request

MAX_FORECAST_DAYS = 16   # Open-Meteo limit

WMO_CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}


def _timezone() -> str:
    return os.environ.get("TIMEZONE") or "Australia/Sydney"


def fetch_forecast(lat: float, lon: float, days: int) -> dict | None:
    """Pull the daily forecast, starting today in the configured timezone."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=weathercode,precipitation_probability_max,precipitation_sum,"
        "temperature_2m_max,temperature_2m_min,windspeed_10m_max"
        f"&timezone={urllib.parse.quote(_timezone(), safe='')}"
        f"&forecast_days={days}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"Weather fetch failed: {e}")
        return None


def get_forecast_table(dates: list[dt.date]) -> str | None:
    """One line per requested date, or None if weather is unavailable.

    Dates beyond the forecast horizon are listed as "no forecast" so the
    coach knows the gap is real rather than guessing.
    """
    if not dates:
        return None
    lat, lon = os.environ.get("LOCATION_LAT"), os.environ.get("LOCATION_LON")
    if not lat or not lon:
        print("LOCATION_LAT/LON not set, skipping weather")
        return None
    try:
        lat, lon = float(lat), float(lon)
    except ValueError:
        print("Invalid LOCATION_LAT/LON values")
        return None

    # Forecast starts today; ask for enough days to reach the last plan date
    try:
        import zoneinfo
        today = dt.datetime.now(zoneinfo.ZoneInfo(_timezone())).date()
    except Exception:
        today = dt.date.today()
    days_needed = min(MAX_FORECAST_DAYS, max(1, (max(dates) - today).days + 1))
    raw = fetch_forecast(lat, lon, days_needed)
    if not raw:
        return None

    daily = raw.get("daily", {})
    by_date = {}
    for i, date in enumerate(daily.get("time", [])):
        def col(name):
            vals = daily.get(name) or []
            return vals[i] if i < len(vals) else None
        by_date[date] = {
            "cond": WMO_CODES.get(col("weathercode"), "unknown"),
            "rain_pct": col("precipitation_probability_max"),
            "rain_mm": col("precipitation_sum"),
            "tmin": col("temperature_2m_min"),
            "tmax": col("temperature_2m_max"),
            "wind": col("windspeed_10m_max"),
        }

    lines = []
    for d in dates:
        w = by_date.get(d.isoformat())
        label = d.strftime("%a %Y-%m-%d")
        if not w:
            lines.append(f"{label}: no forecast available")
            continue
        lines.append(
            f"{label}: {w['cond']}, rain {w['rain_pct']}% ({w['rain_mm']} mm), "
            f"{w['tmin']}-{w['tmax']}°C, wind up to {w['wind']} km/h"
        )
    table = "\n".join(lines)
    print("Weather:\n" + table)
    return table


if __name__ == "__main__":
    start = dt.date.today()
    print(get_forecast_table([start + dt.timedelta(days=i) for i in range(7)]))
