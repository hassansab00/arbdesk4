"""Plan v2 P5.12 part 3a: the one engine decides at the tick's checkpoints and
records it in `decisions`, ordering nothing (scripts/engine_shadow.py)."""
import datetime as dt
import json
import pathlib
import re
import time

import engine_shadow as es

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIONS = {"BUY", "SELL", "SWITCH", "HOLD", "WAIT", "NONE"}


def _declared_code_pattern():
    """decisions.reason_code's check as the migrations declare it: the newest
    migration that states it wins. Read from the SQL, not restated here - on
    27 Sep 15:36Z a code this file believed valid ('s10_wait') failed the
    tick's whole insert against the real constraint."""
    found = None
    for f in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
        for m in re.finditer(r"check \(reason_code ~ '([^']+)'\)", f.read_text()):
            found = m.group(1)
    assert found, "no migration declares decisions.reason_code's check"
    return found


CODE = re.compile(_declared_code_pattern())

BANDS = [{"band_id": f"b{i}", "band_lo": 10 + i, "band_hi": 11 + i, "open_low": i == 0, "open_high": i == 4}
         for i in range(5)]
PROBS = {"b0": 0.05, "b1": 0.2, "b2": 0.5, "b3": 0.2, "b4": 0.05}
# The market prices every bucket at its probability plus a spread: nothing to buy.
MARKET = {b: {"bid": round(p - 0.01, 3), "ask": round(p + 0.02, 3), "last": p} for b, p in PROBS.items()}
ROW = {"city_key": "london", "target_date": "2026-09-28", "checkpoint": "noon",
       "local_decision_time": "2026-09-28T12:00", "probs": PROBS, "market": MARKET}
ACCOUNT = {"account_id": "a1", "strategy_id": "s11_ladder", "cash": 1000.0, "reserved_cash": 0.0}


def flat(c, t):
    return es.ledger(ACCOUNT, [], {}, [], c, t, high_water=1000.0)


def test_reason_codes_fit_the_table():
    assert es.CODE.pattern == CODE.pattern                 # the module holds the table's own rule
    assert es.reason_code("no_trade_band") == "no_trade_band"
    assert es.reason_code("no market to anchor on: a bucket is not quoted") == "no_market_anchor"
    assert es.reason_code("s10 SWITCH: the target moved") == "own_rule_switch"
    for action in sorted(ACTIONS):                         # every word S10's own rule can say
        assert CODE.match(es.reason_code(f"s10 {action}: why")), action
    assert es.reason_code("something new, in words") == "no_view"
    assert es.reason_code(None) == "no_view"
    assert es.s10_action("s10 SELL: the held bucket is certainly lost") == "SELL"
    assert es.s10_action("no ladder") == "NONE"


def test_a_no_leg_is_priced_from_the_same_clob_market():
    b = es.engine_book({"x": {"bid": 0.30, "ask": 0.34, "last": 0.3}, "y": {"bid": None, "ask": 0.02}})
    assert b["x"]["no_ask"] == 0.7 and b["x"]["no_bid"] == 0.66
    assert b["y"]["no_ask"] is None and b["y"]["no_bid"] == 0.98


def test_the_ledger_is_read_the_way_queue_plan_reads_it():
    city_day = {"b1": ("london", "2026-09-28"), "b2": ("paris", "2026-09-28"), "b3": ("london", "2026-09-29")}
    positions = [{"band_id": "b1", "side": "YES", "shares": 50, "cost_basis": 20.0},
                 {"band_id": "b2", "side": "NO", "shares": 10, "cost_basis": 7.0},
                 {"band_id": "b3", "side": "YES", "shares": 5, "cost_basis": 2.0}]
    orders = [{"band_id": "b1", "cash_ceiling": 3.0}, {"band_id": "b2", "cash_ceiling": 1.0}]
    acct = dict(ACCOUNT, cash=950.0, reserved_cash=4.0)
    lg = es.ledger(acct, positions, city_day, orders, "london", "2026-09-28", high_water=990.0, pnl_today=-5.0)
    assert lg["equity_usd"] == 950.0 + 29.0                  # cash plus open positions at cost
    assert lg["cash_usd"] == 946.0                           # reserved cash is not free
    assert lg["held"] == {"b1": (50.0, 0.0)} and lg["held_usd"] == 20.0
    assert lg["on_market_usd"] == 23.0                       # the city-day: positions plus live buys
    assert lg["same_day"] == {"paris": 8.0}                  # another city, the same date
    assert lg["high_water_usd"] == 990.0 and lg["pnl_today_usd"] == -5.0


