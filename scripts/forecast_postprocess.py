#!/usr/bin/env python3
"""
Turn a public point forecast into a calibrated predictive distribution.

WHAT WAS ALREADY HERE, AND WHY IT IS NOT ENOUGH
-----------------------------------------------
probability_engine.py already post-processes. It subtracts a measured bias
from the centre and it sets the width from measured mean absolute error:

    centre = forecast - bias_c
    sigma  = mae_c * 1.2533 * regime * divergence * calibration

Three things were measured on 294 settled city-days, 16-21 Sep, and each one
is a defect this file exists to fix.

  1. THE BIAS IS SWITCHED OFF ON THE DAY THAT MATTERS MOST. A lead-0 market
     whose city has no lead-0 skill row borrows lead-1 skill, and borrowed
     skill is flagged `skill_proxy`, and a proxy forces bias_c to exactly
     zero. Measured on the live board: 6,666 of 8,118 lead-0 rows - 82% of
     today's ladder, the most-traded day on the desk - carry
     bias_applied_c = 0.0000, while the same cities' measured |bias| at lead 0
     averages 0.696 C. On a 1 C bucket that is two thirds of a bucket thrown
     away, every day, on purpose.

     The cliff is the problem, not the caution. "Proxy" is a binary flag over
     a continuum: a city with nine days of its own evidence is not in the same
     position as one with none, and forcing both to zero treats them as if it
     were. Shrinkage is the continuous version of the same caution and it has
     no cliff.

  2. THE SPREAD IS UNIFORMLY TOO WIDE. sd of (observed - centre)/sigma is 1.0
     if and only if sigma was honest. Measured per city over >= 5 settled
     days: median 0.542 - and of 49 cities, 35 read below 0.7, 14 read inside
     [0.7, 1.3], and NOT ONE reads above 1.3. On the current board alone
     (post the multiplier fix) it is 0.873. A distribution 13-15% too wide is
     one that understates every edge and mis-ranks every ladder.

  3. THE CORRECTION THAT FIXES (2) IS MEASURED AND DISABLED. ad4_45 computes
     exactly this z_sd per city and refuses to apply it below 30 days.
     derived_calibration_adjustment today: every row `applied = false`,
     n_days 4 or 5, reason "N verified day(s); needs 30." The desk measured
     its own over-dispersion, wrote the answer down, and switched it off.

THE METHOD
----------
Ensemble Model Output Statistics - EMOS, also called Nonhomogeneous Gaussian
Regression - is the standard operational answer to exactly this problem:
Gneiting, Raftery, Westveld and Goldman (2005), "Calibrated Probabilistic
Forecasting Using Ensemble Model Output Statistics and Minimum CRPS
Estimation", Monthly Weather Review 133. Its full form fits

    mu    = a + b * forecast
    sigma^2 = c + d * ensemble_spread^2

by minimising the continuous ranked probability score.

WE DO NOT FIT THE FULL FORM, because the evidence does not support it. Station
authority evidence per (city, lead) runs 8 to 18 days. Fitting a slope on ten
points is fitting noise and calling it science. So the reduced form:

    mu    = forecast - bias          (b fixed at 1)
    sigma = baseline_sigma * ratio

where `bias` and `ratio` are the two things ten days CAN determine, and the
slope stays at 1 until a cell holds SLOPE_MIN_DAYS. When the evidence arrives
the fuller form becomes available with nothing here to change.

PARTIAL POOLING IS THE POINT
----------------------------
A station bias is mostly a property of the STATION, not of the lead. KSEA
reads 2.148 C below what the forecast says across 247 settled rows; that is a
measurement. The same station's lead-0 cell holds ten days; that is an
anecdote. So every correction is shrunk twice, toward the thing with more
evidence behind it:

    bias_city  = shrink(measured city bias,  n_city, K_CITY, toward 0)
    bias_cell  = shrink(measured cell bias,  n_cell, K_CELL, toward bias_city)

with shrink(x, n, k, prior) = (n/(n+k)) * x + (k/(n+k)) * prior. This is
ordinary empirical-Bayes shrinkage - the James-Stein construction - and it is
what makes a ten-day cell usable instead of either trusted blindly or, as
today, discarded.

K IS NOT ASSERTED, IT IS CHOSEN
-------------------------------
K_CELL and K_CITY are picked from a grid by out-of-sample CRPS over every
cell at once, on held-out day blocks. A constant nobody measured is exactly
what this file is replacing, so it does not get to introduce two more.

OUT OF SAMPLE MEANS BLOCKED BY DAY
----------------------------------
Weather is autocorrelated, so a random hold-out leaks: the days either side of
a held-out day carry most of its information. Folds are CONTIGUOUS DAY BLOCKS,
same reasoning as the moving-block bootstrap in model_promotion.py.

THE GATE
--------
A correction is written for every cell and `applied` only when it beats the
uncorrected baseline on held-out blocks, by CRPS, with at least MIN_DAYS
behind it. A cell that fails stays in shadow and prices exactly as it does
today. Nothing here can make the desk worse than the number it replaces
without the measurement saying so first.
"""

