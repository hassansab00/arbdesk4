"""The market-anchored belief (plan v2 P5.3, amended by Hassan on 27 Sep).

What a strategy believes about a ladder starts from the MARKET, and the model
earns weight only on settled evidence:

    p_post = p_market + w * (p_model - p_market)          w in [0, 1]

p_market is the book's own ladder: each bucket's mid, normalised to sum to
one. w = 0 believes the market; w = 1 believes the model.

WHY. The engine replay of 12-25 Sep (docs/ENGINE_REPLAY_2026-09-27.md) traded
the engine's probabilities through the one engine and lost ~80% of two $1,000
ledgers: where it bought, the engine said 0.281 against an ask of 0.159 and the
bucket won 0.142 of the time (S11); NO at 0.828 against 0.647, won 0.623 (S12).
On 3,019 checkpoints of 13 days with a full ladder and a fully priced book, log
loss was 1.006 at w = 0 and 1.847 at w = 1, and every w from 0.1 to 1 was worse
than 0, in every group (day-ahead, same day, C, F). The plan's reliability map
(belief.py) calibrates the model bin by bin over ALL buckets, most of which
agree with the market; the error lives exactly where they disagree, which is
the only place a strategy trades.

RULE 11. w has
  - a prior:          0 (the market) - W_PRIOR;
  - hard bounds:      [0, 1];
  - a minimum sample: a scope moves off its prior only with MIN_DAYS settled
                      days of evidence;
  - evidence to move: the gain in log loss of the chosen w over w = 0 must have
                      a day-block bootstrap lower 90% bound above zero;
  - a maximum step:   a refit moves w at most MAX_STEP from the previous version;
  - a version:        fit() stamps one; every decision records it.
It is never evaluated on its own training data: fit() takes `as_of` and learns
only from dates strictly before it. MIN_DAYS is the replay contract's floor
(walk_forward.MIN_GATE_DAYS, 20); MAX_STEP and the grid are this module's
priors, recorded in every fitted table.

SCOPES. A weight belongs to one view source and one checkpoint class: the
engine's ladder may earn none while S10's late-day ladder earns some (the P7.3
replay had S10 ahead of the market one hour after the peak).
"""
import hashlib
import json
import math
import random
import sys
from collections import defaultdict

from belief import CHECKPOINT_CLASS

W_PRIOR = 0.0
W_BOUNDS = (0.0, 1.0)
W_GRID = tuple(i / 100 for i in range(101))
MIN_DAYS = 20
MAX_STEP = 0.05
BOOT_N = 2000
BOOT_LOWER = 0.05             # the lower end of a two-sided 90% interval
P_FLOOR = 1e-6
PRIOR_VERSION = "prior"


def scope(source, checkpoint):
    """'engine:pre_day', 's10:post_peak', ... ; an unknown checkpoint is 'any'."""
    return f"{source}:{CHECKPOINT_CLASS.get(checkpoint, 'any')}"


def market_probs(book, band_ids):
    """{band: p} from the book's mids, normalised; None unless every bucket is quoted.

    A bucket with an ask and no bid is quoted at half its ask (the bid is zero);
    one with a bid and no ask at halfway from its bid to one.
    """
    mids = {}
    for b in band_ids:
        q = (book or {}).get(b) or {}
        ask, bid = q.get("ask"), q.get("bid")
        if ask is not None and bid is not None:
            mids[b] = (float(ask) + float(bid)) / 2
        elif ask is not None:
            mids[b] = float(ask) / 2
        elif bid is not None:
            mids[b] = (float(bid) + 1) / 2
        else:
            return None
    total = sum(mids.values())
    if total <= 0:
        return None
    return {b: v / total for b, v in mids.items()}


def anchor(model, market, w):
    """p_market + w (p_model - p_market), floored and renormalised."""
    lo, hi = W_BOUNDS
    w = min(max(float(w), lo), hi)
    q = {b: max(market[b] + w * (model[b] - market[b]), P_FLOOR) for b in market}
    s = sum(q.values())
    return {b: v / s for b, v in q.items()}


