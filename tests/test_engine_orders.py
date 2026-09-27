"""The engine's BUYs reach its shadow ledgers, filled inside the tick (plan v2
P5.12 part 3b, scripts/engine_orders.py)."""
import datetime as dt
import threading
import time
from decimal import Decimal

import pytest

import engine_orders as eo
import engine_shadow as es
from paper_execution import simulate

ORDERS = [{"band_id": "b1", "side": "YES", "order_type": "IOC", "limit_price": 0.35, "usd": 21.0,
           "shares": 21.0 / 0.36, "depth_usd": None},
          {"band_id": "b2", "side": "NO", "order_type": "IOC", "limit_price": 0.8, "usd": 8.0,
           "shares": 8.0 / 0.81, "depth_usd": None},
          {"band_id": "b3", "side": "YES", "order_type": "IOC", "limit_price": 0.04, "usd": 2.0,
           "shares": 2.0 / 0.042, "depth_usd": None},
          {"band_id": "b4", "side": "YES", "order_type": "IOC", "limit_price": None, "usd": 9.0,
           "shares": 0.0, "depth_usd": None}]
P_POST = {"b1": 0.45, "b2": 0.1, "b3": 0.05, "b4": 0.2}


def test_legs_are_what_queue_plan_takes_and_below_minimum_is_not_sent():
    legs, dropped, legs_p = eo.plan_legs(ORDERS, P_POST)
    assert [(l["band_id"], l["side"]) for l in legs] == [("b1", "YES"), ("b2", "NO")]
    b1 = legs[0]
    assert b1["shares"] == "58.33" and b1["limit_price"] == "0.35", "shares rounded down to the 0.01 step"
    assert Decimal(b1["cash_ceiling"]) >= Decimal(b1["shares"]) * Decimal(b1["limit_price"]), \
        "queue_plan: cash_ceiling >= shares x limit"
    assert {d["band_id"]: d["why"] for d in dropped} == {
        "b3": "below the venue minimum of 5 USDC", "b4": "no price to buy at"}
    assert legs_p == {"b1:YES": {"p": 0.45}, "b2:NO": {"p": 0.9}}, "a NO leg believes 1 - P(YES)"


@pytest.mark.parametrize("limit", [0.02, 0.35, 0.5, 0.71, 0.97])
def test_the_reserve_covers_any_fill_the_simulator_can_make(limit):
    """paper_worker rejects a BUY whose notional plus fee exceeds its
    cash_ceiling. The reserve assumes the worst: every share at the limit, and
    the fee at the price below it where rate x q x (1 - q) is largest."""
    shares = round(50.0 / limit, 2)
    legs, _d, _p = eo.plan_legs([{"band_id": "b", "side": "YES", "limit_price": limit, "shares": shares}], {"b": 0.5})
    leg = legs[0]
    now = dt.datetime(2026, 9, 28, 12, tzinfo=dt.timezone.utc)
    tick = Decimal("0.01")
    lim = Decimal(leg["limit_price"])
    # depth spread over every tick from the limit down to a price near a half
    levels = sorted({lim, max(tick, min(lim, Decimal("0.5"))), max(tick, lim - tick)}, reverse=True)
    q = Decimal(leg["shares"])
    book = {"token_id": "tok", "observed_at": now.isoformat(), "tradeable": True, "tick_size": "0.01", "fee_rate": "0.05",
            "min_order_size": "5", "min_order_size_unit": "USDC", "snapshot_id": "s",
            "asks": [{"price": str(p), "size": str(q)} for p in levels]}
    for asks in (book["asks"], book["asks"][-1:]):
        result = simulate({"action": "BUY", "token_id": "tok", "shares": leg["shares"], "limit_price": leg["limit_price"],
                           "share_step": "0.01", "max_book_age_seconds": 120,
                           "expires_at": (now + dt.timedelta(minutes=5)).isoformat()},
                          dict(book, asks=asks), now=now)
        assert result["status"] == "filled", result
        assert Decimal(result["notional"]) + Decimal(result["fee"]) <= Decimal(leg["cash_ceiling"])


