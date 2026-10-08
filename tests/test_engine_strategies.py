"""Plan v2 P8.1-P8.2 part 1: the consolidated strategies as views plus
constraints on the one engine (scripts/strategies/engine_views.py). Pure, on
the real frozen ladders and books of 26 Sep and on constructed ladders."""
import datetime as dt
import json
import pathlib

import pytest

import decision_engine as de
from strategies import engine_views as ev
from strategies import s2_structural_arb as s2
from strategies import tradeable

ROWS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "s10_checkpoints_26sep.json").read_text())["rows"]
LEDGER = {"equity_usd": 1000.0, "cash_usd": 1000.0}


def _ctx(r):
    age = (dt.datetime.fromisoformat(r["decided_at"]) - dt.datetime.fromisoformat(r["reading_at"])).total_seconds() / 60
    return {"bands": r["bands"], "unit": r["unit"], "probs": r["probs"], "book": r["market"],
            "floor_c": r["running_max_c"], "floor_basis": "series", "reading_age_min": age}


def _ladder(probs, asks, bids=None):
    ids = list(probs)
    bands = [{"band_id": b, "band_lo": 20 + i, "band_hi": 21 + i, "open_low": False, "open_high": False}
             for i, b in enumerate(ids)]
    book = {b: {"ask": asks[b], "bid": (bids or {}).get(b, max(asks[b] - 0.02, 0.01))} for b in ids}
    # The constraints are what these ladders test: no market anchor, no gate.
    return {"bands": bands, "unit": "C", "probs": probs, "book": book, "floor_c": None,
            "floor_basis": "series", "reading_age_min": 5, "anchor": False}


