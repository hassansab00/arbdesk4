"""WXPredict WP3b step 3 (plan 2.3): does the live reader make the table's row?

The training table (build_table.py) and the live reader (live_reader.py) build
every row with the same function, features.decision_row, from different
sources: the table from the training's files, the live reader from the live
record the tick wrote. This compares them for the same decisions (event and
instant), column by column, and names every difference.

  numbers   equal within NUM_TOL (1e-6); both empty is equal, one empty is not
  text      equal as written
  market    the mkt_ columns within MARKET_TOL (0.01: step 2.1, the book's
            midpoint against the price record's point, 98.6% within 0.01 on
            stable hours with a spread up to 0.10), the price list bucket by
            bucket; a row whose live books had a bucket with a spread over
            0.10 or one side empty is reported on its own, since step 2.1
            left those open
  labels    not compared (the live row is made before the outcome)

KNOWN source differences, each counted under its name and never as a match
(docs/WXPREDICT.md, the live reader's notes):
  trace_precip       precipitation: a trace is 0.0 in the table and empty live
                     (the tick asks IEM with trace=empty)
  lucknow_half_hour  Lucknow's hourly forecast columns: its hours sit at :30
                     UTC in the training's file and at :00 in the record
  lead1_before_0200  the event day's daily forecast columns before 02:00
                     local: the record holds lead-1 hours 17 h ahead, so the
                     row falls back to lead 2 where the table has lead 1
  price_source_age   mkt_ages_min, mkt_newest_age_min, mkt_moves_1h and
                     mkt_moves_6h: the age and moves of the price record's
                     hourly point against those of the tick's book
Anything else is unexplained. Arrival against rule is compared the same way:
that difference is the cost of the hourly :36 tick, not a parity failure.

Plain Python, so CI tests it (tests/test_wxpredict_parity.py).

    python tools/wxpredict/parity.py --table TABLE.csv.gz --live LIVE.csv.gz \\
        --from 2026-10-13 --to 2026-10-15 [--out data/eval/wxpredict/parity/parity_<from>_<to>.json]
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import features  # noqa: E402

NUM_TOL = 1e-6
MARKET_TOL = 0.01
WIDE_SPREAD = 0.10
EXAMPLES = 5
IDENTITY = ["event_id", "source", "city_key", "station", "date", "unit", "n_bands", "bands_lo", "bands_hi",
            "decision_utc", "decision_local", "day_offset", "local_hour", "weekday", "doy_sin", "doy_cos"]
LABELS = ["winner", "winner_lo", "winner_hi", "station_max_unit", "station_max_c", "station_in_winner",
          "station_reports_day", "label_unit"]
MARKET = [c for c in features.COLUMNS if c.startswith("mkt_")]
COMPARED = [c for c in features.COLUMNS if c not in LABELS]
PRECIP = {"precip_3h_in"}
HOURLY_FC = {c for c in features.COLUMNS if c.startswith("fch_") or c.startswith("anom_")}
DAILY_FC = {c for c in features.COLUMNS if c.startswith("fc_")}
PRICE_SOURCE_AGE = {"mkt_ages_min", "mkt_newest_age_min", "mkt_moves_1h", "mkt_moves_6h"}


def read_csv(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if path.endswith(".gz"):
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode())))


def key(row):
    return row["event_id"], int(float(row["decision_utc"]))


def num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return v


def same(col, a, b):
    """Are two written values the same, by the column's tolerance?"""
    if col in ("mkt_prices", "mkt_ages_min"):
        xs, ys = (a or "").split(";"), (b or "").split(";")
        return len(xs) == len(ys) and all(same("mkt_top_p", x, y) for x, y in zip(xs, ys))
    x, y = num(a), num(b)
    if x is None or y is None:
        return x is None and y is None
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) <= (MARKET_TOL if col.startswith("mkt_") else NUM_TOL)
    return str(a) == str(b)


