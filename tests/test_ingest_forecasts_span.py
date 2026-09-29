"""The nightly forecast ingest fetches only the days it is missing.

Measured 26 Sep: with 19-26 Sep complete for all 48 cities, forecasts.yml
still ran three chained jobs (~68 billed minutes) and pipeline_daily's ingest
step 19 more, because a 36-day chunk with ONE missing date was fetched whole.
"""
import datetime as dt

import pytest

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


# --------------------------------------------------------------------------
# NOTHING BELOW THE OLDEST DAY THE TABLES HOLD (plan v2 P1.6 phase 2, step 5).
# The test above is the gap reader: it still sees a hole the archive has. The
# night no longer fetches it. missing_span runs from the first missing day to
# today, so fetching that hole wrote days 31-32 back into a table that had
# archived them, with the previous-runs API's run_at values rather than the
# archived rows' - and the table's days are whole from its oldest one only
# while nothing is written below it (v_forecast_latest, v_hit_forecasts).
# --------------------------------------------------------------------------
def _drive(monkeypatch, tmp_path, first_held, argv=None):
    asked = []

    def fetch(lat, lon, start, end, label, models=None):
        asked.append((start, end))
        return None, "refused"
    monkeypatch.setattr(f, "WORKERS", 1)
    monkeypatch.setattr(f, "PAUSE", 0)
    monkeypatch.setattr(f, "MODELS", [])
    monkeypatch.setattr(f, "first_held_date", lambda: first_held)
    monkeypatch.setattr(f, "fetch", fetch)
    monkeypatch.setattr(f, "existing_dates", lambda c, s, e: set())      # every day missing
    monkeypatch.setattr(f, "get_cities", lambda require_coords=True: [
        {"city_key": "nyc", "latitude": 0.0, "longitude": 0.0, "timezone": "UTC"}])
    monkeypatch.setattr(f, "log_run", lambda *a, **k: None)
    monkeypatch.setenv("FORECAST_RESULT_PATH", str(tmp_path / "result.json"))
    monkeypatch.setattr(sys, "argv", argv or ["ingest_forecasts.py"])
    f.main()
    return asked


def test_the_night_never_fetches_below_the_oldest_day_held(monkeypatch, tmp_path):
    today = dt.datetime.now(dt.timezone.utc).date()
    held_from = today - dt.timedelta(days=30)
    asked = _drive(monkeypatch, tmp_path, held_from)
    assert asked, "nothing was fetched"
    assert min(s for s, _ in asked) == held_from, asked     # not today - 35


def test_a_window_inside_what_is_held_is_unchanged(monkeypatch, tmp_path):
    today = dt.datetime.now(dt.timezone.utc).date()
    for held_from in (today - dt.timedelta(days=60), None):
        asked = _drive(monkeypatch, tmp_path, held_from)
        assert min(s for s, _ in asked) == today - dt.timedelta(days=35), (held_from, asked)


def test_a_window_the_archive_has_is_refused_not_fetched(monkeypatch, tmp_path):
    held_from = D(2026, 9, 1)
    with pytest.raises(ValueError, match="data/archive"):
        _drive(monkeypatch, tmp_path, held_from, ["ingest_forecasts.py", "2026-08-01", "2026-08-10"])


def test_a_manual_window_across_the_cut_starts_at_it(monkeypatch, tmp_path):
    asked = _drive(monkeypatch, tmp_path, D(2026, 9, 1), ["ingest_forecasts.py", "2026-08-25", "2026-09-05"])
    assert min(s for s, _ in asked) == D(2026, 9, 1), asked


def test_the_oldest_day_held_is_the_later_of_both_tables(monkeypatch):
    oldest = {"weather_forecasts": "2026-08-30", "weather_forecast_models": "2026-09-01"}
    seen = []

    def rest(table, params=None):
        seen.append((table, params))
        return [{"for_date": oldest[table]}]
    monkeypatch.setattr(f, "rest", rest)
    assert f.first_held_date() == D(2026, 9, 1)
    assert [t for t, _ in seen] == ["weather_forecasts", "weather_forecast_models"]
    assert all(("order", "for_date.asc") in p and ("limit", "1") in p for _, p in seen)
    monkeypatch.setattr(f, "rest", lambda table, params=None: [])
    assert f.first_held_date() is None                        # an empty database clamps nothing
    assert f.catchup_start(D(2026, 8, 1), None) == D(2026, 8, 1)

