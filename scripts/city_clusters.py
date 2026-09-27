"""Clusters and correlated exposure (plan v2 P5.9, part 2).

Two city-days are not two bets when their forecasts miss together. This fits how
the day-ahead forecast errors of every pair of cities move together, and turns
that into room on the solver's budget:

  cluster rail   what one cluster holds on one date stays within the fixed 8%
                 (risk_rails cluster_day_frac); a cluster is the cities joined
                 by a shrunk correlation of at least CLUSTER_RHO
  correlated     own exposure plus rho-weighted exposure on every other city
                 that day stays within the same 8%, so a city's cap shrinks as
                 its correlation with what is already held rises; at rho = 1 it
                 IS the cluster rail, at rho = 0 only the 3% city-day rail binds

THE RESIDUALS. The honest record's day-ahead best_match maximum (P2.9,
data/training/previous_runs, lead 1) minus the station's whole local day
(derived_city_day_features.max_c), less each city's own mean error over its
previous BIAS_WINDOW days. The pricing engine removes a trailing station bias
(P3.9), so the part that can hit two books at once is what is left. Measured 27
Sep on 386 dates x 48 cities (2025-07 to 2026-09): without that step jeddah and
shenzhen correlate 0.41 on a shared drift; with it the strongest pairs are
austin-houston 0.32, qingdao-zhengzhou 0.31 and amsterdam-london 0.28.

NOT derived_city_correlation. That table correlates weather_forecasts against
UTC-day observation maxima, and counts every forecast row as a day: on 27 Sep
its newest fit had n_days up to 2,559, while the rows it reads by default (lead
1, last 180 days) cover 62 dates, with up to 14 rows per city-day.

LEDOIT-WOLF. The sample correlation of 48 cities on a few hundred days is mostly
noise. The shrinkage is Ledoit and Wolf's (2004) toward the constant-correlation
target, with their estimated intensity. Measured 27 Sep, fitted on the first
half of the dates and scored on the second half's sample correlation: RMSE
0.117 sample, 0.094 shrunk.

RULE 11. Each pair's rho has
  - a prior:          RHO_PRIOR, the platform's rho for an unmeasured pair
                      (risk_budget.DEFAULT_CORRELATION, 0.30: "a missing pair is
                      not an uncorrelated pair");
  - hard bounds:      [0, 1] (a negative correlation is not a licence);
  - a minimum sample: a city is measured only with MIN_DATES complete dates;
                      otherwise all its pairs are the prior;
  - a maximum step:   a refit moves a pair at most MAX_STEP from the previous
                      version (the first from the prior);
  - a version:        fit() stamps one; every decision records it.
fit() reads only dates strictly before `as_of`. The window, bias window, minimum
sample, step and cluster threshold are this module's priors, recorded in every
fitted table. The refit is weekly (REFIT_DAYS), from the nightly loop.
"""
import datetime as dt
import hashlib
import json
import math
import sys

from risk_budget import DEFAULT_CORRELATION

LEAD = 1
WINDOW_DAYS = 365
BIAS_WINDOW = 30
MIN_DATES = 90
RHO_PRIOR = DEFAULT_CORRELATION
RHO_BOUNDS = (0.0, 1.0)
MAX_STEP = 0.10
CLUSTER_RHO = 0.5
REFIT_DAYS = 7
PRIOR_VERSION = "prior"


def _pair(a, b):
    return f"{a}|{b}" if a < b else f"{b}|{a}"


# --------------------------------------------------------------------------
# the residuals
# --------------------------------------------------------------------------

def residuals(record_rows, labels, lead=LEAD):
    """{city: {date: forecast - observed}} from honest_record rows and labels."""
    out = {}
    for r in record_rows:
        city, ld, ds, tmax = r[0], r[1], r[2], r[3]
        if str(ld) != str(lead) or tmax in (None, ""):
            continue
        y = labels.get((city, ds))
        if y is None:
            continue
        out.setdefault(city, {})[ds] = float(tmax) - float(y)
    return out


