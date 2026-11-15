#!/usr/bin/env python3
"""Two things the station-corrected combination (P3.9) does not do yet, measured
before anything is built (15 Nov 2026). Research: nothing here prices or trades.

P3.9 learns each source's station bias on the Previous Runs record (runs 24 h
or more old) and applies it to each model's LATEST run, then averages the
corrected sources with equal weights. Two candidates, fixed here before a score:

  own     the bias learned from the forward rows' own settled errors instead:
          the same source, the same lead, the rows the engine was served
          (weather_forecast_models, source open-meteo-models-current), the same
          shrinkage (the source's pool, K, MIN_N, the bound) and window as P3.9.
  own_w   own, and each corrected source weighted by 1 / its shrunk mean
          squared error at that city and lead (the pool's below MIN_N days),
          instead of equally.

PART `forward`: every forward row from 12 Oct with a settled station maximum,
at leads 0, 1 and 2. Each run night is fitted only on target days before it.
`prod` is the bias in force that night (derived_station_correction). Then the
served first calls are replayed with the same integrator: the evening-before
call with half the centre's change (the served centre is the station-model
blend, P3.9 is half of it) and the served width; the morning call from the
lead-0 row's centre and the stored station width (what sd_corr:v1 priced,
with `own_w` in place of the combination).

PART `record`: the same question on the long record (data/training/
previous_runs, lead 1, 14 Jul 2025 on): P3.9's recipe as coded (`base`),
inverse-MSE weights (`weights`), and yesterday's out-of-sample miss added back
with a pooled lag-1 coefficient fitted on the window, bounded 0 to 0.6 (`ar`),
both (`weights_ar`), each day fitted on the 45 days before it. Mean absolute
error against the station maximum, and the log loss on the venue's ladders
beside the market at 00:00 (tools/market_vs_model.py's events and ladders),
each recipe with a width from its own earlier errors.

The truth is the station's whole-day maximum (tools/wxpredict/common.
station_daily_rows: 20 reports or more, no gap over 3 h). Intervals are 90%,
resampling dates (seed 11, 2,000).

    python tools/station_weights_study.py --out data/eval/model_audit_2026-11-15
"""
import argparse
import collections
import csv
import datetime as dt
import glob
import gzip
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import station_correction as sc  # noqa: E402
import probability_engine as pe  # noqa: E402

csv.field_size_limit(sys.maxsize)
MODELS = ("ecmwf_ifs025", "gem_seamless", "gfs_seamless", "icon_seamless", "jma_seamless",
          "meteofrance_seamless", "ukmo_seamless")
SOURCES = tuple("open_meteo_" + m for m in MODELS)
K, MIN_N, BOUND, W = sc.K, sc.MIN_N, sc.BOUND_C, sc.WINDOW_DAYS
FORWARD_FROM = "2026-10-12"
RECORD_FROM = "2025-10-01"
TRUE = ("t", "true", "True", "1")
SEED, BOOT = 11, 2000


def read(table):
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "mirror", table, "*.csv.gz"))):
        with gzip.open(path, "rt", newline="") as fh:
            yield from csv.DictReader(fh)


def ts(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def cities():
    path = sorted(glob.glob(os.path.join(ROOT, "data", "mirror", "cities", "cities-*.csv.gz")))[-1]
    return {r["city_key"]: r for r in csv.DictReader(gzip.open(path, "rt"))}


def station_truth(city_rows):
    import importlib.util
    # tools/wxpredict has its own common.py; scripts/common.py is the one on the path
    spec = importlib.util.spec_from_file_location("wxpredict_common", os.path.join(HERE, "wxpredict", "common.py"))
    wx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wx)
    icao = {c: (r["icao"] or "").upper() for c, r in city_rows.items()}
    return {(r["city_key"], r["local_date"]): float(r["tmax_c"]) for r in wx.station_daily_rows()
            if r["tmax_c"] and r["station"] == icao.get(r["city_key"]) and int(r["n_reports"]) >= 20
            and float(r["max_gap_h"] or 99) <= 3.0}


