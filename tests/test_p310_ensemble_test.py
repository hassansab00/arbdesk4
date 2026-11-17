"""The P3.10 ensemble record test (tools/p310_ensemble_test.py): the ensemble
known at a cutoff is only what was fetched by then, the 82 members' summary is
exact, and the 29 Oct count is what the record held."""
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import p310_ensemble_test as T  # noqa: E402

COUNTS = os.path.join(ROOT, "data", "eval", "p310", "ensemble_test_2026-10-29", "counts.json")


def test_the_mixture_is_the_members_together():
    a, b = [20.1, 21.4, 19.8, 22.0, 20.6], [18.9, 23.2, 21.1]
    mu, sd = T.mixture([(len(a), statistics.mean(a), statistics.pstdev(a)),
                        (len(b), statistics.mean(b), statistics.pstdev(b))])
    assert math.isclose(mu, statistics.mean(a + b), abs_tol=1e-12)
    assert math.isclose(sd, statistics.pstdev(a + b), abs_tol=1e-12)


def test_only_what_was_fetched_by_the_cutoff():
    ens = {("x", "2026-10-20", "ecmwf_ifs025"): [(100, 51, 20.0, 1.0), (300, 51, 25.0, 1.0)],
           ("x", "2026-10-20", "gfs025"): [(100, 31, 20.0, 1.0), (300, 31, 25.0, 1.0)]}
    assert T.at_cutoff(ens, "x", "2026-10-20", 99) is None
    assert T.at_cutoff(ens, "x", "2026-10-20", 200)[0] == 20.0
    assert T.at_cutoff(ens, "x", "2026-10-20", 300)[0] == 25.0
    del ens[("x", "2026-10-20", "gfs025")]
    assert T.at_cutoff(ens, "x", "2026-10-20", 300) is None, "both models or none"


def test_a_fold_scores_only_with_min_train_behind_it():
    rows = [{"date": f"2026-10-{d:02d}", "mkt": [0.5, 0.5], "mdl": [0.6, 0.4], "w": 0} for d in range(1, 11)]
    scored, fits = T.by_day(rows)
    assert scored == [] and fits == []


def test_count_names_the_dates_a_fold_would_score_without_scoring():
    rows = [{"date": f"2026-10-{d:02d}"} for d in range(1, 11) for _ in range(T.M.MIN_TRAIN // 4)]
    # 250 a date: 1,000 behind a date once four dates lie two or more days before it
    assert T.countable(rows) == ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10"]


def test_the_29_oct_count_is_below_the_first_look():
    """29 Oct: counted only, no fit and no score; under 20 dates the first look waits."""
    rec = json.load(open(COUNTS))
    assert rec["counted_only"] == "no fit, no score"
    assert {k: v["rows"] for k, v in rec["monthly_as_written"].items()} == {"d0_00": 1172, "d0_08": 1192}
    assert all(not v["months_with_min_train_behind"] for v in rec["monthly_as_written"].values())
    assert {k: len(v["dates_it_would_score"]) for k, v in rec["by_day"].items()} == {"d0_00": 2, "d0_08": 3}
    assert not os.path.exists(os.path.join(os.path.dirname(COUNTS), "result.json"))


FIRST_LOOK = os.path.join(ROOT, "data", "eval", "p310", "ensemble_test_first_look_2026-11-17", "result.json")


def test_the_17_nov_first_look_does_not_add_to_the_price():
    """17 Nov: the first look, due (daily fold 21 and 22 dates); by P3.10's rule
    the ensemble does not add to the price and no second stage beats the market."""
    rec = json.load(open(FIRST_LOOK))
    assert {k: v["scored"]["dates"] for k, v in rec["by_day"].items()} == {"d0_00": 21, "d0_08": 22}
    for fold in ("rule_by_day", "rule_monthly_as_written"):
        assert rec[fold]["ensemble_adds_to_the_price"] is False and rec[fold]["a_second_stage_beats_the_market"] is False
        assert rec[fold]["scored_cutoffs"] == ["d0_00", "d0_08"]
    for fold in ("by_day", "monthly_as_written"):
        for cut, v in rec[fold].items():
            s = v["scored"]
            assert s["gain_pooled_over_recal"]["ci90"][0] <= 0, (fold, cut)
            assert s["gain_recal_over_market"]["ci90"][0] <= 0 and s["gain_pooled_over_market"]["ci90"][0] <= 0, (fold, cut)
    assert rec["by_day"]["d0_00"]["scored"]["gain_pooled_over_recal"] == {"mean": 0.0006, "ci90": [-0.0014, 0.0025]}