import argparse
import datetime as dt
import math
import os
import sys
from collections import defaultdict, namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAE_TO_SIGMA = 1.2533       # sigma = MAE * sqrt(pi/2) for a normal distribution
INV_SQRT_PI = 0.5641895835477563

MIN_DAYS = 8                # below this a cell is not fitted at all
SLOPE_MIN_DAYS = 60         # the b term stays at 1 until a cell holds this many
N_FOLDS = 4                 # contiguous day blocks for the held-out score
RATIO_FLOOR = 0.50          # a fitted sigma may not fall below half the baseline
RATIO_CEILING = 2.00        # nor rise above twice it
BIAS_CEILING_C = 5.0        # a correction larger than this is a data fault, not a bias
SIGMA_FLOOR_C = 0.25        # no distribution narrower than a quarter degree

# The grid K is chosen from. Wide enough to contain "trust the cell" (k small)
# and "trust the city" (k large) at both levels.
K_CELL_GRID = (2.0, 5.0, 10.0, 20.0, 40.0)
K_CITY_GRID = (5.0, 15.0, 30.0, 60.0, 120.0)

VERIFIED_EVIDENCE_SCOPE = "verified_outcomes_v1"
FALLBACK_OBS_SOURCE = "v_city_daily_max"   # the pre-station-authority reader

Day = namedtuple("Day", "for_date forecast_c observed_c spread_c")
Fit = namedtuple("Fit", "bias_c sigma_ratio baseline_sigma_c n_days slope")


# ---------------------------------------------------------------------------
# The scoring rule.
# ---------------------------------------------------------------------------
def normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def normal_pdf(z):
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def crps_gaussian(mu, sigma, y):
    """CRPS of N(mu, sigma) against the observation y, in the units of y.

    Closed form (Gneiting et al. 2005, eq. 5):

        CRPS = sigma * [ z(2*Phi(z) - 1) + 2*phi(z) - 1/sqrt(pi) ],  z=(y-mu)/s

    Lower is better, it is a PROPER scoring rule - a forecaster cannot improve
    its expected score by stating a distribution other than its true belief -
    and unlike a Brier score over buckets it does not depend on where the
    bucket edges happen to fall. That is why the fit is judged on it and the
    ladder is priced from it, rather than the other way round.
    """
    if sigma is None or sigma <= 0:
        return float("inf")
    z = (y - mu) / sigma
    return sigma * (z * (2.0 * normal_cdf(z) - 1.0) + 2.0 * normal_pdf(z) - INV_SQRT_PI)


def shrink(value, n, k, prior):
    """Empirical-Bayes shrinkage of a measurement toward a prior.

    weight = n/(n+k), so k is "how many observations the prior is worth". At
    n = k the answer is halfway. n = 0 returns the prior exactly, which is the
    behaviour a cell with no evidence of its own must have.
    """
    if n <= 0:
        return prior
    w = float(n) / (float(n) + float(k))
    return w * float(value) + (1.0 - w) * float(prior)


# ---------------------------------------------------------------------------
# The fit.
# ---------------------------------------------------------------------------
def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def measured_bias(days):
    """Mean signed error, forecast minus observed. Positive = forecast runs hot.

    Same sign convention as fact_forecast_outcome.error_c and
    derived_forecast_skill.bias_c, so `centre - bias` de-biases in both.
    """
    return _mean(d.forecast_c - d.observed_c for d in days)


