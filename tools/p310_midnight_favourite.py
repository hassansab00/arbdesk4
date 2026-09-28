"""Plan v2.4 P3.10 part 6: at the city's midnight, does buying the market's
favourite win enough to pay for itself?

Hassan, 28 Sep: "we need our predictive model to win the single max temp
winner". The one positive finding on the venue's record is the market's own
under-confidence at 00:00 local (docs/MODEL_VS_MARKET_2026-09-28.md: the
market recalibrated, market^a, beats the market, +0.0031 [+0.0011, +0.0050];
buckets priced 0.50-0.60 won 60.7% [57.8, 63.5]); at 08:00 it is gone. If the
midnight favourite is under-priced, the rule that picks it is a winner-first
rule. This asks whether it survives the cost of trading. Research: nothing
here trades.

FIXED HERE, BEFORE ANY RESULT WAS SEEN
--------------------------------------
Decisions: 00:00 local on the day (d0_00); 08:00 (d0_08) and 18:00 the evening
  before (d1_eve) beside it. The scorable events of tools/market_vs_model.py.
Prices: each bucket's FIRST price at or after the decision, at most AFTER_MIN
  later (never one before: #242); a city-day counts only when every bucket has
  one. The favourite is the bucket with the highest price.
The trade: one YES share of the favourite; cost = ask + fee(ask), ask = p +
  the archived books' median half-spread at p (p310_s10_after_costs), fee
  0.05 x (1 - x). P&L = 1 if it won, minus the cost.
Rule A: the favourite whenever its price lies in a band, each of BANDS and
  all prices; every band is reported (no choosing).
Rule B, walk-forward: each calendar month, the band of BANDS with the best mean
  P&L over every earlier month with at least MIN_TRADES trades there; no trade
  that month if that mean is not positive.
Real asks: on the city-days the archived books cover (23 Aug - 24 Sep), the
  favourite's own best ask at the first snapshot at or after the decision
  (within AFTER_MIN), instead of the record's price plus the half-spread.
Scores: trades, days, hit rate, mean price, mean cost, P&L per share with a 90%
  bootstrap over dates, total.

READ THIS FIRST (kept in the report): the record's price is quoted; the midnight
market is thin, and a quote on a thin book may not be fillable at size. One
share is assumed.

    python tools/p310_midnight_favourite.py > docs/MIDNIGHT_FAVOURITE_2026-09-28.md
"""
import datetime as dt
import glob
import json
import os
import statistics
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_vs_model as mvm              # noqa: E402
import p310_s10_after_costs as costs       # noqa: E402

DECISIONS = {"d1_eve": (-1, 18), "d0_00": (0, 0), "d0_08": (0, 8)}
AFTER_MIN = 60
BANDS = [(0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.95)]
MIN_TRADES = 200


def decision_times(events, tz):
    out = {}
    for eid, e in events.items():
        zone = ZoneInfo(tz[e["city"]])
        day = dt.date.fromisoformat(e["date"])
        for k, (dd, h) in DECISIONS.items():
            out[(eid, k)] = int(dt.datetime.combine(day + dt.timedelta(days=dd), dt.time(h), zone).timestamp())
    return out


def ladders_after(events, times):
    """{(event_id, decision): {band_index: first price at or after, within AFTER_MIN}} (complete ladders only)."""
    best = {}
    for r in mvm.read_csv(mvm.MH + "prices.csv.gz"):
        eid = r["event_id"]
        if eid not in events:
            continue
        t = int(r["t"])
        for k in DECISIONS:
            td = times[(eid, k)]
            if td <= t <= td + AFTER_MIN * 60:
                key = (eid, k, int(r["band_index"]))
                if key not in best or t < best[key][0]:
                    best[key] = (t, float(r["p"]))
    out = {}
    for eid, e in events.items():
        for k in DECISIONS:
            ps = {b["band_id"]: best.get((eid, k, b["band_id"])) for b in e["bands"]}
            if all(v is not None for v in ps.values()):
                out[(eid, k)] = {i: v[1] for i, v in ps.items()}
    return out


def book_asks(events, times, favourites):
    """{(event_id, decision): the favourite's real best ask} from the archived books."""
    token = {(r["event_id"], int(r["band_index"])): r["token_yes"] for r in mvm.read_csv(mvm.MH + "bands.csv.gz")}
    band_of = {}
    for path in sorted(glob.glob(costs.MIRROR_BANDS)):
        for r in costs.read(path):
            if r["token_yes"]:
                band_of[r["token_yes"]] = r["band_id"]
    need = defaultdict(list)
    for (eid, k), fav in favourites.items():
        bid = band_of.get(token.get((eid, fav)))
        if bid:
            need[bid].append((eid, k, times[(eid, k)]))
    best = {}
    for path in sorted(glob.glob(costs.BOOKS)):
        for r in costs.read(path):
            if r["band_id"] not in need or not r["best_ask"]:
                continue
            ts = int(dt.datetime.fromisoformat(r["observed_at"]).timestamp())
            for eid, k, td in need[r["band_id"]]:
                if td <= ts <= td + AFTER_MIN * 60 and ((eid, k) not in best or ts < best[(eid, k)][0]):
                    best[(eid, k)] = (ts, float(r["best_ask"]))
    return {k: v[1] for k, v in best.items()}


