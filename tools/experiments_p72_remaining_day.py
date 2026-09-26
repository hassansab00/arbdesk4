"""The first measurement behind plan P7.2 (the remaining-day model), 26 Sep.

QUESTION. Same-day, the market beat the engine on the frozen checkpoint calls
(the venue's winner picked 86 of 98 times against 56; P4.6, 26 Sep). How much
better can the day's final maximum be called at 09, 11, 13 and 15 local time
from what has happened so far and what the forecast said about the rest of the
day - using only what was known then?

INPUTS, per city-day and decision hour H (the city's wall clock):
  * the station's readings up to H:00 (IEM METAR: data/archive/observations
    and weather_observations): running max R, the latest reading, the 1-h and
    3-h change;
  * the day-before best_match run, hourly (Open-Meteo `_previous_day1`: a run
    from the day before, so it was out before the day began): the forecast at
    H, the forecast maximum over the hours after H, the whole day's forecast
    maximum, the forecast's error so far (reading minus forecast, now and over
    the last 3 h), cloud and radiation over the rest of the daytime;
  * the season.
LABEL: the day's station maximum (derived_city_day_features.max_c).

METHODS, walk-forward by month (each month fitted only on the days before it):
  floor_fc   max(R, the forecast's day maximum) - the floor and the forecast,
             roughly what the engine's same-day path does before its
             trajectory layer
  floor_err  max(R, rest-of-day forecast maximum + the forecast's error now)
  model      R + max(0, ridge prediction of the remaining rise), per decision
             hour, pooled across cities with a shrunk city intercept

    python tools/experiments_p72_remaining_day.py data/training/previous_runs/best_match_hourly_day1_utc.csv.gz \
        obs_utc.csv.gz labels.json units.json tz.json

best_match_hourly_day1_utc.csv.gz: city_key, utc, t, td, cloud, sw, wind, precip,
pmsl - the 26 Sep Previous Runs answers (P2.9), each hour turned back into UTC
with the answer's own fixed offset. obs_utc.csv.gz: city_key, utc, temp_c - the
IEM rows of data/archive/observations plus weather_observations source 'IEM'
from 28 Jul 2026 (551,689 readings, 22 Jul 2025 - 26 Sep 2026, deduplicated on
city and minute). labels.json and units.json as in experiments_p29_honest_mos;
tz.json is select json_object_agg(city_key, timezone) from cities.

RESULT (26 Sep; MAE C / same whole degree in the city's unit):
  hour  n       already set  floor_fc       floor_err      model          model vs floor_fc
  09    15,728  7.1%         1.255 / 25.8%  1.343 / 25.3%  1.007 / 31.3%  +0.248 [0.233, 0.263]
  11    15,722  14.4%        1.148 / 28.6%  1.063 / 31.8%  0.863 / 36.0%  +0.285 [0.270, 0.300]
  13    15,727  40.5%        0.888 / 41.1%  0.726 / 46.0%  0.626 / 48.0%  +0.262 [0.249, 0.275]
  15    15,736  76.8%        0.628 / 60.3%  0.289 / 75.8%  0.313 / 74.7%  +0.314 [0.300, 0.330]

Needs numpy (research only).
"""
import csv, gzip, json, math, random, sys, datetime as dt
from collections import defaultdict
from zoneinfo import ZoneInfo
import numpy as np

fc_path, obs_path, labels_path, units_path, tz_path = sys.argv[1:6]
TZ = json.load(open(tz_path))
UNIT = json.load(open(units_path))
Y = {(c, d): float(m) for c, d, m, *_ in json.load(open(labels_path))}
HOURS = (9, 11, 13, 15)
FIRST_TEST = dt.date(2025, 11, 1)
UTC = dt.timezone.utc


def local(city, utc_str):
    t = dt.datetime.fromisoformat(utc_str).replace(tzinfo=UTC).astimezone(ZoneInfo(TZ[city]))
    return t


# forecast: fc[city][date][hour] = (t, cloud, sw)
fc = defaultdict(lambda: defaultdict(dict))
with gzip.open(fc_path, "rt") as f:
    for r in csv.DictReader(f):
        if r["city_key"] not in TZ or r["t"] == "":
            continue
        t = local(r["city_key"], r["utc"])
        fc[r["city_key"]][t.date().isoformat()][t.hour] = (
            float(r["t"]), float(r["cloud"]) if r["cloud"] else None, float(r["sw"]) if r["sw"] else None)

# observations: obs[city][date] = [(local hours as float, temp)]
obs = defaultdict(lambda: defaultdict(list))
with gzip.open(obs_path, "rt") as f:
    for r in csv.DictReader(f):
        c = r["city_key"]
        if c not in TZ:
            continue
        t = local(c, r["utc"])
        obs[c][t.date().isoformat()].append((t.hour + t.minute / 60, float(r["temp_c"])))


def nearest(series, h, tol):
    best = None
    for x, v in series:
        if abs(x - h) <= tol and (best is None or abs(x - h) < abs(best[0] - h)):
            best = (x, v)
    return best[1] if best else None


FEATURES = ["fc_rest_minus_now", "now_minus_R", "fc_err_now", "fc_err_3h", "slope_1h", "slope_3h",
            "fc_day_minus_R", "cloud_rest", "sw_rest", "doy_sin", "doy_cos"]


