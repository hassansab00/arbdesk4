"""
The hit tournament (plan v2.1 P3.8): which way of pricing a city puts the most
probability on the bucket that actually settles?

WHY
---
Measured 23 Sep on v_city_hit_history (13-22 Sep, day-ahead): our top pick was
the winning bucket on 31.3% of Celsius city-days against the market's 43.8%,
and 27.0% against 47.1% in Fahrenheit. Seven learners feed pricing and none of
them is scored on the winning bucket; each is gated alone, on degrees, and
stacked in a fixed order. This scores whole recipes, per city, on the bucket.

WHAT A RECIPE IS
----------------
centre x bias x width, run through probability_engine.compute_band_probabilities
- the function live pricing uses - over the venue's own ladder:

  centre  model:<name> (each forecast model alone) | mean | median |
          invmse (inverse-error weights per city, shrunk to equal weights)
  bias    none | long (the city's mean residual, shrunk to 0) |
          recent (half-life 5 days, bounded +-1.5 C)
  width   sigma = w x the recipe's own RMSE on training days, w in WIDTHS

(The global calibration temperature T is not a separate dimension: on a
Gaussian ladder it acts almost exactly as a width factor, which `width`
already searches, and on 23 Sep its map had applies=false.)

HOW IT IS SCORED - FORWARD, TWICE
---------------------------------
1. A recipe's price for day d is fitted on the city's days before d only.
2. The CHAMPION for day d - the recipe the tournament would have used - is
   chosen on scores from days before d only: the city's own best once it has
   MIN_GATE_DAYS scored days and beats the pooled choice on them, otherwise
   the recipe best across all cities before d. The summary grades that choice,
   so the tournament's own selection is out of sample too.

The score is the log loss on the winning bucket (primary), top-pick hit,
multiclass Brier and RPS - against the live engine's price and the market's,
as each stood at the same cutoff (18:00 local the evening before, `d1_eve`).

TWO LANES
---------
'asof' uses forecasts whose issue time is known to be before the cutoff and
may one day promote a recipe. 'research' uses Open-Meteo previous-runs values
at nominal lead 1, whose issue time is unverified (P2.6/P7.2): it says which
models tend to be right, over a longer history, and never promotes anything.

RULE 11
-------
Priors: pooled recipe, equal model weights, zero bias. Bounds: weights in
[0,1], bias +-BIAS_BOUND, width in WIDTHS, sigma >= SIGMA_FLOOR_C. Minimum
sample: MIN_TRAIN days to price, MIN_GATE_DAYS to leave the pooled recipe or
to beat the live engine. Max step per night vs prev_params: MAX_STEP. Every
recipe row carries recipe_version. state stays 'shadow': the engine does not
read these recipes yet, and the table says so.

  python scripts/hit_tournament.py             # fit, score, write
  python scripts/hit_tournament.py --dry-run   # report only
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import statistics
import sys
from collections import defaultdict, namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probability_engine import compute_band_probabilities  # noqa: E402
from walk_forward import gate, per_day, MIN_GATE_DAYS, LOG_FLOOR  # noqa: E402

CHECKPOINT = "d1_eve"
LANES = ("asof", "research")
VERSION = "hit_tournament_v1"
LOOKBACK_DAYS = 120
MIN_TRAIN = 5                 # days behind a fit before it may price
SHRINK_K = 10.0               # prior weight, in days, for bias and weights
HALF_LIFE_DAYS = 5.0
BIAS_BOUND_RECENT = 1.5
BIAS_BOUND = 5.0
WIDTHS = (0.8, 1.0, 1.25, 1.6)
RMSE_FLOOR_C = 0.5
SIGMA_FLOOR_C = 0.25
POOLED_COVERAGE = 0.8         # a pooled champion must score >= 80% of the best-covered recipe's days
TOP_N = 10
MAX_STEP = {"bias_c": 0.3, "sigma_rel": 0.10, "weight": 0.10}

Day = namedtuple("Day", "city date unit bands winner observed live market forecasts")


# ---------------------------------------------------------------------------
# Recipes.
# ---------------------------------------------------------------------------
def recipe_key(centre, bias, width):
    return f"centre={centre}|bias={bias}|width={width}"


def parse_key(key):
    return dict(part.split("=", 1) for part in key.split("|"))


def recipes_for(models):
    centres = [f"model:{m}" for m in sorted(models)]
    if len(models) >= 2:
        centres += ["mean", "median", "invmse"]
    return [recipe_key(c, b, w) for c in centres for b in ("none", "long", "recent")
            for w in WIDTHS]


def shrink(value, n, k, prior):
    return (n / (n + k)) * value + (k / (n + k)) * prior


def model_weights(train, lane, models):
    """Inverse-MSE weights per model on training days, shrunk toward equal."""
    inv = {}
    for m in models:
        errs = [(d.forecasts[lane][m] - d.observed) ** 2
                for d in train if m in d.forecasts.get(lane, {})]
        if errs:
            inv[m] = (len(errs), 1.0 / max(1e-6, sum(errs) / len(errs)))
    if not inv:
        return {}
    mean_inv = sum(v for _n, v in inv.values()) / len(inv)
    raw = {m: shrink(v, n, SHRINK_K, mean_inv) for m, (n, v) in inv.items()}
    tot = sum(raw.values())
    return {m: v / tot for m, v in raw.items()}


def centre_of(centre, fc, weights):
    """The recipe's centre from the day's forecasts, or None if it cannot form."""
    if not fc:
        return None
    if centre.startswith("model:"):
        return fc.get(centre[6:])
    vals = list(fc.values())
    if len(vals) < 2:
        return None
    if centre == "mean":
        return sum(vals) / len(vals)
    if centre == "median":
        return statistics.median(vals)
    if centre == "invmse":
        w = {m: weights.get(m, 0.0) for m in fc}
        tot = sum(w.values())
        if tot <= 0:
            return sum(vals) / len(vals)
        return sum(fc[m] * w[m] for m in fc) / tot
    raise ValueError(centre)


def residuals(centre, train, lane, weights):
    out = []
    for d in train:
        c = centre_of(centre, d.forecasts.get(lane, {}), weights)
        if c is not None and d.observed is not None:
            out.append(c - d.observed)
    return out


def bias_of(kind, resid):
    if kind == "none":
        return 0.0
    if kind == "long":
        return max(-BIAS_BOUND, min(BIAS_BOUND,
                   shrink(sum(resid) / len(resid), len(resid), SHRINK_K, 0.0)))
    n = len(resid)
    wts = [0.5 ** ((n - 1 - i) / HALF_LIFE_DAYS) for i in range(n)]
    b = sum(w * x for w, x in zip(wts, resid)) / sum(wts)
    return max(-BIAS_BOUND_RECENT, min(BIAS_BOUND_RECENT, b))


def sigma_of(resid, bias, width):
    rmse = math.sqrt(sum((x - bias) ** 2 for x in resid) / len(resid))
    return max(SIGMA_FLOOR_C, max(RMSE_FLOOR_C, rmse) * float(width))


def fit(key, train, lane, models):
    """Parameters for one recipe on training days, or None if it cannot fit."""
    r = parse_key(key)
    weights = model_weights(train, lane, models) if r["centre"] == "invmse" else {}
    resid = residuals(r["centre"], train, lane, weights)
    if len(resid) < MIN_TRAIN:
        return None
    bias = bias_of(r["bias"], resid)
    return {"bias_c": bias, "sigma_c": sigma_of(resid, bias, r["width"]),
            "weights": weights, "n": len(resid)}


def predict(key, params, day, lane):
    r = parse_key(key)
    c = centre_of(r["centre"], day.forecasts.get(lane, {}), params.get("weights") or {})
    if c is None:
        return None
    return [p for _b, p in compute_band_probabilities(
        c - params["bias_c"], params["sigma_c"], day.unit, day.bands)]


# ---------------------------------------------------------------------------
# Scores.
# ---------------------------------------------------------------------------
def score(probs, winner):
    """(log_loss, hit, brier, rps) of a ladder of probabilities."""
    p_win = probs[winner]
    top = max(range(len(probs)), key=lambda i: probs[i])
    brier = sum((p - (1.0 if i == winner else 0.0)) ** 2 for i, p in enumerate(probs))
    cum, rps = 0.0, 0.0
    for i, p in enumerate(probs):
        cum += p
        rps += (cum - (1.0 if i >= winner else 0.0)) ** 2
    return -math.log(max(p_win, LOG_FLOOR)), 1.0 if top == winner else 0.0, brier, rps


def benchmark(prices):
    """A price list normalised over the ladder, or None if nothing was priced."""
    if prices is None or not any(p is not None for p in prices):
        return None
    vals = [max(0.0, float(p)) if p is not None else 0.0 for p in prices]
    tot = sum(vals)
    return [v / tot for v in vals] if tot > 0 else None


def walk_city(days, lane):
    """{recipe: {date: (ll, hit, brier, rps)}} - each day priced from the days
    before it. Each centre's training residuals are computed once a day and
    shared by its bias and width variants (the same arithmetic as fit())."""
    days = sorted(days, key=lambda d: d.date)
    models = {m for d in days for m in d.forecasts.get(lane, {})}
    keys = recipes_for(models)
    centres = sorted({parse_key(k)["centre"] for k in keys})
    out = defaultdict(dict)
    for i, day in enumerate(days):
        fc = day.forecasts.get(lane)
        if not fc:
            continue
        train = [d for d in days[:i] if d.observed is not None and d.forecasts.get(lane)]
        if len(train) < MIN_TRAIN:
            continue
        weights = model_weights(train, lane, models) if "invmse" in centres else {}
        for centre in centres:
            resid = residuals(centre, train, lane, weights if centre == "invmse" else {})
            if len(resid) < MIN_TRAIN:
                continue
            c_today = centre_of(centre, fc, weights if centre == "invmse" else {})
            if c_today is None:
                continue
            for kind in ("none", "long", "recent"):
                bias = bias_of(kind, resid)
                for w in WIDTHS:
                    probs = [p for _b, p in compute_band_probabilities(
                        c_today - bias, sigma_of(resid, bias, w), day.unit, day.bands)]
                    out[recipe_key(centre, kind, w)][day.date] = score(probs, day.winner)
    return out


# ---------------------------------------------------------------------------
# Choosing - forward, so the choice is graded on days it did not see.
# ---------------------------------------------------------------------------
def _mean_ll(scores, before=None, only=None):
    vals = [s[0] for d, s in scores.items()
            if (before is None or d < before) and (only is None or d in only)]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def pooled_choice(by_city, before=None):
    """The recipe best across every city's scored days (before `before`)."""
    totals = defaultdict(lambda: [0.0, 0])
    for per_recipe in by_city.values():
        for key, scores in per_recipe.items():
            for d, s in scores.items():
                if before is None or d < before:
                    totals[key][0] += s[0]
                    totals[key][1] += 1
    if not totals:
        return None
    most = max(n for _s, n in totals.values())
    eligible = {k: s / n for k, (s, n) in totals.items() if n >= POOLED_COVERAGE * most and n > 0}
    return min(sorted(eligible), key=lambda k: eligible[k]) if eligible else None


