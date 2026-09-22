#!/usr/bin/env python3
"""
The day's own trajectory, priced - instead of a morning forecast at four in
the afternoon.

WHAT THE DESK DOES TODAY. probability_engine prices a band from a Normal
centred on the forecast, and uses the day's observations for exactly one
thing: `observed_floor_c` zeroes the bands the running maximum has already
passed and renormalises the rest. It never narrows the distribution and it
never moves its centre. At 16:00 local, on a day that peaked at 14:00, the
desk is still quoting a 1.50 C morning sigma.

WHAT THE DAY ACTUALLY SAYS, measured on this repo's own archive and sitting
in derived_climb_profile since it was built - 1,248 rows, 52 cities, 24
hours, a mean 89 days behind each, refreshed nightly, READ BY NOTHING:

    local hour   still to climb   sd     already peaked
    09           4.59 C           1.74    1.9%
    12           1.64 C           1.10   22.5%
    15           0.37 C           0.63   71.2%
    16           0.19 C           0.44   83.9%
    18           0.06 C           0.25   94.4%

At 16:00 the measured uncertainty about the rest of the day is 0.44 C against
a published 1.50 - three and a half times narrower - and on 84% of days the
running maximum already IS the answer. That gap is the measured explanation
for a model Brier of 0.808 against the market's 0.098: the market is trading
the trajectory and we are not.

--------------------------------------------------------------------------
TWO SEPARATE THINGS, AND ONLY ONE OF THEM IS A MODEL
--------------------------------------------------------------------------

1. THE IDENTITY, which is not a model and is not gated.

       final maximum = max(running maximum so far, maximum over the rest)

   So the predictive distribution of the final maximum is a Normal with an
   ATOM at the running maximum: every draw below it becomes exactly it.

       F(x) = 0        for x < a
            = Phi(z)   for x >= a,  z = (x - mu) / sigma

   The desk's floor is an approximation of this and it is the wrong one. It
   zeroes the passed bands and renormalises the survivors IN PROPORTION, so
   mass that belongs on the band holding the running maximum is sprinkled
   over every band above it. A day sitting at 25.3 C with 40% of the
   forecast's mass below 25.0 currently hands that 40% to 26, 27 and 28 in
   proportion, which reads as room to climb that the identity forbids. Under
   the atom it lands where it belongs: on the band containing 25.3.

2. THE TRAJECTORY CENTRE AND WIDTH, which IS a model and IS gated.

   Replacing (forecast centre, forecast sigma) with
   (temp_now + typical_climb_left, climb_left_sd) is a claim about which
   description of the rest of the day is better. It is fitted per city and
   local hour, cross-validated on held-out day blocks by CRPS, and applied
   only where it beats the floored forecast the desk already publishes.

   THE BASELINE IS THE FLOORED FORECAST, NOT A BARE ONE. Scoring against an
   unfloored Normal would credit this layer with a gain the floor already
   delivers, and the floor is being fixed here anyway. Both sides are
   max(a, Normal); only the centre and the width differ.

--------------------------------------------------------------------------
WHY CRPS, AND WHY IT HAS A CLOSED FORM HERE
--------------------------------------------------------------------------
CRPS is a proper scoring rule and, unlike a Brier score over buckets, does
not depend on where the bucket edges happen to fall - see the header of
scripts/forecast_postprocess.py, whose Gaussian CRPS this file imports
rather than defining a second copy of.

For M = max(a, X) with X ~ N(mu, sigma) and an observation y >= a, the score
is the Gaussian one minus the part of the integral the floor removes:

    CRPS = CRPS_gauss(mu, sigma, y)
           - sigma * [ alpha*Phi(alpha)^2 + 2*phi(alpha)*Phi(alpha)
                       - Phi(alpha*sqrt(2))/sqrt(pi) ],   alpha = (a-mu)/sigma

derived from  d/dt[t*Phi(t)^2] = Phi(t)^2 + 2t*Phi(t)*phi(t)  and
integral of phi(t)^2 = Phi(t*sqrt(2)) / (2*sqrt(pi)). As alpha -> -infinity
the bracket goes to zero and this is exactly the Gaussian CRPS, which is the
first thing the tests check.
"""

