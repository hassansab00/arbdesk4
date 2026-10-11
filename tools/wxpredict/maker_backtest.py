"""Test 3: does a model's probability make money as a MAKER (resting orders)?

Test 2 bought at the ask and paid the spread; the venue also charges a taker,
never a maker, a fee on weather markets (0.05 p (1 - p) a share,
scripts/cost_model.py). This asks the other way round.

The rule, fixed before it was run:

  At each order-book snapshot of a bucket with a two-sided tradeable book,
  take the newest scored decision of the same city-day at or before the
  snapshot (at most an hour before, as test 2). If the probability p is at
  least THETA above the best bid, rest a bid for one YES share AT the best
  bid; if the best ask is at least THETA above p, rest an offer of one YES
  share at the best ask (the same as a bid for one NO at 1 - the ask). An
  order lives until the next snapshot of the bucket or an hour, whichever
  comes first.

  It is FILLED only if a trade in that bucket after the snapshot and before
  the order ends printed THROUGH its price: a taker selling YES (or buying
  NO) at a YES price below the bid, a taker buying YES (or selling NO) above
  the offer. A print through the price clears every order resting at it, so
  this does not depend on where the order would have stood in the queue. A
  print AT the price (the "touch") is shown beside it as the optimistic case,
  not the test. Prints come from data/archive/trades, NO-token prints turned
  into YES prices (1 - p).

  One share per bucket, side and day: the first fill. Settled on the venue's
  winner: a YES bought at b pays (won) - b, a YES sold at a pays a - (won).
  No fee (makers pay none); the maker's rebate (a quarter of the taker's fee,
  cost_model.maker_rebate) is shown beside, not counted.

  THETA 0.05 is the test; 0.02 and 0.10 beside it. The market's own
  normalised prices (q) and the tempered market are the controls.

  The window: the event dates whose books are in the repository and whose
  trades all carry their token (from the first archived trade file with
  every token known), through the newest date the mirror holds books for.

Mean payoff per share and a 90% interval from resampling whole dates.

HYPOTHESIS H1, fixed 11 Oct after test 3 and before any date after 25 Sep
was looked at: on 11-25 Sep (test 3) and 22 Aug - 25 Sep (test 2), every
loss of the market-residual model (market_residual.py) came from decisions
on the day from 13h local, when the reports move the book faster than an
hourly decision; D-1 and the day before 13h made money in both tests (found
after looking, so not claimed). H1: the rule above at THETA 0.05, through
fills, on decisions at D-1 or on the day before 13h local ("D-1 or D before
13h" in by_when), pays more than zero a share. Its test is the dates after
the ones it was found on: --from 2026-09-26, through the newest date with
decisions, books and trades in the repository. Books after the mirror's
newest date are the archive's alone (the kept books join when the mirror
takes them, 16 days on, and the test is run again then).

    python tools/wxpredict/maker_backtest.py DECISIONS.csv.gz [--model-col p_model] [--from D] [--to D]
                                             [--out data/eval/wxpredict/maker_backtest_<date>.json]
"""
import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ask_backtest as ab  # noqa: E402

ROOT = ab.ROOT
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cost_model  # noqa: E402

THETAS = (0.02, 0.05, 0.10)
TEST_THETA = 0.05
MAX_LIFE_S = 3600


