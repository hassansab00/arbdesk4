"""THE DAY A READING BELONGS TO.

A daily maximum is a LOCAL-CALENDAR quantity - it is what the market settles
on, what Open-Meteo returns for timezone=auto, and what
weather_forecasts.for_date holds.

Three jobs disagreed with that. measure_skill.py, databank.py and the backtest
runner all took the day from `valid_at[:10]` - a slice of the UTC string
PostgREST returns - and then joined it against a forecast keyed by the city's
LOCAL date. The error is small, systematic and lands in mae_c, which sets
sigma, which sets every band probability and every edge.

These are the cross-timezone cases the mismatch actually shows up in.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from common import city_local_date, timezone_of


def test_the_same_instant_is_a_different_day_in_different_cities():
    """23:00 UTC is the crux: still today in New York, already tomorrow in
    Tokyo. Bucketing by UTC puts both in the same day and one of them is
    wrong for whichever city you did not think about."""
    t = "2026-09-07T23:00:00+00:00"
    assert city_local_date(t, "America/New_York") == "2026-09-07"
    assert city_local_date(t, "Asia/Tokyo") == "2026-09-08"
    assert city_local_date(t, "Europe/London") == "2026-09-08"   # BST, +1
    assert city_local_date(t, "UTC") == "2026-09-07"


def test_a_half_hour_offset_is_handled():
    """Kolkata is +05:30 and Chatham is +12:45. A timezone library gets these
    right and an offset-in-hours shortcut does not, which is why this uses
    zoneinfo rather than arithmetic."""
    assert city_local_date("2026-09-07T18:45:00+00:00", "Asia/Kolkata") == "2026-09-08"
    assert city_local_date("2026-09-07T18:14:00+00:00", "Asia/Kolkata") == "2026-09-07"
    assert city_local_date("2026-09-07T11:20:00+00:00", "Pacific/Chatham") == "2026-09-08"


def test_the_morning_of_a_western_city_stays_on_its_own_day():
    """The other half of the bug. 02:00 UTC is the previous EVENING in New
    York, so a UTC slice pushed those readings a day forward - into the
    forecast for a day that had not happened yet."""
    t = "2026-09-08T02:00:00+00:00"
    assert t[:10] == "2026-09-08"                      # what the old code did
    assert city_local_date(t, "America/New_York") == "2026-09-07"


def test_dst_is_not_a_fixed_offset():
    """New York is -4 in September and -5 in January. A stored offset would be
    wrong for half the year, and the half it is wrong for is winter, when the
    desk's cold-side bands are the ones being priced."""
    assert city_local_date("2026-09-08T03:30:00+00:00", "America/New_York") == "2026-09-07"
    assert city_local_date("2026-01-08T03:30:00+00:00", "America/New_York") == "2026-01-07"
    assert city_local_date("2026-01-08T05:30:00+00:00", "America/New_York") == "2026-01-08"


def test_it_degrades_rather_than_throwing():
    """A city with no timezone is a roster problem. It must not take a whole
    skill run down, and it must not silently pretend to be local either - it
    falls back to UTC, which is what the reading is stored in."""
    assert city_local_date("2026-09-07T23:00:00+00:00", None) == "2026-09-07"
    assert city_local_date("2026-09-07T23:00:00+00:00", "Not/AZone") == "2026-09-07"
    assert city_local_date(None, "Asia/Tokyo") is None
    assert city_local_date("not-a-timestamp", "Asia/Tokyo") == "not-a-time"


def test_naive_timestamps_are_read_as_utc():
    """PostgREST returns a timestamptz with an offset, but a fixture or a
    hand-written row may not carry one. Treating it as local would be a guess;
    treating it as UTC is what the column actually stores."""
    assert city_local_date("2026-09-07T23:00:00", "Asia/Tokyo") == "2026-09-08"


def test_timezone_of_maps_the_roster():
    rows = [{"city_key": "tokyo", "timezone": "Asia/Tokyo"},
            {"city_key": "nyc", "timezone": "America/New_York"},
            {"city_key": "nowhere"}]
    tz = timezone_of(rows)
    assert tz["tokyo"] == "Asia/Tokyo"
    assert tz["nowhere"] is None


def test_no_job_buckets_a_reading_by_utc_any_more():
    """The regression itself. Any `valid_at[:10]` in a job that then joins to
    a local-day forecast is this bug coming back."""
    import glob
    import io
    import tokenize

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for path in (glob.glob(os.path.join(root, "scripts", "*.py"))
                 + glob.glob(os.path.join(root, "scripts", "backtest", "*.py"))):
        # common.py IS the canonical implementation, including the fallback
        # for a timestamp that will not parse at all.
        if os.path.basename(path) == "common.py":
            continue
        # Tokenised, not grepped: the comments and docstrings that EXPLAIN
        # this bug are the documentation of it, not the bug, and a plain
        # regex over the file cannot tell those apart from the real thing.
        src = open(path).read()
        code_lines = set()
        try:
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL):
                    continue
                code_lines.add(tok.start[0])
        except tokenize.TokenError:
            code_lines = set(range(1, src.count("\n") + 2))
        for i, line in enumerate(src.splitlines(), 1):
            if i in code_lines and "valid_at" in line and "[:10]" in line:
                offenders.append(f"{os.path.relpath(path, root)}:{i}: {line.strip()}")
    assert not offenders, (
        "these bucket an observation by its UTC day and are joined against "
        "local-day forecasts - use common.city_local_date:\n  "
        + "\n  ".join(offenders))