def test_every_strategy_gets_a_row_the_table_accepts():
    ledgers = {sid: flat for sid in es.STRATEGIES}
    rows, detail = es.decide_all([("cp1", ROW)], {}, {("london", "2026-09-28"): BANDS}, {"london": "C"}, {},
                                 ledgers, {}, None, time.monotonic() + 60, "run-1", "2026-09-28T11:36:00+00:00")
    assert detail == {"city_days": 1, "reached": 1, "out_of_time": 0,
                      "model_only_reached": 1, "model_only_out_of_time": 0}
    assert sorted(r["strategy_id"] for r in rows) == sorted(es.STRATEGIES)
    for r in rows:
        assert r["action"] in ACTIONS and CODE.match(r["reason_code"]), r
        assert r["checkpoint_id"] == "cp1" and r["run_id"] == r["tick_id"] == "run-1"
        assert r["n_signals"] == 0 or r["action"] == "BUY"
    by = {r["strategy_id"]: r for r in rows}
    # the market prices everything at its probability: S11 and S12 pass
    assert by["s11_ladder"]["action"] == "NONE" and by["s12_no"]["action"] == "NONE"
    # S10 has no remaining-day ladder here: recorded, not skipped
    assert by["s10_winner"]["reason_code"] == "no_ladder"
    assert by["s11_ladder"]["params_version"] and "engine" in by["s11_ladder"]["params_version"]


def test_s10_declining_by_its_own_rule_is_a_row_the_table_accepts():
    """The path that failed live (27 Sep 15:36Z): S10 has its remaining-day
    ladder, decides by its own rule not to buy, and its row must still fit
    decisions.reason_code's check or the tick's whole insert is refused."""
    ledgers = {sid: flat for sid in es.STRATEGIES}
    rows, _ = es.decide_all([("cp1", ROW)], {("london", "2026-09-28", "noon"): PROBS},
                            {("london", "2026-09-28"): BANDS}, {"london": "C"}, {}, ledgers, {}, None,
                            time.monotonic() + 60, "run-1", "2026-09-28T11:36:00+00:00")
    s10_rows = [r for r in rows if r["strategy_id"] in es.S10]
    assert len(s10_rows) == len(es.S10)
    for r in rows:
        assert r["action"] in ACTIONS and CODE.match(r["reason_code"]), r
    assert all(r["reason_code"].startswith("own_rule_") for r in s10_rows), s10_rows
    assert all(r["action"] == r["reason_code"].removeprefix("own_rule_").upper() for r in s10_rows)


def test_a_strategy_without_a_ledger_or_a_quoted_book_is_a_row_too():
    half = dict(ROW, market={b: v for b, v in MARKET.items() if b != "b4"})
    rows, _ = es.decide_all([("cp1", half)], {}, {("london", "2026-09-28"): BANDS}, {"london": "C"}, {},
                            {"s11_ladder": flat}, {}, None, time.monotonic() + 60, "r", "t")
    by = {r["strategy_id"]: r for r in rows}
    assert by["s11_ladder"]["reason_code"] == "no_market_anchor"
    assert by["s12_no"]["reason_code"] == "no_ledger" and by["s12_no"]["action"] == "NONE"


def test_the_deadline_defers_city_days_instead_of_overrunning():
    rows, detail = es.decide_all([("cp1", ROW), ("cp2", dict(ROW, city_key="paris"))], {}, {}, {}, {},
                                 {}, {}, None, time.monotonic() - 1, "r", "t")
    assert rows == [] and detail == {"city_days": 2, "reached": 0, "out_of_time": 2,
                                     "model_only_reached": 0, "model_only_out_of_time": 2}


def test_the_twins_decide_only_in_the_time_the_anchored_strategies_leave(monkeypatch):
    """8 Oct: a deadline that falls after the anchored pass costs the
    model-only twins their city-days and never an anchored strategy its own."""
    clock = iter([0.0, 0.0] + [100.0] * 10)        # both anchored checks in time, then past it
    monkeypatch.setattr(es.time, "monotonic", lambda: next(clock))
    ledgers = {sid: flat for sid in es.STRATEGIES}
    rows, detail = es.decide_all([("cp1", ROW), ("cp2", dict(ROW, city_key="paris"))], {},
                                 {("london", "2026-09-28"): BANDS}, {"london": "C", "paris": "C"}, {},
                                 ledgers, {}, None, 50.0, "r", "2026-09-28T11:36:00+00:00")
    assert detail == {"city_days": 2, "reached": 2, "out_of_time": 0,
                      "model_only_reached": 0, "model_only_out_of_time": 2}
    assert sorted({r["strategy_id"] for r in rows}) == sorted(es.ANCHORED)
    assert len(rows) == 2 * len(es.ANCHORED)



