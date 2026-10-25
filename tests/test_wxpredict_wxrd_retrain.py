"""The weekly refit of wxrd-v3 (tools/wxpredict/wxrd_retrain.py): the gate
promotes only on the week the serving model never saw, with a minimum sample
and a bound on change (Rule 11), and every version reaches the rows it writes."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))
sys.path.insert(0, str(ROOT / "tools"))

import wxrd_live  # noqa: E402
import wxrd_retrain as rt  # noqa: E402

PASS = {"n": 2600, "dates": 7, "nll_incumbent": 0.95, "nll_predecessor": 0.96, "tv_mean": 0.05, "valid": True}


def test_a_gate_that_holds_nothing_promotes():
    assert rt.decide(PASS) == (True, [])


def test_the_serving_model_must_not_lose_to_its_predecessor():
    ok, held = rt.decide(dict(PASS, nll_incumbent=0.97))
    assert not ok and "predecessor" in held[0]
    assert rt.decide(dict(PASS, nll_incumbent=0.96))[0], "a tie does no harm"


def test_too_few_decisions_or_dates_hold():
    assert not rt.decide(dict(PASS, n=rt.MIN_DECISIONS - 1))[0]
    assert rt.decide(dict(PASS, n=rt.MIN_DECISIONS))[0]
    assert not rt.decide(dict(PASS, dates=rt.MIN_DATES - 1))[0]


def test_an_update_that_moved_the_ladders_too_far_holds_the_next():
    ok, held = rt.decide(dict(PASS, tv_mean=rt.TV_CAP + 0.001))
    assert not ok and str(rt.TV_CAP) in held[0]
    assert rt.decide(dict(PASS, tv_mean=rt.TV_CAP))[0]


def test_every_reason_is_listed():
    ok, held = rt.decide({"n": 10, "dates": 1, "nll_incumbent": 1.0, "nll_predecessor": 0.9, "tv_mean": 0.5,
                          "valid": False})
    assert not ok and len(held) == 5


def test_a_candidate_without_valid_ladders_holds():
    ok, held = rt.decide(dict(PASS, valid=False))
    assert not ok and "finite" in held[0]


def test_the_bound_is_set_from_the_measured_weekly_change():
    import json
    rec = json.loads((ROOT / "data" / "eval" / "wxpredict" / "retrain_backtest_2026-10-13.json").read_text())
    largest = max(g["tv_candidate_vs_incumbent"]["mean"] for g in rec["gates"])
    assert rt.TV_CAP == round(2 * largest, 2), "TV_CAP is twice the largest measured weekly change"
    assert rt.MIN_DECISIONS <= min(w["n"] for w in rec["served"])


def test_the_week_served_starts_the_day_after_the_last_fitted_day():
    assert rt.served_window("2026-10-11", "2026-10-18") == ("2026-10-12", "2026-10-18")
    assert rt.day("2026-10-11", -rt.PREDECESSOR_DAYS) == "2026-10-04"


def test_a_weekly_version_names_its_rows():
    name = rt.version_name("2026-10-18")
    assert name == "wxrd-v3w20261018"
    assert wxrd_live.variant_version({"version": name, "model_sha256": "ab" * 32}, "wxrd3") == \
        "wxrd3:v3w20261018:abababababab"


def test_the_retrain_scores_the_decisions_the_backtest_scored():
    import json
    rec = json.loads((ROOT / "data" / "eval" / "wxpredict" / "retrain_backtest_2026-10-13.json").read_text())
    assert list(rt.HOURS) == rec["hours_local"]
    assert rt.SEEDS == len(rec["seeds"])
