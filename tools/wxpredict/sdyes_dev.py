"""The same-day YES pilot on development data: which threshold to freeze.

Written before it was run (12 Oct). It picks the candidate's model and THETA
for docs/SAME_DAY_YES_PREREG.md on dates already inspected, and says how
they did there. These dates chose the values, so their result flatters the
candidate; only the forward dates test it.

THE DECISIONS: the walk-forward dump with the remaining-day model
(walkforward.py --remaining --dump; every probability out of sample, each
month's models fitted on the dates before it). Jeddah and Istanbul, the event
day itself, decisions from 10:00 to before 17:00 on the city's clock, event
dates up to DEV_LAST: the ledger's dates already looked at
(data/eval/wxpredict/exposure_ledger.json, untouched_from 10 Oct). The dump's
later rows are dropped before anything is scored, and the walk-forward's own
log loss of its October month is not read.

THE BOOKS: the archived books (data/archive/books, data/mirror/book_snapshots),
a snapshot every hour 12-25 Sep and every two hours from 26 Sep. Each book is
judged with the newest decision of its city-day at or before it, at most an
hour old (as ask_backtest.py). The ladder at size is rebuilt from the archived
depth tiers by the platform's rule (execution.synth_levels): the first tier is
priced at the touch, which is optimistic, so every result is also shown with
that tier one cent dearer.

THE RULE: sdyes.walk_yes and sdyes.choose. YES only; a ticket of $10; a level
is bought while p - (price + taker fee) >= THETA; at least 5 shares (the
minimum order is not in the archived books); one entry per city-day, the
first book at which a bucket qualifies, held to settlement on the venue's
winner.

WHO: the candidates p_weather (the remaining-day model alone) and p_fusion
(it pooled with the market, the WXPredict incumbent); the controls q (the
market's own prices: market-only) and p_tempered (the market recalibrated);
and rd1 (S10, the live same-day incumbent), at its own checkpoints, where one
is at most an hour before the book.

THE CHOICE, fixed before the run: among the two candidates and THETA in
THETAS, the pair with the highest mean net profit per entry date (Jeddah and
Istanbul together) with at least MIN_ENTRY_DATES entry dates. If no pair has
a positive mean, nothing is frozen: the pilot stops at development and that
is reported.

    python tools/wxpredict/sdyes_dev.py DUMP.csv.gz [--out data/eval/wxpredict/sdyes_dev_<date>.json]
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
import capacity  # noqa: E402
import execution as ex  # noqa: E402
import sdyes  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEV_LAST = "2026-10-09"           # exposure_ledger.json: nothing is scored from 10 Oct (untouched_from)
THETAS = (0.02, 0.05, 0.10, 0.15, 0.20)
CANDIDATES = ("p_weather", "p_fusion")
CONTROLS = ("q", "p_tempered")
MIN_ENTRY_DATES = 20
MAX_AGE_S = 3600
SEED = 20261012


def boot(x, dates, n=4000, seed=SEED):
    """Mean per date and its 90% and 95% intervals, whole dates resampled."""
    d = pd.DataFrame({"x": x, "date": dates}).groupby("date").x.sum()
    if not len(d):
        return None
    rng = np.random.default_rng(seed)
    v = d.values
    m = v[rng.integers(0, len(v), size=(n, len(v)))].mean(1)
    q = np.quantile(m, [0.05, 0.95, 0.025, 0.975])
    return {"dates": int(len(v)), "mean_per_date": round(float(v.mean()), 3),
            "ci90": [round(float(q[0]), 3), round(float(q[1]), 3)],
            "ci95": [round(float(q[2]), 3), round(float(q[3]), 3)]}


def read_books(cities, d1):
    tier_cols = [f"{s}_usd_{c}" for s in ("ask", "bid") for c in capacity.TIER_COLS] + ["ask_total_usd", "bid_total_usd"]
    cols = ["band_id", "observed_at", "tradeable", "best_bid", "best_ask"] + tier_cols
    arch, af = ab.read_all("data/archive/books/*.csv.gz", cols)
    kept, kf = ab.read_all("data/mirror/book_snapshots/*.csv.gz", cols)
    books = pd.concat([arch, kept], ignore_index=True)
    bands, _ = ab.read_definitions("bands", ["band_id", "market_id", "band_index", "band_lo", "open_low"])
    markets, _ = ab.read_definitions("markets", ["market_id", "city_key", "resolution_date"])
    bands = bands.drop_duplicates("band_id")
    markets = markets.drop_duplicates("market_id")
    markets = markets[markets.city_key.isin(cities) & (markets.resolution_date <= d1)]
    b = books.merge(bands, on="band_id").merge(markets, on="market_id")
    b["t"] = ((pd.to_datetime(b.observed_at, utc=True, format="ISO8601") - pd.Timestamp("1970-01-01", tz="UTC"))
              // pd.Timedelta(seconds=1)).astype("int64")
    b = b.sort_values(["band_id", "t"]).drop_duplicates(["band_id", "t"])
    return b, bands[bands.market_id.isin(markets.market_id)], markets, len(af), len(kf)


def bucket_map(bands, markets, dec):
    """band_id -> bucket index, where the market's ladder is the scored event's
    bucket for bucket (matched on the lower edges, as ask_backtest.py)."""
    event = {(r.city_key, r.date): ab.edges(r.bands_lo, r.bands_hi) for r in dec.drop_duplicates(["city_key", "date"]).itertuples()}
    mk = markets.set_index("market_id")
    out, unmatched = {}, 0
    for mid, g in bands.groupby("market_id"):
        g = g.sort_values("band_index")
        lows = [None if str(o).lower() == "true" else float(lo) for lo, o in zip(g.band_lo, g.open_low)]
        ev = event.get((mk.at[mid, "city_key"], str(mk.at[mid, "resolution_date"])))
        if ev is None or len(ev) != len(lows) or any(a[0] != c for a, c in zip(ev, lows)):
            unmatched += 1
            continue
        out.update({bid: i for i, bid in enumerate(g.band_id)})
    return out, unmatched


def read_rd1(cities, d1):
    fs = sorted(glob.glob(os.path.join(ROOT, "data/mirror/s10_shadow_checkpoints/*.csv.gz")))
    c = pd.concat([pd.read_csv(f, usecols=["city_key", "target_date", "decided_at", "model_version", "probs"])
                   for f in fs], ignore_index=True)
    c = c[c.city_key.isin(cities) & (c.target_date <= d1) & c.model_version.str.startswith("rd1")]
    c["t_rd1"] = ((pd.to_datetime(c.decided_at, utc=True, format="ISO8601") - pd.Timestamp("1970-01-01", tz="UTC"))
                  // pd.Timedelta(seconds=1)).astype("int64")
    c["probs"] = c.probs.map(json.loads)
    return c.drop_duplicates(["city_key", "target_date", "t_rd1"]).rename(columns={"target_date": "date"})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    with open(args.dump, "rb") as fh:
        dump_sha = hashlib.sha256(fh.read()).hexdigest()
    h0, h1 = sdyes.WINDOW_LOCAL_H
    dec = pd.read_csv(args.dump, low_memory=False)
    dec = dec[dec.city_key.isin(sdyes.CITIES) & (dec.day_offset == 0) & (dec.local_hour >= h0) &
              (dec.local_hour < h1) & (dec.date.astype(str) <= DEV_LAST)].copy()
    dec["date"] = dec.date.astype(str)

    b, bands, markets, n_af, n_kf = read_books(sdyes.CITIES, DEV_LAST)
    bmap, unmatched = bucket_map(bands, markets, dec)
    b = b[b.band_id.isin(bmap)].copy()
    b["bucket"] = b.band_id.map(bmap)
    b["date"] = b.resolution_date.astype(str)
    books_all = len(b)
    b = b[b.tradeable.astype(str).str.lower() == "true"].sort_values("t")
    m = pd.merge_asof(b, dec.sort_values("decision_utc").rename(columns={"decision_utc": "t_dec"}),
                      left_on="t", right_on="t_dec", by=["city_key", "date"], direction="backward",
                      tolerance=MAX_AGE_S)
    m = m[m.t_dec.notna()].reset_index(drop=True)
    rd1 = read_rd1(sdyes.CITIES, DEV_LAST)
    m = pd.merge_asof(m.sort_values("t"), rd1[["city_key", "date", "t_rd1", "probs"]].sort_values("t_rd1"),
                      left_on="t", right_on="t_rd1", by=["city_key", "date"], direction="backward",
                      tolerance=MAX_AGE_S).reset_index(drop=True)
    won = (m.winner.values.astype(int) == m.bucket.values.astype(int))
    tier = np.column_stack([m[f"ask_usd_{c}"].astype(float).values for c in capacity.TIER_COLS] +
                           [m.ask_total_usd.astype(float).values])
    ask = pd.to_numeric(m.best_ask, errors="coerce").values
    levels = [ex.synth_levels(float(a) if np.isfinite(a) else None,
                              [None if not np.isfinite(v) else float(v) for v in row], True)
              for a, row in zip(ask, tier)]

    def prob(col):
        if col == "rd1":
            return np.array([np.nan if not isinstance(pr, dict) else float(pr.get(bid, np.nan))
                             for pr, bid in zip(m.probs.values, m.band_id.values)])
        return np.array([float(s.split(";")[i]) for s, i in zip(m[col].values, m.bucket.values)])

    eligible = m.groupby(["city_key", "date"]).size()
    result = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "dump_sha256": dump_sha, "version": sdyes.VERSION, "dev_last": DEV_LAST,
        "window": [str(m.date.min()), str(m.date.max())] if len(m) else None,
        "rule": " ".join(" ".join(__doc__.strip().split("\n\n")[1:7]).split()),
        "books": {"archive_files": n_af, "mirror_files": n_kf, "books_of_matched_ladders": int(books_all),
                  "tradeable_with_a_decision": int(len(m)), "markets_unmatched": int(unmatched),
                  "first_date": str(m.date.min()) if len(m) else None, "last_date": str(m.date.max()) if len(m) else None},
        "eligible_city_days": {c: int((eligible.index.get_level_values(0) == c).sum()) for c in sdyes.CITIES},
        "results": {},
    }
    for who in CANDIDATES + CONTROLS + ("rd1",):
        p = prob(who)
        result["results"][who] = {}
        for theta in THETAS:
            qual = {}
            for i in range(len(m)):
                if not np.isfinite(p[i]):
                    continue
                w = sdyes.walk_yes(levels[i], p[i], theta)
                if w is None:
                    continue
                shares, usd, fee = w
                key = (m.city_key.values[i], m.date.values[i])
                stressed = sdyes.walk_yes(levels[i], p[i], theta, touch_penalty=0.01)
                qual.setdefault(key, []).append({
                    "t": int(m.t.values[i]), "bucket": int(m.bucket.values[i]), "edge": p[i] - (usd + fee) / shares,
                    "shares": shares, "usd": usd, "fee": fee, "won": bool(won[i]), "p": float(p[i]),
                    "touch": float(ask[i]), "hour": int(m.local_hour.values[i]),
                    "stressed": stressed})
            ents = []
            for (city, date), q in qual.items():
                e = sdyes.choose(q)
                e.update(city=city, date=date, pnl=sdyes.settle(e, e["won"]),
                         pnl_stressed=sdyes.settle(dict(e, usd=e["stressed"][1], fee=e["stressed"][2]), e["won"]),
                         one_share=float(e["won"]) - e["touch"] - sdyes.fee_per_share(e["touch"]))
                ents.append(e)
            result["results"][who][f"theta_{theta:.2f}"] = summarise(ents, eligible)
    result["choice"] = choose_candidate(result["results"])
    text = json.dumps(result, indent=1)
    print(text)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    return result


def summarise(ents, eligible):
    if not ents:
        return {"entries": 0}
    t = pd.DataFrame(ents)
    out = {"entries": int(len(t)), "entry_dates": int(t.date.nunique()),
           "by_city_entries": t.city.value_counts().to_dict(),
           "win_rate": round(float(t.won.mean()), 4), "mean_p": round(float(t.p.mean()), 4),
           "mean_cost_per_share": round(float(((t.usd + t.fee) / t.shares).mean()), 4),
           "mean_touch": round(float(t.touch.mean()), 4), "mean_hour": round(float(t.hour.mean()), 2),
           "usd_spent": round(float(t.usd.sum()), 2), "fees": round(float(t.fee.sum()), 2),
           "net_profit": round(float(t.pnl.sum()), 2), "net_profit_stressed": round(float(t.pnl_stressed.sum()), 2),
           "per_entry_date": boot(t.pnl.values, t.date.values),
           "per_entry_date_stressed": boot(t.pnl_stressed.values, t.date.values),
           "one_share_mean": round(float(t.one_share.mean()), 4),
           "by_city": {c: {"entries": int((t.city == c).sum()), "net_profit": round(float(t.pnl[t.city == c].sum()), 2),
                           "eligible_city_days": int((eligible.index.get_level_values(0) == c).sum())}
                       for c in sdyes.CITIES}}
    by_date = t.groupby("date").pnl.sum()
    by_city = t.groupby("city").pnl.sum()
    out["without_best_date"] = round(float(by_date.sum() - by_date.max()), 2)
    out["without_best_city"] = round(float(by_city.sum() - by_city.max()), 2)
    return out


def choose_candidate(results):
    best = None
    for who in CANDIDATES:
        for k, r in results[who].items():
            pe = r.get("per_entry_date")
            if not pe or r["entry_dates"] < MIN_ENTRY_DATES:
                continue
            if best is None or pe["mean_per_date"] > best[2]:
                best = (who, k, pe["mean_per_date"])
    if best is None or best[2] <= 0:
        return {"frozen": None, "why": "no candidate and THETA had a positive mean per entry date with "
                                       f"at least {MIN_ENTRY_DATES} entry dates", "best": best}
    return {"frozen": {"model": best[0], "theta": float(best[1].split("_")[1])}, "mean_per_entry_date": best[2]}


if __name__ == "__main__":
    main()
