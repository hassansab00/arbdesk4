"""Plan v2.4 P3.10 part 3.2: does S10's post-peak edge survive the cost of trading?

The S10 replay on the venue's record (docs/S10_VS_MARKET_RECORD_2026-09-28.md)
found the remaining-day model better than the market 1 h after the peak (log
loss 0.663 vs 0.679; top pick 74.3% vs 70.5%). A better forecast pays only if
it beats the price a trade actually pays: the ask, not the quoted price, plus
the venue's taker fee. Research: nothing here prices or trades.

FIXED HERE, BEFORE ANY RESULT WAS SEEN
--------------------------------------
Trades. At postpeak_1h, one YES share of the model's top bucket when
    model_top_prob - cost >= MARGIN,  for each MARGIN in MARGINS (all reported)
  cost = ask + fee(ask);  ask = p + half_spread(p);  fee(x) = 0.05 x (1 - x)
  (the venue's taker fee: execution_cost / engine_orders.FEE_RATE, measured on
  every book the paper engine captured, 16-27 Sep)
  p = the bucket's last hourly price in the record at or before the decision
  time, at most MAX_AGE_H old (the record's price is quoted, not executable).
  Outcome: 1 if the bucket won. P&L per share = outcome - cost.
The spread. The archived books (data/archive/books, 23 Aug - 24 Sep) with a
  bid and an ask: half_spread = ask - mid, its MEDIAN per bin of mid (BINS);
  a sensitivity run charges the 75th percentile instead.
The control, same costs, no model: always one YES share of the market's
  favourite (its highest-priced bucket at the decision). If the control also
  makes money, the edge is the market's own under-confidence
  (docs/MODEL_VS_MARKET_2026-09-28.md, Q5), not S10.
Scores: trades, days, hit rate, mean model probability, mean cost, mean P&L
  per share with a 90% bootstrap interval over dates (BOOT, SEED), total P&L
  per share traded, and by month.

FOUND AFTER THE FIRST RUN (28 Sep, the same day) AND ADDED, SAID SO HERE
------------------------------------------------------------------------
The rule above takes the newest price AT OR BEFORE the decision. The model
acts on readings up to the decision hour, so that price can predate the very
reading the model trades on: measured, it was a median 59 min old, and the
model's top bucket rose 0.077 on average in the next hour. That leaks the
reading into the P&L. Two sections were added after the first run:
  * "after": the FIRST price at or after the decision, at most 1 h later;
  * "real asks": the archived books' own best ask at the first snapshot at or
    after the decision (the record's price is quoted and can be a stale last
    trade on a bucket nobody is trading), on the city-days the books cover.
The first run's numbers stay in the report, labelled as leaking.

READ THIS FIRST: S10's form was chosen on a walk-forward whose test months
overlap this record, the peak hours come from history that includes these
days, a fill at the ask is assumed for one share (depth and queue are not
modelled), and the hourly price may be up to MAX_AGE_H old at the decision.
The live shadow days are the clean test.

Inputs, all committed: data/replay/s10_market_record_2026-09-28.csv.gz (the
replay's rows), data/training/market_history (prices), data/archive/books,
data/replay/inputs_2026-09-26/tz.json.

    python tools/p310_s10_after_costs.py > docs/S10_AFTER_COSTS_2026-09-28.md
"""
import csv
import datetime as dt
import glob
import gzip
import json
import random
import statistics
from collections import defaultdict
from zoneinfo import ZoneInfo

ROWS = "data/replay/s10_market_record_2026-09-28.csv.gz"
PRICES = "data/training/market_history/prices.csv.gz"
BOOKS = "data/archive/books/books-*.csv.gz"
TZ = "data/replay/inputs_2026-09-26/tz.json"
CHECKPOINT = "postpeak_1h"
MARGINS = (0.0, 0.05, 0.10)
MAX_AGE_H = 3
AFTER_S = 3600
MIRROR_BANDS = "data/mirror/bands/*.csv.gz"
FEE_RATE = 0.05
BINS = [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001]
BOOT = 4000
SEED = 11


def read(path):
    with gzip.open(path, "rt", newline="") as f:
        yield from csv.DictReader(f)