def city_choice(per_recipe, pooled, before=None):
    """(recipe, source): the city's own best once it has MIN_GATE_DAYS scored
    days and beats the pooled recipe on the same days, else the pooled one."""
    best, best_ll = None, None
    for key, scores in per_recipe.items():
        ll, n = _mean_ll(scores, before)
        if n >= MIN_GATE_DAYS and (best_ll is None or ll < best_ll or (ll == best_ll and key < best)):
            best, best_ll = key, ll
    if best is not None and pooled is not None and best != pooled and pooled in per_recipe:
        common = {d for d in per_recipe[best] if (before is None or d < before)} & \
                 {d for d in per_recipe[pooled] if (before is None or d < before)}
        diffs = [per_recipe[pooled][d][0] - per_recipe[best][d][0] for d in sorted(common)]
        if gate(diffs)["applied"]:
            return best, "city"
    return pooled, "pooled"


def forward_champion(by_city, city, dates, pooled_by_date=None):
    """{date: (recipe, source, score)} - the choice made from earlier days only.
    pooled_by_date: the pooled choice per date, computed once for every city."""
    out = {}
    per_recipe = by_city.get(city, {})
    for d in dates:
        pooled = (pooled_by_date[d] if pooled_by_date is not None and d in pooled_by_date
                  else pooled_choice(by_city, before=d))
        if pooled is None:
            continue
        key, source = city_choice(per_recipe, pooled, before=d)
        s = per_recipe.get(key, {}).get(d)
        if s is None and key != pooled:
            key, source, s = pooled, "pooled", per_recipe.get(pooled, {}).get(d)
        if s is not None:
            out[d] = (key, source, s)
    return out