def test_the_edge_queue_plan_checks_is_share_weighted_after_the_fee():
    legs = [{"band_id": "a", "side": "YES", "shares": "10", "limit_price": "0.4"},
            {"band_id": "b", "side": "NO", "shares": "30", "limit_price": "0.5"}]
    p = {"a:YES": {"p": 0.55}, "b:NO": {"p": 0.6}}
    fee = lambda x: 0.05 * x * (1 - x)
    want = (10 * (0.55 - 0.4 - fee(0.4)) + 30 * (0.6 - 0.5 - fee(0.5))) / 40
    assert eo.net_edge_per_share(legs, p) == pytest.approx(want, abs=1e-6)
    assert eo.net_edge_per_share(legs, {"a:YES": {"p": 0.55}}) is None, "a leg without its belief is not guessed"


def test_the_evidence_names_the_decision_and_what_was_left_out():
    row = {"strategy_id": "s11_ladder", "city_key": "london", "resolution_date": "2026-09-28"}
    d = {"orders": ORDERS, "p_post": P_POST, "engine_version": "engine-v1", "versions": {"engine": "engine-v1"},
         "g_now": 0.0, "g_target": 0.01, "target_usd": 29.0}
    (legs, ev), why = eo.build(4711, row, d, "run-1", "cp-1")
    assert why is None and len(legs) == 2
    assert ev["decision_id"] == 4711 and ev["run_id"] == "run-1" and ev["checkpoint_id"] == "cp-1"
    assert ev["legs_p"]["b1:YES"] == {"p": 0.45} and len(ev["dropped"]) == 2
    assert float(ev["net_edge_per_share"]) == pytest.approx(eo.net_edge_per_share(legs, ev["legs_p"]))
    built, why = eo.build(4712, row, dict(d, orders=ORDERS[2:]), "run-1", "cp-1")
    assert built is None and why == "every leg below the venue minimum or unpriced"


def test_fills_stop_at_the_cap_and_the_deadline_and_never_raise():
    queues = {"a": ["a1", "a2", "a3"], "b": ["b1", "b2"], "c": ["c1"]}
    lock = threading.Lock()

    def claim(aid):
        with lock:
            return {"order_id": queues[aid].pop(0)} if queues[aid] else None

    def fill(order):
        if order["order_id"] == "b2":
            raise RuntimeError("venue down")
        return {"status": "filled"}
    out = eo.fill_ledgers(["a", "b", "c", "a"], time.monotonic() + 60, claim, fill, max_fills=5)
    assert sum(out.values()) == 5, "no more than max_fills, across every ledger's thread"
    queues = {"a": ["a1"]}
    assert eo.fill_ledgers(["a"], time.monotonic() + 1.0, claim, fill) == {}, "not started without time to finish"
    queues = {"a": [], "b": ["b1", "b2"]}
    out = eo.fill_ledgers(["a", "b"], time.monotonic() + 60, claim, fill)
    assert out == {"filled": 1, "error: RuntimeError": 1}


def _rpc_rest(statuses=None):
    calls = []

    def rpc(fn, params=None, **k):
        calls.append((fn, params))
        if fn == "publish_engine_plan":
            return f"plan-{params['p_decision']}"
        if fn == "claim_account_order":
            return None
        raise AssertionError(fn)

    def rest(path, params=None, **k):
        assert path == "paper_trade_plans"
        return [{"plan_id": f"plan-{i}", "status": s, "reason": r} for i, (s, r) in (statuses or {}).items()]
    return calls, rpc, rest


