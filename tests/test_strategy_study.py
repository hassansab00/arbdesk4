"""The strategy study of 15 Nov 2026 (docs/STRATEGY_STUDY_2026-11-15.md): what
the document says is what the committed result holds, and the replay's rules
read nothing they could not have known."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
RESULT = json.load(open(os.path.join(ROOT, "data", "eval", "strategy_study_2026-11-15", "result.json")))
DOC = open(os.path.join(ROOT, "docs", "STRATEGY_STUDY_2026-11-15.md")).read()


def _pc(x):
    return f"{100 * x:+.1f}%"


def test_no_edge_rule_paid_on_the_development_dates():
    e = RESULT["edge_development"]
    assert e["cells_with_interval_above_0"] == 0 and e["cells_with_80_trades"] == 132
    assert e["best_cell"]["ci90"][1] < 0, "even the best variant lost"
    assert f"tried {e['cells_with_80_trades']} variants" in DOC
    assert f"{e['cells_with_interval_below_0']} had one wholly below zero" in DOC
    assert all(v["return_per_dollar"] < 0 for v in e["edge_0.06_all_prices"].values())


def test_the_market_alone_and_the_top_pick_lose():
    for side, bands in RESULT["market_alone_all_dates"].items():
        for band, v in bands.items():
            assert v["ci90"][0] <= 0, f"{side} {band}: no price range is shown to pay"
            assert _pc(v["return_per_dollar"]) in DOC
    for cp, v in RESULT["pick_all_dates"].items():
        assert v["ci90"][1] < 0, cp
        assert f"{_pc(v['return_per_dollar'])} | {_pc(v['ci90'][0])} to {_pc(v['ci90'][1])}" in DOC


def test_cities_chosen_beforehand_do_not_pay_and_hindsight_flatters():
    for name, v in RESULT["cities_all_dates"].items():
        assert v["ten_best_in_hindsight_not_a_rule"]["ci90"][0] > 0, name
        for rule in ("cities_that_paid_so_far", "ten_best_so_far"):
            assert v[rule]["ci90"][0] < 0, f"{name} {rule}: not shown to pay"
            assert _pc(v[rule]["return_per_dollar"]) in DOC


def test_the_postpeak_rule_did_not_repeat_on_the_holdout():
    p = RESULT["postpeak"]
    assert p["development"]["ci90"][0] > 0 and p["holdout"]["ci90"][0] < 0 < p["holdout"]["ci90"][1]
    assert p["both"]["ci90"][0] < 0, "not separable from zero over both"
    assert RESULT["development_to"] < RESULT["holdout_from"]
    for v in p.values():
        assert f"{_pc(v['return_per_dollar'])} | {_pc(v['ci90'][0])} to {_pc(v['ci90'][1])}" in DOC


def test_no_ladder_pays_for_itself():
    lad = RESULT["ladder"]
    assert lad["calls_where_every_yes_costs_under_1"] == 0 and lad["lowest_cost_of_every_yes"] > 1
    assert lad["calls_where_every_no_costs_under_its_payout"] <= 1


def test_the_cost_and_the_return():
    np = pytest.importorskip("numpy")  # noqa: F841
    import strategy_study as S
    assert S.cost(0.5) == pytest.approx(0.5125) and S.cost(0.9) == pytest.approx(0.9045)
    r = S.result([{"date": "d1", "win": 1, "cost": 0.5}, {"date": "d2", "win": 0, "cost": 0.5}])
    assert (r["n"], r["dates"], r["return_per_dollar"], r["win_rate"]) == (2, 2, 0.0, 0.5)
    assert S.result([]) == {"n": 0}


def _city_trades(settle_h):
    import datetime as dt
    ts = []
    for i in range(16):   # "good" wins its first 8 dates and loses every date after; "bad" the reverse
        day = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=i)
        for city, win in (("good", int(i < 8)), ("bad", int(i >= 8))):
            ts.append({"city": city, "date": day.date().isoformat(), "win": win, "cost": 0.5,
                       "decided_at": (day + dt.timedelta(hours=9)).isoformat(),
                       "settled_at": (day + dt.timedelta(hours=settle_h)).isoformat()})
    return ts


def test_a_city_is_chosen_only_on_its_record_before_the_date():
    np = pytest.importorskip("numpy")  # noqa: F841
    import strategy_study as S
    r = S.by_city(_city_trades(settle_h=30))     # settled at 06:00 the next day, before its 09:00 call
    paid = r["cities_that_paid_so_far"]
    assert paid["n"] == 8 and paid["win_rate"] == 0.0, "good is traded only after its 8 wins, and loses them all"
    assert r["all_cities"]["n"] == 32


def test_an_outcome_not_yet_settled_at_the_call_is_not_part_of_the_record():
    """Codex on #512: an earlier target date is not an earlier outcome."""
    np = pytest.importorskip("numpy")  # noqa: F841
    import strategy_study as S
    r = S.by_city(_city_trades(settle_h=36))     # settled at 12:00 the next day, after its 09:00 call
    paid = r["cities_that_paid_so_far"]
    assert (paid["n"], paid["dates"]) == (7, 7), "good's 8th win is known a day later, so it trades one date fewer"
    assert paid["win_rate"] == 0.0
