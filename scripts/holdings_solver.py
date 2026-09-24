"""The holdings solver (plan v2 P5.5), part 1: the growth-optimal ladder.

ONE CITY-DAY LADDER. Its buckets are mutually exclusive and exactly one pays
$1. A position is a set of fractions of the ledger's wealth: some kept as
cash, some spent on YES shares of a bucket (pays in one outcome), some on NO
shares (pays in every outcome but one). The ledger's growth if outcome k
happens is log(W_k), where W_k is what the whole book is worth then; the
objective is its expectation, sum_k p_k log W_k. That is concave in the
fractions, so it has one maximum and the maximum is found, not guessed.

PRICES INCLUDE THE FEE. A share bought at price q costs q + rate*q*(1-q)
(allocator.effective_cost, the venue's curve), so an asset's payoff per
dollar is 1 / that.

TWO WAYS TO THE SAME ANSWER.
  horse_race()  The closed form for YES only, no caps (Kelly 1956): sort the
                buckets by p / c, include them while p_i / c_i > R with
                R = (1 - sum p_incl) / (1 - sum c_incl), spend p_i - R*c_i of
                wealth on each, keep R in cash.
  solve()       Cover's multiplicative update for the log-optimal portfolio,
                w_j <- w_j * sum_k p_k x_jk / (w . x_k), which rises
                monotonically to the optimum on the simplex for ANY payoff
                matrix - so it also handles NO shares and any subset of
                allowed assets (S10 YES-only, S12 NO-only). The tests hold the
                two equal on random ladders.

WHAT PART 1 DOES NOT DO YET, and says so rather than approximating it:
depth caps and the risk layer's caps (upper bounds on a fraction), existing
holdings and the cost of selling them (the bid ladder), and the lock
constraint inside the optimiser. lock_holds() checks a finished book. Part 2
adds those, with a solver that takes bounds.

A FINDING ABOUT THE PLAN'S OBJECTIVE. The plan asks for expected log-growth
"averaged over 200 posterior draws" of the ladder, so that an uncertain
bucket is sized small. But for a fixed book that average is exactly the
growth at the MEAN ladder:

    mean_d sum_k pi_dk log W_k  =  sum_k (mean_d pi_dk) log W_k

The draws cancel; p_sd changes nothing. test_the_draw_average_ignores_p_sd
proves it on real numbers. Making uncertainty shrink a bet needs a different
objective (for example the mean of the worst fraction of draws, which stays
concave), and that choice is Hassan's to make before part 2 wires one in.
Until then solve() uses the posterior means and growth_by_draw() reports the
spread across draws beside it.

RULE 11. lambda (the Kelly fraction) has prior 0.25 and bounds [0.05, 0.5];
h (the no-trade threshold) has prior 0.002 and bounds [0.0005, 0.01]. Both
are the plan's numbers, learned in P5.8, clipped here on every use.
"""
import math
import random

from allocator import effective_cost

LAMBDA_PRIOR, LAMBDA_BOUNDS = 0.25, (0.05, 0.5)
H_PRIOR, H_BOUNDS = 0.002, (0.0005, 0.01)
N_DRAWS = 200
COVER_ITERS = 20000
COVER_TOL = 1e-12


def _clip(x, bounds):
    return min(max(float(x), bounds[0]), bounds[1])


# --------------------------------------------------------------------------
# Assets and their payoffs
# --------------------------------------------------------------------------

def assets(ladder, allow=("YES", "NO")):
    """The tradeable assets of a ladder: cash, then YES/NO per bucket.

    ladder: [{"id", "p", "yes_price", "no_price"}] with p summing to 1.
    Returns [(name, side, index, cost_per_share)] and the payoff matrix
    x[j][k] = what one dollar in asset j is worth if outcome k happens.
    A bucket side with no price, or a price the venue cannot trade (0 or 1),
    is not an asset.
    """
    n = len(ladder)
    out = [("cash", "CASH", None, 1.0)]
    for i, b in enumerate(ladder):
        for side, key in (("YES", "yes_price"), ("NO", "no_price")):
            if side not in allow:
                continue
            c = effective_cost(b.get(key))
            if c is None:
                continue
            out.append((f"{b['id']}:{side}", side, i, c))
    x = []
    for _name, side, i, c in out:
        if side == "CASH":
            x.append([1.0] * n)
        elif side == "YES":
            x.append([(1.0 / c) if k == i else 0.0 for k in range(n)])
        else:
            x.append([0.0 if k == i else (1.0 / c) for k in range(n)])
    return out, x


def wealth(w, x):
    """W_k for every outcome, per dollar of starting wealth."""
    n = len(x[0])
    return [sum(w[j] * x[j][k] for j in range(len(w))) for k in range(n)]


def growth(p, w, x):
    """sum_k p_k log W_k; -inf if any outcome with p_k > 0 leaves nothing."""
    total = 0.0
    for pk, wk in zip(p, wealth(w, x)):
        if pk <= 0:
            continue
        if wk <= 0:
            return -math.inf
        total += pk * math.log(wk)
    return total


# --------------------------------------------------------------------------
# The closed form
# --------------------------------------------------------------------------