# --------------------------------------------------------------------------
# The twins take turns (8 Oct): least recently decided first
# --------------------------------------------------------------------------
def _city(name, cp="noon"):
    return dict(ROW, city_key=name, checkpoint=cp)


def test_the_twins_take_the_city_day_they_decided_longest_ago_first():
    cks = [("c1", _city("london")), ("c2", _city("paris")), ("c3", _city("madrid")), ("c4", _city("rome"))]
    last = {("london", "2026-09-28"): 300.0, ("paris", "2026-09-28"): 100.0, ("rome", "2026-09-28"): 200.0}
    assert [c for c, _ in es.least_recent_first(cks, last)] == ["c3", "c2", "c4", "c1"]
    # never decided keeps the tick's order among itself, and so does an unread map
    assert [c for c, _ in es.least_recent_first(cks, {})] == ["c1", "c2", "c3", "c4"]
    assert [c for c, _ in es.least_recent_first(cks, None)] == ["c1", "c2", "c3", "c4"]
    # a city's other day is another city-day
    other = ("c5", dict(_city("london"), target_date="2026-09-29"))
    assert es.least_recent_first([cks[0], other], last)[0][0] == "c5"


def test_when_time_runs_short_the_twins_reach_the_least_recently_decided(monkeypatch):
    """Three city-days, time for one twin city-day: it is the one the twins
    decided longest ago, not the tick's first; the anchored pass is untouched."""
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        # 3 anchored checks, the twins' order check and their first city-day in time; then past it
        return 0.0 if ticks["n"] <= 5 else 100.0
    monkeypatch.setattr(es.time, "monotonic", clock)
    ledgers = {sid: flat for sid in es.STRATEGIES}
    cks = [("c1", _city("london")), ("c2", _city("paris")), ("c3", _city("madrid"))]
    called = []

    def last(left):
        called.append(left)
        return {("london", "2026-09-28"): 300.0, ("paris", "2026-09-28"): 100.0, ("madrid", "2026-09-28"): 200.0}
    rows, detail = es.decide_all(cks, {}, {("london", "2026-09-28"): BANDS},
                                 {"london": "C", "paris": "C", "madrid": "C"}, {}, ledgers, {}, None,
                                 50.0, "r", "2026-09-28T11:36:00+00:00", model_only_last=last)
    assert called == [50.0], "handed the seconds the twins have left"
    assert detail == {"city_days": 3, "reached": 3, "out_of_time": 0,
                      "model_only_reached": 1, "model_only_out_of_time": 2}
    twins = {r["city_key"] for r in rows if r["strategy_id"] in es.MODEL_ONLY}
    assert twins == {"paris"}
    assert {r["city_key"] for r in rows if r["strategy_id"] in es.ANCHORED} == {"london", "paris", "madrid"}


def test_with_no_time_left_the_twins_order_is_not_even_read(monkeypatch):
    monkeypatch.setattr(es.time, "monotonic", lambda: 100.0)
    called = []
    es.decide_all([("c1", ROW)], {}, {}, {}, {}, {}, {}, None, 50.0, "r", "t",
                  model_only_last=lambda left: called.append(left) or {})
    assert called == []


def test_the_twins_last_decisions_are_read_from_the_first_twins_rows():
    seen = {}

    def rest(path, params=None, tries=4):
        seen.update(path=path, params=dict(params), tries=tries)
        return [{"city_key": "london", "resolution_date": "2026-09-28", "decided_at": "2026-09-28T10:36:50.364465+00:00"},
                {"city_key": "london", "resolution_date": "2026-09-28", "decided_at": "2026-09-28T08:36:41+00:00"},
                {"city_key": "paris", "resolution_date": "2026-09-29", "decided_at": "2026-09-28T09:36:40.1+00:00"}]
    now = dt.datetime(2026, 9, 28, 11, 36, tzinfo=dt.timezone.utc)
    last = es.read_twins_last(rest, now)
    assert last == {("london", "2026-09-28"): dt.datetime(2026, 9, 28, 10, 36, 50, 364465, tzinfo=dt.timezone.utc).timestamp(),
                    ("paris", "2026-09-29"): dt.datetime(2026, 9, 28, 9, 36, 40, 100000, tzinfo=dt.timezone.utc).timestamp()}
    assert seen["path"] == "decisions" and seen["tries"] == 1
    assert seen["params"] == {"select": "city_key,resolution_date,decided_at",
                              "strategy_id": f"eq.{es.MODEL_ONLY[0]}",
                              "decided_at": "gte.2026-09-26T11:36:00Z",
                              "order": "decided_at.desc", "limit": str(es.TWINS_ROWS_MAX)}
    # every reached city-day writes a row for every twin, whatever it decides
    rows, _ = es.decide_all([("c1", ROW)], {}, {}, {}, {}, {}, {}, None, time.monotonic() + 60, "r", "t")
    assert {(r["strategy_id"], r["reason_code"]) for r in rows if r["strategy_id"] == es.MODEL_ONLY[0]} == {
        (es.MODEL_ONLY[0], "no_ledger")}


