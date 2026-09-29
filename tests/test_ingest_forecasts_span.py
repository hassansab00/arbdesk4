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


# --------------------------------------------------------------------------
# THE DATABASE AND THE ARCHIVE (plan v2 P1.6 phase 2, step 3). The window is
# 35 days; at a 30-day keep its oldest five are in data/archive, not the
# table. Read from the table alone they looked missing and the night fetched
# 36 days for every city and wrote them back.
# --------------------------------------------------------------------------
import csv  # noqa: E402
import gzip  # noqa: E402
import io  # noqa: E402

import sys  # noqa: E402

import weather_history  # noqa: E402

TODAY = D(2026, 10, 30)
CUT = TODAY - dt.timedelta(days=30)                 # the first day the table keeps


def _rows(city, days):
    return [{"city_key": city, "model": f.MODEL_LABEL, "for_date": d.isoformat(), "lead_days": lead,
             "run_at": f"{(d - dt.timedelta(days=lead)).isoformat()}T00:00:00+00:00",
             "observed_at": "2026-10-01T00:00:00+00:00", "forecast_max_c": 20.0,
             "source": "open-meteo-previous-runs"} for d in days for lead in f.LEADS]


def _world(monkeypatch, tmp_path, archived_days, held_days):
    name = f"forecasts-{min(archived_days)}-to-{max(archived_days)}.csv.gz"
    path = tmp_path / "data" / "archive" / "forecasts" / name
    path.parent.mkdir(parents=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["city_key", "model", "run_at", "observed_at", "for_date", "lead_days",
                                        "forecast_max_c", "variables", "source"])
    w.writeheader()
    for r in _rows("nyc", archived_days):
        w.writerow(r)
    path.write_bytes(gzip.compress(buf.getvalue().encode()))
    held = _rows("nyc", held_days)

    def rest(p, params=None):
        assert p == "ingest_log", p
        return [{"finished_at": "2026-10-30T02:40:00+00:00",
                 "detail": {"archived_through": CUT.isoformat(),
                            "file": f"data/archive/forecasts/{name}"}}]

    def rest_all(p, params=None, *, order, page_size=500):
        assert p == "weather_forecasts", p
        select, filters, _, _ = weather_history.parse_filters(params)
        return [{c: r[c] for c in select} for r in held if weather_history.matches(r, filters)]
    monkeypatch.setattr(f, "rest", rest)
    # existing_dates imports rest_all from whatever `common` is loaded when
    # it runs, which other tests replace; patch that one.
    monkeypatch.setattr(sys.modules["common"], "rest_all", rest_all)
    monkeypatch.setattr(weather_history, "ROOT", str(tmp_path))


def _days(lo, hi):
    return [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)]


def test_a_day_the_archive_holds_is_not_fetched_again(monkeypatch, tmp_path):
    start = TODAY - dt.timedelta(days=35)
    _world(monkeypatch, tmp_path, archived_days=_days(start, CUT - dt.timedelta(days=1)),
           held_days=_days(CUT, TODAY - dt.timedelta(days=1)))
    have = f.existing_dates("nyc", start, TODAY)
    assert len(have) == 35                            # every day but today
    assert f.missing_span(have, start, TODAY) == (TODAY, TODAY)


def test_a_real_hole_below_the_keep_is_still_found(monkeypatch, tmp_path):
    start = TODAY - dt.timedelta(days=35)
    hole = TODAY - dt.timedelta(days=33)
    _world(monkeypatch, tmp_path,
           archived_days=[d for d in _days(start, CUT - dt.timedelta(days=1)) if d != hole],
           held_days=_days(CUT, TODAY - dt.timedelta(days=1)))
    have = f.existing_dates("nyc", start, TODAY)
    assert hole.isoformat() not in have
    assert f.missing_span(have, start, TODAY) == (hole, TODAY)
