"""The P3.10 ensemble record test (tools/p310_ensemble_test.py): the ensemble
known at a cutoff is only what was fetched by then, the 82 members' summary is
exact, and the committed first run says what it found."""
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import p310_ensemble_test as T  # noqa: E402

FIRST = os.path.join(ROOT, "data", "eval", "p310", "ensemble_test_2026-10-29", "result.json")


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


def test_the_first_run_scored_no_fold():
    rec = json.load(open(FIRST))
    assert rec["rule_monthly_as_written"]["verdict"] == "no fold scored"
    assert rec["rule_by_day"]["verdict"] == "no fold scored"
    for k in ("d0_00", "d0_08"):
        assert rec["monthly_as_written"][k]["rows"] < rec["constants"]["MIN_TRAIN"]
    assert rec["monthly_as_written"]["d0_00"]["rows"] == 898 and rec["monthly_as_written"]["d0_08"]["rows"] == 921
