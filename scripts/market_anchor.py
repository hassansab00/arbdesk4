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
  - evidence to move, walking forward: on every settled day with MIN_DAYS
                      earlier days, the w those earlier days alone choose is
                      scored on the day against w = 0. Over at least MIN_DAYS
                      such days the gain must have a day-block bootstrap lower
                      90% bound above zero, and the latest RECENT_DAYS of them
                      must gain too. So a scope moves no sooner than 2 x
                      MIN_DAYS settled days;
  - a maximum step:   a refit moves w at most MAX_STEP from the previous version;
  - a version:        fit() stamps one; every decision records it.
It is never evaluated on its own training data: fit() takes `as_of` and learns
only from dates strictly before it, and no day scores a w chosen with it.
MIN_DAYS is the replay contract's floor (walk_forward.MIN_GATE_DAYS, 20);
MAX_STEP, RECENT_DAYS and the grid are this module's priors, recorded in every
fitted table.

WHY FORWARD (29 Sep audit, repair 4). Until 30 Sep the gate took the w with
the lowest log loss over every settled day and bootstrapped its gain over
those same days. A bootstrap that never re-picks w keeps the selection's
optimism, and w = 0 is on the grid, so the in-sample gain could never be
negative. Simulated, a model informative for 20 days and then not for 10
passed that gate in 60 of 60 trials and lost on the last 10 days in all 60.
The gate never ran live: every scope had 2-5 of its 20 days.

SCOPES. A weight belongs to one view source and one checkpoint class: the
engine's ladder may earn none while S10's late-day ladder earns some (the P7.3
replay had S10 ahead of the market one hour after the peak).

