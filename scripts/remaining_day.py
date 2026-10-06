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
# From 18:00 fewer than MIN_REST_HOURS forecast hours are left in the day, so
# no row can be built (the replay of 26 Sep found hour 18 unfittable).
# HOURS stays the evaluation harnesses' hours (fec_same_day, the checkpoint
# replay), which reproduce committed results byte for byte.
HOURS = tuple(range(7, 18))
# THE LATE HOURS rd1 SERVES (the P.5 report, question 3; Hassan, 6 Oct: the
# strategies decide before and during EACH city's own peak). A checkpoint sits
# where the city's peak puts it, and Madrid's post-peak falls at 18:xx local:
# with no fit for hour 18, S10 never decided it (7 of 7 days, 29 Sep - 5 Oct).
# At a late hour the rest of the day is every forecast hour left (_min_rest),
# so a row can be built. Only tools/fit_remaining_day.py --extend and the
# shadow step read this; hours 7-17 of the served fit are left exactly as
# they were (tests/test_remaining_day_late_hours.py).
LATE_HOURS = (18,)
SERVED_HOURS = HOURS + LATE_HOURS
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


def _min_rest(hour):
    """Forecast hours that must remain after `hour` for a row: MIN_REST_HOURS,
    except at a late hour rd1 serves (LATE_HOURS), where it is every hour left
    in the day (hour 18: the five hours 19-23). Unchanged for every other hour,
    so 19:00 and later still give no row."""
    return 23 - int(hour) if int(hour) in LATE_HOURS else MIN_REST_HOURS


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
    if len(rest) < _min_rest(hour):
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
# features at the DECISION TIME, not the whole hour (Challenger A, 30 Sep;
# docs/CHALLENGER_A_PREREG.md). A SEPARATE CONTRACT with its own version
# prefix: parameters fitted on `features` must never read these.
#
# WHY. `features` reads readings at or before the integer local hour, and the
# tick decides at H:36 local (H:06 on a half-hour zone). Measured on the 673
# live S10 decisions of 27-30 Sep: they came a mean 46 min after the hour, 202
# of them (30%) had already received a reading after H:00 that the model
# discarded, and the newest reading received was a median 36.6 min old.
#
# WHAT IS AVAILABLE. A reading counts only if it had been RECEIVED by the
# decision. Live, that is weather_observations.observed_at <= decided_at.
# History has no receipt times, so `available()` takes valid time <= decision
# less a lag; 10 min matches what the live tick received (by reading age at
# decision, 27-30 Sep: under 10 min 7.7% received, 10-20 min 87.9%, older
# 92-100%). A result built that way is an approximate as-of backtest and says so.
# ---------------------------------------------------------------------------
VERSION_PREFIX_AT = "rd2"
FEATURES_AT = FEATURES + ["obs_age_h", "min_frac"]
MAX_OBS_AGE_H = 1.5          # older than this, the latest reading is stale: no row
RECEIPT_LAG_H = 10 / 60      # historical availability, see above


def available(readings, t, lag_h=RECEIPT_LAG_H):
    """Readings (local hour float, temp C) with valid time <= t - lag_h."""
    return sorted(p for p in readings if p[0] <= t - lag_h)


def _fc_at(forecast, x):
    """The hourly forecast linearly interpolated at local hour x, or None."""
    h0 = int(math.floor(x))
    a, b = forecast.get(h0), forecast.get(h0 + 1)
    if a is None:
        return None
    if b is None or x == h0:
        return a[0]
    return a[0] + (b[0] - a[0]) * (x - h0)


def features_at(seen, forecast, t, day, spread):
    """The Challenger A feature row at decision time `t` (local hour as a
    float), or None.

    seen:     the readings AVAILABLE at t, [(local hour float, temp C)] of that
              local day (the caller applies `available()` or, live, receipt
              times); nothing later than t is read here either.
    forecast: {local hour: (temp C, cloud %, shortwave W/m2)}, a run issued
              before the day (unchanged: Challenger B is the freshness test).

    Same twelve inputs as `features`, anchored at the LATEST reading instead of
    the whole hour, plus how old that reading is and where in the hour the
    decision falls. A latest reading older than MAX_OBS_AGE_H gives no row:
    the explicit stale-input fallback is "no prediction", never an old one."""
    seen = sorted(p for p in seen if p[0] <= t)
    if len(seen) < MIN_READINGS or len(forecast) < MIN_FORECAST_HOURS:
        return None
    tl, now = seen[-1]
    age = t - tl
    if age > MAX_OBS_AGE_H:
        return None
    R = max(v for _, v in seen)
    h1, h3 = nearest(seen, tl - 1, 0.75), nearest(seen, tl - 3, 0.75)
    if h1 is None or h3 is None:
        return None
    first_rest = int(math.floor(t)) + 1
    rest = [forecast[h][0] for h in range(first_rest, 24) if h in forecast]
    if len(rest) < MIN_REST_HOURS:
        return None
    f_now = _fc_at(forecast, tl)
    if f_now is None:
        return None
    errs = []
    for x in (tl - 2, tl - 1, tl):
        f = _fc_at(forecast, x)
        o = nearest(seen, x, 0.5)
        if f is not None and o is not None:
            errs.append(o - f)
    cloud = [forecast[h][1] for h in range(first_rest, 18) if h in forecast and forecast[h][1] is not None]
    sw = [forecast[h][2] for h in range(first_rest, 18) if h in forecast and forecast[h][2] is not None]
    doy = 2 * math.pi * day.timetuple().tm_yday / 365.25
    fc_day = max(v[0] for v in forecast.values())
    x = [max(rest) - now, now - R, now - f_now, sum(errs) / len(errs) if errs else 0.0,
         now - h1, now - h3, fc_day - R, sum(cloud) / len(cloud) if cloud else DEFAULT_CLOUD,
         sum(sw) if sw else 0.0, math.sin(doy), math.cos(doy), float(spread),
         age, t - math.floor(t)]
    return {"R": R, "now": now, "fc_day": fc_day, "x": x, "obs_age_h": age, "latest_at": tl}