def interval(pairs):
    """Mean and 90% interval of (date, value) pairs, resampling dates."""
    by = collections.defaultdict(list)
    for d, v in pairs:
        by[d].append(v)
    ds = sorted(by)
    rng = np.random.default_rng(SEED)
    st = [np.mean([z for i in rng.integers(0, len(ds), len(ds)) for z in by[ds[i]]]) for _ in range(BOOT)]
    return {"mean": round(float(np.mean([z for d in ds for z in by[d]])), 4),
            "ci90": [round(float(np.percentile(st, 5)), 4), round(float(np.percentile(st, 95)), 4)],
            "dates": len(ds), "n": sum(len(v) for v in by.values())}


def shrunk(errors):
    """(bias, pooled, mse, pooled mse) from {(city, source): [truth - forecast]},
    as station_correction.fit shrinks a cell to its source's pool."""
    pooled, pmse, bias, mse = {}, {}, {}, {}
    for m in {m for _c, m in errors}:
        allv = [x for (_c, mm), v in errors.items() if mm == m for x in v]
        pooled[m] = float(np.clip(np.mean(allv), -BOUND, BOUND))
        pmse[m] = float(np.mean([(x - pooled[m]) ** 2 for x in allv]))
    for (c, m), v in errors.items():
        n = len(v)
        b = pooled[m] if n < MIN_N else pooled[m] + sum(x - pooled[m] for x in v) / (n + K)
        bias[(c, m)] = float(np.clip(b, -BOUND, BOUND))
        mse[(c, m)] = (sum((x - bias[(c, m)]) ** 2 for x in v) + K * pmse[m]) / (n + K)
    return bias, pooled, mse, pmse


# ---------------------------------------------------------------------------
# forward: the rows the engine was served
# ---------------------------------------------------------------------------
def forward_rows():
    """{(city, for_date, run date): {"lead", "v": [7 maxima]}}: the newest run of that date."""
    si = {s: i for i, s in enumerate(SOURCES)}
    out = {}
    for r in read("weather_forecast_models"):
        if r["source"] != sc.FORWARD_SOURCE or r["model"] not in si or not r["forecast_max_c"]:
            continue
        e = out.setdefault((r["city_key"], r["for_date"], r["run_at"][:10]),
                           {"lead": int(r["lead_days"]), "run_at": r["run_at"], "v": [None] * 7})
        if r["run_at"] >= e["run_at"]:
            e["v"][si[r["model"]]] = float(r["forecast_max_c"])
            e["run_at"], e["lead"] = r["run_at"], int(r["lead_days"])
    return out


def forward(truth):
    fw = forward_rows()
    prod = collections.defaultdict(dict)
    for r in read("derived_station_correction"):
        prod[r["as_of"]][(r["city_key"], r["source"], int(r["lead_days"]))] = float(r["bias_c"])
    as_ofs = sorted(prod)
    fits, res = {}, []
    for (c, d, run), e in sorted(fw.items(), key=lambda kv: kv[0][2]):
        y = truth.get((c, d))
        before = [a for a in as_ofs if a <= run]
        if y is None or run < FORWARD_FROM or not before:
            continue
        lead, table = e["lead"], prod[before[-1]]
        lk = max(1, min(lead, max(sc.LEADS)))
        p = [v + table[(c, SOURCES[m], lk)] for m, v in enumerate(e["v"])
             if v is not None and (c, SOURCES[m], lk) in table]
        if (run, lead) not in fits:
            lo = (dt.date.fromisoformat(run) - dt.timedelta(days=W)).isoformat()
            errors = collections.defaultdict(list)
            for (c2, d2, _r2), e2 in fw.items():
                if e2["lead"] == lead and lo <= d2 < run and (c2, d2) in truth:
                    for m, v in enumerate(e2["v"]):
                        if v is not None:
                            errors[(c2, m)].append(truth[(c2, d2)] - v)
            fits[(run, lead)] = shrunk(errors) if errors else None
        if fits[(run, lead)] is None or len(p) < sc.MIN_SOURCES:
            continue
        bias, pooled, mse, pmse = fits[(run, lead)]
        own, wts = [], []
        for m, v in enumerate(e["v"]):
            if v is not None and m in pooled:
                own.append(v + bias.get((c, m), pooled[m]))
                wts.append(1.0 / mse.get((c, m), pmse[m]))
        if len(own) < sc.MIN_SOURCES:
            continue
        res.append({"city": c, "date": d, "run": run, "lead": lead, "y": y, "prod": float(np.mean(p)),
                    "own": float(np.mean(own)), "own_w": float(np.dot(own, wts) / sum(wts))})
    by_lead = {}
    for lead in (0, 1, 2):
        rs = [r for r in res if r["lead"] == lead]
        by_lead[str(lead)] = {
            "n": len(rs), "run_nights": len({r["run"] for r in rs}),
            **{f"mae_{k}": round(float(np.mean([abs(r["y"] - r[k]) for r in rs])), 4) for k in ("prod", "own", "own_w")},
            "mae_gain_prod_minus_own_w": interval([(r["run"], abs(r["y"] - r["prod"]) - abs(r["y"] - r["own_w"]))
                                                   for r in rs])}
    return res, by_lead


