"""Is there arbitrage across a ladder? Measured on the repository's books.

One bucket of a ladder wins. Buying NO on every bucket still alive pays
n_live - 1 whatever happens; a NO costs 1 - the YES best bid (the same order
seen from the other side) or the NO book's own best ask. So when the YES best
bids of the live buckets sum to more than 1, buying every live NO locks in
the excess; the buckets left out (market_state DEAD_LOSER, which is a price,
best ask <= 0.02: scripts/market_state.py) can only add to it, since if one
of them wins every NO bought pays. Buying YES has no such slack: if a bucket
left out wins, every YES bought loses, so that side buys every bucket of the
ladder at its ask and needs all their asks to sum to less than 1.

A ladder snapshot: every band of an open market (no DEAD_WINNER yet) with a
book in the same 10-minute window (the snapshots of one ladder are taken
within milliseconds). Its size is the shares within 1 cent of the best bid on
the thinnest live bucket (bid_usd_1c / best bid). The NO books' own depth is
kept on too few snapshots to size the basket bought at the NO asks (no_book:
2,195 of 432,783 book rows, 11 Oct), so that basket gives counts and gains
only, no shares and no dollars (Codex on #374). Each gain is shown before
and after the venue's taker fee on every leg, 0.05 p (1 - p) a share at price
p (scripts/cost_model.py). Research only.

    python tools/wxpredict/ladder_arb.py [--out data/eval/wxpredict/ladder_arb_<date>.json]
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cost_model  # noqa: E402
COLS = ["band_id", "observed_at", "market_state", "best_bid", "best_ask", "no_best_ask", "bid_usd_1c", "ask_usd_1c"]


def read(pattern, cols):
    files = sorted(glob.glob(os.path.join(ROOT, pattern)))
    return pd.concat([pd.read_csv(f, usecols=cols, low_memory=False) for f in files], ignore_index=True), files


def described(s):
    return {k: round(float(v), 4) for k, v in s.describe().items()} if len(s) else {"count": 0}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    arch, fa = read("data/archive/books/*.csv.gz", COLS)
    kept, fk = read("data/mirror/book_snapshots/*.csv.gz", COLS)
    b = pd.concat([arch, kept], ignore_index=True)
    b["snap"] = pd.to_datetime(b.observed_at, utc=True, format="ISO8601").dt.floor("10min")
    b = b.drop_duplicates(["band_id", "snap"])
    bands, _ = read("data/mirror/bands/*.csv.gz", ["band_id", "market_id"])
    bands = bands.drop_duplicates("band_id")
    markets, _ = read("data/mirror/markets/*.csv.gz", ["market_id", "resolution_date"])
    markets = markets.drop_duplicates("market_id")
    m = b.merge(bands, on="band_id").merge(bands.groupby("market_id").size().rename("n_bands"), on="market_id")
    for c in COLS[3:]:
        m[c] = pd.to_numeric(m[c], errors="coerce")
    m["live"] = m.market_state != "DEAD_LOSER"
    m["winner"] = m.market_state == "DEAD_WINNER"
    # the fee on each leg: a NO bought at 1 - the YES bid, a NO at its own ask, a YES at its ask
    m["fee_no_bid"] = cost_model.taker_fee(1.0, 1 - m.best_bid)
    m["fee_no_ask"] = cost_model.taker_fee(1.0, m.no_best_ask)
    m["fee_yes_ask"] = cost_model.taker_fee(1.0, m.best_ask)
    m["shares_bid"] = m.bid_usd_1c / m.best_bid.clip(lower=0.01)
    m["shares_ask"] = m.ask_usd_1c / m.best_ask.clip(lower=0.01)

    key = ["market_id", "snap"]
    s = m.groupby(key).agg(n=("band_id", "size"), n_bands=("n_bands", "first"), winners=("winner", "sum"))
    lv = m[m.live]
    gl = lv.groupby(key)
    s = s.join(pd.DataFrame({
        "n_live": gl.size(), "sum_bid": gl.best_bid.sum(min_count=1), "bid_missing": gl.best_bid.apply(lambda x: x.isna().sum()),
        "sum_ask": gl.best_ask.sum(min_count=1), "ask_missing": gl.best_ask.apply(lambda x: x.isna().sum()),
        "sum_no_ask": gl.no_best_ask.sum(min_count=1), "no_ask_missing": gl.no_best_ask.apply(lambda x: x.isna().sum()),
        "shares_bid": gl.shares_bid.min(), "shares_ask": gl.shares_ask.min(),
        "fee_no_bid": gl.fee_no_bid.sum(), "fee_no_ask": gl.fee_no_ask.sum()}))
    s = s[(s.n == s.n_bands) & (s.winners == 0) & (s.n_live >= 2)].reset_index().merge(markets, on="market_id")

    def side(part, gain, shares, fee):
        """shares None: the basket's depth is not known, so no size or dollars."""
        x = part.assign(gain=gain, shares=np.nan if shares is None else shares, fee=fee)
        net = x[x.gain - x.fee > 0]
        x = x[x.gain > 0]
        x = x.assign(usd=x.gain * x.shares)
        first = x.sort_values("snap").drop_duplicates("market_id")
        out = {"ladder_snapshots": int(len(part)), "with_a_gain": int(len(x)), "markets": int(x.market_id.nunique()),
               "dates": int(x.resolution_date.nunique()), "gain_per_set": described(x.gain),
               "fee_per_set": described(x.fee),
               "after_taker_fee": {"with_a_gain": int(len(net)), "gain_per_set": described(net.gain - net.fee)}}
        if shares is None:
            out["size"] = "not known: the NO books' depth is not kept"
            return out
        out["shares_on_thinnest_bucket"] = described(x.shares)
        out["usd_first_per_market"] = round(float(first.usd.sum()), 2)
        out["usd_every_snapshot"] = round(float(x.usd.sum()), 2)
        out["after_taker_fee"]["usd_every_snapshot"] = round(float(((net.gain - net.fee) * net.shares).sum()), 2)
        return out

    al = m.groupby(key).agg(sum_ask_all=("best_ask", lambda x: x.sum(min_count=1)),
                            ask_missing_all=("best_ask", lambda x: x.isna().sum()),
                            shares_ask_all=("shares_ask", "min"), fee_yes_ask=("fee_yes_ask", "sum")).reset_index()
    s = s.merge(al, on=key)
    sell = s[s.bid_missing == 0]
    buy = s[s.ask_missing_all == 0]
    no = s[s.no_ask_missing == 0]
    result = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "books": {"archive_files": len(fa), "mirror_files": len(fk), "band_snapshots": int(len(b))},
        "window": [str(s.resolution_date.min()), str(s.resolution_date.max())],
        "open_ladder_snapshots": int(len(s)),
        "buy_every_no_at_1_minus_yes_bid": side(sell, sell.sum_bid - 1, sell.shares_bid, sell.fee_no_bid),
        "buy_every_no_at_its_ask": side(no, (no.n_live - 1) - no.sum_no_ask, None, no.fee_no_ask),
        "buy_every_yes_at_its_ask_every_bucket": side(buy, 1 - buy.sum_ask_all, buy.shares_ask_all, buy.fee_yes_ask),
        "method": __doc__.strip().splitlines()[0],
    }
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