def baseline_sigma(days):
    """The width the desk publishes today: mean absolute error times sqrt(pi/2).

    Kept as the baseline rather than replaced outright so the ratio below is a
    correction TO the current behaviour, and a ratio of 1.0 means "today's
    number was already right".
    """
    mae = _mean(abs(d.forecast_c - d.observed_c) for d in days)
    return max(SIGMA_FLOOR_C, mae * MAE_TO_SIGMA)


def measured_ratio(days, bias_c, base_sigma):
    """The factor that makes sd(z) equal 1.

    For a Gaussian centred at `forecast - bias`, the maximum-likelihood sigma
    is the root mean square of the residuals. Dividing it by the baseline
    gives the multiplier the published number was missing - which is the same
    quantity ad4_45 calls z_sd, arrived at from the residuals rather than from
    the z-scores, so it does not need the published sigma to have existed.
    """
    if base_sigma <= 0:
        return 1.0
    ms = _mean((d.observed_c - (d.forecast_c - bias_c)) ** 2 for d in days)
    return math.sqrt(ms) / base_sigma


def fit_cell(cell_days, city_days, k_cell, k_city):
    """The shrunk correction for one (city, lead), given the city's own pool.

    `city_days` is every settled day for the city ACROSS LEADS, which is where
    the station's own bias is actually determined - 112 city-day-leads on the
    median city against 8-18 in a single cell.
    """
    if not cell_days:
        return None
    base = baseline_sigma(cell_days)

    b_city_raw = measured_bias(city_days) if city_days else 0.0
    b_city = shrink(b_city_raw, len(city_days), k_city, 0.0)
    b_cell = shrink(measured_bias(cell_days), len(cell_days), k_cell, b_city)
    b_cell = max(-BIAS_CEILING_C, min(BIAS_CEILING_C, b_cell))

    r_city_raw = measured_ratio(city_days, b_city, baseline_sigma(city_days)) if city_days else 1.0
    r_city = shrink(r_city_raw, len(city_days), k_city, 1.0)
    r_cell = shrink(measured_ratio(cell_days, b_cell, base), len(cell_days), k_cell, r_city)
    r_cell = max(RATIO_FLOOR, min(RATIO_CEILING, r_cell))

    return Fit(bias_c=b_cell, sigma_ratio=r_cell, baseline_sigma_c=base,
               n_days=len(cell_days), slope=1.0)


def apply_fit(fit, forecast_c):
    """(centre, sigma) for one forecast under a fit. The whole read path."""
    centre = fit.slope * forecast_c - fit.bias_c
    sigma = max(SIGMA_FLOOR_C, fit.baseline_sigma_c * fit.sigma_ratio)
    return centre, sigma


# ---------------------------------------------------------------------------
# Held-out scoring.
# ---------------------------------------------------------------------------
def blocked_folds(n, k=N_FOLDS):
    """k contiguous index blocks over days already sorted by date.

    Contiguous, not interleaved: a random hold-out over autocorrelated days
    puts a held-out day's neighbours in the training set, which scores a fit
    on information it was given.
    """
    if n <= 0 or k <= 1:
        return []
    k = min(k, n)
    size, rem, out, start = n // k, n % k, [], 0
    for i in range(k):
        stop = start + size + (1 if i < rem else 0)
        if stop > start:
            out.append((start, stop))
        start = stop
    return out


def score_days(days, fit):
    """Mean CRPS of a fit over days it did not see. inf if it cannot price."""
    if not days or fit is None:
        return float("inf")
    total = 0.0
    for d in days:
        mu, s = apply_fit(fit, d.forecast_c)
        total += crps_gaussian(mu, s, d.observed_c)
    return total / len(days)


def baseline_fit(days):
    """What the desk does today, as a Fit: no bias, sigma = mae * 1.2533.

    Zero bias rather than the measured one because that IS the live behaviour
    on the lead-0 board this file was written to fix. A cell that already gets
    its bias applied is measured against the harder baseline in
    `baseline_fit_with_bias` below, and the gate takes the better of the two
    so a correction can never be credited with a gain it did not make.
    """
    if not days:
        return None
    return Fit(bias_c=0.0, sigma_ratio=1.0, baseline_sigma_c=baseline_sigma(days),
               n_days=len(days), slope=1.0)