def rows_for(H):
    out = []
    for c, days in obs.items():
        for d, series in days.items():
            y = Y.get((c, d))
            f = fc.get(c, {}).get(d)
            if y is None or not f or len(f) < 20:
                continue
            seen = sorted(p for p in series if p[0] <= H)
            if len(seen) < 3:
                continue
            R = max(v for _, v in seen)
            now = nearest(seen, H, 1.5)
            h1, h3 = nearest(seen, H - 1, 0.75), nearest(seen, H - 3, 0.75)
            if now is None or h1 is None or h3 is None or H not in f:
                continue
            rest = [f[h][0] for h in range(H + 1, 24) if h in f]
            if len(rest) < 6:
                continue
            errs = [nearest(seen, h, 0.5) - f[h][0] for h in (H - 2, H - 1, H)
                    if h in f and nearest(seen, h, 0.5) is not None]
            cloud = [f[h][1] for h in range(H + 1, 18) if h in f and f[h][1] is not None]
            sw = [f[h][2] for h in range(H + 1, 18) if h in f and f[h][2] is not None]
            day = dt.date.fromisoformat(d)
            doy = 2 * math.pi * day.timetuple().tm_yday / 365.25
            fc_day = max(v[0] for v in f.values())
            x = [max(rest) - now, now - R, now - f[H][0], sum(errs) / len(errs) if errs else 0.0,
                 now - h1, now - h3, fc_day - R, sum(cloud) / len(cloud) if cloud else 50.0,
                 sum(sw) if sw else 0.0, math.sin(doy), math.cos(doy)]
            out.append({"city": c, "date": day, "y": y, "R": R, "now": now, "fc_day": fc_day,
                        "fc_rest": max(rest), "fc_err": now - f[H][0], "x": np.array(x)})
    return out


def ridge(X, y, lam, prior):
    P = lam * np.eye(X.shape[1]); P[0, 0] = 0.0
    return np.linalg.solve(X.T @ X + P, X.T @ y + P @ prior)


def fit_predict(train, test, lam_pool=10.0, lam_city=100.0):
    X = np.array([r["x"] for r in train]); mu, sd = X.mean(0), X.std(0) + 1e-9
    D = lambda rows: np.hstack([np.ones((len(rows), 1)), (np.array([r["x"] for r in rows]) - mu) / sd])
    t = lambda rows: np.array([r["y"] - r["R"] for r in rows])
    bpool = ridge(D(train), t(train), lam_pool, np.zeros(X.shape[1] + 1))
    by = defaultdict(list)
    for r in train: by[r["city"]].append(r)
    bc = {c: ridge(D(rs), t(rs), lam_city, bpool) for c, rs in by.items()}
    Dt = D(test)
    return np.array([r["R"] + max(0.0, Dt[i] @ bc.get(r["city"], bpool)) for i, r in enumerate(test)])


def whole(v, unit): return round(v * 9 / 5 + 32) if unit == "F" else round(v)


def score(rows, preds, name):
    e = np.array([p - r["y"] for p, r in zip(preds, rows)])
    hit = np.mean([whole(p, UNIT.get(r["city"], "C")) == whole(r["y"], UNIT.get(r["city"], "C"))
                   for p, r in zip(preds, rows)])
    return {"mae": round(float(np.mean(np.abs(e))), 3), "bias": round(float(np.mean(e)), 3),
            "same_whole": round(float(hit), 4)}


if __name__ == "__main__":
    for H in HOURS:
        data = rows_for(H)
        months = sorted({(r["date"].year, r["date"].month) for r in data})
        scored = []
        for yy, mm in months:
            start = dt.date(yy, mm, 1)
            if start < FIRST_TEST:
                continue
            train = [r for r in data if r["date"] < start]
            test = [r for r in data if (r["date"].year, r["date"].month) == (yy, mm)]
            if not test or len(train) < 2000:
                continue
            pm = fit_predict(train, test)
            for i, r in enumerate(test):
                scored.append((r, max(r["R"], r["fc_day"]), max(r["R"], r["fc_rest"] + r["fc_err"]), float(pm[i])))
        rows = [s[0] for s in scored]
        out = {"hour": H, "n": len(rows), "days": len({r["date"] for r in rows}), "cities": len({r["city"] for r in rows}),
               "already_set": round(float(np.mean([abs(r["y"] - r["R"]) < 0.05 for r in rows])), 3),
               "floor_fc": score(rows, [s[1] for s in scored], "floor_fc"),
               "floor_err": score(rows, [s[2] for s in scored], "floor_err"),
               "model": score(rows, [s[3] for s in scored], "model")}
        by = defaultdict(list)
        for s in scored: by[s[0]["date"]].append(s)
        days = list(by); rng = random.Random(5)
        def gain(sample, a, b):
            return (np.mean([abs(s[a] - s[0]["y"]) for d in sample for s in by[d]])
                    - np.mean([abs(s[b] - s[0]["y"]) for d in sample for s in by[d]]))
        g = [gain([rng.choice(days) for _ in days], 1, 3) for _ in range(300)]
        out["model_vs_floor_fc"] = {"mae_gain": round(float(gain(days, 1, 3)), 3),
                                    "ci95": [round(float(np.percentile(g, 2.5)), 3), round(float(np.percentile(g, 97.5)), 3)]}
        print(json.dumps(out), flush=True)