def fee(x):
    return FEE_RATE * x * (1 - x)


def bin_of(p):
    for i, (lo, hi) in enumerate(zip(BINS, BINS[1:])):
        if lo <= p < hi:
            return i
    return len(BINS) - 2


def spread_model():
    """{bin: (median, p75, n)} of ask - mid over the archived books."""
    by = defaultdict(list)
    files = sorted(glob.glob(BOOKS))
    for path in files:
        for r in read(path):
            if not r["best_bid"] or not r["best_ask"] or not r["mid"]:
                continue
            bid, ask, mid = float(r["best_bid"]), float(r["best_ask"]), float(r["mid"])
            if not (0 < bid < ask < 1):
                continue
            by[bin_of(mid)].append(ask - mid)
    out = {}
    for k, v in by.items():
        v.sort()
        out[k] = (v[len(v) // 2], v[(3 * len(v)) // 4], len(v))
    return out, files


def load_rows():
    tz = json.load(open(TZ))
    rows = []
    for r in read(ROWS):
        if r["checkpoint"] != CHECKPOINT or not r["model_top"]:
            continue
        local = dt.datetime.fromisoformat(r["decision_local"]).replace(tzinfo=ZoneInfo(tz[r["city_key"]]))
        rows.append(dict(r, t=int(local.timestamp())))
    return rows


def prices_at(rows, after=False):
    """{(row index, 'model'|'market'): p} - each bucket's last price at or
    before the decision, or (after) its first at or after it, within AFTER_S."""
    want = defaultdict(list)                       # (event_id, band_index) -> [(row index, kind, t)]
    for i, r in enumerate(rows):
        for kind in ("model", "market"):
            b = r["model_top"] if kind == "model" else r["market_top"]
            if b:
                eid, idx = b.split(":")
                want[(eid, idx)].append((i, kind, r["t"]))
    best = {}
    for p in read(PRICES):
        key = (p["event_id"], p["band_index"])
        if key not in want:
            continue
        t = int(p["t"])
        for i, kind, td in want[key]:
            if after:
                if td <= t <= td + AFTER_S and ((i, kind) not in best or t < best[(i, kind)][0]):
                    best[(i, kind)] = (t, float(p["p"]))
            elif td - MAX_AGE_H * 3600 <= t <= td and ((i, kind) not in best or t >= best[(i, kind)][0]):
                best[(i, kind)] = (t, float(p["p"]))
    return {k: v[1] for k, v in best.items()}, {k: v[0] for k, v in best.items()}


def book_asks(rows):
    """{(row index, 'model'|'market'): best ask} at the first archived book at
    or after the decision, within AFTER_S: record bucket -> token -> the
    database's band (data/mirror/bands) -> data/archive/books."""
    token = {(r["event_id"], r["band_index"]): r["token_yes"]
             for r in read("data/training/market_history/bands.csv.gz")}
    band_of = {}
    for path in sorted(glob.glob(MIRROR_BANDS)):
        for r in read(path):
            if r["token_yes"]:
                band_of[r["token_yes"]] = r["band_id"]
    need = defaultdict(list)
    for i, r in enumerate(rows):
        for kind in ("model", "market"):
            b = r["model_top"] if kind == "model" else r["market_top"]
            if b:
                bid = band_of.get(token.get(tuple(b.split(":"))))
                if bid:
                    need[bid].append((i, kind, r["t"]))
    best = {}
    for path in sorted(glob.glob(BOOKS)):
        for r in read(path):
            if r["band_id"] not in need or not r["best_ask"]:
                continue
            ts = int(dt.datetime.fromisoformat(r["observed_at"]).timestamp())
            for i, kind, td in need[r["band_id"]]:
                if td <= ts <= td + AFTER_S and ((i, kind) not in best or ts < best[(i, kind)][0]):
                    best[(i, kind)] = (ts, float(r["best_ask"]))
    return {k: v[1] for k, v in best.items()}


def boot(per_row):
    by = defaultdict(list)
    for d, v in per_row:
        by[d].append(v)
    dates = sorted(by)
    sums = {d: (sum(v), len(v)) for d, v in by.items()}
    n_all = sum(n for _s, n in sums.values())
    mean = sum(s for s, _n in sums.values()) / n_all
    rng = random.Random(SEED)
    stats = []
    for _ in range(BOOT):
        s = n = 0
        for _i in range(len(dates)):
            a, b = sums[dates[rng.randrange(len(dates))]]
            s += a
            n += b
        stats.append(s / n)
    stats.sort()
    return mean, stats[int(0.05 * BOOT)], stats[int(0.95 * BOOT)]


def trades(rows, px, spreads, which, margin, quantile):
    """quantile: 'median' or 'p75' adds that half-spread to the quoted price;
    'ask' takes px as the executable ask itself."""
    out = []
    for i, r in enumerate(rows):
        p = px.get((i, which))
        if p is None:
            continue
        if quantile == "ask":
            ask = p
        else:
            hs = spreads.get(bin_of(p), (0.0, 0.0, 0))[0 if quantile == "median" else 1]
            ask = min(0.999, p + hs)
        cost = ask + fee(ask)
        bucket = r["model_top"] if which == "model" else r["market_top"]
        q = float(r["model_top_prob"]) if which == "model" else None
        if which == "model" and q - cost < margin:
            continue
        win = 1.0 if bucket == r["winner"] else 0.0
        out.append({"date": r["target_date"], "month": r["target_date"][:7], "q": q, "cost": cost,
                    "p": p, "win": win, "pnl": win - cost})
    return out


def table(p, rows, px, spreads, quantile, title):
    p(f"### {title}")
    p()
    p("| rule | trades | days | hit rate | mean model prob | mean cost | P&L per share [90%] | P&L total |")
    p("|---|---|---|---|---|---|---|---|")
    verdicts = []
    for margin in MARGINS:
        t = trades(rows, px, spreads, "model", margin, quantile)
        if not t:
            p(f"| S10 top, margin {margin:.2f} | 0 | | | | | | |")
            continue
        m = boot([(x["date"], x["pnl"]) for x in t])
        verdicts.append((margin, m, len(t)))
        p(f"| S10 top, margin {margin:.2f} | {len(t):,} | {len({x['date'] for x in t})} | "
          f"{100 * sum(x['win'] for x in t) / len(t):.1f}% | {statistics.mean(x['q'] for x in t):.3f} | "
          f"{statistics.mean(x['cost'] for x in t):.3f} | {m[0]:+.4f} [{m[1]:+.4f}, {m[2]:+.4f}] | "
          f"{sum(x['pnl'] for x in t):+.1f} |")
    c = trades(rows, px, spreads, "market", 0.0, quantile)
    if c:
        m = boot([(x["date"], x["pnl"]) for x in c])
        p(f"| control: the market's favourite, always | {len(c):,} | {len({x['date'] for x in c})} | "
          f"{100 * sum(x['win'] for x in c) / len(c):.1f}% | - | {statistics.mean(x['cost'] for x in c):.3f} | "
          f"{m[0]:+.4f} [{m[1]:+.4f}, {m[2]:+.4f}] | {sum(x['pnl'] for x in c):+.1f} |")
    p()
    return verdicts


def main():
    spreads, files = spread_model()
    rows = load_rows()
    before, before_t = prices_at(rows)
    after, _ = prices_at(rows, after=True)
    asks = book_asks(rows)
    p = print
    p("# S10 after the cost of trading, on the venue's record (28 Sep 2026)")
    p()
    p("Generated by `tools/p310_s10_after_costs.py` from committed inputs only. The trade rule, the cost "
      "model, the margins and the control were fixed in the tool's docstring before the first run; the "
      "'after' and 'real asks' sections were added after it, when the first rule was found to leak (the "
      "docstring says so). Research only: nothing here prices or trades.")
    p()
    p("## Read this first")
    p()
    ages = sorted((rows[i]["t"] - t) / 60 for (i, k), t in before_t.items() if k == "model")
    rise = [after[(i, "model")] - before[(i, "model")] for (i, k) in before
            if k == "model" and (i, "model") in after]
    p(f"- **The first rule leaks.** It priced each trade at the newest price at or before the decision, a median "
      f"{ages[len(ages) // 2]:.0f} min old; the model acts on readings up to the decision hour, and the model's "
      f"top bucket rose {statistics.mean(rise):+.3f} on average in the hour after ({len(rise):,} rows). Its "
      "numbers are shown last, labelled.")
    p("- **Not independent of S10's design.** Its form was chosen on a walk-forward whose test months overlap "
      "this record; the peak hours come from history that includes these days. The live shadow days are the "
      "clean test.")
    p("- **One share at the ask.** Depth, queue and partial fills are not modelled.")
    p()
    p("## The cost model")
    p()
    p(f"Half-spread (ask - mid) over the archived books with a bid and an ask ({len(files)} files, "
      "23 Aug - 24 Sep); taker fee 0.05 x price x (1 - price).")
    p()
    p("| mid | books | median | 75th percentile |")
    p("|---|---|---|---|")
    for k in sorted(spreads):
        med, p75, n = spreads[k]
        p(f"| [{BINS[k]:.1f}, {min(BINS[k + 1], 1):.1f}) | {n:,} | {med:.4f} | {p75:.4f} |")
    p()
    p(f"Post-peak rows: {len(rows):,}. A price for the model's top bucket: before the decision "
      f"{sum(1 for k in before if k[1] == 'model'):,}; after it {sum(1 for k in after if k[1] == 'model'):,}; "
      f"an archived book's ask after it {sum(1 for k in asks if k[1] == 'model'):,}.")
    p()
    p("## 1. Real asks: the archived books, first snapshot at or after the decision")
    p()
    real = table(p, rows, asks, spreads, "ask", "The executable ask (no half-spread added)")
    common = [k for k in asks if k[1] == "model" and k in after]
    if common:
        gap = [asks[k] - after[k] for k in common]
        p("### The same rows, priced both ways")
        p()
        p(f"On the {len(common):,} rows with both a real ask and a record price after the decision, the real ask "
          f"is {statistics.mean(gap):+.4f} above the record's price on average (median "
          f"{statistics.median(gap):+.4f}).")
        p()
        p("| price | trades (margin 0.05) | hit rate | mean cost | P&L per share [90%] |")
        p("|---|---|---|---|---|")
        for label, px_, q in (("real ask", {k: asks[k] for k in common}, "ask"),
                              ("record price + median half-spread", {k: after[k] for k in common}, "median")):
            tr = trades(rows, px_, spreads, "model", 0.05, q)
            if tr:
                m = boot([(x["date"], x["pnl"]) for x in tr])
                p(f"| {label} | {len(tr):,} | {100 * sum(x['win'] for x in tr) / len(tr):.1f}% | "
                  f"{statistics.mean(x['cost'] for x in tr):.3f} | {m[0]:+.4f} [{m[1]:+.4f}, {m[2]:+.4f}] |")
        p()
    p("## 2. The record's price, first at or after the decision, plus the half-spread")
    p()
    aft = table(p, rows, after, spreads, "median", "Half-spread at the median")
    table(p, rows, after, spreads, "p75", "Half-spread at the 75th percentile")
    p("## 3. The first rule, which leaks (price before the decision)")
    p()
    table(p, rows, before, spreads, "median", "Half-spread at the median (leaks: see Read this first)")
    p("## Verdict")
    p()
    for name, v in (("real asks", real), ("record price after the decision", aft)):
        for margin, (m, lo, hi), n in v:
            word = "**positive**" if lo > 0 else ("negative" if hi < 0 else "not shown (the interval spans 0)")
            p(f"- {name}, margin {margin:.2f}: {n:,} trades, {m:+.4f} per share [{lo:+.4f}, {hi:+.4f}]: {word}.")
    p()
    p("The record's price for a bucket the market is not trading can be a stale quote: on the rows where both "
      "exist, the real ask sits above it and the same rule that shows a profit at the record's price shows none "
      "at the real ask. Where the books give the real ask, the sample is the books' own coverage (their "
      "city-days and hours), not every post-peak decision. **No edge after costs is shown.**")
    p()


if __name__ == "__main__":
    main()