def replay(res):
    """The served evening-before and morning first calls, re-priced."""
    study = {(r["city"], r["date"], r["run"]): r for r in res}
    city_rows = cities()
    bands = {}
    recent = csv.DictReader(gzip.open(os.path.join(ROOT, "data", "training", "market_definitions",
                                                   "bands-recent.csv.gz"), "rt"))
    for r in list(read("bands")) + list(recent):
        bands[r["band_id"]] = {"band_id": r["band_id"], "band_index": int(r["band_index"]),
                               "band_lo": float(r["band_lo"]) if r["band_lo"] else None,
                               "band_hi": float(r["band_hi"]) if r["band_hi"] else None,
                               "open_low": r["open_low"] in TRUE, "open_high": r["open_high"] in TRUE}
    corrected = collections.defaultdict(list)
    for r in read("derived_corrected_forecast"):
        corrected[(r["city_key"], r["for_date"])].append(r)
    first = {}
    for r in read("prediction_checkpoints"):
        k = (r["city_key"], r["target_date"], r["checkpoint"])
        if r["checkpoint"] in ("d1_eve", "morning") and r["target_date"] >= "2026-10-14" and (
                k not in first or (r["decided_at"], r["checkpoint_id"])
                < (first[k]["decided_at"], first[k]["checkpoint_id"])):
            first[k] = r
    outcome = {r["checkpoint_id"]: r for r in read("fact_checkpoint_outcome")}

    def score(probs, win):
        p = max(probs.get(win, 0.0) / sum(probs.values()), 1e-6)
        return -math.log(p), int(sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] == win)

    out = collections.defaultdict(list)
    for (c, d, cp), r in first.items():
        f = outcome.get(r["checkpoint_id"])
        if not f or not f["settled_at"] or not f["winner_band_id"] or f["ladder_has_winner"] not in TRUE:
            continue
        served = json.loads(r["probs"])
        bs = sorted((bands[b] for b in served if b in bands), key=lambda b: b["band_index"])
        live = [z for z in corrected.get((c, d), []) if ts(z["computed_at"]) <= ts(r["decided_at"])]
        if len(bs) != len(served) or not live or c not in city_rows:
            continue
        st = max(live, key=lambda z: z["computed_at"])
        s = study.get((c, d, st["computed_at"][:10]))
        if s is None:
            continue
        floor = float(r["running_max_c"]) if r["running_max_c"] else None
        q = ((float(city_rows[c]["observation_q_down"] or 0), float(city_rows[c]["observation_q_up"] or 0))
             if floor is not None else (0.0, 0.0))

        def lad(centre, sigma):
            return {b: max(1e-6, x) for b, x in pe.compute_band_probabilities(
                centre, sigma, city_rows[c]["unit"], bs, floor_c=floor, q_down=q[0], q_up=q[1])}
        win = f["winner_band_id"]
        whole = f["market_complete"] in TRUE and f["log_loss_market"] not in ("", None)
        row = {"date": d, "served": score(served, win),
               "market": (float(f["log_loss_market"]), int(f["market_hit"] in TRUE)) if whole else None}
        if cp == "d1_eve":
            if not r["centre_c"] or not r["sigma_c"]:
                continue
            centre, sigma = float(r["centre_c"]), float(r["sigma_c"])
            row["check"] = abs(score(lad(centre, sigma), win)[0] - row["served"][0])
            row["own_w"] = score(lad(centre + 0.5 * (s["own_w"] - float(st["combined_c"])), sigma), win)
        else:
            if not st["width_c"]:
                continue
            row["combination"] = score(lad(float(st["combined_c"]), float(st["width_c"])), win)
            row["own_w"] = score(lad(s["own_w"], float(st["width_c"])), win)
        out[cp].append(row)
    summary = {}
    for cp, rs in out.items():
        m = [r for r in rs if r["market"]]
        summary[cp] = {"n": len(rs), "dates": len({r["date"] for r in rs}),
                       "first_date": min(r["date"] for r in rs), "last_date": max(r["date"] for r in rs),
                       "market": {"n": len(m), "log_loss": round(float(np.mean([r["market"][0] for r in m])), 4),
                                  "top1": round(float(np.mean([r["market"][1] for r in m])), 4)}}
        for k in ("served", "combination", "own_w"):
            if k in rs[0]:
                summary[cp][k] = {"log_loss": round(float(np.mean([r[k][0] for r in rs])), 4),
                                  "top1": round(float(np.mean([r[k][1] for r in rs])), 4)}
                if k != "served":
                    summary[cp][k]["log_loss_gain_over_served"] = interval(
                        [(r["date"], r["served"][0] - r[k][0]) for r in rs])
        if cp == "d1_eve":
            summary[cp]["replay_of_served_max_log_loss_difference"] = round(max(r["check"] for r in rs), 6)
    return summary


