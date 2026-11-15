"""own_w:v1 in shadow (docs/OWN_W_PREREG.md, tools/own_w_forward.py): the learner
is the study's recipe under Rule 11, a night reads nothing it could not have
known, a look is refused before it is due, and the document says what the
committed design holds."""
import collections
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
DESIGN = json.load(open(os.path.join(ROOT, "data", "eval", "own_w", "design_2026-11-15.json")))
STUDY = json.load(open(os.path.join(ROOT, "data", "eval", "model_audit_2026-11-15", "station_weights_study.json")))
DOC = open(os.path.join(ROOT, "docs", "OWN_W_PREREG.md")).read()


def _g(x):
    return f"{x:+.4f}"


def test_the_design_replays_the_calls_the_study_replayed():
    """Same calls, same base and the same result for the study's own recipe: the
    only new thing in the design is Rule 11."""
    for cp in ("d1_eve", "morning"):
        d, s = DESIGN["calls"][cp], STUDY["forward"]["served_calls_replayed"][cp]
        assert (d["n"], d["dates"], d["first_date"], d["last_date"]) == (s["n"], s["dates"], s["first_date"], s["last_date"])
        assert d["served"] == s["served"] and d["market"] == s["market"]
        assert d["study_own_w"] == {k: s["own_w"][k] for k in ("log_loss", "top1")}
    assert DESIGN["calls"]["morning"]["base"] == {k: STUDY["forward"]["served_calls_replayed"]["morning"]["combination"][k]
                                                 for k in ("log_loss", "top1")}
    assert DESIGN["calls"]["d1_eve"]["study_own_w_gain"] == STUDY["forward"]["served_calls_replayed"]["d1_eve"]["own_w"][
        "log_loss_gain_over_served"]
    for lead in ("0", "1", "2"):
        d, s = DESIGN["station_error_by_lead"][lead], STUDY["forward"]["by_lead"][lead]
        assert (d["n"], d["run_nights"], d["mae_combination"]) == (s["n"], s["run_nights"], s["mae_prod"])


def test_rule_11_costs_little_on_the_study_dates_and_both_calls_gain():
    for cp in ("d1_eve", "morning"):
        d = DESIGN["calls"][cp]
        assert d["gain"]["ci90"][0] > 0, cp
        assert 0 <= d["study_own_w_gain"]["mean"] - d["gain"]["mean"] < 0.002, "the bounds and steps cost under 0.002"
        assert d["own_w_v1"]["log_loss"] > d["market"]["log_loss"], "and it does not reach the market"


def test_the_document_says_what_the_design_holds():
    e, m = DESIGN["calls"]["d1_eve"], DESIGN["calls"]["morning"]
    for d in (e, m):
        assert (f"| {d['n']:,} | {d['base']['log_loss']:.4f} | {d['own_w_v1']['log_loss']:.4f} | {_g(d['gain']['mean'])} | "
                f"{_g(d['gain']['ci90'][0])} to {_g(d['gain']['ci90'][1])} | {_g(d['study_own_w_gain']['mean'])} | "
                f"{d['market']['log_loss']:.4f} |") in DOC
        assert (f"| {100 * d['base']['top1']:.1f}% | {100 * d['own_w_v1']['top1']:.1f}% | | | "
                f"{100 * d['study_own_w']['top1']:.1f}% | {100 * d['market']['top1']:.1f}% |") in DOC
    assert f"by {e['mean_absolute_move_c']:.3f} C" in DOC and f"{m['mean_absolute_move_c']:.3f} C at the morning" in DOC
    assert f"gained in {e['cities_better']} of {e['cities']} cities" in DOC and f"{m['cities_better']} of {m['cities']} at the" in DOC
    assert f"within {e['replay_of_served_max_log_loss_difference']:.4f} log loss" in DOC
    for lead, s in DESIGN["station_error_by_lead"].items():
        assert (f"| {lead} | {s['n']:,} | {s['run_nights']} | {s['mae_combination']:.4f} C | {s['mae_own_w_v1']:.4f} C | "
                f"{s['gain']['mean']:.4f} | {s['gain']['ci90'][0]:.4f} to {s['gain']['ci90'][1]:.4f} |") in DOC
    c = DESIGN["cells"]
    assert f"({c['cell_nights']:,} cell-nights)" in DOC
    assert (f"on {100 * c['skill_at_a_bound']:.1f}%, a skill was held by its step on {100 * c['skill_held_by_its_step']:.1f}%, "
            f"a bias by its step on {100 * c['bias_held_by_its_step']:.2f}%") in DOC


