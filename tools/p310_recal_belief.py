"""Plan v2.4 P3.10 part 3.3: the market's own under-confidence as a belief,
judged after the cost of trading, on both sides of every bucket.

The one positive finding on the venue's record is that the market is
under-confident at 00:00 local: its ladder raised to a power a > 1 and
renormalised (recal) beats it, +0.0031 log loss [+0.0011, +0.0050]
(docs/MODEL_VS_MARKET_2026-09-28.md). Part 6 tested one trade that follows
from it, buying the favourite, and found no edge at the books' real ask
(docs/MIDNIGHT_FAVOURITE_2026-09-28.md). The belief implies more than that:
where recal lowers a long shot below its NO-side cost, it sells it. This asks
the plan's own question: as a belief the engine could trade, does recal make
money after the spread and the fee? Research only: nothing here trades.

FIXED HERE, BEFORE ANY RESULT WAS SEEN
--------------------------------------
Decisions: 00:00 local on the day (d0_00); 18:00 the evening before (d1_eve)
  and 08:00 (d0_08) beside it. The scorable events of tools/market_vs_model.py.
Prices: each bucket's FIRST price at or after the decision, at most AFTER_MIN
  later (p310_midnight_favourite.ladders_after; never one before: #242); a
  city-day counts only when every bucket has one. The belief is formed from
  the same prices it trades against.
The belief: p = the ladder's prices, floored at 0.0005 and normalised;
  q = p^a normalised. a is fitted by maximum likelihood (market_vs_model
  fit_pool, market only) on every earlier calendar month of the same
  decision, with at least MIN_TRAIN city-days; a month without that is not
  scored.
Costs: YES ask = price + the archived books' median half-spread at that
  price; NO ask = 1 - price + the same half-spread (a NO ask is one minus the
  YES bid); fee 0.05 x (1 - x) on the ask (p310_s10_after_costs). The
  platform's price rail applies: nothing is bought above 0.97 (P5.9).
The trade: one share of a side of a bucket whenever the belief's value of it
  exceeds its cost by more than M: YES when q - cost_yes > M, NO when
  (1 - q) - cost_no > M. P&L = 1 if the side won, minus the cost.
Rule A: each M of MARGINS, each side (YES, NO, both); every one is reported.
Rule B, walk-forward: each month, the (side, M) of Rule A with the best mean
  P&L over every earlier month with at least MIN_TRADES trades; no trade that
  month if that mean is not positive.
Real asks: on the city-days the archived books cover (23 Aug - 24 Sep), each
  side's own best ask at the first snapshot at or after the decision (within
  AFTER_MIN), instead of the record's price plus the half-spread; Rule A.
Check first: the belief's log loss against the market's on the same ladders,
  walk-forward, so the under-confidence is shown to hold on these prices.
Scores: trades, days, hit rate, mean cost, mean claimed edge, P&L per share
  with a 90% bootstrap over dates, total.

ADDED AFTER THE RESULTS ABOVE WERE READ (28 Sep, disclosed as such): at 00:00
  the quoted prices gave a gain the real asks did not. The hypothesis that it
  came from stale ladders (quotes summing well away from one, so normalising
  lifts a quote into an "edge") is tested by splitting the 00:00 trades at
  M = 0.02, both sides, by the sum of the ladder's quoted prices.

    python tools/p310_recal_belief.py > docs/RECAL_BELIEF_2026-09-28.md
"""
import datetime as dt
import glob
import json
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_vs_model as mvm               # noqa: E402
import p310_midnight_favourite as mid       # noqa: E402
import p310_s10_after_costs as costs        # noqa: E402

DECISIONS = mid.DECISIONS
AFTER_MIN = mid.AFTER_MIN
MARGINS = (0.0, 0.02, 0.05)
SIDES = ("YES", "NO", "both")
PRICE_RAIL = 0.97
MIN_TRAIN = mvm.MIN_TRAIN
MIN_TRADES = 200


def belief(p, a):
    lg = [a * math.log(x) for x in p]
    top = max(lg)
    ex = [math.exp(v - top) for v in lg]
    s = sum(ex)
    return [v / s for v in ex]


def normalise(raw):
    fl = [max(mvm.PRICE_FLOOR, x) for x in raw]
    s = sum(fl)
    return [x / s for x in fl]


def sides(raw, q, spreads, asks=None):
    """[(bucket index, side, cost, edge)] for every side the rail allows.
    `asks`, when given, is {(bucket index, side): real ask}; a side without one is left out."""
    out = []
    for i, (x, qi) in enumerate(zip(raw, q)):
        hs = spreads.get(costs.bin_of(x), (0.0, 0.0, 0))[0]
        for side, value, ask in (("YES", qi, x + hs), ("NO", 1 - qi, 1 - x + hs)):
            if asks is not None:
                ask = asks.get((i, side))
                if ask is None:
                    continue
            ask = min(ask, 0.999)
            if ask > PRICE_RAIL:
                continue
            cost = ask + costs.fee(ask)
            out.append((i, side, cost, value - cost))
    return out


