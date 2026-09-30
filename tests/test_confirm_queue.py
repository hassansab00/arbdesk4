"""The recent-ladder queue (plan v2.2 P4.7, finished 30 Sep; handoff F1).

30 Sep 09:21Z, as anon: every US city's newest settled day was 28 Sep. The
venue had closed Atlanta's 29 Sep ladder at 05:13-05:21Z; the hourly step,
asking one band at a time, reached 2-34 of ~150 bands a run. These tests hold
the queue to what the supplement asked: eligibility from the city's own clock
(never from C/F), oldest need first, a backoff so one slow ladder cannot hold
the budget, one venue request per ladder, proofs only through verify(), and a
run that ran out of budget reported as partial.
"""
import datetime as dt
import hashlib
import importlib
import json

import pytest

import common
import confirm_queue as cq
import paper_settlement as settlement

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 30, 9, 36, tzinfo=UTC)


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tz,day,end", [
    ("America/New_York", "2026-09-29", "2026-09-30T04:00:00+00:00"),
    ("America/Los_Angeles", "2026-09-29", "2026-09-30T07:00:00+00:00"),
    ("Asia/Tokyo", "2026-09-29", "2026-09-29T15:00:00+00:00"),
    ("Europe/London", "2026-09-29", "2026-09-29T23:00:00+00:00"),
    # daylight saving ends in New York on 1 Nov 2026: that day is 25 hours long
    ("America/New_York", "2026-11-01", "2026-11-02T05:00:00+00:00"),
    ("Asia/Kolkata", "2026-09-29", "2026-09-29T18:30:00+00:00"),
])
def test_the_day_ends_on_the_citys_own_clock(tz, day, end):
    assert cq.local_day_end(day, tz).isoformat() == end


def test_no_timezone_is_no_eligibility_not_a_guess():
    assert cq.local_day_end("2026-09-29", None) is None


def _m(n, city, day="2026-09-29"):
    return {"market_id": f"m{n}", "city_key": city, "resolution_date": day}


TZ = {"nyc": "America/New_York", "la": "America/Los_Angeles", "tokyo": "Asia/Tokyo",
      "mexico_city": "America/Mexico_City", "seattle": "America/Los_Angeles"}


def test_oldest_need_first_and_days_not_ended_wait():
    markets = [_m(1, "la"), _m(2, "nyc"), _m(3, "tokyo", "2026-09-30"), _m(4, "tokyo")]
    due, counts = cq.queue_order(markets, TZ, {}, NOW)
    # tokyo 29 Sep ended 15:00Z 29 Sep, nyc 04:00Z, la 07:00Z; tokyo 30 Sep ends 15:00Z 30 Sep
    assert [m["market_id"] for m in due] == ["m4", "m2", "m1"]
    assert counts["not_ended"] == 1


def test_eligibility_is_the_timezone_not_the_unit():
    """Mexico City is a Celsius city on an American clock: its 29 Sep ends at
    06:00Z 30 Sep, like a US city's, not at a 'C city' time."""
    due, _ = cq.queue_order([_m(1, "mexico_city")], TZ, {}, dt.datetime(2026, 9, 30, 5, 59, tzinfo=UTC))
    assert due == []
    due, _ = cq.queue_order([_m(1, "mexico_city")], TZ, {}, dt.datetime(2026, 9, 30, 6, 0, tzinfo=UTC))
    assert [m["market_id"] for m in due] == ["m1"]


def test_a_ladder_found_unresolved_waits_longer_each_time():
    assert [cq.backoff_minutes(k) for k in range(7)] == [0, 20, 40, 80, 160, 160, 160]
    markets = [_m(1, "nyc"), _m(2, "la"), _m(3, "seattle")]
    attempts = {
        "m1": {"last_asked_at": "2026-09-30T09:26:00+00:00", "unresolved_streak": 1},   # 10 min ago, waits 20
        "m2": {"last_asked_at": "2026-09-30T08:36:00+00:00", "unresolved_streak": 2},   # 60 min ago, waits 40
        "m3": {"last_asked_at": "2026-09-30T08:36:00+00:00", "unresolved_streak": 3},   # 60 min ago, waits 80
    }
    due, counts = cq.queue_order(markets, TZ, attempts, NOW)
    assert [m["market_id"] for m in due] == ["m2"]
    assert counts["backing_off"] == 2


