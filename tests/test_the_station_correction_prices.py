"""The station-corrected combination prices days ahead (plan v2.2 P3.9).

Replayed on 582 settled city-days of 13-25 Sep, day-ahead, on the venue's own
ladders with the engine's own sigma and ONLY the centre changed: right bucket
30.2% -> 37.1% (+6.9 pts, 95% [+2.3, +11.1] by date bootstrap), log loss
1.847 -> 1.657. So it replaces the centre, and nothing else, at lead 1 or more.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import probability_engine as pe
from test_the_correction_reaches_the_price import _price, _pp

ROW = {"city_key": "london", "for_date": "2026-09-19", "lead_days": 1, "combined_c": 22.4,
       "spread_c": 0.6, "n_sources": 7, "version": "station-correction:2026-09-19:abc", "_min_lead": 1}


def _with(monkeypatch, row=ROW, **kw):
    monkeypatch.setattr(pe, "_station_corrected_for",
                        lambda city, day, lead: row if (row and lead is not None and int(lead) >= 1) else None)
    return _price(monkeypatch, **kw)


def test_days_ahead_are_centred_on_the_combination(monkeypatch):
    rows, _reg, why = _with(monkeypatch, lead=1, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    assert rows[0]["centre_c"] == pytest.approx(22.4)
    assert rows[0]["sigma_c"] == pytest.approx(1.2), "the width is the engine's own, as replayed"
    assert rows[0]["forecast_max_c"] == 20.0, "the public forecast stays on the record"
    assert any(r.startswith("station_correction:station-correction:2026-09-19:abc:7sources") for r in why)
    assert any(r.startswith("priced_from:station_correction:station-correction:2026-09-19:abc:") for r in why)


def test_same_day_keeps_its_own_path(monkeypatch):
    rows, _reg, why = _with(monkeypatch, lead=0, pp=_pp(bias=1.2))
    assert rows[0]["centre_c"] == pytest.approx(20.0 - 1.2)
    assert not any(r.startswith("station_correction:") for r in why)


def test_no_row_changes_nothing(monkeypatch):
    rows, _reg, _why = _with(monkeypatch, row=None, lead=1, pp=_pp(bias=1.2))
    assert rows[0]["centre_c"] == pytest.approx(20.0 - 1.2)


def test_a_promoted_model_still_wins(monkeypatch):
    promoted = {("london", 1): {"model_version": "m1", "model_mae_c": 1.0, "gain_vs_public_c": 0.3, "n_days": 60}}
    forecasts = {("london", "2026-09-19"): {"lead_days": 1, "predicted_max_c": 19.0, "model_version": "m1",
                                            "run_at": "2026-09-18T00:00:00Z"}}
    rows, _reg, why = _with(monkeypatch, lead=1, promoted=promoted, model_forecasts=forecasts)
    assert rows[0]["centre_c"] == pytest.approx(19.0)
    assert not any(r.startswith("station_correction:") for r in why)


def test_the_loader_respects_the_switch_the_lead_and_the_age(monkeypatch):
    calls = []

    def rest(path, params=None, **k):
        calls.append((path, params))
        if path == "settings":
            return [{"value": {"enabled": switch["on"], "min_lead_days": 1, "max_age_hours": 36}}]
        return [dict(ROW)]
    monkeypatch.setattr(pe, "rest", rest)
    switch = {"on": False}
    pe._station_cache = None
    assert pe._station_corrected_for("london", "2026-09-19", 1) is None
    assert "derived_corrected_forecast" not in [c[0] for c in calls]
    switch["on"] = True
    pe._station_cache = None
    assert pe._station_corrected_for("london", "2026-09-19", 1)["combined_c"] == 22.4
    assert pe._station_corrected_for("london", "2026-09-19", 0) is None
    params = dict(next(p for path, p in calls if path == "derived_corrected_forecast"))
    assert params["computed_at"].startswith("gte."), "a row older than max_age_hours is never read"
    pe._station_cache = None


def test_a_broken_read_prices_from_the_public_path(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("relation derived_corrected_forecast does not exist")
    monkeypatch.setattr(pe, "rest", boom)
    pe._station_cache = None
    assert pe._station_corrected_for("london", "2026-09-19", 1) is None
    assert "station correction unavailable" in capsys.readouterr().err
    pe._station_cache = None