# One engine decision costs 0.5-0.8 s in pure Python (measured 27 Sep on these
# rows), so each (strategy, real row) is decided once and shared, on ten of the
# twenty: every city once, noon and postpeak_1h alternating (rows are sorted by
# city, then checkpoint).
_REAL = {}
SOME = [2 * k + (k % 2) for k in range(len(ROWS) // 2)]


def _real(sid, i):
    if (sid, i) not in _REAL:
        _REAL[(sid, i)] = _run(sid, _ctx(ROWS[i]))
    return _REAL[(sid, i)]


def _run(sid, ctx, **kw):
    view, book, why = ev.engine_input(sid, ctx)
    if view is None:
        return None, why
    if kw:
        view = dict(view, **kw)
    params = {"against_market_gate_on": False} if ctx.get("anchor") is False else None
    return de.decide(view, book=book, ledger=LEDGER, params=params), None


def test_every_strategy_decides_or_says_why_on_every_real_ladder():
    for i in SOME:
        for sid in ev.ENGINE_STRATEGIES:
            d, why = _real(sid, i)
            assert d is not None or why, sid
            if d is not None:
                assert d["action"] in ("BUY", "NONE", "HOLD", "WAIT") and d["strategy_id"] == sid


def test_no_strategy_but_s2_and_the_locks_buys_a_dead_or_ruled_out_bucket():
    for i in SOME:
        ctx = _ctx(ROWS[i])
        lost = tradeable.ruled_out(ctx["floor_c"], ctx["floor_basis"], ctx["unit"], ctx["bands"])
        for sid in ("s10_winner", "s10_growth", "s11_ladder", "s12_no"):
            d, _ = _real(sid, i)
            for o in (d or {}).get("orders", []):
                assert o["band_id"] not in lost, (sid, r["city_key"])
                assert tradeable.buyable(ctx["book"], o["band_id"], o["side"])[0], (sid, r["city_key"], o)


def test_s11_the_set_shrinks_as_the_posterior_concentrates():
    # Asks summing to one before fees, as real ladders do (the cheapest real
    # basket on 26 Sep cost 1.0107): below one, Kelly backs every bucket.
    asks = {"a": 0.08, "b": 0.22, "c": 0.40, "d": 0.22, "e": 0.08}
    flat = _ladder({"a": 0.16, "b": 0.24, "c": 0.20, "d": 0.24, "e": 0.16}, asks)
    sharp = _ladder({"a": 0.02, "b": 0.12, "c": 0.72, "d": 0.12, "e": 0.02}, asks)
    wide, _ = _run("s11_ladder", flat)
    narrow, _ = _run("s11_ladder", sharp)
    assert wide["action"] == narrow["action"] == "BUY"
    assert len(narrow["orders"]) < len(wide["orders"]), (narrow["orders"], wide["orders"])


def test_s11_is_empty_when_no_bucket_beats_its_price():
    asks = {"a": 0.10, "b": 0.30, "c": 0.40, "d": 0.20}
    total = sum(asks.values())
    ctx = _ladder({k: v / total for k, v in asks.items()}, asks)
    d, _ = _run("s11_ladder", ctx)
    assert d["action"] == "NONE" and not d["orders"]


def test_s11_lock_never_ends_below_what_it_paid():
    probs = {"a": 0.10, "b": 0.50, "c": 0.30, "d": 0.10}
    asks = {"a": 0.08, "b": 0.40, "c": 0.25, "d": 0.08}          # sum 0.81: a lock exists
    d, _ = _run("s11_lock", _ladder(probs, asks))
    assert d["action"] == "BUY"
    paid = sum(o["usd"] for o in d["orders"])
    for k in probs:
        assert sum(o["shares"] for o in d["orders"] if o["band_id"] == k) >= paid - 0.05
    for i in SOME:                                   # no real book was cheap enough
        assert _real("s11_lock", i)[0]["action"] == "NONE"


def test_s12_buys_no_only_and_never_on_a_bucket_already_lost():
    probs = {"a": 0.02, "b": 0.08, "c": 0.60, "d": 0.28, "e": 0.02}
    asks = {"a": 0.15, "b": 0.30, "c": 0.45, "d": 0.20, "e": 0.12}
    bids = {"a": 0.13, "b": 0.28, "c": 0.43, "d": 0.18, "e": 0.10}
    ctx = _ladder(probs, asks, bids)
    d, _ = _run("s12_no", ctx)
    assert d["action"] == "BUY" and {o["side"] for o in d["orders"]} == {"NO"}
    assert {o["band_id"] for o in d["orders"]} <= {"a", "b", "e"}           # the overpriced ones
    ctx_floor = dict(ctx, floor_c=23.0, floor_basis="station")               # rules out a (20-21)
    d2, _ = _run("s12_no", ctx_floor)
    assert "a" not in {o["band_id"] for o in d2["orders"]}


def test_s12_sizing_falls_as_the_posterior_widens():
    probs = {"a": 0.02, "b": 0.08, "c": 0.60, "d": 0.28, "e": 0.02}
    asks = {"a": 0.15, "b": 0.30, "c": 0.45, "d": 0.20, "e": 0.12}
    ctx = _ladder(probs, asks, {k: v - 0.02 for k, v in asks.items()})
    spend = []
    for sd in (0.01, 0.05, 0.10):
        d, _ = _run("s12_no", ctx, sds=[sd] * 5)
        spend.append(sum(o["usd"] for o in d["orders"]))
    assert spend[0] >= spend[1] >= spend[2] and spend[0] > spend[2], spend


def test_s2_needs_every_leg_and_a_basket_below_one_less_the_buffer():
    for i in range(len(ROWS)):                           # 26 Sep: no arbitrage on any real ladder
        assert _real("s2_combination_arb", i)[0] is None
    probs = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
    cheap = _ladder(probs, {"a": 0.20, "b": 0.25, "c": 0.25, "d": 0.20})   # 0.90 before fees
    d, _ = _run("s2_combination_arb", cheap)
    assert d["action"] == "BUY" and {o["band_id"] for o in d["orders"]} == set(probs)
    paid = sum(o["usd"] for o in d["orders"])
    for k in probs:
        assert sum(o["shares"] for o in d["orders"] if o["band_id"] == k) >= paid - 0.05
    incomplete = _ladder(probs, {"a": 0.20, "b": 0.25, "c": 0.25, "d": 0.20})
    incomplete["book"]["d"]["ask"] = None
    assert _run("s2_combination_arb", incomplete) == (None, "a bucket has no ask: the basket is incomplete")
    near = _ladder(probs, {k: 0.235 for k in probs})
    total, _ = s2.basket_cost(near)
    assert 0.95 < total < 0.99                                      # an arbitrage at the prior buffer, 0.01
    assert s2.view(near)[0] is not None
    assert s2.view(near, buffer=0.03)[0] is None                    # not at a learned 0.03


def test_s13_is_research_only():
    for i in range(len(ROWS)):
        d, why = _real("s13_price_drift", i)
        assert d is None and why.startswith("research only")


def test_an_unknown_strategy_is_refused():
    with pytest.raises(ValueError):
        ev.engine_input("s4_tail_fade", _ctx(ROWS[0]))


def test_every_model_view_starts_from_the_market():
    """Hassan, 27 Sep: the market-anchored belief. At the prior weight (0) a
    view IS the market's own ladder, so no real 26 Sep ladder offers S11 or S12
    a trade after fees, and the decision records the weight it used."""
    for i in SOME:
        ctx = _ctx(ROWS[i])
        for sid in ("s11_ladder", "s12_no"):
            view, _b, why = ev.engine_input(sid, ctx)
            if view is None:
                assert why.startswith("no market to anchor")
                continue
            assert view["anchor"]["w"] == 0.0 and view["anchor"]["version"] == "prior"
            d = de.decide(view, book=_b, ledger=LEDGER)
            assert d["action"] != "BUY", (sid, ROWS[i]["city_key"], d["orders"])
            assert d["versions"]["market_anchor"]["w"] == 0.0


def test_a_city_that_earned_its_own_weight_is_anchored_with_it():
    """Per-city weights (28 Sep, after P3.10 Q8): the decision records which
    weight it used, the city's or its scope's."""
    ctx = _ctx(ROWS[SOME[0]])
    ids = [b["band_id"] for b in ctx["bands"]]
    market = ev.market_anchor.market_probs(ctx["book"], ids)
    if market is None:
        pytest.skip("this row's book does not quote every bucket")
    sc = ev.market_anchor.scope("engine", ROWS[SOME[0]]["checkpoint"])
    table = {"version": "v-city", "weights": {sc: 0.0}, "city_weights": {sc: {"here": 0.2}}}
    got, rec = ev.anchored(dict(ctx, anchor={"table": table, "city": "here"}, checkpoint=ROWS[SOME[0]]["checkpoint"]),
                           "engine")
    assert rec == {"w": 0.2, "version": "v-city", "scope": f"{sc}@here"}
    want = ev.market_anchor.anchor({b: float(ctx["probs"].get(b, 0.0)) for b in ids}, market, 0.2)
    assert got["probs"] == want
    _got, rec = ev.anchored(dict(ctx, anchor={"table": table, "city": "elsewhere"},
                                 checkpoint=ROWS[SOME[0]]["checkpoint"]), "engine")
    assert rec == {"w": 0.0, "version": "v-city", "scope": sc}


def test_a_book_that_does_not_quote_every_bucket_has_no_anchor():
    ctx = _ctx(ROWS[1])
    first = ctx["bands"][0]["band_id"]
    ctx["book"] = {b: q for b, q in ctx["book"].items() if b != first}
    view, _b, why = ev.engine_input("s11_ladder", ctx)
    assert view is None and why.startswith("no market to anchor")


# THE MODEL-ONLY TWINS (Hassan, 8 Oct: "run the shadow, and the w = 1 thing").
def test_every_engine_strategy_has_a_model_only_twin():
    bases = (*ev.s10.VARIANTS, *ev.s11.VARIANTS, *ev.s12.VARIANTS)
    assert ev.MODEL_ONLY == {f"{b}_model": b for b in bases}
    assert set(ev.MODEL_ONLY) <= set(ev.ENGINE_STRATEGIES)
    for twin, base in ev.MODEL_ONLY.items():
        assert ev.base_of(twin) == base and ev.base_of(base) == base
    assert ev.base_of("s2_combination_arb") == "s2_combination_arb"


def test_a_twin_decides_by_its_bases_rules_on_the_models_own_ladder():
    """The twin's view is its base's view at w = 1, field for field, under its
    own id - and the anchor it used is on the record."""
    checked = 0
    for i in SOME:
        ctx = _ctx(ROWS[i])
        for base in ("s11_ladder", "s11_lock", "s12_no"):
            twin = f"{base}_model"
            got, gbook, gwhy = ev.engine_input(twin, ctx)
            want, wbook, wwhy = ev.engine_input(base, dict(ctx, anchor={"fixed_w": 1.0, "version": "model-only:w1"}))
            if want is None:
                assert got is None and gwhy == wwhy
                continue
            assert got["strategy_id"] == twin and want["strategy_id"] == base
            assert {k: v for k, v in got.items() if k != "strategy_id"} == \
                   {k: v for k, v in want.items() if k != "strategy_id"}
            assert gbook == wbook
            assert got["anchor"]["w"] == 1.0 and got["anchor"]["version"] == "model-only:w1"
            ids = [b["band_id"] for b in ctx["bands"]]
            market = ev.market_anchor.market_probs(ctx["book"], ids)
            model = {b: float(ctx["probs"].get(b, 0.0)) for b in ids}
            assert got["probs"] == ev.market_anchor.anchor(model, market, 1.0)
            checked += 1
    assert checked > 0, "no real ladder had a market to anchor on"


def test_a_twins_anchor_is_fixed_whatever_the_learned_table_says():
    """The learned weight is the base's; a twin never reads it."""
    ctx = _ctx(ROWS[SOME[0]])
    sc = ev.market_anchor.scope("engine", ROWS[SOME[0]]["checkpoint"])
    table = {"version": "v-learned", "weights": {sc: 0.3}, "city_weights": {sc: {"here": 0.2}}}
    ctx = dict(ctx, anchor={"table": table, "city": "here"}, checkpoint=ROWS[SOME[0]]["checkpoint"])
    base, _b, why = ev.engine_input("s11_ladder", ctx)
    if base is None:
        pytest.skip(why)
    twin, _b, _why = ev.engine_input("s11_ladder_model", ctx)
    assert base["anchor"] == {"w": 0.2, "version": "v-learned", "scope": f"{sc}@here"}
    assert twin["anchor"] == {"w": 1.0, "version": "model-only:w1", "scope": sc}


def test_an_s10_twin_takes_its_bases_s10_rule():
    """S10's variant decides the target; the twin passes its base's name."""
    ctx = _ctx(ROWS[SOME[0]])
    for base in ev.s10.VARIANTS:
        t_trace, b_trace = {}, {}
        tv, _tb, twhy = ev.engine_input(f"{base}_model", ctx, t_trace)
        bv, _bb, bwhy = ev.engine_input(base, dict(ctx, anchor={"fixed_w": 1.0, "version": "model-only:w1"}), b_trace)
        assert t_trace.get("s10") == b_trace.get("s10")
        assert (tv is None) == (bv is None) and twhy == bwhy
        if t_trace.get("s10"):
            assert t_trace["s10"]["variant"] == base
