"""The experiment behind plan v2.2 P3.9 (26 Sep), kept so its numbers can be re-run.

Input: the JSON saved from this query against the live database (one array per
city-day: city, date, station max, previous max, 08:00 temperature, dewpoint
depression, humidity, the latest public forecast issued before 08:00 local,
its age in hours, and each model's lead-1 forecast):

    with d as (
      select f.city_key, f.obs_date, f.max_c, f.prev_max_c, f.morning_temp_c,
             f.dewpoint_depression_c, f.morning_humidity,
             ((f.obs_date::timestamp + interval '8 hours') at time zone c.timezone) as cutoff
        from derived_city_day_features f join cities c using (city_key)
       where f.obs_date >= date '2026-07-29' and f.max_c is not null and c.status = 'active'),
    bm as (
      select distinct on (d.city_key, d.obs_date) d.city_key, d.obs_date, v.forecast_max_c fc,
             v.issued_at, v.model
        from d join v_forecast_issued v on v.city_key = d.city_key and v.for_date = d.obs_date
       where v.model in ('open_meteo_best_match', 'open_meteo_forecast') and v.issued_at <= d.cutoff
       order by d.city_key, d.obs_date, v.issued_at desc),
    mm as (
      select city_key, for_date, jsonb_object_agg(model, forecast_max_c) m
        from weather_forecast_models where lead_days = 1 group by 1, 2)
    select json_agg(json_build_array(d.city_key, d.obs_date, d.max_c, d.prev_max_c,
             d.morning_temp_c, d.dewpoint_depression_c, d.morning_humidity, bm.fc,
             extract(epoch from d.cutoff - bm.issued_at) / 3600, mm.m))
      from d join bm using (city_key, obs_date)
      left join mm on mm.city_key = d.city_key and mm.for_date = d.obs_date;

Result on 26 Sep: docs/PLAN_PROGRESS.md, P3.9 row.

    python tools/experiments_p39_morning_cutoff.py <saved_query_output.txt>

Needs numpy (not a runtime dependency of the platform; research only).
"""
import json, re, sys, math
import numpy as np
raw = open(sys.argv[1]).read()
m = re.search(r'\[\{"json_agg":(\[.*?\])\}\]', raw.replace('\\"', '"'))
rows = json.loads(m.group(1))
MODELS = ["open_meteo_ecmwf_ifs025","open_meteo_gfs_seamless","open_meteo_icon_seamless","open_meteo_ukmo_seamless",
          "open_meteo_jma_seamless","open_meteo_gem_seamless","open_meteo_meteofrance_seamless"]
data = []
for city, d, y, prev, mt, dd, hum, fc, age, mm in rows:
    if None in (y, prev, mt, dd, fc): continue
    mods = {k: v for k, v in (mm or {}).items()}
    data.append(dict(city=city, d=d, y=float(y), prev=float(prev), mt=float(mt), dd=float(dd),
                     hum=float(hum) if hum is not None else None, fc=float(fc), age=float(age), mods=mods))
dates = sorted({r["d"] for r in data})
cut = dates[int(len(dates) * 0.7)]
tr = [r for r in data if r["d"] < cut]; te = [r for r in data if r["d"] >= cut]
print(f"rows {len(data)}  cities {len({r['city'] for r in data})}  dates {dates[0]}..{dates[-1]}  train<{cut}: {len(tr)}  test: {len(te)}")
print("model keys seen:", sorted({k for r in data for k in r['mods']})[:10])

def score(name, preds):
    e = np.array([p - r["y"] for p, r in zip(preds, te) if p is not None])
    n = len(e)
    same = np.mean([round(p) == round(r["y"]) for p, r in zip(preds, te) if p is not None])
    print(f"{name:48s} n={n:4d}  MAE {np.mean(np.abs(e)):.3f}  bias {np.mean(e):+.3f}  same-integer-C {same:.3f}")

score("A raw public forecast (latest issued <= 08:00 local)", [r["fc"] for r in te])

# B: city bias, shrunk (k=10)
def city_bias(rows, k=10.0):
    s, n = {}, {}
    for r in rows:
        s[r["city"]] = s.get(r["city"], 0) + (r["y"] - r["fc"]); n[r["city"]] = n.get(r["city"], 0) + 1
    return {c: s[c] / (n[c] + k) for c in s}
cb = city_bias(tr)
score("B forecast + shrunk city bias", [r["fc"] + cb.get(r["city"], 0) for r in te])

# C: pooled ridge on the forecast error with morning features + shrunk city bias
def X(r):
    return [1.0, r["fc"] - r["prev"], r["mt"] - r["fc"], r["dd"], (r["hum"] if r["hum"] is not None else 60.0) / 100, min(r["age"], 48) / 24]
def ridge(rows, target, lam=5.0):
    A = np.array([X(r) for r in rows]); b = np.array([target(r) for r in rows])
    I = np.eye(A.shape[1]); I[0, 0] = 0
    return np.linalg.solve(A.T @ A + lam * I, A.T @ b)
res_after_city = lambda r: r["y"] - r["fc"] - cb.get(r["city"], 0)
w = ridge(tr, res_after_city)
print("   ridge weights [1, fc-prev, morning-fc, dd, hum, age]:", np.round(w, 3))
score("C B + pooled ridge on morning discrepancies", [r["fc"] + cb.get(r["city"], 0) + float(np.dot(w, X(r))) for r in te])

# D: equal-weight mean of the 7 models (lead 1), with shrunk per-model city bias
def mean_models(r, bias=None):
    vals = [r["mods"][k] + (bias.get((k, r["city"]), 0) if bias else 0) for k in MODELS if r["mods"].get(k) is not None]
    return sum(vals) / len(vals) if len(vals) >= 4 else None
score("D equal-weight 7-model mean (day-before runs)", [mean_models(r) for r in te])
mb = {}
for k in MODELS:
    s, n = {}, {}
    for r in tr:
        v = r["mods"].get(k)
        if v is None: continue
        s[r["city"]] = s.get(r["city"], 0) + (r["y"] - v); n[r["city"]] = n.get(r["city"], 0) + 1
    for c in s: mb[(k, c)] = s[c] / (n[c] + 10.0)
score("E D with each model's shrunk city bias", [mean_models(r, mb) for r in te])
both = [(mean_models(r, mb), r) for r in te]
score("F average of B and E", [((r["fc"] + cb.get(r["city"], 0)) + e) / 2 if e is not None else None for e, r in both])

# paired block bootstrap over TEST DATES: MAE(B) - MAE(F), and hit(F) - hit(B)
import random
rng = random.Random(7)
by_date = {}
for e, r in both:
    if e is None: continue
    b = r["fc"] + cb.get(r["city"], 0); f = (b + e) / 2
    by_date.setdefault(r["d"], []).append((abs(b - r["y"]), abs(f - r["y"]), round(b) == round(r["y"]), round(f) == round(r["y"])))
ds = sorted(by_date)
diffs, hits = [], []
for _ in range(4000):
    pick = [ds[rng.randrange(len(ds))] for _ in ds]
    xs = [x for d in pick for x in by_date[d]]
    diffs.append(sum(x[0] - x[1] for x in xs) / len(xs)); hits.append(sum(x[3] - x[2] for x in xs) / len(xs))
diffs.sort(); hits.sort()
print(f"test dates {len(ds)}; MAE gain F over B: {sum(diffs)/len(diffs):.3f} C, 95% [{diffs[100]:.3f}, {diffs[3899]:.3f}]")
print(f"same-integer gain F over B: {sum(hits)/len(hits):+.3f}, 95% [{hits[100]:+.3f}, {hits[3899]:+.3f}]")
