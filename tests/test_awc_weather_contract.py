"""Contract examples and source finality, independent of WRH's display rules."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

import metar_contract as metar
import weather_outcomes as outcomes

DAY = dt.date(2026, 10, 23)
URL = "https://aviationweather.gov/api/data/metar?ids=KLGA&format=raw&date=2026-10-24T04:00:00Z&hours=27"
RULES = ("The qualifying value is in tenths of a degree Celsius. "
         "A report whose line begins with SPECI is special. The remark has exactly eight digits. "
         "Each market in this event states its range in whole degrees Fahrenheit. "
         "Use the station's local time and the first METAR observation for the next date. "
         "The Aviation Weather Center has published no routine observation reporting a temperature; "
         "the Weather Underground Daily Observations table will be used as the resolution source. " + URL)
FOLLOWING = b"METAR KLGA 240451Z 01005KT 10SM CLR 10/02 A3000 RMK T01000020"
NOW = dt.datetime(2026, 10, 24, 5, 10, tzinfo=dt.timezone.utc)


def spec():
    return outcomes.parse_rule_spec(RULES, "C")


def test_saved_range_unit_overrules_celsius_parser_instructions():
    s = spec()
    assert (s.adapter, s.unit, s.station_id, s.wu_fallback) == ("aviation_weather_metar", "F", "KLGA", True)
    assert outcomes.parse_rule_spec(RULES.replace("range in whole degrees Fahrenheit", "range in whole degrees Celsius"), "F").unit == "C"


@pytest.mark.parametrize("text,value", [
    ("METAR KLGA 231551Z 14/13 RMK T01390133", "13.9"),
    ("KLGA 231517Z M02/M02 RMK T10171022", "-1.7"),
    ("METAR KLGA 231517Z 18/12", "18"),
    ("KLGA 231517Z M11/M17", "-11"),
    ("METAR KLGA 231517Z 18/12 RMK T0999", "18"),
    ("METAR KLGA 231517Z 18/12 RMK T099901331", "18"),
    ("SPECI KLGA 231517Z 40/20 RMK T04000200", None),
    ("KLGA 231517Z ///// RMK AO2", None),
])
def test_report_eligibility_and_exact_temperature_groups(text, value):
    assert metar.temperature_c(text) == (None if value is None else Decimal(value))


@pytest.mark.parametrize("celsius,unit,expected", [
    ("18.3", "F", 65), ("17.8", "F", 64), ("19", "F", 66),
    ("-22.5", "F", -8), ("16.5", "C", 17), ("-8.5", "C", -8),
])
def test_contract_rounds_exact_halves_toward_higher_integer(celsius, unit, expected):
    assert metar.rounded_unit(Decimal(celsius), unit) == expected


def test_raw_source_rejects_wrong_station_and_conflicting_corrections():
    at = dt.datetime(2026, 10, 24, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="wrong station"):
        metar.raw_reports(b"METAR KJFK 232351Z 20/10", "KLGA", at)
    with pytest.raises(ValueError, match="conflicting"):
        metar.raw_reports(b"METAR KLGA 232351Z 20/10\nMETAR KLGA 232351Z COR 21/10", "KLGA", at)
    with pytest.raises(ValueError, match="unrecognized"):
        metar.raw_reports(b"<html>upstream error</html>", "KLGA", at)


def test_month_and_year_boundaries_are_resolved_from_dated_source():
    rows = metar.raw_reports(b"METAR KLGA 312351Z 10/05", "KLGA",
                             dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc))
    assert rows[0]["when"].isoformat() == "2026-12-31T23:51:00+00:00"


def collect(monkeypatch, target, following=FOLLOWING, now=NOW):
    calls = []
    def get(session, url, **kwargs):
        calls.append(url)
        return SimpleNamespace(content=target if url == URL else following)
    monkeypatch.setattr(outcomes, "_get", get)
    result = outcomes.collect_with_fallback(object(), spec(), DAY, "America/New_York", now)
    return result, calls


def test_final_read_uses_routine_max_on_station_local_date_not_special_or_utc_day(monkeypatch):
    raw = b"\n".join([
        b"METAR KLGA 230351Z 40/20 RMK T04000200",  # previous local date
        b"METAR KLGA 231551Z 18/12 RMK T01830120",
        b"KLGA 231517Z 19/12 RMK T01940120",       # routine, off-hour, unlabeled
        b"SPECI KLGA 231530Z 45/20 RMK T04500200",
        b"METAR KLGA 240351Z 20/12 RMK T01990120", # still target local date
    ])
    result, calls = collect(monkeypatch, raw)
    assert result.detail["qualifying_max_c"] == "19.9"
    assert result.detail["display_max"] == 68
    assert result.detail["eligible_readings"] == 3
    assert result.observed_max_c == Decimal("20.000000")
    assert result.observed_at == "2026-10-24T03:51:00+00:00"
    assert calls[-1] == URL  # target read follows proof that next date published
    assert result.detail["finality"] == "following_routine"


def test_special_next_day_report_does_not_finalize_the_day(monkeypatch):
    with pytest.raises(outcomes.OutcomeError) as e:
        collect(monkeypatch, b"METAR KLGA 231551Z 18/12", following=FOLLOWING.replace(b"METAR", b"SPECI"))
    assert e.value.status == "not_final"


def test_successful_empty_primary_waits_until_deadline(monkeypatch):
    with pytest.raises(outcomes.OutcomeError) as e:
        collect(monkeypatch, b"")
    assert e.value.status == "not_final"


def test_deadline_can_finalize_available_reading_without_next_day_routine(monkeypatch):
    result, _ = collect(monkeypatch, b"METAR KLGA 231551Z 18/12", following=b"",
                         now=outcomes.wu_fallback_cutoff(DAY))
    assert result.detail["display_max"] == 64
    assert result.detail["finality"] == "contract_deadline"


def test_provider_or_parser_failure_never_claims_no_primary_publication(monkeypatch):
    calls = []
    def broken(session, url, **kwargs):
        calls.append(url)
        raise outcomes.OutcomeError("source_unavailable", "upstream unavailable")
    monkeypatch.setattr(outcomes, "_get", broken)
    with pytest.raises(outcomes.OutcomeError, match="upstream unavailable"):
        outcomes.collect_awc(object(), spec(), DAY, "America/New_York", outcomes.wu_fallback_cutoff(DAY))
    assert len(calls) == 1 and "wunderground" not in calls[0]


def test_empty_primary_uses_only_named_fallback_after_deadline(monkeypatch):
    calls = []
    def get(session, url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(content=b"wu" if "wunderground" in url else b"")
    monkeypatch.setattr(outcomes, "_get", get)
    monkeypatch.setattr(outcomes, "parse_wu_daily", lambda *a: outcomes.Outcome(
        Decimal("20"), None, "proof", {}, {"source_authority": "Weather Underground Daily Observations"}))
    result = outcomes.collect_awc(object(), spec(), DAY, "America/New_York", outcomes.wu_fallback_cutoff(DAY))
    assert result.detail["fallback_reason"] == "awc_published_no_qualifying_temperature_by_deadline"
    assert calls[-1][1]["headers"] == {"Cookie": "wu_units=e"}


def test_wrong_source_date_refuses_before_any_request(monkeypatch):
    monkeypatch.setattr(outcomes, "_get", lambda *a, **k: pytest.fail("must not fetch mismatched source"))
    with pytest.raises(outcomes.OutcomeError, match="target local day"):
        outcomes.collect_awc(object(), spec(), DAY, "Europe/Amsterdam", NOW)