def trade(e, fav, price, spreads, real_ask=None):
    ask = real_ask if real_ask is not None else min(0.999, price + spreads.get(costs.bin_of(price), (0.0, 0.0, 0))[0])
    cost = ask + costs.fee(ask)
    win = 1.0 if e["winner"] == fav else 0.0
    return {"date": e["date"], "month": e["date"][:7], "p": price, "cost": cost, "win": win, "pnl": win - cost}


def row(p, label, t):
    if not t:
        p(f"| {label} | 0 | | | | | | |")
        return
    m = costs.boot([(x["date"], x["pnl"]) for x in t])
    p(f"| {label} | {len(t):,} | {len({x['date'] for x in t})} | {100 * sum(x['win'] for x in t) / len(t):.1f}% | "
      f"{statistics.mean(x['p'] for x in t):.3f} | {statistics.mean(x['cost'] for x in t):.3f} | "
      f"{m[0]:+.4f} [{m[1]:+.4f}, {m[2]:+.4f}] | {sum(x['pnl'] for x in t):+.1f} |")


def main():
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    events, left = mvm.load_events(tz, cities)
    times = decision_times(events, tz)
    lad = ladders_after(events, times)
    spreads, _ = costs.spread_model()
    favs = {key: max(ps, key=lambda i: (ps[i], -i)) for key, ps in lad.items()}
    asks = book_asks(events, times, favs)
    p = print
    p("# The market's favourite at midnight, after the cost of trading (28 Sep 2026)")
    p()
    p("Generated by `tools/p310_midnight_favourite.py` from committed inputs only (the record, the archived books). "
      "The decisions, bands, walk-forward rule and costs are fixed in the tool's docstring, written before any "
      "result. Research only.")
    p()
    p("## Read this first")
    p()
    p("- **Quoted, then real.** The main tables charge the record's price plus the archived books' median "
      "half-spread; the last table uses the books' own best ask, on the city-days they cover.")
    p("- **Thin hours.** The midnight market is thin; a quote there may not fill at size. One share is assumed.")
    p()
    for k in DECISIONS:
        keys = [key for key in favs if key[1] == k]
        p(f"## `{k}` ({len(keys):,} city-days with a whole ladder priced within {AFTER_MIN} min after the decision)")
        p()
        p("### Rule A: the favourite, by its price")
        p()
        p("| band | trades | days | hit rate | mean price | mean cost | P&L per share [90%] | P&L total |")
        p("|---|---|---|---|---|---|---|---|")
        by_band = defaultdict(list)
        all_t = []
        for key in keys:
            eid = key[0]
            fav = favs[key]
            price = lad[key][fav]
            t = trade(events[eid], fav, price, spreads)
            all_t.append(t)
            for lo, hi in BANDS:
                if lo <= price < hi:
                    by_band[(lo, hi)].append(t)
        for lo, hi in BANDS:
            row(p, f"[{lo:.2f}, {hi:.2f})", by_band[(lo, hi)])
        row(p, "all prices", all_t)
        p()
        p("### Rule B: the band chosen on the months before")
        p()
        months = sorted({t["month"] for t in all_t})
        chosen, picked = [], []
        for mo in months:
            prior = {b: [t for t in v if t["month"] < mo] for b, v in by_band.items()}
            cands = [(statistics.mean(x["pnl"] for x in v), b) for b, v in prior.items() if len(v) >= MIN_TRADES]
            if not cands:
                chosen.append((mo, None, None))
                continue
            m, b = max(cands)
            if m <= 0:
                chosen.append((mo, None, m))
                continue
            chosen.append((mo, b, m))
            picked += [t for t in by_band[b] if t["month"] == mo]
        p("| month | band chosen | its mean P&L before the month |")
        p("|---|---|---|")
        for mo, b, m in chosen:
            p(f"| {mo} | {'none' if b is None else f'[{b[0]:.2f}, {b[1]:.2f})'} | "
              f"{'' if m is None else f'{m:+.4f}'} |")
        p()
        p("| rule | trades | days | hit rate | mean price | mean cost | P&L per share [90%] | P&L total |")
        p("|---|---|---|---|---|---|---|---|")
        row(p, "Rule B", picked)
        p()
        real = [trade(events[key[0]], favs[key], lad[key][favs[key]], spreads, real_ask=asks[key])
                for key in keys if key in asks]
        p("### The books' real ask (city-days the books cover)")
        p()
        p("| rule | trades | days | hit rate | mean price | mean cost | P&L per share [90%] | P&L total |")
        p("|---|---|---|---|---|---|---|---|")
        row(p, "the favourite, all prices, at the real ask", real)
        for lo, hi in BANDS:
            row(p, f"[{lo:.2f}, {hi:.2f}), at the real ask", [t for t in real if lo <= t["p"] < hi])
        p()


if __name__ == "__main__":
    main()
