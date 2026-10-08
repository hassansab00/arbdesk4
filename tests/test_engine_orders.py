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
        if fn == "expire_paper_commands":
            return 0
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
    assert body.index('insert("decisions", rows)') < body.index("send_orders(a_buys"), \
        "the plan must find its decision: write the decision first"
    assert body.index("send_orders(a_buys") < body.index("send_orders(m_buys"), \
        "the anchored strategies' orders go before the model-only twins'"
    import common
    monkeypatch.setattr(common, "rest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out = es.send_orders([({"strategy_id": "s10_winner", "city_key": "x", "resolution_date": "d"}, {}, "cp")],
                         "run", time.monotonic() + 30)
    assert out["error"].startswith("RuntimeError")


# ---------------------------------------------------------------------------
# Step 3: S10's own SELL and SWITCH
# ---------------------------------------------------------------------------
LG = {"equity_usd": 1000.0, "cash_usd": 970.0, "on_market_usd": 30.0, "pnl_today_usd": 0.0,
      "held": {"b2": (60.0, 0.0)}, "held_usd": 30.0, "held_cost": {("b2", "YES"): 30.0},
      "same_day": {}, "high_water_usd": 1000.0}


def test_after_a_sale_the_ledger_holds_the_proceeds_not_the_bucket():
    after = es.after_sale(LG, "b2", "YES", 60.0, 60 * 0.18)
    assert after["held"] == {} and after["held_usd"] == 0.0 and after["on_market_usd"] == 0.0
    assert after["cash_usd"] == pytest.approx(970.0 + 10.8)
    assert after["equity_usd"] == pytest.approx(1000.0 - 30.0 + 10.8)
    half = es.after_sale(LG, "b2", "YES", 30.0, 30 * 0.18)
    assert half["held"] == {"b2": (30.0, 0.0)} and half["held_usd"] == pytest.approx(15.0)


BOOK = {"b1": {"bid": 0.30, "ask": 0.33}, "b2": {"bid": 0.18, "ask": 0.21}, "b3": {"bid": 0.01, "ask": 0.03}}
HELD = {"band_id": "b2", "shares": 60.0}


def _exit(action, trace_extra=None, decide=None, monkeypatch=None):
    import decision_engine as de
    if decide is not None:
        monkeypatch.setattr(de, "decide", decide)
    trace = dict({"s10": {"action": action, "reason": "because", "target": "b1"}}, **(trace_extra or {}))
    return es.exit_of("s10_winner", trace["s10"], HELD, BOOK, BOOK, LG, trace, {}, "run", "2026-09-28T13:36:00+00:00",
                      "cp", "london", "2026-09-28", prediction=("s10-row", "s10_shadow_checkpoints"))


def test_a_certainly_lost_bucket_is_sold_at_the_bid():
    row, ex = _exit("SELL")
    assert (row["action"], row["reason_code"], row["n_signals"]) == ("SELL", "own_rule_sell", 1)
    assert (row["prediction_id"], row["prediction_source"]) == ("s10-row", "s10_shadow_checkpoints"), \
        "an exit names the S10 call it acted on (P2.2 part 3)"
    assert ex["kind"] == "SELL" and ex["buy"] is None
    assert ex["sell"] == {"band_id": "b2", "side": "YES", "shares": 60.0, "limit_price": 0.18}


def test_a_switch_is_a_sale_and_a_buy_sized_on_the_ledger_after_the_sale(monkeypatch):
    seen = {}

    def decide(view, *, book, ledger, params=None, **k):
        seen["ledger"] = ledger
        return {"action": "BUY", "reason_code": "enter", "orders": [{"band_id": "b1", "side": "YES",
                "limit_price": 0.33, "shares": 40.0}], "p_post": {"b1": 0.5}, "g_now": 0.0, "g_wait": None,
                "binding": [], "target_usd": 13.2, "held_usd": 0.0, "versions": {"engine": "engine-v1"}}
    row, ex = _exit("SWITCH", {"switch_view": {"probs": {}}}, decide, monkeypatch)
    assert (row["action"], row["reason_code"], row["n_signals"]) == ("SWITCH", "own_rule_switch", 2)
    assert row["prediction_id"] == "s10-row"
    assert ex["kind"] == "SWITCH" and ex["buy"]["orders"][0]["band_id"] == "b1"
    from strategies.s10_max_temp_winner import _net_bid
    assert seen["ledger"]["held"] == {} and seen["ledger"]["cash_usd"] == pytest.approx(970 + 60 * _net_bid(BOOK, "b2"))


