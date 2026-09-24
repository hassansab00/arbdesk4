"""The venue reads the hourly column. We were feeding it five-minute data.

Every market's rules_text names its own resolution source, exactly:

    "the temperature range that contains the highest temperature recorded by
     NOAA at the London City Airport Station in degrees Celsius on 22 Sep '26
     ... specifically the highest reading under the "Temp" column for all
     times on this day, available here:
     https://www.weather.gov/wrh/timeseries?site=eglc"

Extracting that site id from all 48 active cities and comparing it to
cities.icao: 48 of 48 match. We have never had the wrong station. What we had
was the wrong sample of it.

IEM returns every observation a station files - about 125 a day for the US
ASOS sites, 24 for everyone else - and the page the rules name prints the
routine hourly report. So the roster split two opposite ways against the
venue's own declared winners:

    feed             ladders   names the winning band
    hourly (24/day)      507   90.0%   and when it misses we read BELOW
    5-minute (125/day)    99   76.8%   and when it misses we read ABOVE

US cities: 16 misses, all 16 of them us reading high. A five-minute spike the
"Temp" column never shows is still a reading to max(). Everyone else: 30
misses, 27 of them us reading low, because with 24 samples the peak falls
between reports - a different problem, not fixed here and not claimed to be.

Taking the maximum over the routine report only moves the five-minute cities
76.8% -> 90.9% and costs the hourly ones 0.7 points. Roster-wide 88.5% ->
90.6%.

AND THE LARGER HALF WAS COVERAGE. The BANKED record agreed 52.1% while the
authoritative reader that fed it scored 76.2%: most of that gap was rows with
no observed maximum at all, because scripts/weather_outcomes.py covers about
four city-days in five and nothing was written for the rest. The station feed
agrees 90.6% on its own, so it beats a null - as long as the row says which
thermometer answered, which is fact_band_outcome.obs_source.

WHAT WAS RULED OUT FIRST, so nobody re-runs it: the station (48/48 match), the
day boundary (local 75.0% vs UTC 70.8%), and rounding to the displayed whole
degree (75.0% -> 73.2%, worse, whether rounded per reading or after the max).

THE ONE ASYMMETRY THAT IS DELIBERATE. fact_forecast_outcome does NOT get the
fallback. error_c there is what the model fitter trains against, and a target
measured by two different thermometers is not a target. fact_band_outcome is
different: settled_yes comes from the venue and owes nothing to either
thermometer, and observed_max_c beside it is context.
"""

import pathlib
import re

import pytest

import databank


ROOT = pathlib.Path(__file__).resolve().parents[1]
AGREE = ROOT / "sql/ad4_82_settlement_agreement.sql"
MIG_MIN = (ROOT / "supabase/migrations"
                / "20260922180000_the_venue_reads_the_hourly_column.sql")
MIG_SRC = (ROOT / "supabase/migrations"
                / "20260922183000_a_banked_maximum_says_which_thermometer.sql")


# --------------------------------------------------------------------------
# 1. The filing minute.
# --------------------------------------------------------------------------

def test_the_migration_adds_the_filing_minute():
    sql = MIG_MIN.read_text()
    assert "add column if not exists report_minute" in sql


def test_the_filing_minute_is_read_from_the_station_not_assumed():
    sql = MIG_MIN.read_text()
    assert "mode() within group" in sql, (
        "a station files at ITS minute - :50, :52, :55 - and assuming one is "
        "how the filter silently drops a whole city's readings"
    )
    assert "having count(*) >= 48" in sql, (
        "a modal minute from a handful of rows is a guess wearing a number"
    )


def test_the_migration_survives_a_database_without_observations():
    # The PGlite harness applies every migration against a fixture whose
    # weather_observations has no temp_c and no station. This one must not
    # assume either.
    sql = MIG_MIN.read_text()
    assert "to_regclass('public.weather_observations') is null" in sql
    for col in ("temp_c", "temp_f", "o.station"):
        assert col not in sql, f"the migration reads {col}, which the fixture does not have"


