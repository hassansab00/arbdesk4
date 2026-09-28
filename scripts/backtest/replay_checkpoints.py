"""The checkpoint replay (plan v2 P7.3), scored under docs/S10_MAX_TEMP_WINNER.md.

For every city-day the venue confirmed (24 Aug - 25 Sep 2026 on the first run)
and every checkpoint the tick would have written on it, rebuild what was known
AT THE DECISION TIME and score the remaining-day model (P7.2 stage 1) against
the venue's winner, beside:

  market    the book: each band's latest mid at or before the decision time,
            no older than MARKET_MAX_AGE_S; top-1 needs one priced band, log
            loss and Brier need the whole ladder priced (the contract's
            `market_complete`)
  proxy     the floor-atom Gaussian around max(R, the forecast's day maximum),
            width 1.25 x its training MAE - roughly the engine's same-day path
            before its trajectory layer. NOT the engine as it priced: those
            rows (prediction_checkpoints) exist from 24 Sep only.
  forecast  the day-before best_match run's day maximum, read the venue's way
            (top-1 only)
  uniform   1/11 per band

WHAT A ROW MAY KNOW. Readings at or before the decision hour H (the decision
time floored to the hour - never later); the day-before forecast run (P2.9's
record: out before the day began); the models' day-ahead spread; a model
fitted only on days before the MONDAY of the target's week (expanding window,
retrained weekly). The measurement layer uses the engine's pooled defaults
(q_down 0.02, q_up 0.05): the cities' own were fitted on 23 Sep from
settlements that include these days.

d1_eve (18:00 the day before) is not scored: stage 1 needs the day's readings.

    I=data/replay/inputs_2026-09-26
    python scripts/backtest/replay_checkpoints.py $I/replay_inputs.json.gz \
        data/training/previous_runs/best_match_hourly_day1_utc.csv.gz $I/obs_utc.csv.gz \
        $I/labels_whole.json.gz $I/units.json $I/tz.json data/training/previous_runs/models_daily.csv.gz \
        --out-rows data/replay/s10_replay_2026-09-26.csv.gz --out-report docs/S10_REPLAY_2026-09-26.md

The first run's inputs are kept in data/replay/inputs_2026-09-26 (README there):
replay_inputs is tools/p73_replay_inputs.sql's answer; the others are
tools/experiments_p72_stage1.py's (labels whole days only).
"""
import argparse
import bisect
import csv
import datetime as dt
import gzip
import json
import math
import os
import sys
from collections import defaultdict
from multiprocessing import Pool
from statistics import pstdev
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import market_anchor  # noqa: E402
import remaining_day as rd  # noqa: E402
import tick  # noqa: E402
from model_promotion import bootstrap_interval  # noqa: E402
from probability_engine import DEFAULT_Q_DOWN, DEFAULT_Q_UP  # noqa: E402

CONTRACT = "s10-contract-v1"
CHECKPOINTS = [c for c in tick.CHECKPOINTS if c != "d1_eve"]
MARKET_MAX_AGE_S = 3 * 3600
THRESHOLDS = (0.4, 0.5, 0.6)
LOG_FLOOR = 1e-6
MIN_DAYS = 20          # the contract's floor (walk_forward.MIN_GATE_DAYS)
UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------
def _json(path):
    with (gzip.open(path, "rt") if str(path).endswith(".gz") else open(path)) as f:
        return json.load(f)


