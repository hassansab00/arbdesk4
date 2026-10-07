"""Does the market's climbing favourite pay after costs, before and during a
city's peak? (WXPredict build, wave F, step F.7; Hassan, 6 Oct: strategies
"decide pre, and during peak for each unique city's peak ... and the market
movement where the likely winner starts climbing rapidly".)

Research only: nothing here prices or trades. It decides whether a momentum
trigger is worth running as a shadow strategy; the live shadow on real asks is
the test that counts.

FIXED HERE, BEFORE ANY RESULT WAS COMPUTED (7 Oct 2026)
-------------------------------------------------------
Data. The venue's own record (data/training/market_history): every closed
  "Highest temperature" event of an active city dated before 2026-09-01 (the
  sealed test starts then), exactly one winner, a contiguous ladder, the main
  listing where an arch twin exists (season_predictability.load_market).
  Hourly YES prices; every price used is stamped before 2026-09-01 00:00Z.
Instants. Every whole local hour 09:00-18:00 of the event's own local day.
  Each is labelled by the hours from the city-month's peak hour
  (derived_weather_peak, the mirror's newest row; computed in Sept from
  observation history, used only for this label): PRE when -3 <= rel < 0,
  DURING when 0 <= rel <= 1; other instants belong to no rule.
The leader at an instant: the bucket with the highest last price at or before
  it, among buckets whose last price is at most 2 h old.
The climb: the leader's last price at or before the instant, minus its last
  price at or before the instant less 1 h (that one at most 2 h older).
Rules, one trade per event and phase at the FIRST instant of the phase where
  the rule fires, one YES share of the leader:
  M_d  climb >= d                                       (the market alone)
  A_d  climb >= d and the forecasts agree: the seven models' lead-1 whole-day
       maxima span under 4.8 C with at least 4 models (status-v1's Watch rule)
  W_d  climb >= d and the leader is the bucket of the bias-corrected lead-1
       forecast (season_predictability's primary: 00-17 maximum, less the
       trailing 30-day bias), rounded half up on the venue's ladder
  for d in (0.10, 0.20) and each phase: 12 rules.
Cost. The entry is the bucket's FIRST price at or after the instant, at most
  1 h later (the signal's own price is never the fill). ask = p + the median
  half-spread (ask - mid) of the archived books observed before 2026-09-01 in
  p's bin; fee = 0.05 x ask x (1 - ask), the venue's taker fee.
  P&L per share = won - ask - fee.
Control, same costs and instants: the leader at the first instant of the phase,
  no condition (the favourite, always).
Judged. Mean P&L per share with a (1 - 0.05/12) interval from a bootstrap over
  dates (SEED 11, BOOT 10,000). A rule is ADOPTED for a shadow strategy only
  when it has at least 100 trades on 30 dates, the interval lies wholly above
  zero, and the mean stays above zero when the 75th-percentile half-spread is
  charged instead. Reported, not judged: P&L at the archived books' real ask
  (first snapshot at or after the instant within 1 h, books before
  2026-09-01), per city, and the rules against the control.
No blinded challenger is read (rd3, da_floor, sd_corr, fec_v1).

    python tools/f7/momentum_study.py            # writes data/eval/f7/momentum_2026-10-07.json
    python tools/f7/momentum_study.py --no-write # prints it
"""
import argparse
import bisect
import calendar
import collections
import datetime as dt
import glob
import json
import os
import random
import statistics
import sys
from array import array
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "focus"))
import season_predictability as sp  # noqa: E402

ROOT = sp.ROOT
OUT = os.path.join(ROOT, "data", "eval", "f7", "momentum_2026-10-07.json")
PEAKS = os.path.join(ROOT, "data", "mirror", "derived_weather_peak", "*.csv.gz")
BOOKS = os.path.join(ROOT, "data", "archive", "books", "books-*.csv.gz")
MIRROR_BANDS = os.path.join(ROOT, "data", "mirror", "bands", "*.csv.gz")
HOURS = range(9, 19)
PHASES = {"pre": (-3.0, 0.0), "during": (0.0, 1.0)}      # [lo, hi) for pre, [lo, hi] for during
LEADER_MAX_AGE_S = 2 * 3600
CLIMB_S = 3600
ENTRY_WITHIN_S = 3600
DELTAS = (0.10, 0.20)
FAMILIES = ("M", "A", "W")
SPAN_C = 4.8
MIN_MODELS = 4
FEE_RATE = 0.05
BINS = [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001]
N_RULES = len(DELTAS) * len(FAMILIES) * len(PHASES)
LEVEL = 1 - 0.05 / N_RULES
BOOT = 10000
SEED = 11
MIN_TRADES, MIN_DATES = 100, 30
VERSION = "f7-momentum-v1"