# ---------------------------------------------------------------------------
# Rule 11: bounded steps, versioned.
# ---------------------------------------------------------------------------
def bound_step(params, prev):
    """Move at most MAX_STEP from last night's parameters of the SAME recipe."""
    if not prev:
        return params
    out = dict(params)
    b0 = prev.get("bias_c")
    if b0 is not None:
        out["bias_c"] = max(b0 - MAX_STEP["bias_c"], min(b0 + MAX_STEP["bias_c"], params["bias_c"]))
    s0 = prev.get("sigma_c")
    if s0:
        lo, hi = s0 * (1 - MAX_STEP["sigma_rel"]), s0 * (1 + MAX_STEP["sigma_rel"])
        out["sigma_c"] = max(SIGMA_FLOOR_C, max(lo, min(hi, params["sigma_c"])))
    w0 = prev.get("weights") or {}
    if w0 and params.get("weights"):
        w = {m: max(w0.get(m, v) - MAX_STEP["weight"], min(w0.get(m, v) + MAX_STEP["weight"], v))
             for m, v in params["weights"].items()}
        tot = sum(w.values()) or 1.0
        out["weights"] = {m: v / tot for m, v in w.items()}
    return out


def recipe_version(key, params):
    blob = json.dumps({"v": VERSION, "recipe": key,
                       "params": {k: (round(v, 4) if isinstance(v, float) else v)
                                  for k, v in params.items() if k != "n"}},
                      sort_keys=True, default=lambda x: round(x, 4) if isinstance(x, float) else x)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Evidence.