import argparse
import math
import os
import sys
from collections import defaultdict, namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forecast_postprocess import (  # one definition of each, shared
    crps_gaussian, normal_cdf, normal_pdf, blocked_folds, shrink,
)

MIN_DAYS = 10            # settled days in a (city, hour) cell before fitting
N_FOLDS = 4              # contiguous day blocks for the held-out score
SD_FLOOR_C = 0.15        # the day is never known to better than this
SD_RATIO_FLOOR = 0.5
SD_RATIO_CEILING = 2.5
K_SD = 10.0              # shrinkage of the width correction toward 1
MAX_READING_AGE_MIN = 90 # an observation older than this describes another hour

INV_SQRT_PI = 0.5641895835477563
SQRT_2 = math.sqrt(2.0)

Hour = namedtuple("Hour", "local_date local_hour temp_c running_max_c "
                          "climb_left_c climb_sd_c final_max_c "
                          "forecast_c forecast_sigma_c")
Fit = namedtuple("Fit", "city_key local_hour n_days sd_ratio "
                        "crps_trajectory crps_forecast crps_gain applied reason")


# ---------------------------------------------------------------------------
# The distribution.
# ---------------------------------------------------------------------------
def floored_cdf(x, floor_c, mu, sigma):
    """P(max(floor, N(mu, sigma)) <= x). Zero below the floor, Normal above.

    `floor_c` None means no observation yet, which is the plain Normal.
    """
    if sigma <= 0:
        return 1.0 if x >= max(mu, floor_c if floor_c is not None else mu) else 0.0
    if floor_c is not None and x < floor_c:
        return 0.0
    return normal_cdf((x - mu) / sigma)


def floored_band_mass(lo_edge, hi_edge, floor_c, mu, sigma):
    """Mass in [lo_edge, hi_edge), either edge None for an open end.

    THE ATOM LANDS HERE. The band holding the floor gets F(hi) - 0, which is
    every draw the floor pushed up to it, and that is the whole difference
    between this and zero-and-renormalise.
    """
    hi = 1.0 if hi_edge is None else floored_cdf(hi_edge, floor_c, mu, sigma)
    lo = 0.0 if lo_edge is None else floored_cdf(lo_edge, floor_c, mu, sigma)
    return max(0.0, hi - lo)


def _phi2_integral(alpha):
    """Integral of Phi(t)^2 from -infinity to alpha."""
    Pa = normal_cdf(alpha)
    return alpha * Pa * Pa + 2.0 * normal_pdf(alpha) * Pa - INV_SQRT_PI * normal_cdf(alpha * SQRT_2)


def crps_floored_gaussian(floor_c, mu, sigma, y):
    """CRPS of max(floor, N(mu, sigma)) against y. Lower is better.

    An observation BELOW the floor is impossible under this distribution - a
    running maximum cannot exceed the final one - so it is scored as the
    Gaussian would score it plus the distance, rather than silently returned
    as if the floor were right. It means a bad floor is punished instead of
    hidden, which is what a station mismatch looks like.
    """
    if sigma <= 0:
        return float("inf")
    if floor_c is None:
        return crps_gaussian(mu, sigma, y)
    if y < floor_c:
        return crps_gaussian(mu, sigma, y) + (floor_c - y)
    alpha = (floor_c - mu) / sigma
    return crps_gaussian(mu, sigma, y) - sigma * _phi2_integral(alpha)


# ---------------------------------------------------------------------------
# The two predictors, stated once.
# ---------------------------------------------------------------------------
def forecast_predictor(h, sd_ratio=1.0):
    """(floor, mu, sigma) for what the desk publishes today, floored."""
    return h.running_max_c, h.forecast_c, max(SD_FLOOR_C, h.forecast_sigma_c)


def trajectory_predictor(h, sd_ratio=1.0):
    """(floor, mu, sigma) for the rest of the day, from the climb profile.

    The centre is this hour's reading plus what the city typically has left
    to climb at this hour; the width is how variable that has been. Both come
    from derived_climb_profile, which is measured over ~89 days per cell.
    """
    return (h.running_max_c,
            h.temp_c + h.climb_left_c,
            max(SD_FLOOR_C, h.climb_sd_c * sd_ratio))


