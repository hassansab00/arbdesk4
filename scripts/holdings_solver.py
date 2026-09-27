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
proves it on real numbers.

SO THE OBJECTIVE IS THE WORST FRACTION OF DRAWS (Hassan, 24 Sep: "do the
effective, efficient, smart and logical way"). solve_robust() maximises the
mean growth over the worst ALPHA of the posterior draws - a CVaR of the
growth. Each draw's growth is concave in the book, and the mean of the worst
fraction of concave functions is concave, so there is still one maximum. A
bucket the belief layer is sure of looks the same in every draw and keeps its
size; a bucket it is unsure of is priced at its bad draws and is sized down,
which is what the plan wanted the draws for. At ALPHA = 1 it is exactly the
mean objective, so the plan's version is the limit of this one.

How it is solved: for the current book, the worst ALPHA of draws average to
one pessimistic ladder q; a Cover step on q moves the book up that ladder's
growth; repeat, and keep the running mean of the books. That is mirror
ascent on the concave objective, and the running mean converges to its
maximum. The tests hold it to three properties: it equals solve() when the
draws do not vary, it is never worse than the mean-optimal book on the worst
draws, and it shrinks as the sd grows.

RULE 11. lambda (the Kelly fraction) has prior 0.25 and bounds [0.05, 0.5];
h (the no-trade threshold) has prior 0.002 and bounds [0.0005, 0.01]. Both
are the plan's numbers, learned in P5.8, clipped here on every use. ALPHA,
the worst fraction of draws, has prior 0.25 and bounds [0.05, 1.0]: this
module's prior, not a measurement, learned in P5.8 like lambda (from whether
realised growth tracks the growth the robust book predicted).
"""
import math
import random
from operator import mul

from allocator import effective_cost

LAMBDA_PRIOR, LAMBDA_BOUNDS = 0.25, (0.05, 0.5)
H_PRIOR, H_BOUNDS = 0.002, (0.0005, 0.01)
N_DRAWS = 200
ALPHA_PRIOR, ALPHA_BOUNDS = 0.25, (0.05, 1.0)
ROBUST_ITERS = 3000
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


def worst_mean(values, alpha):
    """Mean of the worst ceil(alpha * n) values: the CVaR of a growth sample."""
    k = max(1, math.ceil(_clip(alpha, ALPHA_BOUNDS) * len(values)))
    return sum(sorted(values)[:k]) / k


def solve_robust(ladder, sds, alpha=ALPHA_PRIOR, allow=("YES", "NO"),
                 n_draws=N_DRAWS, seed=0, iters=ROBUST_ITERS):
    """The book that maximises mean growth over the worst ALPHA of posterior draws.

    ladder carries each bucket's posterior mean as "p"; sds is each bucket's
    posterior sd (belief.ladder_posterior). Draws are seeded, so the answer is
    reproducible. Returns solve()'s shape plus the robust growth, the mean
    growth, alpha and kappa.
    """
    alpha = _clip(alpha, ALPHA_BOUNDS)
    ps = [float(b["p"]) for b in ladder]
    names, x = assets(ladder, allow)
    m, n = len(names), len(ps)
    kappa = concentration(ps, sds)
    draws = dirichlet_draws(ps, kappa, n_draws, seed)
    k = max(1, math.ceil(alpha * len(draws)))

    # The running mean is taken over the second half only: the first half
    # still carries the uniform starting book, which would leave every asset
    # - even one with no edge - a sliver of weight.
    w = [1.0 / m] * m
    avg = [0.0] * m
    counted = 0
    for t in range(1, iters + 1):
        W = wealth(w, x)
        logW = [math.log(v) if v > 0 else -1e9 for v in W]
        worst = sorted(draws, key=lambda d: sum(d[j] * logW[j] for j in range(n)))[:k]
        q = [sum(d[j] for d in worst) / k for j in range(n)]
        new = [w[j] * sum(q[i] * x[j][i] / W[i] for i in range(n) if q[i] > 0) for j in range(m)]
        z = sum(new)
        w = [v / z for v in new]
        if t > iters // 2:
            counted += 1
            avg = [a + (v - a) / counted for a, v in zip(avg, w)]
    robust = worst_mean(growth_by_draw(draws, avg, x), alpha)
    return {"weights": {names[j][0]: avg[j] for j in range(1, m) if avg[j] > 1e-9},
            "cash": avg[0], "growth": robust, "mean_growth": growth(ps, avg, x),
            "alpha": alpha, "kappa": kappa, "iterations": iters}


# --------------------------------------------------------------------------
# Part 2: the book under caps, with what the ledger already holds, and the lock
# --------------------------------------------------------------------------

BOOK_ITERS = 4000
BIND_TOL = 1e-4


def _project_bounded(w, lo, u, total, iters=200):
    """The KL projection of w onto {lo_j <= w_j <= u_j, sum w = total}.

    Its solution is w_j(t) = clip(t * w_j, lo_j, u_j) for the one t that makes
    the sum `total`; the sum rises with t, so t is found by bisection. A
    coordinate with w_j = 0 sits on its lower bound, as it should.
    """
    def total_at(t):
        return sum(min(max(t * v, a), b) for v, a, b in zip(w, lo, u))
    lo_t, hi_t = 0.0, 1.0
    while total_at(hi_t) < total and hi_t < 1e12:
        hi_t *= 2.0
    for _ in range(iters):
        mid = (lo_t + hi_t) / 2.0
        if total_at(mid) < total:
            lo_t = mid
        else:
            hi_t = mid
    return [min(max(hi_t * v, a), b) for v, a, b in zip(w, lo, u)]


def _project(w, u, total):
    """The KL projection of w onto {0 <= w_j <= u_j, sum w = total}.

    Cap every coordinate that exceeds its cap, rescale the free ones to fill
    what is left, repeat until nothing exceeds. It is exact for the capped
    simplex, and feasible whenever the uncapped cash coordinate exists.
    """
    w = [max(v, 0.0) for v in w]
    capped = [False] * len(w)
    for _ in range(len(w) + 1):
        fixed = sum(u[j] for j in range(len(w)) if capped[j])
        free_sum = sum(w[j] for j in range(len(w)) if not capped[j])
        room = total - fixed
        scale = room / free_sum if free_sum > 0 else 0.0
        out = [u[j] if capped[j] else w[j] * scale for j in range(len(w))]
        over = [j for j in range(len(w)) if not capped[j] and out[j] > u[j]]
        if not over:
            return out
        for j in over:
            capped[j] = True
    return out


def _lock_book(best, names, u, c0, n, wealth_of, objective, steps=200):
    """The best book that never ends below the ledger's wealth, or all cash.

    The growth-optimal book broke the lock. A book that cannot lose is the
    EQUAL-SHARES book - the same number of YES shares on every bucket (pays the
    same whichever wins), or of NO shares on every bucket (pays n - 1 shares):
    the old S6's insurance. It exists only when those shares cost less than
    they are sure to pay. Between it and the growth-optimal book every mix is
    feasible where the lock holds, and growth is concave along the segment, so
    the best feasible mix on a fine grid is taken. With neither equal-shares
    book on offer, nothing is bought: the lock never pays to lose.
    """
    m = len(names)
    cash_only = [c0] + [0.0] * (m - 1)
    candidates = []
    for side, pays in (("YES", 1.0), ("NO", float(n - 1))):
        idx = [j for j in range(1, m) if names[j][1] == side]
        if len(idx) != n:
            continue                              # a bucket side has no price: no equal-shares book
        cost = sum(names[j][3] for j in idx)       # one share of each
        if cost >= pays:
            continue
        # Spend what the caps allow, all of it on equal shares.
        k_shares = min([c0 / cost] + [u[j] / names[j][3] for j in idx])
        eq = [c0 - k_shares * cost] + [0.0] * (m - 1)
        for j in idx:
            eq[j] = k_shares * names[j][3]
        candidates.append(eq)
    best_book, best_g = cash_only, objective(cash_only)
    for eq in candidates:
        for t in range(steps + 1):
            a = t / steps
            w = [(1 - a) * e + a * b for e, b in zip(eq, best)]
            if min(wealth_of(w)) < 1.0 - 1e-9:
                continue
            g = objective(w)
            if g > best_g:
                best_book, best_g = w, g
    return best_book


def solve_book(ladder, allow=("YES", "NO"), caps=None, held=None, total_usd=1.0, cash_usd=None,
               sds=None, alpha=ALPHA_PRIOR, lock=False, iters=BOOK_ITERS, n_draws=N_DRAWS, seed=0,
               max_spend=None, max_price=None):
    """The growth-optimal NEW purchases for one ladder, under every constraint part 2 knows.

    caps      {asset name: most of total wealth it may take} - depth at the
              limit, the risk layer's caps (P5.9), the rails. Cash is never capped.
    held      {bucket id: (yes_shares, no_shares)} already on the ledger. They
              pay what they pay whatever this decides; the solver buys around
              them, so a bucket already held is not bought twice.
    total_usd the ledger's wealth (cash plus what it holds, at cost); cash_usd
              the part that is free to spend (default: all of it).
    sds       posterior sds: given, the objective is the worst ALPHA of draws
              (solve_robust's); absent, the mean ladder (solve's).
    lock      the book - holdings plus purchases - must not end below the
              ledger's wealth in ANY outcome. If the growth-optimal book
              breaks it, the best book on the segment from the equal-shares
              (insurance) book to it is taken; with no equal-shares book on
              offer, nothing is bought (_lock_book).

    max_spend the most of total wealth this ladder may take in new purchases,
              all assets together: the city-day rail (risk_rails.ladder_budget).
    max_price no YES or NO bought above it: the price rail (0.97).

    Selling what is held is not decided here: it is the no-trade band's and
    the timing step's (P5.6) question, weighed against the bid ladder.

    Returns {"weights", "cash", "growth", "binding": [...], "wealth_by_outcome"}.
    """
    if max_price is not None:
        # Above the rail a side is not an asset at all.
        ladder = [dict(b, **{k: (None if b.get(k) is not None and float(b[k]) > max_price else b.get(k))
                             for k in ("yes_price", "no_price")}) for b in ladder]
    ps = [float(b["p"]) for b in ladder]
    names, x = assets(ladder, allow)
    m, n = len(names), len(ps)
    total = float(total_usd)
    c0 = (float(cash_usd) if cash_usd is not None else total) / total
    ids = [b["id"] for b in ladder]
    h = [0.0] * n
    for bid, (yes, no) in (held or {}).items():
        i = ids.index(bid)
        for k in range(n):
            h[k] += ((yes if k == i else 0.0) + (0.0 if k == i else no)) / total
    u = [c0] + [min(float((caps or {}).get(names[j][0], c0)), c0) for j in range(1, m)]

    robust = sds is not None
    if robust:
        alpha = _clip(alpha, ALPHA_BOUNDS)
        draws = dirichlet_draws(ps, concentration(ps, sds), n_draws, seed)
        kk = max(1, math.ceil(alpha * len(draws)))

    # THE SAME ARITHMETIC, IN THE SAME ORDER, WITHOUT INDEXING (27 Sep). One
    # call cost 0.5-0.8 s, 86% of it in step_p's per-element indexing, and the
    # tick has one billed minute (P5.12). Every sum below adds the same terms in
    # the same order as before and the sort is the same stable sort, so the
    # result is bit-identical (tests/test_holdings_solver_speed.py holds it so).
    xT = [[x[j][k] for j in range(m)] for k in range(n)]

    def wealth_of(w):
        return [sum(map(mul, w, xT[k])) + h[k] for k in range(n)]

    def step_p(W):
        if not robust:
            return ps
        logW = [math.log(v) if v > 0 else -1e9 for v in W]
        vals = [sum(map(mul, d, logW)) for d in draws]
        worst = sorted(range(len(draws)), key=vals.__getitem__)[:kk]
        return [sum(col) / kk for col in zip(*[draws[i] for i in worst])]

    # The city-day rail: all new purchases on this ladder together, so cash
    # may not fall below c0 - max_spend. A floor on cash, not a cap on assets.
    if max_spend is not None:
        lo = [max(c0 - max(float(max_spend), 0.0), 0.0)] + [0.0] * (m - 1)
        proj = lambda v: _project_bounded(v, lo, u, c0)
    else:
        proj = lambda v: _project(v, u, c0)
    w = proj([c0 / m] * m)
    avg, counted = [0.0] * m, 0
    for t in range(1, iters + 1):
        W = wealth_of(w)
        q = step_p(W)
        g = [sum(q[k] * x[j][k] / W[k] for k in range(n) if q[k] > 0) for j in range(m)]
        w = proj([w[j] * g[j] for j in range(m)])
        if t > iters // 2:
            counted += 1
            avg = [a + (v - a) / counted for a, v in zip(avg, w)]

    binding = [names[j][0] for j in range(1, m) if u[j] < c0 and avg[j] >= u[j] - BIND_TOL]
    if max_spend is not None and avg[0] <= c0 - float(max_spend) + BIND_TOL:
        binding.append("city_day")

    def objective(wv):
        if robust:
            return worst_mean([sum(d[k] * math.log(v) for k, v in enumerate(wealth_of(wv)) if d[k] > 0)
                               for d in draws], alpha)
        return sum(ps[k] * math.log(v) for k, v in enumerate(wealth_of(wv)) if ps[k] > 0)

    W = wealth_of(avg)
    if lock and min(W) < 1.0 - 1e-9:
        avg = _lock_book(avg, names, u, c0, n, wealth_of, objective)
        W = wealth_of(avg)
        binding.append("lock")

    return {"weights": {names[j][0]: avg[j] for j in range(1, m) if avg[j] > 1e-9},
            "cash": avg[0], "growth": objective(avg), "binding": binding,
            "wealth_by_outcome": dict(zip(ids, W)), "robust": robust}


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
