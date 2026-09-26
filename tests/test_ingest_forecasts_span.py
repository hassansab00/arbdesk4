"""The nightly forecast ingest fetches only the days it is missing.

Measured 26 Sep: with 19-26 Sep complete for all 48 cities, forecasts.yml
still ran three chained jobs (~68 billed minutes) and pipeline_daily's ingest
step 19 more, because a 36-day chunk with ONE missing date was fetched whole.
"""
import datetime as dt

import ingest_forecasts as f

D = dt.date


def _have(*days):
    return {d.isoformat() for d in days}


def test_a_night_with_only_today_missing_asks_for_today():
    have = _have(*(D(2026, 8, 22) + dt.timedelta(days=i) for i in range(36)))
    assert f.missing_span(have, D(2026, 8, 22), D(2026, 9, 27)) == (D(2026, 9, 27), D(2026, 9, 27))


def test_an_old_hole_is_still_seen():
    days = [D(2026, 8, 22) + dt.timedelta(days=i) for i in range(37)]
    have = _have(*[d for d in days if d not in (D(2026, 8, 30), D(2026, 9, 27))])
    assert f.missing_span(have, D(2026, 8, 22), D(2026, 9, 27)) == (D(2026, 8, 30), D(2026, 9, 27))


def test_a_complete_chunk_has_no_span():
    have = _have(*(D(2026, 9, 1) + dt.timedelta(days=i) for i in range(10)))
    assert f.missing_span(have, D(2026, 9, 1), D(2026, 9, 10)) is None
    assert f.chunk_is_covered(have, D(2026, 9, 1), D(2026, 9, 10))
