import base64
import datetime as dt
import gzip
import json
import sys
from decimal import Decimal

import pytest

import weather_outcomes as outcomes


def _wrh_payload(*, include_following=True):
    times = [
        "2026-09-12T00:53:00-0700",
        "2026-09-12T01:20:00-0700",
        "2026-09-12T02:20:00-0700",
    ]
    temps = [68.4, 69.6, 99.0]
    pressure = [1012.3, None, None]
    metars = ["KSFO 120753Z", "KSFO 120820Z SPECI", None]
    if include_following:
        times.append("2026-09-13T00:53:00-0700")
        temps.append(65.0)
        pressure.append(1013.0)
        metars.append("KSFO 130753Z")
    return {
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "KSFO",
            "NAME": "San Francisco International Airport",
            "TIMEZONE": "America/Los_Angeles",
            "SHORTNAME": "ASOS/AWOS",
            "OBSERVATIONS": {
                "date_time": times,
                "air_temp_set_1": temps,
                "sea_level_pressure_set_1": pressure,
                "metar_set_1": metars,
            },
        }],
    }


def test_rules_choose_exact_saved_authority_and_unit():
    rules = (
        "highest temperature recorded by NOAA in degrees Fahrenheit. "
        "available here: https://www.weather.gov/wrh/timeseries?site=ksfo "
        "This market will resolve off of the Hourly Data provided using the Show Hourly Data button."
    )
    spec = outcomes.parse_rule_spec(rules, "C")
    assert spec.adapter == "weather_gov_wrh"
    assert spec.station_id == "KSFO"
    assert spec.unit == "F"  # saved rules outrank a stale market column
    assert spec.hourly_only is True


def test_weather_underground_is_visible_not_guessed():
    with pytest.raises(outcomes.OutcomeError) as error:
        outcomes.parse_rule_spec(
            "degrees Celsius; https://www.wunderground.com/history/daily/cn/jinan/ZSJN",
            "C",
        )
    assert error.value.status == "unsupported_source"


def test_known_unsupported_authority_does_not_make_schedule_red(monkeypatch):
    monkeypatch.setattr(
        outcomes, "run",
        lambda **_: {"failures": 0, "needs_attention": 1, "unsupported": 1},
    )
    monkeypatch.setattr(sys, "argv", ["weather_outcomes.py"])
    outcomes.main()


def test_real_collection_failure_still_fails_schedule(monkeypatch):
    monkeypatch.setattr(
        outcomes, "run",
        lambda **_: {"failures": 1, "needs_attention": 1, "unsupported": 0},
    )
    monkeypatch.setattr(sys, "argv", ["weather_outcomes.py"])
    with pytest.raises(SystemExit) as error:
        outcomes.main()
    assert error.value.code == 2


def test_wrh_parser_reproduces_hourly_filter_rounding_and_local_finality():
    spec = outcomes.RuleSpec(
        "weather_gov_wrh", "NOAA weather.gov WRH Time Series", "KSFO",
        "https://www.weather.gov/wrh/timeseries?site=ksfo", "F", True,
    )
    payload = _wrh_payload()
    raw = json.dumps(payload).encode()
    result = outcomes.parse_wrh_payload(payload, raw, spec, dt.date(2026, 9, 12))

    # The 99F non-hourly row is excluded. The 69.6F SPECI row is displayed as
    # 70F by weather.gov, then stored canonically in Celsius.
    assert result.observed_max_c == Decimal("21.111111")
    assert result.observed_at == "2026-09-12T01:20:00-07:00"
    assert result.detail["target_readings"] == 3
    assert result.detail["eligible_readings"] == 2
    packed = gzip.decompress(base64.b64decode(result.raw_payload["data"]))
    assert json.loads(packed)["first_following_datapoint"].startswith("2026-09-13")
    assert outcomes._sha(packed) == result.payload_sha256


def test_wrh_parser_refuses_running_daily_maximum():
    spec = outcomes.RuleSpec(
        "weather_gov_wrh", "NOAA weather.gov WRH Time Series", "KSFO",
        "https://www.weather.gov/wrh/timeseries?site=ksfo", "F", True,
    )
    payload = _wrh_payload(include_following=False)
    with pytest.raises(outcomes.OutcomeError) as error:
        outcomes.parse_wrh_payload(
            payload, json.dumps(payload).encode(), spec, dt.date(2026, 9, 12)
        )
    assert error.value.status == "not_final"


