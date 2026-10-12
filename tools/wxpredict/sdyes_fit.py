"""The same-day YES pilot's frozen model (docs/SAME_DAY_YES_PREREG.md).

The remaining-day model (remaining.py, phase 3A) fitted once, as
walkforward.py --remaining fits each month's, on every eligible row of the
table (the event day from 09h with a running maximum, all cities) with an
event date up to FIT_LAST, and saved with its inputs' hashes. It is not
refitted during the pilot's evaluation (Rule 11: nothing is learned while it
is scored; a refit is a new version on later dates).

    python tools/wxpredict/sdyes_fit.py [--out data/models/sdyes]
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
import sdyes  # noqa: E402
import walkforward as wf  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIT_LAST = "2026-10-11"        # every labelled date of the 12 Oct table; the forward dates start 13 Oct


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "models", "sdyes"))
    args = ap.parse_args(argv)
    with open(wf.TABLE, "rb") as fh:
        table_sha = hashlib.sha256(fh.read()).hexdigest()
    df = pd.read_csv(wf.TABLE, low_memory=False)
    df = df[df.label_unit.notna() & (df.date.astype(str) <= FIT_LAST)].reset_index(drop=True).copy()
    x_all = wf.features(df)
    el = remaining.eligible(df)
    x = remaining.features(df, x_all)[el]
    j = remaining.target(df)[el]
    model = remaining.fit(x, j, wf.SEED)
    text = model.model_to_string()
    os.makedirs(args.out, exist_ok=True)
    name = f"{sdyes.VERSION}_remaining.txt.gz"
    raw = text.encode()
    with open(os.path.join(args.out, name), "wb") as fh:
        fh.write(gzip.compress(raw, compresslevel=9, mtime=0))
    meta = {
        "version": sdyes.VERSION,
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "model_file": name,
        "model_sha256": hashlib.sha256(raw).hexdigest(),
        "table_gzip_sha256": table_sha,
        "fit_last": FIT_LAST,
        "rows": int(el.sum()), "dates": [str(df.date[el].min()), str(df.date[el].max())],
        "cities": int(df.city_key[el].nunique()),
        "features": list(x.columns),
        "params": dict(remaining.PARAMS, seed=wf.SEED, rounds=250),
        "inc_from_hour": remaining.INC_FROM_HOUR, "k": remaining.K,
        "lightgbm": lgb.__version__,
    }
    with open(os.path.join(args.out, f"{sdyes.VERSION}_remaining.json"), "w") as fh:
        fh.write(json.dumps(meta, indent=1) + "\n")
    print(json.dumps({k: v for k, v in meta.items() if k != "features"}, indent=1))


if __name__ == "__main__":
    main()
