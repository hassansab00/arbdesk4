"""YES on the three best cities, NO on the three worst (section 10 of
docs/STRATEGY_STUDY_2026-11-15.md, tools/city_sides_study.py): the document says
what the committed result holds, and a city is chosen only on its earlier record."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
RESULT = json.load(open(os.path.join(ROOT, "data", "eval", "city_sides_2026-11-15", "result.json")))
DOC = open(os.path.join(ROOT, "docs", "STRATEGY_STUDY_2026-11-15.md")).read()
NAMES = {"d1_eve": "Evening before", "morning": "Morning", "noon": "Noon", "prepeak_2h": "2 h before peak",
         "prepeak_1h": "1 h before peak", "postpeak_1h": "1 h after peak"}


def _pc(x):
    return f"{100 * x:+.1f}%"


def test_the_strategy_traded_forward_is_not_shown_to_pay_at_any_call():
    for cp, c in RESULT["checkpoints"].items():
        w = c["walk"]["hit"]
        y, n, b = w["yes_on_the_3_best"], w["no_on_the_3_worst"], w["both_legs"]
        assert b["return_per_dollar"] < 0 and b["ci90"][0] < 0, cp
        assert (f"| {NAMES[cp]} | {y['n']} | {_pc(y['return_per_dollar'])} | {n['n']} | {_pc(n['return_per_dollar'])} | "
                f"{_pc(b['return_per_dollar'])} | {_pc(b['ci90'][0])} to {_pc(b['ci90'][1])} | "
                f"{c['persist']['hit_rate_rank_correlation']:+.2f} |") in DOC


def test_the_no_leg_never_paid_and_one_cell_in_24_stood_out():
    wc = RESULT["walk_cells"]
    assert wc["cells"] == 24 and wc["no_leg_cells_with_a_positive_return"] == 0
    assert wc["interval_above_0"] == ["postpeak_1h:hit:yes_on_the_3_best"]
    assert f"{wc['interval_below_0']} of the 24 had an interval wholly below zero" in DOC
    for c in RESULT["checkpoints"].values():
        for w in c["walk"].values():
            assert w["no_on_the_3_worst"]["return_per_dollar"] < 0


def test_the_six_cities_and_what_hindsight_adds():
    m = RESULT["checkpoints"]["morning"]
    for leg, side, label in (("yes_on", "yes", "YES"), ("no_on", "no", "NO")):
        assert len(m["now"][leg]) == RESULT["k"] == 3
        for x in m["now"][leg]:
            r = x[side]
            assert (f"| {x['city']} | {label} | {x['picks']} | {100 * x['hit_rate']:.1f}% | {r['mean_cost']:.3f} | "
                    f"{100 * r['win_rate']:.1f}% | {_pc(r['return_per_dollar'])} | {_pc(r['ci90'][0])} to {_pc(r['ci90'][1])} |") in DOC
    best = [x["hit_rate"] for x in m["now"]["yes_on"]]
    worst = [x["hit_rate"] for x in m["now"]["no_on"]]
    assert best == sorted(best, reverse=True) and worst == sorted(worst, reverse=True) and min(best) > max(worst)
    h, w = m["now"]["hindsight_not_a_rule"]["both_legs"], m["walk"]["hit"]["both_legs"]
    assert h["return_per_dollar"] > 0 > w["return_per_dollar"], "picked on the record they are scored on, they look good"
    assert f"both legs {_pc(h['return_per_dollar'])} ({_pc(h['ci90'][0])} to {_pc(h['ci90'][1])})" in DOC
    assert f"{_pc(w['return_per_dollar'])} ({_pc(w['ci90'][0])} to {_pc(w['ci90'][1])})" in DOC


def _trades(days=20):
    """`good` is right on its first 10 dates and wrong after; `bad` the reverse;
    six fillers are right every other day."""
    out = []
    for i in range(days):
        d = f"2026-10-{i + 1:02d}"
        for city, win in [("good", int(i < 10)), ("bad", int(i >= 10))] + [(f"f{j}", (i + j) % 2) for j in range(6)]:
            base = {"city": city, "date": d, "cp": "noon", "top": 1}
            out.append(dict(base, side="YES", price=0.5, cost=0.5, p=0.6, win=win))
            out.append(dict(base, side="NO", price=0.5, cost=0.5, p=0.4, win=1 - win))
    return out


def test_a_city_is_chosen_only_on_its_record_before_the_date():
    pytest.importorskip("numpy")
    import city_sides_study as C
    yes, no = C.sides(_trades(), "noon")
    w = C.walk(yes, no, "hit")
    # from date 9 (8 earlier picks) `good` is among the best and `bad` among the worst:
    # that is right on dates 9 and 10 and wrong on every date after
    y = w["yes_on_the_3_best"]
    assert y["dates"] == 12 and y["n"] == 36
    good = [t for t in yes if t["city"] == "good" and t["date"] >= "2026-10-09"]
    assert sum(t["win"] for t in good) == 2 and len(good) == 12
    assert w["both_legs"]["n"] == w["yes_on_the_3_best"]["n"] + w["no_on_the_3_worst"]["n"]
    # nothing trades before any city has MIN_TRADES earlier picks
    early = [t for t in _trades(8)]
    yes8, no8 = C.sides(early, "noon")
    assert C.walk(yes8, no8, "hit")["both_legs"] == {"n": 0}


def test_the_rank_and_the_correlation():
    pytest.importorskip("numpy")
    import city_sides_study as C
    hist = {"a": [(1, 0.5)] * 8, "b": [(0, -1.0)] * 8, "c": [(1, 0.2)] * 4 + [(0, -1.0)] * 4, "thin": [(1, 1.0)] * 7}
    assert C.rank(hist, "hit") == ["a", "c", "b"], "a city with 7 picks is not ranked"
    assert C.rank(hist, "yes_return") == ["a", "c", "b"]
    assert C.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert C.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert C.spearman([1, 1, 2, 2], [1, 1, 2, 2]) == pytest.approx(1.0), "ties share a rank"
