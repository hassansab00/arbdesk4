#!/usr/bin/env python3
"""Collect final weather outcomes from each market's saved resolution rules.

This job is deliberately separate from venue settlement.  It writes only
authoritative station evidence after the contract's finality condition is met;
it never estimates a missing result and never rewrites an earlier capture.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from collections import Counter
from urllib.parse import urlsplit, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from common import log_run, rest_all, upsert
import metar_contract


PARSER_VERSION = "weather-outcomes-v5"

# How far back "recent" reaches. City-days inside this window are offered a
# slot BEFORE the backlog, so yesterday is always attempted no matter how big
# or how stuck the backlog behind it is. Three days, because a contract's
# authority can take two days to publish a final figure and anything that has
# not resolved by then is backlog, not news.
RECENT_DAYS = 3
USER_AGENT = "ArbDesk4/2.0 (+https://github.com/hassansab00/arbdesk4)"
MAX_RETRY_WAIT_S = 30  # Bounded recovery; longer provider waits defer to a later run.
WRH_KEY_URL = "https://www.weather.gov/source/wrh/apiKey.js"
WRH_API_URL = "https://api.synopticdata.com/v2/stations/timeseries"
HKO_DATA_URL = "https://www.weather.gov.hk/cis/dailyExtract/dailyExtract_{year}{month:02d}.xml"
WRH_URL_RE = re.compile(
    r"https://www\.weather\.gov/wrh/timeseries\?[^\s]*?site=([a-z0-9]{2,8})",
    re.IGNORECASE,
)
WU_URL_RE = re.compile(r"https://www\.wunderground\.com/[^\s]+", re.IGNORECASE)


class OutcomeError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.source_authority = None
        self.source_url = None


@dataclass(frozen=True)
class RuleSpec:
    adapter: str
    source_authority: str
    station_id: str
    source_url: str
    unit: str
    hourly_only: bool
    wu_fallback: bool = False


@dataclass(frozen=True)
class Outcome:
    observed_max_c: Decimal
    observed_at: str | None
    payload_sha256: str
    raw_payload: dict[str, Any]
    detail: dict[str, Any]


def parse_rule_spec(rules_text: str | None, market_unit: str | None = None) -> RuleSpec:
    rules = (rules_text or "").strip()
    lower = re.sub(r"\s+", " ", rules.lower().replace("\\n", " "))
    if not rules:
        raise OutcomeError("unsupported_source", "Market has no saved resolution rules")

    awc = metar_contract.source_url(rules)
    if awc:
        # These rules mention Celsius in their parsing instructions even for
        # Fahrenheit markets. Read the explicit RANGE unit, not the first unit.
        range_unit = re.search(r"each market in this event states its range in whole degrees (celsius|fahrenheit)", lower)
        required = ("a report whose line begins with speci", "exactly eight digits",
                    "first metar observation", "station's local time")
        if not range_unit or not all(part in lower for part in required):
            raise OutcomeError("unsupported_source", "AWC rules do not match the supported routine-temperature contract")
        try:
            station, _ = metar_contract.source_parameters(awc)
        except ValueError as exc:
            raise OutcomeError("parse_error", str(exc)) from exc
        return RuleSpec("aviation_weather_metar", "Aviation Weather Center routine METAR",
                        station, awc, "F" if range_unit[1] == "fahrenheit" else "C", True,
                        wu_fallback="aviation weather center has published no routine observation reporting a temperature" in lower
                        and "weather underground daily observations table will be used as the resolution source" in lower)

    if "degrees celsius" in lower or "deg. c" in lower:
        unit = "C"
    elif "degrees fahrenheit" in lower or "deg. f" in lower:
        unit = "F"
    elif market_unit in ("C", "F"):
        unit = market_unit
    else:
        raise OutcomeError("parse_error", "Resolution unit is not stated")

    if "hong kong observatory" in lower and "daily extract" in lower:
        source = re.search(r"https://www\.weather\.gov\.hk/[^\s]+", rules, re.I)
        return RuleSpec(
            adapter="hko_daily_extract",
            source_authority="Hong Kong Observatory Daily Extract",
            station_id="HKO",
            source_url=(source.group(0).rstrip(".,)") if source else
                        "https://www.weather.gov.hk/en/cis/climat.htm"),
            unit="C",
            hourly_only=False,
        )

    wrh = WRH_URL_RE.search(rules)
    if wrh:
        return RuleSpec(
            adapter="weather_gov_wrh",
            source_authority="NOAA weather.gov WRH Time Series",
            station_id=wrh.group(1).upper(),
            source_url=wrh.group(0).rstrip(".,)"),
            unit=unit,
            hourly_only="hourly data" in lower,
            wu_fallback=bool(re.search(
                r"if noaa data for the observation date is unavailable by 11:59 pm et "
                r"on the day following the observation date, the weather underground "
                r"daily observations table will be used as the resolution source",
                lower,
            )),
        )

    wu = WU_URL_RE.search(rules)
    if wu:
        raise OutcomeError(
            "unsupported_source",
            "Weather Underground is the named primary source; no stable, documented machine interface is configured",
        )

    raise OutcomeError("unsupported_source", "No supported authoritative source found in saved rules")


def retry_wait(response, attempt: int, now: dt.datetime | None = None) -> float:
    """Respect the provider's Retry-After; never retry earlier to fit our budget."""
    value = getattr(response, "headers", {}).get("Retry-After")
    if value is not None:
        try:
            seconds = float(value)
            if math.isfinite(seconds):
                return max(0.0, seconds)
        except (TypeError, ValueError):
            try:
                until = parsedate_to_datetime(value)
                if until.tzinfo is None:
                    until = until.replace(tzinfo=dt.timezone.utc)
                return max(0.0, (until - (now or dt.datetime.now(dt.timezone.utc))).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return float(2 ** attempt)


def local_day_finished(for_date: str, timezone: str | None, now: dt.datetime) -> bool:
    if not timezone:
        raise OutcomeError("source_clock_unavailable", "City timezone is missing; final evidence was not requested")
    try:
        end = dt.datetime.combine(dt.date.fromisoformat(for_date) + dt.timedelta(days=1),
                                  dt.time(), ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise OutcomeError("source_clock_unavailable", "City timezone is unknown; final evidence was not requested") from exc
    return now >= end


def _get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    import common
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    # A provider's rate limit covers later cities too, not just this URL.
    cooldowns = getattr(session, "_outcome_cooldowns", None)
    if cooldowns is None:
        cooldowns = session._outcome_cooldowns = {}
    host = urlsplit(url).netloc
    last_error: Exception | None = None
    for attempt in range(4):
        response = None
        try:
            remaining_wait = cooldowns.get(host, 0) - time.monotonic()
            if remaining_wait > MAX_RETRY_WAIT_S:
                raise OutcomeError("source_unavailable", f"Provider cooldown active for {host}")
            if remaining_wait > 0:
                common._budget_sleep(remaining_wait)
            response = session.get(url, headers=headers,
                                   **common._request_kwargs(dict(kwargs, timeout=45)))
            if response.status_code < 400:
                return response
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise OutcomeError("source_unavailable", f"HTTP {response.status_code} from {url}")
            last_error = requests.HTTPError(
                f"HTTP {response.status_code} from {url}", response=response
            )
            if response.status_code in {429, 503}:
                cooldowns[host] = time.monotonic() + retry_wait(response, attempt)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
        except TimeoutError as exc:
            raise OutcomeError("deferred", str(exc)) from exc
        if attempt < 3:
            wait = retry_wait(response, attempt)
            if wait > MAX_RETRY_WAIT_S:
                break  # Preserve the failure and defer; do not hammer before Retry-After.
            try:
                common._budget_sleep(wait)
                # This serial session has waited out its own cooldown.
                if response is not None and response.status_code in {429, 503}:
                    cooldowns.pop(host, None)
            except TimeoutError as exc:
                raise OutcomeError("deferred", str(exc)) from exc
    raise OutcomeError("source_unavailable", str(last_error or f"Unable to read {url}"))


def _json_bytes(response: requests.Response) -> tuple[dict[str, Any], bytes]:
    raw = response.content
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutcomeError("parse_error", f"Source returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise OutcomeError("parse_error", "Source JSON root is not an object")
    return parsed, raw


def _packed_raw(raw: bytes, content_type: str) -> dict[str, Any]:
    """Retain the exact response compactly; mtime=0 makes exports reproducible."""
    return {
        "encoding": "gzip+base64",
        "content_type": content_type,
        "uncompressed_bytes": len(raw),
        "data": base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0)).decode("ascii"),
    }


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_source_time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OutcomeError("parse_error", f"Invalid source timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise OutcomeError("parse_error", f"Source timestamp has no UTC offset: {value!r}")
    return parsed


def _js_round(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OutcomeError("parse_error", f"Invalid temperature value: {value!r}") from exc
    if not math.isfinite(number):
        raise OutcomeError("parse_error", f"Non-finite temperature value: {value!r}")
    return math.floor(number + 0.5)


def _wrh_hourly_eligible(
    *, network: str, minute: int, sea_level_pressure: Any, metar: Any,
    station_id: str, has_pressure_series: bool, point_count: int,
) -> bool:
    network = "ASOS/AWOS" if network == "GLOBAL-METAR" else network
    if network == "ASOS/AWOS":
        if has_pressure_series:
            return sea_level_pressure is not None or (
                isinstance(metar, str) and metar.upper().startswith(station_id.upper())
            )
        return 51 <= minute <= 59
    return minute <= 4 or minute >= 56 or point_count <= 72


def parse_wrh_payload(
    payload: dict[str, Any], raw: bytes, spec: RuleSpec, for_date: dt.date,
) -> Outcome:
    summary = payload.get("SUMMARY") or {}
    if summary.get("RESPONSE_MESSAGE") != "OK":
        raise OutcomeError(
            "source_unavailable",
            f"WRH upstream response: {summary.get('RESPONSE_MESSAGE') or 'unknown error'}",
        )
    stations = payload.get("STATION") or []
    if len(stations) != 1 or not isinstance(stations[0], dict):
        raise OutcomeError("parse_error", "WRH response did not contain exactly one station")
    station = stations[0]
    returned_id = str(station.get("STID") or "").upper()
    if returned_id != spec.station_id:
        raise OutcomeError(
            "parse_error", f"Requested {spec.station_id}, source returned {returned_id or 'no station'}"
        )
    # The current named interface (weather.gov/source/wrh/timeseries/obs.js,
    # v202610211630) only renders COUNTRY == US; it explicitly withholds
    # international observations. API reachability alone is not publication
    # by the contract's authority. Keep prior finalized evidence immutable.
    country = station.get("COUNTRY")
    if country is None:
        raise OutcomeError("parse_error", "WRH station country is missing")
    if country != "US":
        raise OutcomeError("unsupported_source",
                           "The named weather.gov WRH interface no longer publishes international observations")
    observations = station.get("OBSERVATIONS") or {}
    times = observations.get("date_time") or []
    temperatures = observations.get("air_temp_set_1") or []
    if not times or len(times) != len(temperatures):
        raise OutcomeError("parse_error", "WRH timestamps and temperatures are missing or misaligned")

    pressure = observations.get("sea_level_pressure_set_1")
    metars = observations.get("metar_set_1")
    has_pressure = isinstance(pressure, list)
    network = str(station.get("SHORTNAME") or "").upper()
    target_rows: list[dict[str, Any]] = []
    selected: list[tuple[dt.datetime, int]] = []
    following: list[dt.datetime] = []

    for index, timestamp in enumerate(times):
        when = _parse_source_time(str(timestamp))
        if when.date() > for_date:
            following.append(when)
        if when.date() != for_date:
            continue
        temp = temperatures[index]
        slp = pressure[index] if isinstance(pressure, list) and index < len(pressure) else None
        metar = metars[index] if isinstance(metars, list) and index < len(metars) else None
        eligible = temp is not None and (
            not spec.hourly_only or _wrh_hourly_eligible(
                network=network,
                minute=when.minute,
                sea_level_pressure=slp,
                metar=metar,
                station_id=spec.station_id,
                has_pressure_series=has_pressure,
                point_count=len(times),
            )
        )
        target_rows.append({
            "date_time": timestamp,
            "air_temp": temp,
            "sea_level_pressure": slp,
            "metar": metar,
            "eligible": eligible,
        })
        if eligible:
            selected.append((when, _js_round(temp)))

    if not following:
        raise OutcomeError(
            "not_final", "The source has not published its first datapoint for the following local date"
        )
    if not selected:
        raise OutcomeError("parse_error", "No eligible temperature readings exist for the target local date")

    display_max = max(value for _, value in selected)
    max_time = min(when for when, value in selected if value == display_max)
    max_c = (Decimal(display_max) if spec.unit == "C" else
             (Decimal(display_max) - Decimal(32)) * Decimal(5) / Decimal(9))
    max_c = max_c.quantize(Decimal("0.000001"))

    canonical = {
        "schema": "weather-gov-wrh-timeseries-v1",
        "station": {
            "id": returned_id,
            "name": station.get("NAME"),
            "timezone": station.get("TIMEZONE"),
            "network": station.get("SHORTNAME"),
        },
        "for_date": for_date.isoformat(),
        "display_unit": spec.unit,
        "hourly_only": spec.hourly_only,
        "observations": target_rows,
        "first_following_datapoint": min(following).isoformat(),
    }
    canonical_raw = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return Outcome(
        observed_max_c=max_c,
        observed_at=max_time.isoformat(),
        payload_sha256=_sha(canonical_raw),
        raw_payload=_packed_raw(canonical_raw, "application/json; profile=weather-gov-wrh-timeseries-v1"),
        detail={
            "source_response_sha256": _sha(raw),
            "source_timezone": station.get("TIMEZONE"),
            "source_network": station.get("SHORTNAME"),
            "target_readings": len(target_rows),
            "eligible_readings": len(selected),
            "display_max": display_max,
            "display_unit": spec.unit,
            "first_following_datapoint": min(following).isoformat(),
        },
    )


def parse_wrh_token(source: str) -> str:
    """Read the active static assignment, preserving strings while removing JS comments.

    weather.gov retains a retired assignment in a line comment. Matching the
    first occurrence of the variable sends that retired token to the provider.
    Do not execute the downloaded JavaScript or fall back to commented values.
    """
    uncommented = re.sub(
        r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|/\*[\s\S]*?\*/|//[^\r\n]*''',
        lambda match: match.group(1) or "", source,
    )
    matches = re.findall(
        r'''(?m)^[ \t]*(?:var|let|const)\s+mesoToken\s*=\s*(['"])([A-Za-z0-9]+)\1\s*;''',
        uncommented,
    )
    if not matches:
        raise OutcomeError("parse_error", "weather.gov did not publish an active WRH data token")
    return matches[-1][1]


def fetch_wrh_outcome(
    session: requests.Session, spec: RuleSpec, for_date: dt.date,
) -> Outcome:
    key_response = _get(session, WRH_KEY_URL)
    token = parse_wrh_token(key_response.text)
    start = (for_date - dt.timedelta(days=1)).strftime("%Y%m%d0000")
    end = (for_date + dt.timedelta(days=2)).strftime("%Y%m%d2359")
    params = {
        "STID": spec.station_id,
        "showemptystations": "1",
        "start": start,
        "end": end,
        "complete": "1",
        "token": token,
        "obtimezone": "local",
    }
    if spec.unit == "F":
        params["units"] = "temp|F,speed|mph,english"
    response = _get(
        session,
        WRH_API_URL,
        params=params,
        headers={"Referer": "https://www.weather.gov/", "Origin": "https://www.weather.gov"},
    )
    parsed, raw = _json_bytes(response)
    return parse_wrh_payload(parsed, raw, spec, for_date)


def parse_hko_payload(
    payload: dict[str, Any], raw: bytes, spec: RuleSpec, for_date: dt.date,
) -> Outcome:
    blocks = ((payload.get("stn") or {}).get("data") or [])
    block = next((item for item in blocks if item.get("month") == for_date.month), None)
    if not block:
        raise OutcomeError("not_final", "HKO has not published the target month in Daily Extract")
    row = next(
        (item for item in (block.get("dayData") or [])
         if item and str(item[0]).strip().isdigit()
         and int(str(item[0]).strip()) == for_date.day),
        None,
    )
    if not row:
        raise OutcomeError("not_final", "HKO has not finalized the target date in Daily Extract")
    if len(row) < 3:
        raise OutcomeError("parse_error", "HKO Daily Extract row has no Absolute Daily Max column")
    raw_value = str(row[2]).strip()
    if not raw_value or "*" in raw_value or "#" in raw_value:
        raise OutcomeError("not_final", "HKO target value is unavailable or marked incomplete")
    try:
        max_c = Decimal(raw_value)
    except InvalidOperation as exc:
        raise OutcomeError("parse_error", f"Invalid HKO Absolute Daily Max: {raw_value!r}") from exc
    if not Decimal("-100") <= max_c <= Decimal("70"):
        raise OutcomeError("parse_error", f"HKO Absolute Daily Max is outside physical bounds: {max_c}")
    return Outcome(
        observed_max_c=max_c,
        observed_at=None,
        payload_sha256=_sha(raw),
        raw_payload=_packed_raw(raw, "application/json; profile=hko-daily-extract-v1"),
        detail={
            "display_max": str(max_c),
            "display_unit": "C",
            "daily_extract_column": "Absolute Daily Max (deg. C)",
        },
    )


def fetch_hko_outcome(
    session: requests.Session, spec: RuleSpec, for_date: dt.date,
) -> Outcome:
    data_url = HKO_DATA_URL.format(year=for_date.year, month=for_date.month)
    response = _get(session, data_url)
    parsed, raw = _json_bytes(response)
    outcome = parse_hko_payload(parsed, raw, spec, for_date)
    return Outcome(
        observed_max_c=outcome.observed_max_c,
        observed_at=outcome.observed_at,
        payload_sha256=outcome.payload_sha256,
        raw_payload=outcome.raw_payload,
        detail={**outcome.detail, "data_url": data_url},
    )


def collect_one(
    session: requests.Session, spec: RuleSpec, for_date: dt.date,
) -> Outcome:
    if spec.adapter == "weather_gov_wrh":
        return fetch_wrh_outcome(session, spec, for_date)
    if spec.adapter == "hko_daily_extract":
        return fetch_hko_outcome(session, spec, for_date)
    raise OutcomeError("unsupported_source", f"Unsupported adapter: {spec.adapter}")


def wu_fallback_cutoff(for_date: dt.date) -> dt.datetime:
    """The saved contract's ET deadline, including DST, not the city's clock."""
    return dt.datetime.combine(for_date + dt.timedelta(days=1), dt.time(23, 59),
                               ZoneInfo("America/New_York"))


def collect_awc(session, spec, for_date, timezone, now):
    """Read the named raw source AFTER its next-local-day publication check.

    A successful empty source is different from a network/parser failure.
    Missing measurements never become a fabricated lowest-bracket temperature;
    the independent venue verifier can record that contract payout instead.
    """
    try:
        station, anchor = metar_contract.validate_window(spec.source_url, for_date, timezone)
        zone = ZoneInfo(timezone)
        local_end = dt.datetime.combine(for_date + dt.timedelta(days=1), dt.time(), zone)
        if station != spec.station_id:
            raise ValueError("AWC source station/date does not match the target local day")
        if now < local_end:
            raise OutcomeError("not_final", "The station's local day has not ended")
        cutoff = wu_fallback_cutoff(for_date)
        check_at = min(now, cutoff).astimezone(dt.timezone.utc)
        hours = max(1, math.ceil((check_at - local_end).total_seconds() / 3600) + 1)
        check_url = "https://aviationweather.gov/api/data/metar?" + urlencode({
            "ids": station, "format": "raw", "date": check_at.isoformat().replace("+00:00", "Z"),
            "hours": hours})
        # Check publication first, then read the contract's target-date surface.
        check = _get(session, check_url)
        following_raw = check.content
        next_rows = metar_contract.raw_reports(following_raw, station, check_at,strict=False)
        following = [r["when"] for r in next_rows
                     if r["routine"] and r["when"].astimezone(zone).date() == for_date + dt.timedelta(days=1)]
        if not following and now < cutoff:
            raise OutcomeError("not_final", "AWC has not published a routine observation for the following local date")
        response = _get(session, spec.source_url)
        skipped=[]
        rows = metar_contract.raw_reports(response.content, station, anchor,strict=False,skipped=skipped)
        if len(rows) >= 400:
            raise ValueError("AWC response reached its documented result limit")
        selected = [r for r in rows if r["routine"] and r["temp_c"] is not None
                    and r["when"].astimezone(zone).date() == for_date]
        # A malformed time cannot be assigned to a local date. It is harmless
        # only if its temperature cannot increase the proved valid maximum.
        # Otherwise keep the outcome unknown, including at the fallback cutoff.
        located={r['raw'] for r in rows}
        uncertain=[]
        for line in response.content.decode('utf-8-sig').splitlines():
            line=line.strip().rstrip('=').strip()
            if line and line not in located:
                value=metar_contract.temperature_c(line)
                if value is not None: uncertain.append(value)
        if uncertain and (not selected or max(uncertain)>max(r['temp_c'] for r in selected)):
            raise OutcomeError('parse_error','Unreadable observation time could change the qualifying maximum')
        if not selected:
            if now < cutoff:
                raise OutcomeError("not_final", "AWC has no qualifying temperature; awaiting the contract fallback deadline")
            if not spec.wu_fallback:
                raise OutcomeError("source_unavailable", "AWC published no qualifying temperature and no supported fallback is named")
            wu_url = f"https://www.wunderground.com/history/daily/{station}/date/{for_date.isoformat()}"
            wu = _get(session, wu_url, headers={"Cookie": "wu_units=" + ("m" if spec.unit == "C" else "e")})
            outcome = parse_wu_daily(wu.content, spec, for_date, timezone, now)
            return Outcome(outcome.observed_max_c, outcome.observed_at, outcome.payload_sha256,
                           outcome.raw_payload, {**outcome.detail,
                           "fallback_reason": "awc_published_no_qualifying_temperature_by_deadline",
                           "primary_source_response_sha256": _sha(response.content),
                           "primary_publication_check_sha256": _sha(following_raw)})
        maximum = max(r["temp_c"] for r in selected)
        max_time = min(r["when"] for r in selected if r["temp_c"] == maximum)
        display = metar_contract.rounded_unit(maximum, spec.unit)
        max_c = Decimal(display) if spec.unit == "C" else (Decimal(display) - 32) * 5 / 9
        canonical = {"schema": "awc-routine-metar-v1", "station_id": station,
                     "for_date": for_date.isoformat(), "timezone": timezone,
                     "source_url": spec.source_url, "target_response": response.content.decode("utf-8-sig"),
                     "publication_check_url": check_url, "publication_check_response": following_raw.decode("utf-8-sig"),
                     "first_following_routine": min(following).isoformat() if following else None,
                     "read_at": now.isoformat(), "deadline": cutoff.isoformat()}
        packed = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        return Outcome(max_c.quantize(Decimal("0.000001")), max_time.isoformat(), _sha(packed),
                       _packed_raw(packed, "application/json; profile=awc-routine-metar-v1"),
                       {"source_authority": spec.source_authority, "source_url": spec.source_url,
                        "source_timezone": timezone, "qualifying_max_c": str(maximum),
                        "eligible_readings": len(selected), "display_max": display, "display_unit": spec.unit,
                        "first_following_routine": canonical["first_following_routine"],
                        "nondecisive_unreadable_reports":len(skipped),
                        "finality": "following_routine" if following else "contract_deadline"})
    except (ValueError, UnicodeDecodeError, ZoneInfoNotFoundError) as exc:
        raise OutcomeError("parse_error", str(exc)) from exc


class _WUDailyPage(HTMLParser):
    """Read only the airport daily table and its timestamped chart twin.

    Never retain the whole HTML: WU's unrelated page configuration contains
    client keys. The canonical evidence below contains observation data only.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.airports = []
        self.in_airport = False
        self.table_depth = 0
        self.tables = 0
        self.rows = []
        self.cells = None
        self.cell = None
        self.charts = []
        self.chart = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "airport-body":
            self.airports.append(attrs)
            self.in_airport = True
        if not self.in_airport:
            return
        if tag == "table":
            if self.table_depth:
                self.table_depth += 1
            elif "observations-table" in attrs.get("class", "").split():
                self.tables += 1
                self.table_depth = 1
        if self.table_depth:
            if tag == "tr":
                self.cells = []
            elif tag in {"th", "td"} and self.cells is not None:
                self.cell = []
        if (tag == "script" and attrs.get("type") == "application/json"
                and "chart-data" in attrs.get("class", "").split()):
            self.chart = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        if self.chart is not None:
            self.chart.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.cell is not None:
            self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.cells is not None:
            self.rows.append(self.cells)
            self.cells = None
        elif tag == "table" and self.table_depth:
            self.table_depth -= 1
        elif tag == "script" and self.chart is not None:
            self.charts.append("".join(self.chart))
            self.chart = None
        elif tag == "airport-body":
            self.in_airport = False


def parse_wu_daily(raw: bytes, spec: RuleSpec, for_date: dt.date,
                   timezone: str, now: dt.datetime) -> Outcome:
    cutoff = wu_fallback_cutoff(for_date)
    if now < cutoff:
        raise OutcomeError("not_final", "The saved rules' Weather Underground fallback deadline has not passed")
    page = _WUDailyPage()
    try:
        page.feed(raw.decode("utf-8-sig"))
        if len(page.airports) != 1 or page.tables != 1 or len(page.charts) != 1:
            raise ValueError("Expected one airport, daily table and timestamped chart")
        airport = page.airports[0]
        if (airport.get("data-icao-code") != spec.station_id
                or airport.get("data-location-id", "").split(":")[0] != spec.station_id
                or airport.get("data-mode") != "daily"):
            raise ValueError("Airport identity or daily page mode does not match the contract")
        if dt.date(*map(int, airport.get("data-date", "").split("-"))) != for_date:
            raise ValueError("Daily page returned a different observation date")
        source_zone = ZoneInfo(airport["data-time-zone"])
        expected_zone = ZoneInfo(timezone)
        for day in (for_date, for_date + dt.timedelta(days=1)):
            midnight = dt.datetime.combine(day, dt.time())
            if midnight.replace(tzinfo=source_zone).utcoffset() != midnight.replace(tzinfo=expected_zone).utcoffset():
                raise ValueError("Airport timezone does not match the city's local day")
        if not local_day_finished(for_date.isoformat(), timezone, now):
            raise ValueError("The airport's local observation day has not ended")
        if not page.rows or page.rows[0][:2] != ["Time", "Temperature"]:
            raise ValueError("Daily Observations table columns changed")
        table = []
        for row in page.rows[1:]:
            if len(row) != len(page.rows[0]):
                raise ValueError("Daily Observations row is incomplete")
            clock = dt.datetime.strptime(row[0], "%I:%M %p").time().isoformat()
            if row[1] in {"-", "--", ""}:
                table.append((clock, None))
                continue
            reading = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*°([CF])", row[1])
            if not reading or reading.group(2) != spec.unit:
                raise ValueError("Daily table temperature unit does not match the contract")
            table.append((clock, Decimal(reading.group(1))))
        points = json.loads(page.charts[0], parse_float=Decimal)
        if not isinstance(points, list) or not points:
            raise ValueError("No timestamped daily observations")
        canonical_rows = []
        twin = []
        timestamps = set()
        for point in points:
            stamp = point["ts"]
            if isinstance(stamp, bool) or not isinstance(stamp, (int, Decimal)):
                raise ValueError("Observation timestamp is invalid")
            when = dt.datetime.fromtimestamp(float(stamp) / 1000, dt.timezone.utc).astimezone(source_zone)
            if when.date() != for_date or stamp in timestamps:
                raise ValueError("Observation timestamp is duplicated or outside the target local day")
            timestamps.add(stamp)
            value = point.get("temp")
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                    raise ValueError("Observation temperature is invalid")
                value = Decimal(value)
                celsius = value if spec.unit == "C" else (value - 32) * Decimal(5) / 9
                if not value.is_finite() or not Decimal(-100) <= celsius <= Decimal(70):
                    raise ValueError("Observation temperature is outside physical bounds")
            twin.append((when.time().isoformat(), value))
            canonical_rows.append({"date_time": when.isoformat(),
                                   "temperature": str(value) if value is not None else None})
        if Counter(table) != Counter(twin):
            raise ValueError("Daily table and timestamped observations disagree")
        values = [(row["date_time"], Decimal(row["temperature"]))
                  for row in canonical_rows if row["temperature"] is not None]
        if not values:
            raise ValueError("Daily table has no measured temperatures")
    except (ValueError, KeyError, TypeError, OverflowError, InvalidOperation, ZoneInfoNotFoundError) as exc:
        raise OutcomeError("parse_error", "Weather Underground daily page failed station/date/unit/table validation") from exc

    # Contracts use whole degrees; read the table directly in that unit before
    # rounding. Converting rounded Fahrenheit back to Celsius loses precision.
    display_max = _js_round(float(max(value for _, value in values)))
    observed_at = min(when for when, value in values if _js_round(float(value)) == display_max)
    max_c = Decimal(display_max) if spec.unit == "C" else (Decimal(display_max) - 32) * Decimal(5) / 9
    source_url = f"https://www.wunderground.com/history/daily/{spec.station_id}/date/{for_date.isoformat()}"
    canonical = {"schema": "wunderground-airport-daily-table-v1", "station_id": spec.station_id,
                 "source_timezone": str(source_zone), "for_date": for_date.isoformat(),
                 "display_unit": spec.unit, "observations": sorted(canonical_rows, key=lambda r: r["date_time"]),
                 "fallback_cutoff": cutoff.isoformat()}
    packed = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return Outcome(max_c.quantize(Decimal("0.000001")), observed_at, _sha(packed),
                   _packed_raw(packed, "application/json; profile=wunderground-airport-daily-table-v1"),
                   {"source_authority": "Weather Underground Daily Observations",
                    "source_url": source_url, "source_response_sha256": _sha(raw),
                    "source_timezone": str(source_zone), "target_readings": len(values),
                    "display_max": display_max, "display_unit": spec.unit,
                    "fallback_cutoff": cutoff.isoformat(), "fallback_reason": "named_primary_withholds_international_observations"})


def collect_with_fallback(session, spec, for_date, timezone, now):
    if spec.adapter == "aviation_weather_metar":
        return collect_awc(session, spec, for_date, timezone, now)
    try:
        return collect_one(session, spec, for_date)
    except OutcomeError as exc:
        # A local timeout, bad credential or novel parser failure says nothing
        # about publication by the primary authority. Only the known WRH
        # international publication restriction establishes this fallback.
        if not (spec.adapter == "weather_gov_wrh" and spec.wu_fallback
                and exc.status == "unsupported_source"):
            raise
        if now < wu_fallback_cutoff(for_date):
            raise OutcomeError("not_final", "Primary publication unavailable; awaiting the saved rules' Weather Underground deadline") from exc
        url = f"https://www.wunderground.com/history/daily/{spec.station_id}/date/{for_date.isoformat()}"
        # This is WU's own anonymous UI preference (scale-change / preference-model).
        try:
            response = _get(session, url, headers={"Cookie": "wu_units=" + ("m" if spec.unit == "C" else "e")})
            return parse_wu_daily(response.content, spec, for_date, timezone, now)
        except OutcomeError as error:
            error.source_authority = "Weather Underground Daily Observations"
            error.source_url = url
            raise


def _id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()


def _safe_error(exc: Exception) -> str:
    text = re.sub(r"token=[^&\s]+", "token=[redacted]", str(exc), flags=re.I)
    return text[:500]


# An EMPTY rules_text is not a verdict about anything.
#
# parse_rule_spec raises unsupported_source for it (line ~79), and the attempt
# row then records a fingerprint of the empty string - which never moves while
# the column stays empty, so _load_targets skips that city-day for ever.
# Measured 2026-09-21: of 253 permanently-skipped city-days, 233 were markets
# whose rules_text was blank, all of them resolution_date 2026-09-06..09-12,
# all backfilled into `markets` on 09-15 already closed, and every one of them
# with rules_fetched_at NULL because P0.5 (Refresh Rules Text) did not exist
# until 09-12. Nothing was ever going to refresh them, and nothing was ever
# going to re-ask.
#
# The cost is not settlement - the venue settles YES/NO and every trading day
# from 09-13 has its 48-49 verified evidence rows. The cost is forecast-skill
# scoring: weather_resolution_evidence has ZERO rows for 09-06..09-11, which
# is about 3,000 unbanked fact_forecast_outcome rows, roughly a fifth of the
# month's calibration sample, and invisible.
#
# So: an absent input gets no fingerprint. The verdict is still recorded - the
# attempt row is written either way - but without a hash it is a question the
# next run asks again rather than an answer it already has. The bound matters
# and is deliberate: NOT fingerprinting every unsupported_source would recreate
# the 2026-09-15 outage verbatim, where 250 of 250 city-day slots were spent
# re-deciding the same unparseable markets and `captured` came back 0. A
# Weather Underground market with real rules naming a source with no machine
# interface is a genuine, stable verdict; a blank column is not.
def rules_are_absent(rules_text: str | None) -> bool:
    return not (rules_text or "").strip()


def _rules_fingerprint(rules_text: str | None, unit: str | None) -> str:
    """A hash of exactly what parse_rule_spec reads, and nothing else.

    unsupported_source is a verdict on the market's saved rules, not on the
    weather: the same text through the same parser fails the same way every
    time. Recording this hash beside the verdict is what lets a later run tell
    "we already know this cannot be parsed" from "this might work now" - the
    hash moves the moment the venue republishes rules naming a source we do
    support, and the city-day becomes a target again on its own.

    market_unit is in here because parse_rule_spec reads it too: the same
    rules text with the unit filled in is a different input.
    """
    h = hashlib.sha256()
    h.update((rules_text or "").strip().encode("utf-8"))
    h.update(b"\x00")
    h.update((unit or "").encode("utf-8"))
    return h.hexdigest()


def _unparseable(days: int) -> dict[tuple[str, str], str]:
    """City-days whose newest attempt says the saved rules cannot be parsed.

    Maps (city, date) -> the rules fingerprint at the time of that verdict.
    _load_targets skips one only if the market's CURRENT fingerprint still
    matches, so this can never bury a city-day whose rules have since changed.

    Attempts written before this hash existed carry no fingerprint and are
    simply not returned: they get a slot, fail again, and record one.
    """
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        rows = rest_all(
            "weather_resolution_attempts",
            [("select", "city_key,for_date,outcome_status,detail,captured_at"),
             ("for_date", f"gte.{cutoff}")],
            order="city_key.asc,for_date.asc,captured_at.asc",
            page_size=1000,
        )
    except Exception:
        return {}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:                       # ascending, so the last write wins
        latest[(row["city_key"], str(row["for_date"]))] = row
    out: dict[tuple[str, str], str] = {}
    for key, row in latest.items():
        if row.get("outcome_status") != "unsupported_source":
            continue
        fingerprint = (row.get("detail") or {}).get("rules_sha256")
        if fingerprint:
            out[key] = fingerprint
    return out


def _load_targets(days: int, maximum: int,
                  finalized: set[tuple[str, str]] | None = None,
                  unparseable: dict[tuple[str, str], str] | None = None,
                  city_keys: set[str] | None = None,
                  clocks: dict[str, str | None] | None = None,
                  now: dt.datetime | None = None,
                  last_attempts: dict[tuple[str, str], str] | None = None,
                  ) -> list[dict[str, Any]]:
    """The city-days that still NEED collecting, recent first, capped at `maximum`.

    THE BACKLOG COULD NOT TRAVERSE. The cap used to be applied to every
    city-day in the window, finished or not, after sorting oldest-first:

        return sorted(latest.values(), key=...)[:maximum]

    Every already-verified day therefore consumed a slot. Once the oldest 250
    were done, every subsequent run loaded those same 250, skipped all of them
    as `already_final`, and stopped - so widening --days reached no further
    back. It re-read history it had already finished and never arrived at the
    part it had not. That is why 49 cities sat at ~7 days of evidence each
    while the window nominally covered fourteen.

    Skipping them HERE instead spends the whole budget on work that remains,
    and the run walks steadily backwards through the backlog. The skip rule is
    deliberately identical to run()'s own - record_status == 'verified', the
    frozen first publication - so this only moves WHEN the decision is made,
    never WHAT is decided. An unverified prior (not_final, a source revision,
    a parse error) is still a target, exactly as before.

    IT COULD NOT TRAVERSE AGAIN, for the mirror-image reason, and this is the
    part that stopped the desk. Skipping the finished days left the budget to
    the outstanding ones - but 259 of the 408 outstanding city-days in the
    window were markets whose saved rules name a source with no machine
    interface. unsupported_source is not a transient failure; it is the same
    verdict on the same text every single run. 259 against a cap of 250, taken
    oldest first, is the whole budget: every run spent all 250 slots
    re-deriving verdicts it already had, reached 2026-09-14, and stopped.
    Nothing was attempted for the 15th or the 16th at all. The last verified
    outcome froze at 2026-09-14 16:54, fact_forecast_outcome and
    fact_signal_outcome went stale behind it, and "what the desk expects"
    stopped being scored - which is what a desk owner sees as the predictions
    being out of date.

    So two rules now, not one:

      RECENT FIRST. City-days inside RECENT_DAYS are offered slots before the
      backlog. Yesterday is attempted even if the backlog is enormous, stuck,
      or stuck in a way nobody has thought of yet - which is the guarantee
      worth having, because this is the second distinct cause of the same
      outage and there is no reason to believe it is the last.

      AND SKIP WHAT CANNOT SUCCEED. A city-day whose newest attempt is
      unsupported_source, and whose rules fingerprint has not changed since,
      is not retried. It is a verdict, not a failure, and re-deriving it costs
      a slot that real work needs. It returns to the list by itself the moment
      the venue republishes different rules.

    Neither rule changes WHAT is decided or writes anything. They change only
    which city-days get a slot, and in what order.
    """
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    today = dt.date.today().isoformat()
    recent_from = (dt.date.today() - dt.timedelta(days=RECENT_DAYS)).isoformat()
    rows = rest_all(
        "markets",
        [
            ("select", "market_id,city_key,resolution_date,rules_text,last_seen_at"),
            ("resolution_date", f"gte.{cutoff}"),
            ("resolution_date", f"lte.{today}"),
        ],
        order="resolution_date.asc,market_id.asc",
        page_size=500,
    )
    # THE CANONICAL UNIT (plan v2 P2.5). The rule is parsed in the market's
    # unit, and 667 raw markets dated 15 Apr - 5 Sep carry the wrong one
    # (measured 23 Sep). rules_text and last_seen_at are not in the canonical
    # view, so the unit alone is read from it.
    canonical_unit = {r["market_id"]: r.get("unit") for r in rest_all(
        "v_canonical_markets",
        [
            ("select", "market_id,unit"),
            ("resolution_date", f"gte.{cutoff}"),
            ("resolution_date", f"lte.{today}"),
        ],
        order="resolution_date.asc,market_id.asc",
        page_size=500,
    )}
    for row in rows:
        row["unit"] = canonical_unit.get(row.get("market_id"))
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["city_key"], row["resolution_date"])
        if key not in latest or str(row.get("last_seen_at") or "") > str(latest[key].get("last_seen_at") or ""):
            latest[key] = row
    done = finalized or set()
    stuck = unparseable or {}
    outstanding = []
    for row in latest.values():
        if city_keys and row["city_key"] not in city_keys:
            continue
        key = (row["city_key"], row["resolution_date"])
        if key in done:
            continue
        if stuck.get(key) == _rules_fingerprint(row.get("rules_text"), row.get("unit")):
            continue
        if clocks is not None:
            try:
                if not local_day_finished(str(row["resolution_date"]), clocks.get(row["city_key"]),
                                          now or dt.datetime.now(dt.timezone.utc)):
                    continue
            except OutcomeError:
                pass  # A missing/invalid city clock must produce an explicit attempt error.
        outstanding.append(row)

    def oldest_first(row: dict[str, Any]) -> tuple[str, str, str]:
        key = (row["city_key"], row["resolution_date"])
        return ((last_attempts or {}).get(key, ""), row["resolution_date"], row["city_key"])

    recent = sorted([r for r in outstanding if r["resolution_date"] >= recent_from],
                    key=oldest_first)
    backlog = sorted([r for r in outstanding if r["resolution_date"] < recent_from],
                     key=oldest_first)
    # Untried/least recently attempted first within each pool, then older days.
    # Ongoing local days never consume a collection slot when clocks are supplied.
    return (recent + backlog)[:maximum]


def _last_attempts(days: int) -> dict[tuple[str, str], str]:
    """Least recently attempted first, so a bounded retry can reach later cities."""
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = rest_all("weather_resolution_attempts", [
        ("select", "city_key,for_date,captured_at"), ("for_date", f"gte.{cutoff}"),
    ], order="city_key.asc,for_date.asc,captured_at.asc", page_size=1000)
    return {(r["city_key"], str(r["for_date"])): str(r["captured_at"])
            for r in rows if r.get("captured_at")}


def _existing_evidence(days: int) -> dict[tuple[str, str], dict[str, Any]]:
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = rest_all(
        "weather_resolution_evidence",
        [("select", "evidence_id,city_key,for_date,observed_max_c,record_status,payload_sha256,captured_at"),
         ("for_date", f"gte.{cutoff}")],
        order="city_key.asc,for_date.asc,captured_at.asc,evidence_id.asc",
        page_size=500,
    )
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        latest[(row["city_key"], row["for_date"])] = row
    return latest


def run(days: int = 14, maximum: int = 250, dry_run: bool = False,
        cities: str = "", now: dt.datetime | None = None,
        budget_seconds: float | None = None) -> dict[str, int]:
    import common
    if budget_seconds is not None:
        if not math.isfinite(budget_seconds) or budget_seconds <= 0:
            raise ValueError("budget-seconds must be finite and positive")
        # Include setup, evidence writes and logging in the recovery allowance.
        with common.request_deadline(time.monotonic() + budget_seconds):
            return _run(days, maximum, dry_run, cities, now, bounded=True)
    return _run(days, maximum, dry_run, cities, now)


def _run(days, maximum, dry_run, cities, now, bounded=False):
    import common
    if days < 1 or maximum < 1:
        raise ValueError("days and max-city-days must be positive")
    now = now or dt.datetime.now(dt.timezone.utc)
    clocks = {r["city_key"]: r.get("timezone") for r in rest_all(
        "cities", [("select", "city_key,timezone")], order="city_key.asc")}
    city_keys = {c.strip() for c in cities.split(",") if c.strip()}
    if city_keys - clocks.keys():
        raise ValueError("Unknown recovery city: " + ", ".join(sorted(city_keys - clocks.keys())))
    # Evidence first: the finalized set decides which city-days are still
    # worth a slot, and the cap is applied to what remains rather than to the
    # whole window. See _load_targets.
    existing = _existing_evidence(days)
    finalized = {key for key, row in existing.items()
                 if row.get("record_status") == "verified"}
    # Verdicts already reached on rules that have not changed. Skipped rather
    # than re-derived; see _load_targets.
    unparseable = _unparseable(days)
    targets = _load_targets(days, maximum, finalized, unparseable, city_keys or None,
                            clocks, now, _last_attempts(days))
    run_id = os.environ.get("GITHUB_RUN_ID", "local-" + uuid.uuid4().hex)
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    session = requests.Session()
    counts = {
        "targets": len(targets), "backlog_done": len(finalized), "already_final": 0, "captured": 0,
        "not_final": 0, "unsupported": 0, "failures": 0,
        "needs_attention": 0, "dry_run": 0,
        # Visible in ingest_log, because a number that silently grows is how
        # the budget got eaten in the first place.
        "skipped_unparseable": len(unparseable),
        "deferred": 0,
    }

    for index, market in enumerate(targets):
        deadline = common._request_end.get()
        if bounded and deadline is not None and deadline - time.monotonic() < 15:
            counts["deferred"] = len(targets) - index
            break
        key = (market["city_key"], market["resolution_date"])
        prior = existing.get(key)
        # The contract freezes the first finalized publication. Never poll and
        # silently replace it with a later source revision.
        if prior and prior.get("record_status") == "verified":
            counts["already_final"] += 1
            continue

        spec: RuleSpec | None = None
        outcome: Outcome | None = None
        status = "captured"
        error: str | None = None
        failed_authority = failed_source_url = None
        try:
            spec = parse_rule_spec(market.get("rules_text"), market.get("unit"))
            if not local_day_finished(market["resolution_date"], clocks.get(market["city_key"]), now):
                raise OutcomeError("not_final", "The city's local day has not ended; final evidence was not requested")
            if bounded:
                with common.request_deadline(deadline - 10):
                    outcome = collect_with_fallback(session, spec, dt.date.fromisoformat(market["resolution_date"]), clocks[market["city_key"]], now)
            else:
                outcome = collect_with_fallback(session, spec, dt.date.fromisoformat(market["resolution_date"]), clocks[market["city_key"]], now)
            if prior:
                if prior.get("payload_sha256") == outcome.payload_sha256:
                    status = "unchanged"
                else:
                    status = "source_revised"
        except OutcomeError as exc:
            if exc.status == "deferred":
                counts["deferred"] = len(targets) - index
                break  # No evidence/verdict was reached; next run starts here.
            status = exc.status
            error = _safe_error(exc)
            failed_authority, failed_source_url = exc.source_authority, exc.source_url
        except Exception as exc:  # A novel parser bug is recorded, never promoted to evidence.
            status = "parse_error"
            error = _safe_error(exc)

        authority = failed_authority or (spec.source_authority if spec else "Unresolved saved market rules")
        station_id = spec.station_id if spec else None
        source_url = failed_source_url or (spec.source_url if spec else "saved-market-rules")
        if outcome:
            authority = outcome.detail.get("source_authority", authority)
            source_url = outcome.detail.get("source_url", source_url)
        if dry_run:
            counts["dry_run"] += 1
            print(f"DRY RUN {market['city_key']} {market['resolution_date']}: {status}")
            continue

        attempt_id = _id(
            run_id, run_attempt, market["market_id"], status,
            outcome.payload_sha256 if outcome else error or "no-detail",
        )
        attempt = {
            "attempt_id": attempt_id,
            "market_id": market["market_id"],
            "city_key": market["city_key"],
            "for_date": market["resolution_date"],
            "source_authority": authority,
            "station_id": station_id,
            "source_url": source_url,
            "outcome_status": status,
            "observed_max_c": str(outcome.observed_max_c) if outcome else None,
            "payload_sha256": outcome.payload_sha256 if outcome else None,
            "parser_version": PARSER_VERSION,
            "detail": {
                **(outcome.detail if outcome else {}),
                **({"error": error} if error else {}),
                # The verdict is about this text, so the text is identified.
                # Without it the next run cannot tell an answer it already has
                # from a question worth asking again.
                **({"rules_sha256": _rules_fingerprint(market.get("rules_text"), market.get("unit"))}
                   if status == "unsupported_source" and spec is None
                   and not rules_are_absent(market.get("rules_text")) else {}),
                # A publisher's coverage can return without the saved rules
                # changing. Unlike unparseable rules, retry this source scope.
                **({"source_scope_unavailable": True}
                   if status == "unsupported_source" and spec is not None else {}),
                # Said out loud, because "no rules_sha256" is otherwise just an
                # absence and absences do not get investigated.
                **({"rules_missing": True}
                   if status == "unsupported_source"
                   and rules_are_absent(market.get("rules_text")) else {}),
            },
        }
        upsert("weather_resolution_attempts", [attempt], "attempt_id")

        if status == "captured" and outcome:
            evidence_id = _id(
                market["city_key"], market["resolution_date"], PARSER_VERSION,
                outcome.payload_sha256,
            )
            evidence = {
                "evidence_id": evidence_id,
                "city_key": market["city_key"],
                "for_date": market["resolution_date"],
                "observed_max_c": str(outcome.observed_max_c),
                "unit": "C",
                "source_authority": authority,
                "station_id": station_id,
                "source_url": source_url,
                "record_status": "verified",
                "observed_at": outcome.observed_at,
                "parser_version": PARSER_VERSION,
                "payload_sha256": outcome.payload_sha256,
                "raw_payload": outcome.raw_payload,
                "supersedes_evidence_id": None,
            }
            upsert("weather_resolution_evidence", [evidence], "evidence_id")
            existing[key] = evidence
            counts["captured"] += 1
        elif status == "not_final":
            counts["not_final"] += 1
        elif status == "unsupported_source":
            # Known source-coverage gaps stay visible in health metrics, but
            # cannot make every scheduled run red forever. A named source is
            # never substituted or guessed merely to turn the check green.
            counts["unsupported"] += 1
            counts["needs_attention"] += 1
        elif status not in {"unchanged"}:
            counts["failures"] += 1
            counts["needs_attention"] += 1
        print(f"{market['city_key']} {market['resolution_date']}: {status}")

    overall = ("attention" if counts["needs_attention"] else
               "partial" if counts["deferred"] else "ok")
    if not dry_run:
        log_run("weather_outcomes", overall, counts["captured"], counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--max-city-days", type=int, default=250)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cities", default="", help="Comma-separated city keys for bounded manual recovery")
    parser.add_argument("--budget-seconds", type=float, default=None)
    args = parser.parse_args()
    counts = run(days=args.days, maximum=args.max_city_days, dry_run=args.dry_run,
                 cities=args.cities, budget_seconds=args.budget_seconds)
    print(json.dumps(counts, sort_keys=True))
    # Unsupported authorities are an explicit coverage limitation. Genuine
    # source/parser/revision failures still fail the workflow loudly.
    if counts["failures"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
