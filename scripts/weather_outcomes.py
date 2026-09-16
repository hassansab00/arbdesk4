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
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from common import log_run, rest_all, upsert


PARSER_VERSION = "weather-outcomes-v1"

# How far back "recent" reaches. City-days inside this window are offered a
# slot BEFORE the backlog, so yesterday is always attempted no matter how big
# or how stuck the backlog behind it is. Three days, because a contract's
# authority can take two days to publish a final figure and anything that has
# not resolved by then is backlog, not news.
RECENT_DAYS = 3
USER_AGENT = "ArbDesk4/2.0 (+https://github.com/hassansab00/arbdesk4)"
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


@dataclass(frozen=True)
class RuleSpec:
    adapter: str
    source_authority: str
    station_id: str
    source_url: str
    unit: str
    hourly_only: bool


@dataclass(frozen=True)
class Outcome:
    observed_max_c: Decimal
    observed_at: str | None
    payload_sha256: str
    raw_payload: dict[str, Any]
    detail: dict[str, Any]


def parse_rule_spec(rules_text: str | None, market_unit: str | None = None) -> RuleSpec:
    rules = (rules_text or "").strip()
    lower = rules.lower()
    if not rules:
        raise OutcomeError("unsupported_source", "Market has no saved resolution rules")

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
        )

    wu = WU_URL_RE.search(rules)
    if wu:
        raise OutcomeError(
            "unsupported_source",
            "Weather Underground is the named primary source; no stable, documented machine interface is configured",
        )

    raise OutcomeError("unsupported_source", "No supported authoritative source found in saved rules")


def _get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = session.get(url, headers=headers, timeout=45, **kwargs)
            if response.status_code < 400:
                return response
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                response.raise_for_status()
            last_error = requests.HTTPError(
                f"HTTP {response.status_code} from {url}", response=response
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
        if attempt < 3:
            time.sleep(2 ** attempt)
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


def fetch_wrh_outcome(
    session: requests.Session, spec: RuleSpec, for_date: dt.date,
) -> Outcome:
    key_response = _get(session, WRH_KEY_URL)
    match = re.search(
        r"mesoToken\s*=\s*['\"]([^'\"]+)['\"]", key_response.text, re.IGNORECASE
    )
    if not match:
        raise OutcomeError("parse_error", "weather.gov did not publish its WRH data token")
    token = match.group(1)
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


def _id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()


def _safe_error(exc: Exception) -> str:
    text = re.sub(r"token=[^&\s]+", "token=[redacted]", str(exc), flags=re.I)
    return text[:500]


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
            ("select", "market_id,city_key,resolution_date,unit,rules_text,last_seen_at"),
            ("resolution_date", f"gte.{cutoff}"),
            ("resolution_date", f"lte.{today}"),
        ],
        order="resolution_date.asc,market_id.asc",
        page_size=500,
    )
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["city_key"], row["resolution_date"])
        if key not in latest or str(row.get("last_seen_at") or "") > str(latest[key].get("last_seen_at") or ""):
            latest[key] = row
    done = finalized or set()
    stuck = unparseable or {}
    outstanding = []
    for row in latest.values():
        key = (row["city_key"], row["resolution_date"])
        if key in done:
            continue
        if stuck.get(key) == _rules_fingerprint(row.get("rules_text"), row.get("unit")):
            continue
        outstanding.append(row)

    def oldest_first(row: dict[str, Any]) -> tuple[str, str]:
        return (row["resolution_date"], row["city_key"])

    recent = sorted([r for r in outstanding if r["resolution_date"] >= recent_from],
                    key=oldest_first)
    backlog = sorted([r for r in outstanding if r["resolution_date"] < recent_from],
                     key=oldest_first)
    # Oldest-first WITHIN the recent window as well: the 15th is likelier to
    # have a final published figure than the 16th, which is still happening.
    return (recent + backlog)[:maximum]


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


def run(days: int = 14, maximum: int = 250, dry_run: bool = False) -> dict[str, int]:
    if days < 1 or maximum < 1:
        raise ValueError("days and max-city-days must be positive")
    # Evidence first: the finalized set decides which city-days are still
    # worth a slot, and the cap is applied to what remains rather than to the
    # whole window. See _load_targets.
    existing = _existing_evidence(days)
    finalized = {key for key, row in existing.items()
                 if row.get("record_status") == "verified"}
    # Verdicts already reached on rules that have not changed. Skipped rather
    # than re-derived; see _load_targets.
    unparseable = _unparseable(days)
    targets = _load_targets(days, maximum, finalized, unparseable)
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
    }

    for market in targets:
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
        try:
            spec = parse_rule_spec(market.get("rules_text"), market.get("unit"))
            outcome = collect_one(session, spec, dt.date.fromisoformat(market["resolution_date"]))
            if prior:
                if prior.get("payload_sha256") == outcome.payload_sha256:
                    status = "unchanged"
                else:
                    status = "source_revised"
        except OutcomeError as exc:
            status = exc.status
            error = _safe_error(exc)
        except Exception as exc:  # A novel parser bug is recorded, never promoted to evidence.
            status = "parse_error"
            error = _safe_error(exc)

        authority = spec.source_authority if spec else "Unresolved saved market rules"
        station_id = spec.station_id if spec else None
        source_url = spec.source_url if spec else "saved-market-rules"
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
                   if status == "unsupported_source" else {}),
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

    overall = "ok" if counts["needs_attention"] == 0 else "attention"
    if not dry_run:
        log_run("weather_outcomes", overall, counts["captured"], counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--max-city-days", type=int, default=250)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    counts = run(days=args.days, maximum=args.max_city_days, dry_run=args.dry_run)
    print(json.dumps(counts, sort_keys=True))
    # Unsupported authorities are an explicit coverage limitation. Genuine
    # source/parser/revision failures still fail the workflow loudly.
    if counts["failures"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
