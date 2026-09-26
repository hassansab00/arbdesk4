"""The remaining-day model, stage 1 (plan v2 P7.2): the distribution of a
city's FINAL daily maximum at a decision hour, from what the day has done so far
and what the forecast said about the rest of it.

WHAT IT CAN KNOW. At local hour H (the city's wall clock):
  * the station's readings at or before H:00 - running max R, the latest
    reading, the 1-h and 3-h change;
  * a forecast run issued before the day began, hourly (Open-Meteo best_match
    `_previous_day1`; P2.9's record) - the forecast at H, its maximum over the
    hours after H and over the whole day, its error so far (reading minus
    forecast, now and over the last 3 h), cloud and radiation to 17:00;
  * the spread of the seven models' day-ahead maxima (P2.8);
  * the season.
Nothing later: `features` reads only readings at or before H (a test proves it).

THE DISTRIBUTION is an equal mix of two forms, fitted per decision hour,
pooled across cities with each city's effect shrunk to the pool:
  A  "the maximum is already set" (a logistic: the rest of the day adds less
     than SET_RISE_C), else a lognormal rise above R;
  B  M = max(R, X), X ~ Normal(R + expected rise, s): the floor atom around a
     ridge's expected rise.
Both scales depend on the day (a ridge on the absolute residual), because the
plan requires a sharp day to be priced sharper than an uncertain one.

WHY THIS FORM (measured 26 Sep, `tools/experiments_p72_stage1.py`, walk-forward
by month Nov 2025 - Sep 2026, 48 cities, ~15,700 city-days per hour, the
station maximum of whole days as the label; bucket log loss on the whole degree
in the city's unit):
  hour  floor-atom Gaussian on floor+forecast   A      B      A+B (this)
  09    2.048                                   1.924  1.805  1.801
  11    1.945                                   1.669  1.644  1.616
  13    1.720                                   1.290  1.298  1.257
  15    1.604                                   0.700  0.741  0.684
The mix was chosen over A and B on those same months (four forms tried), so
its advantage over the better single form there is not a confirmed result; the
P7.3 replay against the venue and the shadow days after it are.

ITS WIDTH IS CALIBRATED. Uncalibrated, the 80% interval covered 75% at 11h and
71% at 13h (too little mass near R at midday). Both scales are now multiplied
by a factor chosen on each training window's last fifth (choose_widen): the
same walk-forward then covered 81-84% in every tercile of model disagreement at
every hour, for bucket log loss 1.804/1.627/1.269/0.678 (+0.003, +0.011,
+0.014, -0.002).

RULE 11. Every learned parameter has a prior (a city's coefficients shrink to
the pooled fit; the pool to zero; the width factor to 1.0), bounds (BOUNDS,
WIDEN_GRID), a minimum sample (MIN_TRAIN_ROWS for an hour, MIN_CITY_ROWS before
a city leaves the pool, MIN_INNER_ROWS before the width moves), and a version
on every output. A refit is weekly (plan P7.2); the nightly bounded
update of city effects and the recent-bias term is not built yet.

Pure Python (the scheduled jobs install no numpy).
"""
import datetime as dt
import hashlib
import json
import math

from weather_model import solve

VERSION_PREFIX = "rd1"
FEATURES = ["fc_rest_minus_now", "now_minus_R", "fc_err_now", "fc_err_3h", "slope_1h", "slope_3h",
            "fc_day_minus_R", "cloud_rest", "sw_rest", "doy_sin", "doy_cos", "models_spread"]
HOURS = tuple(range(7, 20))
SET_RISE_C = 0.25
WEIGHT_A = 0.5
LAM_POOL = 10.0
LAM_CITY = 100.0
LAM_LOGIT = 1.0
LOGIT_ITERS = 50
LOGIT_TOL = 1e-8
MIN_TRAIN_ROWS = 2000
# THE WIDTH, CALIBRATED (Rule 11: prior 1.0, bounds, chosen on training days
# only). Both scales are multiplied by the smallest WIDEN_GRID value whose 80%
# interval covers at least TARGET_COVER of the last INNER_SHARE of the training
# days, fitted on the days before them. Measured 26 Sep before it: 80%
# intervals covered 75% at 11h and 71% at 13h.
WIDEN_GRID = (1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.4, 1.5, 1.6)
TARGET_COVER = 0.80
INNER_SHARE = 0.2
MIN_INNER_ROWS = 400
MIN_CITY_ROWS = 60
MIN_READINGS = 3
MIN_REST_HOURS = 6
MIN_FORECAST_HOURS = 20
DEFAULT_CLOUD = 50.0
BOUNDS = {
    "log_rise": (-3.0, 3.5),     # exp: 0.05 C to 33 C
    "scale_a": (0.15, 2.0),      # of the log rise
    "rise_b": (-5.0, 20.0),      # C
    "scale_b": (0.3, 5.0),       # C
}
SQRT_HALF_PI = math.sqrt(math.pi / 2)   # E|N(0, s)| = s / SQRT_HALF_PI