def test_the_engine_reads_the_twins_turns_beside_its_other_reads(monkeypatch):
    """record() starts the read when it starts and hands decide_all a
    collector; an unread map leaves the tick's order and says why."""
    import common, market_anchor, city_clusters
    import concurrent.futures as cf
    failed = cf.Future()
    failed.set_exception(RuntimeError("down"))
    monkeypatch.setattr(es, "start_twins_read", lambda rest, now: failed)
    got = {}

    def decide_all(*a, **k):
        got["last"] = k["model_only_last"](0.25)
        k["model_only_last"](30.0)
        return [], {"city_days": 1}
    waits = {}
    real_collect = es.collect_status

    def collect_status(future, wait_s=es.GATE_WAIT_S):
        if future is failed:
            waits.setdefault("twins", []).append(wait_s)
        return real_collect(future, wait_s)
    monkeypatch.setattr(es, "collect_status", collect_status)
    monkeypatch.setattr(es, "decide_all", decide_all)
    monkeypatch.setattr(es, "read_ledgers", lambda *a, **k: {})
    monkeypatch.setattr(common, "rest_all", lambda *a, **k: [])
    monkeypatch.setattr(common, "insert", lambda *a, **k: 1)
    monkeypatch.setattr(market_anchor, "load", lambda rest=None: None)
    monkeypatch.setattr(city_clusters, "load", lambda rest=None: None)
    row = dict(ROW, local_decision_time="2026-09-28T13:36:00")
    out = es.record([row], {}, {}, {("london", "2026-09-28"): {"market_id": "m"}}, {}, {},
                    dt.datetime(2026, 9, 28, 11, 36, tzinfo=dt.timezone.utc), deadline=100.0)
    assert "error" not in out, out
    assert got["last"] == {} and out["model_only_order"] == {"read": 0, "unread": "RuntimeError: down"}
    # never waits longer than the twins have left, and never longer than TWINS_WAIT_S (Codex on #342)
    assert waits["twins"] == [0.25, es.TWINS_WAIT_S]



# --------------------------------------------------------------------------
# The twins solve at MODEL_ONLY_BOOK_ITERS and leave time to fill what they buy
# --------------------------------------------------------------------------
def _buy_everything(monkeypatch, seen_params):
    import decision_engine as de
    from strategies import engine_views as ev
    monkeypatch.setattr(ev, "engine_input",
                        lambda sid, ctx, trace=None: ({"probs": PROBS, "strategy_id": sid}, ctx["book"], None))

    def decide(view, *, book, ledger, params):
        seen_params.append((view["strategy_id"], params))
        return {"action": "BUY", "reason_code": "enter", "orders": [], "versions": {}, "binding": []}
    monkeypatch.setattr(de, "decide", decide)


def test_only_the_twins_solve_at_their_own_iterations(monkeypatch):
    from strategies import engine_views as ev
    seen = []
    _buy_everything(monkeypatch, seen)
    rows, _ = es.decide_all([("c1", ROW)], {}, {("london", "2026-09-28"): BANDS}, {"london": "C"}, {},
                            {sid: flat for sid in es.STRATEGIES}, {"clusters": "k"}, None, time.monotonic() + 60,
                            "r", "2026-09-28T11:36:00+00:00")
    by = dict(seen)
    engine = [s for s in es.STRATEGIES if s not in es.S10]          # S10 needs its own ladder; none here
    assert sorted(by) == sorted(engine)
    assert all(by[s] == {"clusters": "k"} for s in engine if s in es.ANCHORED)
    assert all(by[s] == {"clusters": "k", "book_iters": ev.MODEL_ONLY_BOOK_ITERS} for s in engine if s in es.MODEL_ONLY)
    assert ev.MODEL_ONLY_BOOK_ITERS == 1000