def test_hko_uses_absolute_daily_max_and_rejects_incomplete_rows():
    spec = outcomes.parse_rule_spec(
        "Hong Kong Observatory degrees Celsius, Absolute Daily Max (deg. C) "
        "once finalized in the Daily Extract: https://www.weather.gov.hk/en/cis/climat.htm",
        "C",
    )
    payload = {"stn": {"data": [{"month": 9, "dayData": [
        ["12", "1005.1", "31.7", "28.4", "26.5", "23.5", "75", "77", "0.0"]
    ]}]}}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    result = outcomes.parse_hko_payload(payload, raw, spec, dt.date(2026, 9, 12))
    assert result.observed_max_c == Decimal("31.7")
    assert result.payload_sha256 == outcomes._sha(raw)

    payload["stn"]["data"][0]["dayData"][0][2] = "31.7#"
    with pytest.raises(outcomes.OutcomeError) as error:
        outcomes.parse_hko_payload(payload, raw, spec, dt.date(2026, 9, 12))
    assert error.value.status == "not_final"


def test_phase2b_is_cost_neutral_private_and_exportable():
    workflow = open(".github/workflows/pipeline_daily.yml", encoding="utf-8").read()
    migration = open(
        "supabase/migrations/20260913230000_phase2b_weather_resolution_collection.sql",
        encoding="utf-8",
    ).read()
    export = open("web/lib/proprietaryExport.ts", encoding="utf-8").read()

    assert "python scripts/weather_outcomes.py" in workflow
    assert workflow.count("cron:") == 1  # no new paid schedule
    assert "enable row level security" in migration.lower()
    assert "revoke all on public.weather_resolution_attempts" in migration.lower()
    assert "before update or delete" in migration.lower()
    assert "before truncate" in migration.lower()
    assert "weather_resolution_evidence" in export
    assert "weather_resolution_attempts" in export


# --------------------------------------------------------------------------
# BACKLOG TRAVERSAL
#
# The cap used to be applied to every city-day in the window, finished or
# not, after sorting oldest-first. Every already-verified day consumed a
# slot, so once the oldest `maximum` were done every later run reloaded that
# same set, skipped all of it as already_final, and stopped. Widening --days
# reached no further back: it re-read finished history and never arrived at
# the part it had not done.
#
# Measured symptom: 49 cities sat at ~7 days of evidence each (average 6.9,
# max 7) while the window nominally covered fourteen, and no city had the
# 20+ days that skill measurement needs to stamp a row verified - so every
# band stayed blocked from trading on `no_verified_skill`.
# --------------------------------------------------------------------------
@pytest.fixture
def markets(monkeypatch):
    """Scripted `markets` rows, one per city-day."""
    def go(rows):
        monkeypatch.setattr(outcomes, "rest_all",
                            lambda path, params=None, **kw: list(rows))
        return rows
    return go


def _market(city, date, market_id=None):
    return {"market_id": market_id or f"{city}-{date}", "city_key": city,
            "resolution_date": date, "unit": "C", "rules_text": "",
            "last_seen_at": "2026-09-14T00:00:00Z"}


def test_finalized_days_do_not_consume_the_budget(markets):
    markets([_market("london", f"2026-09-{d:02d}") for d in range(1, 11)])
    done = {("london", f"2026-09-{d:02d}") for d in range(1, 9)}   # 8 finished

    picked = outcomes._load_targets(days=30, maximum=3, finalized=done)

    assert [r["resolution_date"] for r in picked] == ["2026-09-09", "2026-09-10"], \
        "the budget must be spent on days that still need collecting"


def test_without_the_fix_the_same_input_would_return_only_finished_days(markets):
    """The regression, stated as the contract it broke: with no finalized set
    the oldest days win the cap - which is correct only because nothing is
    done yet."""
    markets([_market("london", f"2026-09-{d:02d}") for d in range(1, 11)])
    picked = outcomes._load_targets(days=30, maximum=3, finalized=set())
    assert [r["resolution_date"] for r in picked] == \
        ["2026-09-01", "2026-09-02", "2026-09-03"]


def test_an_unverified_prior_is_still_a_target(markets):
    """not_final, a source revision and a parse error all leave a row behind
    that is NOT frozen. Those days must still be retried - the skip rule is
    record_status == 'verified', nothing looser."""
    markets([_market("london", "2026-09-01"), _market("london", "2026-09-02")])
    picked = outcomes._load_targets(days=30, maximum=10, finalized=set())
    assert len(picked) == 2


def test_the_walk_is_oldest_first_so_the_backlog_fills_backwards(markets):
    markets([_market("b", "2026-09-05"), _market("a", "2026-09-01"),
             _market("c", "2026-09-03")])
    picked = outcomes._load_targets(days=30, maximum=10, finalized=set())
    assert [r["resolution_date"] for r in picked] == \
        ["2026-09-01", "2026-09-03", "2026-09-05"]


def test_one_row_per_city_day_newest_market_wins(markets):
    """Two market rows for one city-day (a re-listing) is one target."""
    a = _market("london", "2026-09-01", "old")
    b = _market("london", "2026-09-01", "new")
    b["last_seen_at"] = "2026-09-14T12:00:00Z"
    markets([a, b])
    picked = outcomes._load_targets(days=30, maximum=10, finalized=set())
    assert len(picked) == 1 and picked[0]["market_id"] == "new"