# ---------------------------------------------------------------------------
# features (pure)
# ---------------------------------------------------------------------------
def nearest(series, h, tol):
    """The reading closest to local hour h within tol hours, or None; the
    earlier reading wins a tie. series is sorted by hour."""
    best = None
    for x, v in series:
        if abs(x - h) <= tol and (best is None or abs(x - h) < abs(best[0] - h)):
            best = (x, v)
    return best[1] if best else None


def features(readings, forecast, hour, day, spread):
    """The feature row for one city-day at local hour `hour`, or None.

    readings: [(local hour as a float, temp C)] of that local day, any order;
              only those at or before `hour` are read.
    forecast: {local hour: (temp C, cloud %, shortwave W/m2)} from a run issued
              before the day.
    spread:   population sd of the models' day-ahead maxima, C.
    """
    seen = sorted(p for p in readings if p[0] <= hour)
    if len(seen) < MIN_READINGS or len(forecast) < MIN_FORECAST_HOURS or hour not in forecast:
        return None
    R = max(v for _, v in seen)
    now = nearest(seen, hour, 1.5)
    h1, h3 = nearest(seen, hour - 1, 0.75), nearest(seen, hour - 3, 0.75)
    if now is None or h1 is None or h3 is None:
        return None
    rest = [forecast[h][0] for h in range(hour + 1, 24) if h in forecast]
    if len(rest) < MIN_REST_HOURS:
        return None
    errs = []
    for h in (hour - 2, hour - 1, hour):
        if h in forecast:
            o = nearest(seen, h, 0.5)
            if o is not None:
                errs.append(o - forecast[h][0])
    cloud = [forecast[h][1] for h in range(hour + 1, 18) if h in forecast and forecast[h][1] is not None]
    sw = [forecast[h][2] for h in range(hour + 1, 18) if h in forecast and forecast[h][2] is not None]
    doy = 2 * math.pi * day.timetuple().tm_yday / 365.25
    fc_day = max(v[0] for v in forecast.values())
    x = [max(rest) - now, now - R, now - forecast[hour][0], sum(errs) / len(errs) if errs else 0.0,
         now - h1, now - h3, fc_day - R, sum(cloud) / len(cloud) if cloud else DEFAULT_CLOUD,
         sum(sw) if sw else 0.0, math.sin(doy), math.cos(doy), float(spread)]
    return {"R": R, "now": now, "fc_day": fc_day, "x": x}


# ---------------------------------------------------------------------------
# the fit (pure Python)
# ---------------------------------------------------------------------------
def _standardiser(xs):
    n, p = len(xs), len(xs[0])
    mu = [sum(x[j] for x in xs) / n for j in range(p)]
    sd = [math.sqrt(sum((x[j] - mu[j]) ** 2 for x in xs) / n) or 1.0 for j in range(p)]
    return mu, sd


def _z(x, mu, sd):
    return [1.0] + [(x[j] - mu[j]) / sd[j] for j in range(len(mu))]


def _gram(zs, ys, ws=None):
    p = len(zs[0])
    A = [[0.0] * p for _ in range(p)]
    b = [0.0] * p
    for k, z in enumerate(zs):
        w = 1.0 if ws is None else ws[k]
        for i in range(p):
            wzi = w * z[i]
            if wzi == 0.0:
                continue
            Ai = A[i]
            for j in range(i, p):
                Ai[j] += wzi * z[j]
            b[i] += (ys[k] * z[i]) if ws is None else (ys[k] * wzi)
    for i in range(p):
        for j in range(i):
            A[i][j] = A[j][i]
    return A, b


def _ridge(A, b, lam, prior=None):
    """(A + lam I') beta = b + lam I' prior, the intercept unpenalised."""
    p = len(b)
    prior = prior or [0.0] * p
    M = [[A[i][j] + (lam if i == j and i > 0 else 0.0) for j in range(p)] for i in range(p)]
    rhs = [b[i] + (lam * prior[i] if i > 0 else 0.0) for i in range(p)]
    return solve(M, rhs) or list(prior)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _city_ridge(zs, ys, cities):
    """(pooled, {city: shrunk to pooled}); a city under MIN_CITY_ROWS keeps the pool."""
    pooled = _ridge(*_gram(zs, ys), LAM_POOL)
    by = {}
    for k, c in enumerate(cities):
        by.setdefault(c, []).append(k)
    out = {}
    for c, ks in by.items():
        if len(ks) >= MIN_CITY_ROWS:
            out[c] = _ridge(*_gram([zs[k] for k in ks], [ys[k] for k in ks]), LAM_CITY, pooled)
    return pooled, out