def test_the_migration_changes_no_reading():
    sql = MIG_MIN.read_text().lower()
    for verb in ("delete from", "drop ", "truncate", "update public.weather_observations"):
        assert verb not in sql, f"{verb} has no business here - every reading stays"


def test_preflight_knows_about_the_column():
    reg = (ROOT / "sql/ad4_00_preflight.sql").read_text()
    assert "('cities','report_minute','smallint')" in reg, (
        "a column the registry does not know about is a column a half-migrated "
        "database will not have"
    )


# --------------------------------------------------------------------------
# 2. The view, and what it compares.
# --------------------------------------------------------------------------

def test_the_day_max_publishes_both_units():
    sql = AGREE.read_text()
    for col in ("as max_c", "as max_f", "as max_c_hourly", "as max_f_hourly", "as n_hourly"):
        assert col in sql, f"v_station_day_max is missing {col}"


def test_the_hourly_columns_read_the_primary_source_and_no_minute_window():
    """Plan v2 P2.2 replaced the filing-minute window.

    The window kept any row within four minutes of report_minute WHATEVER ITS
    SOURCE, so the NWS five-minute feed leaked in (Dallas 13 Sep: KDAL 100.4F
    at :55 against a routine 99.0F, winner 98-99F), and it dropped reports the
    station did file off its routine minute (London's :20 half-hourlies, NYC's
    specials). The hourly columns now read the primary source, whole. The
    behaviour is exercised on those three cases in
    tests/database/settlement-agreement.cjs; this pins the shape.
    """
    view = AGREE.read_text()
    view = view[view.index("create or replace view v_station_day_max"):]
    view = view[:view.index("comment on view v_station_day_max")]
    assert view.count("o.source = obs_primary_source()") == 4, (
        "station, max_c_hourly, max_f_hourly and n_hourly must all read the primary source")
    code = [line for line in view.splitlines() if not line.strip().startswith("--")]
    assert not any("report_minute" in line for line in code), (
        "a minute window is back in v_station_day_max")


def test_the_agreement_view_compares_against_the_hourly_report():
    sql = AGREE.read_text()
    view = sql[sql.index("create or replace view v_settlement_agreement"):]
    assert "venue_round(s.max_c_hourly, w.unit)" in view, (
        "the venue settles on the station's own reports, read as a whole degree "
        "in its own unit; comparing anything else measures a different question"
    )
    assert "s.max_f else s.max_c end as ours" not in view


def test_the_agreement_view_compares_in_the_markets_own_unit():
    """Rounded in the market's unit, from the stored Celsius.

    The earlier worry was the round trip: Austin's 100.4F is exactly 38.0C,
    and comparing an unrounded Celsius-converted value against F bands cost
    2.9 points. Rounded to a whole degree first, the two agree: measured on
    the live database on 23 Sep over 638 settled ladders, rounding from the
    stored Celsius and rounding the native Fahrenheit gave the same whole
    degree on every one of them.
    """
    view = AGREE.read_text()
    view = view[view.index("create or replace view v_settlement_agreement"):]
    assert "venue_round(s.max_c_hourly, w.unit)" in view
    assert "v_canonical_markets" in view, "the unit must be the canonical one (P2.5)"
    assert "band_local_value" not in view


def test_a_city_with_too_little_history_gets_no_trust_score():
    view = AGREE.read_text()
    view = view[view.index("create or replace view v_settlement_agreement"):]
    assert "count(ours) < 10" in view
    assert "when count(ours) >= 10" in view, (
        "observation_trust from three ladders is not a trust score"
    )


def test_the_verdicts_are_ordered_and_reachable():
    v = AGREE.read_text()
    v = v[v.index("create or replace view v_settlement_agreement"):]
    i_none = v.index("'no readings - nothing to compare'")
    i_few  = v.index("too few settled ladders to judge")
    i_good = v.index("trustworthy - the observation names")
    assert i_none < i_few < i_good, (
        "a case arm placed after a broader one is an arm that never fires"
    )


