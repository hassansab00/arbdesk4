"""Bounded AWC METAR fallback for stations IEM did not deliver freshly.

This is another delivery of the same airport's routine/special reports, not
model weather. Keep its source separate, its reading time intact, and the
local receipt time separate from both it and AWC's upstream receipt time.
Never convert it into an IEM research-capture response.
"""
import datetime as dt
import math
import re

import requests

AWC = "https://aviationweather.gov/api/data/metar"
MAX_AGE_H = 3
BATCH_SIZE = 20
MAX_BATCHES = 3
TIMEOUT_S = 6
RESULT_LIMIT = 400


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def stale_cities(cities, rows, now):
    latest = {}
    for row in rows:
        if row.get("temp_c") is None:
            continue
        try:
            valid = dt.datetime.fromisoformat(row["valid_at"].replace("Z", "+00:00"))
            if valid.tzinfo is None or valid > now:
                continue
            key = row["city_key"]
            latest[key] = max(latest.get(key, valid), valid)
        except (ValueError, TypeError, KeyError):
            continue
    cutoff = now - dt.timedelta(hours=MAX_AGE_H)
    return sorted(c["city_key"] for c in cities
                  if latest.get(c["city_key"], cutoff - dt.timedelta(seconds=1)) < cutoff)


def parse(reports, cities, now, received_at):
    """Only requested ICAOs, matching raw METAR identities, finite temperatures
    and reading times in the tick's six-hour window. Drop malformed entries
    independently. A provider's timestamp never becomes our first receipt.
    """
    by_station = {(c.get("icao") or "").strip().upper(): c["city_key"] for c in cities}
    rows = {}
    for x in reports:
        if not isinstance(x, dict):
            continue
        station = x.get("icaoId")
        raw = x.get("rawOb") or ""
        if (station not in by_station or not station or x.get("metarType") not in ("METAR", "SPECI")
                or not isinstance(raw, str)):
            continue
        identity = re.match(r"^(?:(?:METAR|SPECI)\s+)?" + re.escape(station) + r"\s+(\d{6})Z\b", raw)
        if not identity:
            continue
        temp = number(x.get("temp"))
        stamp = number(x.get("obsTime"))
        if temp is None or stamp is None:
            continue
        try:
            valid = dt.datetime.fromtimestamp(stamp, dt.timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
        if not now - dt.timedelta(hours=6) <= valid <= now or valid.strftime("%d%H%M") != identity.group(1):
            continue
        row = {"city_key": by_station[station], "station": station,
               "valid_at": valid.isoformat(), "observed_at": received_at.isoformat(),
               "temp_c": temp, "temp_f": round(temp * 9 / 5 + 32, 2),
               "source": "AWC"}
        # Keep the fallback deliberately small: no guessed conversions of
        # pressure, visibility, precipitation or cloud codes into features.
        rows[(station, row["valid_at"])] = row
    return list(rows.values())


def fetch(cities, now):
    rows, errors = [], []
    ordered = sorted(cities, key=lambda c: c["icao"])
    batches = [ordered[i:i + BATCH_SIZE] for i in range(0, len(ordered), BATCH_SIZE)]
    if len(batches) > MAX_BATCHES:
        raise ValueError("AWC fallback exceeds its bounded station budget")
    for batch in batches:
        try:
            r = requests.get(AWC, params={"ids": ",".join(c["icao"] for c in batch),
                                          "format": "json", "hours": 6},
                             headers={"User-Agent": "ArbDesk4 station-report fallback"}, timeout=TIMEOUT_S)
            r.raise_for_status()
            reports = [] if r.status_code == 204 else r.json()
            received = dt.datetime.now(dt.timezone.utc)
            if not isinstance(reports, list):
                raise ValueError("AWC returned a non-list response")
            if len(reports) >= RESULT_LIMIT:
                raise ValueError("AWC response reached its result cap; history may be incomplete")
            rows.extend(parse(reports, batch, now, received))
        except Exception as e:
            errors.append(f"{type(e).__name__}: {str(e)[:160]}")
    return rows, {"requests": len(batches), "rows": len(rows), "errors": errors}
