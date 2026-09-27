"""Plan v2 P5.12 acceptance: the replay of a live day, on the inputs the tick
recorded, through the same decide_all (scripts/engine_replay_live.py)."""
import datetime as dt
import functools
import json
import pathlib
import time

import engine_replay_live as rl
import engine_shadow as es
from test_engine_shadow import BANDS, PROBS, ROW

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLOORS = {"london": ("2026-09-28", 18.0, "series", "2026-09-28T11:10:00+00:00")}
NOW = dt.datetime(2026, 9, 28, 11, 36, tzinfo=dt.timezone.utc)
# b2 is offered well under its probability: the ladder strategies have
# something to buy, and S10 holds b1 - it has something to weigh.
CHEAP = {b: {"bid": round(p - 0.01, 3), "ask": round(p + 0.02, 3), "last": p} for b, p in PROBS.items()}
CHEAP["b2"] = {"bid": 0.28, "ask": 0.30, "last": 0.29}
LIVE_ROW = dict(ROW, market=CHEAP)
S10_LADDER = {"b0": 0.02, "b1": 0.18, "b2": 0.6, "b3": 0.15, "b4": 0.05}


def _read_ledgers():
    """The ledgers as the tick reads them, and the snapshot it records."""
    accounts = [{"account_id": f"a-{sid}", "strategy_id": sid, "cash": 1000.0, "reserved_cash": 0.0}
                for sid in es.STRATEGIES]
    accounts[0] = dict(accounts[0], cash=990.0)                     # s10_winner holds b1

    def rest(path, params=None, **k):
        return {"paper_accounts": accounts,
                "v_desk_risk_state": [{"account_id": a["account_id"], "high_water": 1000.0} for a in accounts]
                }.get(path, [])

    def rest_all(path, params=None, **k):
        return {"paper_positions": [{"account_id": "a-s10_winner", "band_id": "b1", "side": "YES", "shares": 50.0,
                                     "cost_basis": 10.0}],
                "bands": [{"band_id": "b1", "market_id": "m1"}],
                "markets": [{"market_id": "m1", "city_key": "london", "resolution_date": "2026-09-28"}]
                }.get(path, [])
    snap = {}
    led = es.read_ledgers(rest, rest_all, NOW, snapshot=snap)
    return led, snap


@functools.lru_cache(maxsize=1)
def _live():
    """A live run: decide_all as the tick calls it, and what the tick records.
    Shared by the tests (none changes it): the solver makes a run ~1 s."""
    assert es.STRATEGIES[0] == "s10_winner"
    led, snap = _read_ledgers()
    rows, _ = es.decide_all([("cp-1", LIVE_ROW)], {("london", "2026-09-28", "noon"): S10_LADDER},
                            {("london", "2026-09-28"): BANDS}, {"london": "C"}, FLOORS, led, {"clusters": None},
                            None, time.monotonic() + 60, "run-1", NOW.isoformat())
    inputs = json.loads(json.dumps({"decided_at": NOW.isoformat(), "floors": {c: list(v) for c, v in FLOORS.items()},
                                    "ledgers": snap}))
    checkpoints = {"cp-1": json.loads(json.dumps(LIVE_ROW))}
    return rows, inputs, checkpoints


def _replay(rows, inputs, checkpoints, tables=None):
    return rl.replay_run(rows, inputs, checkpoints, {("london", "2026-09-28", "noon"): S10_LADDER},
                         {("london", "2026-09-28"): BANDS}, {"london": "C"}, tables or {})


def test_a_recorded_run_replays_to_the_same_decisions():
    rows, inputs, checkpoints = _live()
    assert {r["action"] for r in rows} - {"NONE", "WAIT", "HOLD"}, "the fixture must decide something"
    replayed, why = _replay(rows, inputs, checkpoints)
    assert why is None
    c = rl.compare(rows, replayed)
    assert (c["rows"], c["same_version"], c["matched"]) == (len(rows), len(rows), len(rows)), c["mismatches"]
    assert rl.verdict(c) == (1.0, True)


def test_the_replay_hears_what_was_recorded_not_what_is_now():
    rows, inputs, checkpoints = _live()
    stale = dict(inputs, floors={"london": ["2026-09-28", 18.0, "series", "2026-09-28T09:00:00+00:00"]})
    replayed, _ = _replay(rows, stale, checkpoints)
    c = rl.compare(rows, replayed)
    assert c["matched"] < c["same_version"] and c["mismatches"], "a different reading age decides differently"
    assert {m["strategy_id"] for m in c["mismatches"]} <= set(es.S10)
    share, ok = rl.verdict(c)
    assert ok is False and share < rl.ACCEPT


def test_a_row_decided_on_other_parameters_is_counted_apart():
    rows, inputs, checkpoints = _live()
    replayed, _ = _replay(rows, inputs, checkpoints)
    i = next(i for i, r in enumerate(rows) if '"engine"' in (r["params_version"] or ""))
    other = list(rows)
    other[i] = dict(rows[i], params_version=json.dumps(dict(json.loads(rows[i]["params_version"]), engine="engine-v0")))
    c = rl.compare(other, replayed)
    assert c["other_version"] == 1 and c["same_version"] == len(rows) - 1
    assert rl.canonical('{"b": 1, "a": 2}') == rl.canonical('{"a": 2, "b": 1}')
    # How far a row got decides which versions it records, not which it used.
    own_rule = json.dumps({"market_anchor": {"version": "prior"}, "s10": {"view": "v1"}})
    reached = json.dumps({"market_anchor": {"version": "prior"}, "s10": {"view": "v1"}, "engine": "engine-v1"})
    assert rl.same_params(own_rule, reached) and rl.same_params(None, reached)
    assert not rl.same_params(reached, json.dumps({"engine": "engine-v2"}))


def test_a_learned_version_must_be_found():
    live = [{"params_version": json.dumps({"clusters": "prior", "market_anchor": {"version": "mw-3"}})}]
    assert rl.learned_tables(live, {}) == (None, None, "market_weight mw-3 not found")
    assert rl.learned_tables(live, {("market_weight", "mw-3"): {"w": 1}}) == (None, {"w": 1}, None)
    assert rl.learned_tables([{"params_version": None}], {}) == (None, None, None)


def test_a_run_without_recorded_inputs_is_skipped_and_counted():
    rows, inputs, checkpoints = _live()
    s10 = {("london", "2026-09-28", "noon", "v1"): S10_LADDER}
    runs = {"run-1": {"inputs": inputs, "s10_version": "v1"}, "run-0": {"inputs": None}}
    decisions = {"run-1": rows, "run-0": [dict(r, run_id="run-0") for r in rows]}
    t = rl.replay_day(runs, decisions, checkpoints, s10, {("london", "2026-09-28"): BANDS}, {"london": "C"}, {})
    assert t["runs"] == 1 and t["runs_skipped"] == {"no recorded inputs": 1}
    assert t["matched"] == len(rows) and t["share"] == 1.0 and t["accepted"] is True
    missing = rl.replay_run(rows, inputs, {}, {}, {}, {}, {})
    assert missing == (None, "1 checkpoint rows not found")


def test_it_runs_nightly_after_the_day():
    wf = (ROOT / ".github" / "workflows" / "pipeline_daily.yml").read_text()
    assert "run: python scripts/engine_replay_live.py" in wf