def pnl(side, i, winner, cost):
    won = (i == winner) if side == "YES" else (i != winner)
    return (1.0 if won else 0.0) - cost, won


def fits(rows):
    """{month: a} for each month with MIN_TRAIN earlier city-days."""
    months = sorted({r["month"] for r in rows})
    out = {}
    for mo in months:
        train = [(r["p"], r["p"], r["w"]) for r in rows if r["month"] < mo]
        if len(train) >= MIN_TRAIN:
            out[mo] = mvm.fit_pool(train, with_model=False)[0]
    return out


def book_asks(events, times, keys):
    """{(event_id, decision): {(bucket index, side): the side's best ask}} from the archived books."""
    token = {(r["event_id"], int(r["band_index"])): r["token_yes"]
             for r in mvm.read_csv(mvm.MH + "bands.csv.gz")}
    band_of = {}
    for path in sorted(glob.glob(costs.MIRROR_BANDS)):
        for r in costs.read(path):
            if r["token_yes"]:
                band_of[r["token_yes"]] = r["band_id"]
    need = defaultdict(list)
    for eid, k in keys:
        for b in events[eid]["bands"]:
            bid = band_of.get(token.get((eid, b["band_id"])))
            if bid:
                need[bid].append((eid, k, b["band_id"], times[(eid, k)]))
    best = {}
    for path in sorted(glob.glob(costs.BOOKS)):
        for r in costs.read(path):
            if r["band_id"] not in need:
                continue
            ts = int(dt.datetime.fromisoformat(r["observed_at"]).timestamp())
            for eid, k, i, td in need[r["band_id"]]:
                if not td <= ts <= td + AFTER_MIN * 60:
                    continue
                for side, col in (("YES", "best_ask"), ("NO", "no_best_ask")):
                    if r[col] and ((eid, k, i, side) not in best or ts < best[(eid, k, i, side)][0]):
                        best[(eid, k, i, side)] = (ts, float(r[col]))
    out = defaultdict(dict)
    for (eid, k, i, side), (_ts, ask) in best.items():
        out[(eid, k)][(i, side)] = ask
    return out


def row(p, label, t):
    if not t:
        p(f"| {label} | 0 | | | | | | |")
        return
    m = costs.boot([(x["date"], x["pnl"]) for x in t])
    p(f"| {label} | {len(t):,} | {len({x['date'] for x in t})} | {100 * sum(x['won'] for x in t) / len(t):.1f}% | "
      f"{statistics.mean(x['cost'] for x in t):.3f} | {statistics.mean(x['edge'] for x in t):+.3f} | "
      f"{m[0]:+.4f} [{m[1]:+.4f}, {m[2]:+.4f}] | {sum(x['pnl'] for x in t):+.1f} |")


def trades_of(r, cands):
    out = []
    for i, side, cost, edge in cands:
        v, won = pnl(side, i, r["w"], cost)
        out.append({"date": r["date"], "month": r["month"], "side": side, "cost": cost, "edge": edge,
                    "pnl": v, "won": won})
    return out


def pick(t, side, m):
    return [x for x in t if x["edge"] > m and (side == "both" or x["side"] == side)]


