"""WXPredict's remaining-day model, v2: reports counted when they arrive.

The remaining-day model (remaining.py, phase 3A) fitted once on the v2 table
(build_table.py --v2: a station report known features.REPORT_LAG_V2_S, 8 min,
after its reading, where v1 waited 20) on every eligible row (the event day
from 09h with a running maximum, all cities) with an event date up to
FIT_LAST, and saved with its inputs' hashes, as sdyes_fit.py saves v1's.

Why v2 (12 Oct, data/eval/wxpredict/report_lag_2026-10-12.json): on the same
venue decisions of 15 Sep - 11 Oct, each fold fitted only on the days before
it, the 8-minute table's model hit the winning bucket more often from 13h
and lost less log loss at every hour group than the 20-minute table's.

It is a frozen version: nothing here is refitted while it is scored (Rule 11);
a refit on later dates is a new version with its own name and hashes.

    python tools/wxpredict/wxrd_fit.py [--table data/training/wxpredict/table_v2/wxpredict_table.csv.gz]
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sys

import lightgbm as lgb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remaining  # noqa: E402
import walkforward as wf  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VERSION = "wxrd-v2"
FIT_LAST = "2026-10-11"        # every labelled date of the 12 Oct v2 table
REPORT_LAG_S = 8 * 60          # the table's rule (features.REPORT_LAG_V2_S); the json records it
TABLE_V2 = os.path.join(ROOT, "data", "training", "wxpredict", "table_v2", "wxpredict_table.csv.gz")
OUT = os.path.join(ROOT, "data", "models", "wxrd")
MODEL_FILE = f"data/models/wxrd/{VERSION}_remaining.txt.gz"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=TABLE_V2)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    with open(args.table, "rb") as fh:
        table_sha = hashlib.sha256(fh.read()).hexdigest()
    df = pd.read_csv(args.table, low_memory=False)
    df = df[df.label_unit.notna() & (df.date.astype(str) <= FIT_LAST)].reset_index(drop=True).copy()
    x_all = wf.features(df)
    el = remaining.eligible(df)
    x = remaining.features(df, x_all)[el]
    j = remaining.target(df)[el]
    model = remaining.fit(x, j, wf.SEED)
    raw = model.model_to_string().encode()
    os.makedirs(args.out, exist_ok=True)
    name = f"{VERSION}_remaining.txt.gz"
    with open(os.path.join(args.out, name), "wb") as fh:
        fh.write(gzip.compress(raw, compresslevel=9, mtime=0))
    meta = {
        "version": VERSION,
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "model_file": name,
        "model_sha256": hashlib.sha256(raw).hexdigest(),
        "table_gzip_sha256": table_sha,
        "report_lag_s": REPORT_LAG_S,
        "fit_last": FIT_LAST,
        "rows": int(el.sum()), "dates": [str(df.date[el].min()), str(df.date[el].max())],
        "cities": int(df.city_key[el].nunique()),
        "features": list(x.columns),
        "params": dict(remaining.PARAMS, seed=wf.SEED, rounds=250),
        "inc_from_hour": remaining.INC_FROM_HOUR, "k": remaining.K,
        "lightgbm": lgb.__version__,
    }
    with open(os.path.join(args.out, f"{VERSION}_remaining.json"), "w") as fh:
        fh.write(json.dumps(meta, indent=1) + "\n")
    print(json.dumps({k: v for k, v in meta.items() if k != "features"}, indent=1))
    return meta


if __name__ == "__main__":
    main()
