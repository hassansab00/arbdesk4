"""The evening-before call, where Hassan trades (section 11 of
docs/STRATEGY_STUDY_2026-11-15.md, tools/evening_rules_study.py): the document
says what the committed result holds, and the picks, prices and rules are what
they are said to be."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
RESULT = json.load(open(os.path.join(ROOT, "data", "eval", "evening_rules_2026-11-15", "result.json")))
SIDES = json.load(open(os.path.join(ROOT, "data", "eval", "city_sides_2026-11-15", "result.json")))
DOC = open(os.path.join(ROOT, "docs", "STRATEGY_STUDY_2026-11-15.md")).read()


def _pc(x):
    return f"{100 * x:+.1f}%"


def test_the_market_is_right_where_the_model_and_the_market_disagree():
    a = RESULT["agreement"]
    assert a["agree"]["calls"] + a["disagree"]["calls"] == RESULT["calls"]
    assert a["disagree"]["favourite_right"] > a["disagree"]["model_right"] + 0.15
    assert f"| They agree | {a['agree']['calls']:,} | {100 * a['agree']['pick_right']:.1f}% |" in DOC
    assert (f"| They disagree | {a['disagree']['calls']:,} | {100 * a['disagree']['model_right']:.1f}% | "
            f"{100 * a['disagree']['favourite_right']:.1f}% |") in DOC
    assert f"on {100 * a['share_agree']:.1f}% of calls" in DOC


def test_the_nine_rules_are_the_document_s():
    assert len(RESULT["rules"]) == 9
    for v in RESULT["rules"].values():
        b = v["both"]
        assert (f"| {v['rule']} | {b['n']:,} | {_pc(b['return_per_dollar'])} | {_pc(b['ci90'][0])} to {_pc(b['ci90'][1])} | "
                f"{_pc(v['development']['return_per_dollar'])} | {_pc(v['holdout']['return_per_dollar'])} |") in DOC
        assert b["n"] == v["development"]["n"] + v["holdout"]["n"]
    assert RESULT["development_to"] < RESULT["holdout_from"]


def test_no_rule_is_shown_to_pay_and_one_clearly_loses():
    above = [k for k, v in RESULT["rules"].items() if v["both"]["ci90"][0] > 0]
    assert above == [], "no interval is above zero"
    bad = RESULT["rules"]["disagree_yes_model"]
    assert all(bad[k]["ci90"][1] < 0 for k in ("development", "holdout", "both")), "YES against the market loses in both halves"
    best = RESULT["rules"]["agree_and_sure_yes"]
    assert max(RESULT["rules"], key=lambda k: RESULT["rules"][k]["both"]["return_per_dollar"]) == "agree_and_sure_yes"
    assert best["development"]["return_per_dollar"] > 0 and best["holdout"]["return_per_dollar"] > 0
    assert best["both"]["ci90"][0] < 0, "and its interval still includes zero"
    z = RESULT["sizing"]
    assert z["trades"] == best["both"]["n"] and z["trades_for_80pc_power_at_this_return"] > 5 * z["trades"]
    assert f"would need {z['trades_for_80pc_power_at_this_return']} trades for 80% power" in DOC
    assert f"about {z['calendar_days_at_this_rate']} days" in DOC and f"fires {z['trades_a_calendar_day']} times a calendar day" in DOC


def test_a_taker_starts_behind_a_fair_price():
    c = RESULT["costs"]
    mid, ask = c["at_the_midpoint_no_fee"], c["at_the_ask_with_the_fee"]
    assert mid["n"] == ask["n"] and mid["win_rate"] == ask["win_rate"], "the same calls"
    assert mid["ci90"][0] < 0 < mid["ci90"][1], "fair at the midpoint"
    assert ask["return_per_dollar"] < mid["return_per_dollar"] - 0.04
    for v in (mid, ask):
        assert f"| {_pc(v['return_per_dollar'])} | {_pc(v['ci90'][0])} to {_pc(v['ci90'][1])} |" in DOC
    assert f"mean spread is {c['mean_spread']:.4f} and the mean fee {c['mean_fee_a_share']:.4f}" in DOC


def test_98_percent_over_three_dates_is_not_what_the_record_gives():
    t = RESULT["three_and_three"]
    for label, m in (("Hit rate", "hit"), ("Past YES return", "yes_return")):
        r, w = t[m]["replay_12_to_14_nov"], t[m]["three_date_runs"]
        assert r["return_per_dollar"] < 0.98
        assert (f"| {label} | {r['trades']} | {r['won']} | {_pc(r['return_per_dollar'])} | {_pc(w['lowest'])} | "
                f"{_pc(w['median'])} | {_pc(w['highest'])} | {w['at_or_over_0.98']} |") in DOC
        assert w["at_or_over_0.98"] <= 1 and w["median"] < 0
        assert t[m]["swing_of_one_trade"] > 1.0, "one trade swings by about its stake"
    # the same walk as section 10's, on the same record
    for m in ("hit", "yes_return"):
        assert SIDES["checkpoints"]["d1_eve"]["walk"][m]["both_legs"]["dates"] == t[m]["three_date_runs"]["runs"] + 2


def test_the_picks_and_the_prices():
    pytest.importorskip("numpy")
    import evening_rules_study as E
    assert E.price_of({"ask": 0.6, "bid": 0.5}) == pytest.approx(0.55)
    assert E.price_of({"ask": 0.6, "bid": None}) == 0.6 and E.price_of({"ask": None, "bid": 0.4}) == 0.4
    assert E.price_of(None) == -1 and E.price_of({}) == -1
    book = {"a": {"ask": 0.30, "bid": 0.26}, "b": {"ask": 0.62, "bid": 0.58}, "c": {"ask": 0.12, "bid": None}}
    r = E.call("x", "2026-10-20", {"a": 0.55, "b": 0.35, "c": 0.10}, book, "b")
    assert (r["agree"], r["model_hit"], r["favourite_hit"], r["p"]) == (False, 0, 1, 0.55)
    assert r["yes_top"]["price"] == 0.30 and r["yes_top"]["win"] == 0
    assert r["no_top"]["price"] == pytest.approx(0.74) and r["no_top"]["win"] == 1, "NO costs 1 less the bid"
    assert r["yes_fav"]["price"] == 0.62 and r["yes_fav"]["win"] == 1 and r["no_fav"]["win"] == 0
    assert E.call("x", "d", {"a": 0.6, "c": 0.4}, book, "a")["agree"] is True
    assert E.call("x", "d", {"c": 0.9, "a": 0.1}, book, "a")["no_top"] is None, "no bid, no NO"


def test_each_rule_takes_the_trade_it_names():
    pytest.importorskip("numpy")
    import evening_rules_study as E
    book = {"a": {"ask": 0.30, "bid": 0.26}, "b": {"ask": 0.62, "bid": 0.58}}
    differ = E.call("x", "d", {"a": 0.55, "b": 0.45}, book, "b")
    agree = E.call("x", "d", {"b": 0.55, "a": 0.45}, book, "b")
    weak = E.call("x", "d", {"b": 0.49, "a": 0.30, "c": 0.21}, book, "b")    # agree, model under 50%
    assert weak["agree"] and weak["p"] == 0.49
    take = {k: fn for k, (_label, fn) in E.RULES.items()}
    assert take["agree_yes"](differ) is None and take["agree_yes"](agree) is agree["yes_top"]
    assert take["disagree_yes_favourite"](differ) is differ["yes_fav"] and take["disagree_yes_favourite"](agree) is None
    assert take["disagree_no_model"](differ) is differ["no_top"] and take["disagree_yes_model"](differ) is differ["yes_top"]
    assert take["disagree_no_favourite"](differ) is differ["no_fav"] and take["agree_no"](agree) is agree["no_top"]
    assert take["model_sure_yes"](differ) is differ["yes_top"], "50% or more, whatever the market says"
    assert take["agree_and_sure_yes"](differ) is None and take["agree_and_sure_yes"](agree) is agree["yes_top"]
    assert take["agree_and_sure_yes"](weak) is None and take["agree_yes"](weak) is weak["yes_top"]
    assert take["favourite_yes"](differ) is differ["yes_fav"]
