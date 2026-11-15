"""Hassan's six frozen cities (section 13 of docs/STRATEGY_STUDY_2026-11-15.md,
docs/FROZEN6_PREREG.md, tools/frozen6_study.py): the document says what the
committed result holds, the rule is the one fixed, and the trades, the ledger
and the verdict are what they are said to be."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
RESULT = json.load(open(os.path.join(ROOT, "data", "eval", "frozen6_2026-11-15", "result.json")))
CODEX = json.load(open(os.path.join(ROOT, "data", "eval", "codex_test_2026-11-12", "result.json")))
SIDES = json.load(open(os.path.join(ROOT, "data", "eval", "city_sides_2026-11-15", "result.json")))
DOC = open(os.path.join(ROOT, "docs", "STRATEGY_STUDY_2026-11-15.md")).read()
PREREG = open(os.path.join(ROOT, "docs", "FROZEN6_PREREG.md")).read()


def _pc(x):
    return f"{100 * x:+.1f}%"


def _usd(x):
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def test_the_rule_is_the_one_fixed():
    pytest.importorskip("numpy")
    import frozen6_study as F
    assert F.YES_CITIES == ("shanghai", "helsinki", "amsterdam") and F.NO_CITIES == ("houston", "austin", "sao_paulo")
    assert RESULT["cities"] == {"yes": list(F.YES_CITIES), "no": list(F.NO_CITIES)}
    assert F.FIRST_FORWARD_DATE == "2026-11-16" > RESULT["last_date"] and F.LOOKS == (23, 46) and F.OFFICIAL == "22:36Z"
    flat = " ".join(PREREG.split())
    for text in ("| YES | Shanghai, Helsinki, Amsterdam |", "| NO | Houston, Austin, Sao Paulo |", "**16 Nov 2026**",
                 "**First look: 23 valid dates.**", "**Last look: 46 valid dates.**", "**The official capture is 22:36Z**"):
        assert text in flat, text


def test_profit_to_date_is_the_codex_trades_priced_four_ways():
    X = RESULT["codex_test_to_date"]
    assert X["trades"] == 18 and X["listed_results"]["provisional"] == 5
    for k in ("at_the_evening_calls_book", "at_the_listed_prices_where_given", "at_the_live_book_21:36Z",
              "at_the_live_book_22:36Z", "at_the_live_book_23:36Z"):
        v = X[k]
        assert v["trades_priced"] == 18 and v["staked"] == 1800 and v["profit_of_100_a_trade"] > 0
        assert f"| 18 | {_usd(v['profit_of_100_a_trade'])} on $1,800 | {_pc(v['return_per_dollar'])} |" in DOC, k
    assert X["at_the_live_book_22:36Z"]["return_per_dollar"] < X["at_the_evening_calls_book"]["return_per_dollar"] * 0.7, \
        "the live book in the window pays well under the frozen call book"
    assert abs(X["at_the_evening_calls_book"]["return_per_dollar"] - CODEX["replay"]["flat_stake_pnl_per_dollar"]) < 0.001, \
        "the same figure as the Codex check's flat-stake return"


def test_the_six_cities_are_break_even_at_the_live_book_and_better_than_the_average_city():
    W = RESULT["window"]
    for label, v in W.items():
        six, every = v["six"]["both"], v["every_city"]["both"]
        assert six["ci90"][0] < 0 < six["ci90"][1], f"{label}: not separable from zero"
        assert every["ci90"][1] < 0, f"{label}: the average city loses"
        assert six["return_per_dollar"] > every["return_per_dollar"] + 0.05
        assert (f"| {six['dates']} | {six['n']} | {_pc(six['return_per_dollar'])} | {_pc(six['ci90'][0])} to {_pc(six['ci90'][1])} | "
                f"{_usd(v['six']['profit_of_100_a_trade'])} | {_pc(every['return_per_dollar'])} |") in DOC, label
    c = RESULT["call"]
    assert (f"| {c['six']['both']['dates']} | {c['six']['both']['n']} | {_pc(c['six']['both']['return_per_dollar'])} | "
            f"{_pc(c['six']['both']['ci90'][0])} to {_pc(c['six']['both']['ci90'][1])} | {_usd(c['six']['profit_of_100_a_trade'])} | "
            f"{_pc(c['every_city']['both']['return_per_dollar'])} |") in DOC
    assert c["every_city"]["both"]["n"] > 4000 and c["every_city"]["both"]["ci90"][1] < 0
    assert abs(SIDES["checkpoints"]["d1_eve"]["persist"]["hit_rate_rank_correlation"]) < 0.1, "a city's rank does not carry forward"
    assert "rank\n  correlation +0.06" in DOC


def test_by_city_and_the_two_that_carry_it():
    for k, v in RESULT["call"]["by_city"].items():
        city, side = k.split(":")
        w = RESULT["window"]["22:36Z"]["by_city"][k]
        assert (f"| {city.replace('_', ' ').title()} | {side} | {v['n']} | {_pc(v['return_per_dollar'])} | {w['n']} | "
                f"{_pc(w['return_per_dollar'])} |") in DOC, k
    w = {k: v["return_per_dollar"] for k, v in RESULT["window"]["22:36Z"]["by_city"].items()}
    assert [k for k, v in w.items() if v < 0] == ["helsinki:YES", "shanghai:YES", "sao_paulo:NO"]
    assert max(w, key=w.get) == "houston:NO"


def test_the_trades_and_what_they_cost():
    pytest.importorskip("numpy")
    import frozen6_study as F
    t = F.trade("YES", 0.5, True, "2026-11-16")
    assert t["cost"] == pytest.approx(0.5125) and t["win"] == 1
    calls = {("shanghai", "2026-11-16"): {"top": "a", "winner": "a", "settled": None, "book": {"a": {"ask": 0.4, "bid": 0.35}}},
             ("houston", "2026-11-16"): {"top": "b", "winner": "c", "settled": None, "book": {"b": {"ask": 0.2, "bid": 0.15}}},
             ("tokyo", "2026-11-16"): {"top": "a", "winner": "a", "settled": None, "book": {"a": {"ask": 0.4, "bid": 0.35}}}}
    six = {"yes": set(F.YES_CITIES), "no": set(F.NO_CITIES)}
    out = F.call_trades(calls, six)
    assert [(x["city"], x["price"], x["win"]) for x in out["yes"]] == [("shanghai", 0.4, 1)], "YES is the ask; Tokyo is not one of the six"
    assert [(x["city"], round(x["price"], 2), x["win"]) for x in out["no"]] == [("houston", 0.85, 1)], "NO is one less the bid, and wins when the pick loses"
    books = {("shanghai", "2026-11-16"): {"22:36Z": [0.5, 0.6, 10.0, 7.0]}, ("houston", "2026-11-16"): {"22:36Z": [0.1, 0.9, 12.0, 8.0]}}
    w = F.window_trades(calls, books, six, "22:36Z")
    assert w["yes"][0]["price"] == 0.6 and w["yes"][0]["size"] == 7.0 and w["no"][0]["price"] == pytest.approx(0.9) and w["no"][0]["size"] == 12.0
    assert F.window_trades(calls, books, six, "21:36Z") == {"yes": [], "no": []}, "no capture, no trade"
    s = F.summary(w)
    assert s["staked_at_100_a_trade"] == 200 and s["profit_of_100_a_trade"] == pytest.approx(100 * (1 / S_cost(0.6) - 1) + 100 * (1 / S_cost(0.9) - 1), abs=0.01)


def S_cost(p):
    return p + 0.05 * p * (1 - p)


def test_the_ledger_counts_valid_dates_only_and_the_verdict_follows_the_interval():
    pytest.importorskip("numpy")
    import frozen6_study as F
    cities = list(F.YES_CITIES) + list(F.NO_CITIES)
    calls, books = {}, {}
    for date, n in (("2026-11-15", 6), ("2026-11-16", 6), ("2026-11-17", 5)):    # 15 Nov is before the forward dates
        for city in cities[:n]:
            calls[(city, date)] = {"top": "a", "winner": "a" if city in F.YES_CITIES else "b", "settled": None, "book": {}}
            books[(city, date)] = {"22:36Z": [0.4, 0.5, 10.0, 10.0]}
    out = F.ledger(calls, books)
    assert [d["date"] for d in out["days"]] == ["2026-11-16", "2026-11-17"] and out["valid_dates"] == 1
    assert out["days"][0]["valid"] and not out["days"][1]["valid"] and out["days"][0]["trades"] == 6
    assert out["days"][1]["cumulative"] == pytest.approx(out["days"][0]["profit_of_100_a_trade"] + out["days"][1]["profit_of_100_a_trade"])
    assert out["looks"] == {"23": {"needs": 23, "due": False}, "46": {"needs": 46, "due": False}}
    assert F.verdict({"ci90": [0.01, 0.2]}) == "positive research evidence"
    assert F.verdict({"ci90": [-0.2, -0.01]}) == "failed" and F.verdict({"ci90": [-0.1, 0.1]}) == "insufficient evidence"
    assert RESULT["forward"]["days"] == [] and RESULT["forward"]["valid_dates"] == 0, "no forward date has settled yet"
