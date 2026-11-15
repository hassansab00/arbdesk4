"""A same-day price on the forecast path takes the station-corrected
combination and its width, while settings.station_same_day_pricing is on.

At lead 0 the engine priced from the public forecast less a per-city bias; the
evening before it had priced the same day from the combination. sd_corr:v1's
forward rows compare the two on the same calls (docs/SD_CORR_PREREG.md, the
second look's files): where the forecast path served, 41 dates, the corrected
ladder gained +0.2413 log loss at the morning call, 90% [+0.2044, +0.2814],
and something at every later checkpoint; it lost only to wxrd3
(docs/MODEL_AUDIT_2026-11-15.md). So the price made here is that candidate's:
the combination's own centre and width, never the station-model blend.
"""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import probability_engine as pe
import variant_shadow as vs
from test_the_correction_reaches_the_price import BANDS, _price, _pp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW = {"city_key": "london", "for_date": "2026-09-19", "lead_days": 1, "combined_c": 20.7,
       "spread_c": 0.6, "n_sources": 7, "version": "station-correction:2026-09-18:abc", "_min_lead": 1,
       "width_c": 0.9, "width_version": "station-width:2026-09-18:w1"}
ON = {"enabled": True}


def _with(monkeypatch, row=ROW, cfg=ON, **kw):
    monkeypatch.setattr(pe, "_station_cache", {("london", "2026-09-19"): dict(row)} if row else {})
    monkeypatch.setattr(pe, "_station_width_cfg", {})
    monkeypatch.setattr(pe, "_station_same_day_cfg", cfg)
    return _price(monkeypatch, **kw)


def _label(why):
    return next(r[len("priced_from:"):] for r in why if r.startswith("priced_from:"))


def test_off_the_same_day_price_is_the_public_forecast_path(monkeypatch):
    for cfg in ({}, None, {"enabled": False}, {"enabled": "true"}):
        rows, _reg, why = _with(monkeypatch, cfg=cfg, lead=0)
        assert rows[0]["centre_c"] == pytest.approx(19.5), "20.0 less the measured bias 0.5"
        assert not any(r.startswith("station_") for r in why)
        assert _label(why).startswith("nws:")


def test_on_it_prices_from_the_combination_and_its_width_and_says_so(monkeypatch):
    rows, _reg, why = _with(monkeypatch, lead=0)
    assert rows[0]["centre_c"] == pytest.approx(20.7)
    assert rows[0]["sigma_c"] == pytest.approx(0.9) and rows[0]["forecast_sigma_c"] == pytest.approx(0.9)
    assert rows[0]["station_width_c"] == pytest.approx(0.9)
    assert rows[0]["forecast_max_c"] == 20.0, "the raw forecast is still on the record"
    assert f"station_same_day:{pe.STATION_SAME_DAY_POLICY}" in why
    assert any(r.startswith("station_width:station-width:2026-09-18:w1:lead1:") for r in why)
    assert _label(why).startswith(
        "station_correction:station-correction:2026-09-18:abc+station-width:2026-09-18:w1:nws:")
    assert sum(r["raw_prob"] for r in rows) == pytest.approx(1.0, abs=1e-5)


def test_the_row_s_own_lead_does_not_matter(monkeypatch):
    """sd_corr read the live row whatever its lead: a row written at lead 0 by
    the nightly fit and one written at lead 2 two evenings before price alike."""
    for lead in (0, 1, 2):
        rows, _reg, _why = _with(monkeypatch, row=dict(ROW, lead_days=lead), lead=0)
        assert rows[0]["centre_c"] == pytest.approx(20.7) and rows[0]["sigma_c"] == pytest.approx(0.9)


def test_it_is_the_ladder_sd_corr_was_scored_on(monkeypatch):
    rows, _reg, _why = _with(monkeypatch, lead=0)
    tested = vs.ladder(20.7, 0.9, "C", BANDS, None, pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP)
    assert {r["band_id"]: r["raw_prob"] for r in rows} == tested


def test_the_advance_width_guard_does_not_widen_it(monkeypatch):
    """The stored width is already an advance width, measured on this centre."""
    guarded, _r, why_off = _with(monkeypatch, cfg={}, lead=0, skill_mae=2.0)
    assert guarded[0]["sigma_c"] > 0.9
    rows, _reg, why = _with(monkeypatch, lead=0, skill_mae=2.0)
    assert rows[0]["sigma_c"] == pytest.approx(0.9)
    assert not any(pe.forecast_uncertainty.POLICY in r for r in why)