def test_the_constants_are_the_document_s():
    pytest.importorskip("numpy")
    import own_w_forward as O
    import station_correction as sc
    r = DESIGN["rule_11"]
    assert (O.MIN_N, O.BIAS_MAX_STEP, O.REL_LO, O.REL_HI, O.REL_MAX_STEP, O.GAP_DAYS, O.STATE_FROM) == (
        r["min_n"], r["bias_max_step_c"], r["skill_bounds"][0], r["skill_bounds"][1], r["skill_max_step"],
        r["gap_days"], r["state_from"]) == (5, 0.25, 0.5, 2.0, 0.10, 2, "2026-09-26")
    assert (O.MIN_N, O.BIAS_MAX_STEP) == (sc.MIN_N, sc.MAX_STEP_C), "the station correction's own priors"
    assert O.LOOKS == {1: 20, 2: 40} and O.MIN_CALLS == 20 and O.FIRST_FORWARD_DATE == "2026-11-17" > O.DEV_TO
    assert "**17 Nov 2026**" in DOC and "first **20** valid dates" in DOC and "**40** valid" in DOC


def _errors(seed=3, cities=6, models=7, days=30, thin=("c5",)):
    import numpy as np
    rng = np.random.default_rng(seed)
    out = {}
    for c in range(cities):
        for m in range(models):
            n = 3 if f"c{c}" in thin else days
            out[(f"c{c}", m)] = list(rng.normal(0.3 * m - 0.5, 0.4 + 0.25 * m, n))
    return out


def test_the_target_is_the_study_s_fit_with_a_minimum_sample_and_bounds():
    pytest.importorskip("numpy")
    import own_w_forward as O
    import station_weights_study as W
    errors = _errors()
    t = O.target(errors)
    bias, pooled, mse, pmse = W.shrunk(errors)
    assert all(t["bias"][k] == pytest.approx(bias[k]) for k in errors), "the bias is the study's"
    assert all(O.REL_LO <= v <= O.REL_HI for v in t["rel"].values())
    # a full city: skill is precision over the city's mean precision, where no bound bites
    prec = {m: 1 / mse[("c0", m)] for m in range(7)}
    mean = sum(prec.values()) / 7
    for m in range(7):
        want = prec[m] / mean
        assert t["rel"][("c0", m)] == pytest.approx(min(O.REL_HI, max(O.REL_LO, want)))
    assert any(v in (O.REL_LO, O.REL_HI) for v in t["rel"].values()), "this fixture does reach a bound"
    # a city with 3 days: the pool's bias and the pool's skill
    assert all(t["bias"][("c5", m)] == pytest.approx(pooled[m]) for m in range(7))
    assert all(t["rel"][("c5", m)] == pytest.approx(t["pool_rel"][m]) for m in range(7))


def test_a_model_with_too_few_days_keeps_its_pool_skill_in_a_city_that_has_measured_others():
    """Codex on #510: with pool precisions equal and one model measured ten times
    better at a city, the three unmeasured models there must stay at the pool's
    skill, not be pushed to the lower bound by the measured one."""
    pytest.importorskip("numpy")
    import numpy as np
    import own_w_forward as O
    rng = np.random.default_rng(7)
    errors = {}
    for c in ("p1", "p2", "p3", "p4"):                      # the pool: four models of equal skill
        for m in range(4):
            errors[(c, m)] = list(rng.normal(0, 1.0, 400))
    for m in range(4):
        errors[("mixed", m)] = list(rng.normal(0, 1.0, 3))  # under MIN_N
    errors[("mixed", 0)] = list(rng.normal(0, 0.1, 60))     # one model measured, and far better here
    errors[("two", 0)] = list(rng.normal(0, 0.3, 60))
    errors[("two", 1)] = list(rng.normal(0, 1.5, 60))
    for m in (2, 3):
        errors[("two", m)] = list(rng.normal(0, 1.0, 2))
    t = O.target(errors)
    for m in (1, 2, 3):
        assert t["rel"][("mixed", m)] == t["pool_rel"][m], "not measured here: the pool's skill"
        assert t["bias"][("mixed", m)] == t["pool_bias"][m]
    # one measured model has nothing to be compared with: it keeps the pool's share too
    assert t["rel"][("mixed", 0)] == pytest.approx(t["pool_rel"][0], rel=0.05)
    # two measured models split their pool share by precision, inside the bounds
    assert t["rel"][("two", 0)] > 1.5 and t["rel"][("two", 1)] == O.REL_LO
    assert all(t["rel"][("two", m)] == t["pool_rel"][m] for m in (2, 3))
    # and a city with every model measured is the study's recipe, unscaled
    import station_weights_study as W
    _b, _p, mse, _pm = W.shrunk(errors)
    prec = [1 / mse[("p1", m)] for m in range(4)]
    assert [t["rel"][("p1", m)] for m in range(4)] == pytest.approx([x / (sum(prec) / 4) for x in prec])