# ---------------------------------------------------------------------------
def build_days(ladder_rows, forecast_rows):
    """Days from v_hit_ladders and v_hit_forecasts rows."""
    ladders = defaultdict(list)
    for r in ladder_rows:
        ladders[(r["city_key"], str(r["for_date"]))].append(r)
    fcs = defaultdict(lambda: defaultdict(dict))
    for r in forecast_rows:
        if r.get("forecast_max_c") is None:
            continue
        fcs[(r["city_key"], str(r["for_date"]))][r["lane"]][r["model"]] = float(r["forecast_max_c"])
    days = []
    for (city, date), rows in ladders.items():
        rows.sort(key=lambda r: (0 if r["open_low"] else 2 if r["open_high"] else 1,
                                 float(r["band_lo"]) if r["band_lo"] is not None else -1e9))
        winners = [i for i, r in enumerate(rows) if r["settled_yes"]]
        if len(winners) != 1:
            continue
        bands = [{"band_id": r["band_id"],
                  "band_lo": float(r["band_lo"]) if r["band_lo"] is not None else None,
                  "band_hi": float(r["band_hi"]) if r["band_hi"] is not None else None,
                  "open_low": bool(r["open_low"]), "open_high": bool(r["open_high"])} for r in rows]
        obs = rows[0].get("observed_max_c")
        days.append(Day(city=city, date=date, unit=rows[0].get("unit") or "C", bands=bands,
                        winner=winners[0], observed=float(obs) if obs is not None else None,
                        live=benchmark([r.get("live_prob") for r in rows]),
                        market=benchmark([r.get("market_price") for r in rows]),
                        forecasts={k: dict(v) for k, v in fcs.get((city, date), {}).items()}))
    return days


def load(lookback_days=LOOKBACK_DAYS):
    from common import rest_all
    since = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    ladder_rows = rest_all("v_hit_ladders", [
        ("select", "city_key,for_date,band_id,band_lo,band_hi,open_low,open_high,unit,"
                   "settled_yes,observed_max_c,live_prob,market_price"),
        ("for_date", f"gte.{since}")], order="city_key.asc,for_date.asc,band_id.asc",
        page_size=1000)
    forecast_rows = rest_all("v_hit_forecasts", [
        ("select", "city_key,for_date,lane,model,forecast_max_c"),
        ("for_date", f"gte.{since}")], order="city_key.asc,for_date.asc,lane.asc,model.asc",
        page_size=1000)
    return build_days(ladder_rows, forecast_rows)


# ---------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------
def _rate(vals):
    return sum(vals) / len(vals) if vals else None


