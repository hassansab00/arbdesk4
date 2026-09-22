"""The IEM rate limit was never about going slower.

scripts/ingest_observations.py sent ONE HTTP REQUEST PER STATION - 48 of them
a run. On a GitHub runner, which gets its own egress, that works and takes
about 115 seconds. On n8n Cloud, where every tenant shares one IP, it does
not. Measured against the live service and recorded in the workflow's own
comment:

    12 concurrent   44 of 48 stations returned nothing
     4 concurrent   42 of 48
     1 at a time    34 of 48, in 26 s, because most were rejected before
                    they were served
    every failure body: "Too many requests from your IP address, slow down."

SERIALISING DID NOT HELP, and that is the whole diagnosis. A concurrency cap
is cleared by going one at a time; this one was not, so the limiter counts
REQUESTS per IP. The answer is not a longer interval - it is to stop making
48 requests. The ASOS service accepts repeated station= parameters, and
`requests` serialises a list into exactly that.

Measured from the same n8n egress that had been losing two thirds of the
board, 2026-09-22:

    one request, all 48 stations       48 of 48 returned, 2,239 rows,
                                       138 KB, ~4 s, no rate limit
    the same 48 over the last 4h       168 rows, 10 KB
                                       (sts/ets, which is what makes an
                                        hourly cadence cheap)

AND IT UNCOVERED THE TRAP THAT WOULD HAVE SHIPPED WITH IT. IEM answers a US
station under its THREE-LETTER id: ask for KAUS, get rows labelled AUS. With
one station per request that is invisible, because the caller already knows
which city it asked about. With one response carrying all of them, a map
keyed on cities.icao drops every US city - 11 of 48 here, including New York,
Chicago, Los Angeles and Miami - and the entire suite passes while it happens,
because the one shared fixture uses KAUS where production sends AUS.

    select o.station, c.icao from weather_observations o join cities c ...
      ATL/KATL  AUS/KAUS  BKF/KBKF  DAL/KDAL  DCA/KDCA  HOU/KHOU
      LAX/KLAX  LGA/KLGA  MIA/KMIA  ORD/KORD  SEA/KSEA  SFO/KSFO

The database had been storing the three-letter form all along.
"""

import datetime as dt
import pathlib

import pytest

import ingest_observations as io_obs


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "iem_asos_batched.csv"

CITIES = [
    {"city_key": "jeddah", "icao": "OEJN"},
    {"city_key": "austin", "icao": "KAUS"},
    {"city_key": "london", "icao": "EGLC"},
    {"city_key": "nyc", "icao": "KLGA"},
    {"city_key": "san_francisco", "icao": "KSFO"},
    {"city_key": "beijing", "icao": "ZBAA"},
]


# ---------------------------------------------------------------------------
# one request
# ---------------------------------------------------------------------------
def capture(monkeypatch):
    sent = {}

    class R:
        text = FIXTURE.read_text(encoding="utf-8")

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, timeout=None):
        sent.clear()
        sent.update(params or {})
        return R()

    monkeypatch.setattr(io_obs.requests, "get", fake_get)
    return sent


def test_a_list_of_stations_goes_out_as_repeated_parameters(monkeypatch):
    """requests turns {"station": [...]} into station=A&station=B. That is
    exactly the form the ASOS service wants, and it is the whole fix."""
    sent = capture(monkeypatch)
    io_obs.fetch_station(["KAUS", "EGLC", "ZBAA"],
                         dt.date(2026, 9, 20), dt.date(2026, 9, 22))
    assert sent["station"] == ["KAUS", "EGLC", "ZBAA"]


def test_one_station_still_goes_out_as_one_string(monkeypatch):
    """scripts/live_weather.py reads a single city this way and must not
    change behaviour. A one-element list would also work on the wire; a
    string is what the older callers pass and what they get back."""
    sent = capture(monkeypatch)
    io_obs.fetch_station("EGLC", dt.date(2026, 9, 20), dt.date(2026, 9, 22))
    assert sent["station"] == "EGLC"


def test_the_day_window_is_still_sent(monkeypatch):
    """day2-is-exclusive was a three-week bug and its guard reads these keys
    off the query. sts/ets override them at the service; they still go."""
    sent = capture(monkeypatch)
    io_obs.fetch_station(["KAUS"], dt.date(2026, 9, 20), dt.date(2026, 9, 22))
    assert (sent["year1"], sent["month1"], sent["day1"]) == (2026, 9, 20)
    assert (sent["year2"], sent["month2"], sent["day2"]) == (2026, 9, 22)
    assert "sts" not in sent and "ets" not in sent


def test_an_instant_window_is_sent_as_sts_and_ets(monkeypatch):
    """What makes hourly cheap: 168 rows and 10 KB instead of 2,239 and 138 KB."""
    sent = capture(monkeypatch)
    since = dt.datetime(2026, 9, 22, 3, 0, tzinfo=dt.timezone.utc)
    until = dt.datetime(2026, 9, 22, 7, 0, tzinfo=dt.timezone.utc)
    io_obs.fetch_station(["KAUS"], since.date(), until.date(), since=since, until=until)
    assert sent["sts"] == "2026-09-22T03:00Z"
    assert sent["ets"] == "2026-09-22T07:00Z"


# ---------------------------------------------------------------------------
# the K-prefix trap
# ---------------------------------------------------------------------------
def test_a_us_station_is_reachable_under_both_spellings():
    m = io_obs.station_map(CITIES)
    assert m["KAUS"] == "austin" and m["AUS"] == "austin"
    assert m["KLGA"] == "nyc" and m["LGA"] == "nyc"


