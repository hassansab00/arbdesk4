"""Explicit research export of first-call current forecasts; no replay or fitting.

Missing context remains missing. Later forecasts never fill an earlier call.
The existing arrival dataset and automatic candidate recipe keep their default
columns. Coverage readiness is an input check, not model acceptance.
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import arrival_dataset, candidate_cycle, current_forecasts, live_reader

POLICY = "wx-current-context-v2"
CHECKPOINTS = candidate_cycle.REMAINING_CHECKPOINTS
EXTRA = ("checkpoint", "current_context_available", "recorded_checkpoint_id",
         "recorded_row_sha256", "recorded_current_sha256", *current_forecasts.FIELDS)
COLUMNS = (*live_reader.COLUMNS, *EXTRA)


def complete(row):
    def number(name):
        value = row.get(name)
        try:
            return float(value) if value is not None and value != "" else math.nan
        except (ValueError, TypeError):
            return math.nan
    expected = number("expected_hours")
    return (number("current_context_available") == 1 and expected in (23, 24, 25)
            and number("current_hours") == expected and math.isfinite(number("current_max_c")))


def coverage(rows, input_coverage, first, last):
    start, end = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    if end < start:
        raise ValueError("reversed dataset dates")
    dates = [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    groups = collections.defaultdict(lambda: {"paired": 0, "with_context": 0, "complete": set()})
    seen = set()
    for row in rows:
        key = row["date"], row["checkpoint"], row["city_key"]
        if key in seen or key[0] not in dates or key[1] not in CHECKPOINTS:
            raise ValueError("duplicate or out-of-window research call")
        seen.add(key)
        group = groups[key[:2]]
        group["paired"] += 1
        group["with_context"] += int(str(row.get("current_context_available")) == "1")
        if complete(row):
            group["complete"].add(row["city_key"])
    by_date = [{"date": day, "checkpoint": cp,
                "paired": groups[(day, cp)]["paired"],
                "with_context": groups[(day, cp)]["with_context"],
                "complete_current_cities": len(groups[(day, cp)]["complete"])}
               for day in dates for cp in CHECKPOINTS]
    n = sum(row["complete_current_cities"] for row in by_date)
    expected = input_coverage["first_calls"]
    if expected < len(rows):
        raise ValueError("research calls exceed the first-call denominator")
    fraction = n / expected if expected else 0.0
    sparse = [r["date"] + ":" + r["checkpoint"] for r in by_date
              if r["complete_current_cities"] < candidate_cycle.MIN_CITIES_PER_DATE]
    return {"policy": POLICY, "first_calls": expected, "paired_rows": len(rows),
            "checkpoints":list(CHECKPOINTS),
            "complete_current_rows": n, "current_coverage": fraction,
            "minimum_cities_per_checkpoint_date": candidate_cycle.MIN_CITIES_PER_DATE,
            "minimum_coverage": candidate_cycle.MIN_COVERAGE,
            "coverage_ready": not sparse and fraction >= candidate_cycle.MIN_COVERAGE,
            "sparse_checkpoint_dates": sparse, "by_checkpoint_date": by_date,
            "input_coverage": input_coverage, "changes_serving": False}


def write(path, rows):
    """Stable bytes, preserving numeric precision and source integrity hashes."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="raise", lineterminator="\n")
    writer.writeheader()
    for row in sorted(rows, key=lambda r: (r["date"], r["city_key"], r["decision_utc"], r["checkpoint"])):
        writer.writerow({k: row.get(k) for k in COLUMNS})
    blob = gzip.compress(buf.getvalue().encode(), mtime=0)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob)
    return hashlib.sha256(blob).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="first", required=True)
    ap.add_argument("--to", dest="last", required=True)
    ap.add_argument("--through", required=True, help="exclusive verified-mirror cutoff (ISO timestamp)")
    ap.add_argument("--labels-table", required=True, help="labels only; no replayed predictors are read")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    through = live_reader.unix(args.through)
    if dt.date.fromisoformat(args.last) < dt.date.fromisoformat(args.first):
        raise ValueError("reversed dataset dates")
    rows, inputs = arrival_dataset.build(args.first, args.last, args.labels_table, through,
                                        include_current=True,checkpoints=CHECKPOINTS)
    result = coverage(rows, inputs, args.first, args.last)
    result.update(dates=[args.first, args.last], through=args.through,
                  dataset_sha256=write(args.out, rows), uses_reconstructed_features=False)
    with open(args.labels_table, "rb") as fh:
        result["labels_table_sha256"] = hashlib.sha256(fh.read()).hexdigest()
    with open(args.out + ".json", "w") as fh:
        json.dump(result, fh, indent=1)
        fh.write("\n")
    print(json.dumps(result, indent=1))
    return result


if __name__ == "__main__":
    main()