CITIES (28 Sep, after P3.10 Q8; Hassan: "we need our predictive model to win
the single max temp winner"). Under each scope, a city may carry its own
weight, so a city where the model knows something the market does not can earn
it there alone. Q8 is why this is guarded as it is: on the venue's record, 3 of
48 cities beat the market with a 90% interval above zero, about what chance
gives, and the cities that gained through July did not in August-September.
So, on top of the scope's rules:
  - the prior is the scope's own weight: a city with no entry uses it;
  - a city's estimate counts only with CITY_MIN_DAYS settled days of its own;
  - it carries only its difference from the scope's pooled best fit, shrunk by
    n / (n + CITY_K) days (Q8's constant), added to the scope's weight;
  - the family gate: those shrunk differences, each fitted on the days before
    it, must beat the pooled fit on the day itself, walking forward over every
    settled day, with a day-block bootstrap lower 90% bound above zero and at
    least MIN_DAYS such days. One test for all cities of a scope, so 48 cities
    are not 48 chances for luck to pass;
  - a city moves at most MAX_STEP a night from its last weight (or from the
    scope's), and walks back to the scope's weight when the gate stops passing;
  - bounds [0, 1] and the table's version, as the scope's.
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
RECENT_DAYS = 10             # the latest forward days must gain too: an edge that fades shows there first
BOOT_N = 2000
BOOT_LOWER = 0.05             # the lower end of a two-sided 90% interval
P_FLOOR = 1e-6
PRIOR_VERSION = "prior"
CITY_MIN_DAYS = MIN_DAYS      # a city's own settled days before its estimate counts
CITY_K = 60                   # shrink a city's difference by n / (n + CITY_K) days (P3.10 Q8's K8)


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


def weight_for(table, scope_key, city=None):
    """(w, version, the scope used): the city's own weight where the table has
    one, else the scope's (weight)."""
    w, version = weight(table, scope_key)
    own = ((table or {}).get("city_weights") or {}).get(scope_key, {}).get(city) if city else None
    if own is None:
        return w, version, scope_key
    return float(own), version, f"{scope_key}@{city}"


# --------------------------------------------------------------------------
# Fitting: walk-forward, bounded, versioned
# --------------------------------------------------------------------------

def _log_loss(model, market, winner, w):
    return -math.log(anchor(model, market, w)[winner])


def _ll_grid(model, market, winner):
    """A row's log loss at every w of W_GRID, computed once and reused."""
    return [_log_loss(model, market, winner, w) for w in W_GRID]


def _add(totals, grid):
    for i, v in enumerate(grid):
        totals[i] += v


def _best(totals):
    """The index on W_GRID with the lowest total log loss; the smaller w on a tie."""
    return min(range(len(W_GRID)), key=lambda i: (totals[i], W_GRID[i]))


def _clamp(w):
    return min(max(w, W_BOUNDS[0]), W_BOUNDS[1])


def _ix(w):
    """w's index on W_GRID; weights are kept on its 0.01 steps."""
    return min(max(int(round(float(w) * 100)), 0), len(W_GRID) - 1)


def _interval(per, seed=0):
    """Mean per-row gain and its day-block bootstrap lower bound, from
    {day: (the day's summed gain, its rows)}."""
    days = sorted(per)
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


def _family(by_day):
    """The walk-forward test for one scope's cities: (family result, {city: its own result}).

    On each settled day, every city with CITY_MIN_DAYS earlier days of its own
    is priced at the pooled best fit of the days before plus its shrunk
    difference from it, fitted on the same days, and scored against the pooled
    fit alone; the gain is the pooled fit's log loss minus the city's. The
    family result pools every such city on every such day; each city's own
    result is its days alone.
    """
    pooled = [0.0] * len(W_GRID)
    city_tot = defaultdict(lambda: [0.0] * len(W_GRID))
    city_days = defaultdict(int)
    per, per_city = {}, defaultdict(dict)
    for d in sorted(by_day):
        rows = by_day[d]
        if any(c is not None and city_days.get(c, 0) >= CITY_MIN_DAYS for _g, c in rows):
            ip = _best(pooled)
            at = {}
            for g, c in rows:
                if c is None or city_days.get(c, 0) < CITY_MIN_DAYS:
                    continue
                if c not in at:
                    k = city_days[c]
                    dev = k / (k + CITY_K) * (W_GRID[_best(city_tot[c])] - W_GRID[ip])
                    at[c] = _ix(_clamp(round(W_GRID[ip] + dev, 2)))
                gain, n = per_city[c].get(d, (0.0, 0))
                per_city[c][d] = (gain + g[ip] - g[at[c]], n + 1)
            per[d] = (sum(per_city[c][d][0] for c in at), sum(per_city[c][d][1] for c in at))
        seen = set()                                   # then the day is learned from
        for g, c in rows:
            _add(pooled, g)
            if c is not None:
                _add(city_tot[c], g)
                if c not in seen:
                    seen.add(c)
                    city_days[c] += 1
    return _verdict(per), per_city


def _forward(by_day):
    """The walk-forward test of a scope's pooled fit: {day: (the day's summed gain, its rows)}.

    On each settled day with MIN_DAYS earlier days, the w with the lowest
    total log loss over those earlier days alone is scored on the day against
    the prior; the gain is the prior's log loss minus that w's. Only then is
    the day learned from.
    """
    i0 = _ix(W_PRIOR)
    totals = [0.0] * len(W_GRID)
    per = {}
    for n, d in enumerate(sorted(by_day)):
        rows = by_day[d]
        if n >= MIN_DAYS:
            i = _best(totals)
            per[d] = (sum(g[i0] - g[i] for g, _c in rows), len(rows))
        for g, _c in rows:
            _add(totals, g)
    return per


def _forward_verdict(per):
    """_verdict of the forward gains, which also holds when the latest
    RECENT_DAYS of them do not gain."""
    out = _verdict(per)
    if len(per) < MIN_DAYS:
        return out
    recent = sorted(per)[-RECENT_DAYS:]
    gain = sum(per[d][0] for d in recent) / sum(per[d][1] for d in recent)
    out["recent_days"], out["recent_gain"] = len(recent), round(gain, 5)
    if out["passed"] and not gain > 0:
        out["passed"], out["held"] = False, f"no gain over the latest {RECENT_DAYS} days scored forward"
    return out


def _verdict(per):
    """{days, rows, gain, gain_lower90, passed} for walk-forward gains {day: (sum, rows)}."""
    if len(per) < MIN_DAYS:
        return {"days": len(per), "passed": False, "held": f"fewer than {MIN_DAYS} days scored forward"}
    mean, lower = _interval(per)
    return {"days": len(per), "rows": sum(n for _g, n in per.values()), "gain": round(mean, 5),
            "gain_lower90": round(lower, 5), "passed": mean > 0 and lower > 0}


def _fit_cities(by_day, i_pool, w_scope, prev):
    """({city: w} wherever it differs from the scope's weight, evidence) for one scope.

    by_day: {day: [(log-loss grid, city)]}; i_pool: the scope's pooled best
    fit on W_GRID; w_scope: the scope's weight tonight; prev: the cities' last
    weights. A city's target is the scope's weight plus its shrunk difference
    from the pooled fit when both the family and the city's own walk-forward
    test pass (protected testing: the family first, so 48 cities are not 48
    chances for luck), else the scope's weight; it moves at most MAX_STEP from
    where it was.
    """
    totals = defaultdict(lambda: [0.0] * len(W_GRID))
    days = defaultdict(set)
    for d, rows in by_day.items():
        for g, c in rows:
            if c is not None:
                _add(totals[c], g)
                days[c].add(d)
    family, forward = _family(by_day)
    out, per_city = {}, {}
    for c in sorted(set(days) | set(prev)):
        n = len(days.get(c, ()))
        target = float(w_scope)
        if n >= CITY_MIN_DAYS:
            best = W_GRID[_best(totals[c])]
            dev = n / (n + CITY_K) * (best - W_GRID[i_pool])
            own = _verdict(forward.get(c, {})) if family["passed"] else {"passed": False, "held": "family gate"}
            if own["passed"]:
                target = _clamp(round(float(w_scope) + dev, 2))
            per_city[c] = {"days": n, "best_w": best, "deviation": round(dev, 4), "forward": own, "target": target}
        start = float(prev.get(c, w_scope))
        w = round(_clamp(start + max(-MAX_STEP, min(MAX_STEP, target - start))), 4)
        if w != round(float(w_scope), 4):
            out[c] = w
    return out, {"cities": len(days), "eligible": len(per_city), "pooled_best_w": W_GRID[i_pool],
                 "family": family, "by_city": per_city}


def fit(rows, as_of, previous=None):
    """A fitted weight table from settled rows before `as_of`.

    rows: [(date, scope, {band: p_model}, {band: p_market}, winner_band[, city_key])];
    a row without a city counts for its scope only.
    """
    by_scope = defaultdict(lambda: defaultdict(list))
    for r in rows:
        d, sc, model, market, winner = r[:5]
        if str(d) >= str(as_of) or winner not in market:
            continue
        by_scope[sc][str(d)].append((_ll_grid(model, market, winner), r[5] if len(r) > 5 else None))
    prev = (previous or {}).get("weights") or {}
    prev_city = (previous or {}).get("city_weights") or {}
    weights, evidence, city_weights, city_evidence = {}, {}, {}, {}
    for sc, by_day in sorted(by_scope.items()):
        start = float(prev.get(sc, W_PRIOR))
        totals, n_rows = [0.0] * len(W_GRID), 0
        for rs in by_day.values():
            for g, _c in rs:
                _add(totals, g)
                n_rows += 1
        i = _best(totals)
        if len(by_day) < MIN_DAYS:
            weights[sc] = start
            evidence[sc] = {"days": len(by_day), "held": f"fewer than {MIN_DAYS} settled days"}
        else:
            # best: what every day before as_of chooses, the w a pass moves
            # toward. forward: whether choosing that way has paid on days it
            # had not seen (_forward); the choice is never scored on its own days.
            best = W_GRID[i]
            forward = _forward_verdict(_forward(by_day))
            target = best if (best > 0 and forward["passed"]) else W_PRIOR
            step = max(-MAX_STEP, min(MAX_STEP, target - start))
            weights[sc] = round(min(max(start + step, W_BOUNDS[0]), W_BOUNDS[1]), 4)
            evidence[sc] = {"days": len(by_day), "rows": n_rows, "best_w": best, "forward": forward,
                            "target": target}
        cw, city_evidence[sc] = _fit_cities(by_day, i, weights[sc], prev_city.get(sc) or {})
        if cw:
            city_weights[sc] = cw
    body = {"as_of": str(as_of), "w_prior": W_PRIOR, "bounds": list(W_BOUNDS), "min_days": MIN_DAYS,
            "max_step": MAX_STEP, "recent_days": RECENT_DAYS, "previous": (previous or {}).get("version"),
            "weights": weights,
            "evidence": evidence, "city_min_days": CITY_MIN_DAYS, "city_k": CITY_K,
            "city_weights": city_weights, "city_evidence": city_evidence}
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
