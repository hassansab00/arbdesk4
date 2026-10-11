"""Test 2 at size: how many dollars could the one-share rule have put to work,
and what would they have made after costs?

The one-share reports (ask_backtest.py) say whether a rule's average share
pays. They do not say how many shares the book held at that price, what the
fee and the walk down the book cost, or how much cash it ties up. This
answers those for the same signals, beside the one-share score, which it
never replaces (the Codex action plan of 11 Oct, section 6).

The rule, fixed before it was run on any date from 5 Oct (11 Oct):

  SIGNALS: the trades of the one-share rule at THETA 0.05, unchanged
  (ask_backtest.py --trades-out): each is one entry on one band, side and
  day, at the book snapshot it qualified on. The model's and the market's
  own (the control) are run the same way.

  THE LADDER at that snapshot is rebuilt from the archived depth tiers by
  the platform's rule (execution.synth_levels; sql/ad4_13_reconcile.sql,
  ad4_synth_levels). YES walks the asks; NO walks the YES bids at 1 - price
  (the venue's NO ask is that complement on 99.93% of rows). Whether the
  book also carried a real NO ask is recorded, and the complement-only
  entries are reported on their own.

  SIZE: level by level while the probability is at least THETA above the
  level's price, up to a ticket of T dollars, never past 25 cents from the
  touch; the venue's taker fee (cost_model.taker_fee) on every share at its
  level's price. STRESSED: the same entries with every share of the first
  tier one cent dearer (the platform's rule prices that tier at the touch). An entry below the minimum order is not made. The minimum
  order is NOT RECORDED (markets.min_order_size is empty on all 2,708
  markets, and the stored books keep only bids and asks): 5 shares is
  assumed, 1 share is shown beside it.

  CASH: a bankroll of B dollars, each entry's cost paid at its snapshot and
  its winnings returned at the end of the day after the event date (UTC);
  an entry the free cash cannot pay is skipped, and so is one that would
  put more than C of the bankroll on one city-day. Entries are funded in
  time order, and at one instant by band and side (execution.funding_order:
  never by probability or outcome).

  LATENCY: (a) at the snapshot itself; (b) at the band's next tradeable
  snapshot within 3 hours, with the same probability and the rule checked
  again there. A band's book was archived hourly 12-25 Sep and every 2 hours
  from 26 Sep (per-band gap median 120.0 min, p90 121.0), so (b) is a
  pessimistic bound and (a) an optimistic one; the live delay is not
  measured yet.

  T 10, 50 and 250 dollars, B 1,000, C 10%: a simulation grid, not an
  allocation.

Reported: entries and skips by reason; shares, dollars spent and fees; net
profit, its return on the dollars spent, the mean profit per date with a 90%
interval from resampling whole dates, the worst drawdown, the profit without
the best date and without the best city, how long cash is locked; YES and NO,
and real NO asks against complement only; and the one-share score of the same
signals, to reconcile.

    python tools/wxpredict/capacity.py TRADES.csv.gz [--out data/eval/wxpredict/capacity_<...>.json]
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ask_backtest as ab  # noqa: E402
import execution as ex  # noqa: E402

THETA = 0.05
TICKETS = (10.0, 50.0, 250.0)
BANKROLL = 1000.0
CITY_DAY_CAP = 0.10
MIN_SHARES = (5.0, 1.0)            # assumed; the venue's minimum is not recorded
NEXT_WITHIN_S = 3 * 3600          # covers the 2-hourly books from 26 Sep (gaps of 120-121 min)
TIER_COLS = ("1c", "2c", "5c", "10c", "25c")


def tiers(row, side):
    return [None if pd.isna(row[f"{side}_usd_{c}"]) else float(row[f"{side}_usd_{c}"]) for c in TIER_COLS] + \
        [None if pd.isna(row[f"{side}_total_usd"]) else float(row[f"{side}_total_usd"])]


def ladder(row, side):
    if side == "yes":
        return ex.synth_levels(row.best_ask, tiers(row, "ask"), True)
    return ex.as_no_asks(ex.synth_levels(row.best_bid, tiers(row, "bid"), False))


def max_drawdown(pnl_by_day):
    eq = np.cumsum(pnl_by_day)
    return float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq)) if len(eq) else 0.0


def report(taken, skipped, signals):
    if not taken:
        return {"signals": signals, "entries": 0, "skipped": skipped}
    t = pd.DataFrame(taken)
    t["pnl"] = np.where(t.win, t.shares, 0.0) - t.usd - t.fee
    by_date = t.groupby("date").pnl.sum()
    by_city = t.groupby("city").pnl.sum()
    settle_order = t.sort_values("settle_t").groupby("settle_t").pnl.sum().values
    spent = float((t.usd + t.fee).sum())
    return {
        "signals": signals, "entries": int(len(t)), "skipped": skipped, "dates": int(t.date.nunique()),
        "shares": round(float(t.shares.sum()), 1), "usd_spent": round(float(t.usd.sum()), 2),
        "fees": round(float(t.fee.sum()), 2), "net_profit": round(float(t.pnl.sum()), 2),
        "return_on_spent": round(float(t.pnl.sum()) / spent, 4) if spent else None,
        "mean_profit_per_date": round(float(by_date.mean()), 2),
        "ci90_per_date": ab.boot_ci(by_date.values, by_date.index.values),
        "max_drawdown": round(max_drawdown(settle_order), 2),
        "without_best_date": round(float(by_date.sum() - by_date.max()), 2),
        "without_best_city": round(float(by_city.sum() - by_city.max()), 2),
        "best_city": str(by_city.idxmax()), "mean_lock_days": round(float(((t.settle_t - t.t) / 86400).mean()), 2),
        "mean_usd_per_entry": round(float(t.usd.mean()), 2),
        "by_side": {s: {"entries": int((t.side == s).sum()), "net_profit": round(float(t.pnl[t.side == s].sum()), 2)}
                    for s in ("yes", "no")},
        "no_real_vs_complement": {k: {"entries": int((t.no_quote == k).sum()),
                                      "net_profit": round(float(t.pnl[t.no_quote == k].sum()), 2)}
                                  for k in ("real", "complement only")},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("trades")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    with open(args.trades, "rb") as fh:
        trades_sha = hashlib.sha256(fh.read()).hexdigest()
    sig = pd.read_csv(args.trades, low_memory=False)
    sig["date"] = sig.date.astype(str)

    cols = ["band_id", "observed_at", "tradeable", "best_bid", "best_ask", "no_best_ask"] + \
        [f"{s}_usd_{c}" for s in ("ask", "bid") for c in TIER_COLS] + ["ask_total_usd", "bid_total_usd"]
    arch, _ = ab.read_all("data/archive/books/*.csv.gz", cols)
    kept, _ = ab.read_all("data/mirror/book_snapshots/*.csv.gz", cols)
    books = pd.concat([arch, kept], ignore_index=True)
    books = books[books.band_id.isin(sig.band_id)].copy()
    books["t"] = ((pd.to_datetime(books.observed_at, utc=True, format="ISO8601") -
                   pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).astype("int64")
    books = books.sort_values(["band_id", "t"]).drop_duplicates(["band_id", "t"])
    books = books[books.tradeable.astype(str).str.lower() == "true"]
    by_band = {k: g.reset_index(drop=True) for k, g in books.groupby("band_id")}

    bands, _ = ab.read_definitions("bands", ["band_id", "market_id"])
    markets, _ = ab.read_definitions("markets", ["market_id", "city_key"])
    city_of = bands.drop_duplicates("band_id").merge(markets.drop_duplicates("market_id"), on="market_id") \
        .set_index("band_id").city_key.to_dict()

    def entries_for(who, latency, stressed, ticket, min_shares):
        out, skipped = [], {"no_book": 0, "no_next_book": 0, "below_rule_or_minimum": 0}
        used = set()
        for r in sig[sig.who == who].itertuples():
            g = by_band.get(r.band_id)
            if g is None:
                skipped["no_book"] += 1
                continue
            i = int(np.searchsorted(g.t.values, r.t))
            if i >= len(g) or g.t.values[i] != r.t:
                skipped["no_book"] += 1
                continue
            if latency == "next snapshot":
                i += 1
                if i >= len(g) or g.t.values[i] - r.t > NEXT_WITHIN_S:
                    skipped["no_next_book"] += 1
                    continue
            row = g.iloc[i]
            key = (r.band_id, int(row.t), r.side)
            if key in used:                                 # each snapshot's depth is used once
                skipped["below_rule_or_minimum"] += 1
                continue
            fill = ex.size_entry(ladder(row, r.side), r.p, THETA, ticket, min_shares,
                                 touch_penalty=0.01 if stressed else 0.0)
            if fill is None:
                skipped["below_rule_or_minimum"] += 1
                continue
            used.add(key)
            shares, usd, fee = fill
            settle = int(pd.Timestamp(r.date, tz="UTC").timestamp()) + 2 * 86400
            real_no = pd.notna(row.no_best_ask) and abs(float(row.no_best_ask) - (1 - float(row.best_bid))) <= 0.001
            out.append({"t": int(row.t), "settle_t": settle, "band_id": r.band_id,
                        "city_day": (city_of.get(r.band_id), r.date),
                        "city": city_of.get(r.band_id), "date": r.date, "side": r.side, "shares": shares,
                        "usd": usd, "fee": fee, "win": bool(r.win),
                        "no_quote": ("real" if real_no else "complement only") if r.side == "no" else "n/a"})
        return out, skipped

    result = {"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
              "trades_sha256": trades_sha, "theta": THETA, "bankroll": BANKROLL, "city_day_cap": CITY_DAY_CAP,
              "window": [str(sig.date.min()), str(sig.date.max())],
              "rule": " ".join(" ".join(__doc__.strip().split("\n\n")[2:9]).split()),
              "one_share": {}, "results": {}}
    for who in sig.who.unique():
        s = sig[sig.who == who]
        result["one_share"][who] = {"trades": int(len(s)), "payoff_per_share_after_fee": round(float(s.payoff_after_fee.mean()), 4),
                                    "ci90": ab.boot_ci(s.payoff_after_fee.values, s.date.values),
                                    "total_after_fee": round(float(s.payoff_after_fee.sum()), 2)}
        result["results"][who] = {}
        for latency in ("at the snapshot", "next snapshot"):
            for stressed in (False, True):
                for min_shares in MIN_SHARES:
                    for ticket in TICKETS:
                        ents, sk = entries_for(who, latency, stressed, ticket, min_shares)
                        taken, sk2 = ex.simulate(ents, BANKROLL, CITY_DAY_CAP)
                        name = (f"{latency} | {'touch +1c' if stressed else 'platform ladder'} | "
                                f"min {int(min_shares)} shares | ticket ${int(ticket)}")
                        result["results"][who][name] = report(taken, {**sk, **sk2}, int(len(s)))
    text = json.dumps(result, indent=1)
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    return result


if __name__ == "__main__":
    main()