def score(rows, predictor, sd_ratio=1.0):
    """Mean CRPS of a predictor over rows it did not choose its own fit from."""
    if not rows:
        return float("inf")
    total = 0.0
    for h in rows:
        floor_c, mu, sigma = predictor(h, sd_ratio)
        total += crps_floored_gaussian(floor_c, mu, sigma, h.final_max_c)
    return total / len(rows)


# ---------------------------------------------------------------------------
# The width correction.
# ---------------------------------------------------------------------------
def measured_sd_ratio(rows):
    """The factor that makes the trajectory's stated width match its errors.

    climb_left_sd_c is CLIMATOLOGICAL - the spread of "how much was left" over
    89 days at this hour, whatever the weather was doing. Conditioned on
    today's reading it can be too wide or too narrow, and this is the one
    number that says which. Same construction as the ratio in
    forecast_postprocess: the root mean square residual over the stated width.
    """
    num = den = 0.0
    for h in rows:
        stated = max(SD_FLOOR_C, h.climb_sd_c)
        resid = h.final_max_c - (h.temp_c + h.climb_left_c)
        num += resid * resid
        den += stated * stated
    if den <= 0:
        return 1.0
    return math.sqrt(num / den)


def fit_cell(city_key, local_hour, rows, min_days=MIN_DAYS, folds=N_FOLDS):
    """Decide whether the trajectory beats the floored forecast at this hour."""
    rows = sorted(rows, key=lambda h: h.local_date)
    n = len(rows)
    base = dict(city_key=city_key, local_hour=local_hour, n_days=n)
    if n < min_days:
        return Fit(**base, sd_ratio=1.0, crps_trajectory=None, crps_forecast=None,
                   crps_gain=None, applied=False,
                   reason=f"{n} settled day(s) at this hour; needs {min_days}")

    ratio = shrink(measured_sd_ratio(rows), n, K_SD, 1.0)
    ratio = max(SD_RATIO_FLOOR, min(SD_RATIO_CEILING, ratio))

    traj_total = fc_total = 0.0
    scored = 0
    for start, stop in blocked_folds(n, folds):
        test, train = rows[start:stop], rows[:start] + rows[stop:]
        if not test or not train:
            continue
        r = max(SD_RATIO_FLOOR, min(SD_RATIO_CEILING,
                shrink(measured_sd_ratio(train), len(train), K_SD, 1.0)))
        traj_total += score(test, trajectory_predictor, r) * len(test)
        fc_total += score(test, forecast_predictor) * len(test)
        scored += len(test)
    if not scored:
        return Fit(**base, sd_ratio=ratio, crps_trajectory=None, crps_forecast=None,
                   crps_gain=None, applied=False,
                   reason="held-out score could not be computed")

    traj, fc = traj_total / scored, fc_total / scored
    gain = fc - traj
    applied = gain > 0
    verdict = "better than the floored forecast" if applied else "no gain, stays in shadow"
    return Fit(**base, sd_ratio=round(ratio, 4),
               crps_trajectory=round(traj, 5), crps_forecast=round(fc, 5),
               crps_gain=round(gain, 5), applied=applied,
               reason=(f"held-out CRPS {traj:.4f} against the floored forecast's "
                       f"{fc:.4f} over {scored} day(s): {gain:+.4f} C - {verdict}"))


