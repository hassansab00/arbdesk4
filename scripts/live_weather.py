"""
AD4 live weather monitor (Task 13d / Revision A §8.3). Every 15 minutes
via GitHub Actions (no per-run execution quota there, unlike n8n).

The observation infrastructure that makes S5 (running-max lock) possible:
the one place uncertainty collapses from +/-1.25C to near zero, because
you stop forecasting and start measuring. `day_decided` is the observed
trade cutoff. `BAND_CROSS` is the moment a market's likely winner changes.

Resolution-source-first, IEM-fallback: 50 of 54 cities resolve on
weather.gov/wrh/timeseries?site=<icao> [SOURCED]; this module tries that
first and falls back to IEM METAR (scripts/ingest_observations.py's
already-working fetch_station) if the primary is unavailable, RECORDING
WHICH SOURCE WAS USED on every row (never silently substituting). Hong
Kong's HKO endpoint is not given anywhere in the spec, so it always falls
back to IEM here rather than guessing a URL - same limitation as
scripts/settlement.py, same reason.

HONEST LIMITATION: the primary-source parser has not been confirmed
against a live weather.gov page (this sandbox has no egress to
weather.gov - see docs/settlement_verification.md for the same gap on
the settlement side). Until confirmed, this always exercises the IEM
fallback path in practice, which is real, tested code
(ingest_observations.py). Fix the parser using the same steps as
docs/settlement_verification.md before relying on the primary path.
"""
import datetime as dt
import sys

import requests

from common import rest, insert, upsert, get_cities, log_run, normalize_sky_condition, retry
from ingest_observations import fetch_station, parse as parse_iem

COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass_from_deg(deg):
    if deg is None:
        return None
    return COMPASS_16[round((deg % 360) / 22.5) % 16]


def resolution_source_url(icao):
    return f"https://www.weather.gov/wrh/timeseries?site={icao}"


def fetch_primary_reading(icao):
    """
    Best-effort read of the actual resolution source. Returns a dict
    matching the IEM row shape, or None if unparseable - see module
    docstring on why this currently always falls through to IEM.
    """
    try:
        r = requests.get(resolution_source_url(icao), timeout=15)
        r.raise_for_status()
        # TODO: unmeasured - page structure not confirmed live (see
        # module docstring). Not attempting to parse an unconfirmed shape
        # here; docs/settlement_verification.md's steps apply equally to
        # building this parser once real network access is available.
        return None
    except Exception:
        return None


def fetch_reading(city_key, icao, resolution_source):
    """Returns (row: dict, source: str) or (None, None)."""
    if resolution_source != "HKO":
        primary = fetch_primary_reading(icao)
        if primary is not None:
            return primary, "weather.gov/wrh/timeseries"

    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(hours=3)
    # Through retry(), not raw: this runs every 15 minutes across ~54
    # stations, which reliably trips the IEM API's rate limit. An unretried
    # 429 here killed the whole run - one busy station took every city's
    # reading down with it. retry() returns None on give-up, so a station
    # that stays rate-limited is skipped and the rest still get read.
    text = retry(lambda: fetch_station(icao, start.date(), end.date()),
                 label=f"IEM {icao}")
    rows = parse_iem(text, city_key) if text else []
    if not rows:
        return None, None
    rows.sort(key=lambda r: r["valid_at"])
    return rows[-1], "IEM"


def running_stats(observations_today):
    """observations_today: list of (valid_at_iso, temp_c), any order."""
    if not observations_today:
        return None, None, None
    ordered = sorted(observations_today, key=lambda o: o[0])
    max_val, max_at = None, None
    min_val = None
    for valid_at, temp_c in ordered:
        if temp_c is None:
            continue
        if max_val is None or temp_c > max_val:
            max_val, max_at = temp_c, valid_at
        if min_val is None or temp_c < min_val:
            min_val = temp_c
    return max_val, max_at, min_val


def trend_from_change(change_1h):
    if change_1h is None:
        return None
    if change_1h > 0.2:
        return "RISING"
    if change_1h < -0.2:
        return "FALLING"
    return "FLAT"


def compute_day_decided(observations_today, running_max_c, consecutive_needed=3, min_drop_c=0.5):
    """
    Noise-resistant trade cutoff: TRUE once the most recent
    `consecutive_needed` observations are each at least `min_drop_c` below
    the running max, AND none of them re-set a new max. A single reading
    below the max is a passing cloud, not the day turning over.
    """
    if running_max_c is None or len(observations_today) < consecutive_needed:
        return False
    ordered = sorted(observations_today, key=lambda o: o[0])
    tail = ordered[-consecutive_needed:]
    return all(t is not None and t <= running_max_c - min_drop_c for _, t in tail)


def peak_window_state(local_dt, window_start_hour, window_end_hour):
    """Returns (state, minutes_to_peak). window hours are local, 0-23."""
    if window_start_hour is None or window_end_hour is None or local_dt is None:
        return None, None
    hour = local_dt.hour + local_dt.minute / 60.0
    if hour < window_start_hour:
        return "BEFORE", int((window_start_hour - hour) * 60)
    if hour > window_end_hour:
        return "AFTER", None
    return "INSIDE", 0


