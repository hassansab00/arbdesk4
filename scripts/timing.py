"""Timing: act now, or wait for the next tick (plan v2 P5.6).

At every tick, for a decision the solver says is worth taking now (expected
log-growth G_now), the question is whether waiting an hour is worth more:

    act  if  G_now >= E[max(G_next, 0)] - c_wait

G_next is what the same decision would be worth at the next tick. It is
simulated from a TRANSITION MODEL: the joint one-hour change of (p_post, best
ask, depth) for the same bucket, measured from consecutive hourly observations
(prediction_checkpoints and the tick's book snapshots) and grouped into cells
by what the change depends on - hours to the local peak, the model-vs-market
gap, the regime and the liquidity. max(., 0) because at the next tick the
option not to trade is still there. c_wait is the learned cost of waiting
(P5.8), prior 0.

THE PRIOR RULE. A cell with fewer than MIN_PAIRS observed hourly pairs has no
model worth simulating from. There the plan's prior decides: act when
G_now > 0 and the model-vs-market gap is not shrinking (a shrinking gap is the
market coming to the model, so the price is moving away). With no previous
observation the trend is unknown; that is not evidence of shrinking, so the
prior acts, and says the trend was unknown. Every decision records which rule
made it, so the log shows how much of the book ran on the prior.

RULE 11. The transition model is learned (P5.8 fits it nightly); here it is
only consumed, and a cell moves off the prior only at MIN_PAIRS (the plan's
500). c_wait: prior 0, bounds [0, C_WAIT_MAX]. The cell edges below are this
module's priors, not measurements, and are part of TIMING_VERSION, which every
decision carries.

This module decides; it does not fetch. The tick and the replay harness (P5.12)
hand it the numbers, so the same code runs live and in replay.
"""
import math
import random
from collections import defaultdict

TIMING_VERSION = "timing-v1"
MIN_PAIRS = 500
C_WAIT_PRIOR = 0.0
C_WAIT_MAX = 0.01            # a bound on the learned cost, per tick, in log-growth
N_SIM = 400
PAIR_GAP_MIN = (50, 70)      # consecutive observations 50-70 min apart make an hourly pair

# Cell edges (priors). Hours to the local peak: after it, the last two hours,
# the morning, the day before, earlier. Gap |p - ask| in probability points.
# Liquidity: dollars fillable within 5c of the touch.
PEAK_EDGES = (0, 2, 6, 24)
GAP_EDGES = (0.03, 0.08, 0.15)
LIQ_EDGES = (50, 500)


def _bin(x, edges):
    if x is None:
        return "na"
    for i, e in enumerate(edges):
        if x < e:
            return i
    return len(edges)


def cell(hours_to_peak, gap, regime, liquidity_usd):
    """The transition cell a bucket is in right now."""
    return (_bin(hours_to_peak, PEAK_EDGES), _bin(None if gap is None else abs(gap), GAP_EDGES),
            regime or "UNKNOWN", _bin(liquidity_usd, LIQ_EDGES))


def _minutes(a, b):
    return (b - a).total_seconds() / 60.0


def pairs_from_series(series):
    """Hourly transitions of ONE bucket's observations.

    series: [{"at": datetime, "p": p_post, "ask": best ask, "depth": usd,
              "hours_to_peak": h, "regime": label}] in any order.
    Returns [(cell_at_start, (dp, dask, ddepth))] for each pair of consecutive
    observations PAIR_GAP_MIN minutes apart. Anything else - a missed tick, a
    duplicate - is not an hour and is not a pair.
    """
    obs = sorted((o for o in series if o.get("p") is not None and o.get("ask") is not None),
                 key=lambda o: o["at"])
    out = []
    for a, b in zip(obs, obs[1:]):
        gap_min = _minutes(a["at"], b["at"])
        if not PAIR_GAP_MIN[0] <= gap_min <= PAIR_GAP_MIN[1]:
            continue
        c = cell(a.get("hours_to_peak"), a["p"] - a["ask"], a.get("regime"), a.get("depth"))
        out.append((c, (b["p"] - a["p"], b["ask"] - a["ask"],
                        (b.get("depth") or 0.0) - (a.get("depth") or 0.0))))
    return out


class TransitionModel:
    """Observed hourly changes, per cell. Sampling draws observed changes."""

    def __init__(self, pairs=(), version="empty"):
        self.by_cell = defaultdict(list)
        for c, d in pairs:
            self.by_cell[c].append(d)
        self.version = version

    def n(self, c):
        return len(self.by_cell.get(c, ()))

    def sample(self, c, k, rng):
        deltas = self.by_cell[c]
        return [deltas[rng.randrange(len(deltas))] for _ in range(k)]


def _gap_trend(gap_now, gap_prev):
    if gap_prev is None or gap_now is None:
        return "unknown"
    if abs(gap_now) < abs(gap_prev):
        return "shrinking"
    return "not_shrinking"


def decide(g_now, state, model=None, g_next_fn=None, c_wait=C_WAIT_PRIOR, seed=0):
    """Act now (True) or wait (False), with the record of why.

    g_now      expected log-growth of acting now (the solver's, P5.5)
    state      {"p", "ask", "depth", "hours_to_peak", "regime", "gap_prev"}
    model      a TransitionModel, or None
    g_next_fn  (p, ask, depth) -> expected log-growth of the same decision at
               those numbers; required for the model rule
    c_wait     the learned cost of waiting, clipped to [0, C_WAIT_MAX]
    """
    gap = None if state.get("p") is None or state.get("ask") is None else state["p"] - state["ask"]
    c = cell(state.get("hours_to_peak"), gap, state.get("regime"), state.get("depth"))
    n = model.n(c) if model is not None else 0
    c_wait = min(max(float(c_wait), 0.0), C_WAIT_MAX)
    record = {"timing_version": TIMING_VERSION, "cell": list(c), "cell_pairs": n,
              "g_now": g_now, "model_version": getattr(model, "version", None)}
    if g_now is None or not math.isfinite(g_now):
        return False, {**record, "rule": "no_growth", "reason": "no expected growth to act on"}

    if n < MIN_PAIRS or g_next_fn is None:
        trend = _gap_trend(gap, state.get("gap_prev"))
        act = g_now > 0 and trend != "shrinking"
        why = ("G_now > 0 and the gap is not shrinking" if act else
               "G_now <= 0" if g_now <= 0 else "the model-market gap is shrinking")
        return act, {**record, "rule": "prior", "gap_trend": trend, "reason": why,
                     "prior_because": f"{n} hourly pairs in the cell < {MIN_PAIRS}" if n < MIN_PAIRS
                     else "no growth function for the next tick"}

    rng = random.Random(seed)
    nexts = []
    for dp, dask, ddepth in model.sample(c, N_SIM, rng):
        p1 = min(max(state["p"] + dp, 0.0), 1.0)
        ask1 = min(max(state["ask"] + dask, 0.0), 1.0)
        depth1 = max((state.get("depth") or 0.0) + ddepth, 0.0)
        nexts.append(max(g_next_fn(p1, ask1, depth1), 0.0))
    e_next = sum(nexts) / len(nexts)
    act = g_now >= e_next - c_wait
    return act, {**record, "rule": "model", "g_wait": e_next, "c_wait": c_wait,
                 "reason": "acting now is worth at least waiting" if act else "waiting is worth more"}