def test_it_replaces_a_fitted_same_day_cell_as_the_day_ahead_path_does(monkeypatch):
    rows, _reg, _why = _with(monkeypatch, lead=0, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    assert rows[0]["centre_c"] == pytest.approx(20.7) and rows[0]["sigma_c"] == pytest.approx(0.9)


def test_a_blended_row_gives_its_own_combination_not_the_blend(monkeypatch):
    blended = dict(ROW, combined_c=21.4, p39_c=20.7, version="station-correction:2026-09-18:abc+station-mos:m1",
                   p39_version="station-correction:2026-09-18:abc", mos_c=22.1,
                   width_c=1.3, width_version="city-blend-width:c1",
                   p39_width_c=0.9, p39_width_version="station-width:2026-09-18:w1")
    rows, _reg, why = _with(monkeypatch, row=blended, lead=0)
    assert rows[0]["centre_c"] == pytest.approx(20.7) and rows[0]["sigma_c"] == pytest.approx(0.9)
    assert "station-mos" not in _label(why) and "city-blend-width" not in _label(why)
    row = pe._station_same_day_for("london", "2026-09-19")
    assert "mos_c" not in row and row["same_day"] == pe.STATION_SAME_DAY_POLICY
    assert pe._station_cache[("london", "2026-09-19")]["combined_c"] == 21.4, "the cached row is not changed"


def test_a_row_without_a_width_prices_nothing_here(monkeypatch):
    for row in (dict(ROW, width_c=None, width_version=None), dict(ROW, width_version=None),
                dict(ROW, width_c=0.0), dict(ROW, width_c="x"), dict(ROW, combined_c=None), None):
        rows, _reg, why = _with(monkeypatch, row=row, lead=0)
        assert rows[0]["centre_c"] == pytest.approx(19.5)
        assert not any(r.startswith("station_same_day:") for r in why)


def test_days_ahead_and_a_promoted_model_are_untouched(monkeypatch):
    ahead_on, _r, why_on = _with(monkeypatch, lead=1)
    ahead_off, _r, why_off = _with(monkeypatch, cfg={}, lead=1)
    assert [r["raw_prob"] for r in ahead_on] == [r["raw_prob"] for r in ahead_off]
    assert not any(r.startswith("station_same_day:") for r in why_on)
    promoted = {("london", 0): {"model_version": "m1", "model_mae_c": 1.0, "gain_vs_public_c": 0.3, "n_days": 60}}
    forecasts = {("london", "2026-09-19"): {"lead_days": 0, "predicted_max_c": 19.0, "model_version": "m1",
                                            "run_at": "2026-09-18T00:00:00Z"}}
    rows, _reg, why = _with(monkeypatch, lead=0, promoted=promoted, model_forecasts=forecasts)
    assert not any(r.startswith("station_") for r in why)


def test_the_blend_keeps_what_it_replaces(monkeypatch):
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    cal = {"applies": True, "city_key": "london", "lead_days": 1, "version": "city-blend-width:c1", "sigma_c": 1.3}

    def rest(path, params=None, **k):
        p = dict(params or [])
        if path == "settings":
            return [{"value": {"enabled": True, "min_lead_days": 1, "max_age_hours": 36}}]
        if path == "derived_mos_forecast":
            return [{"city_key": "london", "for_date": "2026-09-19", "lead_days": 1, "mos_c": 22.1, "blend_c": 21.4,
                     "p39_version": ROW["version"], "version": "station-mos:m1", "computed_at": stamp,
                     "calibration": cal, "inputs": {}}]
        return []
    cache = {("london", "2026-09-19"): dict(ROW)}
    monkeypatch.setattr(pe, "rest", rest)
    assert pe._blend_station_model(cache) == 1
    row = cache[("london", "2026-09-19")]
    assert (row["combined_c"], row["width_c"], row["width_version"]) == (21.4, 1.3, "city-blend-width:c1")
    assert (row["p39_c"], row["p39_version"], row["p39_width_c"], row["p39_width_version"]) == (
        20.7, ROW["version"], 0.9, ROW["width_version"])


def test_the_loader_reads_the_switch_and_an_unreadable_one_is_off(monkeypatch, capsys):
    state = {"boom": False}

    def rest(path, params=None, **k):
        p = dict(params or [])
        if path == "settings" and p.get("key") == "eq.station_same_day_pricing":
            if state["boom"]:
                raise RuntimeError("settings unreachable")
            return [{"value": ON}]
        if path == "settings" and p.get("key") == "eq.station_correction_pricing":
            return [{"value": {"enabled": True, "min_lead_days": 1, "max_age_hours": 36}}]
        if path == "derived_corrected_forecast":
            stamp = dt.datetime.now(dt.timezone.utc).isoformat()
            return [dict(ROW, computed_at=stamp, inputs_oldest_run_at=stamp, inputs_newest_run_at=stamp)]
        return []
    monkeypatch.setattr(pe, "rest", rest)
    try:
        pe._station_cache = None
        assert pe._station_corrected_for("london", "2026-09-19", 0) is None, "the day-ahead reader keeps its lead"
        assert pe._station_same_day_for("london", "2026-09-19")["combined_c"] == 20.7
        state["boom"] = True
        pe._station_cache = None
        assert pe._station_same_day_for("london", "2026-09-19") is None
        assert "same-day station pricing switch unreadable" in capsys.readouterr().err
    finally:
        pe._station_cache = None
        pe._station_width_cfg = None
        pe._station_same_day_cfg = None


def test_the_correction_s_own_switch_off_turns_it_off_too(monkeypatch):
    def rest(path, params=None, **k):
        if path == "settings":
            return [{"value": {"enabled": dict(params or []).get("key") == "eq.station_same_day_pricing"}}]
        raise AssertionError("nothing else is read while the correction is off")
    monkeypatch.setattr(pe, "rest", rest)
    try:
        pe._station_cache = None
        assert pe._station_same_day_for("london", "2026-09-19") is None
    finally:
        pe._station_cache = None
        pe._station_same_day_cfg = None


def test_the_migration_turns_it_on_once_and_names_the_evidence():
    sql = open(os.path.join(ROOT, "supabase", "migrations", "20261115120000_same_day_station_pricing.sql")).read()
    block = sql[sql.index("'station_same_day_pricing'"):]
    block = block[:block.index("on conflict (key) do nothing")]
    assert "'enabled', true" in block and f"'policy', '{pe.STATION_SAME_DAY_POLICY}'" in block
    assert "+0.2413" in sql and "docs/MODEL_AUDIT_2026-11-15.md" in sql
