"""Test 2 (Hassan, 11 Oct: "yes run test 2"): does the walk-forward fusion make
money at the real asks?

The rule, fixed before it was run:

  For every order-book snapshot of a bucket with a tradeable book, take the
  newest scored decision of the same city-day at or before the snapshot (at
  most an hour before; tools/wxpredict/walkforward.py --dump, every
  probability out of sample). Buy one share of YES at the best ask when the
  probability is at least THETA above it, or one share of NO at its best ask
  (1 - best bid where the book gives no NO ask) when 1 - p is at least THETA
  above that. One share per bucket, side and day: the first snapshot that
  qualifies. Settled on the venue's winner. No fee.

  THETA 0.05 is the test; 0.02 and 0.10 are shown beside it. The same rule
  with the market's own normalised prices (q) is the control, which should
  not make money, and with the tempered market (q^a) the market's own
  calibration.

  The window is the event dates whose books are all in the repository: the
  archive's pruned books (data/archive/books) and the mirror's kept books
  (data/mirror/book_snapshots), which wait 16 days before they are mirrored.

Mean payoff per share, with a 90% interval from resampling whole dates.
Beside it, outside the rule: the same trades after the venue's taker fee on
weather markets, 0.05 p (1 - p) a share at price p (scripts/cost_model.py),
which the rule above leaves out.

    python tools/wxpredict/ask_backtest.py DECISIONS.csv.gz [--model-col p_fusion] [--out data/eval/wxpredict/ask_backtest_<date>.json]

--model-col names the decisions' column holding the model's probabilities
(walkforward.py --dump writes p_fusion, market_residual.py --dump p_model);
the rule is the same for every model.
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

sys.path.insert(0, os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "scripts"))
import cost_model  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
THETAS = (0.02, 0.05, 0.10)
TEST_THETA = 0.05
SEED = 20261011
MAX_AGE_S = 3600


def read_all(pattern, usecols=None):
    files = sorted(glob.glob(os.path.join(ROOT, pattern)))
    return pd.concat([pd.read_csv(f, usecols=usecols, low_memory=False) for f in files], ignore_index=True), files


def edges(lo_s, hi_s):
    lo = [None if x == "" else float(x) for x in lo_s.split(";")]
    hi = [None if x == "" else float(x) for x in hi_s.split(";")]
    return list(zip(lo, hi))


def boot_ci(x, dates, n=4000, seed=SEED):
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({"x": x, "date": dates}).groupby("date")["x"].agg(["sum", "count"])
    s, c = d["sum"].values, d["count"].values
    idx = rng.integers(0, len(s), size=(n, len(s)))
    m = s[idx].sum(1) / c[idx].sum(1)
    return [round(float(np.quantile(m, 0.05)), 4), round(float(np.quantile(m, 0.95)), 4)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("decisions")
    ap.add_argument("--out", default=None)
    ap.add_argument("--model-col", default="p_fusion")
    ap.add_argument("--from", dest="date_from", default=None, help="first event date (default: the first with books)")
    ap.add_argument("--to", dest="date_to", default=None,
                    help="last event date (default: the mirror's newest; after it the books are the archive's alone)")
    args = ap.parse_args(argv)

    dec = pd.read_csv(args.decisions, low_memory=False)
    dec_sha = hashlib.sha256(open(args.decisions, "rb").read()).hexdigest()

    cols = ["band_id", "observed_at", "tradeable", "best_bid", "best_ask", "no_best_ask"]
    arch, arch_files = read_all("data/archive/books/*.csv.gz", cols)
    kept, kept_files = read_all("data/mirror/book_snapshots/*.csv.gz", cols + ["mirror_resolution_date"])
    kept_through = kept.mirror_resolution_date.max()
    books = pd.concat([arch, kept[cols]], ignore_index=True)
    bands, _ = read_all("data/mirror/bands/*.csv.gz",
                        ["band_id", "market_id", "band_index", "band_lo", "band_hi", "open_low", "open_high"])
    markets, _ = read_all("data/mirror/markets/*.csv.gz", ["market_id", "city_key", "resolution_date"])
    bands = bands.drop_duplicates("band_id")
    markets = markets.drop_duplicates("market_id")

    b = books.merge(bands, on="band_id", how="inner").merge(markets, on="market_id", how="inner")
    first_book = b.resolution_date.min()
    # The window: dates whose kept books the mirror has already taken, unless
    # --from/--to say otherwise (after the mirror's newest date, the archive's alone).
    d0 = max(first_book, args.date_from) if args.date_from else first_book
    d1 = args.date_to or kept_through
    b = b[(b.resolution_date >= d0) & (b.resolution_date <= d1)]
    dec = dec[(dec.date >= d0) & (dec.date <= d1)]

    # Each market's ladder must be the scored event's, bucket for bucket.
    ladders = {}
    for mid, g in bands[bands.market_id.isin(b.market_id.unique())].groupby("market_id"):
        g = g.sort_values("band_index")
        ladders[mid] = [(None if str(o_lo).lower() == "true" else float(lo),
                         None if str(o_hi).lower() == "true" else float(hi), bid)
                        for lo, hi, o_lo, o_hi, bid in zip(g.band_lo, g.band_hi, g.open_low, g.open_high, g.band_id)]
    event_ladder = {(r.city_key, r.date): edges(r.bands_lo, r.bands_hi)
                    for r in dec.drop_duplicates(["city_key", "date"]).itertuples()}
    mk = markets.set_index("market_id")
    band_bucket = {}
    unmatched = 0
    for mid, lad in ladders.items():
        key = (mk.at[mid, "city_key"], mk.at[mid, "resolution_date"])
        ev = event_ladder.get(key)
        # Matched on the lower edges: the bands table writes an upper edge
        # inclusive on some older ladders (Lucknow, 1 Sep: 26-26) and
        # exclusive on the rest, the training table always exclusive.
        if ev is None or len(ev) != len(lad) or any(a[0] != c[0] for a, c in zip(ev, lad)):
            unmatched += 1
            continue
        for i, (_, _, bid) in enumerate(lad):
            band_bucket[bid] = i
    b = b[b.band_id.isin(band_bucket)].copy()
    b["bucket"] = b.band_id.map(band_bucket)
    # whole seconds since the epoch, whatever resolution pandas keeps the times in
    b["t"] = ((pd.to_datetime(b.observed_at, utc=True, format="ISO8601") - pd.Timestamp("1970-01-01", tz="UTC"))
              // pd.Timedelta(seconds=1)).astype("int64")
    b = b[b.tradeable.astype(str).str.lower() == "true"]

    # The newest scored decision at or before each snapshot, at most an hour old.
    dec = dec.sort_values("decision_utc")
    b = b.sort_values("t")
    # by city AND date: at a D-1 hour a city has two decisions at one instant,
    # the evening's for tomorrow and the day's own
    b["date"] = b.resolution_date.astype(str)
    dec["date"] = dec.date.astype(str)
    m = pd.merge_asof(b, dec.rename(columns={"decision_utc": "t_dec"}),
                      left_on="t", right_on="t_dec", by=["city_key", "date"], direction="backward",
                      tolerance=MAX_AGE_S)
    m = m[m.t_dec.notna()]

    def prob(col):
        return np.array([float(s.split(";")[i]) for s, i in zip(m[col].values, m.bucket.values)])

    probs = {args.model_col.removeprefix("p_"): prob(args.model_col), "market (control)": prob("q"),
             "market tempered": prob("p_tempered")}
    won = (m.winner.values.astype(int) == m.bucket.values.astype(int))
    yes_ask = pd.to_numeric(m.best_ask, errors="coerce").values
    bid = pd.to_numeric(m.best_bid, errors="coerce").values
    no_ask = pd.to_numeric(m.no_best_ask, errors="coerce").values
    no_ask = np.where(np.isfinite(no_ask), no_ask, 1 - bid)

    def trades(p, theta):
        rows = []
        for side, price, pp, win in (("yes", yes_ask, p, won), ("no", no_ask, 1 - p, ~won)):
            ok = np.isfinite(price) & (price > 0) & (price < 1) & (pp - price >= theta)
            t = pd.DataFrame({"band_id": m.band_id.values[ok], "date": m.date.values[ok], "t": m.t.values[ok],
                              "price": price[ok], "p": pp[ok], "win": win[ok], "side": side,
                              "hour": m.local_hour.values[ok], "day_offset": m.day_offset.values[ok],
                              "age_min": (m.t.values[ok] - m.t_dec.values[ok]) / 60})
            rows.append(t.sort_values("t").drop_duplicates(["band_id", "side", "date"]))
        t = pd.concat(rows, ignore_index=True)
        t["payoff"] = t.win.astype(float) - t.price
        t["payoff_after_fee"] = t.payoff - cost_model.taker_fee(1.0, t.price.values)
        return t

    def summary(t):
        if not len(t):
            return {"trades": 0}
        return {"trades": int(len(t)), "dates": int(t.date.nunique()),
                "mean_price": round(float(t.price.mean()), 4), "mean_p": round(float(t.p.mean()), 4),
                "win_rate": round(float(t.win.mean()), 4),
                "payoff_per_share": round(float(t.payoff.mean()), 4), "ci90": boot_ci(t.payoff.values, t.date.values),
                "total": round(float(t.payoff.sum()), 2),
                "after_taker_fee": {"payoff_per_share": round(float(t.payoff_after_fee.mean()), 4),
                                    "ci90": boot_ci(t.payoff_after_fee.values, t.date.values)}}

    result = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "decisions_sha256": dec_sha,
        "model_col": args.model_col,
        "window": [str(d0), str(d1)],
        "books_after_mirror": "archive only" if str(d1) > str(kept_through) else None,
        "books": {"archive_files": len(arch_files), "mirror_files": len(kept_files),
                  "snapshots_matched": int(len(m)), "markets_unmatched": unmatched,
                  "dates": int(m.date.nunique())},
        # the rule, the thresholds and the window: the three paragraphs under "The rule, fixed before it was run:"
        "rule": " ".join(" ".join(__doc__.strip().split("\n\n")[2:5]).split()),
        "test_theta": TEST_THETA,
        "results": {},
    }
    for name, p in probs.items():
        result["results"][name] = {}
        for theta in THETAS:
            t = trades(p, theta)
            r = summary(t)
            r["by_side"] = {s: summary(t[t.side == s]) for s in ("yes", "no")}
            r["by_when"] = {w: summary(t[sel]) for w, sel in (
                ("D-1", t.day_offset < 0), ("D before 13h", (t.day_offset == 0) & (t.hour < 13)),
                ("D 13h on", (t.day_offset == 0) & (t.hour >= 13)))}
            # how old the decision was at the snapshot: the decision's prices are the hour's
            r["by_age"] = {w: summary(t[sel]) for w, sel in (
                ("0-15 min", t.age_min < 15), ("15-30 min", (t.age_min >= 15) & (t.age_min < 30)),
                ("30-60 min", t.age_min >= 30))}
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
