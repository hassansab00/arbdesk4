"""Plan v2.4 P3.10 part 2: the S10 checkpoint replay's inputs, from the venue's
record instead of the database's book snapshots.

scripts/backtest/replay_checkpoints.py scores the remaining-day model at every
intraday checkpoint against the venue's winner, beside the market. On its
first run (docs/S10_REPLAY_2026-09-26.md) the market came from book snapshots,
which priced the whole ladder at a decision time on 1-4 days per checkpoint.
The record (tools/market_history.py) prices every bucket hourly, so the same
replay can compare log loss with the market on every city-day it covers.

This writes a replay_inputs.json.gz in the replay's own format:

  markets  [event_id, city, date, winning band id, unit, zone] - the events
           tools/market_vs_model.py scores (active city, resolved, exactly one
           winner, settled on the city's current station), dated from FROM to
           the last day the committed readings and labels cover
  bands    ["<event_id>:<index>", event_id, lo, hi, open_low, open_high]
  mids     ["<event_id>:<index>", unix time, price] - every hourly price from
           00:00 to 23:59 local on the day itself (every scored checkpoint is
           on that day; replay_checkpoints takes the newest at or before the
           decision time, at most 3 h old)
  peaks, q copied from data/replay/inputs_2026-09-26 (derived_weather_peak
           and the cities' q layer as read on 26 Sep; the replay uses the
           engine's pooled q, not these)

    python tools/p310_replay_inputs.py data/replay/inputs_market_record/replay_inputs.json.gz
"""
import datetime as dt
import gzip
import json
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_vs_model as mvm   # noqa: E402

FROM = "2025-12-01"
LAST = "2026-09-25"            # labels_whole ends 25 Sep; obs_utc 26 Sep
BASE = "data/replay/inputs_2026-09-26/replay_inputs.json.gz"


def build():
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    events, left = mvm.load_events(tz, cities)
    events = {k: e for k, e in events.items() if FROM <= e["date"] <= LAST}
    base = json.load(gzip.open(BASE, "rt"))
    markets, bands, window = [], [], {}
    for eid, e in sorted(events.items(), key=lambda kv: (kv[1]["date"], kv[1]["city"])):
        markets.append([eid, e["city"], e["date"], f"{eid}:{e['winner']}", e["unit"], tz[e["city"]]])
        for b in e["bands"]:
            bands.append([f"{eid}:{b['band_id']}", eid, b["band_lo"], b["band_hi"], b["open_low"], b["open_high"]])
        zone = ZoneInfo(tz[e["city"]])
        day = dt.date.fromisoformat(e["date"])
        start = int(dt.datetime.combine(day, dt.time(0), zone).timestamp())
        end = int(dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), zone).timestamp())
        window[eid] = (start, end)
    mids = []
    for r in mvm.read_csv(mvm.MH + "prices.csv.gz"):
        w = window.get(r["event_id"])
        if w is None:
            continue
        t = int(r["t"])
        if w[0] <= t < w[1]:
            mids.append([f"{r['event_id']}:{r['band_index']}", t, float(r["p"])])
    source = ("the venue's own record (`data/training/market_history/`, built by `tools/market_history.py`; "
              "these inputs by `tools/p310_replay_inputs.py`), events " + FROM + " to " + LAST)
    market_note = (f"- **The market is the venue's own hourly price.** Every bucket's hourly price from Polymarket's "
                   f"price history; the whole ladder counts as priced when every bucket has a price within "
                   "3 h (`MARKET_MAX_AGE_S`) before the decision time (the n in 'Against the market'). It is the venue's quoted price, "
                   "not an executable bid or ask: what a trade would have paid is not measured here.")
    return {"exported_at": dt.date.today().isoformat(), "source": source, "market_note": market_note,
            "markets": markets, "bands": bands,
            "peaks": base["peaks"], "q": base["q"], "mids": mids}, left


def main():
    out = sys.argv[1]
    inputs, left = build()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with gzip.open(out, "wt") as f:
        json.dump(inputs, f)
    print(json.dumps({"markets": len(inputs["markets"]), "bands": len(inputs["bands"]),
                      "mids": len(inputs["mids"]), "left_out": left}), file=sys.stderr)


if __name__ == "__main__":
    main()
