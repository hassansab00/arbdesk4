"""Decide at the book, not at the hour: the market residual's corrections on
the order book's own prices.

Every test so far lost its money on the day from 13h (docs/WXPREDICT.md,
"Phase 3, the next builds": test 2 -0.0659 a share, test 3 -0.0961, H1's
holdout -0.0806), the market's own prices too. The decision there is the
table's: the venue's hourly price, taken at hh:01, judged against a book
snapshot up to an hour later, while the reports move the book in between.
The market residual (market_residual.py) is the market's price plus a
correction per bucket learned from the weather (`delta`, the trees' change
to the bucket's logit). Here the same correction is put on the book's price
at the snapshot instead of the hour's:

  For every snapshot of a ladder (every bucket of the market with a book in
  the same 10-minute window; one ladder's books are taken within a second),
  the newest decision of that city-day at most an hour before it. Each
  bucket's book price is the middle of its best bid and best ask (a missing
  bid counts 0, a missing ask 1), floored at 0.0005 and normalised over the
  ladder: q_book. Its probability is sigmoid(logit(q_book) + delta) with
  the decision's delta, normalised over the ladder.

Written in the form ask_backtest.py and maker_backtest.py read, one row per
ladder snapshot, `decision_utc` the snapshot's first book time, so each book
is judged against its own snapshot's decision: `p_model` the corrected
probability, `q` and `p_tempered` the book's own normalised prices (the
control). The weather features are still the hourly decision's (at most an
hour old); only the market's price is fresh. Research only.

FOUND ON (11 Oct, exploration, not a result): on the windows of tests 2 and
3, where the losses from 13h were found, the corrections on the book paid at
THETA 0.05: test 2 after the taker fee +0.0334 a share (90% +0.0055 to
+0.0677, 549 trades, 22 Aug - 25 Sep), test 3 +0.0300 (+0.0117 to +0.0640,
320 fills, 11-25 Sep); the hour-anchored model lost there (-0.0225 and
-0.0162), the book's own prices did not pay (-0.0140, -0.0190).

HYPOTHESIS H3, fixed 11 Oct before any date after 25 Sep was run with the
book anchor: the market residual's corrections on the book (this tool on a
market_residual.py dump, p_model) pay more than zero a share at THETA 0.05
on every decision, (a) by test 2's rule after the taker fee
(ask_backtest.py, "after_taker_fee") and (b) by test 3's rule with through
fills (maker_backtest.py). Its tests are dates it was not found on: first
26 Sep - 4 Oct (the table's newest date; the hour-anchored model's maker
fills were looked at there for H1, the book anchor's never), with --from
2026-09-26 --to 2026-10-04, then from 5 Oct as the table grows. Books after
25 Sep are the archive's alone (the mirror takes the kept ones 16 days on).
Each is judged on its 90% interval from resampling whole dates; a window too
short to separate it from zero says so and is not read either way.

    python tools/wxpredict/book_anchor.py RESIDUAL_DUMP.csv.gz OUT.csv.gz
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ask_backtest as ab  # noqa: E402

FLOOR = 0.0005                 # features.PRICE_FLOOR
CLIP = 1e-6
SNAP = "10min"


def logit(p):
    p = np.clip(p, CLIP, 1 - CLIP)
    return np.log(p / (1 - p))


def book_ladders(bucket_of):
    """{(market_id, snapshot): (first book time in seconds, q_book by bucket)} for every
    complete ladder snapshot of the markets in `bucket_of` ({band_id: bucket})."""
    cols = ["band_id", "observed_at", "best_bid", "best_ask"]
    arch, _ = ab.read_all("data/archive/books/*.csv.gz", cols)
    kept, _ = ab.read_all("data/mirror/book_snapshots/*.csv.gz", cols)
    b = pd.concat([arch, kept], ignore_index=True)
    b = b[b.band_id.isin(bucket_of)].copy()
    ts = pd.to_datetime(b.observed_at, utc=True, format="ISO8601")
    b["t"] = ((ts - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).astype("int64")
    b["snap"] = ts.dt.floor(SNAP)
    b = b.sort_values("t").drop_duplicates(["band_id", "snap"])
    b["bucket"] = b.band_id.map(bucket_of)
    bid = pd.to_numeric(b.best_bid, errors="coerce").fillna(0.0).clip(0, 1)
    ask = pd.to_numeric(b.best_ask, errors="coerce").fillna(1.0).clip(0, 1)
    b["mid"] = (bid + ask) / 2
    return b


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("residual_dump")
    ap.add_argument("out")
    args = ap.parse_args(argv)

    dec = pd.read_csv(args.residual_dump, low_memory=False)
    assert "delta" in dec.columns, "a market_residual.py dump with delta"
    bands, _ = ab.read_all("data/mirror/bands/*.csv.gz",
                           ["band_id", "market_id", "band_index", "band_lo", "open_low"])
    markets, _ = ab.read_all("data/mirror/markets/*.csv.gz", ["market_id", "city_key", "resolution_date"])
    bands = bands.drop_duplicates("band_id")
    markets = markets.drop_duplicates("market_id").set_index("market_id")

    # each market's ladder must be the scored event's, bucket for bucket (lower edges; ask_backtest.py)
    event_ladder = {(r.city_key, r.date): ab.edges(r.bands_lo, r.bands_hi)
                    for r in dec.drop_duplicates(["city_key", "date"]).itertuples()}
    bucket_of, market_of, n_of, unmatched = {}, {}, {}, 0
    for mid, g in bands[bands.market_id.isin(markets.index)].groupby("market_id"):
        key = (markets.at[mid, "city_key"], str(markets.at[mid, "resolution_date"]))
        ev = event_ladder.get(key)
        if ev is None:
            continue
        g = g.sort_values("band_index")
        lad = [None if str(o).lower() == "true" else float(lo) for lo, o in zip(g.band_lo, g.open_low)]
        if len(ev) != len(lad) or any(a[0] != c for a, c in zip(ev, lad)):
            unmatched += 1
            continue
        for i, bid in enumerate(g.band_id):
            bucket_of[bid] = i
            market_of[bid] = mid
        n_of[mid] = len(lad)

    b = book_ladders(bucket_of)
    b["market_id"] = b.band_id.map(market_of)
    g = b.groupby(["market_id", "snap"])
    snaps = g.agg(t0=("t", "min"), n=("bucket", "nunique")).reset_index()
    snaps["n_bands"] = snaps.market_id.map(n_of)
    snaps = snaps[snaps.n == snaps.n_bands].reset_index(drop=True)
    snaps["sid"] = np.arange(len(snaps))
    b = b.merge(snaps[["market_id", "snap", "sid"]], on=["market_id", "snap"])
    kmax = int(snaps.n_bands.max())
    mids = np.full((len(snaps), kmax), np.nan)
    mids[b.sid.values, b.bucket.values.astype(int)] = b.mid.values
    snaps["city_key"] = snaps.market_id.map(markets.city_key)
    snaps["date"] = snaps.market_id.map(markets.resolution_date).astype(str)

    # the newest decision of the city-day at most an hour before the snapshot
    d = dec.rename(columns={"decision_utc": "t_dec"}).sort_values("t_dec")
    d["date"] = d.date.astype(str)
    m = pd.merge_asof(snaps.sort_values("t0"), d, left_on="t0", right_on="t_dec", by=["city_key", "date"],
                      direction="backward", tolerance=ab.MAX_AGE_S)
    m = m[m.t_dec.notna()].reset_index(drop=True)

    q_out, p_out = [], []
    for sid, n, ds in zip(m.sid.values, m.n_bands.values.astype(int), m.delta.values):
        q = np.maximum(mids[sid, :n], FLOOR)
        q = q / q.sum()
        delta = np.array([float(v) for v in ds.split(";")])
        assert len(delta) == n
        z = 1 / (1 + np.exp(-(logit(q) + delta)))
        p = z / z.sum()
        q_out.append(";".join(f"{v:.6f}" for v in q))
        p_out.append(";".join(f"{v:.6f}" for v in p))
    out = pd.DataFrame({
        "event_id": m.event_id.values, "city_key": m.city_key.values, "date": m.date.values,
        "unit": m.unit.values, "decision_utc": m.t0.values.astype("int64"),
        "hourly_decision_utc": m.t_dec.values.astype("int64"),
        "day_offset": m.day_offset.values, "local_hour": m.local_hour.values,
        "bands_lo": m.bands_lo.values, "bands_hi": m.bands_hi.values, "winner": m.winner.values,
        "q": q_out, "p_tempered": q_out, "p_model": p_out})
    out = out.sort_values(["city_key", "date", "decision_utc"]).drop_duplicates(["city_key", "date", "decision_utc"])
    out.to_csv(args.out, index=False, compression="gzip")
    print(json.dumps({"ladder_snapshots_complete": int(len(snaps)), "with_a_decision": int(len(m)),
                      "rows": int(len(out)), "markets_unmatched": unmatched,
                      "dates": [str(out.date.min()), str(out.date.max())]}))


if __name__ == "__main__":
    main()
