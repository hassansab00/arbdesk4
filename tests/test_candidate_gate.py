"""The Candidate gate (WXPredict build F.7; Hassan, 6 Oct: "each strategy
trades only cities whose status is Candidate").

A strategy may enter only where v_city_status says `candidate` for that
city-day and checkpoint. The gate acts after the engine has decided, on a BUY
only: probabilities, prices and exits are untouched, and a held-back BUY keeps
what it would have bought, so the gate can itself be judged on settled
outcomes. Unread, it holds every BUY back.
"""
import concurrent.futures as cf
import datetime as dt
import json
import re
import time

import engine_replay_live as erl
import engine_shadow as es

from test_engine_shadow import BANDS, CODE, ROW, flat

KEY = ("london", "2026-09-28", "noon")
BUY = {"action": "BUY", "reason_code": "enter", "target_usd": 25.0, "held_usd": 0.0, "g_now": 0.01,
       "orders": [{"band_id": "b2", "side": "YES", "order_type": "IOC", "limit_price": 0.31, "usd": 25.0,
                   "shares": 78.0}],
       "versions": {"engine": "engine-v1", "market_anchor": {"w": 0.0}}}


def test_a_buy_outside_a_candidate_city_is_held_back_and_kept():
    d = es.candidate_gate(dict(BUY), ("watch", "status-v1"))
    assert d["action"] == "NONE" and d["reason_code"] == "not_candidate" and d["orders"] == []
    assert d["target_usd"] == d["held_usd"] == 0.0
    assert d["versions"]["status"] == {
        "status": "watch", "rules": "status-v1",
        "would": {"action": "BUY", "target_usd": 25.0,
                  "orders": [{"band_id": "b2", "side": "YES", "limit_price": 0.31, "usd": 25.0}]}}
    assert d["g_now"] == BUY["g_now"] and d["versions"]["market_anchor"] == {"w": 0.0}, "nothing else moves"
    assert CODE.match("not_candidate")


def test_a_buy_in_a_candidate_city_passes_and_names_the_status():
    d = es.candidate_gate(dict(BUY), ("candidate", "status-v1"))
    assert d["action"] == "BUY" and d["orders"] == BUY["orders"] and d["target_usd"] == 25.0
    assert d["versions"]["status"] == {"status": "candidate", "rules": "status-v1"}


def test_only_a_buy_is_gated():
    for action in ("NONE", "HOLD", "WAIT"):
        d = es.candidate_gate(dict(BUY, action=action, orders=[]), ("unavailable", "status-v1"))
        assert d["action"] == action and "would" not in d["versions"]["status"]


def test_no_status_is_not_candidate_and_a_held_position_holds():
    d = es.candidate_gate(dict(BUY), None)
    assert d["action"] == "NONE" and d["versions"]["status"]["status"] is None
    d = es.candidate_gate(dict(BUY, held_usd=12.0), ("insufficient", "status-v1"))
    assert d["action"] == "HOLD" and d["target_usd"] == 12.0


def _decide_all(monkeypatch, status):
    """decide_all with the engine forced to BUY (every live view is anchored on
    the market at w = 0, so nothing buys on its own)."""
    import decision_engine as de

    def decide(view, book=None, ledger=None, params=None):
        return json.loads(json.dumps(BUY))
    monkeypatch.setattr(de, "decide", decide)
    buys = []
    rows, _ = es.decide_all([("cp1", ROW)], {}, {("london", "2026-09-28"): BANDS}, {"london": "C"}, {},
                            {sid: flat for sid in es.STRATEGIES}, {}, None, time.monotonic() + 60, "run-1",
                            "2026-09-28T11:36:00+00:00", buys=buys, status=status)
    return {r["strategy_id"]: r for r in rows if r["strategy_id"] not in es.S10}, buys


def test_the_engine_sends_no_order_for_a_held_back_buy(monkeypatch):
    rows, buys = _decide_all(monkeypatch, {KEY: ("watch", "status-v1")})
    assert buys == []
    for r in rows.values():
        assert r["action"] == "NONE" and r["reason_code"] == "not_candidate" and r["n_signals"] == 0
        assert json.loads(r["params_version"])["status"]["would"]["action"] == "BUY"
    rows, buys = _decide_all(monkeypatch, {KEY: ("candidate", "status-v1")})
    assert len(rows) == 3 and len(buys) == 3 and all(r["action"] == "BUY" for r in rows.values())
    assert all(json.loads(r["params_version"])["status"]["status"] == "candidate" for r in rows.values())
    rows, buys = _decide_all(monkeypatch, {})                 # read but empty, or unread
    assert buys == [] and all(r["reason_code"] == "not_candidate" for r in rows.values())


