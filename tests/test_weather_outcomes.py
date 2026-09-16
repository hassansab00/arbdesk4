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


# --------------------------------------------------------------------------
# THE BACKLOG COULD NOT TRAVERSE AGAIN - and this time it stopped the desk.
#
# Skipping finished days left the whole budget to the outstanding ones. On
# 2026-09-16 the 14-day window held 714 city-days, 408 of them outstanding,
# and 259 of those were markets whose saved rules name a source with no
# machine interface. unsupported_source is a verdict on text, not a transient
# failure: the same rules through the same parser fail identically every run.
#
# 259 against a cap of 250, taken oldest first, is the entire budget. Every
# run re-derived verdicts it already held, walked as far as 2026-09-14 and
# stopped. The 15th and the 16th were never attempted at all - max(for_date)
# across every attempt in 72 hours was 2026-09-14, and the newest verified
# evidence in the database was frozen at 2026-09-14 16:54 while the collector
# reported "ok" on every run.
#
# Downstream, scripts/databank.py banks a forecast outcome only where
# verified evidence exists, so fact_forecast_outcome went 33 hours without a
# row and fact_signal_outcome 81, and "what the desk expects" stopped being
# scored against what happened.
# --------------------------------------------------------------------------
def _stuck(city, date, rules="", unit="C"):
    """The (city, date) -> fingerprint entry a prior unsupported verdict leaves."""
    return {(city, date): outcomes._rules_fingerprint(rules, unit)}


def test_yesterday_is_attempted_even_when_the_backlog_exceeds_the_budget(markets):
    """The guarantee, independent of WHY the backlog is stuck.

    Three hundred backlog city-days and a budget of ten: without the recent
    window the ten oldest win and the newest day is never reached, which is
    exactly the outage. It is worth having as a rule of its own because this
    was the second distinct cause of it, not the first.
    """
    today = dt.date.today()
    rows = [_market("c%03d" % i, (today - dt.timedelta(days=9)).isoformat())
            for i in range(300)]
    rows.append(_market("london", (today - dt.timedelta(days=1)).isoformat()))
    markets(rows)

    picked = outcomes._load_targets(days=30, maximum=10, finalized=set())

    assert (today - dt.timedelta(days=1)).isoformat() in \
        [r["resolution_date"] for r in picked], "yesterday must always get a slot"


def test_the_recent_window_takes_precedence_but_does_not_take_everything(markets):
    """Backlog traversal survives: leftover slots still walk backwards."""
    today = dt.date.today()
    recent = (today - dt.timedelta(days=1)).isoformat()
    old = (today - dt.timedelta(days=10)).isoformat()
    markets([_market("a", old), _market("b", old), _market("london", recent)])

    picked = [r["resolution_date"] for r in
              outcomes._load_targets(days=30, maximum=3, finalized=set())]

    assert picked[0] == recent, "the recent window is served first"
    assert picked.count(old) == 2, "the rest of the budget still walks the backlog"


def test_within_the_recent_window_the_older_day_goes_first(markets):
    """The 15th has a published final figure; the 16th is still happening."""
    today = dt.date.today()
    y, d2 = (today - dt.timedelta(days=1)).isoformat(), (today - dt.timedelta(days=2)).isoformat()
    markets([_market("london", y), _market("london", d2)])
    picked = [r["resolution_date"] for r in
              outcomes._load_targets(days=30, maximum=10, finalized=set())]
    assert picked == [d2, y]


def test_a_known_unparseable_city_day_does_not_consume_a_slot(markets):
    """The verdict is kept, not re-derived. This is the 259 that ate the 250."""
    today = dt.date.today()
    old = (today - dt.timedelta(days=10)).isoformat()
    older = (today - dt.timedelta(days=11)).isoformat()
    markets([_market("stuck", older), _market("live", old)])

    picked = outcomes._load_targets(days=30, maximum=1, finalized=set(),
                                    unparseable=_stuck("stuck", older))

    assert [r["city_key"] for r in picked] == ["live"], \
        "a slot must not be spent re-deriving a verdict that cannot change"


def test_changed_rules_make_an_unparseable_city_day_a_target_again(markets):
    """Self-healing: the venue republishing rules is what un-blocks it.

    The fingerprint covers exactly what parse_rule_spec reads, so a market
    re-listed with a source we do support no longer matches and comes back
    into the list without anyone clearing anything.
    """
    today = dt.date.today()
    date = (today - dt.timedelta(days=10)).isoformat()
    row = _market("stuck", date)
    row["rules_text"] = "resolves to the NOAA weather.gov WRH time series, degrees celsius"
    markets([row])

    picked = outcomes._load_targets(days=30, maximum=10, finalized=set(),
                                    unparseable=_stuck("stuck", date, rules=""))

    assert len(picked) == 1, "a different fingerprint is a different question"


def test_an_attempt_with_no_recorded_fingerprint_is_still_a_target(markets):
    """Attempts written before the hash existed must not bury a city-day."""
    today = dt.date.today()
    date = (today - dt.timedelta(days=10)).isoformat()
    markets([_market("stuck", date)])
    picked = outcomes._load_targets(days=30, maximum=10, finalized=set(), unparseable={})
    assert len(picked) == 1


def test_the_fingerprint_reads_the_unit_as_well_as_the_text():
    """parse_rule_spec reads market_unit too, so the same text with a unit
    filled in is a different input and must hash differently."""
    a = outcomes._rules_fingerprint("resolves per the station", "C")
    b = outcomes._rules_fingerprint("resolves per the station", "F")
    c = outcomes._rules_fingerprint("resolves per the station", None)
    assert len({a, b, c}) == 3
    assert outcomes._rules_fingerprint(" resolves per the station ", "C") == a, \
        "surrounding whitespace is stripped by the parser, so it is stripped here"


def test_unparseable_reads_only_the_newest_attempt_per_city_day(monkeypatch):
    """A city-day that failed on Monday and captured on Tuesday is not stuck."""
    rows = [
        {"city_key": "london", "for_date": "2026-09-01", "outcome_status": "unsupported_source",
         "detail": {"rules_sha256": "abc"}, "captured_at": "2026-09-01T00:00:00Z"},
        {"city_key": "london", "for_date": "2026-09-01", "outcome_status": "captured",
         "detail": {}, "captured_at": "2026-09-02T00:00:00Z"},
        {"city_key": "paris", "for_date": "2026-09-01", "outcome_status": "unsupported_source",
         "detail": {"rules_sha256": "def"}, "captured_at": "2026-09-02T00:00:00Z"},
    ]
    monkeypatch.setattr(outcomes, "rest_all", lambda path, params=None, **kw: list(rows))
    assert outcomes._unparseable(30) == {("paris", "2026-09-01"): "def"}