def known(col, table_row, live_row):
    """The name of the known source difference that explains this one, or None."""
    if col in PRECIP and num(table_row.get(col)) == 0.0 and num(live_row.get(col)) is None:
        return "trace_precip"
    if table_row.get("city_key") == "lucknow" and col in HOURLY_FC:
        return "lucknow_half_hour"
    if col in DAILY_FC and table_row.get("day_offset") in ("0", "0.0") and \
            num(table_row.get("local_hour")) is not None and num(table_row.get("local_hour")) < 2:
        return "lead1_before_0200"
    if col in PRICE_SOURCE_AGE:
        return "price_source_age"
    return None


def wide_or_one_sided(live_row):
    """True when the live books had a bucket with a spread over 0.10 or a side empty."""
    spreads = (live_row.get("mkt_spreads") or "").split(";")
    return any(s == "" or float(s) > WIDE_SPREAD for s in spreads) if spreads != [""] else True


def compare(pairs, cols):
    """{col: {compared, equal, known: {name: n}, unexplained, examples}} over
    [(key, reference row, other row)]."""
    out = {}
    for col in cols:
        r = {"compared": 0, "equal": 0, "known": collections.Counter(), "unexplained": 0, "examples": []}
        for k, a, b in pairs:
            r["compared"] += 1
            if same(col, a.get(col), b.get(col)):
                r["equal"] += 1
                continue
            name = known(col, a, b)
            if name:
                r["known"][name] += 1
                continue
            r["unexplained"] += 1
            if len(r["examples"]) < EXAMPLES:
                r["examples"].append({"event_id": k[0], "decision_utc": k[1], "city": a.get("city_key"),
                                      "reference": a.get(col), "other": b.get(col)})
        r["known"] = dict(r["known"])
        out[col] = r
    return out


def unexplained(result):
    return sum(r["unexplained"] for r in result.values())


def run(table_rows, live_rows, first, last):
    in_window = lambda r: first <= r["date"] <= last  # noqa: E731
    table = {key(r): r for r in table_rows if in_window(r)}
    by_mode = collections.defaultdict(dict)
    for r in live_rows:
        if in_window(r):
            by_mode[r["live_mode"]][key(r)] = r
    rule, arrival = by_mode.get("rule", {}), by_mode.get("arrival", {})
    both = sorted(set(table) & set(rule))
    narrow = [(k, table[k], rule[k]) for k in both if not wide_or_one_sided(rule[k])]
    wide = [(k, table[k], rule[k]) for k in both if wide_or_one_sided(rule[k])]
    weather = [c for c in COMPARED if c not in MARKET]
    rule_vs_table = compare([(k, table[k], rule[k]) for k in both], weather)
    rule_vs_table.update(compare(narrow, MARKET))
    out = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "window": [first, last],
        "tolerances": {"numbers": NUM_TOL, "market": MARKET_TOL, "wide_spread": WIDE_SPREAD},
        "rows": {"table": len(table), "live_rule": len(rule), "live_arrival": len(arrival),
                 "matched": len(both), "only_table": len(set(table) - set(rule)),
                 "only_live": len(set(rule) - set(table)),
                 "market_narrow": len(narrow), "market_wide_or_one_sided": len(wide)},
        "rule_vs_table": rule_vs_table,
        "market_wide_or_one_sided": compare(wide, MARKET),
        "arrival_vs_rule": compare([(k, rule[k], arrival[k]) for k in sorted(set(rule) & set(arrival))], COMPARED),
    }
    out["unexplained"] = unexplained(rule_vs_table)
    out["verdict"] = ("parity holds: every difference is a named source difference"
                      if out["unexplained"] == 0 and both else
                      "no matched rows" if not both else
                      f"{out['unexplained']} unexplained differences")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--live", required=True)
    ap.add_argument("--from", dest="first", required=True)
    ap.add_argument("--to", dest="last", required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    result = run(read_csv(args.table), read_csv(args.live), args.first, args.last)
    text = json.dumps(result, indent=1)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    print(json.dumps({k: result[k] for k in ("window", "rows", "unexplained", "verdict")}, indent=1))
    return result


if __name__ == "__main__":
    main()
