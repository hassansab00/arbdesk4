"""How much bankroll a strategy has earned, from its own settled record.

WHAT THIS REPLACES. On 2026-09-22 five strategies were retired by hand: I
measured return per stake on their settled signals, wrote the numbers down,
and Hassan decided. That is the right decision made the wrong way. It took
three weeks of losses to notice, the numbers were only computed because
somebody went looking, and the same thing will be true again in a month with
different strategies.

A strategy's share of the bankroll should be a function of its own record,
recomputed every run, so a strategy that stops working shrinks toward zero
without anyone noticing in time.

THE STATISTIC. Each settled signal contributes one number: what a dollar
committed to it came back as, net of fees - v_signal_mark's
mark_net_per_share over price_at_fire. Those are noisy and there are not many
of them, so the sample mean is a bad answer: four signals at +11% is not
evidence, and s8 and s9 had exactly four each.

So the mean is shrunk toward zero through a conjugate normal update with a
prior centred on zero:

    posterior_mean = (tau^2 * xbar) / (tau^2 + se^2)
    posterior_var  = (tau^2 * se^2) / (tau^2 + se^2)

ZERO IS THE RIGHT PRIOR CENTRE, not the observed average of other strategies.
The venue takes roughly 13.5% of overround out of every quoted ladder and
charges a fee on top; a strategy with no edge returns slightly less than
nothing, and a new strategy has shown no edge. tau is how surprised we are
willing to be - PRIOR_SD below - and it is deliberately small enough that a
handful of lucky signals cannot buy a full allocation.

THE WEIGHT. Not the posterior mean, which is in return units and would size
a strategy by how good it looks. What a strategy earns is CONFIDENCE that it
is above water:

    weight = clamp((P(mean > 0) - floor) / (1 - floor), 0, 1) * evidence

At P = 0.5 - no evidence either way, which is where every strategy starts -
the confidence term is zero, and a strategy with no record trades nothing.
That is the behaviour s8 and s9 should have had.

AND CONFIDENCE ALONE IS NOT ENOUGH, which the first version of this got
wrong. Four signals returning +30%, +25%, +40% and +20% agree closely, so
their standard error is small, so the normal update reports near-certainty
and hands over a full allocation on four observations. The arithmetic is
right and the conclusion is nonsense, because the model behind it assumes
those four are independent draws and they are not: s8 and s9 emit one signal
PER LEG of a basket, so four signals can be two legs of two market-days in
one city. Tight agreement between correlated observations measures the
correlation, not the edge.

So the confidence is multiplied by an evidence factor, min(1, n / MIN_SIGNALS),
which is a flat refusal to extrapolate from a handful however well those few
agree. It is deliberately crude: the honest alternative is to model the
within-day correlation, and there is not enough settled history yet to
estimate it. When there is, this is the line that should be replaced.

WHAT THIS IS NOT. It is not a retirement. A strategy whose weight goes to
zero keeps firing signals, keeps being marked, and keeps its place on the
board - it simply stops being given money, and it can earn its way back when
the evidence turns. Retiring a strategy stays a decision a person makes, with
a reason written down; this is the dial in between, which did not exist.
"""

import math
from dataclasses import dataclass


PRIOR_SD = 0.05           # tau: a 5c-on-the-dollar edge is already a lot here
CONFIDENCE_FLOOR = 0.60   # below this a strategy is not distinguishable from noise
MIN_SIGNALS = 10          # below this the weight is scaled down pro rata, not just flagged


def _normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass
class Verdict:
    strategy_id: str
    n: int
    observed_mean: float          # return per dollar, unshrunk
    posterior_mean: float         # shrunk toward zero
    posterior_sd: float
    prob_positive: float
    weight: float                 # share of the allocation this has earned
    thin: bool
    reason: str


def assess(strategy_id, returns, prior_sd=PRIOR_SD,
           floor=CONFIDENCE_FLOOR, min_signals=MIN_SIGNALS):
    """`returns` is one number per settled signal: net return per dollar staked."""
    xs = [float(r) for r in returns if r is not None]
    n = len(xs)
    if n == 0:
        return Verdict(strategy_id, 0, None, 0.0, prior_sd, 0.5, 0.0, True,
                       "nothing has settled - a strategy with no record gets no money")

    xbar = sum(xs) / n
    if n == 1:
        # One signal carries no width of its own, so the prior supplies all of
        # it. The alternative - treating a single observation as exact - is
        # how four signals became a verdict.
        se = prior_sd
    else:
        var = sum((x - xbar) ** 2 for x in xs) / (n - 1)
        se = math.sqrt(var / n) if var > 0 else 1e-9

    tau2, se2 = prior_sd ** 2, se ** 2
    post_mean = (tau2 * xbar) / (tau2 + se2)
    post_var = (tau2 * se2) / (tau2 + se2)
    post_sd = math.sqrt(post_var)

    prob_pos = _normal_cdf(post_mean / post_sd) if post_sd > 0 else (
        1.0 if post_mean > 0 else 0.0)

    confidence = min(1.0, max(0.0, (prob_pos - floor) / (1.0 - floor)))
    # Signals inside one market-day are not independent draws - a basket
    # strategy emits one per leg - so a handful that agree closely still
    # cannot buy a full allocation.
    evidence = min(1.0, n / float(min_signals)) if min_signals > 0 else 1.0
    weight = confidence * evidence

    thin = n < min_signals
    if weight <= 0:
        reason = (f"{n} settled signal(s), {100*xbar:+.1f}c on the dollar - "
                  f"{100*prob_pos:.0f}% confident it is above water, under the "
                  f"{100*floor:.0f}% floor, so no allocation")
    elif thin:
        reason = (f"{n} settled signal(s) is under {min_signals}, so the weight is "
                  f"scaled to {weight:.2f} - {100*prob_pos:.0f}% confident, held back "
                  f"because a handful of correlated signals is not a record")
    else:
        reason = (f"{n} settled signals, {100*xbar:+.1f}c on the dollar, shrunk to "
                  f"{100*post_mean:+.1f}c - {100*prob_pos:.0f}% confident it is "
                  f"above water, weight {weight:.2f}")

    return Verdict(strategy_id, n, xbar, post_mean, post_sd, prob_pos,
                   weight, thin, reason)


def weights(records, **kw):
    """{strategy_id: [returns]} -> {strategy_id: Verdict}."""
    return {sid: assess(sid, rs, **kw) for sid, rs in records.items()}


def normalise(verdicts, total=1.0):
    """Turn earned weights into shares of one bankroll.

    A strategy's weight says what it deserves on its own; this says what it
    gets when it has to share. If nothing has earned anything the answer is
    nothing - the bankroll stays in cash rather than being spread evenly over
    strategies that have all failed to show an edge.
    """
    earned = {sid: v.weight for sid, v in verdicts.items() if v.weight > 0}
    s = sum(earned.values())
    if s <= 0:
        return {sid: 0.0 for sid in verdicts}
    return {sid: (earned.get(sid, 0.0) / s) * total for sid in verdicts}