def main():
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    events, _left = mvm.load_events(tz, cities)
    times = mid.decision_times(events, tz)
    lad = mid.ladders_after(events, times)
    spreads, _ = costs.spread_model()
    p = print
    p("# The market's own under-confidence as a belief, after the cost of trading (28 Sep 2026)")
    p()
    p("Generated by `tools/p310_recal_belief.py` from committed inputs only (the record, the archived books). "
      "The decisions, the belief, the costs and both rules are fixed in the tool's docstring, written before any "
      "result. Research only.")
    p()
    p("## Read this first")
    p()
    p("- **Quoted, then real.** The main tables charge the record's price plus the archived books' median "
      "half-spread on each side; the last table uses each side's own best ask in the books, on the city-days "
      "they cover.")
    p("- **Many trades a city-day.** Every side of every bucket whose value beats its cost is a trade, one share "
      "each, so trades on one city-day are not independent; the interval resamples whole dates.")
    p(f"- **The rail.** Nothing is bought above {PRICE_RAIL} (P5.9), as on the platform.")
    p()
    by_k = {}
    for k in DECISIONS:
        rows = []
        for (eid, kk), ps in lad.items():
            if kk != k:
                continue
            e = events[eid]
            raw = [ps[b["band_id"]] for b in e["bands"]]
            w = [b["band_id"] for b in e["bands"]].index(e["winner"])
            rows.append({"eid": eid, "date": e["date"], "month": e["date"][:7], "raw": raw,
                         "p": normalise(raw), "w": w})
        a_of = fits(rows)
        by_k[k] = (rows, a_of, [r for r in rows if r["month"] in a_of])
    asks = book_asks(events, times, [(r["eid"], k) for k, (_r, _a, sc) in by_k.items() for r in sc])
    for k in DECISIONS:
        rows, a_of, scored = by_k[k]
        p(f"## `{k}` ({len(rows):,} city-days with a whole ladder priced within {AFTER_MIN} min after the "
          f"decision; {len(scored):,} in months with a walk-forward fit)")
        p()
        p("| month | a fitted on the months before |")
        p("|---|---|")
        for mo in sorted(a_of):
            p(f"| {mo} | {a_of[mo]:.3f} |")
        p()
        ll = [(r["date"], -math.log(r["p"][r["w"]]) + math.log(belief(r["p"], a_of[r["month"]])[r["w"]]))
              for r in scored]
        if ll:
            m = costs.boot(ll)
            p(f"Check: log loss of the market minus the belief's, per city-day: {m[0]:+.4f} [{m[1]:+.4f}, "
              f"{m[2]:+.4f}] (positive: the belief is better).")
            p()
        allt = []
        for r in scored:
            q = belief(r["p"], a_of[r["month"]])
            allt += trades_of(r, sides(r["raw"], q, spreads))
        p("### Rule A: every side whose value beats its cost by more than M")
        p()
        p("| side, M | trades | days | hit rate | mean cost | mean claimed edge | P&L per share [90%] | P&L total |")
        p("|---|---|---|---|---|---|---|---|")
        for side in SIDES:
            for mg in MARGINS:
                row(p, f"{side}, M = {mg:.2f}", pick(allt, side, mg))
        p()
        p("### Rule B: the side and M chosen on the months before")
        p()
        chosen, picked = [], []
        for mo in sorted({x["month"] for x in allt}):
            cands = []
            for side in SIDES:
                for mg in MARGINS:
                    prior = [x for x in pick(allt, side, mg) if x["month"] < mo]
                    if len(prior) >= MIN_TRADES:
                        cands.append((statistics.mean(x["pnl"] for x in prior), side, mg))
            if not cands or max(cands)[0] <= 0:
                chosen.append((mo, None, max(cands)[0] if cands else None))
                continue
            best = max(cands)
            chosen.append((mo, (best[1], best[2]), best[0]))
            picked += [x for x in pick(allt, best[1], best[2]) if x["month"] == mo]
        p("| month | side, M chosen | its mean P&L before the month |")
        p("|---|---|---|")
        for mo, c, m in chosen:
            p(f"| {mo} | {'none' if c is None else f'{c[0]}, {c[1]:.2f}'} | {'' if m is None else f'{m:+.4f}'} |")
        p()
        p("| rule | trades | days | hit rate | mean cost | mean claimed edge | P&L per share [90%] | P&L total |")
        p("|---|---|---|---|---|---|---|---|")
        row(p, "Rule B", picked)
        p()
        if k == "d0_00":
            p("### Added after the results were read: the 00:00 trades at M = 0.02 by the ladder's quoted sum")
            p()
            p("| ladder's quoted sum | city-days | trades | days | hit rate | mean cost | mean claimed edge | "
              "P&L per share [90%] | P&L total |")
            p("|---|---|---|---|---|---|---|---|---|")
            for lo, hi in ((0.0, 0.95), (0.95, 1.05), (1.05, 99.0)):
                inside = [r for r in scored if lo <= sum(r["raw"]) < hi]
                t = [x for r in inside for x in pick(trades_of(r, sides(r["raw"], belief(r["p"], a_of[r["month"]]),
                                                                        spreads)), "both", 0.02)]
                buf = []
                row(buf.append, "", t)
                label = f"[{lo:.2f}, {hi:.2f})" if hi < 99 else f"{lo:.2f} or more"
                p(f"| {label} | {len(inside):,} |" + buf[0][len("|  |"):])
            p()
        real = []
        for r in scored:
            got = asks.get((r["eid"], k))
            if got:
                real += trades_of(r, sides(r["raw"], belief(r["p"], a_of[r["month"]]), spreads, asks=got))
        p(f"### The books' real asks ({len({x['date'] for x in real})} dates the books cover)")
        p()
        p("| side, M | trades | days | hit rate | mean cost | mean claimed edge | P&L per share [90%] | P&L total |")
        p("|---|---|---|---|---|---|---|---|")
        for side in SIDES:
            for mg in MARGINS:
                row(p, f"{side}, M = {mg:.2f}, real ask", pick(real, side, mg))
        p()


if __name__ == "__main__":
    main()