# ---------------------------------------------------------------------------
# record: the long honest record, lead 1
# ---------------------------------------------------------------------------
def record_arrays(truth):
    rows = list(csv.DictReader(gzip.open(os.path.join(ROOT, "data", "training", "previous_runs",
                                                      "models_daily.csv.gz"), "rt")))
    ck = sorted({r["city_key"] for r in rows})
    d0 = dt.date.fromisoformat(min(r["for_date"] for r in rows))
    T = (dt.date.fromisoformat(max(r["for_date"] for r in rows)) - d0).days + 1
    ci, mi = {c: i for i, c in enumerate(ck)}, {m: i for i, m in enumerate(MODELS)}
    X = {lead: np.full((len(ck), T, len(MODELS)), np.nan) for lead in (1, 2)}
    for r in rows:
        if int(r["lead_days"]) in X and r["tmax_00_17_c"]:
            X[int(r["lead_days"])][ci[r["city_key"]], (dt.date.fromisoformat(r["for_date"]) - d0).days,
                                   mi[r["model"]]] = float(r["tmax_00_17_c"])
    dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(T)]
    Y = np.full((len(ck), T), np.nan)
    for (c, d), y in truth.items():
        if c in ci and d in dates:
            Y[ci[c], dates.index(d)] = y
    return ck, dates, X, Y


def record_recipes(X, Y, dates):
    """{recipe: centre[c, t]} from RECORD_FROM, each day fitted on the W days before it."""
    t0, T = dates.index(RECORD_FROM), Y.shape[1]
    out = {k: np.full(Y.shape, np.nan) for k in ("base", "weights", "ar", "weights_ar", "base_lead2")}
    resid = np.full(Y.shape, np.nan)

    def fit(E):
        ok = np.isfinite(E)
        pooled = np.clip(np.where(ok, E, 0).sum(axis=(0, 1)) / np.maximum(ok.sum(axis=(0, 1)), 1), -BOUND, BOUND)
        n = ok.sum(axis=1)
        dev = np.where(ok, E - pooled[None, None, :], 0).sum(axis=1)
        return np.clip(np.where(n >= MIN_N, pooled[None, :] + dev / (n + K), pooled[None, :]), -BOUND, BOUND)

    def mean(cor):
        return np.where(np.isfinite(cor).sum(axis=1) >= sc.MIN_SOURCES, np.nanmean(cor, axis=1), np.nan)
    with np.errstate(all="ignore"):
        for t in range(t0 - 60, T):
            win = slice(t - W, t)
            E = Y[:, win, None] - X[1][:, win, :]
            bias = fit(E)
            cor = X[1][:, t, :] + bias
            base = mean(cor)
            resid[:, t] = Y[:, t] - base
            if t < t0:
                continue
            R = E - bias[:, None, :]
            mse = (np.nansum(R ** 2, axis=1) + K * np.nanmean(R ** 2, axis=(0, 1))[None, :]) / (
                np.isfinite(R).sum(axis=1) + K)
            wv = np.where(np.isfinite(cor), 1.0 / mse, 0.0)
            weighted = np.where(np.isfinite(cor).sum(axis=1) >= sc.MIN_SOURCES,
                                (np.where(np.isfinite(cor), cor, 0) * wv).sum(axis=1) / wv.sum(axis=1), np.nan)
            a, b = resid[:, t - W:t - 1].ravel(), resid[:, t - W + 1:t].ravel()
            ok = np.isfinite(a) & np.isfinite(b)
            phi = min(max(float((a[ok] * b[ok]).sum() / max((a[ok] ** 2).sum(), 1e-9)), 0.0), 0.6) if ok.sum() > 200 else 0.0
            last = np.nan_to_num(resid[:, t - 1])
            out["base"][:, t], out["weights"][:, t] = base, weighted
            out["ar"][:, t], out["weights_ar"][:, t] = base + phi * last, weighted + phi * last
            out["base_lead2"][:, t] = mean(X[2][:, t, :] + fit(Y[:, win, None] - X[2][:, win, :]))
    return out, t0