def test_a_switch_the_engine_will_not_size_holds(monkeypatch):
    def decide(view, **k):
        return {"action": "NONE", "reason_code": "no_trade_band", "orders": [], "g_now": 0.0, "g_wait": None,
                "binding": [], "target_usd": 0.0, "held_usd": 0.0, "versions": {}}
    row, ex = _exit("SWITCH", {"switch_view": {"probs": {}}}, decide, monkeypatch)
    assert (row["action"], row["reason_code"], row["n_signals"]) == ("HOLD", "switch_unsized", 0) and ex is None
    row, ex = _exit("SWITCH")                                       # no buy view at all
    assert (row["action"], row["reason_code"]) == ("HOLD", "switch_unsized") and ex is None


def test_the_engine_collects_s10_exits(monkeypatch):
    from strategies import engine_views as ev
    from test_engine_shadow import BANDS, ROW, flat

    def fake(sid, ctx, trace=None):
        if trace is not None:
            trace["s10"] = {"action": "SELL", "reason": "the held bucket is certainly lost", "target": None}
        return None, ctx["book"], "s10 SELL: the held bucket is certainly lost"
    monkeypatch.setattr(ev, "engine_input", fake)
    holding = lambda c, t: dict(es.ledger({"cash": 970.0, "reserved_cash": 0.0},
                                          [{"band_id": "b2", "side": "YES", "shares": 60.0, "cost_basis": 30.0}],
                                          {"b2": ("london", "2026-09-28")}, [], c, t), held_cost={("b2", "YES"): 30.0})
    ledgers = {sid: (holding if sid in es.S10 else flat) for sid in es.STRATEGIES}
    exits = []
    rows, _ = es.decide_all([("cp1", ROW)], {("london", "2026-09-28", "noon"): {b["band_id"]: 0.2 for b in BANDS}},
                            {("london", "2026-09-28"): BANDS}, {"london": "C"}, {}, ledgers, {}, None,
                            time.monotonic() + 60, "run-1", "2026-09-28T11:36:00+00:00", exits=exits)
    assert {e["row"]["strategy_id"] for e in exits} == set(es.S10)
    assert all(e["sell"]["band_id"] == "b2" and e["sell"]["shares"] == 60.0 for e in exits)
    assert {r["action"] for r in rows if r["strategy_id"] in es.S10} == {"SELL"}


def test_an_exit_below_the_venue_minimum_or_without_a_bid_is_not_sent():
    assert eo.sell_leg({"shares": 60.0, "limit_price": 0.18}) == ((60.0, 0.18), None)
    assert eo.sell_leg({"shares": 60.0, "limit_price": 0.01})[1] == "below the venue minimum of 5 USDC", \
        "a certainly-lost bucket at a cent rarely reaches the venue's 5 USDC"
    assert eo.sell_leg({"shares": 60.0, "limit_price": None})[1] == "no bid to sell at"


def _exit_rpc(fill_status="filled"):
    calls = []

    def rpc(fn, params=None, **k):
        calls.append((fn, params))
        return {"submit_engine_exit": "order-1", "claim_account_order": {"order_id": "order-1"},
                "expire_paper_commands": 0, "publish_engine_plan": "plan-9"}.get(fn)
    return calls, rpc


def test_a_switch_buys_only_once_its_sale_has_filled():
    row = {"strategy_id": "s10_winner", "city_key": "london", "resolution_date": "2026-09-28"}
    buy = {"orders": ORDERS[:1], "p_post": P_POST}
    ex = {"kind": "SWITCH", "row": row, "sell": {"band_id": "b2", "side": "YES", "shares": 60.0, "limit_price": 0.18},
          "buy": buy, "checkpoint_id": "cp"}
    ids = {("s10_winner", "london", "2026-09-28"): 7}
    for status, n_buys in (("filled", 1), ("partial", 1), ("rejected", 0)):
        calls, rpc = _exit_rpc()
        out, buys = eo.send_exits([ex], {"s10_winner": "acct"}, {"s10_winner"}, ids, time.monotonic() + 60, rpc,
                                  lambda o, s=status: {"status": s})
        assert [c[0] for c in calls] == ["submit_engine_exit", "claim_account_order"]
        assert calls[0][1] == {"p_account": "acct", "p_decision": 7, "p_band": "b2", "p_side": "YES",
                               "p_shares": 60.0, "p_limit": 0.18}
        assert len(buys) == n_buys and out["fills"] == {status: 1}
    calls, rpc = _exit_rpc()
    out, buys = eo.send_exits([ex], {"s10_winner": "acct"}, set(), ids, time.monotonic() + 60, rpc,
                              lambda o: pytest.fail("not switched on"))
    assert calls == [] and out["skipped"] == {"strategy not switched on": 1}


