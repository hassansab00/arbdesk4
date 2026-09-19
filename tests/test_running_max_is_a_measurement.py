"""A running maximum is a measurement, and it has to behave like one.

Measured on the live database, 2026-09-19:

    54 cities
    25  no running maximum at all
    14  a CURRENT TEMPERATURE ABOVE THE STORED MAXIMUM, worst by 12.5 C
        (munich held 9.0 while its thermometer read 21.5)
    15  self-consistent

The cause was that live_weather.running_max_c was assembled only from
weather_observations, and IEM serves 37 of the 54 cities about a day late -
exactly 24 complete hourly rows, arriving tomorrow. For those cities the
archive has nothing about today, while n8n keeps live_weather.temp_c current
for all of them. So the maximum came from whatever happened to be stored.

That field drives holds_running_max, out_of_reach, day_decided, strategy s5
and the observed-max floor in the probability engine. Too low, and probability
stays on buckets the day has already passed while bands it has already cleared
look unreachable.

THE DISTINCTION THESE TESTS EXIST TO KEEP is between a maximum and a floor.
One reading says the day reached AT LEAST that. It is a perfectly good FLOOR
under a probability, and it is not a maximum: s5's premise - "the day is over
and the maximum is locked in this band" - cannot rest on it, because the real
maximum may be several degrees higher and in another band.

The SQL half of this is tested against a real Postgres in
tests/database/observation-health.cjs, because the bug being guarded is two
pieces of SQL disagreeing and text assertions pass on that.
"""

import datetime as dt
import re
from pathlib import Path

import pytest

import signal_engine as se
from common import city_local_date

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# s5 may not lock on a floor
# ---------------------------------------------------------------------------
def _opp(**over):
    row = {
        "band_id": "b1", "side": "YES", "city_key": "london",
        "resolution_date": "2026-09-19", "band_lo": 20, "band_hi": 21,
        "open_low": False, "open_high": False, "band_label": "20°C", "unit": "C",
        "model_prob": 0.31, "market_price": 0.22, "edge_net_pp": 9.0,
        "tradeable": True, "block_reason": None, "confidence": 0.62,
        "regime_label": "NORMAL", "market_state": "LIVE",
        "fillable_usd_5c": 250.0, "token_yes": "ty", "token_no": "tn", "spread": 0.02,
    }
    row.update(over)
    return row


def _live(basis, **over):
    """A city whose day is over, read by a station - s5's other two gates
    already satisfied, so only the basis is under test."""
    row = {"city_key": "london", "local_date": "2026-09-19", "running_max_c": 21.5,
           "day_decided": True, "source_kind": "station", "running_max_basis": basis}
    row.update(over)
    return row


@pytest.fixture
def board(monkeypatch):
    def go(live):
        def fake_rest_all(path, params=None, **kw):
            return [_opp()] if path == "v_opportunities" else (live if path == "live_weather" else [])
        monkeypatch.setattr(se, "rest_all", fake_rest_all)
        monkeypatch.setattr(se, "rest", lambda path, params=None: [])
        return se._band_views()[0]
    return go


def test_s5_locks_a_band_only_on_a_series(board):
    assert board([_live("series")]).s5_allowed is True


def test_s5_will_not_lock_a_band_on_a_single_reading(board):
    v = board([_live("floor_only")])
    assert v.s5_allowed is False, (
        "one reading is a FLOOR under the day's maximum, not the maximum. s5 "
        "buys the band holding the locked maximum, and 21.5 C with nothing else "
        "measured could as easily have peaked at 26 C in a band three higher")


def test_s5_will_not_lock_a_band_when_nothing_was_measured_today(board):
    assert board([_live("absent", running_max_c=None)]).s5_allowed is False


def test_the_running_maximum_still_reaches_the_strategies_as_a_floor(board):
    """Refusing s5 is not the same as hiding the number. Everything that wants
    a floor - out_of_reach, the probability engine - still gets one."""
    assert board([_live("floor_only")]).running_max_c == 21.5


# ---------------------------------------------------------------------------
# the probability engine's floor
# ---------------------------------------------------------------------------
def test_the_floor_is_read_from_the_view_that_carries_the_invariant(monkeypatch):
    import probability_engine as pe

    asked = []

    def fake_rest(path, params=None, **kw):
        asked.append(path)
        return [
            # the munich case: no archive today, but the thermometer says 21.5
            {"city_key": "munich", "local_date": "2026-09-19",
             "running_max_c": 21.5, "running_max_basis": "floor_only"},
            # a full series
            {"city_key": "austin", "local_date": "2026-09-19",
             "running_max_c": 31.0, "running_max_basis": "series"},
            # the shanghai case: rolled into a new local day, nothing measured
            {"city_key": "shanghai", "local_date": "2026-09-20",
             "running_max_c": None, "running_max_basis": "absent"},
        ]

    monkeypatch.setattr(pe, "rest", fake_rest)
    floors = pe._observed_floors()

    assert asked == ["v_city_running_max"], (
        "reading live_weather directly takes the number without the invariant "
        "that it is never below the latest reading of the same day")
    assert floors["austin"] == ("2026-09-19", 31.0)
    assert floors["munich"] == ("2026-09-19", 21.5), (
        "a floor may rest on a single reading - 'the day already reached at "
        "least 21.5' is true of one reading, and it is exactly what a floor is")
    assert "shanghai" not in floors, (
        "nothing measured today is no floor; treating it as zero would make "
        "every band impossible")


