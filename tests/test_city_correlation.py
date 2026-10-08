"""scripts/city_correlation.py returns what recompute_correlation returned
(Fresh Supabase, part 2a, 8 Oct).

tests/fixtures/city_correlation_sql.json is the SQL function itself, run by
tests/database/city-correlation-sql.cjs over seeded random readings and
forecasts. The Python reads the same rows through the database, and through
the archive once both tables were pruned, and must give the same pairs.
"""
import csv
import datetime as dt
import gzip
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import city_correlation as cc  # noqa: E402
import weather_history as wh  # noqa: E402
from archive_observations import TABLES, _cell  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "city_correlation_sql.json").read_text())
TODAY = dt.date.fromisoformat(FIXTURE["anchor"])


def _utc_day(valid_at):
    return wh._ts(valid_at).astimezone(dt.timezone.utc).date().isoformat()


def _view(readings):
    """v_city_utc_day_max over the readings the database holds."""
    days = {}
    for r in readings:
        cc._merge(days, (r["city_key"], _utc_day(r["valid_at"])), r["temp_c"])
    return [{"city_key": c, "utc_date": d, "max_c": m} for (c, d), m in days.items()]


def _db(forecasts, readings, log=()):
    """A rest_all over what the database holds, filtered as PostgREST would."""
    tables = {"weather_forecasts": forecasts, "v_city_utc_day_max": _view(readings), "ingest_log": list(log)}

    def rest_all(path, params, order=None, page_size=1000):
        rows = tables[path]
        if path == "ingest_log":
            job = next(v for k, v in params if k == "job")[3:]
            return [r for r in rows if r["job"] == job]
        if path == "v_city_utc_day_max":
            lo = next(v for k, v in params if k == "utc_date")[4:]
            return [r for r in rows if r["utc_date"] >= lo]
        _select, filters, _order, _limit = wh.parse_filters(params)
        return [dict(r) for r in rows if wh.matches(r, filters)]
    return rest_all


def _expected():
    return [(r["city_a"], r["city_b"], r["n_days"], r["err_corr"]) for r in FIXTURE["rows"]]


def _same(got):
    want = _expected()
    assert [(a, b, n) for a, b, n, _ in got] == [(a, b, n) for a, b, n, _ in want]
    for (a, b, _n, v), (_a, _b, _m, w) in zip(got, want):
        # float8 sums in another order: the last of 15 digits may differ
        assert abs(Decimal(cc.as_numeric(v)) - Decimal(w)) <= Decimal("1e-15"), (a, b, cc.as_numeric(v), w)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    wh.reset()
    monkeypatch.setattr(wh, "ROOT", str(tmp_path))
    yield
    wh.reset()


def test_the_python_returns_what_recompute_correlation_returns():
    # Measured on this fixture: 18 of the 21 equal to the 15th digit, the
    # other three 1e-17 to 1e-16 apart.
    _same(cc.compute(_db(FIXTURE["forecasts"], FIXTURE["readings"]), today=TODAY))


def _write(root, dataset, name, columns, rows):
    path = Path(root) / "data" / "archive" / dataset / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: _cell(r.get(c)) for c in columns})
    return os.path.relpath(path, root)


def test_both_tables_pruned_into_the_archive_give_the_same_pairs(tmp_path):
    """The readings cut at an instant part-way through a UTC day, the
    forecasts at a date: each table's older rows only in data/archive."""
    obs_cut = "2026-09-26T13:00:00+00:00"
    fc_cut = "2026-09-29"
    old_r = [r for r in FIXTURE["readings"] if r["valid_at"] < obs_cut]
    new_r = [r for r in FIXTURE["readings"] if r["valid_at"] >= obs_cut]
    old_f = [r for r in FIXTURE["forecasts"] if r["for_date"] < fc_cut]
    new_f = [r for r in FIXTURE["forecasts"] if r["for_date"] >= fc_cut]
    assert old_r and new_r and old_f and new_f
    obs_file = _write(tmp_path, "observations", "observations-2026-09-07-to-2026-09-26.csv.gz",
                      TABLES["observations"]["columns"], old_r)
    fc_file = _write(tmp_path, "forecasts", "forecasts-2026-09-07-to-2026-09-28.csv.gz",
                     TABLES["forecasts"]["columns"], old_f)
    log = [
        {"job": "archive_observations", "finished_at": "2026-09-27T02:40:00Z",
         "detail": {"archived_through": obs_cut, "file": obs_file}},
        {"job": "archive_forecasts", "finished_at": "2026-09-29T02:40:00Z",
         "detail": {"archived_through": fc_cut, "file": fc_file}},
    ]
    _same(cc.compute(_db(new_f, new_r, log), today=TODAY))


