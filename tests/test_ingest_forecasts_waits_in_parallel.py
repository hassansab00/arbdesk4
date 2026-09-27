"""The forecast ingest waits on hung requests in parallel, and asks a hung city
once more inside the same run (27 Sep).

Measured on the 27 Sep 03:36Z run: a city that answered took ~4 s, 13 of the
36 cities it reached hung for 2 x 30 s + 4 s each, one after another, and the
run hit its 20-minute deadline at city 37 of 48 before any current-run
request - so three continuation jobs and pipeline_daily repeated the work, 85
billed minutes a night. These tests drive the real main() with a simulated
source: the counters that decide `incomplete` (and so a paid continuation)
must come out exactly as before, and the hangs must overlap."""
import datetime as dt
import json
import sys
import time
from collections import defaultdict

import pytest

import ingest_forecasts as f

DAY = dt.date(2026, 9, 26)


class Source:
    """A fake Open-Meteo: `hang_first` cities hang on their first request only,
    `hang_always` on every one, `refuse` answer 400."""

    def __init__(self, hang_first=(), hang_always=(), refuse=(), wait=0.0):
        self.hang_first, self.hang_always, self.refuse = set(hang_first), set(hang_always), set(refuse)
        self.wait, self.calls = wait, defaultdict(int)
        self.store = defaultdict(set)       # city -> dates with every lead

    def fetch(self, lat, lon, start, end, label, models=None):
        city = label.split()[0]
        self.calls[(city, bool(models))] += 1
        if city in self.refuse and not models:
            return None, "refused"
        if city in self.hang_always or (city in self.hang_first and self.calls[(city, False)] == 1 and not models):
            time.sleep(self.wait)
            return None, "unreached"
        return {"city": city, "first": start, "last": end}, "ok"

    def fetch_current(self, lat, lon, label):
        return {"ok": True}, "ok"


def _run(monkeypatch, tmp_path, src, cities, workers=4):
    monkeypatch.setattr(f, "WORKERS", workers)
    monkeypatch.setattr(f, "PAUSE", 0)
    monkeypatch.setattr(f, "MODELS", ["m1"])
    monkeypatch.setattr(f, "fetch", src.fetch)
    monkeypatch.setattr(f, "fetch_current", src.fetch_current)

    def build_rows(city_key, js, model=None, tz=None, first=None, last=None):
        if model:
            return [{"city_key": city_key, "model": model, "for_date": first.isoformat(), "lead_days": 1}]
        return [{"city_key": city_key, "for_date": first.isoformat(), "lead_days": l} for l in f.LEADS]

    def upsert(table, rows, key):
        if table == "weather_forecasts":
            for r in rows:
                src.store[r["city_key"]].add(r["for_date"])
        return len(rows)

    monkeypatch.setattr(f, "build_rows", build_rows)
    monkeypatch.setattr(f, "build_current_rows", lambda c, js, m, at, tz=None: [{"city_key": c}])
    monkeypatch.setattr(f, "upsert", upsert)
    monkeypatch.setattr(f, "existing_dates", lambda c, s, e: {dt.date.fromisoformat(d) for d in src.store[c]})
    monkeypatch.setattr(f, "get_cities", lambda require_coords=True: [
        {"city_key": c, "latitude": 0.0, "longitude": 0.0, "timezone": "UTC"} for c in cities])
    logged = []
    monkeypatch.setattr(f, "log_run", lambda job, status, rows, detail: logged.append((status, detail)))
    out = tmp_path / "result.json"
    monkeypatch.setenv("FORECAST_RESULT_PATH", str(out))
    monkeypatch.setattr(sys, "argv", ["ingest_forecasts.py", DAY.isoformat(), DAY.isoformat()])
    t0 = time.monotonic()
    f.main()
    return json.loads(out.read_text()), logged[-1], time.monotonic() - t0


CITIES = [f"c{i:02d}" for i in range(12)]


def test_a_city_that_hung_once_is_finished_in_the_same_run(monkeypatch, tmp_path):
    src = Source(hang_first=CITIES[:4])
    result, (status, detail), _ = _run(monkeypatch, tmp_path, src, CITIES)
    assert result == {"incomplete": False, "rows_offered": 12 * (len(f.LEADS) + 1),
                      "missing_chunks": 0, "unreached_chunks": 0, "completed_dates": 12}
    assert status == "ok" and all(src.calls[(c, False)] == 2 for c in CITIES[:4])
    assert all(src.calls[(c, False)] == 1 for c in CITIES[4:])


def test_a_city_that_never_answers_still_leaves_the_run_incomplete(monkeypatch, tmp_path):
    src = Source(hang_always=["c03"], refuse=["c07"])
    result, (status, detail), _ = _run(monkeypatch, tmp_path, src, CITIES)
    assert result["incomplete"] is True and status == "partial"
    assert result["unreached_chunks"] == 1 and result["missing_chunks"] == 1
    assert result["completed_dates"] == 10          # every city but the hung and the refused
    assert src.calls[("c03", False)] == 2           # asked twice, not forever


def test_complete_cities_are_not_asked_again(monkeypatch, tmp_path):
    src = Source()
    for c in CITIES:
        src.store[c].add(DAY.isoformat())
    result, _, _ = _run(monkeypatch, tmp_path, src, CITIES)
    assert result["completed_dates"] == 0 and not any(k[1] is False for k in src.calls)


def test_hangs_overlap_instead_of_queueing(monkeypatch, tmp_path):
    hung = CITIES[:8]
    _, _, one_at_a_time = _run(monkeypatch, tmp_path, Source(hang_first=hung, wait=0.15), CITIES, workers=1)
    _, _, four_at_once = _run(monkeypatch, tmp_path, Source(hang_first=hung, wait=0.15), CITIES, workers=4)
    assert one_at_a_time >= 8 * 0.15
    assert four_at_once < 0.6 * one_at_a_time


def test_a_hang_costs_at_most_two_short_waits():
    assert f.TIMEOUT <= 20 and f.TRIES == 2 and f.RETRY_WAIT <= 2 and 1 <= f.WORKERS <= 8