# ---------------------------------------------------------------------------
# Challenger C: the individual models' day-before maxima, bias-corrected
# (30 Sep; docs/CHALLENGER_C_PREREG.md). A separate contract, `rd3`: rd1's
# features plus three summaries of the models. Rule 11: the bias has a prior
# (0), shrinkage (MODEL_BIAS_PRIOR_DAYS), a minimum sample (MIN_BIAS_DAYS),
# bounds (MODEL_BIAS_BOUND_C), is fitted only on training days, and its table
# is in the version (version_of_c). It moves only when the file is refitted
# (tools/fit_remaining_day.py --challenger rd3), never nightly.
# ---------------------------------------------------------------------------
VERSION_PREFIX_C = "rd3"
FEATURES_C = FEATURES + ["models_rise_c", "models_frac_up", "models_n"]
MIN_MODELS_C = 4
MODEL_BIAS_PRIOR_DAYS = 30.0
MODEL_BIAS_BOUND_C = 4.0
MODEL_RISE_MARGIN_C = 0.5
# Added 30 Sep after the scored runs, a no-op on every one of them: the
# smallest (model, city) sample at any evaluated cutoff was 161 days
# (ecmwf_ifs025 at denver, 1 Jan 2026) and the smallest pooled one 7,837.
MIN_BIAS_DAYS = 30


def fit_model_bias(pairs):
    """{(model, city): bias C} and {model: pooled bias C} from training
    (model, city, forecast max - station max) triples. A city's bias shrinks
    to its model's pooled bias, the pooled bias to 0, each by n / (n + prior)."""
    by_model, by_mc = {}, {}
    for m, c, e in pairs:
        by_model.setdefault(m, []).append(e)
        by_mc.setdefault((m, c), []).append(e)
    clip = lambda v: max(-MODEL_BIAS_BOUND_C, min(MODEL_BIAS_BOUND_C, v))
    pooled = {m: (clip(sum(v) / len(v) * len(v) / (len(v) + MODEL_BIAS_PRIOR_DAYS))
                  if len(v) >= MIN_BIAS_DAYS else 0.0) for m, v in by_model.items()}
    out = {}
    for (m, c), v in by_mc.items():
        if len(v) < MIN_BIAS_DAYS:
            out[(m, c)] = pooled[m]           # too few days: the prior stands
            continue
        w = len(v) / (len(v) + MODEL_BIAS_PRIOR_DAYS)
        out[(m, c)] = clip(w * (sum(v) / len(v)) + (1 - w) * pooled[m])
    return out, pooled


def bias_to_json(bias, pooled):
    """The bias table as JSON-safe dicts: {"model|city": C}, {model: C}."""
    return ({f"{m}|{c}": round(v, 6) for (m, c), v in sorted(bias.items())},
            {m: round(v, 6) for m, v in sorted(pooled.items())})


def bias_from_json(bias_js, pooled_js):
    return ({tuple(k.split("|", 1)): float(v) for k, v in (bias_js or {}).items()},
            {m: float(v) for m, v in (pooled_js or {}).items()})


def version_of_c(params, bias_js, pooled_js):
    """rd3:<last training date>:<hash of the hours AND the bias table>."""
    blob = json.dumps({"hours": {str(h): p for h, p in sorted(params.items())},
                       "bias": bias_js, "pooled": pooled_js}, sort_keys=True, default=str)
    last = max((p["last_date"] for p in params.values()), default="none")
    return f"{VERSION_PREFIX_C}:{last}:{hashlib.sha256(blob.encode()).hexdigest()[:10]}"


def row_c(row, models, city, bias, pooled):
    """rd1's feature row with the models' three summaries appended, or None
    with fewer than MIN_MODELS_C models (then rd3 says nothing)."""
    extra = models_features(models, row["R"], city, bias, pooled)
    if extra is None:
        return None
    return dict(row, x=list(row["x"]) + extra)


def models_features(models, R, city, bias, pooled):
    """[models_rise_c, models_frac_up, models_n] from {model: day max C}, or
    None with fewer than MIN_MODELS_C models (the explicit fallback: no row)."""
    if not models or len(models) < MIN_MODELS_C:
        return None
    adj = [v - bias.get((m, city), pooled.get(m, 0.0)) for m, v in models.items()]
    rise = sum(max(0.0, a - R) for a in adj) / len(adj)
    up = sum(1 for a in adj if a > R + MODEL_RISE_MARGIN_C) / len(adj)
    return [rise, up, float(len(adj))]


def fit_at(rows_by_hour):
    """{hour: parameters} and the rd2 version naming them (same form as fit)."""
    params = {h: p for h, p in ((h, fit_hour(rs)) for h, rs in sorted(rows_by_hour.items())) if p}
    return params, version_of(params, prefix=VERSION_PREFIX_AT)


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


def version_of(params, prefix=VERSION_PREFIX):
    blob = json.dumps({str(h): p for h, p in sorted(params.items())}, sort_keys=True, default=str)
    last = max((p["last_date"] for p in params.values()), default="none")
    return f"{prefix}:{last}:{hashlib.sha256(blob.encode()).hexdigest()[:10]}"


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
