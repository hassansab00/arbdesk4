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
import market_anchor
from strategies import s2_structural_arb as s2
from strategies import s10_max_temp_winner as s10
from strategies import s11_ladder_optimiser as s11
from strategies import s12_overpriced_no as s12

RESEARCH_ONLY = {
    "s13_price_drift": "research only: no decisions until the replay shows out-of-sample "
                       "predictive power (walk-forward R2 > 0, bootstrap lower bound > 0, >= 30 dates)",
}
# THE MODEL-ONLY TWINS (Hassan, 8 Oct: "run the shadow, and the w = 1 thing").
# Each decides exactly as its base strategy does - the same rules, the same
# constraints, the same engine - on the model's own ladder: the market anchor
# at w = 1 instead of the learned weight, which stays 0 until the model has
# earned it (40 settled days; the engine's checkpoints had 13-14 on 8 Oct). They
# trade only their own shadow ledgers (Rule 6), so what the model would have
# done is on the record beside what the anchored strategy did. Every decision
# records the anchor it used: {"w": 1.0, "version": MODEL_ONLY_VERSION}.
MODEL_ONLY = {f"{b}_model": b for b in (*s10.VARIANTS, *s11.VARIANTS, *s12.VARIANTS)}
MODEL_ONLY_W = 1.0
MODEL_ONLY_VERSION = "model-only:w1"
ENGINE_STRATEGIES = (tuple(s10.VARIANTS) + tuple(s11.VARIANTS) + tuple(s12.VARIANTS)
                     + tuple(s2.VARIANTS) + tuple(RESEARCH_ONLY) + tuple(MODEL_ONLY))


def base_of(strategy_id):
    """The strategy whose rules a model-only twin follows; any other id is its own."""
    return MODEL_ONLY.get(strategy_id, strategy_id)


def _with(view, constraints, ctx, strategy_id):
    return dict(view, strategy_id=strategy_id, cluster=ctx.get("cluster"),
                checkpoint=ctx.get("checkpoint"), **constraints)


def _stamp(view, rec):
    """The anchor's weight and version travel with the view onto the decision."""
    return dict(view, anchor=rec) if view is not None and rec is not None else view


def anchored(ctx, source):
    """(ctx with the market-anchored probabilities, record) or (None, why).

    Every view that rests on a model starts from the market (market_anchor,
    Hassan 27 Sep): p = p_market + w (p_model - p_market), w learned per
    view source and checkpoint class (and per city, where one has earned its
    own), 0 until the model earns it. A book
    that does not quote every bucket has no market to anchor on, so no view.
    ctx["anchor"] = False turns it off (tests of the constraints alone).
    """
    spec = ctx.get("anchor", {})
    if spec is False:
        return ctx, {"w": 1.0, "version": "off", "scope": None}
    ids = [b["band_id"] for b in ctx["bands"]]
    market = market_anchor.market_probs(ctx.get("book"), ids)
    if market is None:
        return None, "no market to anchor on: a bucket is not quoted"
    sc = market_anchor.scope(spec.get("source", source), ctx.get("checkpoint"))
    if spec.get("fixed_w") is not None:
        # A model-only twin: the weight is fixed, not learned, and says so.
        w, version, used = float(spec["fixed_w"]), spec.get("version", "fixed"), sc
    else:
        w, version, used = market_anchor.weight_for(spec.get("table"), sc, spec.get("city"))
    probs = market_anchor.anchor({b: float(ctx["probs"].get(b, 0.0)) for b in ids}, market, w)
    return dict(ctx, probs=probs), {"w": w, "version": version, "scope": used}


def engine_input(strategy_id, ctx, trace=None):
    """`trace`, when a dict, receives S10's own decision under "s10": its SELL
    and SWITCH are carried out by the caller (plan v2 P5.12 part 3b), and they
    need the held bucket, the target and the bid, not only the reason. It
    also receives the market anchor used ("anchor"), which a row that
    declined by S10's own rule records."""
    book = ctx.get("book") or {}
    rec = None
    if strategy_id in RESEARCH_ONLY:
        return None, book, RESEARCH_ONLY[strategy_id]
    base = base_of(strategy_id)
    if strategy_id in MODEL_ONLY and ctx.get("anchor") is not False:
        ctx = dict(ctx, anchor=dict(ctx.get("anchor") or {}, fixed_w=MODEL_ONLY_W, version=MODEL_ONLY_VERSION))
    if base not in s2.VARIANTS:                         # S2's view is prices only
        ctx, rec = anchored(ctx, "s10" if base in s10.VARIANTS else "engine")
        if ctx is None:
            return None, book, rec
        if trace is not None:
            trace["anchor"] = rec
    if base in s10.VARIANTS:
        d = s10.decide(base, bands=ctx["bands"], unit=ctx["unit"], probs=ctx["probs"], book=book,
                       floor_c=ctx.get("floor_c"), floor_basis=ctx.get("floor_basis"),
                       reading_age_min=ctx.get("reading_age_min"), held=ctx.get("held"),
                       cluster=ctx.get("cluster"), checkpoint=ctx.get("checkpoint"))
        if trace is not None:
            trace["s10"] = d
            if d["action"] == "SWITCH":
                # The buy half of a switch, built as a BUY of the new target is.
                trace["switch_view"] = _stamp(_with(
                    {"probs": dict(ctx["probs"])},
                    {"allow": ("YES",), "only": [f"{d['target']}:YES"], "lock": False},
                    ctx, strategy_id), rec)
        if d["action"] != "BUY":
            return None, book, f"s10 {d['action']}: {d['reason']}"
        # s10_winner / s10_growth: the one target bucket; s10_lock: the whole
        # ladder under the lock, anchored on the target S10 chose.
        lock = base == "s10_lock"
        return _stamp(_with({"probs": dict(ctx["probs"])},
                     {"allow": ("YES",), "only": None if lock else [f"{d['target']}:YES"], "lock": lock},
                     ctx, strategy_id), rec), book, None
    if base in s11.VARIANTS:
        v = s11.view(ctx)
        if v is None:
            return None, book, "no ladder"
        return _stamp(_with(v, s11.constraints(ctx, base), ctx, strategy_id), rec), book, None
    if base in s12.VARIANTS:
        v = s12.view(ctx)
        if v is None:
            return None, book, "no ladder"
        return _stamp(_with(v, s12.constraints(ctx, base), ctx, strategy_id), rec), s12.book(ctx), None
    if strategy_id in s2.VARIANTS:
        v, why = s2.view(ctx)
        if v is None:
            return None, book, why
        return _with(v, s2.constraints(ctx, strategy_id), ctx, strategy_id), book, None
    raise ValueError(f"{strategy_id!r} does not decide through the engine")
