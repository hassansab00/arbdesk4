"""Every archived venue verdict stays readable (plan v2 P4.5).

prune_resolution_evidence archives a proof to data/archive/resolution and
deletes its row, and until 24 Sep the venue's answer went with it:
v_venue_band_resolution read the evidence table, so a pruned proof turned a
confirmed band into no answer. Measured 24 Sep: 2,948 of the 7,447 band
outcomes banked since 13 Sep had fallen out of v_verified_fact_band_outcome.

resolution_verdicts keeps each verdict (condition, tokens, winning token,
time) without the payloads, and a trigger copies every new proof into it. This
script puts back the verdicts that were archived before that trigger existed:
it reads every resolution file the archive index lists, from the repository,
and offers each row to the ledger. The ledger's key ignores a verdict it
already holds, so running it again adds nothing and it is safe every night.

Reads the repository and writes one append-only table. Deletes nothing.
"""
import csv
import gzip
import io
import json
import os
import sys

from common import log_run, upsert

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "web", "public", "archive", "index.json")
ON_CONFLICT = "condition_id,token_yes,token_no,captured_at"
KEYS = ("condition_id", "token_yes", "token_no", "winning_token", "captured_at")


def archived_files(index_path=INDEX):
    """[(asset name, path, rows the index says)] for the resolution dataset."""
    with open(index_path) as f:
        index = json.load(f)
    ds = (index.get("datasets") or {}).get("resolution") or {}
    return [(a["asset"], os.path.join(ROOT, "data", "archive", "resolution", a["asset"]), a["rows"])
            for a in ds.get("assets") or []]


def verdicts(path, asset):
    """The ledger rows in one archived file. A row that is not a verdict - a
    winning token that is neither of the two - is returned separately, never
    sent: the ledger's check would refuse the whole batch around it."""
    text = gzip.decompress(open(path, "rb").read()).decode()
    good, bad = [], []
    for r in csv.DictReader(io.StringIO(text)):
        row = {k: r.get(k) for k in KEYS}
        if not all(row.values()) or row["winning_token"] not in (row["token_yes"], row["token_no"]):
            bad.append(row)
            continue
        good.append(dict(row, source=f"archive:{asset}"))
    return good, bad


def main():
    files = archived_files()
    offered, refused, detail = 0, 0, {}
    for asset, path, indexed in files:
        if not os.path.exists(path):
            raise SystemExit(f"{asset} is in the archive index but not in the repository")
        good, bad = verdicts(path, asset)
        if len(good) + len(bad) != indexed:
            raise SystemExit(f"{asset}: the file holds {len(good) + len(bad)} rows, the index says {indexed}")
        upsert("resolution_verdicts", good, ON_CONFLICT)
        offered += len(good)
        refused += len(bad)
        detail[asset] = {"offered": len(good), "not_a_verdict": len(bad)}
        print(f"{asset}: {len(good)} verdicts offered, {len(bad)} rows that are not verdicts")
    log_run("restore_verdicts", "ok" if not refused else "attention", offered,
            {"files": len(files), "offered": offered, "not_a_verdict": refused, "per_file": detail})


if __name__ == "__main__":
    sys.exit(main())