def test_a_market_never_asked_goes_before_nothing_it_is_just_due():
    due, _ = cq.queue_order([_m(1, "nyc")], TZ, {"m1": {"last_asked_at": None, "unresolved_streak": 0}}, NOW)
    assert len(due) == 1


def test_gamma_is_asked_with_repeated_condition_ids():
    """A comma list returns [] (tested against the venue 30 Sep)."""
    p = cq.gamma_params(["0xa", "0xb"])
    assert p == [("condition_ids", "0xa"), ("condition_ids", "0xb"), ("closed", "true")]


@pytest.mark.parametrize("asked,resolved,captured,failed,out", [
    (0, 0, 0, 0, "proven"),
    (11, 0, 0, 0, "not_closed"),
    (11, 4, 4, 0, "partial"),
    (11, 11, 11, 0, "resolved"),
    (11, 11, 10, 0, "partial"),     # one void band: the ladder is not complete
    (11, 11, 0, 1, "failed"),
    (11, 11, 10, 1, "partial"),
])
def test_what_an_attempt_found(asked, resolved, captured, failed, out):
    assert cq.ladder_outcome(asked, resolved, captured, failed) == out


# ---------------------------------------------------------------------------
# run(), against scripted tables and a scripted venue
# ---------------------------------------------------------------------------
def _band(market, i, winner=False):
    return {"band_id": f"{market}-b{i}", "market_id": market, "condition_id": f"0x{market}{i}",
            "token_yes": f"y-{market}{i}", "token_no": f"n-{market}{i}", "_winner": winner}


def _gamma(b, closed=True, status="resolved", yes_price=None, closed_time="2026-09-30 05:15:17+00"):
    win = b["_winner"] if yes_price is None else yes_price == "1"
    return {"conditionId": b["condition_id"], "closed": closed, "umaResolutionStatus": status,
            "clobTokenIds": json.dumps([b["token_yes"], b["token_no"]]), "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps(["1", "0"] if win else ["0", "1"]) if yes_price != "0.5"
            else '["0.5","0.5"]', "closedTime": closed_time}


def _clob(b, winner_yes=None):
    win = b["_winner"] if winner_yes is None else winner_yes
    return {"condition_id": b["condition_id"], "closed": True, "accepting_orders": False,
            "tokens": [{"token_id": b["token_yes"], "winner": win},
                       {"token_id": b["token_no"], "winner": not win}]}


@pytest.fixture
def world(monkeypatch):
    state = {"markets": [], "attempts": [], "bands": [], "proven": set(), "gamma": {}, "clob": {},
             "writes": [], "rpcs": [], "logs": [], "gamma_calls": [], "clob_calls": [], "clock": [0.0]}

    def rest_all(path, params=None, *, order, page_size=500):
        pairs = dict(params.items()) if isinstance(params, dict) else dict(params or [])
        if path == "markets":
            return list(state["markets"])
        if path == "cities":
            return [{"city_key": k, "timezone": v} for k, v in TZ.items()]
        if path == "market_confirmation_attempts":
            return list(state["attempts"])
        if path == "bands":
            wanted = pairs["market_id"][4:-1].split(",")
            return [{k: v for k, v in b.items() if not k.startswith("_")}
                    for b in state["bands"] if b["market_id"] in wanted]
        if path == "resolution_verdicts":
            wanted = pairs["condition_id"][4:-1].split(",")
            return [{"condition_id": c} for c in wanted if c in state["proven"]]
        raise AssertionError(path)

    def get(url, params):
        if "gamma-api" in url:
            conds = [v for k, v in params if k == "condition_ids"]
            state["gamma_calls"].append(conds)
            return [state["gamma"][c] for c in conds if c in state["gamma"]]
        cond = url.rsplit("/", 1)[1]
        state["clob_calls"].append(cond)
        v = state["clob"][cond]
        if isinstance(v, Exception):
            raise v
        return v

    # the modules run() imports at call time, whatever another test left in
    # sys.modules, not the objects this file bound at collection
    live_common = importlib.import_module("common")
    live_settlement = importlib.import_module("paper_settlement")
    monkeypatch.setattr(live_common, "rest_all", rest_all)
    monkeypatch.setattr(live_settlement, "rest_all", rest_all)
    monkeypatch.setattr(live_common, "upsert", lambda table, rows, key: state["writes"].extend(
        (table, r) for r in rows) or len(rows))
    monkeypatch.setattr(live_common, "rpc", lambda fn, args=None: state["rpcs"].append((fn, args)) or 0)
    monkeypatch.setattr(live_common, "log_run", lambda *a: state["logs"].append(a))
    state["get"] = get
    return state