def baseline_fit_with_bias(days):
    """The harder baseline: today's unshrunk bias and today's width."""
    if not days:
        return None
    return Fit(bias_c=measured_bias(days), sigma_ratio=1.0,
               baseline_sigma_c=baseline_sigma(days), n_days=len(days), slope=1.0)


def cross_validate(cell_days, city_days, k_cell, k_city, folds=N_FOLDS):
    """(crps_fitted, crps_baseline, n_scored) over held-out contiguous blocks.

    The city pool is trimmed to the training dates too. Leaving it whole would
    let a held-out day inform the city bias that prices it - the subtle half of
    the same leak the blocked folds close.
    """
    days = sorted(cell_days, key=lambda d: d.for_date)
    blocks = blocked_folds(len(days), folds)
    if not blocks:
        return float("inf"), float("inf"), 0
    fit_total = base_total = 0.0
    scored = 0
    for start, stop in blocks:
        test = days[start:stop]
        train = days[:start] + days[stop:]
        if not train or not test:
            continue
        test_dates = {d.for_date for d in test}
        city_train = [d for d in city_days if d.for_date not in test_dates]
        f = fit_cell(train, city_train, k_cell, k_city)
        b = baseline_fit(train)
        bb = baseline_fit_with_bias(train)
        if f is None or b is None:
            continue
        fit_total += score_days(test, f) * len(test)
        base_total += min(score_days(test, b), score_days(test, bb)) * len(test)
        scored += len(test)
    if scored == 0:
        return float("inf"), float("inf"), 0
    return fit_total / scored, base_total / scored, scored


def choose_shrinkage(cells, k_cell_grid=K_CELL_GRID, k_city_grid=K_CITY_GRID,
                     folds=N_FOLDS):
    """One (k_cell, k_city) for the whole desk, chosen on held-out CRPS.

    Pooled across every cell rather than per cell: a K fitted per cell on the
    same ten days it then prices is the over-fit this file exists to avoid,
    and the prior strength is a property of how much evidence a cell-sized
    sample carries, which is a desk-wide question.

    Ties break toward the LARGER k - more shrinkage, less trust in the thin
    sample - because when the evidence cannot separate two answers the
    conservative one is correct.
    """
    best = None
    table = []
    for kc in k_cell_grid:
        for kt in k_city_grid:
            total = base_total = 0.0
            scored = 0
            for cell_days, city_days in cells:
                f, b, n = cross_validate(cell_days, city_days, kc, kt, folds)
                if n == 0 or not math.isfinite(f) or not math.isfinite(b):
                    continue
                total += f * n
                base_total += b * n
                scored += n
            if scored == 0:
                continue
            crps = total / scored
            row = {"k_cell": kc, "k_city": kt, "crps": crps,
                   "crps_baseline": base_total / scored, "n": scored}
            table.append(row)
            if best is None or crps < best["crps"] - 1e-12 or (
                    abs(crps - best["crps"]) <= 1e-12
                    and (kc, kt) > (best["k_cell"], best["k_city"])):
                best = row
    return best, table


# ---------------------------------------------------------------------------
# Evidence.
# ---------------------------------------------------------------------------
def _newest_per_run_key(rows):
    """One row per (city, for_date, lead): the newest run, whichever model.

    fact_forecast_outcome holds a row per MODEL, and the intraday job re-fetches
    the same date, so a city-day-lead can carry several. Counting them all
    would multiply a ten-day sample by the number of models and make thin
    evidence look decisive - the same trap ad4_45 avoids by taking one row per
    city-day.

    NEWEST RUN REGARDLESS OF MODEL is not a shortcut, it is the alignment that
    makes the fit apply. probability_engine._forecast_for orders by
    `lead_days.asc, run_at.desc` and takes one row, so the number being priced
    is the newest run whichever model produced it. Fitting on a per-model
    average would correct a forecast the desk never quotes.
    """
    best, spread = {}, defaultdict(list)
    for r in rows:
        f, o = r.get("forecast_max_c"), r.get("observed_max_c")
        if f is None or o is None or r.get("lead_days") is None:
            continue
        key = (r["city_key"], str(r["for_date"]), int(r["lead_days"]))
        spread[key].append(float(f))
        cur = best.get(key)
        if cur is None or str(r.get("run_at") or "") > str(cur.get("run_at") or ""):
            best[key] = r
    out = {}
    for key, r in best.items():
        vals = spread[key]
        out[key] = Day(for_date=key[1],
                       forecast_c=float(r["forecast_max_c"]),
                       observed_c=float(r["observed_max_c"]),
                       spread_c=(max(vals) - min(vals)) if len(vals) > 1 else 0.0)
    return out