def test_the_file_is_installed():
    order = (ROOT / "sql/INSTALL_ORDER.txt").read_text().split()
    assert "ad4_82_settlement_agreement.sql" in order


# --------------------------------------------------------------------------
# 3. The banking side: the authority wins, the station covers the rest.
# --------------------------------------------------------------------------

def test_the_authority_wins_every_key_it_covers(monkeypatch):
    monkeypatch.setattr(databank, "_verified_weather",
                        lambda d: {("nyc", "2026-09-01"): {"max_c": 20, "n_obs": None,
                                                           "source": "authority:KLGA",
                                                           "evidence_at": "x"}})
    monkeypatch.setattr(databank, "_station_day_max",
                        lambda d: {("nyc", "2026-09-01"): {"max_c": 99, "n_obs": 24,
                                                           "source": "station:KLGA",
                                                           "evidence_at": None},
                                   ("dallas", "2026-09-01"): {"max_c": 30, "n_obs": 24,
                                                              "source": "station:KDAL",
                                                              "evidence_at": None}})
    merged = databank.observed_with_fallback(7)
    assert merged[("nyc", "2026-09-01")]["max_c"] == 20, "the station overwrote the authority"
    assert merged[("nyc", "2026-09-01")]["source"] == "authority:KLGA"
    assert merged[("dallas", "2026-09-01")]["max_c"] == 30, "the fallback did not cover the gap"


def test_the_fallback_is_empty_rather_than_wrong_when_the_view_is_missing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("v_station_day_max does not exist")
    monkeypatch.setattr(databank, "rest_all", boom)
    assert databank._station_day_max(7) == {}, (
        "a missing view must cost coverage, never correctness"
    )


def test_the_fallback_reads_the_hourly_maximum(monkeypatch):
    seen = {}

    def fake_rest_all(table, params, **kw):
        seen["table"] = table
        seen["select"] = dict(params).get("select", "")
        return [{"city_key": "nyc", "for_date": "2026-09-01",
                 "max_c_hourly": 21.0, "n_hourly": 24, "station": "KLGA"}]

    monkeypatch.setattr(databank, "rest_all", fake_rest_all)
    out = databank._station_day_max(7)
    assert seen["table"] == "v_station_day_max"
    assert "max_c_hourly" in seen["select"]
    assert "max_c," not in seen["select"], (
        "reading the whole-feed maximum here reintroduces the five-minute "
        "over-read this was written to remove"
    )
    assert out[("nyc", "2026-09-01")]["source"] == "station:KLGA"


def test_a_day_with_no_reading_is_skipped_not_banked_as_null(monkeypatch):
    monkeypatch.setattr(databank, "rest_all",
                        lambda *a, **k: [{"city_key": "nyc", "for_date": "2026-09-01",
                                          "max_c_hourly": None, "n_hourly": 0,
                                          "station": "KLGA"}])
    assert databank._station_day_max(7) == {}


def test_the_band_record_says_which_thermometer():
    src = (ROOT / "scripts/databank.py").read_text()
    assert '"obs_source": (obs or {}).get("source")' in src, (
        "two sources in one column with no label is not a record"
    )


def test_the_fitters_target_keeps_one_thermometer():
    src = (ROOT / "scripts/databank.py").read_text()
    main = src[src.index("banded = observed_with_fallback("):]
    # the forecasts' authority stays on the --days window; only the band
    # record's context reaches back for late proofs (plan v2 P4.5)
    assert 'verified = _verified_weather(args.days)' in src
    assert "bank_forecasts(verified," in main, (
        "fact_forecast_outcome.error_c is what the model fits against. Measured "
        "by two different thermometers it stops being a target."
    )
    assert "bank_bands(banded," in main


def test_the_provenance_column_migration_adds_only():
    sql = MIG_SRC.read_text().lower()
    assert "add column if not exists obs_source" in sql
    for verb in ("delete", "drop ", "truncate", "set observed_max_c"):
        assert verb not in sql, f"{verb} would rewrite a frozen record"