def fee(x):
    return FEE_RATE * x * (1 - x)


def bin_of(p):
    for i, (lo, hi) in enumerate(zip(BINS, BINS[1:])):
        if lo <= p < hi:
            return i
    return len(BINS) - 2


def spread_model():
    """{bin: (median, p75, n)} of ask - mid over the archived books observed
    before the cutoff."""
    by = collections.defaultdict(list)
    for path in sorted(glob.glob(BOOKS)):
        for r in sp.read_csv(path):
            if r["observed_at"][:10] >= sp.CUTOFF.isoformat():
                continue
            if not r["best_bid"] or not r["best_ask"] or not r["mid"]:
                continue
            bid, ask, mid = float(r["best_bid"]), float(r["best_ask"]), float(r["mid"])
            if not (0 < bid < ask < 1):
                continue
            sp.guard(r["observed_at"][:10], "books (spread)")
            by[bin_of(mid)].append(ask - mid)
    out = {}
    for k, v in sorted(by.items()):
        v.sort()
        out[k] = (v[len(v) // 2], v[(3 * len(v)) // 4], len(v))
    return out


def load_peaks():
    """{(city, month): peak hour local}, the newest mirrored row of each."""
    best = {}
    for path in sorted(glob.glob(PEAKS)):
        for r in sp.read_csv(path):
            if not r["peak_hour_local"]:
                continue
            k = (r["city_key"], int(r["month"]))
            if k not in best or r["computed_at"] >= best[k][0]:
                best[k] = (r["computed_at"], float(r["peak_hour_local"]))
    return {k: v[1] for k, v in best.items()}


def load_prices(events):
    """{(event_id, band_index): (times array, prices array)} sorted by time,
    only prices stamped before the cutoff."""
    t_of, p_of = collections.defaultdict(lambda: array("q")), collections.defaultdict(lambda: array("d"))
    for r in sp.read_csv(os.path.join(sp.MH, "prices.csv.gz")):
        if r["event_id"] not in events:
            continue
        t = int(r["t"])
        if t >= sp.CUTOFF_UTC:
            continue
        k = (r["event_id"], int(r["band_index"]))
        t_of[k].append(t)
        p_of[k].append(float(r["p"]))
    out = {}
    for k in t_of:
        pairs = sorted(zip(t_of[k], p_of[k]))
        out[k] = (array("q", (a for a, _ in pairs)), array("d", (b for _, b in pairs)))
    return out


def last_at_or_before(series, t, max_age):
    ts, ps = series
    i = bisect.bisect_right(ts, t) - 1
    if i < 0 or t - ts[i] > max_age:
        return None
    return ts[i], ps[i]


def first_at_or_after(series, t, within):
    ts, ps = series
    i = bisect.bisect_left(ts, t)
    if i >= len(ts) or ts[i] - t > within:
        return None
    return ts[i], ps[i]


def load_book_asks():
    """{token_yes: (times array, asks array)} from the archived books before
    the cutoff, through the mirror's band -> token map."""
    token_of = {}
    for path in sorted(glob.glob(MIRROR_BANDS)):
        for r in sp.read_csv(path):
            if r.get("token_yes"):
                token_of[r["band_id"]] = r["token_yes"]
    rows = collections.defaultdict(list)
    for path in sorted(glob.glob(BOOKS)):
        for r in sp.read_csv(path):
            if r["observed_at"][:10] >= sp.CUTOFF.isoformat() or not r["best_ask"]:
                continue
            tok = token_of.get(r["band_id"])
            if tok is None:
                continue
            ts = int(dt.datetime.fromisoformat(r["observed_at"]).timestamp())
            if ts >= sp.CUTOFF_UTC:
                continue
            rows[tok].append((ts, float(r["best_ask"])))
    out = {}
    for tok, v in rows.items():
        v.sort()
        out[tok] = (array("q", (a for a, _ in v)), array("d", (b for _, b in v)))
    return out


def forecast_buckets(cities, events):
    """{event_id: bucket index of the bias-corrected lead-1 forecast} and
    {event_id: True/False the forecasts agree}, for the events that have them."""
    fcs, _ = sp.load_forecasts()
    fc = fcs["primary_l1_00_17"]
    whole, _, _ = sp.load_station_days(cities)
    spans = collections.defaultdict(list)
    for r in sp.read_csv(sp.MODELS_DAILY):
        if r["lead_days"] == "1" and r["for_date"] < sp.CUTOFF.isoformat():
            v = sp.num(r["tmax_c"])
            if v is not None:
                spans[(r["city_key"], r["for_date"])].append(v)
    bucket, agree = {}, {}
    for eid, e in events.items():
        key = (e["city"], e["date"])
        ms = spans.get(key) or []
        if ms:
            sp.guard(e["date"], "model forecasts")
        agree[eid] = len(ms) >= MIN_MODELS and max(ms) - min(ms) < SPAN_C
        f = fc.get(key)
        if f is None:
            continue
        d = dt.date.fromisoformat(e["date"])
        b, _ = sp.trailing_bias(fc, whole.get(e["city"], {}), e["city"], d)
        if b is None:
            continue
        sp.guard(e["date"], "forecasts")
        v = f - b
        v = sp.c_to_f(v) if e["unit"] == "F" else v
        bucket[eid] = sp.bucket_of(sp.round_half_up(v), e["bands"])
    return bucket, agree


def instants(e, tz, peak):
    """[(phase, unix instant)] over the event's local day, in time order."""
    d = dt.date.fromisoformat(e["date"])
    out = []
    for h in HOURS:
        t = int(dt.datetime.combine(d, dt.time(h), ZoneInfo(tz)).timestamp())
        if t + ENTRY_WITHIN_S >= sp.CUTOFF_UTC:
            continue
        rel = h - peak
        for ph, (lo, hi) in PHASES.items():
            if (ph == "pre" and lo <= rel < hi) or (ph == "during" and lo <= rel <= hi):
                out.append((ph, t))
    return out


def observe(e, eid, prices, t):
    """(leader band, climb or None, entry price or None) at instant t."""
    best = None
    for i in range(len(e["bands"])):
        s = prices.get((eid, i))
        if s is None:
            continue
        lp = last_at_or_before(s, t, LEADER_MAX_AGE_S)
        if lp is not None and (best is None or lp[1] > best[1]):
            best = (i, lp[1], lp[0])
    if best is None:
        return None
    i, p_now, _ = best
    s = prices[(eid, i)]
    then = last_at_or_before(s, t - CLIMB_S, LEADER_MAX_AGE_S)
    climb = None if then is None else p_now - then[1]
    entry = first_at_or_after(s, t, ENTRY_WITHIN_S)
    if entry is not None:
        sp.guard_t(entry[0], "prices (entry)")
    return i, climb, (None if entry is None else entry[1])


def boot(per_row, level, seed=SEED, n=BOOT):
    by = collections.defaultdict(list)
    for d, v in per_row:
        by[d].append(v)
    dates = sorted(by)
    sums = [(sum(by[d]), len(by[d])) for d in dates]
    mean = sum(s for s, _ in sums) / sum(c for _, c in sums)
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        s = c = 0
        for _i in range(len(dates)):
            a, b = sums[rng.randrange(len(dates))]
            s += a
            c += b
        stats.append(s / c)
    stats.sort()
    tail = (1 - level) / 2
    lo, hi = stats[int(tail * n)], stats[min(n - 1, int((1 - tail) * n))]
    return mean, [lo, hi]


def r4(x):
    return None if x is None else round(x, 4)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    cities = sp.load_cities()
    events, left = sp.load_market(cities)
    peaks = load_peaks()
    spreads = spread_model()
    prices = load_prices(events)
    fbucket, agree = forecast_buckets(cities, events)
    asks = load_book_asks()
    token = {(r["event_id"], int(r["band_index"])): r["token_yes"]
             for r in sp.read_csv(os.path.join(sp.MH, "bands.csv.gz")) if r["event_id"] in events}

    def cost(p, q):
        ask = min(0.999, p + spreads[bin_of(p)][q])
        return ask + fee(ask)

    rules = [(f, d, ph) for f in FAMILIES for d in DELTAS for ph in PHASES]
    trades = {r: [] for r in rules}
    control = {ph: [] for ph in PHASES}
    no_peak = 0
    for eid, e in sorted(events.items()):
        tz = cities[e["city"]]["timezone"]
        peak = peaks.get((e["city"], int(e["date"][5:7])))
        if peak is None or not tz:
            no_peak += 1
            continue
        done = set()
        for ph, t in instants(e, tz, peak):
            o = observe(e, eid, prices, t)
            if o is None:
                continue
            band, climb, entry = o
            if entry is None:
                continue
            won = int(band == e["winner"])
            ask_real = None
            s = asks.get(token.get((eid, band)))
            if s is not None:
                a = first_at_or_after(s, t, ENTRY_WITHIN_S)
                ask_real = None if a is None else a[1]
            row = {"event": eid, "city": e["city"], "date": e["date"], "won": won, "p": entry,
                   "pnl": won - cost(entry, 0), "pnl_p75": won - cost(entry, 1),
                   "pnl_real": None if ask_real is None else won - ask_real - fee(ask_real)}
            if ("control", ph) not in done:
                control[ph].append(row)
                done.add(("control", ph))
            if climb is None:
                continue
            for f, d, rph in rules:
                if rph != ph or (f, d, ph) in done or climb < d - 1e-12:
                    continue
                if f == "A" and not agree.get(eid):
                    continue
                if f == "W" and fbucket.get(eid) != band:
                    continue
                trades[(f, d, ph)].append(row)
                done.add((f, d, ph))

    def summary(rows, judge):
        if not rows:
            return {"trades": 0}
        dates = {r["date"] for r in rows}
        mean, ci = boot([(r["date"], r["pnl"]) for r in rows], LEVEL)
        p75 = statistics.fmean(r["pnl_p75"] for r in rows)
        real = [r for r in rows if r["pnl_real"] is not None]
        out = {"trades": len(rows), "dates": len(dates), "cities": len({r["city"] for r in rows}),
               "hit_rate": r4(statistics.fmean(r["won"] for r in rows)),
               "mean_price": r4(statistics.fmean(r["p"] for r in rows)),
               "pnl_per_share": r4(mean), "interval": [r4(ci[0]), r4(ci[1])],
               "pnl_per_share_p75_spread": r4(p75),
               "real_asks": {"trades": len(real), "dates": len({r["date"] for r in real}),
                             "pnl_per_share": r4(statistics.fmean(r["pnl_real"] for r in real)) if real else None}}
        if judge:
            enough = len(rows) >= MIN_TRADES and len(dates) >= MIN_DATES
            out["adopted"] = bool(enough and ci[0] > 0 and p75 > 0)
            out["why"] = ("too few trades or dates" if not enough else
                          "interval not wholly above zero" if ci[0] <= 0 else
                          "negative at the 75th-percentile spread" if p75 <= 0 else "all three conditions hold")
        return out

    def by_city(rows):
        c = collections.defaultdict(list)
        for r in rows:
            c[r["city"]].append(r)
        return {k: {"trades": len(v), "pnl_per_share": r4(statistics.fmean(x["pnl"] for x in v))}
                for k, v in sorted(c.items())}

    result = {
        "version": VERSION,
        "cutoff": sp.CUTOFF.isoformat(),
        "events": len(events), "events_left_out": left, "events_without_peak_hour": no_peak,
        "first_date": min(e["date"] for e in events.values()),
        "last_date": max(e["date"] for e in events.values()),
        "level": LEVEL, "boot": BOOT, "seed": SEED,
        "spread_model": {str(k): {"median": r4(v[0]), "p75": r4(v[1]), "n": v[2]} for k, v in spreads.items()},
        "control": {ph: summary(rows, False) for ph, rows in control.items()},
        "rules": {f"{f}_{d:.2f}_{ph}": summary(rows, True) for (f, d, ph), rows in trades.items()},
        "by_city": {f"{f}_{d:.2f}_{ph}": by_city(rows) for (f, d, ph), rows in trades.items()},
    }
    result["adopted"] = sorted(k for k, v in result["rules"].items() if v.get("adopted"))
    result["last_date_used"] = max(sp.USED_MAX.values()) if sp.USED_MAX else None
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    if args.no_write:
        sys.stdout.write(text)
    else:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w") as f:
            f.write(text)
        print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
