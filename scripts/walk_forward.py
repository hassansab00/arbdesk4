"""
Forward-only evaluation, one definition for every learner (plan v2 P3.4).

WHY THIS FILE EXISTS
--------------------
forecast_postprocess.py and trajectory.py cross-validated on "blocked folds":
train on every block but the test block - including blocks AFTER it. A fit
scored that way has seen the future of the days it is graded on, and on
23 Sep the two layers were applied on a bare `gain > 0` from those folds:
72 of 399 post-process cells (320 had no gain) and 459 of 1,176 trajectory
city-hours.

Three rules, here once:

1. EXPANDING WINDOW. Test block k is predicted from blocks < k only. The
   first block is never scored - there is nothing before it to learn from.

2. SCORE THE PUBLISHED BUCKETS. A learner is graded on the distribution the
   engine publishes - probability_engine.compute_band_probabilities over the
   venue's ladder, floor atom and measurement layer included (P3.1) - by the
   ranked probability score, not on a continuous density the venue never
   pays on.

3. A GAIN MUST SURVIVE A BOOTSTRAP. Per-day paired score differences
   (baseline minus candidate) are resampled in day blocks, as
   model_promotion does; a layer applies only if the lower end of the 90%
   interval is above zero AND at least MIN_GATE_DAYS days were scored. That
   is stricter than a one-sided 90% bound, on purpose: with 20-odd days of
   evidence the conservative reading is the right one.
"""
import math

from model_promotion import bootstrap_interval
from probability_engine import compute_band_probabilities, venue_round

MIN_GATE_DAYS = 20          # the plan's floor for a layer to price
LADDER_HALF_WIDTH = 12      # buckets either side of the action; beyond it, open tails
LOG_FLOOR = 1e-6            # a zero on the winning bucket scores as this, not -inf


def walk_forward_folds(n, k=4, min_train=1):
    """[(start, stop), ...] test blocks over n date-ordered items.

    Train on items[:start] only. The first of k contiguous blocks is training
    history, never a test, and a block whose history is shorter than
    min_train is skipped rather than scored on a fit of almost nothing.
    """
    if n <= 1 or k <= 1:
        return []
    k = min(k, n)
    size, rem, bounds, start = n // k, n % k, [], 0
    for i in range(k):
        stop = start + size + (1 if i < rem else 0)
        bounds.append((start, stop))
        start = stop
    return [(s, e) for s, e in bounds[1:] if s >= max(1, min_train) and e > s]


def bucket_width(unit):
    return 2 if unit == "F" else 1


def ladder_around(unit, *values_c):
    """The venue's ladder, in its unit, wide enough to hold every value given.

    Celsius markets pay on single degrees; Fahrenheit ones on two-degree
    buckets with an even lower edge ("70-71F"). Closed buckets reach
    LADDER_HALF_WIDTH either side of the values, with open tails beyond, so
    the tails carry nothing that matters to the score.
    """
    w = bucket_width(unit)
    read = [venue_round(v, unit) for v in values_c if v is not None]
    lo = (min(read) // w) * w - LADDER_HALF_WIDTH * w
    hi = (max(read) // w) * w + (LADDER_HALF_WIDTH + 1) * w
    bands = [{"band_id": "low", "band_lo": None, "band_hi": lo,
              "open_low": True, "open_high": False}]
    edge = lo
    while edge < hi:
        bands.append({"band_id": str(int(edge)), "band_lo": edge, "band_hi": edge + w,
                      "open_low": False, "open_high": False})
        edge += w
    bands.append({"band_id": "high", "band_lo": hi, "band_hi": None,
                  "open_low": False, "open_high": True})
    return bands


def winning_index(bands, outcome_c, unit):
    r = venue_round(outcome_c, unit)
    for i, b in enumerate(bands):
        if b["open_low"] and r < b["band_hi"]:
            return i
        if b["open_high"] and r >= b["band_lo"]:
            return i
        if not b["open_low"] and not b["open_high"] and b["band_lo"] <= r < b["band_hi"]:
            return i
    raise ValueError(f"{outcome_c} C is on no bucket")


def bucket_scores(centre_c, sigma_c, unit, outcome_c, floor_c=None, q_down=0.0, q_up=0.0):
    """(rps, log_loss, p_winner, hit) of the published ladder against the outcome.

    rps is the ranked probability score over the buckets: the squared distance
    between the forecast's and the outcome's cumulative distributions, summed.
    It is proper and it knows that one bucket off is better than five.
    """
    bands = ladder_around(unit, centre_c, outcome_c, floor_c)
    probs = [p for _bid, p in compute_band_probabilities(
        centre_c, sigma_c, unit, bands, floor_c=floor_c, q_down=q_down, q_up=q_up)]
    win = winning_index(bands, outcome_c, unit)
    cum, rps = 0.0, 0.0
    for i, p in enumerate(probs):
        cum += p
        obs = 1.0 if i >= win else 0.0
        rps += (cum - obs) ** 2
    p_win = probs[win]
    top = max(range(len(probs)), key=lambda i: probs[i])
    return rps, -math.log(max(p_win, LOG_FLOOR)), p_win, top == win


def gate(diffs, min_days=MIN_GATE_DAYS, seed=0):
    """Verdict on date-ordered paired differences (baseline minus candidate).

    Returns dict(n, mean, lo, hi, applied, reason). One difference per DAY:
    several scored rows on one date are one piece of evidence, not several.
    """
    n = len(diffs)
    if n == 0:
        return {"n": 0, "mean": None, "lo": None, "hi": None, "applied": False,
                "reason": "no forward-scored day"}
    mean = sum(diffs) / n
    lo, hi = bootstrap_interval(diffs, seed=seed)
    applied = n >= min_days and lo is not None and lo > 0
    if n < min_days:
        why = f"{n} forward-scored day(s); the gate needs {min_days}"
    elif applied:
        why = f"gain {mean:+.4f} RPS a day, 90% interval {lo:+.4f} to {hi:+.4f} over {n} day(s)"
    else:
        why = (f"gain {mean:+.4f} RPS a day, 90% interval {lo:+.4f} to {hi:+.4f} over {n} "
               "day(s) - the interval reaches zero, stays in shadow")
    return {"n": n, "mean": mean, "lo": lo, "hi": hi, "applied": applied, "reason": why}


def per_day(pairs):
    """[(date, diff), ...] -> date-ordered list of one mean diff per date."""
    by = {}
    for d, x in pairs:
        by.setdefault(str(d), []).append(x)
    return [sum(v) / len(v) for _d, v in sorted(by.items())]