def weight(table, scope_key):
    """(w, version) for a scope: the fitted value, or the prior."""
    if not table:
        return W_PRIOR, PRIOR_VERSION
    w = (table.get("weights") or {}).get(scope_key)
    if w is None:
        return W_PRIOR, table.get("version", PRIOR_VERSION)
    return float(w), table.get("version", PRIOR_VERSION)


# --------------------------------------------------------------------------
# Fitting: walk-forward, bounded, versioned
# --------------------------------------------------------------------------

def _log_loss(model, market, winner, w):
    return -math.log(anchor(model, market, w)[winner])


def _gain_interval(by_day, w, seed=0):
    """Mean per-row gain of w over w = 0 and its day-block bootstrap lower bound."""
    days = sorted(by_day)
    per = {d: (sum(_log_loss(m, k, win, 0.0) - _log_loss(m, k, win, w) for m, k, win in rows), len(rows))
           for d, rows in by_day.items()}
    total_n = sum(n for _g, n in per.values())
    mean = sum(g for g, _n in per.values()) / total_n
    rng = random.Random(seed)
    boots = []
    for _ in range(BOOT_N):
        pick = [rng.choice(days) for _d in days]
        n = sum(per[d][1] for d in pick)
        boots.append(sum(per[d][0] for d in pick) / n)
    boots.sort()
    return mean, boots[int(BOOT_LOWER * BOOT_N)]


def fit(rows, as_of, previous=None):
    """A fitted weight table from settled rows before `as_of`.

    rows: [(date, scope, {band: p_model}, {band: p_market}, winner_band)].
    """
    by_scope = defaultdict(lambda: defaultdict(list))
    for d, sc, model, market, winner in rows:
        if str(d) >= str(as_of) or winner not in market:
            continue
        by_scope[sc][str(d)].append((model, market, winner))
    prev = (previous or {}).get("weights") or {}
    weights, evidence = {}, {}
    for sc, by_day in sorted(by_scope.items()):
        start = float(prev.get(sc, W_PRIOR))
        if len(by_day) < MIN_DAYS:
            weights[sc] = start
            evidence[sc] = {"days": len(by_day), "held": f"fewer than {MIN_DAYS} settled days"}
            continue
        flat = [r for rs in by_day.values() for r in rs]
        best = min(W_GRID, key=lambda w: (sum(_log_loss(m, k, win, w) for m, k, win in flat), w))
        mean, lower = _gain_interval(by_day, best) if best > 0 else (0.0, 0.0)
        target = best if (best > 0 and lower > 0) else W_PRIOR
        step = max(-MAX_STEP, min(MAX_STEP, target - start))
        weights[sc] = round(min(max(start + step, W_BOUNDS[0]), W_BOUNDS[1]), 4)
        evidence[sc] = {"days": len(by_day), "rows": len(flat), "best_w": best, "gain": round(mean, 5),
                        "gain_lower90": round(lower, 5), "target": target}
    body = {"as_of": str(as_of), "w_prior": W_PRIOR, "bounds": list(W_BOUNDS), "min_days": MIN_DAYS,
            "max_step": MAX_STEP, "previous": (previous or {}).get("version"), "weights": weights,
            "evidence": evidence}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    body["version"] = f"market-anchor:{as_of}:{digest[:10]}"
    return body


def load(rest=None):
    """The latest fitted table from strategy_params, or None (every weight the prior, 0).

    Read only while settings.strategy_learning.enabled is true (P5.8); until
    then every strategy believes the market.
    """
    if rest is None:
        from common import rest as rest
    import learned
    if not learned.enabled(rest):
        return None
    try:
        rows = rest("strategy_params", [("select", "value,version"), ("param", "eq.market_weight"),
                                        ("order", "fitted_at.desc"), ("limit", "1")])
    except Exception as e:
        print(f"  note: no market weights ({e}); every strategy believes the market", file=sys.stderr)
        return None
    if not rows:
        return None
    table = rows[0].get("value") or {}
    table.setdefault("version", rows[0].get("version") or PRIOR_VERSION)
    return table