def load_evidence(lookback_days=365, verified_only=True):
    """Settled days per (city, lead), station-authority evidence only.

    THE FILTER IS NOT OPTIONAL. 2,268 rows dated 26 Aug - 7 Sep carry
    obs_source = 'v_city_daily_max', the reader in use before the settlement
    repair - the one that let US five-minute feeds over-read and international
    hourly feeds under-read. Fitting a bias correction on those days would
    learn the reader's error and then apply it to days the reader no longer
    makes. A correction is only ever as good as the outcome behind it.
    """
    from common import rest_all
    since = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    params = [
        ("select", "city_key,for_date,lead_days,model,run_at,"
                   "forecast_max_c,observed_max_c,obs_source"),
        ("observed_max_c", "not.is.null"),
        ("forecast_max_c", "not.is.null"),
        ("for_date", f"gte.{since}"),
        ("lead_days", "gte.0"),
        ("lead_days", "lte.7"),
    ]
    if verified_only:
        params.append(("obs_source", f"neq.{FALLBACK_OBS_SOURCE}"))
        params.append(("obs_source", "not.is.null"))
    # The order has to be UNIQUE or pagination skips rows: the primary key is
    # (city_key, for_date, model, lead_days), and leaving `model` out of the
    # sort leaves ties for the server to break however it likes between pages.
    rows = rest_all("fact_forecast_outcome", params,
                    order="city_key.asc,for_date.asc,lead_days.asc,model.asc",
                    page_size=1000)
    by_cell = defaultdict(list)
    for (city, _date, lead), day in _newest_per_run_key(rows).items():
        by_cell[(city, lead)].append(day)
    for days in by_cell.values():
        days.sort(key=lambda d: d.for_date)
    return by_cell


def city_pools(by_cell):
    """Every settled day a city has, across leads - the partial-pooling parent.

    A day appears once per lead it was forecast at, and that is correct: each
    is a separate (forecast, observation) pair and each carries the station's
    representativeness error. What it is not is 8 independent days, so the
    weight this pool earns is bounded by K_CITY rather than by its raw count.
    """
    pools = defaultdict(list)
    for (city, _lead), days in by_cell.items():
        pools[city].extend(days)
    for days in pools.values():
        days.sort(key=lambda d: d.for_date)
    return pools