def load_repo(fc_path, obs_path, labels_path, tz_path, models_path):
    TZ = _json(tz_path)
    Y = {(c, str(d)[:10]): float(m) for c, d, m, *_ in _json(labels_path)}
    fc = defaultdict(lambda: defaultdict(dict))
    with gzip.open(fc_path, "rt") as f:
        for r in csv.DictReader(f):
            if r["city_key"] not in TZ or r["t"] == "":
                continue
            t = dt.datetime.fromisoformat(r["utc"]).replace(tzinfo=UTC).astimezone(ZoneInfo(TZ[r["city_key"]]))
            fc[r["city_key"]][t.date().isoformat()][t.hour] = (
                float(r["t"]), float(r["cloud"]) if r["cloud"] else None, float(r["sw"]) if r["sw"] else None)
    obs = defaultdict(lambda: defaultdict(list))
    with gzip.open(obs_path, "rt") as f:
        for r in csv.DictReader(f):
            c = r["city_key"]
            if c not in TZ:
                continue
            t = dt.datetime.fromisoformat(r["utc"]).replace(tzinfo=UTC).astimezone(ZoneInfo(TZ[c]))
            obs[c][t.date().isoformat()].append((t.hour + t.minute / 60, float(r["temp_c"])))
    sp = defaultdict(list)
    with gzip.open(models_path, "rt") as f:
        for r in csv.DictReader(f):
            if r["lead_days"] == "1" and r["tmax_c"]:
                sp[(r["city_key"], r["for_date"])].append(float(r["tmax_c"]))
    spread = {k: pstdev(v) for k, v in sp.items() if len(v) >= 4}
    med = sorted(spread.values())[len(spread) // 2]
    return fc, obs, Y, spread, med


def training_rows(H, fc, obs, Y, spread, med):
    out = []
    for c, days in obs.items():
        for d, series in days.items():
            y, f = Y.get((c, d)), fc.get(c, {}).get(d)
            if y is None or not f:
                continue
            day = dt.date.fromisoformat(d)
            row = rd.features(series, f, H, day, spread.get((c, d), med))
            if row:
                row.update(city=c, date=day, y=y)
                out.append(row)
    return out


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


# ---------------------------------------------------------------------------
# scoring (pure)
# ---------------------------------------------------------------------------
def wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def log_loss(probs, winner):
    return -math.log(max(probs.get(winner, 0.0), LOG_FLOOR))


def brier(probs, winner):
    return sum((p - (1.0 if b == winner else 0.0)) ** 2 for b, p in probs.items())


def top(probs):
    return max(probs, key=lambda b: (probs[b], b))


def market_probs(band_ids, mids_of, at_epoch):
    """(probs normalised over the ladder or None, top band or None): each
    band's latest mid at or before at_epoch, no older than MARKET_MAX_AGE_S."""
    px = {}
    for b in band_ids:
        best = None
        for t, m in mids_of.get(b, ()):
            if t <= at_epoch and at_epoch - t <= MARKET_MAX_AGE_S and (best is None or t > best[0]):
                best = (t, m)
        if best is not None:
            px[b] = max(0.0, float(best[1]))
    if not px:
        return None, None
    t = max(px, key=lambda b: (px[b], b))
    total = sum(px.values())
    complete = len(px) == len(band_ids) and total > 0
    return ({b: px[b] / total for b in band_ids} if complete else None), t


def index_tops(tops):
    """{band: ([epochs], [(bid, ask)])} sorted by time, from --tops rows
    [band_id, epoch, best_bid, best_ask]."""
    out = defaultdict(list)
    for b, t, bid, ask in tops["rows"]:
        out[b].append((int(t), (bid, ask)))
    index = {}
    for b, v in out.items():
        v.sort(key=lambda r: r[0])
        index[b] = ([t for t, _ in v], [q for _, q in v])
    return index


def engine_market(band_ids, tops_of, at_epoch):
    """The market ladder the engine would read at at_epoch: each band's newest
    top of book at or before it, no older than MARKET_MAX_AGE_S, read by
    market_anchor.market_probs (half the ask for an ask-only book). None
    unless every band is quoted - the engine's own rule."""
    book = {}
    for b in band_ids:
        ts, qs = tops_of.get(b, ((), ()))
        i = bisect.bisect_right(ts, at_epoch) - 1
        if i < 0 or at_epoch - ts[i] > MARKET_MAX_AGE_S:
            return None
        bid, ask = qs[i]
        book[b] = {"bid": bid, "ask": ask}
    return market_anchor.market_probs(book, band_ids)


def proxy_probs(bands, unit, R, fc_day, sigma):
    centre = max(R, fc_day)
    import probability_engine as pe
    return dict(pe.compute_band_probabilities(centre, sigma, unit, bands, floor_c=R,
                                              q_down=DEFAULT_Q_DOWN, q_up=DEFAULT_Q_UP))


def forecast_top(bands, unit, fc_day):
    import probability_engine as pe
    ladder, i = pe.floor_bucket(fc_day, unit, bands)
    return ladder[i]["band_id"] if i is not None else None


# ---------------------------------------------------------------------------
# the replay
# ---------------------------------------------------------------------------
_CACHE = {}


def _fit_job(args):
    H, cutoff, paths = args
    if paths not in _CACHE:                       # once per worker process
        _CACHE.clear()
        _CACHE[paths] = load_repo(*paths)
    fc, obs, Y, spread, med = _CACHE[paths]
    key = (paths, H)
    if key not in _CACHE:
        _CACHE[key] = training_rows(H, fc, obs, Y, spread, med)
    rows = [r for r in _CACHE[key] if r["date"] < cutoff]
    p = rd.fit_hour(rows)
    base = [max(r["R"], r["fc_day"]) for r in rows]
    sigma = max(0.5, sum(abs(b - r["y"]) for b, r in zip(base, rows)) / len(rows) * 1.25) if rows else None
    return (H, cutoff.isoformat()), p, sigma


def plan_rows(inputs):
    """[(market, checkpoint, local decision time, utc decision time)]"""
    peak = {(c, int(m)): h for c, m, h in inputs["peaks"] if h is not None}
    out, notes = [], defaultdict(int)
    for mid, city, date, winner, unit, tz in inputs["markets"]:
        day = dt.date.fromisoformat(date)
        times = tick.decision_times(day, tz, peak.get((city, day.month)))
        for cp in CHECKPOINTS:
            if cp not in times:
                notes[f"{cp}: no measured peak hour"] += 1
                continue
            loc, utc = times[cp]
            out.append(((mid, city, day, winner, unit, tz), cp, loc, utc))
    return out, dict(notes)


def replay(inputs, paths, processes=4, tops=None, ladders=None):
    """ladders: a list to fill, when given with tops, with each row's full
    model ladder and the engine's market ladder (the market-weight evidence,
    P5.3 amended); the rows and the report are unchanged by it."""
    planned, notes = plan_rows(inputs)
    bands_of = defaultdict(list)
    for band_id, market_id, lo, hi, olo, ohi in inputs["bands"]:
        bands_of[market_id].append({"band_id": band_id, "band_lo": lo, "band_hi": hi,
                                    "open_low": bool(olo), "open_high": bool(ohi)})
    mids_of = defaultdict(list)
    for band_id, t, m in inputs["mids"]:
        mids_of[band_id].append((int(t), float(m)))
    jobs = sorted({(loc.hour, week_start(m[2])) for m, cp, loc, utc in planned
                   if rd.HOURS[0] <= loc.hour <= rd.HOURS[-1]})
    with Pool(processes) as pool:
        fits = {k: (p, s) for k, p, s in pool.map(_fit_job, [(H, c, paths) for H, c in jobs])}
    fc, obs, Y, spread, med = load_repo(*paths)
    rows, skipped = [], defaultdict(int)
    for (mid, city, day, winner, unit, tz), cp, loc, utc in planned:
        H = loc.hour
        p, sigma = fits.get((H, week_start(day).isoformat()), (None, None))
        if p is None:
            skipped[f"{cp}: no model for local hour {H}"] += 1
            continue
        d = day.isoformat()
        f = fc.get(city, {}).get(d)
        feat = rd.features(obs.get(city, {}).get(d, []), f, H, day, spread.get((city, d), med)) if f else None
        if feat is None:
            skipped[f"{cp}: too few readings or no forecast run"] += 1
            continue
        bands = bands_of[mid]
        dist = rd.distribution(p, city, feat["x"], feat["R"])
        model = dict(rd.ladder_probabilities(dist, unit, bands, DEFAULT_Q_DOWN, DEFAULT_Q_UP))
        proxy = proxy_probs(bands, unit, feat["R"], feat["fc_day"], sigma)
        mkt, mkt_top = market_probs([b["band_id"] for b in bands], mids_of, utc.timestamp())
        uni = {b["band_id"]: 1.0 / len(bands) for b in bands}
        if ladders is not None and tops is not None:
            ids = [b["band_id"] for b in bands]
            ladders.append({"city_key": city, "target_date": d, "checkpoint": cp, "winner": winner,
                            "model": {b: round(v, 6) for b, v in model.items()},
                            "market": engine_market(ids, tops, utc.timestamp())})
        rows.append({
            "city_key": city, "target_date": d, "checkpoint": cp, "decision_local": loc.isoformat(timespec="minutes"),
            "model_hour": H, "model_version": rd.version_of({H: p}), "contract": CONTRACT, "winner": winner,
            "R": round(feat["R"], 2), "fc_day": round(feat["fc_day"], 2), "median_c": round(rd.median(dist), 2),
            "model_top": top(model), "model_top_prob": round(model[top(model)], 4),
            "model_p_win": round(model.get(winner, 0.0), 4), "model_ll": log_loss(model, winner),
            "model_brier": brier(model, winner),
            "proxy_top": top(proxy), "proxy_ll": log_loss(proxy, winner), "proxy_brier": brier(proxy, winner),
            "forecast_top": forecast_top(bands, unit, feat["fc_day"]),
            "market_top": mkt_top, "market_complete": mkt is not None,
            "market_ll": log_loss(mkt, winner) if mkt else None, "market_brier": brier(mkt, winner) if mkt else None,
            "uniform_ll": log_loss(uni, winner),
        })
    return rows, notes, dict(skipped)


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
def paired(rows, a, b):
    """(mean of b - a per row, n rows, days, 90% moving-block interval over
    per-day means in date order): positive means a had the lower loss."""
    by = defaultdict(list)
    for r in rows:
        if r.get(a) is not None and r.get(b) is not None:
            by[r["target_date"]].append(r[b] - r[a])
    days = sorted(by)
    per_day = [sum(by[d]) / len(by[d]) for d in days]
    flat = [v for d in days for v in by[d]]
    if not flat:
        return None, 0, 0, (None, None)
    # the point estimate is the mean of the per-day means, the same statistic
    # the interval is for (a row-weighted mean can sit outside it)
    return sum(per_day) / len(per_day), len(flat), len(days), bootstrap_interval(per_day)


def summarise(rows, cp):
    rs = [r for r in rows if r["checkpoint"] == cp]
    n = len(rs)
    if not n:
        return None
    out = {"n": n, "days": len({r["target_date"] for r in rs}), "cities": len({r["city_key"] for r in rs})}
    for who in ("model", "proxy", "forecast", "market"):
        called = [r for r in rs if r[f"{who}_top"] is not None]
        k = sum(r[f"{who}_top"] == r["winner"] for r in called)
        out[f"{who}_hit"] = (k, len(called), wilson(k, len(called)))
    out["selective"] = {t: (sum(1 for r in rs if r["model_top_prob"] >= t),
                            sum(1 for r in rs if r["model_top_prob"] >= t and r["model_top"] == r["winner"]))
                        for t in THRESHOLDS}
    rel = defaultdict(lambda: [0, 0, 0.0])
    for r in rs:
        b = min(9, int(r["model_top_prob"] * 10))
        rel[b][0] += 1
        rel[b][1] += r["model_top"] == r["winner"]
        rel[b][2] += r["model_top_prob"]
    out["reliability"] = {b: (v[0], v[1] / v[0], v[2] / v[0]) for b, v in sorted(rel.items())}
    for who in ("model", "proxy", "uniform"):
        out[f"{who}_ll"] = sum(r[f"{who}_ll"] for r in rs) / n
    out["model_brier"] = sum(r["model_brier"] for r in rs) / n
    mk = [r for r in rs if r["market_complete"]]
    out["market_n"] = len(mk)
    if mk:
        out["market_ll"] = sum(r["market_ll"] for r in mk) / len(mk)
        out["model_ll_on_market"] = sum(r["model_ll"] for r in mk) / len(mk)
        out["market_brier"] = sum(r["market_brier"] for r in mk) / len(mk)
        out["model_brier_on_market"] = sum(r["model_brier"] for r in mk) / len(mk)
    out["vs_proxy"] = paired(rs, "model_ll", "proxy_ll")
    out["vs_uniform"] = paired(rs, "model_ll", "uniform_ll")
    out["vs_market"] = paired(mk, "model_ll", "market_ll")
    out["top1_vs_market"] = paired_hits(rs, "market")
    out["top1_vs_forecast"] = paired_hits(rs, "forecast")
    return out


def paired_hits(rows, who):
    """Top-1 on the SAME rows: (n, model hits, other hits, days, 90% interval of
    the per-day mean of model hit - other hit, in date order)."""
    by = defaultdict(list)
    for r in rows:
        if r.get(f"{who}_top") is not None:
            by[r["target_date"]].append((r["model_top"] == r["winner"]) - (r[f"{who}_top"] == r["winner"]))
    days = sorted(by)
    rs = [r for r in rows if r.get(f"{who}_top") is not None]
    if not rs:
        return 0, 0, 0, 0, None, (None, None)
    per_day = [sum(by[d]) / len(by[d]) for d in days]
    return (len(rs), sum(r["model_top"] == r["winner"] for r in rs), sum(r[f"{who}_top"] == r["winner"] for r in rs),
            len(days), sum(per_day) / len(per_day), bootstrap_interval(per_day))


def verdict(p):
    mean, n, days, (lo, hi) = p
    if not n:
        return "no rows"
    if days < MIN_DAYS:
        return f"not judged ({days} days < {MIN_DAYS})"
    if lo is not None and lo > 0:
        return "model better (90% interval above 0)"
    if hi is not None and hi < 0:
        return "model worse (90% interval below 0)"
    return "not shown"


def _pct(k, n):
    return f"{100 * k / n:.1f}%" if n else "-"


def report(rows, notes, skipped, inputs_meta):
    # Inputs built elsewhere than tools/p73_replay_inputs.sql say so (`source`,
    # `market_note`), so a report never names a source it was not built from.
    source = inputs_meta.get("source") or (f"the database export of {inputs_meta['exported_at'][:16]}Z "
                                           "(`tools/p73_replay_inputs.sql`)")
    market_note = inputs_meta.get("market_note") or (
        "- **The market is thinly observed.** Book snapshots with a mid exist for part of the ladder on 18-23 "
        f"of the days per checkpoint; the whole ladder was priced at a decision time on almost no rows, so log "
        f"loss and Brier against the market cannot be judged. The market's top-1 is its highest mid among the "
        f"bands priced within {MARKET_MAX_AGE_S // 3600} h - on a partly priced ladder that can miss its real "
        "favourite.")
    lines = [f"# S10 replay, stage 1 against the venue ({CONTRACT})", "",
             f"Generated by `scripts/backtest/replay_checkpoints.py` from {source}. "
             f"{len(rows)} scored checkpoints; truth is the venue winner only.", "",
             "Not scored: " + ", ".join(f"{k} ({v})" for k, v in sorted({**notes, **skipped}.items())) + ".", "",
             "## Read this first", "",
             "- **Not independent of the model's design.** Stage 1's form and its width grid were chosen on a "
             "walk-forward (station maxima as labels) whose test months include these dates. This replay changes "
             "the truth (the venue's winner) and the unit of scoring (the venue's ladder), not the period. The "
             "shadow days after it (P7.6) are the clean test.",
             market_note,
             "- **The proxy is not the engine.** It is a floor-atom Gaussian around max(R, the day-before "
             "forecast's maximum); the engine's own frozen rows start 24 Sep.",
             "- **Peak hours** (the peak-relative checkpoints) are today's `derived_weather_peak`, computed from "
             "history that includes these days (a modal hour per city-month).",
             "- The day-before forecast run is used throughout; the model has not seen same-day runs.", ""]
    lines += ["| checkpoint | n | days | model top-1 [Wilson 95%] | market top-1 | proxy top-1 | forecast top-1 | "
              "log loss model / proxy / uniform | proxy - model, mean over days [90%] | verdict vs proxy |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    S = {cp: summarise(rows, cp) for cp in CHECKPOINTS}
    for cp, s in S.items():
        if not s:
            continue
        k, n, (lo, hi) = s["model_hit"]
        mk = s["market_hit"]
        vp = s["vs_proxy"]
        lines.append(f"| {cp} | {s['n']} | {s['days']} | {_pct(k, n)} [{100 * lo:.1f}, {100 * hi:.1f}] | "
                     f"{_pct(mk[0], mk[1])} (n {mk[1]}) | {_pct(*s['proxy_hit'][:2])} | "
                     f"{_pct(*s['forecast_hit'][:2])} (n {s['forecast_hit'][1]}) | "
                     f"{s['model_ll']:.3f} / {s['proxy_ll']:.3f} / {s['uniform_ll']:.3f} | "
                     f"{vp[0]:+.3f} [{vp[3][0]:+.3f}, {vp[3][1]:+.3f}] | {verdict(vp)} |")
    lines += ["", "## Top-1 on the same rows", "",
              "The difference is the mean over days of (model hit rate - the other's) that day, with its 90% "
              "moving-block interval; the rates beside it are over rows.", "",
              "| checkpoint | vs market: n (days), model / market | difference | verdict | vs forecast: model / forecast | difference |",
              "|---|---|---|---|---|---|"]
    for cp, s in S.items():
        if not s:
            continue
        n, km, ko, days, dm, (lo, hi) = s["top1_vs_market"]
        fn, fm, fo, fdays, fd, (flo, fhi) = s["top1_vs_forecast"]
        v = ("not judged" if days < MIN_DAYS else "model better" if lo > 0 else "market better" if hi < 0 else "not shown")
        lines.append(f"| {cp} | {n} ({days}) {_pct(km, n)} / {_pct(ko, n)} | "
                     f"{100 * dm:+.1f} pts [{100 * lo:+.1f}, {100 * hi:+.1f}] | {v} | "
                     f"{_pct(fm, fn)} / {_pct(fo, fn)} | {100 * fd:+.1f} pts [{100 * flo:+.1f}, {100 * fhi:+.1f}] |")
    lines += ["", "## Against the market, where the whole ladder was priced", "",
              "| checkpoint | n (days) | log loss model / market | Brier model / market | market - model, mean over days [90%] | verdict |",
              "|---|---|---|---|---|---|"]
    for cp, s in S.items():
        if not s or not s["market_n"]:
            continue
        vm = s["vs_market"]
        lines.append(f"| {cp} | {s['market_n']} ({vm[2]}) | {s['model_ll_on_market']:.3f} / {s['market_ll']:.3f} | "
                     f"{s['model_brier_on_market']:.3f} / {s['market_brier']:.3f} | "
                     f"{vm[0]:+.3f} [{vm[3][0]:+.3f}, {vm[3][1]:+.3f}] | {verdict(vm)} |")
    lines += ["", "## Selective accuracy (model)", "", "| checkpoint | " + " | ".join(f">= {t}" for t in THRESHOLDS) + " |",
              "|---|" + "---|" * len(THRESHOLDS)]
    for cp, s in S.items():
        if s:
            lines.append(f"| {cp} | " + " | ".join(
                f"{_pct(s['selective'][t][1], s['selective'][t][0])} of {s['selective'][t][0]} "
                f"({_pct(s['selective'][t][0], s['n'])} covered)" for t in THRESHOLDS) + " |")
    lines += ["", "## Reliability of the model's top probability", "",
              "| checkpoint | bin: n, realised / stated |", "|---|---|"]
    for cp, s in S.items():
        if s:
            lines.append(f"| {cp} | " + "; ".join(f"{b / 10:.1f}-{(b + 1) / 10:.1f}: {v[0]}, {v[1]:.2f} / {v[2]:.2f}"
                                                   for b, v in s["reliability"].items()) + " |")
    lines += ["", "Rows: `data/replay/`. Proxy, market, forecast and the measurement layer: see the script's docstring.", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs")
    ap.add_argument("fc")
    ap.add_argument("obs")
    ap.add_argument("labels")
    ap.add_argument("units")
    ap.add_argument("tz")
    ap.add_argument("models")
    ap.add_argument("--out-rows")
    ap.add_argument("--out-report")
    ap.add_argument("--tops", help="top-of-book rows [band_id, epoch, best_bid, best_ask] (json.gz)")
    ap.add_argument("--out-ladders", help="each row's model and market ladders (jsonl.gz)")
    a = ap.parse_args(argv)
    inputs = _json(a.inputs)
    tops = index_tops(_json(a.tops)) if a.tops else None
    ladders = [] if a.out_ladders else None
    rows, notes, skipped = replay(inputs, (a.fc, a.obs, a.labels, a.tz, a.models), tops=tops, ladders=ladders)
    if a.out_ladders:
        with gzip.open(a.out_ladders, "wt") as f:
            for r in ladders:
                f.write(json.dumps(r, sort_keys=True) + "\n")
    text = report(rows, notes, skipped, inputs)
    if a.out_rows:
        os.makedirs(os.path.dirname(a.out_rows), exist_ok=True)
        with gzip.open(a.out_rows, "wt", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            for r in rows:
                w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})
    if a.out_report:
        open(a.out_report, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