def detrended(series, window=BIAS_WINDOW):
    """Each error less the mean of the city's previous `window` errors; a date
    without that many before it has none."""
    out = {}
    for city, by_date in series.items():
        ds = sorted(by_date)
        run, out[city] = [], {}
        for d in ds:
            if len(run) >= window:
                out[city][d] = by_date[d] - sum(run[-window:]) / window
            run.append(by_date[d])
    return out


def matrix(series, as_of, window_days=WINDOW_DAYS, min_dates=MIN_DATES):
    """(cities, dates, rows): the cities with an error on at least `min_dates`
    of the window's dates, and the dates on which every one of them has one."""
    lo = str(as_of - dt.timedelta(days=window_days))
    hi = str(as_of)
    inside = {c: {d: v for d, v in s.items() if lo <= d < hi} for c, s in series.items()}
    cities = sorted(c for c, s in inside.items() if len(s) >= min_dates)
    while cities:
        dates = sorted(set.intersection(*(set(inside[c]) for c in cities)))
        if len(dates) >= min_dates:
            return cities, dates, [[inside[c][d] for c in cities] for d in dates]
        # drop the city that costs the most dates, and try again
        cities.remove(min(cities, key=lambda c: (len(inside[c]), c)))
    return [], [], []


# --------------------------------------------------------------------------
# Ledoit-Wolf, constant-correlation target (pure Python: 48 x 48 is small)
# --------------------------------------------------------------------------

def ledoit_wolf(rows):
    """(R, shrinkage, rbar): the shrunk correlation matrix of the columns."""
    t, n = len(rows), len(rows[0])
    mu = [sum(r[j] for r in rows) / t for j in range(n)]
    x = [[r[j] - mu[j] for j in range(n)] for r in rows]
    cols = list(zip(*x))
    S = [[sum(a * b for a, b in zip(cols[i], cols[j])) / t for j in range(n)] for i in range(n)]
    var = [S[i][i] for i in range(n)]
    sd = [math.sqrt(v) for v in var]
    rbar = (sum(S[i][j] / (sd[i] * sd[j]) for i in range(n) for j in range(n)) - n) / (n * (n - 1))
    F = [[var[i] if i == j else rbar * sd[i] * sd[j] for j in range(n)] for i in range(n)]
    sq = [[v * v for v in c] for c in cols]
    cube = [[v ** 3 for v in c] for c in cols]
    phi_mat = [[sum(a * b for a, b in zip(sq[i], sq[j])) / t - S[i][j] ** 2 for j in range(n)]
               for i in range(n)]
    phi = sum(map(sum, phi_mat))
    theta = [[0.0 if i == j else
              sum(a * b for a, b in zip(cube[i], cols[j])) / t - var[i] * S[i][j]
              for j in range(n)] for i in range(n)]
    rho = sum(phi_mat[i][i] for i in range(n)) + rbar * sum(
        sd[j] / sd[i] * theta[i][j] for i in range(n) for j in range(n))
    gamma = sum((S[i][j] - F[i][j]) ** 2 for i in range(n) for j in range(n))
    shrink = max(0.0, min(1.0, (phi - rho) / gamma / t)) if gamma > 0 else 1.0
    sig = [[shrink * F[i][j] + (1 - shrink) * S[i][j] for j in range(n)] for i in range(n)]
    d = [math.sqrt(sig[i][i]) for i in range(n)]
    return [[sig[i][j] / (d[i] * d[j]) for j in range(n)] for i in range(n)], shrink, rbar


