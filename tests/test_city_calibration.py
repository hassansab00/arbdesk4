"""The blend learns its own city widths; later dates decide whether to use them."""
import datetime as dt
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import city_calibration as cc
import station_mos as sm

AS_OF = dt.date(2026, 10, 16)


def predictions():
    return [{"city": f"c{city}", "date": AS_OF - dt.timedelta(days=30 - day),
             "y": (-1.) ** day, "blend": 0., "p39": -2 * (-1.) ** day}
            for day in range(30) for city in range(48)]


def test_each_city_has_its_own_later_date_gate_and_nightly_bound():
    rows = predictions()
    # Only c0 changes regime on the later dates. No other city's evidence
    # can make that city's poor calibration pass.
    for r in rows:
        if r["city"] == "c0" and r["date"] >= AS_OF - dt.timedelta(days=14):
            r["y"] *= 20
    fits = cc.fit(rows, 1, AS_OF, {"c1": {"sigma_c": 1.}})
    assert fits["c1"]["applies"] is True
    assert fits["c0"]["applies"] is False and fits["c0"]["crps_gain_90"][1] < 0
    assert fits["c1"]["n_fit"] == 16 and fits["c1"]["n_validation"] == 14
    assert fits["c1"]["sigma_c"] == pytest.approx(1.1), "at most one nightly 10% step"
    assert fits["c1"]["version"] != fits["c0"]["version"]


def test_validation_labels_cannot_change_the_width_being_validated():
    rows = predictions()
    first = cc.fit(rows, 1, AS_OF)
    for r in rows:
        if r["date"] >= AS_OF - dt.timedelta(days=14):
            r["y"] *= 20
    second = cc.fit(rows, 1, AS_OF)
    assert first["c0"]["validation_sigma_c"] == second["c0"]["validation_sigma_c"]
    assert first["c0"]["baseline_sigma_c"] == second["c0"]["baseline_sigma_c"]
    assert first["c0"]["applies"] and not second["c0"]["applies"]


def test_thin_city_and_empty_pool_do_not_borrow_another_city_validation():
    rows = [r for r in predictions() if r["city"] != "c0" or r["date"] >= AS_OF - dt.timedelta(days=18)]
    assert cc.fit(rows, 1, AS_OF)["c0"]["reason"] == "insufficient earlier calibration errors"
    thin = [r for r in predictions() if r["city"] == "c0"]
    assert not cc.fit(thin, 1, AS_OF)["c0"]["applies"]
    assert cc.fit([], 1, AS_OF) == {}


def test_current_and_future_days_are_not_fit_and_duplicates_are_refused():
    rows = predictions()
    future = dict(rows[0], date=AS_OF, y=1000.)
    assert cc.fit(rows + [future], 1, AS_OF) == cc.fit(rows, 1, AS_OF)
    with pytest.raises(ValueError, match="duplicate"):
        cc.fit(rows + [rows[0]], 1, AS_OF)


def test_rerun_anchors_before_the_night_instead_of_taking_another_step():
    before = {"sigma_c": 1., "applies": True}
    first = cc.fit(predictions(), 1, AS_OF, {"c0": before})["c0"]
    row = {"city_key": "c0", "lead_days": 1, "as_of": str(AS_OF),
           "prev_as_of": str(AS_OF - dt.timedelta(days=1)),
           "calibration": first, "prev_calibration": before}
    previous = sm.load_previous_calibration(lambda *a, **k: [row], AS_OF)
    again = cc.fit(predictions(), 1, AS_OF, {"c0": previous[("c0", 1)]})["c0"]
    assert again == first


def test_first_passing_fit_is_bounded_from_previous_effective_combination_width():
    rows = {"derived_mos_coefficients": [{"city_key": "c0", "lead_days": 1,
        "as_of": "2026-10-15", "calibration": {"applies": False, "sigma_c": 2.5}}],
        "derived_station_width": [{"city_key": "c0", "lead_days": 1,
        "as_of": "2026-10-16", "sigma_c": 1.05, "prev_as_of": "2026-10-15", "prev_sigma_c": 1.}]}
    prev = sm.load_previous_calibration(lambda table, *a, **k: rows[table], AS_OF)
    fit = cc.fit(predictions(), 1, AS_OF, {"c0": prev[("c0", 1)]})["c0"]
    assert fit["sigma_c"] == 1.1, "an unused candidate is not the live nightly anchor"


def test_temperature_fit_precedes_calibration_and_replay_uses_its_own_label_scope(monkeypatch):
    cutoff = AS_OF - dt.timedelta(days=sm.EVAL_DAYS)
    history = [{"city": "c0", "date": cutoff - dt.timedelta(days=i + 1), "y": 20.,
                "base": 19., "x": [], "full": {}} for i in range(1000)]
    history += [{"city": "c0", "date": cutoff + dt.timedelta(days=i), "y": 21.,
                 "base": 20., "x": [], "full": {}} for i in range(30)]
    truth = {("c0", cutoff.isoformat()): 22.}
    def choose(train):
        assert max(r["date"] for r in train) < cutoff
        return {}, {}, {}
    def replay(rows, lead, days, truth=None):
        assert truth is not None and truth[("c0", cutoff.isoformat())] == 22.
        assert all(r["date"] < AS_OF for r in rows)
        return {("c0", d): 20. for d in days}
    monkeypatch.setattr(sm, "choose_and_fit", choose)
    monkeypatch.setattr(sm, "p39_replay", replay)
    monkeypatch.setattr(sm, "predict", lambda coef, row: 21.)
    preds = []
    assert sm.held_out(history, 1, AS_OF, preds, p39_truth=truth)["n"] == 30
    assert min(r["date"] for r in preds) == cutoff


def test_combination_replay_does_not_substitute_station_labels_for_venue_truth(monkeypatch):
    yesterday = AS_OF - dt.timedelta(days=1)
    rows = [{"city": "c0", "date": d, "y": 10., "full": {"one": 19.}}
            for d in (yesterday, AS_OF)]
    truth = {("c0", str(yesterday)): 22., ("c0", str(AS_OF)): 23.}
    def fit(pairs, previous=None):
        assert {p[-1] for p in pairs} == {22.}
        assert all(p[1] < str(AS_OF) for p in pairs)
        return {"cells": {}}
    monkeypatch.setattr(sm.sc, "fit", fit)
    monkeypatch.setattr(sm.sc, "combine", lambda *a: (21., None, {}))
    assert sm.p39_replay(rows, 1, [AS_OF], truth=truth)[("c0", AS_OF)] == 21.