def test_s10s_twins_build_their_lock_book_at_the_twins_iterations(monkeypatch):
    from strategies import engine_views as ev
    import strategies.s10_max_temp_winner as s10
    seen = {}

    def decide(variant, **k):
        seen[variant, k.get("book_iters")] = True
        return {"action": "NONE", "reason": "test"}
    monkeypatch.setattr(s10, "decide", decide)
    ctx = {"bands": BANDS, "unit": "C", "probs": dict(PROBS), "book": MARKET, "checkpoint": "noon",
           "anchor": {"table": None, "city": "london"}}
    for sid in ("s10_lock", "s10_lock_model"):
        ev.engine_input(sid, dict(ctx), {})
    assert seen == {("s10_lock", None): True, ("s10_lock", ev.MODEL_ONLY_BOOK_ITERS): True}


def test_the_twins_stop_in_time_to_fill_what_they_bought(monkeypatch):
    """8 Oct 11:36Z: three twin plans published with the tick nearly out; the
    one the rails passed never filled. Each BUY moves the twins' stop earlier
    by its fill time."""
    seen = []
    _buy_everything(monkeypatch, seen)
    clock = iter([0.0, 0.0,          # the anchored pass, both city-days
                  0.0,               # the twins' first city-day
                  33.0])             # their second: inside 40 s, not inside 40 - 3 BUYs x 2.5
    monkeypatch.setattr(es.time, "monotonic", lambda: next(clock))
    cks = [("c1", ROW), ("c2", dict(ROW, city_key="paris"))]
    buys = []
    rows, detail = es.decide_all(cks, {}, {("london", "2026-09-28"): BANDS}, {"london": "C", "paris": "C"}, {},
                                 {sid: flat for sid in es.STRATEGIES}, {}, None, 50.0, "r",
                                 "2026-09-28T11:36:00+00:00", buys=buys, model_only_deadline=40.0,
                                 model_only_fill_s=2.5)
    assert detail["model_only_reached"] == 1 and detail["model_only_out_of_time"] == 1
    assert detail["reached"] == 2 and detail["out_of_time"] == 0, "the anchored pass never pays for it"
    assert len([b for b in buys if b[0]["strategy_id"] in es.MODEL_ONLY]) == 3      # s11 x2, s12
    # with no BUY yet the twins keep their whole window
    seen.clear()
    clock = iter([0.0, 0.0, 39.0])
    monkeypatch.setattr(es.time, "monotonic", lambda: next(clock))
    _rows, detail = es.decide_all([("c1", ROW)], {}, {}, {"london": "C"}, {}, {sid: flat for sid in es.STRATEGIES},
                                  {}, None, 50.0, "r", "2026-09-28T11:36:00+00:00", buys=[],
                                  model_only_deadline=40.0, model_only_fill_s=2.5)
    assert detail["model_only_reached"] == 1


def test_the_tick_hands_the_twins_the_fill_time():
    src = (ROOT / "scripts" / "engine_shadow.py").read_text()
    assert "model_only_last=twins_last, model_only_fill_s=engine_orders.FILL_SECONDS)" in src


def test_it_never_raises_into_the_tick(monkeypatch):
    import common

    def boom(*a, **k):
        raise RuntimeError("database down")
    monkeypatch.setattr(common, "rest_all", boom)
    monkeypatch.setattr(common, "rest", boom)
    import datetime as dt
    out = es.record([ROW], {}, {}, {}, {}, {}, dt.datetime(2026, 9, 28, 11, 36, tzinfo=dt.timezone.utc),
                    deadline=time.monotonic() + 30)
    assert "error" in out and out["written"] == 0


def test_the_migration_registers_them_disabled_and_lets_switch_be_recorded():
    sql = (ROOT / "supabase" / "migrations" / "20260927150000_engine_strategies_are_registered.sql").read_text()
    for sid in es.ANCHORED:
        assert f"('{sid}'," in sql
    assert sql.count(", false,") == len(es.ANCHORED)                # none enabled
    assert "on conflict (strategy_id) do nothing" in sql
    assert "'SWITCH'" in sql and "drop constraint if exists decisions_action_check" in sql
    assert "s2_combination_arb" not in es.STRATEGIES


def test_the_model_only_twins_are_registered_off_then_put_in_shadow_once():
    """20261008100000: each twin registered switched off (its ledger opens),
    its automatic exits off as its base's are, then into shadow only from the
    state registration gave it - a re-run never switches a twin back on."""
    from strategies import engine_views as ev
    sql = (ROOT / "supabase" / "migrations" / "20261008100000_the_model_only_twins_trade_on_paper.sql").read_text()
    assert es.MODEL_ONLY == tuple(f"{s}_model" for s in es.ANCHORED)
    assert set(es.MODEL_ONLY) == set(ev.MODEL_ONLY)
    for sid in es.MODEL_ONLY:
        assert f"('{sid}'," in sql
        assert f'"base":"{ev.base_of(sid)}"' in sql
        assert sql.count(f"'{sid}'") == 3, sid                     # the row, the exits, the state
    assert sql.count("'YES', false,") + sql.count("'NO', false,") == len(es.MODEL_ONLY)
    assert "on conflict (strategy_id) do nothing" in sql
    assert """'{"auto_exit_enabled": false}'""" in sql
    assert "state = 'research' and reason = 'registered'" in sql
    assert "public.set_strategy_state(r.strategy_id, 'shadow'," in sql
    assert "portfolio" not in sql.split("=" * 75)[-1]                 # the body never names the portfolio