def test_only_a_switched_on_strategy_with_a_ledger_orders():
    row = lambda sid: {"strategy_id": sid, "city_key": "london", "resolution_date": "2026-09-28"}
    d = {"orders": ORDERS[:1], "p_post": P_POST}
    buys = [(row("s10_winner"), d, "cp"), (row("s11_ladder"), d, "cp"), (row("s12_no"), d, "cp")]
    ids = {("s10_winner", "london", "2026-09-28"): 1, ("s11_ladder", "london", "2026-09-28"): 2}
    calls, rpc, rest = _rpc_rest({1: ("queued", None)})
    out = eo.send(buys, {"s10_winner": "acct-1", "s12_no": "acct-3"}, {"s10_winner", "s12_no"}, ids, "run",
                  time.monotonic() + 60, rpc, rest, lambda o: {"status": "filled"})
    assert [c for c in calls if c[0] == "publish_engine_plan"] == [
        ("publish_engine_plan", {"p_account": "acct-1", "p_decision": 1,
                                 "p_legs": eo.plan_legs(ORDERS[:1], P_POST)[0],
                                 "p_evidence": eo.build(1, row("s10_winner"), d, "run", "cp")[0][1]})]
    assert out["skipped"] == {"strategy not switched on": 1, "decision id not found": 1}
    assert (out["published"], out["queued"]) == (1, 1)
    assert ("claim_account_order", {"p_account": "acct-1"}) in calls, "a queued plan is filled in the tick"


def test_a_refused_plan_is_counted_with_its_reason_and_nothing_is_filled():
    row = {"strategy_id": "s10_winner", "city_key": "london", "resolution_date": "2026-09-28"}
    calls, rpc, rest = _rpc_rest({1: ("blocked", "Rail: 31.20 on london 2026-09-28 would exceed 0.03 of the account")})
    out = eo.send([(row, {"orders": ORDERS[:1], "p_post": P_POST}, "cp")], {"s10_winner": "acct-1"},
                  {"s10_winner"}, {("s10_winner", "london", "2026-09-28"): 1}, "run", time.monotonic() + 60,
                  rpc, rest, lambda o: pytest.fail("nothing to fill"))
    assert out["queued"] == 0 and list(out["blocked"].values()) == [1]
    assert not any(c[0] == "claim_account_order" for c in calls)


def test_a_dry_run_publishes_nothing():
    row = {"strategy_id": "s10_winner", "city_key": "london", "resolution_date": "2026-09-28"}
    calls, rpc, rest = _rpc_rest()
    out = eo.send([(row, {"orders": ORDERS[:1], "p_post": P_POST}, "cp")], {"s10_winner": "acct-1"},
                  {"s10_winner"}, {("s10_winner", "london", "2026-09-28"): 1}, "run", time.monotonic() + 60,
                  rpc, rest, lambda o: pytest.fail("no fill on a dry run"), dry_run=True)
    assert calls == [] and out["published"] == 1


def test_the_engine_hands_its_buys_over_and_writes_the_decision_first(monkeypatch):
    import decision_engine as de
    from test_engine_shadow import BANDS, ROW, flat
    buy = {"action": "BUY", "reason_code": "enter", "orders": ORDERS[:1], "p_post": P_POST, "g_now": 0.0,
           "g_wait": None, "binding": [], "target_usd": 21.0, "held_usd": 0.0, "versions": {"engine": "engine-v1"}}
    monkeypatch.setattr(de, "decide", lambda view, **k: dict(buy, strategy_id=view.get("strategy_id")))
    buys = []
    rows, _ = es.decide_all([("cp1", ROW)], {}, {("london", "2026-09-28"): BANDS}, {"london": "C"}, {},
                            {sid: flat for sid in es.STRATEGIES}, {}, None, time.monotonic() + 60, "run-1",
                            "2026-09-28T11:36:00+00:00", buys=buys)
    bought = {r["strategy_id"] for r in rows if r["action"] == "BUY"}
    assert bought and {r["strategy_id"] for r, _d, _c in buys} == bought
    assert all(d is not None and c == "cp1" for _r, d, c in buys)


def test_orders_follow_the_decisions_insert_and_never_raise(monkeypatch):
    src = open(es.__file__).read()
    body = src[src.index("def record("):]
    assert body.index('insert("decisions", rows)') < body.index("send_orders(buys"), \
        "the plan must find its decision: write the decision first"
    import common
    monkeypatch.setattr(common, "rest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out = es.send_orders([({"strategy_id": "s10_winner", "city_key": "x", "resolution_date": "d"}, {}, "cp")],
                         "run", time.monotonic() + 30)
    assert out["error"].startswith("RuntimeError")
