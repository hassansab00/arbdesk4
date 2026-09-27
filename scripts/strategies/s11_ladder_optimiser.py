"""S11, the ladder optimiser (plan v2 P8.1-P8.2): a view plus constraints.

Hypothesis: the market misprices the SHAPE of the distribution - too flat on
sharp days, too narrow on uncertain ones. It replaces S3 (concentration), S8
(two-bucket cover) and S9 (ladder basket), which were fixed-width versions of
this idea; the old S6's no-loss book lives on as the s11_lock variant.

  view         the full ladder posterior (the engine's probabilities through the
               belief layer, P5.3)
  s11_ladder   YES only, any set of buckets: the horse-race Kelly set the solver
               finds. On a sharp day the posterior is concentrated and the set
               is small; on an uncertain one it widens. No width rule is
               needed for that (P8.1).
  s11_lock     the same view, and the book must not end below its cost in any
               outcome (the old S6 insurance). The whole ladder stays in, dead
               buckets included: a lock is about what the venue pays.

Buckets ruled out by a station floor, and books the platform calls untradeable,
are never bought by s11_ladder (strategies.tradeable).
"""
from strategies import tradeable

VARIANTS = ("s11_ladder", "s11_lock")


def view(ctx):
    probs = ctx.get("probs")
    return {"probs": dict(probs)} if probs else None


def constraints(ctx, variant):
    if variant not in VARIANTS:
        raise ValueError(f"unknown S11 variant {variant!r}")
    if variant == "s11_lock":
        return {"allow": ("YES",), "only": None, "lock": True}
    ids = [b["band_id"] for b in ctx["bands"]]
    lost = tradeable.ruled_out(ctx.get("floor_c"), ctx.get("floor_basis"), ctx["unit"], ctx["bands"])
    only = [f"{b}:YES" for b in ids if b not in lost and tradeable.buyable(ctx["book"], b, "YES")[0]]
    return {"allow": ("YES",), "only": only, "lock": False}
