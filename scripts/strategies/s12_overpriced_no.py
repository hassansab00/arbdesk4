"""S12, the overpriced-bucket NO (plan v2 P8.1-P8.2): a view plus constraints.

Hypothesis: buckets the market overprices, tails especially, can be sold by
buying their NO. It replaces S4 (tail fade), which did this with a fixed
threshold.

  view         the full ladder posterior; NO's probability is 1 - p_post
  constraints  NO only. A NO price is the book's NO ask, else 1 - the YES bid
               (the plan's). No NO on a bucket the station floor has already
               ruled out for YES (it has nothing left to pay for) or on a book
               the platform calls untradeable (strategies.tradeable). Sizing
               falls as p_sd rises because the solver sizes on the worst draws
               of the posterior (P5.5), not by a rule here.

The belief layer's tail cells (p < 0.1) are where this edge lives or dies
(P8.2); they are P5.3's, fitted nightly.
"""
from strategies import tradeable

VARIANTS = ("s12_no",)


def view(ctx):
    probs = ctx.get("probs")
    return {"probs": dict(probs)} if probs else None


def book(ctx):
    """The book as the engine should see it: every bucket's NO ask filled in."""
    return {b: dict(q or {}, no_ask=tradeable.no_price(q)) for b, q in (ctx.get("book") or {}).items()}


def constraints(ctx, variant="s12_no"):
    if variant not in VARIANTS:
        raise ValueError(f"unknown S12 variant {variant!r}")
    ids = [b["band_id"] for b in ctx["bands"]]
    lost = tradeable.ruled_out(ctx.get("floor_c"), ctx.get("floor_basis"), ctx["unit"], ctx["bands"])
    only = [f"{b}:NO" for b in ids if b not in lost and tradeable.buyable(ctx["book"], b, "NO")[0]]
    return {"allow": ("NO",), "only": only, "lock": False}