# ---------------------------------------------------------------------------
# the day a reading belongs to
# ---------------------------------------------------------------------------
def _zone_whose_date_is_not_utc_s():
    """A timezone in which it is currently a different calendar date than UTC.

    Pinning one zone would make this pass or fail depending on the hour the
    suite runs; there is no zone that differs from UTC at every hour. These two
    between them cover all 24.
    """
    hour = dt.datetime.now(dt.timezone.utc).hour
    return "Pacific/Pago_Pago" if hour <= 10 else "Pacific/Kiritimati"   # -11 / +14


def test_live_weather_stamps_a_reading_with_the_city_s_own_day(monkeypatch):
    """It stamped the SERVER's UTC date, into a column named local_date.

    scripts/live_weather.py read dt.date.today() for both the history window
    and the value it wrote. A daily-high market resolves on the city's own
    local day, so Tokyo's running maximum was assembled from the wrong fifteen
    hours and then labelled with the wrong day - and the probability engine
    matches that label against the market's resolution date, so the floor was
    either applied to the wrong day or silently dropped.
    """
    import live_weather as lw

    tz = _zone_whose_date_is_not_utc_s()
    now = dt.datetime.now(dt.timezone.utc)
    expected = city_local_date(now, tz)
    assert expected != now.date().isoformat(), "the fixture zone is not doing its job"

    reading = {"city_key": "kiritimati", "valid_at": now.isoformat(), "temp_c": 18.0,
               "temp_f": 64.4, "cloud_cover": None}
    written = {}

    def fake_rest(path, params=None, **kw):
        if path == "settings":
            return []
        if path == "weather_observations":
            return [
                # today, where this city lives
                {"valid_at": (now - dt.timedelta(hours=1)).isoformat(), "temp_c": 18.0},
                # 26 hours back is ALWAYS another local date, whatever the hour
                {"valid_at": (now - dt.timedelta(hours=26)).isoformat(), "temp_c": 30.0},
            ]
        return []

    monkeypatch.setattr(lw, "get_cities", lambda **kw: [
        {"city_key": "kiritimati", "icao": "PLCH", "timezone": tz, "resolution_source": None}])
    monkeypatch.setattr(lw, "fetch_reading", lambda *a: (dict(reading), "iem"))
    monkeypatch.setattr(lw, "rest", fake_rest)
    monkeypatch.setattr(lw, "insert", lambda *a, **k: None)
    monkeypatch.setattr(lw, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(lw, "upsert",
                        lambda table, rows, on: written.setdefault(table, rows))
    lw.main()

    row = written["live_weather"][0]
    assert row["local_date"] == expected, (
        f"stamped {row['local_date']} - the runner's UTC date - on a city whose "
        f"own date is {expected}")
    assert row["running_max_c"] == 18.0, (
        "yesterday's 30.0 C was counted into today's running maximum: the "
        "history window has to be the city's day, not the server's")


# ---------------------------------------------------------------------------
# one definition of what a maximum rests on
# ---------------------------------------------------------------------------
def _listed():
    return [l.strip() for l in (ROOT / "sql" / "INSTALL_ORDER.txt").read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def test_the_columns_are_added_before_the_function_that_writes_them():
    order = _listed()
    assert "ad4_71_observation_health.sql" in order
    assert (order.index("ad4_71_observation_health.sql")
            < order.index("ad4_live_weather_timing.sql")), (
        "refresh_live_weather_timing() writes readings_today and "
        "running_max_basis, which ad4_71 adds - the other way round the install "
        'fails with `column "running_max_basis" of relation "live_weather" does '
        "not exist`")


def test_nothing_defines_the_basis_a_second_time():
    """The view that REPORTS what a maximum rests on and the function that
    STORES it must not each carry their own CASE.

    They did, for about an hour, and disagreed: the function gated the live
    thermometer to the city's own day and the view did not, so sixteen cities
    read 'absent' from live_weather and 'floor_only' from
    v_city_observation_health. Nothing reading either one could have told.
    """
    definer = ROOT / "sql" / "ad4_71_observation_health.sql"
    assert "create or replace function public.ad4_running_max_basis" in definer.read_text()

    for path in sorted((ROOT / "sql").glob("*.sql")):
        src = "\n".join(l for l in path.read_text().splitlines()
                        if not l.strip().startswith("--"))
        # Comments and string literals both explain the rule in prose that
        # reads exactly like the code ("running_max_basis = series means a real
        # maximum"), so a naive scan matches the explanation and passes on code
        # that still carries a second CASE.
        src = re.sub(r"'(?:[^']|'')*'", "''", src)
        for m in re.finditer(r"running_max_basis\s*=", src):
            tail = src[m.end(): m.end() + 120]
            assert "ad4_running_max_basis" in tail, (
                f"{path.name} assigns running_max_basis without calling "
                "ad4_running_max_basis() - that is a second definition of the "
                f"same fact:\n  {tail.splitlines()[0]}")
