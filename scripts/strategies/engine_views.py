"""The strategies that decide through the engine (plan v2 P8.1-P8.2), and the
one place that turns a strategy's view and constraints into the engine's input.

Hassan approved the consolidation on 27 Sep: S10 (three variants), S11 ladder
optimiser (and s11_lock), S12 overpriced-bucket NO, S2 structural arbitrage,
S13 price drift (research only). The old s1, s3-s9 are retired as strategy ids
once these decide live (P5.12 part 3); until then they keep running on the old
signal path so no shadow evidence stops.

engine_input(strategy_id, ctx) -> (view for decision_engine.decide, book, None)
                                  or (None, book, why not)

ctx: {"bands" (venue order), "unit", "probs" {band: p}, "book" {band: {ask, bid,
no_ask, no_bid, depth_usd}}, "floor_c", "floor_basis", "reading_age_min",
"held" (S10: {"band_id", "shares"} or None), "cluster", "checkpoint"}.

S10's SELL (certainly lost) and SWITCH are its own decisions and come back as
the reason; the engine sizes buys only (it does not decide sells, P5.5).
"""
from strategies import s2_structural_arb as s2
from strategies import s10_max_temp_winner as s10
from strategies import s11_ladder_optimiser as s11
from strategies import s12_overpriced_no as s12

RESEARCH_ONLY = {
    "s13_price_drift": "research only: no decisions until the replay shows out-of-sample "
                       "predictive power (walk-forward R2 > 0, bootstrap lower bound > 0, >= 30 dates)",
}
ENGINE_STRATEGIES = (tuple(s10.VARIANTS) + tuple(s11.VARIANTS) + tuple(s12.VARIANTS)
                     + tuple(s2.VARIANTS) + tuple(RESEARCH_ONLY))


def _with(view, constraints, ctx, strategy_id):
    return dict(view, strategy_id=strategy_id, cluster=ctx.get("cluster"),
                checkpoint=ctx.get("checkpoint"), **constraints)


def engine_input(strategy_id, ctx):
    book = ctx.get("book") or {}
    if strategy_id in RESEARCH_ONLY:
        return None, book, RESEARCH_ONLY[strategy_id]
    if strategy_id in s10.VARIANTS:
        d = s10.decide(strategy_id, bands=ctx["bands"], unit=ctx["unit"], probs=ctx["probs"], book=book,
                       floor_c=ctx.get("floor_c"), floor_basis=ctx.get("floor_basis"),
                       reading_age_min=ctx.get("reading_age_min"), held=ctx.get("held"),
                       cluster=ctx.get("cluster"), checkpoint=ctx.get("checkpoint"))
        if d["action"] != "BUY":
            return None, book, f"s10 {d['action']}: {d['reason']}"
        # s10_winner / s10_growth: the one target bucket; s10_lock: the whole
        # ladder under the lock, anchored on the target S10 chose.
        lock = strategy_id == "s10_lock"
        return _with({"probs": dict(ctx["probs"])},
                     {"allow": ("YES",), "only": None if lock else [f"{d['target']}:YES"], "lock": lock},
                     ctx, strategy_id), book, None
    if strategy_id in s11.VARIANTS:
        v = s11.view(ctx)
        if v is None:
            return None, book, "no ladder"
        return _with(v, s11.constraints(ctx, strategy_id), ctx, strategy_id), book, None
    if strategy_id in s12.VARIANTS:
        v = s12.view(ctx)
        if v is None:
            return None, book, "no ladder"
        return _with(v, s12.constraints(ctx, strategy_id), ctx, strategy_id), s12.book(ctx), None
    if strategy_id in s2.VARIANTS:
        v, why = s2.view(ctx)
        if v is None:
            return None, book, why
        return _with(v, s2.constraints(ctx, strategy_id), ctx, strategy_id), book, None
    raise ValueError(f"{strategy_id!r} does not decide through the engine")