def test_the_reading_age_is_minutes_from_the_station_reading_to_the_decision():
    assert es.reading_age("2026-09-28T11:10:00+00:00", "2026-09-28T11:36:00+00:00") == 26.0
    assert es.reading_age("2026-09-28T11:10:00Z", "2026-09-28T11:36:00+00:00") == 26.0
    assert es.reading_age(None, "2026-09-28T11:36:00+00:00") is None
    assert es.reading_age("2026-09-28T11:40:00+00:00", "2026-09-28T11:36:00+00:00") is None, "a reading after the decision"
    assert es.reading_age("not a time", "2026-09-28T11:36:00+00:00") is None


def _s10_rows(floors):
    ledgers = {sid: flat for sid in es.STRATEGIES}
    rows, _ = es.decide_all([("cp1", ROW)], {("london", "2026-09-28", "noon"): PROBS},
                            {("london", "2026-09-28"): BANDS}, {"london": "C"}, floors, ledgers, {}, None,
                            time.monotonic() + 60, "run-1", "2026-09-28T11:36:00+00:00")
    return [r for r in rows if r["strategy_id"] in es.S10]


def test_s10_hears_how_old_the_station_reading_is():
    """Until 27 Sep the engine passed S10 no reading age, and S10 waits on a
    reading it cannot date: 123 of 123 S10 rows with a ladder were
    own_rule_wait. A fresh reading on the target's own day lets S10's other
    rules speak (here: nothing grows at these asks, so NONE); a stale one, or
    none, is still a WAIT."""
    fresh = _s10_rows({"london": ("2026-09-28", 18.0, "series", "2026-09-28T11:10:00+00:00")})
    assert {r["reason_code"] for r in fresh} == {"own_rule_none"}, fresh
    stale = _s10_rows({"london": ("2026-09-28", 18.0, "series", "2026-09-28T10:00:00+00:00")})
    assert {r["reason_code"] for r in stale} == {"own_rule_wait"}, "96 minutes old is past the 75-minute limit"
    none = _s10_rows({"london": ("2026-09-28", 18.0, "series", None)})
    assert {r["reason_code"] for r in none} == {"own_rule_wait"}


def test_an_s10_row_records_the_parameters_its_own_rule_used():
    """Until 27 Sep an S10 row that declined by its own rule had no
    params_version (33 of 33 at 20:36Z), though its ladder was pulled toward
    the market; the replay is compared with live for the same params version
    (P5.12 acceptance)."""
    import json
    from strategies import s10_max_temp_winner as s10
    rows = _s10_rows({"london": ("2026-09-28", 18.0, "series", "2026-09-28T11:10:00+00:00")})
    assert rows and all(r["params_version"] for r in rows)
    for r in rows:
        pv = json.loads(r["params_version"])
        assert pv["s10"] == {"view": s10.VIEW_VERSION, "h_switch": s10.h_switch()}
        want = ("model-only:w1", 1.0) if r["strategy_id"] in es.MODEL_ONLY else ("prior", 0.0)
        assert (pv["market_anchor"]["version"], pv["market_anchor"]["w"]) == want, r["strategy_id"]
        assert "scope" in pv["market_anchor"]
    assert {r["strategy_id"] for r in rows} == set(es.S10)
    row = es.decision_row("run", "t", "cp", "s11_ladder", "london", "2026-09-28", why="no ladder")
    assert es.stamp_s10(row, None) is row, "a row S10's own rule did not decide passes through"