def load_trades():
    files = sorted(glob.glob(os.path.join(ROOT, "data/archive/trades/*.csv.gz")))
    parts, first_full = [], None
    for f in files:
        d = pd.read_csv(f, usecols=["band_id", "token_id", "traded_at", "price", "size", "side"], low_memory=False)
        if first_full is None and len(d) and d.token_id.notna().all():
            first_full = d.traded_at.min()[:10]
        parts.append(d)
    t = pd.concat(parts, ignore_index=True).dropna(subset=["token_id"])
    return t, files, first_full


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("decisions")
    ap.add_argument("--model-col", default="p_model")
    ap.add_argument("--out", default=None)
    ap.add_argument("--from", dest="date_from", default=None, help="first event date (default: the first with tokens)")
    ap.add_argument("--to", dest="date_to", default=None, help="last event date (default: the mirror's newest)")
    args = ap.parse_args(argv)

    dec = pd.read_csv(args.decisions, low_memory=False)
    dec_sha = hashlib.sha256(open(args.decisions, "rb").read()).hexdigest()
    cols = ["band_id", "observed_at", "tradeable", "best_bid", "best_ask"]
    arch, arch_files = ab.read_all("data/archive/books/*.csv.gz", cols)
    kept, kept_files = ab.read_all("data/mirror/book_snapshots/*.csv.gz", cols + ["mirror_resolution_date"])
    kept_through = kept.mirror_resolution_date.max()
    books = pd.concat([arch, kept[cols]], ignore_index=True)
    bands, _ = ab.read_all("data/mirror/bands/*.csv.gz",
                           ["band_id", "market_id", "band_index", "band_lo", "band_hi", "open_low", "open_high",
                            "token_yes", "token_no"])
    markets, _ = ab.read_all("data/mirror/markets/*.csv.gz", ["market_id", "city_key", "resolution_date"])
    bands = bands.drop_duplicates("band_id")
    markets = markets.drop_duplicates("market_id")
    trades, trade_files, first_full = load_trades()

    b = books.merge(bands[["band_id", "market_id"]], on="band_id").merge(markets, on="market_id")
    d0 = max(first_full, args.date_from) if args.date_from else first_full
    d1 = args.date_to or kept_through
    b = b[(b.resolution_date >= d0) & (b.resolution_date <= d1)]
    dec = dec[(dec.date >= d0) & (dec.date <= d1)]

    # each market's ladder must be the scored event's, bucket for bucket (lower edges; ask_backtest.py)
    event_ladder = {(r.city_key, r.date): ab.edges(r.bands_lo, r.bands_hi)
                    for r in dec.drop_duplicates(["city_key", "date"]).itertuples()}
    mk = markets.set_index("market_id")
    band_bucket, unmatched = {}, 0
    for mid, g in bands[bands.market_id.isin(b.market_id.unique())].groupby("market_id"):
        g = g.sort_values("band_index")
        lad = [None if str(o).lower() == "true" else float(lo) for lo, o in zip(g.band_lo, g.open_low)]
        ev = event_ladder.get((mk.at[mid, "city_key"], mk.at[mid, "resolution_date"]))
        if ev is None or len(ev) != len(lad) or any(a[0] != c for a, c in zip(ev, lad)):
            unmatched += 1
            continue
        for i, bid in enumerate(g.band_id):
            band_bucket[bid] = i
    b = b[b.band_id.isin(band_bucket)].copy()
    b["bucket"] = b.band_id.map(band_bucket)
    b["t"] = ((pd.to_datetime(b.observed_at, utc=True, format="ISO8601") - pd.Timestamp("1970-01-01", tz="UTC"))
              // pd.Timedelta(seconds=1)).astype("int64")
    b = b.sort_values(["band_id", "t"]).drop_duplicates(["band_id", "t"])
    # the order ends at the bucket's next snapshot or after MAX_LIFE_S
    nxt = b.groupby("band_id").t.shift(-1)
    b["t_end"] = np.minimum(nxt.fillna(b.t + MAX_LIFE_S), b.t + MAX_LIFE_S).astype("int64")
    b["bid"] = pd.to_numeric(b.best_bid, errors="coerce")
    b["ask"] = pd.to_numeric(b.best_ask, errors="coerce")
    b = b[(b.tradeable.astype(str).str.lower() == "true") & (b.bid > 0) & (b.ask < 1) & (b.bid < b.ask)]

    dec = dec.sort_values("decision_utc")
    b = b.sort_values("t")
    b["date"] = b.resolution_date.astype(str)
    dec["date"] = dec.date.astype(str)
    m = pd.merge_asof(b, dec.rename(columns={"decision_utc": "t_dec"}), left_on="t", right_on="t_dec",
                      by=["city_key", "date"], direction="backward", tolerance=ab.MAX_AGE_S)
    m = m[m.t_dec.notna()].reset_index(drop=True)

    # every print as a YES price and the taker's direction in YES
    tok = bands.set_index("band_id")[["token_yes"]]
    tr = trades[trades.band_id.isin(band_bucket)].merge(tok, left_on="band_id", right_index=True)
    is_yes = tr.token_id.astype(str) == tr.token_yes.astype(str)
    tr["yes_price"] = np.where(is_yes, tr.price, 1 - tr.price)
    buys_yes = (is_yes & (tr.side == "BUY")) | (~is_yes & (tr.side == "SELL"))
    tr["dir"] = np.where(buys_yes, 1, -1)
    tr["t"] = ((pd.to_datetime(tr.traded_at, utc=True, format="ISO8601") - pd.Timestamp("1970-01-01", tz="UTC"))
               // pd.Timedelta(seconds=1)).astype("int64")
    by_band = {k: (g.t.values, g.yes_price.values, g.dir.values) for k, g in tr.sort_values("t").groupby("band_id")}

    def fills(band_ids, t0, t1, price, want_dir, sign):
        """(through, touch): a print in (t0, t1] of the given taker direction
        strictly past the price (sign -1: below a bid; +1: above an offer), or at it."""
        thr = np.zeros(len(band_ids), bool)
        tch = np.zeros(len(band_ids), bool)
        for i, (k, a, z, p) in enumerate(zip(band_ids, t0, t1, price)):
            if k not in by_band:
                continue
            ts, ps, ds = by_band[k]
            lo, hi = np.searchsorted(ts, a, side="right"), np.searchsorted(ts, z, side="right")
            if hi <= lo:
                continue
            pp, dd = ps[lo:hi], ds[lo:hi]
            s = dd == want_dir
            thr[i] = bool(np.any(s & (sign * (pp - p) > 1e-9)))
            tch[i] = thr[i] or bool(np.any(s & (np.abs(pp - p) <= 1e-9)))
        return thr, tch

    def prob(col):
        return np.array([float(s.split(";")[i]) for s, i in zip(m[col].values, m.bucket.values)])

    won = (m.winner.values.astype(int) == m.bucket.values.astype(int)).astype(float)
    probs = {args.model_col.removeprefix("p_"): prob(args.model_col), "market (control)": prob("q"),
             "market tempered": prob("p_tempered")}

    def orders(p, theta):
        out = []
        for side, ok, price, want, sign in (
                ("bid yes", p - m.bid.values >= theta, m.bid.values, -1, -1),
                ("offer yes", m.ask.values - p >= theta, m.ask.values, 1, 1)):
            idx = np.flatnonzero(ok)
            thr, tch = fills(m.band_id.values[idx], m.t.values[idx], m.t_end.values[idx], price[idx], want, sign)
            for name, f in (("through", thr), ("touch", tch)):
                j = idx[f]
                pay = won[j] - price[j] if side == "bid yes" else price[j] - won[j]
                d = pd.DataFrame({"band_id": m.band_id.values[j], "date": m.date.values[j], "t": m.t.values[j],
                                  "price": price[j], "p": p[j], "won": won[j], "payoff": pay, "side": side,
                                  "fill": name, "hour": m.local_hour.values[j], "day_offset": m.day_offset.values[j],
                                  "rebate": cost_model.maker_rebate(cost_model.taker_fee(1.0, price[j]))})
                out.append(d.sort_values("t").drop_duplicates(["band_id", "side", "date"]))
            out.append(pd.DataFrame({"side": [side], "fill": ["placed"], "n": [len(idx)]}))
        return out

    def summary(t):
        if not len(t):
            return {"fills": 0}
        return {"fills": int(len(t)), "dates": int(t.date.nunique()), "mean_price": round(float(t.price.mean()), 4),
                "payoff_per_share": round(float(t.payoff.mean()), 4), "ci90": ab.boot_ci(t.payoff.values, t.date.values),
                "total": round(float(t.payoff.sum()), 2),
                "with_rebate": round(float((t.payoff + t.rebate).mean()), 4)}

    result = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "decisions_sha256": dec_sha,
        "model_col": args.model_col, "window": [str(d0), str(d1)],
        "books_after_mirror": "archive only" if str(d1) > str(kept_through) else None,
        "books": {"archive_files": len(arch_files), "mirror_files": len(kept_files), "snapshots_matched": int(len(m)),
                  "markets_unmatched": unmatched, "dates": int(m.date.nunique())},
        "trades": {"files": len(trade_files), "prints_in_window_buckets": int(len(tr))},
        "rule": " ".join(" ".join(__doc__.strip().split("\n\n")[3:8]).split()),
        "test_theta": TEST_THETA, "results": {}}
    for name, p in probs.items():
        result["results"][name] = {}
        for theta in THETAS:
            parts = orders(p, theta)
            placed = {d.side.iloc[0]: int(d.n.iloc[0]) for d in parts if "n" in d.columns}
            t = pd.concat([d for d in parts if "n" not in d.columns], ignore_index=True)
            r = {"orders_placed": placed}
            for fill in ("through", "touch"):
                f = t[t.fill == fill]
                r[fill] = summary(f)
                r[fill]["by_side"] = {s: summary(f[f.side == s]) for s in ("bid yes", "offer yes")}
                r[fill]["by_when"] = {w: summary(f[sel]) for w, sel in (
                    ("D-1", f.day_offset < 0), ("D before 13h", (f.day_offset == 0) & (f.hour < 13)),
                    ("D 13h on", (f.day_offset == 0) & (f.hour >= 13)),
                    ("D-1 or D before 13h", (f.day_offset < 0) | (f.hour < 13)))}
            result["results"][name][f"theta_{theta:.2f}"] = r
    text = json.dumps(result, indent=1)
    print(text)
    if args.out:
        if os.path.dirname(args.out):
            os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    return result


if __name__ == "__main__":
    main()