def test_a_cell_moves_one_step_a_night():
    pytest.importorskip("numpy")
    import own_w_forward as O
    prev = {"bias": {("a", 0): 0.0, ("a", 1): 1.0, ("gone", 0): 0.7}, "rel": {("a", 0): 1.0, ("a", 1): 1.0, ("gone", 0): 1.3},
            "pool_bias": {0: 0.0, 1: 0.0}, "pool_rel": {0: 1.0, 1: 1.0}, "n": {}}
    tgt = {"bias": {("a", 0): 0.9, ("a", 1): 0.9, ("new", 0): -2.0}, "rel": {("a", 0): 1.8, ("a", 1): 0.95, ("new", 0): 0.5},
           "pool_bias": {0: 0.1, 1: 0.2}, "pool_rel": {0: 1.1, 1: 0.9}, "n": {}}
    s = O.step(tgt, prev)
    assert s["bias"][("a", 0)] == 0.25 and s["rel"][("a", 0)] == pytest.approx(1.10), "held to its step"
    assert s["bias"][("a", 1)] == 0.9 and s["rel"][("a", 1)] == 0.95, "inside the step: the target"
    assert s["bias"][("new", 0)] == -2.0 and s["rel"][("new", 0)] == 0.5, "no night before it: the target"
    assert s["bias"][("gone", 0)] == 0.7 and s["rel"][("gone", 0)] == 1.3, "no data tonight: last night's"
    assert s["held"] == {"bias": 1, "rel": 1} and s["pool_bias"] == tgt["pool_bias"]
    assert O.step(tgt, None) is tgt and O.step(None, prev) is prev


def test_the_centre_is_the_skill_weighted_mean_of_the_corrected_models():
    pytest.importorskip("numpy")
    import own_w_forward as O
    state = {"bias": {("a", 0): 1.0, ("a", 1): 0.0, ("a", 2): 0.0, ("a", 3): 0.0},
             "rel": {("a", 0): 2.0, ("a", 1): 1.0, ("a", 2): 0.5, ("a", 3): 0.5},
             "pool_bias": {0: 0.5, 1: 0.5, 2: 0.5, 3: 0.5}, "pool_rel": {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0}}
    v = [10.0, 12.0, 14.0, 16.0, None, None, None]
    assert O.centre("a", v, state) == pytest.approx((2 * 11 + 12 + 0.5 * 14 + 0.5 * 16) / 4.0)
    assert O.centre("other", v, state) == pytest.approx(13.5), "a city without cells: the pools, equal skill"
    assert O.centre("a", [10.0, 12.0, 14.0, None, None, None, None], state) is None, "under 4 models is no centre"


def _runs_and_truth(days=20):
    import datetime as dt
    import numpy as np
    rng = np.random.default_rng(5)
    runs, truth = collections.defaultdict(lambda: collections.defaultdict(list)), {}
    for i in range(days):
        d = (dt.date(2026, 9, 24) + dt.timedelta(days=i)).isoformat()
        for c in ("a", "b"):
            truth[(c, d)] = 20.0 + float(rng.normal())
            for m in range(7):
                runs[(c, d)][m].append((f"{d}T00:30:00+00:00", 0, truth[(c, d)] - 0.2 * m + float(rng.normal(0, 0.5))))
    return runs, truth


def test_a_night_reads_only_days_two_or_more_before_it():
    pytest.importorskip("numpy")
    import own_w_forward as O
    runs, truth = _runs_and_truth()
    night = "2026-10-10"
    s = O.States(runs, truth).at(night)[0]
    later = dict(truth)
    for (c, d) in truth:
        if d >= "2026-10-09":            # the day before the night, and everything after
            later[(c, d)] = truth[(c, d)] + 5.0
    assert O.States(runs, later).at(night)[0]["bias"] == s["bias"], "they are not inputs"
    earlier = dict(truth)
    earlier[("a", "2026-10-08")] += 5.0  # two days before: an input
    assert O.States(runs, earlier).at(night)[0]["bias"] != s["bias"]
    assert O.States(runs, truth).at("2026-09-25") is None, "before the first night there is no state"


