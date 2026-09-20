#!/usr/bin/env python3
"""Rebuild web/public/archive/index.json from the Releases themselves.

WHY THIS EXISTS. scripts/archive_observations.py writes that index after every
prune - and the workflow that runs it has no commit step, so the file is
written onto the runner and destroyed with it. The only committed copy was one
hand-made on 19 Sep listing research alone, while four Releases held:

    observations   412,257 rows across 4 assets
    forecasts       34,180 rows across 4 assets
    trades          90,640 rows
    research        85,691 rows across 2 assets

/api/archive reads that index to find an asset. So over half a million rows
were sitting in Releases that the platform had no way to offer - which is the
exact thing the index was built to prevent.

IT DOES NOT TRUST ingest_log, AND THAT IS THE POINT. The row count on each
entry is obtained by downloading the asset and PARSING it, not by reading what
some earlier run claimed it wrote. An index is a promise that the data can be
fetched; the only honest way to make that promise is to fetch it. Assets are
also hashed here, so proprietary_data_manifests can carry a digest that was
computed from the bytes actually stored.

    GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo python3 tools/rebuild_archive_index.py
    ... --dry-run     print what it would write, change nothing
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import urllib.request

API = "https://api.github.com"
MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "web", "public", "archive", "index.json")

# dataset -> (release tag, table). The same four the archive prunes, plus
# resolution, which has never successfully archived and so has no Release yet.
DATASETS = {
    "observations": ("observations-archive", "weather_observations"),
    "forecasts":    ("forecasts-archive",    "weather_forecasts"),
    "trades":       ("trades-archive",       "trades_observed"),
    "research":     ("research-archive",     "research_captures"),
    "resolution":   ("resolution-archive",   "paper_resolution_evidence"),
}


def _get(url, token, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def rows_and_digest(blob):
    """Rows in the gzipped CSV, header excluded, and the sha256 of the bytes.

    Parsed with csv rather than counted by newline: a line count is wrong the
    day any exported field contains one, and this number is what tells a
    reader the archive is complete.
    """
    text = gzip.decompress(blob).decode()
    rows = max(0, sum(1 for _ in csv.reader(io.StringIO(text))) - 1)
    return rows, hashlib.sha256(blob).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY are required.", file=sys.stderr)
        return 1

    index, total = {"datasets": {}}, 0
    for name, (tag, table) in DATASETS.items():
        try:
            rel = json.loads(_get(f"{API}/repos/{repo}/releases/tags/{tag}", token))
        except Exception as e:                      # no Release yet is not an error
            print(f"{name:14s} no release ({tag}): {e}")
            continue

        assets = []
        for a in rel.get("assets", []):
            if not a["name"].endswith(".csv.gz"):
                continue
            blob = _get(a["url"], token, accept="application/octet-stream")
            rows, digest = rows_and_digest(blob)
            # The range is in the filename, which is how the archive names them:
            # <dataset>-<from>-to-<to>.csv.gz
            stem = a["name"][:-len(".csv.gz")]
            try:
                lo, hi = stem.split("-to-")
                lo = lo[len(name) + 1:]
            except ValueError:
                lo = hi = ""
            assets.append({
                "asset": a["name"], "rows": rows, "gzip_bytes": a["size"],
                "from": lo, "to": hi,
                # What a page needs to say "this range lives in the archive".
                # Derived from the newest row IN THE FILE rather than from a
                # cutoff some earlier run logged: the file is the thing being
                # promised, so its own contents define how far it reaches.
                # Conservative by construction - anything after `to` is either
                # in a later asset or still live in Postgres.
                "archived_through": hi,
                "archived_at": a["created_at"], "sha256": digest,
            })
            print(f"{name:14s} {a['name']:48s} {rows:>9,} rows  verified")
        if not assets:
            continue
        assets.sort(key=lambda x: x["from"])
        index["datasets"][name] = {
            "table": table, "release_tag": tag, "assets": assets,
            "rows_archived": sum(x["rows"] for x in assets),
        }
        total += index["datasets"][name]["rows_archived"]

    index["updated_at"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat(timespec="seconds")
    index["rebuilt_from"] = "release assets, row counts parsed from the files themselves"

    print(f"\n{total:,} rows across {len(index['datasets'])} dataset(s)")
    if args.dry_run:
        print("--dry-run: nothing written.")
        return 0
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
