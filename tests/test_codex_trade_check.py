"""Hassan's Codex test, 12-14 Nov (section 12 of docs/STRATEGY_STUDY_2026-11-15.md,
tools/codex_trade_check.py): the document says what the committed result holds,
the pasted list parses as it reads, and the staking and the walk are what they
are said to be."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
OUT = os.path.join(ROOT, "data", "eval", "codex_test_2026-11-12")
RESULT = json.load(open(os.path.join(OUT, "result.json")))
LIST = json.load(open(os.path.join(OUT, "trades.json")))
DOC = open(os.path.join(ROOT, "docs", "STRATEGY_STUDY_2026-11-15.md")).read()


def _pc(x):
    return f"{100 * x:+.1f}%"


def test_the_listed_trades_check_out_against_the_record():
    s = RESULT["summary"]
    assert RESULT["trades"] == len(LIST["trades"]) == 18
    assert s["price_is_the_evening_book"] == 14 and s["priced_trades"] == 15
    assert s["settled_bucket_is_the_venues"] == 15 and s["settled_bucket_differs_from_the_venues"] == 0
    assert s["unsettled_in_the_record"] == 3
    assert s["shares_off_by_more_than_a_cent"] == 0 and s["pnl_off_by_more_than_a_cent"] == 0
    assert s["bucket_is_the_models_pick"] == 16
    the_other = [c for c in RESULT["checks"] if not c["is_model_top"]]
    assert {(c["city"], c["date"]) for c in the_other} == {("helsinki", "2026-11-14"), ("warsaw", "2026-11-14")}
    assert all(c["is_market_favourite"] for c in the_other), "in both, the market's favourite"
    shanghai = next(c for c in RESULT["checks"] if c["city"] == "shanghai" and c["date"] == "2026-11-14")
    assert shanghai["price_is"].startswith("the morning call's book") and shanghai["recorded_price"] == 0.52
    for text in (f"{s['price_is_the_evening_book']} of the {s['priced_trades']} listed prices", f"${s['fees_not_in_the_list']:.2f}",
                 f"{s['settled_bucket_is_the_venues']} of {s['settled_bucket_is_the_venues']} trades the venue has settled",
                 f"The other {s['bucket_is_the_models_pick']} of 18"):
        assert text in DOC, text


def test_the_stake_is_one_seventh_and_a_half_of_the_equity():
    days = RESULT["listed"]["days"]
    assert [d["listed_stake"] for d in days] == [4.0, 5.57, 8.35]
    assert [d["stake_from_equity"] for d in days] == [4.0, 5.56, 8.34], "30, 41.73 and 62.58 divided by 7.5"
    assert days[0]["listed_pnl"] == 11.73 and days[1]["listed_pnl"] == 20.85
    assert days[2]["trades_with_a_listed_pnl"] == 3 and RESULT["listed"]["trades_without_a_listed_price"] == 3
    assert f"{days[0]['listed_pnl']:+.2f} on 12 Nov and {days[1]['listed_pnl']:+.2f} on 13 Nov" in DOC


def test_the_scenarios_and_what_moves_them():
    R = RESULT["replay"]
    sc = {x["scenario"]: x for x in R["scenarios"]}
    base = R["scenarios"][0]
    assert 1.0 < base["return_on_start"] < 1.5, "the +98% is reproduced at the recorded book"
    for x in R["scenarios"]:
        days = " / ".join(f"{v['pnl']:+.2f}" for v in x["days"])
        assert f"| {days} | ${x['equity']:.2f} | {_pc(x['return_on_start'])} | {_pc(x['pnl_per_dollar_staked'])} |" in DOC
    ba = next(x for k, x in sc.items() if k.startswith("Buenos Aires"))
    top = next(x for k, x in sc.items() if k.startswith("the two trades"))
    best = next(x for k, x in sc.items() if k.startswith("without the best"))
    assert ba["return_on_start"] < 0.9 < base["return_on_start"], "an unsettled outcome moves it past +85%"
    assert top["return_on_start"] < base["return_on_start"] / 3, "on the model's own pick the three days return about a quarter"
    assert best["equity"] < base["equity"] - 15 and 0.3 < R["best_trade"]["share_of_all_profit"] < 0.4
    assert R["best_trade"]["trade"] == ["shanghai", "2026-11-12"]
    assert f"**One trade is {100 * R['best_trade']['share_of_all_profit']:.1f}% of the profit:**" in DOC


def test_the_rule_as_described_is_not_shown_to_pay_and_the_three_days_are_its_best():
    F = RESULT["form"]
    both = {k: v["both"] for k, v in F["windows"].items()}
    assert set(both) == {"5", "7", "10", "14", "21", "28", "all"}
    assert all(b["ci90"][0] < 0 for b in both.values()), "no window is separable from zero in the positive direction"
    assert both["14"]["ci90"][1] < 0, "14 days is wholly below it"
    assert both["all"]["return_per_dollar"] < 0 and both["all"]["ci90"][0] < 0 < both["all"]["ci90"][1] + 0.2, "all history, as Hassan describes it"
    for k, v in F["windows"].items():
        assert f"| {v['both']['n']} | {_pc(v['both']['return_per_dollar'])} | {_pc(v['both']['ci90'][0])} to {_pc(v['both']['ci90'][1])} |" in DOC, k
    assert f"{_pc(both['all']['return_per_dollar'])} a dollar over {both['all']['n']} trades" in DOC
    rt = F["random_tie_breaks"]
    assert rt["seeds"] == 200 and rt["both_mean"] < 0 and rt["share_of_seeds_above_0"] < 0.5
    assert f"{100 * rt['share_of_seeds_above_0']:.1f}% of tie-breaks end above zero" in DOC
    t = F["three_date_runs"]
    assert t["nov_12_to_14"]["rank_among_runs"] == 1 and t["best"]["from"] == "2026-11-12" and t["best"]["to"] == "2026-11-14"
    assert t["median"] < 0 and t["share_above_0"] < 0.5
    assert f"{t['nov_12_to_14']['return_per_dollar'] * 100:+.1f}% (rank 1, {t['nov_12_to_14']['trades']} trades) is the highest" in DOC
    ten = F["fit_of_the_six_cities"]
    for d in ten.values():
        assert max(d["yes_positions_from_best"]) <= 6 and max(d["no_positions_from_worst"]) <= 7, "a ten-day window puts them at the top and bottom"
        assert d["yes_in_default_top3"] == 2 and d["no_in_default_bottom3"] == 2
    assert sum(d["yes_in_default_top3"] + d["no_in_default_bottom3"] for d in ten.values()) == 12
    allh = F["fit_of_the_six_cities_all_history"]
    assert any(max(d["yes_positions_from_best"]) > 15 or max(d["no_positions_from_worst"]) > 15 for d in allh.values()), \
        "all history leaves some of the six cities mid-table"
    for dd, v in allh.items():
        assert (f"{dd[5:]}: YES at {', '.join(map(str, v['yes_positions_from_best']))} from the best, "
                f"NO at {', '.join(map(str, v['no_positions_from_worst']))} from the worst") in DOC


def test_what_existed_at_the_selection_time():
    pytest.importorskip("numpy")
    import codex_trade_check as X
    import datetime as dt
    Z = RESULT["at_the_selection_time"]
    assert X.selection_time("2026-11-13") == dt.datetime(2026, 11, 12, 21, 30, tzinfo=dt.timezone.utc), "23:30 Beirut, UTC+2"
    assert Z["trades_whose_listed_call_is_after_it"] == 10
    assert Z["cities_with_a_later_call"] == ["austin", "buenos_aires", "houston", "miami"]
    assert Z["available_calls"] == {"d1_eve": 8, "day_ahead": 10}
    late = [t for t in Z["trades"] if t["listed_call_is_after_the_selection"]]
    assert {t["city"] for t in late} == set(Z["cities_with_a_later_call"]) and all(t["available_call"] == "day_ahead" for t in late)
    wrong = {(t["city"], t["date"]) for t in Z["trades"] if not t["listed_bucket_is_the_models_pick"]}
    assert wrong == {("buenos_aires", "2026-11-13"), ("houston", "2026-11-13"), ("helsinki", "2026-11-14"), ("warsaw", "2026-11-14")}
    assert Z["listed_bucket_is_the_models_pick_at_that_call"] == 14
    L, M = Z["replays"]["listed"], Z["replays"]["top"]
    for r in (L, M):
        assert r["trades_priced"] == 18
        assert f"| {r['trades_priced']} | {r['won']} | ${r['day_end_staking']['equity']:.2f}, {_pc(r['day_end_staking']['return_on_start'])} | " \
               f"${r['settlement_aware']['equity']:.2f}, {_pc(r['settlement_aware']['return_on_start'])}, " in DOC
    assert M["day_end_staking"]["return_on_start"] < L["day_end_staking"]["return_on_start"] / 3, "on the model's pick it is far less"
    assert M["day_end_staking"]["days"][2]["pnl"] < 0, "and 14 Nov loses"
    assert 0 < M["settlement_aware"]["return_on_start"] < L["settlement_aware"]["return_on_start"]
    flat = " ".join(DOC.split())
    assert "Selection is night time beirut time 23:30" in flat and "21:30Z" in flat


def test_settlement_aware_replay_is_in_the_document_and_cannot_beat_the_day_end_convention():
    R = RESULT["replay"]
    a = R["settlement_aware"]["as_listed"]
    assert a["orders"] == 18 and a["orders_cut_to_the_cash_in_hand"] >= 1 and a["smallest_stake"] == 0.0
    assert a["equity"] < R["scenarios"][0]["equity"], "a day's profit cannot be spent before it settles"
    assert 1.0 < a["return_on_start"] < R["scenarios"][0]["return_on_start"]
    assert R["settlement_aware"]["buenos_aires_on_14_nov_as_a_loss"]["equity"] == a["equity"], "an order the cash cannot cover has no outcome"
    assert f"${a['equity']:.2f}, {_pc(a['return_on_start'])} on $30" in DOC
    assert f"{a['orders_cut_to_the_cash_in_hand']} of the {a['orders']} orders are cut to the cash" in DOC
    assert "Codex on #515" in DOC


def test_settlement_aware_account_uses_only_what_has_settled():
    pytest.importorskip("numpy")
    import datetime as dt
    import codex_trade_check as X
    t0 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    h = lambda n: t0 + dt.timedelta(hours=n)   # noqa: E731
    # one winning order settles at hour 10; the next is placed at hour 5 (before) and the one after at hour 12 (after)
    early = X.settlement_aware([(h(0), h(10), 0.5, True), (h(5), h(20), 0.5, True)])
    late = X.settlement_aware([(h(0), h(10), 0.5, True), (h(12), h(20), 0.5, True)])
    assert early["stakes"][1] == pytest.approx(4.0), "placed before the first settles: realised equity is still $30"
    assert late["stakes"][1] > early["stakes"][1], "placed after it settles: the profit is in the stake"
    assert late["equity"] > early["equity"]
    # the cash: 30 / 7.5 = 4 an order; eight orders at once want 32 and the last ones are cut
    broke = X.settlement_aware([(h(0), h(50), 0.5, True)] * 9)
    assert broke["orders_cut_to_the_cash_in_hand"] == 2 and broke["stakes"][-2:] == [2.0, 0.0] and sum(broke["stakes"]) == pytest.approx(30.0)
    # a loss is the stake, a win pays stake / cost
    one_win = X.settlement_aware([(h(0), h(1), 0.5, True)])
    one_loss = X.settlement_aware([(h(0), h(1), 0.5, False)])
    assert one_win["equity"] == pytest.approx(30 - 4.0 + 4.0 / 0.5125, abs=0.01) and one_loss["equity"] == pytest.approx(26.0, abs=0.01)


def test_the_recording_window_prices_the_same_trades_at_the_live_book():
    pytest.importorskip("numpy")
    import datetime as dt
    import codex_trade_check as X
    W = RESULT["at_the_recording_window"]
    a, b = X.window_of("2026-11-13")
    assert (a, b) == (dt.datetime(2026, 11, 12, 21, 0, tzinfo=dt.timezone.utc), dt.datetime(2026, 11, 13, 0, 0, tzinfo=dt.timezone.utc)), \
        "23:00 to 02:00 Beirut, UTC+2"
    assert W["captures"] == ["21:36Z", "22:36Z", "23:36Z"] and len(W["trades"]) == 18
    base = RESULT["replay"]["scenarios"][0]
    for at, v in W["replays"].items():
        assert v["trades_priced"] == 18, "the three unpriced 14 Nov NO trades are priced from the live book"
        assert v["day_end_staking"]["pnl_per_dollar_staked"] < base["pnl_per_dollar_staked"] * 0.7, at
        assert 0.3 < v["day_end_staking"]["return_on_start"] < base["return_on_start"] * 0.6, at
        assert v["settlement_aware"]["return_on_start"] < 0.2 and v["settlement_aware"]["orders_cut_to_the_cash_in_hand"] >= 3, at
        assert (f"| {v['trades_priced']} | ${v['day_end_staking']['equity']:.2f}, {_pc(v['day_end_staking']['return_on_start'])} | "
                f"{_pc(v['day_end_staking']['pnl_per_dollar_staked'])} | ${v['settlement_aware']['equity']:.2f}, "
                f"{_pc(v['settlement_aware']['return_on_start'])}, {v['settlement_aware']['orders_cut_to_the_cash_in_hand']} orders cut | "
                f"{v['touch_smaller_than_a_4_dollar_order']} |") in DOC, at
    sh = W["shanghai_12_nov"]
    assert sh["listed"] == 0.22 and all(p > 0.4 for p in sh["price_at"].values())
    assert f"was {sh['price_at']['21:36Z']:.2f}, {sh['price_at']['22:36Z']:.2f} and {sh['price_at']['23:36Z']:.2f}" in " ".join(DOC.split())
    listed = {(t["city"], t["date"]): t for t in W["trades"]}
    assert listed[("amsterdam", "2026-11-12")]["price_at"]["21:36Z"] == 0.63, "some prices did not move"
    assert listed[("warsaw", "2026-11-14")]["listed_price"] is None and all(listed[("warsaw", "2026-11-14")]["price_at"].values())


def test_the_dates_a_test_needs():
    z = RESULT["form"]["sizing"]
    n = z["dates_for_80pc_power"]
    assert n["true_return_+0.35"] < n["true_return_+0.20"] < n["true_return_+0.10"]
    assert n["true_return_+0.10"] == pytest.approx((2.4865 * z["sd_of_a_dates_mean_return"] / 0.10) ** 2, abs=1.5)
    assert f"{n['true_return_+0.35']} dates if its true return is +35% a dollar" in DOC and f"{n['true_return_+0.10']} dates at +10%" in DOC


def test_the_pasted_list_parses_as_it_reads():
    pytest.importorskip("numpy")
    import codex_trade_check as X
    assert X.number("−$4.00") == -4.0 and X.number("+$14.18") == 14.18 and X.number("98.6¢") == pytest.approx(0.986)
    assert X.number("63¢") == pytest.approx(0.63) and X.number("Unknown") is None
    assert X.bucket_lo("74–75°F") == 74.0 and X.bucket_lo("10°C") == 10.0 and X.bucket_lo("68–69°F") == 68.0
    ts = X.load_trades()
    assert len(ts) == 18 and ts[0]["date"] == "2026-11-12" and ts[0]["city"] == "amsterdam" and ts[1]["city"] == "buenos_aires"
    assert sum(t["provisional"] for t in ts) == 5 and sum(t["price"] is None for t in ts) == 3
    assert [t["won"] for t in ts if t["city"] == "austin"] == [True, False]


def test_profit_and_equity():
    pytest.importorskip("numpy")
    import codex_trade_check as X
    assert X.pnl(0.5, 4.0, False) == -4.0
    assert X.pnl(0.5, 4.0, True, fee=False) == pytest.approx(4.0)          # 8 shares at 0.50, pays 8
    assert X.pnl(0.5, 4.0, True) == pytest.approx(4.0 / 0.5125 - 4.0)      # the fee raises the cost to 0.5125
    path = X.equity_path([[(0.5, True)] * 6, [(0.5, False)] * 6])
    assert path[0]["stake_a_trade"] == 4.0 and path[0]["equity"] == pytest.approx(30 + 6 * (4.0 / 0.5125 - 4.0), abs=0.01)
    assert path[1]["pnl"] == pytest.approx(-6 * path[1]["stake_a_trade"], abs=0.05), "six losses lose the day's 80%"
    assert path[1]["equity"] == pytest.approx(path[0]["equity"] * 0.2, abs=0.05)


def test_the_ranking_uses_only_the_window_and_breaks_ties_at_random():
    pytest.importorskip("numpy")
    import codex_trade_check as X
    hist = {"a": [1, 1, 1], "b": [1, 0, 0], "c": [0, 0, 0], "thin": [1, 1], "x": [1, 0, 1], "y": [1, 0, 1]}
    assert X.ranked(hist) == ["a", "x", "y", "b", "c"], "ties by more picks, then name; two picks are not ranked"
    orders = {tuple(X.ranked(hist, seed=f"{s}")) for s in range(40)}
    assert len(orders) == 2 and all(o[0] == "a" and o[-2:] == ("b", "c") for o in orders), "only the tie moves"
    assert X.ranked(hist, seed="3") == X.ranked(hist, seed="3")


def test_the_three_date_runs():
    pytest.importorskip("numpy")
    import codex_trade_check as X
    ts = [{"date": f"2026-10-{d:02d}", "win": w, "cost": 0.5} for d, w in ((1, 1), (2, 1), (3, 0), (4, 0), (5, 1))]
    runs = X.three_date_runs(ts)
    assert [(r[1], r[2]) for r in runs] == [("2026-10-01", "2026-10-03"), ("2026-10-02", "2026-10-04"), ("2026-10-03", "2026-10-05")]
    assert runs[0][0] == pytest.approx((1 + 1 - 1) / 3) and runs[1][0] == pytest.approx(-1 / 3)