def test_without_the_gate_a_run_decides_as_before(monkeypatch):
    """The replay of a run recorded before the gate passes no statuses: it
    decides as that run did, with nothing stamped."""
    rows, buys = _decide_all(monkeypatch, None)
    assert len(buys) == 3 and all("status" not in json.loads(r["params_version"]) for r in rows.values())


def test_the_read_is_waited_for_briefly_and_fails_closed():
    assert es.collect_status(None) == ({}, "not started")
    done = cf.Future()
    done.set_result({KEY: ("candidate", "status-v1")})
    assert es.collect_status(done) == ({KEY: ("candidate", "status-v1")}, None)
    failed = cf.Future()
    failed.set_exception(RuntimeError("database down"))
    assert es.collect_status(failed) == ({}, "RuntimeError: database down")
    t = time.monotonic()
    statuses, why = es.collect_status(cf.Future(), wait_s=0.2)
    assert statuses == {} and why == "TimeoutError" and time.monotonic() - t < 1.0
    assert es.GATE_WAIT_S <= 2.0, "the tick is billed in whole minutes"


def test_the_tick_records_the_statuses_it_gated_on_for_the_replay(monkeypatch):
    import city_clusters
    import common
    import market_anchor

    def rest_all(path, params=None, **k):
        assert path == "prediction_checkpoints", path
        return [{"checkpoint_id": "cp1", "city_key": "london", "target_date": "2026-09-28", "checkpoint": "noon",
                 "engine_version": None}]
    monkeypatch.setattr(common, "rest_all", rest_all)
    monkeypatch.setattr(common, "rest", lambda *a, **k: [])
    monkeypatch.setattr(common, "insert", lambda table, rows: len(rows))
    monkeypatch.setattr(es, "read_ledgers", lambda *a, **k: {})
    monkeypatch.setattr(city_clusters, "load", lambda rest: None)
    monkeypatch.setattr(market_anchor, "load", lambda rest: None)
    seen = {}

    def decide_all(*a, **k):
        seen.update(k)
        return [], {"city_days": 1, "reached": 1, "out_of_time": 0}
    monkeypatch.setattr(es, "decide_all", decide_all)
    fut = cf.Future()
    fut.set_result({KEY: ("watch", "status-v1"), ("paris", "2026-09-28", "noon"): ("candidate", "status-v1")})
    out = es.record([ROW], {}, {}, {}, {}, {}, dt.datetime(2026, 9, 28, 11, 36, tzinfo=dt.timezone.utc),
                    deadline=time.monotonic() + 30, status_read=fut)
    assert "error" not in out, out
    assert seen["status"] == {KEY: ("watch", "status-v1")}, "only the city-days decided"
    assert out["inputs"]["status"] == {"london|2026-09-28|noon": ["watch", "status-v1"]}
    assert out["status"] == {"read": 2, "seen": 1, "unread": None}
    json.dumps(out["inputs"])
    # and the replay reads it back as decide_all takes it
    assert erl.status_from(out["inputs"]) == {KEY: ("watch", "status-v1")}
    assert erl.status_from({"decided_at": "x"}) is None


def test_the_status_reaches_no_price():
    """engine_shadow reads v_city_status in read_status only, and uses it in
    candidate_gate only: after decide, never in a view or a probability."""
    import inspect
    src = inspect.getsource(es)
    assert len(re.findall(r"v_city_status\"", src)) == 1
    assert 'rest("v_city_status"' in inspect.getsource(es.read_status)
    body = inspect.getsource(es.decide_all)
    assert "candidate_gate(d, status.get" in body
    assert body.index("de.decide(") < body.index("candidate_gate(d, status.get")
    for fn in (es.engine_book, es.s10_call, es.exit_of):
        assert "status" not in inspect.getsource(fn)