def horse_race(ladder):
    """Kelly's horse-race solution for YES only, fee-inclusive, no caps.

    Returns ({bucket id: fraction of wealth spent}, R = fraction kept).
    With no bucket where p > c it spends nothing: R = 1.
    """
    rows = []
    for b in ladder:
        c = effective_cost(b.get("yes_price"))
        if c is not None and b["p"] > 0:
            rows.append((b["p"] / c, b["id"], b["p"], c))
    rows.sort(reverse=True)
    # Add buckets in order of p / c while the next one beats R, the cash
    # return of the set so far (R = 1 before anything is bought).
    incl, sp, sc, R = [], 0.0, 0.0, 1.0
    for ratio, bid, p, c in rows:
        if ratio <= R or sc + c >= 1.0:
            break
        incl.append((bid, p, c))
        sp, sc = sp + p, sc + c
        R = (1.0 - sp) / (1.0 - sc)
    if not incl:
        return {}, 1.0
    return {bid: p - R * c for bid, p, c in incl}, R


# --------------------------------------------------------------------------
# The numeric optimum
# --------------------------------------------------------------------------

def solve(ladder, allow=("YES", "NO"), iters=COVER_ITERS, tol=COVER_TOL):
    """The log-optimal fractions over the allowed assets, by Cover's update.

    Returns {"weights": {asset name: fraction}, "cash": fraction kept,
    "growth": expected log-growth per period, "iterations": n}.
    """
    p = [float(b["p"]) for b in ladder]
    names, x = assets(ladder, allow)
    m = len(names)
    w = [1.0 / m] * m
    g_old = growth(p, w, x)
    it = 0
    for it in range(1, iters + 1):
        W = wealth(w, x)
        new = []
        for j in range(m):
            s = sum(p[k] * x[j][k] / W[k] for k in range(len(p)) if p[k] > 0)
            new.append(w[j] * s)
        z = sum(new)
        w = [v / z for v in new]
        g = growth(p, w, x)
        if abs(g - g_old) < tol:
            break
        g_old = g
    weights = {names[j][0]: w[j] for j in range(1, m) if w[j] > 1e-9}
    return {"weights": weights, "cash": w[0], "growth": growth(p, w, x), "iterations": it}


# --------------------------------------------------------------------------
# Posterior draws (reported, not optimised - see the module note)
# --------------------------------------------------------------------------

def concentration(ps, sds):
    """One Dirichlet concentration matching the buckets' sds.

    A Dirichlet's marginals have Var = p(1-p)/(kappa+1), so each bucket
    implies kappa = p(1-p)/sd^2 - 1. The ladder has one kappa; this takes the
    probability-weighted mean of the buckets' implied values, floored at 1.
    """
    num = den = 0.0
    for p, sd in zip(ps, sds):
        if p <= 0 or p >= 1 or not sd or sd <= 0:
            continue
        num += p * (p * (1.0 - p) / (sd * sd) - 1.0)
        den += p
    return max(num / den, 1.0) if den > 0 else 1.0


def dirichlet_draws(ps, kappa, n=N_DRAWS, seed=0):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        g = [rng.gammavariate(max(kappa * p, 1e-9), 1.0) for p in ps]
        z = sum(g)
        out.append([v / z for v in g])
    return out


def growth_by_draw(draws, w, x):
    return [growth(d, w, x) for d in draws]


# --------------------------------------------------------------------------
# lambda, the no-trade band, the lock, and S10
# --------------------------------------------------------------------------

def fractional(result, lam=LAMBDA_PRIOR):
    """Scale every risky fraction by lambda (clipped to its bounds); the rest is cash."""
    lam = _clip(lam, LAMBDA_BOUNDS)
    weights = {a: f * lam for a, f in result["weights"].items()}
    return {"weights": weights, "cash": 1.0 - sum(weights.values()), "lambda": lam}


def worth_trading(g_target, g_now, h=H_PRIOR):
    """Move only when the growth gained beats h (clipped to its bounds).

    Transaction costs are already inside g_target - every price is
    fee-inclusive - so they are not subtracted a second time here.
    """
    return (g_target - g_now) > _clip(h, H_BOUNDS)


def lock_holds(ladder, shares, spent_usd):
    """True when the book's net P&L is >= 0 whatever bucket wins.

    shares: {bucket id: (yes_shares, no_shares)}. spent_usd: all it cost.
    """
    ids = [b["id"] for b in ladder]
    for k in ids:
        payout = sum((y if bid == k else n) for bid, (y, n) in shares.items())
        if payout - spent_usd < -1e-9:
            return False
    return True


def single_bucket_growth(p, price):
    """The best growth from Kelly-betting YES on one bucket alone, fee-inclusive.

    At the Kelly stake f = (p - c) / (1 - c) the growth is
    p log(p/c) + (1-p) log((1-p)/(1-c)); zero when p <= c (no bet).
    """
    c = effective_cost(price)
    if c is None or p <= c or p >= 1.0:
        return 0.0
    return p * math.log(p / c) + (1.0 - p) * math.log((1.0 - p) / (1.0 - c))


def best_single_bucket(ladder):
    """S10's choice: the ONE bucket whose YES grows the ledger most.

    Not the most probable bucket. A 50% bucket at 45c grows the ledger less
    than a 20% bucket at 10c, because growth pays for the edge relative to
    the price, not for being likely (P7.5 documents the difference).
    """
    best, best_g = None, 0.0
    for b in ladder:
        g = single_bucket_growth(b["p"], b.get("yes_price"))
        if g > best_g:
            best, best_g = b["id"], g
    return best, best_g