def record(truth):
    import market_vs_model as M
    ck, dates, X, Y = record_arrays(truth)
    out, t0 = record_recipes(X, Y, dates)
    ok = np.isfinite(Y[:, t0:])
    for v in out.values():
        ok &= np.isfinite(v[:, t0:])
    mae = {k: round(float(np.abs(Y[:, t0:] - v[:, t0:])[ok].mean()), 4) for k, v in out.items()}
    # the venue's ladders at 00:00, each recipe with a width from its own earlier errors
    old = os.getcwd()
    os.chdir(ROOT)
    try:
        tz = json.load(open(M.RI + "tz.json"))
        city_rows, _ = M.load_cities()
        events, _left = M.load_events(tz, city_rows)
        ladders = M.market_ladders(events)
    finally:
        os.chdir(old)
    widths = {}
    with np.errstate(all="ignore"):
        for k in ("base", "weights", "ar", "weights_ar"):
            E, S = np.abs(Y - out[k]), np.full(Y.shape, np.nan)
            for t in range(t0 + sc.WIDTH_DAYS, Y.shape[1]):
                w = E[:, t - sc.WIDTH_DAYS:t]
                pool, n = np.nanmean(w), np.isfinite(w).sum(axis=1)
                S[:, t] = np.clip(sc.MAE_TO_SIGMA * np.where(n >= MIN_N, (np.nansum(w, axis=1) + sc.WIDTH_K * pool)
                                                             / (n + sc.WIDTH_K), pool), sc.WIDTH_LO_C, sc.WIDTH_HI_C)
            widths[k] = S
    ci, di = {c: i for i, c in enumerate(ck)}, {d: i for i, d in enumerate(dates)}
    rs = []
    for eid, e in events.items():
        if e["city"] not in ci or e["date"] not in di or (eid, "d0_00") not in ladders:
            continue
        i, t = ci[e["city"]], di[e["date"]]
        if not all(np.isfinite(out[k][i, t]) and np.isfinite(widths[k][i, t]) for k in widths):
            continue
        mkt = ladders[(eid, "d0_00")][0]
        row = {"date": e["date"], "market": (M.logloss(mkt, e["winner"]), int(M.top(mkt) == e["winner"]))}
        for k in widths:
            raw = [max(M.MODEL_FLOOR, p) for _b, p in pe.compute_band_probabilities(
                float(out[k][i, t]), float(widths[k][i, t]), e["unit"], e["bands"])]
            p = [x / sum(raw) for x in raw]
            row[k] = (M.logloss(p, e["winner"]), int(M.top(p) == e["winner"]))
        rs.append(row)
    lad = {"n": len(rs), "dates": len({r["date"] for r in rs}),
           "first_date": min(r["date"] for r in rs), "last_date": max(r["date"] for r in rs)}
    for k in ("market", "base", "weights", "ar", "weights_ar"):
        lad[k] = {"log_loss": round(float(np.mean([r[k][0] for r in rs])), 4),
                  "top1": round(float(np.mean([r[k][1] for r in rs])), 4)}
        if k not in ("market", "base"):
            lad[k]["log_loss_gain_over_base"] = interval([(r["date"], r["base"][0] - r[k][0]) for r in rs])
    return {"from": RECORD_FROM, "to": dates[-1], "city_days": int(ok.sum()), "mae_c": mae, "ladders_at_00": lad}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    truth = station_truth(cities())
    res, by_lead = forward(truth)
    out = {"priors": {"k": K, "min_n": MIN_N, "bound_c": BOUND, "window_days": W},
           "forward": {"from_run_night": FORWARD_FROM, "by_lead": by_lead, "served_calls_replayed": replay(res)},
           "record": record(truth)}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "station_weights_study.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