def run_lane(days, lane, prev_by_city=None):
    """(tournament rows, recipe rows, per-unit summaries) for one lane."""
    prev_by_city = prev_by_city or {}
    by_city_days = defaultdict(list)
    for d in days:
        by_city_days[d.city].append(d)
    by_city = {c: walk_city(ds, lane) for c, ds in by_city_days.items()}
    pooled_final = pooled_choice(by_city)
    all_dates = sorted({d.date for d in days})
    pooled_by_date = {d: pooled_choice(by_city, before=d) for d in all_dates}

    t_rows, r_rows = [], []
    unit_series = defaultdict(lambda: {"champ": [], "live": [], "market": [], "uniform": [],
                                       "vs_live": [], "vs_market": []})
    for city, per_recipe in sorted(by_city.items()):
        cdays = {d.date: d for d in by_city_days[city]}
        unit = next(iter(cdays.values())).unit
        ranked = sorted(((k, _mean_ll(s)) for k, s in per_recipe.items() if s),
                        key=lambda x: (x[1][0], x[0]))
        for rank, (key, (ll, n)) in enumerate(ranked[:TOP_N], start=1):
            sc = list(per_recipe[key].values())
            t_rows.append({"city_key": city, "checkpoint": CHECKPOINT, "lane": lane, "recipe": key,
                           "n_days": n, "log_loss": round(ll, 5),
                           "hit_rate": round(_rate([s[1] for s in sc]), 4),
                           "brier": round(_rate([s[2] for s in sc]), 5),
                           "rps": round(_rate([s[3] for s in sc]), 5),
                           "rank_in_city": rank, "is_pooled_champion": key == pooled_final})
        if pooled_final and pooled_final in per_recipe and pooled_final not in [k for k, _ in ranked[:TOP_N]]:
            sc = list(per_recipe[pooled_final].values())
            ll, n = _mean_ll(per_recipe[pooled_final])
            t_rows.append({"city_key": city, "checkpoint": CHECKPOINT, "lane": lane,
                           "recipe": pooled_final, "n_days": n, "log_loss": round(ll, 5),
                           "hit_rate": round(_rate([s[1] for s in sc]), 4),
                           "brier": round(_rate([s[2] for s in sc]), 5),
                           "rps": round(_rate([s[3] for s in sc]), 5),
                           "rank_in_city": None, "is_pooled_champion": True})

        # the forward champion: the tournament's own choice, graded out of sample
        fwd = forward_champion(by_city, city, sorted(cdays), pooled_by_date)
        champ_ll, champ_hit, live_ll, live_hit, mkt_ll, mkt_hit = [], [], [], [], [], []
        vs_live = []
        for d, (_key, _src, s) in sorted(fwd.items()):
            day = cdays[d]
            champ_ll.append(s[0])
            champ_hit.append(s[1])
            unit_series[unit]["champ"].append((d, s[0], s[1]))
            unit_series[unit]["uniform"].append((d, math.log(len(day.bands))))
            if day.live:
                ls = score(day.live, day.winner)
                live_ll.append(ls[0])
                live_hit.append(ls[1])
                vs_live.append((d, ls[0] - s[0]))
                unit_series[unit]["live"].append((d, ls[0], ls[1]))
                unit_series[unit]["vs_live"].append((d, ls[0] - s[0]))
            if day.market:
                ms = score(day.market, day.winner)
                mkt_ll.append(ms[0])
                mkt_hit.append(ms[1])
                unit_series[unit]["market"].append((d, ms[0], ms[1]))
                unit_series[unit]["vs_market"].append((d, ms[0] - s[0]))

        # tomorrow's recipe: chosen on every day, fitted on every day, stepped
        key, source = city_choice(per_recipe, pooled_final)
        if key is None:
            continue
        train = [d for d in by_city_days[city] if d.observed is not None and d.forecasts.get(lane)]
        models = {m for d in train for m in d.forecasts.get(lane, {})}
        params = fit(key, train, lane, models)
        if params is None:
            continue
        prev = prev_by_city.get(city) or {}
        prev_params = prev.get("params") if prev.get("recipe") == key else None
        bounded = bound_step(params, prev_params)
        g = gate([x for _d, x in sorted(vs_live)])
        verdict = ("forward champion beats the live engine: " + g["reason"]
                   if g["applied"] else "against the live engine: " + g["reason"])
        r_rows.append({
            "city_key": city, "checkpoint": CHECKPOINT, "lane": lane, "recipe": key,
            "params": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in bounded.items()},
            "prev_params": prev_params, "recipe_version": recipe_version(key, bounded),
            "source": source, "state": "shadow", "n_days": len(champ_ll),
            "log_loss": round(_rate(champ_ll), 5) if champ_ll else None,
            "hit_rate": round(_rate(champ_hit), 4) if champ_hit else None,
            "live_log_loss": round(_rate(live_ll), 5) if live_ll else None,
            "live_hit_rate": round(_rate(live_hit), 4) if live_hit else None,
            "market_log_loss": round(_rate(mkt_ll), 5) if mkt_ll else None,
            "market_hit_rate": round(_rate(mkt_hit), 4) if mkt_hit else None,
            "gain_vs_live": round(g["mean"], 5) if g["mean"] is not None else None,
            "gain_vs_live_lo": round(g["lo"], 5) if g["lo"] is not None else None,
            "gain_vs_live_hi": round(g["hi"], 5) if g["hi"] is not None else None,
            "n_vs_live": g["n"],
            "reason": (f"{lane}: {source} recipe; {verdict}. Shadow: the engine does not read "
                       f"hit recipes yet" + ("" if lane == "asof" else
                                              "; research lane (issue time unverified) never promotes")),
        })

    summaries = []
    for unit, s in sorted(unit_series.items()):
        def agg(series):
            return (_rate([x[1] for x in series]), _rate([x[2] for x in series])) if series else (None, None)
        c_ll, c_hit = agg(s["champ"])
        l_ll, l_hit = agg(s["live"])
        m_ll, m_hit = agg(s["market"])
        gl = gate(per_day(s["vs_live"]), min_days=1)
        gm = gate(per_day(s["vs_market"]), min_days=1)
        summaries.append({
            "unit": unit, "checkpoint": CHECKPOINT, "lane": lane, "n_days": len(s["champ"]),
            "champion_hit_rate": c_hit, "champion_log_loss": c_ll,
            "live_hit_rate": l_hit, "live_log_loss": l_ll,
            "market_hit_rate": m_hit, "market_log_loss": m_ll,
            "uniform_log_loss": _rate([x[1] for x in s["uniform"]]),
            "n_live_days": len(s["live"]), "n_market_days": len(s["market"]),
            "gain_vs_live": gl["mean"], "gain_vs_live_lo": gl["lo"], "gain_vs_live_hi": gl["hi"],
            "gain_vs_market": gm["mean"], "gain_vs_market_lo": gm["lo"], "gain_vs_market_hi": gm["hi"],
        })
    return t_rows, r_rows, summaries