def test_the_read_is_one_request_and_refuses_a_full_page():
    asked = []

    def rest(path, params=None, tries=4):
        asked.append((path, dict(params), tries))
        return [{"city_key": "london", "target_date": "2026-09-28", "checkpoint": "noon", "status": "watch",
                 "rules_version": "status-v1"}]
    assert es.read_status(rest) == {KEY: ("watch", "status-v1")}
    (path, params, tries), = asked
    assert path == "v_city_status" and tries == 1 and params["limit"] == str(es.STATUS_ROWS_MAX)

    def full(path, params=None, tries=4):
        return [{"city_key": f"c{i}", "target_date": "2026-09-28", "checkpoint": "noon", "status": "candidate",
                 "rules_version": "status-v1"} for i in range(es.STATUS_ROWS_MAX)]
    try:
        es.read_status(full)
    except RuntimeError as e:
        assert "cap" in str(e)
    else:
        raise AssertionError("a full page may be cut by the row cap: it must not pass as complete")


def test_the_read_runs_on_a_daemon_thread(monkeypatch):
    """The tick's process never waits for it at exit."""
    import threading
    import common
    gate = threading.Event()
    seen = {}

    def rest(path, params=None, tries=4):
        seen["daemon"] = threading.current_thread().daemon
        gate.wait(5)
        return []
    monkeypatch.setattr(common, "rest", rest)
    fut = es.start_status_read()
    assert es.collect_status(fut, wait_s=0.05) == ({}, "TimeoutError")
    gate.set()
    assert fut.result(timeout=5) == {} and seen["daemon"] is True


def _switch(monkeypatch, gate):
    """exit_of for S10's SWITCH from b1 to b2, the engine sizing the buy half."""
    import decision_engine as de
    monkeypatch.setattr(de, "decide", lambda view, book=None, ledger=None, params=None: json.loads(json.dumps(BUY)))
    lg = es.ledger({"account_id": "a1", "strategy_id": "s10_winner", "cash": 980.0, "reserved_cash": 0.0},
                   [], {}, [], "london", "2026-09-28", high_water=1000.0)
    book = {"b1": {"bid": 0.2, "ask": 0.22}, "b2": {"bid": 0.29, "ask": 0.31}}
    return es.exit_of("s10_winner", {"action": "SWITCH", "target": "b2"}, {"band_id": "b1", "shares": 50.0},
                      book, book, lg, {"switch_view": {"probs": {}}}, {}, "run-1", "2026-09-28T11:36:00+00:00",
                      "cp1", "london", "2026-09-28", gate=gate)


def test_a_switch_into_a_city_that_is_not_candidate_holds(monkeypatch):
    """Codex on #330: a SWITCH's buy half is an entry. Held back, the switch
    is a HOLD, as an unsized one is: selling alone would leave the ledger flat
    on a bucket S10 wanted to hold."""
    row, ex = _switch(monkeypatch, lambda d: es.candidate_gate(d, ("watch", "status-v1")))
    assert ex is None and row["action"] == "HOLD" and row["reason_code"] == "not_candidate"
    assert row["n_signals"] == 0
    assert json.loads(row["params_version"])["status"]["would"]["orders"][0]["band_id"] == "b2"
    row, ex = _switch(monkeypatch, lambda d: es.candidate_gate(d, ("candidate", "status-v1")))
    assert row["action"] == "SWITCH" and ex["kind"] == "SWITCH" and ex["buy"]["action"] == "BUY"
    row, ex = _switch(monkeypatch, None)
    assert row["action"] == "SWITCH" and "status" not in json.loads(row["params_version"])


def test_a_sell_is_never_gated(monkeypatch):
    def gate(d):
        raise AssertionError("a SELL has no buy half to gate")
    lg = es.ledger({"account_id": "a1", "strategy_id": "s10_winner", "cash": 980.0, "reserved_cash": 0.0},
                   [], {}, [], "london", "2026-09-28", high_water=1000.0)
    row, ex = es.exit_of("s10_winner", {"action": "SELL", "reason": "lost"}, {"band_id": "b1", "shares": 50.0},
                         {"b1": {"bid": 0.01}}, {}, lg, {}, {}, "run-1", "2026-09-28T11:36:00+00:00", "cp1",
                         "london", "2026-09-28", gate=gate)
    assert ex["kind"] == "SELL" and row["n_signals"] == 1


def test_decide_all_hands_the_gate_to_the_switch():
    import inspect
    body = inspect.getsource(es.decide_all)
    assert "gate=gate" in body and "candidate_gate(dd, status.get(k))" in body
    assert "gate = None if status is None" in body
