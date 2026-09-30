"""The fec-v1 same-day harness (tools/fec_same_day.py): the decision instant on
each city's clock, full-ladder scoring, a market ladder only when complete and
fresh, and a bootstrap that resamples dates, not rows."""
import datetime as dt
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import fec_same_day as F  # noqa: E402


def test_the_decision_is_the_ticks_minute_on_the_citys_clock():
    t, tick = F.decision_time(dt.date(2026, 7, 15), 13, "America/New_York")
    assert abs(t - (13 + 36 / 60)) < 1e-9 and tick.isoformat() == "2026-07-15T17:36:00+00:00"
    t, _ = F.decision_time(dt.date(2026, 7, 15), 13, "Asia/Kolkata")     # a half-hour zone
    assert abs(t - (13 + 6 / 60)) < 1e-9
    t, _ = F.decision_time(dt.date(2026, 1, 15), 13, "Europe/London")    # winter, UTC+0
    assert abs(t - (13 + 36 / 60)) < 1e-9


def test_scoring_is_over_the_whole_ladder():
    probs = {"a": 0.2, "b": 0.5, "c": 0.3}
    ll, hit, brier = F.score(probs, "c", ["a", "b", "c"])
    assert abs(ll + math.log(0.3)) < 1e-12 and not hit
    assert abs(brier - (0.04 + 0.25 + 0.49)) < 1e-12
    ll, hit, _ = F.score({"a": 0.0, "b": 1.0}, "a", ["a", "b"])
    assert ll == -math.log(F.FLOOR), "floored, never infinite"
    assert F.score({"a": 0.5, "b": 0.5}, "a", ["a", "b"])[1], "a tie goes to the lower bucket"


def test_the_market_is_compared_only_complete_fresh_and_after_the_decision():
    bands = [{"band_id": "x"}, {"band_id": "y"}]
    t = 1_000_000
    series = {"x": [(t - 60, 0.9), (t + 600, 0.6)], "y": [(t + 1200, 0.4)]}
    m = F.market_ladder(series, bands, t)
    assert m == {"x": 0.6, "y": 0.4}, "the first price at or after the decision, not the stale one before"
    assert F.market_ladder({"x": [(t + 600, 0.6)]}, bands, t) is None, "a bucket with no price"
    assert F.market_ladder({"x": [(t + 600, 0.6)], "y": [(t + 4000, 0.4)]}, bands, t) is None, "older than an hour"
    assert F.market_ladder({"x": [(t, 0.9)], "y": [(t, 0.9)]}, bands, t) is None, "a sum of 1.8 is not a ladder"


def test_the_bootstrap_moves_a_date_with_all_its_rows():
    # one date carries every positive value: a row bootstrap would narrow this,
    # a date bootstrap must straddle it
    pairs = [("d1", 1.0)] * 50 + [(f"d{i}", -0.02) for i in range(2, 22)]
    b = F.cluster_boot(pairs, n=400)
    assert b["dates"] == 21 and b["n"] == 70
    assert b["ci90"][0] < 0 < b["ci90"][1]


def test_a_wilson_interval():
    lo, hi = F.wilson(50, 100)
    assert lo < 0.5 < hi and abs((lo + hi) / 2 - 0.5) < 1e-9
    assert F.wilson(0, 0) is None


def test_the_day_ahead_lane_scores_identical_rows(tmp_path):
    """tools/fec_day_ahead.py: the engine, the raw forecast's bucket and the
    priced centre on the same rows; the market only on head-to-head rows."""
    import gzip
    import fec_day_ahead as DA
    head = ("city_key,unit,for_date,hours_before_day,model_hit,model_prob_on_winner,brier_model,"
            "brier_uniform,head_to_head,market_hit,market_prob_on_winner,brier_model_common,brier_market,"
            "observed_max_c,forecast_max_c,centre_c,raw_hit,centre_hit")
    rows = ["a,C,2026-09-24,3,1,0.5,0.4,0.9,1,1,0.6,0.4,0.3,20,21,20.2,0,1",
            "b,F,2026-09-24,3,0,0.1,0.9,0.9,0,,,,,25,24,,1,",
            "a,C,2026-09-25,3,0,0.0,1.0,0.9,1,0,0.2,1.0,0.8,20,20,,1,"]
    p = tmp_path / "da_rows_x.csv.gz"
    with gzip.open(p, "wt") as f:
        f.write(head + "\n" + "\n".join(rows) + "\n")
    b = DA.block(DA.load(str(p)))
    assert b["n"] == 3 and b["dates"] == 2
    assert b["engine"]["top1"] == round(1 / 3, 4) and b["raw_forecast"]["top1"] == round(2 / 3, 4)
    assert abs(b["engine"]["logloss"] - round((-math.log(0.5) - math.log(0.1) - math.log(1e-6)) / 3, 4)) < 1e-9
    assert b["priced_centre_subset"]["n"] == 1 and b["priced_centre_subset"]["centre_top1"] == 1.0
    assert b["head_to_head"]["n"] == 2, "the market only where both priced the winner before the day"


def test_the_models_column_is_the_whole_day_unless_a_sensitivity_run_asks(tmp_path, monkeypatch):
    """rd3's pre-registered input is tmax_c; tmax_00_17_c (no hour after 17:00,
    so no run that could be published after a morning decision) is a
    sensitivity reading only."""
    import gzip
    p = tmp_path / "models_daily.csv.gz"
    with gzip.open(p, "wt") as f:
        f.write("city_key,lead_days,model,for_date,tmax_c,tmax_00_17_c\n"
                "a,1,gfs,2026-08-01,25.0,24.0\n"
                "a,2,gfs,2026-08-01,26.0,26.0\n"
                "a,1,icon,2026-08-01,23.5,23.5\n")
    monkeypatch.setitem(F.INPUTS, "models", str(p))
    monkeypatch.setattr(F, "G", {})
    assert F.load_models() == {("a", "2026-08-01"): {"gfs": 25.0, "icon": 23.5}}, "lead 1 only, whole day"
    F.G["models_col"] = "tmax_00_17_c"
    assert F.load_models() == {("a", "2026-08-01"): {"gfs": 24.0, "icon": 23.5}}


def test_a_sensitivity_run_is_never_an_acceptance_test():
    src = open(os.path.join(ROOT, "tools", "fec_same_day.py")).read()
    assert '"sensitivity only (not an acceptance test)"' in src
    assert '"_m0017" if sensitivity' in src, "its outputs never overwrite the acceptance run's"
    try:
        F.main(["--period", "holdout", "--challenger", "rd2", "--models-column", "tmax_00_17_c"])
    except SystemExit as e:
        assert e.code == 2, "the option is refused for rd2 before anything runs"
    else:
        raise AssertionError("rd2 with a models column must be refused")