def test_the_nights_run_in_order_whatever_is_asked_first():
    pytest.importorskip("numpy")
    import own_w_forward as O
    runs, truth = _runs_and_truth()
    a = O.States(runs, truth)
    last = a.at("2026-10-12")
    b = O.States(runs, truth)
    for night in ("2026-10-01", "2026-10-05", "2026-10-12"):
        b.at(night)
    assert b.at("2026-10-12")[0]["bias"] == last[0]["bias"] and b.at("2026-10-12")[0]["rel"] == last[0]["rel"]
    # and each night is one step from the night before
    for k, v in a.nights["2026-10-12"][0]["bias"].items():
        p = a.nights["2026-10-11"][0]["bias"].get(k)
        assert p is None or abs(v - p) <= O.BIAS_MAX_STEP + 1e-12
    for k, v in a.nights["2026-10-12"][0]["rel"].items():
        p = a.nights["2026-10-11"][0]["rel"].get(k)
        assert p is None or abs(v - p) <= O.REL_MAX_STEP + 1e-12
    assert O.version_of(a.at("2026-10-12"), "2026-10-12") == O.version_of(b.at("2026-10-12"), "2026-10-12")
    assert O.version_of(a.at("2026-10-12"), "2026-10-12").startswith("own_w:v1:2026-10-12:")
    assert O.version_of(a.at("2026-10-11"), "2026-10-11") != O.version_of(a.at("2026-10-12"), "2026-10-12")


class _Rec:
    """A record of `days` dates with `per` scored calls a checkpoint, each gaining `gain`."""

    def __init__(self, days, per=25, gain=0.02, thin=()):
        import datetime as dt
        self.rows = []
        for i in range(days):
            d = (dt.date(2026, 11, 17) + dt.timedelta(days=i)).isoformat()
            for cp in ("d1_eve", "morning"):
                for j in range(5 if d in thin else per):
                    self.rows.append({"city": f"c{j}", "date": d, "cp": cp, "lead": 1, "served": (1.5, 0), "market": (1.2, 1),
                                      "moved_c": 0.1, "base": (1.5, 0), "own_w_v1": (1.5 - gain - 0.001 * (j % 3), 0),
                                      "study_own_w": (1.48, 0), "check": 0.0})

    def calls(self, first, last=None):
        return list(self.rows), {}

    def station_error(self, a, b):
        return {}

    def cells(self, a, b):
        return {}

    class states:
        @staticmethod
        def at(night):
            return {}


def test_a_date_is_valid_with_enough_calls_at_both_checkpoints_once_settled():
    pytest.importorskip("numpy")
    import own_w_forward as O
    rec = _Rec(10, thin=("2026-11-19",))
    dates = O.valid_dates(rec.rows)
    assert "2026-11-19" not in dates, "5 calls a checkpoint is not a valid date"
    assert dates == ["2026-11-17", "2026-11-18", "2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"], \
        "and the last 3 days are not counted yet"
    out = O.count(rec)
    assert out["valid_dates"] == 6 and out["looks"]["1"] == {"needs": 20, "due": False}
    assert "log_loss" not in json.dumps(out) and "gain" not in json.dumps(out), "a count shows no score"


def test_a_look_is_refused_before_its_dates_and_reads_exactly_them_after():
    pytest.importorskip("numpy")
    import own_w_forward as O
    with pytest.raises(SystemExit, match="look 1 is not due: 19 valid dates of 20"):
        O.look(1, _Rec(22))
    out = O.look(1, _Rec(23))
    assert (out["valid_dates"], out["from"], out["to"]) == (20, "2026-11-17", "2026-12-06")
    assert out["calls"]["morning"]["n"] == 20 * 25 and out["verdict"] == "accepted as a proposal"
    later = O.look(1, _Rec(60))
    assert later["calls"] == out["calls"], "more dates arriving do not change what look 1 read"
    with pytest.raises(SystemExit, match="look 2 is not due"):
        O.look(2, _Rec(42))


def test_the_verdict_needs_both_calls():
    pytest.importorskip("numpy")
    import own_w_forward as O

    def s(eve, morning):
        return {"d1_eve": {"gain": {"ci90": list(eve)}}, "morning": {"gain": {"ci90": list(morning)}}}
    assert O.verdict(1, s((0.01, 0.02), (0.02, 0.04))) == "accepted as a proposal"
    assert O.verdict(1, s((-0.01, 0.02), (0.02, 0.04))) == "insufficient evidence"
    assert O.verdict(2, s((-0.01, 0.02), (0.02, 0.04))) == "insufficient evidence: the test ends"
    assert O.verdict(1, s((-0.03, -0.01), (0.02, 0.04))) == "rejected", "a loss at either call rejects it"
    assert O.verdict(2, s((0.01, 0.02), (-0.04, -0.02))) == "rejected"
