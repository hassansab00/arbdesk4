"""The width around the corrected centre (plan v2.3 P3.9 part 3): recorded
beside every price, priced from only while settings.station_width_pricing is on.

docs/P39_SERVED_WIDTH_2026-09-27.md scored it on 17-25 Sep (416 city-days,
day-ahead, the venue's ladders): +0.138 log loss per city-day, 90% [+0.105,
+0.171], over the width served. Those dates came before the design, so the
engine records the stored width on every price for the forward score, and
prices from it only when the switch says so: at lead <= max_lead_days, and
only where the centre is the combination the width was fitted around.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import probability_engine as pe
from test_the_correction_reaches_the_price import _price, _pp

ROW = {"city_key": "london", "for_date": "2026-09-19", "lead_days": 1, "combined_c": 22.4,
       "spread_c": 0.6, "n_sources": 7, "version": "station-correction:2026-09-19:abc", "_min_lead": 1,
       "width_c": 0.9, "width_version": "station-width:2026-09-19:w1"}
ON = {"enabled": True, "max_lead_days": 1}


def _with(monkeypatch, row=ROW, cfg=None, **kw):
    monkeypatch.setattr(pe, "_station_corrected_for",
                        lambda city, day, lead: row if (row and lead is not None and int(lead) >= 1) else None)
    monkeypatch.setattr(pe, "_station_width_cfg", cfg if cfg is not None else {})
    return _price(monkeypatch, **kw)


def _label(why):
    return next(r[len("priced_from:"):] for r in why if r.startswith("priced_from:"))


def test_off_the_engine_keeps_its_width_and_records_the_stored_one(monkeypatch):
    rows, _reg, why = _with(monkeypatch, lead=1, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    assert rows[0]["centre_c"] == pytest.approx(22.4)
    assert rows[0]["sigma_c"] == pytest.approx(1.2), "the engine's own width while the switch is off"
    assert all(r["station_width_c"] == pytest.approx(0.9) for r in rows), "recorded on every band"
    assert not any(r.startswith("station_width:") for r in why)
    assert "+station-width:" not in _label(why), "the label claims only what priced"


def test_on_the_stored_width_prices_and_says_so(monkeypatch):
    rows, _reg, why = _with(monkeypatch, cfg=ON, lead=1, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    assert rows[0]["sigma_c"] == pytest.approx(0.9)
    assert rows[0]["forecast_sigma_c"] == pytest.approx(0.9), "the width the forecast path produced"
    assert rows[0]["station_width_c"] == pytest.approx(0.9), "sigma_c == station_width_c: it priced"
    assert "station_width:station-width:2026-09-19:w1:lead1:sigma1.20->0.90C" in why
    assert _label(why).startswith(
        "station_correction:station-correction:2026-09-19:abc+station-width:2026-09-19:w1:nws:")


def test_a_narrower_width_concentrates_the_ladder_where_the_centre_is(monkeypatch):
    off, _r, _w = _with(monkeypatch, lead=1, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    on, _r, _w = _with(monkeypatch, cfg=ON, lead=1, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    top_off = max(r["raw_prob"] for r in off)
    top_on = max(r["raw_prob"] for r in on)
    assert top_on > top_off
    assert sum(r["raw_prob"] for r in on) == pytest.approx(1.0, abs=1e-5)


def test_beyond_max_lead_it_is_recorded_not_priced(monkeypatch):
    row = dict(ROW, lead_days=2)
    rows, _reg, why = _with(monkeypatch, row=row, cfg=ON, lead=2, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    assert rows[0]["sigma_c"] == pytest.approx(1.2)
    assert rows[0]["station_width_c"] == pytest.approx(0.9)
    assert not any(r.startswith("station_width:") for r in why)


def test_the_rows_lead_decides_not_the_engines(monkeypatch):
    """The width was fitted at the lead of the runs that built this centre
    (the forward row's lead), so that lead is the one the switch reads."""
    row = dict(ROW, lead_days=2)
    rows, _reg, _why = _with(monkeypatch, row=row, cfg=ON, lead=1, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    assert rows[0]["sigma_c"] == pytest.approx(1.2)


def test_no_stored_width_no_record(monkeypatch):
    for row in (dict(ROW, width_c=None, width_version=None), dict(ROW, width_version=None),
                dict(ROW, width_c=0.0)):
        rows, _reg, why = _with(monkeypatch, row=row, cfg=ON, lead=1, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
        assert rows[0]["sigma_c"] == pytest.approx(1.2)
        assert "station_width_c" not in rows[0], "omitted, not sent as null or as a width nobody stored"
        assert not any(r.startswith("station_width:") for r in why)


def test_same_day_and_a_promoted_model_never_touch_it(monkeypatch):
    rows, _reg, why = _with(monkeypatch, cfg=ON, lead=0, pp=_pp(bias=1.2, ratio=0.8, baseline=1.5))
    assert "station_width_c" not in rows[0] and rows[0]["sigma_c"] == pytest.approx(1.2)
    promoted = {("london", 1): {"model_version": "m1", "model_mae_c": 1.0, "gain_vs_public_c": 0.3, "n_days": 60}}
    forecasts = {("london", "2026-09-19"): {"lead_days": 1, "predicted_max_c": 19.0, "model_version": "m1",
                                            "run_at": "2026-09-18T00:00:00Z"}}
    rows, _reg, why = _with(monkeypatch, cfg=ON, lead=1, promoted=promoted, model_forecasts=forecasts)
    assert "station_width_c" not in rows[0]
    assert not any(r.startswith("station_width:") for r in why)


def test_the_loader_reads_the_switch_and_an_unreadable_one_is_off(monkeypatch, capsys):
    def rest(path, params=None, **k):
        p = dict(params or [])
        if path == "settings" and p.get("key") == "eq.station_width_pricing":
            if width["boom"]:
                raise RuntimeError("settings unreachable")
            return [{"value": ON}]
        if path == "settings":
            return [{"value": {"enabled": True, "min_lead_days": 1, "max_age_hours": 36}}]
        if path == "derived_corrected_forecast":
            assert "width_c" in p["select"] and "width_version" in p["select"]
            return [dict(ROW)]
        return []
    monkeypatch.setattr(pe, "rest", rest)
    width = {"boom": False}
    pe._station_cache = None
    row = pe._station_corrected_for("london", "2026-09-19", 1)
    assert pe._station_width_for(row) == (0.9, True)
    width["boom"] = True
    pe._station_cache = None
    row = pe._station_corrected_for("london", "2026-09-19", 1)
    assert row["combined_c"] == 22.4, "the centre does not depend on the width switch"
    assert pe._station_width_for(row) == (0.9, False)
    assert "station width switch unreadable" in capsys.readouterr().err
    pe._station_cache = None
    pe._station_width_cfg = None


def test_the_switch_starts_off_in_the_migration():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sql = open(os.path.join(root, "supabase", "migrations",
                            "20260927190000_the_width_around_the_corrected_centre.sql")).read()
    block = sql[sql.index("'station_width_pricing'"):]
    block = block[:block.index("on conflict (key) do nothing")]
    assert "'enabled', false" in block and "'max_lead_days', 1" in block