def _logistic(zs, ys):
    """Ridge-penalised logistic regression by Newton steps."""
    p = len(zs[0])
    beta = [0.0] * p
    for _ in range(LOGIT_ITERS):
        ps = [1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, _dot(z, beta))))) for z in zs]
        g = [0.0] * p
        for z, pr, y in zip(zs, ps, ys):
            d = pr - y
            for i in range(p):
                g[i] += d * z[i]
        for i in range(1, p):
            g[i] += LAM_LOGIT * beta[i]
        H, _ = _gram(zs, [0.0] * len(zs), [pr * (1 - pr) for pr in ps])
        for i in range(1, p):
            H[i][i] += LAM_LOGIT
        step = solve(H, g)
        if step is None:
            break
        beta = [b - s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < LOGIT_TOL:
            break
    return beta


def _coef(pool, cities, city):
    return cities.get(city, pool)


def fit_hour(rows):
    """One decision hour. rows: dicts with city, x (FEATURES), R, y (the day's
    maximum), date. Returns the parameters, or None under MIN_TRAIN_ROWS."""
    if len(rows) < MIN_TRAIN_ROWS:
        return None
    p = _fit_core(rows)
    p["widen"], p["widen_cover"], p["widen_n"] = choose_widen(rows)
    return p


def choose_widen(rows):
    """(factor, inner 80% coverage at it, inner n): the smallest WIDEN_GRID
    factor whose 80% interval covers TARGET_COVER of the last INNER_SHARE of
    the training days, fitted on the days before them. 1.0 when the inner
    split is too small to say (Rule 11: no move off the prior)."""
    days = sorted({r["date"] for r in rows})
    cut = days[int(len(days) * (1 - INNER_SHARE))] if days else None
    early = [r for r in rows if r["date"] < cut]
    late = [r for r in rows if r["date"] >= cut]
    if len(late) < MIN_INNER_ROWS or len(early) < MIN_TRAIN_ROWS:
        return 1.0, None, len(late)
    p = _fit_core(early)
    dists = [distribution(dict(p, widen=1.0), r["city"], r["x"], r["R"]) for r in late]
    cover = None
    for k in WIDEN_GRID:
        hits = 0
        for d, r in zip(dists, late):
            dk = dict(d, scale_a=d["scale_a"] * k, scale_b=d["scale_b"] * k)
            hits += quantile(dk, 0.1) <= r["y"] <= quantile(dk, 0.9)
        cover = hits / len(late)
        if cover >= TARGET_COVER:
            return k, round(cover, 4), len(late)
    return WIDEN_GRID[-1], round(cover, 4), len(late)


def _fit_core(rows):
    mu, sd = _standardiser([r["x"] for r in rows])
    zs = [_z(r["x"], mu, sd) for r in rows]
    cities = [r["city"] for r in rows]
    rise = [r["y"] - r["R"] for r in rows]
    logit = _logistic(zs, [1.0 if v < SET_RISE_C else 0.0 for v in rise])
    pos = [k for k, v in enumerate(rise) if v >= SET_RISE_C]
    zp, cp = [zs[k] for k in pos], [cities[k] for k in pos]
    lr = [math.log(rise[k]) for k in pos]
    a_pool, a_city = _city_ridge(zp, lr, cp)
    a_res = [abs(lr[k] - _dot(zp[k], _coef(a_pool, a_city, cp[k]))) for k in range(len(pos))]
    a_scale = _ridge(*_gram(zp, a_res), LAM_POOL)
    b_pool, b_city = _city_ridge(zs, rise, cities)
    b_res = [abs(rise[k] - _dot(zs[k], _coef(b_pool, b_city, cities[k]))) for k in range(len(rows))]
    b_scale = _ridge(*_gram(zs, b_res), LAM_POOL)
    return {"mu": mu, "sd": sd, "logit": logit, "a_pool": a_pool, "a_city": a_city, "a_scale": a_scale,
            "b_pool": b_pool, "b_city": b_city, "b_scale": b_scale, "n": len(rows),
            "n_set": len(rows) - len(pos), "cities": sorted(set(cities)),
            "first_date": min(r["date"] for r in rows).isoformat(),
            "last_date": max(r["date"] for r in rows).isoformat()}


def fit(rows_by_hour):
    """{hour: parameters} and the version naming them."""
    params = {h: p for h, p in ((h, fit_hour(rs)) for h, rs in sorted(rows_by_hour.items())) if p}
    return params, version_of(params)


def version_of(params):
    blob = json.dumps({str(h): p for h, p in sorted(params.items())}, sort_keys=True, default=str)
    last = max((p["last_date"] for p in params.values()), default="none")
    return f"{VERSION_PREFIX}:{last}:{hashlib.sha256(blob.encode()).hexdigest()[:10]}"


# ---------------------------------------------------------------------------
# the distribution (pure)
# ---------------------------------------------------------------------------
def _clip(v, name):
    lo, hi = BOUNDS[name]
    return max(lo, min(hi, v))


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def distribution(p, city, x, R):
    """The parameters of the final-maximum distribution for one city-day."""
    z = _z(x, p["mu"], p["sd"])
    s = _dot(z, p["logit"])
    return {"R": R,
            "p_set": 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, s)))),
            "log_rise": _clip(_dot(z, _coef(p["a_pool"], p["a_city"], city)), "log_rise"),
            "scale_a": _clip(_dot(z, p["a_scale"]) * SQRT_HALF_PI * p.get("widen", 1.0), "scale_a"),
            "centre": R + _clip(_dot(z, _coef(p["b_pool"], p["b_city"], city)), "rise_b"),
            "scale_b": _clip(_dot(z, p["b_scale"]) * SQRT_HALF_PI * p.get("widen", 1.0), "scale_b")}