def test_a_checkout_without_the_readings_file_refuses(tmp_path):
    log = [{"job": "archive_observations", "finished_at": "2026-09-27T02:40:00Z",
            "detail": {"archived_through": "2026-09-26T13:00:00+00:00",
                       "file": "data/archive/observations/observations-2026-09-07-to-2026-09-26.csv.gz"}}]
    with pytest.raises(wh.StaleCheckout):
        cc.compute(_db(FIXTURE["forecasts"], FIXTURE["readings"], log), today=TODAY)


def test_a_prune_below_the_window_reads_no_archive():
    log = [{"job": "archive_observations", "finished_at": "2026-09-01T02:40:00Z",
            "detail": {"archived_through": "2026-08-30T02:36:00+00:00",
                       "file": "data/archive/observations/not-in-this-checkout.csv.gz"}}]
    _same(cc.compute(_db(FIXTURE["forecasts"], FIXTURE["readings"], log), today=TODAY))


def test_corr_is_postgres_corr():
    c = cc._Corr()
    for y, x in [(1.0, 2.0), (None, 3.0), (2.0, None)]:
        c.add(y, x)
    assert c.pairings == 3 and c.value() is None          # one known pairing: no variance
    flat = cc._Corr()
    for y in (0.5, 0.5, 0.5):
        flat.add(y, 1.0 + y)
    assert flat.value() is None                            # Sxx == 0
    line = cc._Corr()
    for i in range(5):
        line.add(2.0 * i + 1, float(i))
    assert line.value() == pytest.approx(1.0)


def test_pairs_need_twenty_pairings_not_twenty_days():
    errors = {"2026-10-01": {"amman": [0.1] * 4, "berlin": [0.2] * 5}}   # 20 pairings, one day
    assert [(a, b, n) for a, b, n, _ in cc.pairs(errors)] == [("amman", "berlin", 20)]
    errors = {"2026-10-01": {"amman": [0.1] * 4, "berlin": [0.2] * 4}}
    assert cc.pairs(errors) == []


def test_the_database_order_decides_which_city_is_a():
    """recompute_correlation pairs a.city_key < b.city_key in en_US order,
    where an underscore is ignored first (weather_history.text_key)."""
    errors = {"2026-10-01": {"new_york": [0.0, 1.0, 2.0, 3.0, 4.0], "newark": [1.0, 0.0, 3.0, 2.0, 4.0]}}
    rows = cc.pairs({d: {c: v * 4 for c, v in m.items()} for d, m in errors.items()})
    assert [(a, b) for a, b, _n, _v in rows] == [("newark", "new_york")]


def test_recompute_writes_one_set_in_one_request():
    calls = []
    n = cc.recompute(_db(FIXTURE["forecasts"], FIXTURE["readings"]),
                     lambda table, rows, chunk: calls.append((table, rows, chunk)),
                     today=TODAY, now=dt.datetime(2026, 10, 8, 4, 50, tzinfo=dt.timezone.utc))
    assert n == len(FIXTURE["rows"]) and len(calls) == 1
    table, rows, chunk = calls[0]
    assert table == "derived_city_correlation" and chunk == len(rows)
    assert {r["computed_at"] for r in rows} == {"2026-10-08T04:50:00+00:00"}
    assert all(isinstance(r["err_corr"], str) for r in rows)