def clusters(cities, rho, threshold=CLUSTER_RHO):
    """{city: cluster id}: connected components of the pairs at or above the
    threshold; the id is the component's first city."""
    parent = {c: c for c in cities}

    def root(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c
    for key, v in rho.items():
        a, b = key.split("|")
        if v >= threshold and a in parent and b in parent:
            ra, rb = root(a), root(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    return {c: root(c) for c in cities}


# --------------------------------------------------------------------------
# the fit
# --------------------------------------------------------------------------

def fit(record_rows, labels, as_of, previous=None, all_cities=None):
    """A fitted table from residuals on dates strictly before `as_of`."""
    series = detrended(residuals(record_rows, labels))
    cities, dates, rows = matrix(series, as_of)
    prev = (previous or {}).get("rho") or {}
    known = sorted(set(all_cities or []) | set(series) | {c for k in prev for c in k.split("|")})
    measured = {}
    shrink = rbar = None
    if cities:
        R, shrink, rbar = ledoit_wolf(rows)
        idx = {c: i for i, c in enumerate(cities)}
        measured = {_pair(a, b): R[idx[a]][idx[b]] for a in cities for b in cities if a < b}
    lo, hi = RHO_BOUNDS
    rho, moved = {}, 0
    for i, a in enumerate(known):
        for b in known[i + 1:]:
            key = _pair(a, b)
            start = float(prev.get(key, RHO_PRIOR))
            target = min(max(measured.get(key, RHO_PRIOR), lo), hi)
            step = max(-MAX_STEP, min(MAX_STEP, target - start))
            rho[key] = round(min(max(start + step, lo), hi), 4)
            moved += abs(step) > 1e-12
    body = {"as_of": str(as_of), "lead": LEAD, "window_days": WINDOW_DAYS, "bias_window": BIAS_WINDOW,
            "min_dates": MIN_DATES, "rho_prior": RHO_PRIOR, "bounds": list(RHO_BOUNDS), "max_step": MAX_STEP,
            "cluster_rho": CLUSTER_RHO, "previous": (previous or {}).get("version"),
            "measured_cities": cities, "n_dates": len(dates),
            "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
            "shrinkage": None if shrink is None else round(shrink, 4),
            "rbar": None if rbar is None else round(rbar, 4),
            "pairs_moved": moved, "rho": rho, "cluster": clusters(known, rho)}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    body["version"] = f"city-clusters:{as_of}:{digest[:10]}"
    return body


def due(previous, as_of):
    """A refit is due REFIT_DAYS after the last one."""
    if not previous or not previous.get("as_of"):
        return True
    return (as_of - dt.date.fromisoformat(previous["as_of"])).days >= REFIT_DAYS


# --------------------------------------------------------------------------
# what the engine reads
# --------------------------------------------------------------------------

def rho_of(table, a, b):
    if a == b:
        return 1.0
    if a is None or b is None:
        return RHO_PRIOR
    v = ((table or {}).get("rho") or {}).get(_pair(a, b))
    return RHO_PRIOR if v is None else float(v)


def cluster_of(table, city):
    return ((table or {}).get("cluster") or {}).get(city, city)


def version_of(table):
    return (table or {}).get("version", PRIOR_VERSION)


def room(rails, table, city, equity_usd, on_city_usd, same_day):
    """(room as a fraction of equity, the constraint that sets it) for NEW
    spend on `city`, given `same_day` {other city: usd held + reserved on the
    same date}. None when neither binds before the city-day rail would."""
    if equity_usd <= 0:
        return 0.0, "cluster_day"
    cap = rails["cluster_day_frac"] * equity_usd
    others = {c: max(float(v), 0.0) for c, v in (same_day or {}).items() if c != city}
    own = max(float(on_city_usd), 0.0)
    home = cluster_of(table, city)
    in_cluster = own + sum(v for c, v in others.items() if cluster_of(table, c) == home)
    correlated = own + sum(rho_of(table, city, c) * v for c, v in others.items())
    by = {"cluster_day": cap - in_cluster, "correlated": cap - correlated}
    name = min(by, key=lambda k: (by[k], k))
    return max(by[name], 0.0) / equity_usd, name


def load(rest=None):
    """The latest fitted table, or None (every pair the prior) while the
    nightly loop's values are not in use (settings.strategy_learning)."""
    if rest is None:
        from common import rest as rest
    import learned
    if not learned.enabled(rest):
        return None
    try:
        rows = rest("strategy_params", [("select", "value,version"), ("param", "eq.city_clusters"),
                                        ("order", "fitted_at.desc"), ("limit", "1")])
    except Exception as e:
        print(f"  note: no city clusters ({e}); every pair at the prior {RHO_PRIOR}", file=sys.stderr)
        return None
    if not rows:
        return None
    table = rows[0].get("value") or {}
    table.setdefault("version", rows[0].get("version") or PRIOR_VERSION)
    return table
