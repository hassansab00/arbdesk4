"""S2, the structural arbitrage, on the engine (plan v2 P8.1-P8.2).

Hypothesis: exactly one bucket pays $1, so a ladder whose fee-inclusive YES
asks sum below one pays more than it costs whatever wins, and buying every
bucket is riskless. It keeps S2's id and ledger; the old signal code
(s2_combination_arb.py) stays until the engine runs live.

  view         none - prices only
  opportunity  every bucket has an ask (dead ones too: an arbitrage needs every
               leg, which is why this is the one strategy that does not use
               strategies.tradeable's rule), and the sum of their fee-inclusive
               costs is below 1 - buffer. buffer is learned (strategy_params
               "s2_buffer": prior 0.01, bounds [0.002, 0.05]).
  constraints  YES on every bucket, and the lock: the book cannot end below its
               cost in any outcome. The solver buys the equal-shares book when
               the lock binds. All legs or none.

The probabilities handed to the engine are the costs normalised: under them no
bucket is better than another, so the book is the arbitrage and nothing else.
Measured on the old code's first working run (21 Sep): 71 of 99 city-days formed
a complete basket, the cheapest cost 1.0628 against a $1.00 payout.
"""
import strategy_params
from allocator import effective_cost

VARIANTS = ("s2_combination_arb",)


def basket_cost(ctx):
    """(total fee-inclusive cost of one YES share of every bucket, or None, why)."""
    costs = []
    for b in ctx["bands"]:
        q = (ctx.get("book") or {}).get(b["band_id"]) or {}
        c = effective_cost(q.get("ask")) if q.get("ask") is not None else None
        if c is None:
            return None, "a bucket has no ask: the basket is incomplete"
        costs.append(c)
    return sum(costs), None


def view(ctx, buffer=None):
    total, why = basket_cost(ctx)
    if total is None:
        return None, why
    edge = strategy_params.value("s2_buffer", buffer)
    if total >= 1 - edge:
        return None, f"the basket costs {total:.4f}, not below 1 - {edge}"
    probs = {}
    for b in ctx["bands"]:
        probs[b["band_id"]] = effective_cost(ctx["book"][b["band_id"]]["ask"]) / total
    return {"probs": probs}, None


def constraints(ctx, variant="s2_combination_arb"):
    if variant not in VARIANTS:
        raise ValueError(f"unknown S2 variant {variant!r}")
    return {"allow": ("YES",), "only": None, "lock": True}
