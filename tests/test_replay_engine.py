"""The engine replay's own mechanics (scripts/backtest/replay_engine.py, plan v2
P5.12 part 2): what a row may know, how a leg fills, how a market settles."""
import datetime as dt

from backtest import replay_engine as re_

SNAP = {"epoch": 1000, "best_bid": 0.30, "best_ask": 0.32, "no_best_ask": 0.70,
        "ask_usd_1c": 10.0, "ask_usd_2c": 25.0, "ask_usd_5c": 60.0, "ask_usd_10c": 100.0, "ask_usd_25c": 200.0,
        "bid_usd_1c": 9.0, "bid_usd_2c": 18.0, "bid_usd_5c": 30.0, "bid_usd_10c": 60.0, "bid_usd_25c": 90.0}


def test_as_of_never_reads_the_future_and_refuses_the_stale():
    series = ([100, 200, 300], ["a", "b", "c"])
    assert re_.as_of(series, 250, 1000) == "b"
    assert re_.as_of(series, 300, 1000) == "c"
    assert re_.as_of(series, 99, 1000) is None
    assert re_.as_of(series, 900, 500) is None            # 600 s old against 500 allowed


def test_a_yes_leg_walks_the_tiers_at_their_worse_edge_and_pays_the_fee():
    sh, paid = re_.fill({"side": "YES", "usd": 20.0}, SNAP, 0.97)
    assert paid == 20.0
    # 10 at 0.33, 10 at 0.34, each plus the taker fee at that price
    want = 10 / (0.33 + re_.fee_per_share(0.33)) + 10 / (0.34 + re_.fee_per_share(0.34))
    assert abs(sh - want) < 1e-9


def test_what_the_book_does_not_hold_is_not_filled():
    sh, paid = re_.fill({"side": "YES", "usd": 500.0}, SNAP, 0.97)
    assert paid == 200.0 and sh > 0


def test_nothing_fills_above_the_price_rail():
    snap = dict(SNAP, best_ask=0.95)
    sh, paid = re_.fill({"side": "YES", "usd": 100.0}, snap, 0.97)
    assert paid == 25.0          # 0.96 and 0.97 only; +5c would be 1.00


def test_a_no_leg_draws_on_the_yes_bids():
    sh, paid = re_.fill({"side": "NO", "usd": 1000.0}, SNAP, 0.97)
    # 90 YES-bid dollars at 0.30 = 300 shares -> 300 x 0.70 = 210 NO dollars at most,
    # and only up to the 0.97 rail: 0.70 + 0.25 = 0.95 is inside it
    assert abs(paid - 90 / 0.30 * 0.70) < 1e-6


def test_no_book_no_fill():
    assert re_.fill({"side": "YES", "usd": 10.0}, None, 0.97) == (0.0, 0.0)
    assert re_.fill({"side": "YES", "usd": 10.0}, dict(SNAP, best_ask=None), 0.97) == (0.0, 0.0)


def test_the_plan_decides_before_it_settles_and_in_time_order():
    markets = {"m": {"market_id": "m", "city": "london", "date": dt.date(2026, 9, 20), "winner": "b",
                     "unit": "C", "tz": "Europe/London"}}
    ev = re_.plan(markets, {("london", 9): 15.0}, dt.date(2026, 9, 12), dt.date(2026, 9, 25), {"m": [(0, {})]})
    kinds = [e[2] for e in ev]
    assert kinds[-1] == "settle" and kinds.count("decide") == 6
    assert [e[0] for e in ev] == sorted(e[0] for e in ev)
    end = dt.datetime(2026, 9, 21, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=1)))   # BST
    assert ev[-1][0] == int(end.timestamp())


def test_a_market_outside_the_window_or_without_pricing_is_not_replayed():
    markets = {"m": {"market_id": "m", "city": "london", "date": dt.date(2026, 9, 26), "winner": "b",
                     "unit": "C", "tz": "Europe/London"}}
    assert re_.plan(markets, {}, dt.date(2026, 9, 12), dt.date(2026, 9, 25), {"m": [(0, {})]}) == []
    markets["m"]["date"] = dt.date(2026, 9, 20)
    assert re_.plan(markets, {}, dt.date(2026, 9, 12), dt.date(2026, 9, 25), {}) == []


def test_a_decision_sees_the_same_date_and_the_high_water_mark(monkeypatch):
    """P5.9 part 2: the ledger handed to the engine carries what the strategy
    holds on the other cities of the same date, and its high-water mark."""
    import strategies.engine_views as ev
    day = dt.date(2026, 9, 20)
    markets = {m: {"market_id": m, "city": c, "date": d, "winner": "w", "unit": "C", "tz": "UTC"}
               for m, c, d in (("m1", "a", day), ("m2", "b", day), ("m3", "c", day + dt.timedelta(days=1)))}
    bands = {m: [{"band_id": f"{m}b"}] for m in markets}
    ladders = {m: [(0, {f"{m}b": 1.0})] for m in markets}
    books = {f"{m}b": ([0], [dict(SNAP)]) for m in markets}
    seen = []
    monkeypatch.setattr(ev, "engine_input", lambda sid, ctx: ({"probs": ctx["probs"]}, ctx["book"], None))

    def decide(view, *, book, ledger, rails):
        seen.append(ledger)
        if len(seen) == 1:           # the first decision buys; the rest hold
            return {"action": "BUY", "reason_code": "enter", "g_now": 0, "g_target": 0,
                    "orders": [{"band_id": list(book)[0], "side": "YES", "usd": 10.0, "limit_price": 0.32}]}
        return {"action": "NONE", "reason_code": "x", "g_now": 0, "g_target": 0, "orders": []}
    monkeypatch.setattr(re_.de, "decide", decide)
    events = [(10, 0, "decide", "m1", "noon"), (20, 0, "decide", "m2", "noon"), (30, 0, "decide", "m3", "noon")]
    re_.walk(("s", events, markets, bands, ladders, books, {"max_price": 0.97}))
    assert seen[0]["same_day"] == {} and seen[0]["high_water_usd"] == re_.BANKROLL
    assert seen[1]["same_day"] == {"a": 10.0}          # m1's city, same date
    assert seen[2]["same_day"] == {}                   # the next day holds nothing