def test_a_non_us_station_gains_no_alias():
    """Only the US network is re-spelled. EGLC must not also answer to GLC -
    a three-letter alias for every ICAO invites exactly the collision the
    map refuses."""
    m = io_obs.station_map(CITIES)
    assert "GLC" not in m and "BAA" not in m and "EJN" not in m
    assert m["EGLC"] == "london" and m["ZBAA"] == "beijing"


def test_two_cities_on_one_station_id_is_refused_not_resolved():
    """Attributing one city's afternoon to another city's market is worse
    than a station nobody claims."""
    with pytest.raises(ValueError, match="same IEM station id"):
        io_obs.station_map([{"city_key": "a", "icao": "KAUS"},
                            {"city_key": "b", "icao": "AUS"}])


def test_a_city_without_an_icao_is_skipped_not_crashed():
    m = io_obs.station_map(CITIES + [{"city_key": "nowhere", "icao": None},
                                     {"city_key": "blank", "icao": "  "}])
    # three non-US cities at one spelling each, three US cities at two
    assert "nowhere" not in m.values() and "blank" not in m.values()
    assert len(m) == 9


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------
def test_every_city_in_a_batched_response_is_attributed():
    """The regression itself. Six cities asked for, six cities back - three
    of them only reachable through the three-letter spelling."""
    rows = io_obs.parse(FIXTURE.read_text(encoding="utf-8"),
                        by_station=io_obs.station_map(CITIES))
    assert {r["city_key"] for r in rows} == {
        "jeddah", "austin", "london", "nyc", "san_francisco", "beijing"}


def test_the_icao_only_map_is_what_would_have_dropped_them():
    """Kept as data rather than described: with the obvious map, the US
    cities vanish and nothing says so."""
    icao_only = {c["icao"]: c["city_key"] for c in CITIES}
    rows = io_obs.parse(FIXTURE.read_text(encoding="utf-8"), by_station=icao_only)
    got = {r["city_key"] for r in rows}
    assert "austin" not in got and "nyc" not in got and "san_francisco" not in got
    assert got == {"jeddah", "london", "beijing"}


def test_a_station_nobody_asked_for_is_dropped_never_guessed(capsys):
    """XXXX is in the fixture on purpose. Falling back to the last-seen city
    would put one city's reading on another city's market."""
    rows = io_obs.parse(FIXTURE.read_text(encoding="utf-8"),
                        by_station=io_obs.station_map(CITIES))
    assert all(r["city_key"] in {c["city_key"] for c in CITIES} for r in rows)
    assert "XXXX" in capsys.readouterr().err


def test_the_single_city_form_still_works():
    """parse(text, "nyc") - the second positional argument - is what
    scripts/live_weather.py and the older tests pass."""
    rows = io_obs.parse(FIXTURE.read_text(encoding="utf-8"), "nyc")
    assert rows and {r["city_key"] for r in rows} == {"nyc"}
    assert len(rows) == 7, "without a map every row belongs to the named city"


def test_the_station_column_is_kept_alongside_the_city():
    rows = io_obs.parse(FIXTURE.read_text(encoding="utf-8"),
                        by_station=io_obs.station_map(CITIES))
    austin = [r for r in rows if r["city_key"] == "austin"]
    assert austin and austin[0]["station"] == "AUS"


def test_the_values_survive_the_batched_path():
    """Same conversions as the single-station path - the batching must not
    quietly change what a row means."""
    rows = io_obs.parse(FIXTURE.read_text(encoding="utf-8"),
                        by_station=io_obs.station_map(CITIES))
    by_city = {r["city_key"]: r for r in rows}
    assert by_city["austin"]["temp_c"] == pytest.approx(32.78, abs=0.01)
    assert by_city["austin"]["pressure_hpa"] == 1010.90
    assert by_city["london"]["cloud_cover"] == 6      # BKN035 -> 6 oktas
    assert by_city["san_francisco"]["cloud_cover"] == 0   # CLR
    assert by_city["san_francisco"]["precip"] is None     # empty
    assert by_city["jeddah"]["pressure_hpa"] is None      # empty mslp
    assert by_city["nyc"]["wind_dir_deg"] == 180.0


# ---------------------------------------------------------------------------
# chunking, so a year-long backfill is not one 25 MB response
# ---------------------------------------------------------------------------
def test_a_short_window_is_a_single_request():
    got = io_obs.day_chunks(dt.date(2026, 9, 20), dt.date(2026, 9, 22))
    assert got == [(dt.date(2026, 9, 20), dt.date(2026, 9, 22))]


def test_a_year_is_split_and_covers_the_whole_window():
    start, end = dt.date(2025, 9, 22), dt.date(2026, 9, 22)
    got = io_obs.day_chunks(start, end)
    assert got[0][0] == start and got[-1][1] == end
    assert all(b - a <= dt.timedelta(days=io_obs.MAX_DAYS_PER_REQUEST) for a, b in got)
    assert all(got[i][1] == got[i + 1][0] for i in range(len(got) - 1)), "a gap"
    assert len(got) == 13


def test_an_empty_window_still_asks_once():
    """days=0 must not turn into zero requests and a silent green run."""
    d = dt.date(2026, 9, 22)
    assert io_obs.day_chunks(d, d) == [(d, d)]