# ---------------------------------------------------------------------------
# Evidence.
# ---------------------------------------------------------------------------
def load_evidence(lookback_days=120):
    """{(city_key, local_hour): [Hour, ...]} from v_trajectory_evidence.

    A row needs all of: a reading, a running maximum, a settled answer, a
    climb profile for that city-hour, and the distribution the desk published
    that day. Anything missing one of those cannot compare the two predictors
    and is dropped rather than defaulted.
    """
    from common import rest_all
    import datetime as dt
    since = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    rows = rest_all("v_trajectory_evidence", [
        ("select", "city_key,local_date,local_hour,temp_c,running_max_c,"
                   "final_max_c,final_is_verified,climb_left_c,climb_sd_c,"
                   "climb_n_days,forecast_c,forecast_sigma_c"),
        ("local_date", f"gte.{since}"),
    ], order="city_key.asc,local_date.asc,local_hour.asc", page_size=1000)

    by_cell, verified = defaultdict(list), 0
    for r in rows:
        try:
            h = Hour(local_date=str(r["local_date"]), local_hour=int(r["local_hour"]),
                     temp_c=float(r["temp_c"]), running_max_c=float(r["running_max_c"]),
                     climb_left_c=float(r["climb_left_c"]), climb_sd_c=float(r["climb_sd_c"]),
                     final_max_c=float(r["final_max_c"]),
                     forecast_c=float(r["forecast_c"]),
                     forecast_sigma_c=float(r["forecast_sigma_c"]))
        except (TypeError, ValueError, KeyError):
            continue
        # A RUNNING MAXIMUM ABOVE THE FINAL ONE IS NOT EVIDENCE, it is a
        # station or a timezone mismatch, and fitting on it would teach the
        # layer to distrust a floor that is usually right.
        if h.running_max_c > h.final_max_c + 1e-9:
            continue
        by_cell[(r["city_key"], h.local_hour)].append(h)
        verified += 1 if r.get("final_is_verified") else 0
    return by_cell, len(rows), verified


def fit_all(by_cell, min_days=MIN_DAYS, folds=N_FOLDS):
    out = []
    for (city, hour), rows in sorted(by_cell.items()):
        f = fit_cell(city, hour, rows, min_days, folds)
        out.append({
            "city_key": f.city_key, "local_hour": f.local_hour,
            "n_days": f.n_days,
            "climb_n_days": None,
            "sd_ratio": f.sd_ratio,
            "crps_trajectory": f.crps_trajectory,
            "crps_forecast": f.crps_forecast,
            "crps_gain": f.crps_gain,
            "applied": f.applied, "reason": f.reason,
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="Fit the intraday trajectory layer")
    ap.add_argument("--lookback", type=int, default=120)
    ap.add_argument("--min-days", type=int, default=MIN_DAYS)
    ap.add_argument("--folds", type=int, default=N_FOLDS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from common import upsert, log_run

    by_cell, n_rows, n_verified = load_evidence(args.lookback)
    if not by_cell:
        print("no trajectory evidence - nothing to fit", file=sys.stderr)
        log_run("trajectory", "ok", 0, "no evidence")
        return 0
    print(f"{n_rows} evidence row(s), {n_verified} against a verified station "
          f"maximum, over {len(by_cell)} city-hour cell(s)")

    rows = fit_all(by_cell, args.min_days, args.folds)
    applied = [r for r in rows if r["applied"]]
    print(f"{len(rows)} cell(s), {len(applied)} applied, "
          f"{len(rows) - len(applied)} shadow")
    if applied:
        gains = sorted(r["crps_gain"] for r in applied)
        print(f"  CRPS gain over the floored forecast: "
              f"min {gains[0]:+.4f}  median {gains[len(gains)//2]:+.4f}  "
              f"max {gains[-1]:+.4f} C")
        by_hour = defaultdict(int)
        for r in applied:
            by_hour[r["local_hour"]] += 1
        print("  applied by local hour: " +
              ", ".join(f"{h:02d}h x{n}" for h, n in sorted(by_hour.items())))
    if args.dry_run:
        for r in sorted(rows, key=lambda r: -(r["crps_gain"] or -99))[:20]:
            print(f"  {r['city_key']:<16} {r['local_hour']:02d}h  "
                  f"n={r['n_days']:<3} ratio {r['sd_ratio']:.2f}  "
                  f"{'APPLIED' if r['applied'] else 'shadow '}  {r['reason']}")
        return 0

    upsert("derived_trajectory", rows, on_conflict="city_key,local_hour")
    log_run("trajectory", "ok", len(rows),
            f"{len(applied)} of {len(rows)} city-hours applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