def _round(row):
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in row.items()}


def main():
    ap = argparse.ArgumentParser(description="The hit tournament (plan v2.1 P3.8)")
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from common import rest, upsert_replace, log_run
    days = load(args.lookback)
    print(f"{len(days)} settled city-day(s) with one winning bucket")
    prev = defaultdict(dict)
    try:
        for r in rest("derived_hit_recipe", [("select", "city_key,lane,recipe,params"),
                                             ("checkpoint", f"eq.{CHECKPOINT}")]):
            prev[r["lane"]][r["city_key"]] = r
    except Exception as e:                       # first run: nothing to step from
        print(f"  no previous recipes ({e})", file=sys.stderr)

    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    detail = {"days": len(days), "lanes": {}}
    for lane in LANES:
        t_rows, r_rows, summaries = run_lane(days, lane, prev.get(lane))
        for s in summaries:
            print(f"[{lane}] {s['unit']}: {s['n_days']} forward day(s) - champion hit "
                  f"{(s['champion_hit_rate'] or 0):.3f}, live {s['live_hit_rate']}, "
                  f"market {s['market_hit_rate']}; log-loss gain vs live {s['gain_vs_live']} "
                  f"[{s['gain_vs_live_lo']}, {s['gain_vs_live_hi']}] over {s['n_live_days']} day(s)")
        detail["lanes"][lane] = {"cities": len(r_rows), "tournament_rows": len(t_rows),
                                 "summary": [_round(s) for s in summaries]}
        if args.dry_run:
            continue
        for rows in (t_rows, r_rows, summaries):
            for r in rows:
                r["computed_at"] = computed_at
        if t_rows:
            upsert_replace("derived_hit_tournament", t_rows, on_conflict="city_key,checkpoint,lane,recipe")
        if r_rows:
            upsert_replace("derived_hit_recipe", r_rows, on_conflict="city_key,checkpoint,lane")
        if summaries:
            upsert_replace("derived_hit_summary", [_round(s) for s in summaries],
                           on_conflict="unit,checkpoint,lane")
    if not args.dry_run:
        log_run("hit_tournament", "ok", len(days), detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