def test_send_expires_leftovers_first_and_sends_the_switch_buy():
    row = {"strategy_id": "s10_winner", "city_key": "london", "resolution_date": "2026-09-28"}
    ex = {"kind": "SWITCH", "row": row, "sell": {"band_id": "b2", "side": "YES", "shares": 60.0, "limit_price": 0.18},
          "buy": {"orders": ORDERS[:1], "p_post": P_POST}, "checkpoint_id": "cp"}
    calls, rpc = _exit_rpc()
    out = eo.send([], {"s10_winner": "acct"}, {"s10_winner"}, {("s10_winner", "london", "2026-09-28"): 7}, "run",
                  time.monotonic() + 60, rpc, lambda *a, **k: [{"plan_id": "plan-9", "status": "queued"}],
                  lambda o: {"status": "filled"}, exits=[ex])
    names = [c[0] for c in calls]
    assert names[0] == "expire_paper_commands"
    assert names.index("submit_engine_exit") < names.index("publish_engine_plan"), "sell first, then buy"
    assert out["exit_orders"]["switch_buys"] == 1 and out["published"] == 1 and out["queued"] == 1


def test_one_refused_order_does_not_stop_the_others():
    import requests
    row = lambda sid: {"strategy_id": sid, "city_key": "london", "resolution_date": "2026-09-28"}
    sell = {"band_id": "b2", "side": "YES", "shares": 60.0, "limit_price": 0.18}
    exits = [{"kind": "SELL", "row": row(s), "sell": sell, "buy": None, "checkpoint_id": "cp"}
             for s in ("s10_winner", "s10_growth")]
    ids = {("s10_winner", "london", "2026-09-28"): 7, ("s10_growth", "london", "2026-09-28"): 8,
           ("s11_ladder", "london", "2026-09-28"): 9, ("s12_no", "london", "2026-09-28"): 10}
    calls = []

    def rpc(fn, params=None, **k):
        calls.append((fn, params))
        if (fn, (params or {}).get("p_decision")) in (("submit_engine_exit", 7), ("publish_engine_plan", 9)):
            raise requests.HTTPError(f"{fn} -> HTTP 400: " + '{"code":"P0001","details":null,"hint":null,'
                                     '"message":"Shares already sold or reserved for exit"}')
        return {"submit_engine_exit": "order-8", "claim_account_order": {"order_id": "o"},
                "expire_paper_commands": 0, "publish_engine_plan": "plan-10"}.get(fn)
    d = {"orders": ORDERS[:1], "p_post": P_POST}
    sids = {"s10_winner", "s10_growth", "s11_ladder", "s12_no"}
    out = eo.send([(row("s11_ladder"), d, "cp"), (row("s12_no"), d, "cp")], {s: f"acct-{s}" for s in sids}, sids,
                  ids, "run", time.monotonic() + 60, rpc, lambda *a, **k: [{"plan_id": "plan-10", "status": "queued"}],
                  lambda o: {"status": "filled"}, exits=exits)
    assert out["exit_orders"]["skipped"] == {"refused: Shares already sold or reserved for exit": 1}
    assert out["exit_orders"]["submitted"] == 1 and out["exit_orders"]["fills"] == {"filled": 1}
    assert out["skipped"] == {"refused: Shares already sold or reserved for exit": 1}
    assert out["published"] == 1 and out["queued"] == 1
    assert eo.refusal(RuntimeError("plain")) == "plain"


def test_the_migration_carries_out_the_engines_exits():
    src = open(eo.__file__.replace("scripts/engine_orders.py", "supabase/migrations/"
                                   "20260927220000_the_engine_exits_by_its_own_rules.sql")).read()
    assert "d.action not in ('SELL', 'SWITCH')" in src and "d.action not in ('BUY', 'SWITCH')" in src
    assert "case when new.context ? 'decision_id' then 'engine_exit' else 'auto_exit' end" in src


def test_send_fills_no_more_than_the_budget_it_is_given(monkeypatch):
    """MAX_FILLS is per tick; a second send in the same tick passes what is left (Codex on #341)."""
    seen = {}
    monkeypatch.setattr(eo, "fill_ledgers",
                        lambda ids, deadline, claim, fill_one, max_fills=eo.MAX_FILLS:
                        seen.setdefault("max_fills", max_fills) and {})
    monkeypatch.setattr(eo, "build", lambda *a, **k: (([{"band_id": "b"}], {}), None))
    rest = lambda table, params: [{"plan_id": "p1", "status": "queued"}]
    eo.send([({"strategy_id": "s", "city_key": "c", "resolution_date": "d"}, {}, "cp")],
            {"s": "acct"}, {"s"}, {("s", "c", "d"): 1}, "run", time.monotonic() + 30,
            lambda *a, **k: "p1", rest, lambda o: {}, max_fills=4)
    assert seen["max_fills"] == 4