def _ladder(state, market, city, n=3, winner=1, day="2026-09-29"):
    state["markets"].append(_m(market[1:], city, day) | {"market_id": market})
    bands = [_band(market, i, winner=(i == winner)) for i in range(n)]
    state["bands"] += bands
    return bands


def test_a_resolved_ladder_is_proven_in_one_venue_request_plus_its_clob_checks(world):
    bands = _ladder(world, "mA", "nyc")
    for b in bands:
        world["gamma"][b["condition_id"]] = _gamma(b)
        world["clob"][b["condition_id"]] = _clob(b)
    d = cq.run(budget_seconds=30, now=NOW, get=world["get"])
    assert len(world["gamma_calls"]) == 1 and len(world["gamma_calls"][0]) == 3
    assert sorted(world["clob_calls"]) == sorted(b["condition_id"] for b in bands)
    proofs = [r for t, r in world["writes"] if t == "paper_resolution_evidence"]
    assert len(proofs) == 3
    winner = next(p for p in proofs if p["condition_id"] == bands[1]["condition_id"])
    assert winner["winning_token"] == bands[1]["token_yes"]
    # the proof's identity is exactly paper_settlement.cycle()'s
    g, c = winner["gamma"], winner["clob"]
    assert winner["proof_id"] == hashlib.sha256(json.dumps(
        {"gamma": g, "clob": c}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rec = [a for fn, a in world["rpcs"] if fn == "record_market_confirmation_attempts"][0]["p_rows"]
    assert rec[0]["outcome"] == "resolved" and rec[0]["bands_proven"] == 3
    assert rec[0]["local_day_end"] == "2026-09-30T04:00:00+00:00"
    assert rec[0]["venue_closed_at"] == "2026-09-30T05:15:17+00:00"
    assert d["completed"] == 1 and d["evidence_captured"] == 3 and d["pending_after"] == 0
    assert world["logs"][0][:2] == ("venue_confirm_queue", "ok")


def test_a_ladder_the_venue_has_not_closed_costs_one_request(world):
    _ladder(world, "mB", "la", n=11)
    d = cq.run(budget_seconds=30, now=NOW, get=world["get"])
    assert len(world["gamma_calls"]) == 1 and world["clob_calls"] == []
    rec = [a for fn, a in world["rpcs"] if fn == "record_market_confirmation_attempts"][0]["p_rows"]
    assert rec[0]["outcome"] == "not_closed" and rec[0]["venue_closed_at"] is None
    assert d["venue_calls"] == 1 and d["pending_after"] == 1 and d["oldest_pending_hours"] == 2.6


def test_proposed_is_not_resolved_and_a_void_payout_is_never_a_proof(world):
    bands = _ladder(world, "mC", "nyc")
    world["gamma"][bands[0]["condition_id"]] = _gamma(bands[0], status="proposed")
    world["gamma"][bands[1]["condition_id"]] = _gamma(bands[1], yes_price="0.5")
    world["gamma"][bands[2]["condition_id"]] = _gamma(bands[2])
    world["clob"][bands[1]["condition_id"]] = _clob(bands[1])
    world["clob"][bands[2]["condition_id"]] = _clob(bands[2])
    d = cq.run(budget_seconds=30, now=NOW, get=world["get"])
    proofs = [r for t, r in world["writes"] if t == "paper_resolution_evidence"]
    assert [p["condition_id"] for p in proofs] == [bands[2]["condition_id"]]
    assert d["void_or_split_bands"] == 1
    rec = [a for fn, a in world["rpcs"] if fn == "record_market_confirmation_attempts"][0]["p_rows"]
    assert rec[0]["outcome"] == "partial"


def test_sources_that_disagree_are_counted_not_paid_and_not_raised(world):
    bands = _ladder(world, "mD", "nyc")
    for b in bands:
        world["gamma"][b["condition_id"]] = _gamma(b)
        world["clob"][b["condition_id"]] = _clob(b)
    world["clob"][bands[1]["condition_id"]] = _clob(bands[1], winner_yes=False)   # CLOB disagrees
    world["clob"][bands[0]["condition_id"]] = TimeoutError("slow")
    d = cq.run(budget_seconds=30, now=NOW, get=world["get"])
    proofs = [r for t, r in world["writes"] if t == "paper_resolution_evidence"]
    assert [p["condition_id"] for p in proofs] == [bands[2]["condition_id"]]
    assert d["failed"] == 2 and d["first_failure"]["market_id"] == "mD"
    assert world["logs"][0][1] == "attention"


def test_bands_already_proven_are_not_asked_again(world):
    bands = _ladder(world, "mE", "nyc")
    world["proven"] |= {bands[0]["condition_id"], bands[1]["condition_id"]}
    world["gamma"][bands[2]["condition_id"]] = _gamma(bands[2])
    world["clob"][bands[2]["condition_id"]] = _clob(bands[2])
    cq.run(budget_seconds=30, now=NOW, get=world["get"])
    assert world["gamma_calls"] == [[bands[2]["condition_id"]]]
    rec = [a for fn, a in world["rpcs"] if fn == "record_market_confirmation_attempts"][0]["p_rows"]
    assert rec[0]["outcome"] == "resolved" and rec[0]["bands_proven"] == 3


def test_a_fully_proven_ladder_costs_no_venue_call(world):
    bands = _ladder(world, "mF", "nyc")
    world["proven"] |= {b["condition_id"] for b in bands}
    d = cq.run(budget_seconds=30, now=NOW, get=world["get"])
    assert world["gamma_calls"] == [] and d["asked"] == 0 and d["completed"] == 1


def test_out_of_budget_with_ladders_due_is_partial_and_says_how_many(world, monkeypatch):
    for i, city in enumerate(["nyc", "la", "seattle"]):
        _ladder(world, f"m{i}", city)
    ticks = iter([0.0, 0.1] + [0.2 + 10 * k for k in range(100)])
    monkeypatch.setattr(cq.time, "monotonic", lambda: next(ticks))
    d = cq.run(budget_seconds=12, now=NOW, get=world["get"])
    assert d["unreached"] >= 1 and d["asked"] + d["unreached"] == 3
    assert world["logs"][0][1] == "partial"


def test_the_tick_writes_no_row_of_its_own_here(world):
    _ladder(world, "mG", "la")
    cq.run(budget_seconds=30, now=NOW, get=world["get"], trigger="tick", log=False)
    assert world["logs"] == []
    rec = [a for fn, a in world["rpcs"] if fn == "record_market_confirmation_attempts"][0]["p_rows"]
    assert rec[0]["trigger"] == "tick"


def test_a_dry_run_writes_nothing(world):
    bands = _ladder(world, "mH", "nyc")
    for b in bands:
        world["gamma"][b["condition_id"]] = _gamma(b)
        world["clob"][b["condition_id"]] = _clob(b)
    d = cq.run(budget_seconds=30, now=NOW, get=world["get"], dry_run=True)
    assert world["writes"] == [] and world["rpcs"] == [] and world["logs"] == []
    assert d["evidence_captured"] == 3


def test_the_intraday_pipeline_asks_then_banks_then_refreshes():
    """The queue, then databank's bands-only pass, then the page cache: a
    ladder confirmed in this run reaches the page in this run. No new
    schedule (Rule 7)."""
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/pipeline_intraday.yml").read_text()
    q = wf.index("python scripts/confirm_queue.py")
    b = wf.index("python scripts/databank.py --bands-only")
    r = wf.index('rpc("refresh_page_cache")')
    assert q < b < r
    assert "schedule:" not in wf.split("on:")[1].split("concurrency:")[0]
