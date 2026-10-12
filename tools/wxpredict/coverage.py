"""What a window holds, date by date, before anything on it is scored.

A test on new dates (H2 and H3a from 5 Oct; docs/WXPREDICT.md) is only as good
as the data under it. This counts, per event date, what each source holds for
the events the decisions dump scores, and scores nothing: no price is compared
with a probability and no payoff is computed.

  events     city-days in the dump; with_winner, the ones settled
  decisions  rows of the dump
  books      archived book rows (data/archive/books and the mirror's kept
             books) of those events' buckets, and the complete ladder snapshots
             (every bucket of a market in one 10-minute window)
  trades     archived prints (data/archive/trades) in those events' buckets
  hourly     cities with a whole local day of lead-1 hourly forecasts (at
             least 23 of the city's own hours; the file is in UTC and each
             row is put on the city's wall clock, as honest_record.py does;
             data/training/previous_runs/best_match_hourly_day1_utc.csv.gz)
  stations   cities with a station day on the date (station_daily.csv.gz and
             the nightly days beside it, station_daily_nightly)

A date is flagged when an event has no book, no print or no winner, or when a
city of the dump has no hourly forecast or station day. Books after the
mirror's newest date are the archive's alone (the mirror takes the kept ones
16 days on); trade capture before 27 Sep was thinner (docs/WXPREDICT.md, H3).
The mirror copies a market 9 days after its date; a later date's ladders come
from the definitions the nightly mirror writes (data/training/market_definitions),
and without them its books cannot be matched (11 Oct: 3 and 4 Oct had none).

    python tools/wxpredict/coverage.py DUMP.csv.gz --from 2026-10-05 [--to D] [--out data/eval/wxpredict/coverage/....json]
"""
import argparse
import datetime as dt
import glob
import gzip
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ask_backtest as ab  # noqa: E402
import snapshot_match  # noqa: E402

ROOT = ab.ROOT
HOURLY = os.path.join(ROOT, "data/training/previous_runs/best_match_hourly_day1_utc.csv.gz")
STATION_DAILY = os.path.join(ROOT, "data/training/wxpredict/station_daily.csv.gz")