def cdf(d, v):
    """P(final maximum <= v), C. Zero below R: a maximum cannot go down."""
    R = d["R"]
    if v < R:
        return 0.0
    if v > R:
        u = min(1.0, (v - R) / SET_RISE_C)
        rise = _phi((math.log(v - R) - d["log_rise"]) / d["scale_a"])
    else:
        u, rise = 0.0, 0.0
    a = d["p_set"] * u + (1.0 - d["p_set"]) * rise
    b = _phi((v - d["centre"]) / d["scale_b"])
    return WEIGHT_A * a + (1.0 - WEIGHT_A) * b


def quantile(d, q, lo=-60.0, hi=70.0):
    """The value v with P(final maximum <= v) = q, C (R when the atom covers q)."""
    lo = max(lo, d["R"])
    if cdf(d, lo) >= q:
        return lo
    for _ in range(50):
        mid = (lo + hi) / 2
        if cdf(d, mid) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def median(d):
    """The predicted maximum (the distribution's median), C."""
    return quantile(d, 0.5)


def ladder_probabilities(d, unit, bands, q_down=0.0, q_up=0.0):
    """[(band_id, prob)] over a venue ladder, summing to 1.

    The same construction as probability_engine.compute_band_probabilities
    (plan P3.1): the bucket holding R, read the venue's way, takes every
    draw at or below its upper edge; buckets below it take nothing; then the
    measurement layer moves q_down / q_up of that atom one bucket down / up.
    """
    import probability_engine as pe

    def F(edge):
        return cdf(d, pe.unit_edge_c(unit, edge))

    raw = {}
    for b in bands:
        if b.get("open_low"):
            raw[b["band_id"]] = F(b["band_hi"])
        elif b.get("open_high"):
            raw[b["band_id"]] = 1.0 - F(b["band_lo"])
        else:
            raw[b["band_id"]] = max(0.0, F(b["band_hi"]) - F(b["band_lo"]))
    masses = raw
    ladder, i = pe.floor_bucket(d["R"], unit, bands)
    if i is not None:
        b_r = ladder[i]
        atom = 1.0 if b_r.get("open_high") else F(b_r["band_hi"])
        masses = {b["band_id"]: 0.0 if j < i else (atom if j == i else raw[b["band_id"]])
                  for j, b in enumerate(ladder)}
        q_down = min(max(float(q_down or 0.0), 0.0), 0.5)
        q_up = min(max(float(q_up or 0.0), 0.0), 0.5)
        if i > 0 and q_down > 0:
            masses[ladder[i - 1]["band_id"]] += q_down * atom
            masses[b_r["band_id"]] -= q_down * atom
        if i < len(ladder) - 1 and q_up > 0:
            masses[ladder[i + 1]["band_id"]] += q_up * atom
            masses[b_r["band_id"]] -= q_up * atom
    total = sum(masses.values())
    if total <= 0:
        return [(b["band_id"], 1.0 / len(bands)) for b in bands]
    return [(b["band_id"], masses[b["band_id"]] / total) for b in bands]


def whole_bucket_probability(d, unit, k):
    """P(the maximum reads k whole degrees in `unit`) - the experiment's score."""
    lo, hi = (k - 0.5, k + 0.5) if unit != "F" else ((k - 0.5 - 32) * 5 / 9, (k + 0.5 - 32) * 5 / 9)
    return cdf(d, hi) - cdf(d, lo)


def to_json(params, version):
    return json.dumps({"version": version, "hours": {str(h): p for h, p in sorted(params.items())},
                       "features": FEATURES, "weight_a": WEIGHT_A, "bounds": BOUNDS,
                       "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
                      sort_keys=True)