def test_the_ledger_the_engine_read_is_kept_for_the_replay():
    """v_city_running_max and the ledgers are read as of now and kept nowhere;
    the tick records them beside the decisions (P5.12 acceptance)."""
    def rest(path, params=None, **k):
        return {"paper_accounts": [{"account_id": "a1", "strategy_id": "s10_winner", "cash": 970.0,
                                    "reserved_cash": 6.17}],
                "v_desk_risk_state": [{"account_id": "a1", "high_water": 1000.0}]}.get(path, [])

    def rest_all(path, params=None, **k):
        return {"paper_positions": [{"account_id": "a1", "band_id": "b2", "side": "YES", "shares": 60.0,
                                     "cost_basis": 30.0}],
                "paper_orders": [{"account_id": "a1", "band_id": "b3", "cash_ceiling": 6.17}],
                "paper_trades": [{"account_id": "a1", "net_pnl": -1.5}],
                "bands": [{"band_id": "b2", "market_id": "m1"}, {"band_id": "b3", "market_id": "m1"}],
                "markets": [{"market_id": "m1", "city_key": "london", "resolution_date": "2026-09-28"}]}.get(path, [])
    snap = {}
    led = es.read_ledgers(rest, rest_all, dt.datetime(2026, 9, 28, 11, 36, tzinfo=dt.timezone.utc), snapshot=snap)
    assert snap == {"s10_winner": {"cash": 970.0, "reserved_cash": 6.17, "high_water": 1000.0, "pnl_today": -1.5,
                                   "positions": [["b2", "YES", 60.0, 30.0, ["london", "2026-09-28"]]],
                                   "orders": [["b3", 6.17, ["london", "2026-09-28"]]]}}
    json.dumps(snap)
    assert led["s10_winner"]("london", "2026-09-28")["held"] == {"b2": (60.0, 0.0)}
    src = (ROOT / "scripts" / "engine_shadow.py").read_text()
    assert 'out["inputs"] = {"decided_at": now.isoformat(),' in src


def test_yesterdays_reading_vouches_for_nothing_today(monkeypatch):
    from strategies import engine_views as ev
    seen = []
    monkeypatch.setattr(ev, "engine_input",
                        lambda sid, ctx, trace=None: (seen.append((sid, ctx)), (None, None, "no ladder"))[1])
    floors = {"london": ("2026-09-27", 18.0, "series", "2026-09-28T11:10:00+00:00")}
    es.decide_all([("cp1", ROW)], {("london", "2026-09-28", "noon"): PROBS}, {("london", "2026-09-28"): BANDS},
                  {"london": "C"}, floors, {sid: flat for sid in es.STRATEGIES}, {}, None,
                  time.monotonic() + 60, "run-1", "2026-09-28T11:36:00+00:00")
    assert seen and all(c["floor_c"] is None and c["floor_basis"] is None and c["reading_age_min"] is None
                        for _sid, c in seen)
    seen.clear()
    floors = {"london": ("2026-09-28", 18.0, "series", "2026-09-28T11:10:00+00:00")}
    es.decide_all([("cp1", ROW)], {("london", "2026-09-28", "noon"): PROBS}, {("london", "2026-09-28"): BANDS},
                  {"london": "C"}, floors, {sid: flat for sid in es.STRATEGIES}, {}, None,
                  time.monotonic() + 60, "run-1", "2026-09-28T11:36:00+00:00")
    assert all((c["floor_c"], c["floor_basis"], c["reading_age_min"]) == (18.0, "series", 26.0) for _s, c in seen)


def test_the_tick_hands_the_engine_the_readings_time():
    src = (ROOT / "scripts" / "tick.py").read_text()
    assert '(running.get(c) or {}).get("latest_reading_at")) for c, (d, f) in floors.items()}' in src


# --------------------------------------------------------------------------
# P2.2 part 3: each decision names the call it acted on
# --------------------------------------------------------------------------
def _decide(s10_ladders, checkpoint_id="cp1"):
    ledgers = {sid: flat for sid in es.STRATEGIES}
    rows, _ = es.decide_all([(checkpoint_id, ROW)], s10_ladders, {("london", "2026-09-28"): BANDS},
                            {"london": "C"}, {}, ledgers, {}, None, time.monotonic() + 60, "run-1",
                            "2026-09-28T11:36:00+00:00")
    return {r["strategy_id"]: r for r in rows}


def test_s11_and_s12_name_the_engine_call_and_s10_the_stored_s10_row():
    """Live on 4 Oct, every S10 decision with a checkpoint (1,833) named the
    engine's call, though S10 acts on its own ladder."""
    by = _decide({("london", "2026-09-28", "noon"): {"probs": PROBS, "id": "s10-row-1"}})
    for sid in ("s11_ladder", "s11_lock", "s12_no"):
        assert (by[sid]["prediction_id"], by[sid]["prediction_source"]) == ("cp1", "prediction_checkpoints"), sid
    for sid in es.S10:
        assert (by[sid]["prediction_id"], by[sid]["prediction_source"]) == ("s10-row-1", "s10_shadow_checkpoints"), sid
        assert by[sid]["checkpoint_id"] == "cp1", "checkpoint_id still names the tick's checkpoint"