def fit_all(by_cell, pools, k_cell, k_city, min_days=MIN_DAYS, folds=N_FOLDS):
    """One decided row per cell. Cells below min_days are reported, not fitted."""
    out = []
    for (city, lead), days in sorted(by_cell.items()):
        pool = pools.get(city, [])
        n = len(days)
        spread_days = sum(1 for d in days if d.spread_c > 0)
        base = {
            "city_key": city, "lead_days": lead, "n_days": n,
            "n_city_days": len(pool), "spread_days": spread_days,
            "k_cell": k_cell, "k_city": k_city,
            "first_day": days[0].for_date if days else None,
            "last_day": days[-1].for_date if days else None,
            "evidence_scope": VERIFIED_EVIDENCE_SCOPE,
        }
        if n < min_days:
            out.append({**base, "bias_c": 0.0, "sigma_ratio": 1.0,
                        "baseline_sigma_c": None, "crps_fitted": None,
                        "crps_baseline": None, "crps_gain": None,
                        "applied": False,
                        "reason": f"{n} settled day(s); needs {min_days}"})
            continue
        fit = fit_cell(days, pool, k_cell, k_city)
        crps_f, crps_b, scored = cross_validate(days, pool, k_cell, k_city, folds)
        gain = (crps_b - crps_f) if (math.isfinite(crps_f) and math.isfinite(crps_b)) else None
        applied = bool(gain is not None and gain > 0.0 and scored > 0)
        if applied:
            reason = (f"held-out CRPS {crps_f:.4f} against {crps_b:.4f} over "
                      f"{scored} day(s): {gain:+.4f} C better")
        elif gain is None:
            reason = "held-out score could not be computed"
        else:
            reason = (f"held-out CRPS {crps_f:.4f} against {crps_b:.4f} over "
                      f"{scored} day(s): {gain:+.4f} C - no gain, stays in shadow")
        out.append({**base,
                    "bias_c": round(fit.bias_c, 4),
                    "sigma_ratio": round(fit.sigma_ratio, 4),
                    "baseline_sigma_c": round(fit.baseline_sigma_c, 4),
                    "crps_fitted": round(crps_f, 5) if math.isfinite(crps_f) else None,
                    "crps_baseline": round(crps_b, 5) if math.isfinite(crps_b) else None,
                    "crps_gain": round(gain, 5) if gain is not None else None,
                    "applied": applied, "reason": reason})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--lookback", type=int, default=365)
    ap.add_argument("--min-days", type=int, default=MIN_DAYS)
    ap.add_argument("--folds", type=int, default=N_FOLDS)
    ap.add_argument("--dry-run", action="store_true",
                    help="fit and report, write nothing")
    args = ap.parse_args()

    from common import upsert_replace, log_run

    by_cell = load_evidence(args.lookback)
    if not by_cell:
        print("no station-authority settled days - nothing to fit", file=sys.stderr)
        log_run("forecast_postprocess", "ok", 0, "no evidence")
        return 0
    pools = city_pools(by_cell)
    fittable = [(d, pools.get(c, [])) for (c, _l), d in by_cell.items()
                if len(d) >= args.min_days]
    print(f"{len(by_cell)} cell(s), {len(pools)} city pool(s), "
          f"{len(fittable)} cell(s) with >= {args.min_days} days")

    best, table = choose_shrinkage(fittable, folds=args.folds)
    if best is None:
        print("no cell could be cross-validated - nothing to fit", file=sys.stderr)
        log_run("forecast_postprocess", "ok", 0, "no cross-validatable cell")
        return 0
    for row in sorted(table, key=lambda r: r["crps"])[:5]:
        print(f"  k_cell={row['k_cell']:>5} k_city={row['k_city']:>5}  "
              f"CRPS {row['crps']:.5f} vs baseline {row['crps_baseline']:.5f}  "
              f"n={row['n']}")
    print(f"chosen: k_cell={best['k_cell']} k_city={best['k_city']}  "
          f"CRPS {best['crps']:.5f} vs {best['crps_baseline']:.5f} "
          f"({best['crps_baseline'] - best['crps']:+.5f} C)")

    rows = fit_all(by_cell, pools, best["k_cell"], best["k_city"],
                   args.min_days, args.folds)
    n_applied = sum(1 for r in rows if r["applied"])
    print(f"{len(rows)} cell(s), {n_applied} applied, {len(rows) - n_applied} shadow")
    if args.dry_run:
        for r in sorted(rows, key=lambda r: (r["city_key"], r["lead_days"]))[:20]:
            print(f"  {r['city_key']:<16} lead {r['lead_days']}  "
                  f"bias {r['bias_c']:+.3f}  ratio {r['sigma_ratio']:.3f}  "
                  f"{'APPLIED' if r['applied'] else 'shadow '}  {r['reason']}")
        return 0
    # REPLACE, NOT IGNORE, AND STAMP THE HOUR IT WAS DECIDED.
    #
    # This is a verdict table - one row per (city, lead) saying what to
    # subtract and how much to narrow - and upsert() is ignore-duplicates, so
    # every run after the first computed 399 correct rows and the database
    # threw all of them away. MEASURED, 2026-09-22: the 17:27 run wrote the
    # table, the 17:50 run recomputed it against a day more settled evidence
    # and `computed_at` never moved off 17:27:16. That is the identical
    # failure upsert_replace()'s own docstring records for
    # derived_model_promotion the day before, and a bias that cannot be
    # re-fitted is not a fitted layer.
    #
    # computed_at travels on the row because merge-duplicates updates only the
    # columns the payload carries - a default of now() fires on INSERT and
    # never again - and ad4_39_freshness reads that column to decide whether
    # this layer is still current.
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    for r in rows:
        r["computed_at"] = computed_at
    upsert_replace("derived_forecast_postprocess", rows,
                   on_conflict="city_key,lead_days")
    log_run("forecast_postprocess", "ok", len(rows),
            f"{n_applied} applied, k_cell={best['k_cell']}, k_city={best['k_city']}, "
            f"CRPS {best['crps']:.5f} vs {best['crps_baseline']:.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
