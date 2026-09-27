"""Plan v2 P5.12 part 3a: the one engine decides at the tick's checkpoints and
records it in `decisions`, ordering nothing (scripts/engine_shadow.py)."""
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
    assert detail == {"city_days": 1, "reached": 1, "out_of_time": 0}
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
    assert rows == [] and detail == {"city_days": 2, "reached": 0, "out_of_time": 2}


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
    for sid in es.STRATEGIES:
        assert f"('{sid}'," in sql
    assert sql.count(", false,") == len(es.STRATEGIES)              # none enabled
    assert "on conflict (strategy_id) do nothing" in sql
    assert "'SWITCH'" in sql and "drop constraint if exists decisions_action_check" in sql
    assert "s2_combination_arb" not in es.STRATEGIES