def test_a_call_the_record_does_not_hold_is_not_named():
    bare = _decide({("london", "2026-09-28", "noon"): PROBS})              # the replay's shape
    unread = _decide({("london", "2026-09-28", "noon"): {"probs": PROBS, "id": None}})
    for by in (bare, unread):
        for sid in es.S10:
            assert (by[sid]["prediction_id"], by[sid]["prediction_source"]) == (None, None), sid
            assert by[sid]["reason_code"].startswith("own_rule_"), "it still decides on the ladder it has"
    none = _decide({})
    for sid in es.S10:
        assert none[sid]["reason_code"] == "no_ladder" and none[sid]["prediction_id"] is None
    assert _decide({}, checkpoint_id=None)["s11_ladder"]["prediction_id"] is None


def test_the_two_columns_are_named_together_as_the_table_requires():
    """decisions_prediction_named: prediction_id and prediction_source are
    both set or both null (20261004210000)."""
    for by in (_decide({("london", "2026-09-28", "noon"): {"probs": PROBS, "id": "x"}}), _decide({})):
        for r in by.values():
            assert (r["prediction_id"] is None) == (r["prediction_source"] is None), r
            assert r["prediction_source"] in (None, "prediction_checkpoints", "s10_shadow_checkpoints")
    mig = (ROOT / "supabase" / "migrations" / "20261004210000_decisions_name_their_call.sql").read_text()
    assert "check\n      ((prediction_id is null) = (prediction_source is null))" in mig


def test_the_twins_stop_at_their_own_deadline(monkeypatch):
    """8 Oct: the model-only twins stop MODEL_ONLY_RESERVE_S before the engine's
    deadline, so variant_shadow keeps its time; the anchored pass is untouched."""
    clock = iter([0.0, 0.0] + [20.0] * 10)
    monkeypatch.setattr(es.time, "monotonic", lambda: next(clock))
    ledgers = {sid: flat for sid in es.STRATEGIES}
    rows, detail = es.decide_all([("cp1", ROW), ("cp2", dict(ROW, city_key="paris"))], {},
                                 {("london", "2026-09-28"): BANDS}, {"london": "C", "paris": "C"}, {},
                                 ledgers, {}, None, 50.0, "r", "2026-09-28T11:36:00+00:00",
                                 model_only_deadline=10.0)
    assert detail["reached"] == 2 and detail["model_only_reached"] == 0 and detail["model_only_out_of_time"] == 2
    assert {r["strategy_id"] for r in rows} == set(es.ANCHORED)


def test_record_sends_the_anchored_orders_on_the_engines_deadline_and_the_twins_on_theirs(monkeypatch):
    import datetime as dt
    import engine_orders
    calls = []
    monkeypatch.setattr(es, "send_orders", lambda b, run, deadline, dry, exits=(), max_fills=None: calls.append(
        (sorted({x[0]["strategy_id"] for x in b}), deadline, max_fills)) or {"buys": len(b), "fills": {"filled": 5}})

    def decide_all(*a, **k):
        k["buys"].extend([({"strategy_id": "s12_no"}, {}, "cp"), ({"strategy_id": "s12_no_model"}, {}, "cp")])
        assert k["model_only_deadline"] == 100.0 - es.MODEL_ONLY_RESERVE_S - 2 * engine_orders.FILL_SECONDS
        return [{"action": "BUY"}], {"city_days": 1}
    monkeypatch.setattr(es, "decide_all", decide_all)
    monkeypatch.setattr(es, "read_ledgers", lambda *a, **k: {})
    monkeypatch.setattr(es, "collect_status", lambda f: ({}, None))
    import common, market_anchor, city_clusters
    monkeypatch.setattr(common, "rest_all", lambda *a, **k: [])
    monkeypatch.setattr(common, "insert", lambda *a, **k: 1)
    monkeypatch.setattr(market_anchor, "load", lambda rest=None: None)
    monkeypatch.setattr(city_clusters, "load", lambda rest=None: None)
    row = dict(ROW, local_decision_time="2026-09-28T13:36:00")
    out = es.record([row], {}, {}, {("london", "2026-09-28"): {"market_id": "m"}}, {}, {},
                    dt.datetime(2026, 9, 28, 11, 36, tzinfo=dt.timezone.utc), deadline=100.0)
    assert "error" not in out, out
    # MAX_FILLS is per tick: the twins get what the anchored orders left (Codex on #341)
    assert calls == [(["s12_no"], 100.0 - es.ORDER_RESERVE_S, None),
                     (["s12_no_model"], 100.0 - es.MODEL_ONLY_RESERVE_S, engine_orders.MAX_FILLS - 5)]
    assert out["orders"]["buys"] == 1 and out["model_only_orders"]["buys"] == 1
