"""databank.bank_forecasts reads the forecasts the database no longer holds
from data/archive/forecasts (Fresh Supabase, part 2a, 8 Oct): the forecasts'
keep falls to days, and the bank looks back a week."""
import csv
import datetime as dt
import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import databank  # noqa: E402
import weather_history as wh  # noqa: E402
from archive_observations import TABLES, _cell  # noqa: E402

TODAY = dt.date.today()
OLD = (TODAY - dt.timedelta(days=5)).isoformat()      # below the cut
NEW = (TODAY - dt.timedelta(days=1)).isoformat()      # still in the database
CUT = (TODAY - dt.timedelta(days=2)).isoformat()


def _forecast(day, value, run, fid=None):
    return {"forecast_id": fid, "city_key": "london", "model": "open_meteo_forecast",
            "run_at": f"{run}T06:00:00+00:00", "observed_at": f"{run}T06:05:00+00:00",
            "for_date": day, "lead_days": 1, "forecast_max_c": value, "variables": None,
            "source": "open-meteo"}


@pytest.fixture
def archive(monkeypatch, tmp_path):
    wh.reset()
    monkeypatch.setattr(wh, "ROOT", str(tmp_path))
    rel = f"data/archive/forecasts/forecasts-{OLD}-to-{OLD}.csv.gz"
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    cols = TABLES["forecasts"]["columns"]
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        day_before = (dt.date.fromisoformat(OLD) - dt.timedelta(days=1)).isoformat()
        w.writerow({c: _cell(v) for c, v in _forecast(OLD, 18.5, day_before).items() if c in cols})
    yield rel
    wh.reset()


def _rest_all(log):
    db = [_forecast(NEW, 21.0, (TODAY - dt.timedelta(days=2)).isoformat(), fid=7)]

    def rest_all(path, params=None, order=None, page_size=1000):
        if path == "ingest_log":
            return log
        if path == "weather_forecasts":
            _s, filters, _o, _l = wh.parse_filters(params)
            return [dict(r) for r in db if wh.matches(r, filters)]
        if path == "fact_forecast_outcome":
            return []
        raise AssertionError(path)
    return rest_all


def test_a_forecast_below_the_cut_is_banked_from_the_archive(monkeypatch, archive):
    log = [{"finished_at": "x", "detail": {"archived_through": CUT, "file": archive}}]
    monkeypatch.setattr(databank, "rest_all", _rest_all(log))
    verified = {("london", OLD): {"max_c": 19.0, "n_obs": 24, "source": "v"},
                ("london", NEW): {"max_c": 20.0, "n_obs": 24, "source": "v"}}
    out = databank.bank_forecasts(verified, 7, True)
    assert sorted((r["for_date"], r["forecast_max_c"]) for r in out) == [(OLD, 18.5), (NEW, 21.0)]


def test_a_checkout_without_the_file_refuses(monkeypatch, archive):
    log = [{"finished_at": "x", "detail": {"archived_through": CUT,
                                           "file": "data/archive/forecasts/missing.csv.gz"}}]
    monkeypatch.setattr(databank, "rest_all", _rest_all(log))
    with pytest.raises(wh.StaleCheckout):
        databank.bank_forecasts({}, 7, True)
