"""WP5 step 3: sdyes-v1's forward decisions, from the live record
(docs/SAME_DAY_YES_PREREG.md).

For each forward event date of Jeddah and Istanbul, every decision instant of
the event day from 10:00 to before 17:00 on the city's clock (the training
table's instants), as the live system knew it then:

  the row     tools/wxpredict/live_reader.py in arrival mode, made by
              features.decision_row from what the tick had captured by t
  the model   the frozen remaining-day model (sdyes.MODEL_FILE, its sha256
              checked), its bucket probabilities by remaining.bucket_probs;
              a row without a running maximum is skipped, 'no running maximum'
  the books   the minute books (live record kind 'focus_book') of the first
              clean books poll 60-300 s after t (sdyes.fresh_answer)
  the rule    sdyes.ledger: the candidate, the market-only control and S10
              rd1 judged on the same books; one entry per city-day each

Writes the ledger (every decision, every bucket, every skip, with its reason)
and the entries, unsettled, under data/eval/wxpredict/sdyes/, and prints the
counts. Before the first look (sdyes.FIRST_LOOK_DATES valid settled dates)
nothing is settled and no profit is computed: the pre-registration allows
counts only.

    python tools/wxpredict/sdyes_forward.py --from 2026-10-13 --to 2026-10-14
"""
import argparse
import collections
import csv
import datetime as dt
import glob
import gzip
import hashlib
import io
import json
import os
import sys
import tempfile

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wxpredict import common, live_reader as lr  # noqa: E402
import remaining  # noqa: E402
import sdyes  # noqa: E402
import walkforward as wf  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "data", "eval", "wxpredict", "sdyes")
LEDGER_COLUMNS = ["version", "model_sha256", "row_hash", "city", "date", "event_id", "t", "who", "bucket", "band_id",
                  "books_at", "p",
                  "touch", "shares", "usd", "fee", "cost", "edge", "qualified", "reason"]


def load_model():
    with open(os.path.join(ROOT, sdyes.MODEL_FILE), "rb") as fh:
        text = gzip.decompress(fh.read())
    sha = hashlib.sha256(text).hexdigest()
    if sha != sdyes.MODEL_SHA256:
        raise SystemExit(f"the frozen model's sha256 is {sha}, not {sdyes.MODEL_SHA256}: refusing to decide")
    return lgb.Booster(model_str=text.decode())