def seconds(stamps):
    return ((pd.to_datetime(stamps, utc=True, format="ISO8601") - pd.Timestamp("1970-01-01", tz="UTC"))
            // pd.Timedelta(seconds=1)).astype("int64")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    dec = pd.read_csv(args.dump, usecols=lambda c: c in ("city_key", "date", "winner", "event_id"), low_memory=False)
    dec["date"] = dec.date.astype(str)
    d1 = args.date_to or dec.date.max()
    dec = dec[(dec.date >= args.date_from) & (dec.date <= d1)]

    bands, _ = ab.read_definitions("bands", ["band_id", "market_id"])
    markets, _ = ab.read_definitions("markets", ["market_id", "city_key", "resolution_date"])
    mirrored, _ = ab.read_all("data/mirror/markets/*.csv.gz", ["resolution_date"])
    markets_mirrored_through = str(mirrored.resolution_date.astype(str).max())
    bands = bands.drop_duplicates("band_id")
    markets = markets.drop_duplicates("market_id")
    markets["resolution_date"] = markets.resolution_date.astype(str)
    events = dec.drop_duplicates(["city_key", "date"])[["city_key", "date"]]
    mk = markets.merge(events, left_on=["city_key", "resolution_date"], right_on=["city_key", "date"])
    bk = bands.merge(mk[["market_id", "city_key", "date"]], on="market_id")
    n_bands = bk.groupby("market_id").band_id.nunique()

    cols = ["band_id", "observed_at"]
    arch, _ = ab.read_all("data/archive/books/*.csv.gz", cols)
    kept, _ = ab.read_all("data/mirror/book_snapshots/*.csv.gz", cols + ["mirror_resolution_date"])
    kept_through = str(kept.mirror_resolution_date.max())
    books = pd.concat([arch, kept[cols]], ignore_index=True).merge(bk, on="band_id")
    books["t"] = seconds(books.observed_at)
    books["snap"] = books.t // snapshot_match.SNAP_S
    per_snap = books.groupby(["market_id", "snap"]).band_id.nunique().reset_index()
    per_snap["complete"] = per_snap.band_id.values == n_bands.reindex(per_snap.market_id).values
    complete = per_snap[per_snap.complete].merge(mk[["market_id", "date"]], on="market_id")

    trades, _ = ab.read_all("data/archive/trades/*.csv.gz", ["band_id", "traded_at"])
    trades = trades.merge(bk, on="band_id")

    with gzip.open(HOURLY, "rt") as fh:
        hourly = pd.read_csv(fh, usecols=["city_key", "utc"])
    zones, _ = ab.read_all(sorted(glob.glob(os.path.join(ROOT, "data/mirror/cities/cities-*.csv.gz")))[-1]
                           .replace(ROOT + os.sep, ""), ["city_key", "timezone"])
    zone = zones.drop_duplicates("city_key").set_index("city_key").timezone
    utc = pd.to_datetime(hourly.utc, utc=True)
    hourly["date"] = ""
    for city, idx in hourly.groupby("city_key").groups.items():
        if city in zone.index:                       # a city with no zone has no local day here
            hourly.loc[idx, "date"] = utc[idx].dt.tz_convert(zone[city]).dt.strftime("%Y-%m-%d")
    whole = hourly.groupby(["city_key", "date"]).size()
    whole = whole[whole >= 23].reset_index()[["city_key", "date"]]
    # the file and each night's days beside it (fetch_obs.py update, 12 Oct)
    parts = []
    for path in [STATION_DAILY, *sorted(glob.glob(os.path.join(os.path.dirname(STATION_DAILY),
                                                                "station_daily_nightly", "days-*.csv.gz")))]:
        with gzip.open(path, "rt") as fh:
            parts.append(pd.read_csv(fh, usecols=["city_key", "local_date"]))
    sdays = pd.concat(parts, ignore_index=True)

    days = []
    for date, ev in dec.groupby("date"):
        cities = set(ev.city_key)
        e = ev.drop_duplicates(["city_key", "date"]).merge(
            ev.groupby("city_key").winner.apply(lambda w: w.notna().any()).rename("settled"), on="city_key")
        b = books[books.date == date]
        tr = trades[trades.date == date]
        ev_markets = set(mk.city_key[mk.resolution_date == date]) & cities
        ev_books = set(b.city_key)
        ev_trades = set(tr.city_key)
        hr = set(whole.city_key[whole.date == date]) & cities
        st = set(sdays.city_key[sdays.local_date == date]) & cities
        row = {"date": date, "events": len(cities), "with_winner": int(e.settled.sum()), "decisions": int(len(ev)),
               "book_rows": int(len(b)), "events_with_books": len(ev_books & cities),
               "complete_ladder_snapshots": int((complete.date == date).sum()),
               "trade_prints": int(len(tr)), "events_with_trades": len(ev_trades & cities),
               "cities_with_hourly": len(hr), "cities_with_station_day": len(st),
               "books_source": "archive only" if date > kept_through else "archive and mirror",
               "events_with_market": len(ev_markets),
               "definitions_source": "mirror" if date <= markets_mirrored_through else
                                     ("recent definitions" if ev_markets else "none: not mirrored yet, no recent definitions")}
        row["flags"] = [f for f, bad in (
            ("an event without its market's definition", len(ev_markets) < len(cities)),
            ("an event without a book", row["events_with_books"] < row["events"]),
            ("an event without a print", row["events_with_trades"] < row["events"]),
            ("an event without a winner", row["with_winner"] < row["events"]),
            ("a city without a whole local day of hourly forecasts", len(hr) < len(cities)),
            ("a city without a station day", len(st) < len(cities))) if bad]
        days.append(row)

    result = {"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
              "dump": os.path.basename(args.dump), "window": [args.date_from, str(d1)],
              "mirror_books_through": kept_through, "mirror_markets_through": markets_mirrored_through,
              "dates": len(days), "dates_flagged": sum(1 for r in days if r["flags"]), "by_date": days}
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