def detect_events(city_key, prev, curr, thresholds):
    """
    prev/curr: dicts with temp_c, running_max_c, sky_condition, day_decided,
    peak_window_state, observed_at. prev may be None (first poll).
    Returns list of weather_events rows (without detected_at/city_key,
    caller fills those in).
    """
    events = []
    if prev is None:
        return events

    if curr.get("temp_change_1h") is not None:
        if curr["temp_change_1h"] >= thresholds.get("spike_c_per_hour", 2.0):
            events.append(dict(kind="SPIKE", severity="high", temp_c=curr["temp_c"],
                                change_c=curr["temp_change_1h"], detail={}))
        elif curr["temp_change_1h"] <= -thresholds.get("drop_c_per_hour", 2.0):
            events.append(dict(kind="DROP", severity="high", temp_c=curr["temp_c"],
                                change_c=curr["temp_change_1h"], detail={}))

    if curr.get("running_max_c") is not None and prev.get("running_max_c") is not None \
            and curr["running_max_c"] > prev["running_max_c"]:
        events.append(dict(kind="NEW_RUNNING_MAX", severity="high", temp_c=curr["running_max_c"],
                            change_c=curr["running_max_c"] - prev["running_max_c"], detail={}))

    if curr.get("day_decided") and not prev.get("day_decided"):
        events.append(dict(kind="DAY_DECIDED", severity="critical", temp_c=curr.get("temp_c"), detail={}))

    if curr.get("peak_window_state") == "INSIDE" and prev.get("peak_window_state") == "BEFORE":
        events.append(dict(kind="PEAK_WINDOW_OPEN", severity="medium", detail={}))

    if curr.get("sky_condition") and prev.get("sky_condition") and curr["sky_condition"] != prev["sky_condition"]:
        events.append(dict(kind="CONDITION_CHANGE", severity="medium",
                            detail={"from": prev["sky_condition"], "to": curr["sky_condition"]}))
        if curr["sky_condition"] in ("RAIN", "SNOW", "STORM") and prev["sky_condition"] not in ("RAIN", "SNOW", "STORM"):
            events.append(dict(kind="PRECIP_START", severity="high", detail={}))

    return events


def main():
    cities = get_cities(require_coords=False, require_icao=True)
    settings_rows = rest("settings", {"select": "key,value"})
    settings = {r["key"]: r["value"] for r in settings_rows}
    thresholds = settings.get("weather_alerts") or {}

    live_rows, event_rows, obs_rows = [], [], []
    critical_or_high = []

    for c in cities:
        city_key, icao = c["city_key"], c["icao"]
        resolution_source = c.get("resolution_source")
        row, source = fetch_reading(city_key, icao, resolution_source)
        if row is None:
            print(f"  ! {city_key} ({icao}): no reading available from any source")
            continue

        sky = normalize_sky_condition(row.get("cloud_cover"))
        obs_rows.append({**row, "sky_condition": sky, "source": source})

        today_start = dt.date.today().isoformat()
        history = rest("weather_observations", [
            ("select", "valid_at,temp_c"), ("city_key", f"eq.{city_key}"),
            ("valid_at", f"gte.{today_start}"), ("order", "valid_at.asc"),
        ])
        obs_today = [(h["valid_at"], h["temp_c"]) for h in history if h.get("temp_c") is not None]
        obs_today.append((row["valid_at"], row["temp_c"]))

        running_max_c, running_max_at, running_min_c = running_stats(obs_today)

        one_hour_ago = [t for v, t in obs_today if v <= (dt.datetime.fromisoformat(row["valid_at"]) - dt.timedelta(hours=1)).isoformat()]
        change_1h = (row["temp_c"] - one_hour_ago[-1]) if one_hour_ago else None

        day_decided = compute_day_decided(
            obs_today, running_max_c,
            consecutive_needed=thresholds.get("day_decided_consecutive_observations", 3),
            min_drop_c=thresholds.get("day_decided_min_drop_c", 0.5),
        )

        prev_rows = rest("live_weather", [("select", "*"), ("city_key", f"eq.{city_key}")])
        prev = prev_rows[0] if prev_rows else None

        curr = dict(temp_c=row["temp_c"], running_max_c=running_max_c, sky_condition=sky,
                    day_decided=day_decided, temp_change_1h=change_1h, peak_window_state=None)

        events = detect_events(city_key, prev, curr, thresholds)
        for e in events:
            event_rows.append({"city_key": city_key, **e, "notified": False})
            if e["severity"] in ("high", "critical"):
                critical_or_high.append({"city_key": city_key, **e})

        live_rows.append({
            "city_key": city_key, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "observed_at": row["valid_at"], "temp_c": row["temp_c"],
            "temp_f": row.get("temp_f"), "humidity": row.get("humidity"),
            "wind_speed_kt": row.get("wind_speed"), "wind_dir_deg": row.get("wind_dir_deg"),
            "wind_dir_compass": compass_from_deg(row.get("wind_dir_deg")),
            "pressure_hpa": row.get("pressure_hpa"), "visibility_m": row.get("visibility_m"),
            "sky_condition": sky, "precip_1h": row.get("precip"),
            "running_max_c": running_max_c, "running_max_at": running_max_at, "running_min_c": running_min_c,
            "temp_change_1h": change_1h, "trend": trend_from_change(change_1h),
            "day_decided": day_decided, "local_date": today_start,
        })

    if obs_rows:
        upsert("weather_observations", obs_rows, "city_key,valid_at,source")
    if live_rows:
        upsert("live_weather", live_rows, "city_key")
    if event_rows:
        insert("weather_events", event_rows)

    print(f"polled {len(cities)} cities, {len(event_rows)} events ({len(critical_or_high)} high/critical)")

    if critical_or_high:
        webhook = (settings.get("weather_alert_webhook") or {}).get("url")
        if webhook:
            try:
                requests.post(webhook, json={"events": critical_or_high}, timeout=15)
            except Exception as e:
                print(f"  ! n8n webhook notify failed: {e}", file=sys.stderr)
        else:
            print("  (no settings.weather_alert_webhook configured - n8n P1.1 notify skipped)")

    log_run("live_weather", "ok", len(live_rows), {"events": len(event_rows)})


if __name__ == "__main__":
    main()