def read_polls():
    """Every focus poll the mirror holds, as sdyes.fresh_answer reads them."""
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data/mirror/focus_obs_polls/*.csv.gz"))):
        for r in common.read_csv(path):
            out.append({"source": r["source"], "stations": json.loads(r["stations"] or "[]"),
                        "answered_at": lr.unix(r["answered_at"]) if r.get("answered_at") else None,
                        "status_code": int(r["status_code"]) if r.get("status_code") else None,
                        "error": r.get("error") or None, "request_id": r["request_id"]})
    seen, unique = set(), []
    for p in out:                          # a poll the mirror copied twice is read once
        if p["request_id"] not in seen:
            seen.add(p["request_id"])
            unique.append(p)
    return unique


def read_rd1():
    """{(city, date): [(t, {band_id: p})]} of S10 rd1's checkpoints, oldest first."""
    out = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(ROOT, "data/mirror/s10_shadow_checkpoints/*.csv.gz"))):
        for r in common.read_csv(path):
            if r["city_key"] in sdyes.CITIES and (r.get("model_version") or "").startswith("rd1"):
                out[(r["city_key"], r["target_date"])].append((lr.unix(r["decided_at"]), json.loads(r["probs"])))
    for v in out.values():
        v.sort(key=lambda x: x[0])
    return out


def model_probs(rows, booster):
    """{(event_id, t): [bucket probabilities] or None} for the rows' decisions:
    the frozen model's, on the row as the training table would hold it (written
    and read back as the table is). booster: one model, or a list of models
    fitted with different seeds, whose bucket ladders are averaged (each one
    floored and normalised first, as stability_eval.py measured it)."""
    boosters = list(booster) if isinstance(booster, (list, tuple)) else [booster]
    booster = boosters[0]
    if not rows:
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rows.csv.gz")
        lr.write_rows(path, rows)
        df = pd.read_csv(path, low_memory=False)
    x = remaining.features(df, wf.features(df))
    names = booster.feature_name()
    missing = [c for c in names if c not in x.columns]
    if missing:
        raise SystemExit(f"the live row lacks the model's features {missing}")
    x = x[names]
    el = remaining.eligible(df)
    out = {(str(e), int(t)): None for e, t in zip(df.event_id.values, df.decision_utc.values)}
    if el.any():
        sub = df[el]
        k = int(sub.n_bands.max())
        lo = np.full((len(sub), k), np.nan)
        hi = np.full((len(sub), k), np.nan)
        mask = np.zeros((len(sub), k), bool)
        for i, (a, b) in enumerate(zip(sub.bands_lo.values, sub.bands_hi.values)):
            la, lb = wf.parse_edges(a), wf.parse_edges(b)
            lo[i, :len(la)] = [-np.inf if v is None else v for v in la]
            hi[i, :len(lb)] = [np.inf if v is None else v for v in lb]
            mask[i, :len(la)] = True
        w = np.mean([remaining.bucket_probs(b.predict(x[el]), sub.unit.values, sub.rmax_unit.values, lo, hi, mask)
                     for b in boosters], axis=0)
        for (e, t), p, m in zip(zip(sub.event_id.values, sub.decision_utc.values), w, mask):
            out[(str(e), int(t))] = [float(v) for v in p[m]]
    return out


def row_hashes(rows):
    """{(event_id, t): sha256 of the live row as written (live_reader.write_rows'
    columns and formatting)}: what binds each recorded probability to its inputs."""
    out = {}
    for r in rows:
        vals = [r.get(c) if isinstance(r.get(c), str) else lr.features.fmt(r.get(c)) for c in lr.COLUMNS]
        line = "\x1f".join("" if v is None else str(v) for v in vals)
        out[(str(r["event_id"]), int(r["decision_utc"]))] = hashlib.sha256(line.encode()).hexdigest()
    return out


def decisions_for(rows, events, probs, cities):
    """sdyes.ledger's decisions: every instant of the window, a row or not. A
    missing row or running maximum leaves the candidate without a probability
    (no_probs); the controls still decide on the same books (Codex on #392)."""
    h0, h1 = sdyes.WINDOW_LOCAL_H
    have = {(str(r["event_id"]), int(r["decision_utc"])) for r in rows}
    hashes = row_hashes(rows)
    out = []
    for t, todo in lr.instants(events, cities, 0, 2 ** 40).items():
        for e, off, local in todo:
            if off != 0 or not (h0 <= local.hour < h1):
                continue
            key = (e["event_id"], int(t))
            no_probs = "no row" if key not in have else "no running maximum" if probs.get(key) is None else None
            out.append({"city": e["city"], "date": e["date"], "event_id": e["event_id"], "t": int(t),
                        "icao": e["station"], "band_ids": e["band_ids"], "probs": probs.get(key),
                        "no_probs": no_probs, "row_hash": hashes.get(key), "skip": None})
    return sorted(out, key=lambda d: (d["date"], d["city"], d["t"]))


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in columns})
    with open(path, "wb") as fh:
        fh.write(gzip.compress(buf.getvalue().encode(), mtime=0))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="first", default=sdyes.FIRST_FORWARD_DATE)
    ap.add_argument("--to", dest="last", required=True)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--record", nargs="*", help="record files (default: the mirror's and the archive's)")
    args = ap.parse_args(argv)
    if args.first < sdyes.FIRST_FORWARD_DATE:
        raise SystemExit(f"forward dates start {sdyes.FIRST_FORWARD_DATE}; {args.first} is before the freeze")
    from wxpredict import build_table
    booster = load_model()
    record = lr.Record(lr.read_record(args.record or lr.record_files()))
    if record.first is None:
        raise SystemExit("the record holds no tick: nothing to decide on")
    cities = common.cities()
    markets, bands = lr.read_definitions()
    events = [e for e in lr.live_events(markets, bands, cities, args.first, args.last) if e["city"] in sdyes.CITIES]
    sdays, _ = build_table.load_station_daily()
    bm, md = build_table.load_daily_forecasts()
    h0, h1 = sdyes.WINDOW_LOCAL_H
    rows = [r for r in lr.build(record, events, cities, sdays, bm, md, record.first, record.last, modes=("arrival",))
            if int(r["day_offset"]) == 0 and h0 <= int(r["local_hour"]) < h1]
    probs = model_probs(rows, booster)
    decisions = decisions_for(rows, events, probs, cities)
    polls = read_polls()
    rd1 = read_rd1()

    def books_at(at):
        return record.values("focus_book", at)

    def rd1_at(city, date, t):
        held = [p for at, p in rd1.get((city, date), []) if at <= t]
        return held[-1] if held else None

    ledger_rows, entries = sdyes.ledger(decisions, polls, books_at, rd1_at)
    tag = f"{args.first}_{args.last}"
    write_csv(os.path.join(args.out, f"ledger_{tag}.csv.gz"), LEDGER_COLUMNS, ledger_rows)
    entry_rows = [dict(v, who=k[0]) for k, v in sorted(entries.items())]
    write_csv(os.path.join(args.out, f"entries_{tag}.csv.gz"), LEDGER_COLUMNS, entry_rows)
    summary = {"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "version": sdyes.VERSION,
               "model_sha256": sdyes.MODEL_SHA256, "window": [args.first, args.last],
               "events": len(events), "record": [record.first, record.last], "polls": len(polls),
               **sdyes.counts(ledger_rows, entries)}
    with open(os.path.join(args.out, f"counts_{tag}.json"), "w") as fh:
        fh.write(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    main()
