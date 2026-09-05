"""
Move cold observations out of Postgres and into a GitHub Release.

WHY. weather_observations is 98% of this database - 184 MB of a 500 MB free
tier at 710k rows, or 272 bytes a row. A Postgres row carries a 24-byte header,
per-column length bytes and index entries; the same data as gzipped CSV is
3.6 MB. Fifty-one times smaller, for data nobody queries interactively.

WHERE IT GOES. A GitHub Release asset on this repo: free, 2 GB per asset,
unlimited assets, durable, versioned, and already inside the pipeline that
produced the data. No new account, no new credential, no new bill.

(The idea of hiding data in YouTube uploads is real and people do it. It is
also against their terms, unqueryable, and takes minutes per megabyte. The
instinct - cold storage somewhere free - is right; this is the version that
works.)

WHAT MAKES IT SAFE, in order and without exception:

  1 refresh the feature cache, so every city-day about to lose its raw rows
    exists as a derived row first
  2 export the cold rows to gzipped CSV
  3 upload, then RE-DOWNLOAD and count the rows back
  4 only then prune

Step 3 is the one that matters. An upload that returns 201 and stores a
truncated file would otherwise be discovered months later, by a model with a
hole in it. prune_observations() independently refuses if step 1 did not cover
the range, so the guard exists on both sides.

  python scripts/archive_observations.py --keep-days 180 --dry-run
  python scripts/archive_observations.py --keep-days 180 --commit
"""
import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import sys

import requests

from common import rest, log_run, rpc, refresh_feature_cache, _cfg, _headers

COLUMNS = ["city_key", "station", "valid_at", "temp_c", "temp_f", "dewpoint_c",
           "humidity", "wind_speed", "wind_dir_deg", "precip", "cloud_cover",
           "pressure_hpa", "source"]

API = "https://api.github.com"
TAG = "observations-archive"
PAGE = 50000


def _rpc(fn, params=None):
    # common.rpc, so a Postgres error reaches the log instead of being replaced
    # by "500 Server Error for url: ...".
    return rpc(fn, params, timeout=600)


def export_cold(cutoff):
    """Every observation strictly older than the cutoff, as a gzipped CSV.

    KEYSET PAGING ON THE PRIMARY KEY, and that is the whole point of this
    function. It used to page with OFFSET over `order=valid_at.asc`, and
    valid_at is nowhere near unique - 37 cities times two sources share every
    timestamp. Postgres gives no order among ties, so rows at a page boundary
    could be returned twice or not at all, and OFFSET would then walk past the
    ones it skipped.

    That is not a slow query, it is silent data loss: a skipped row is never
    written to the archive, and the prune below deletes by DATE, so it deletes
    that row anyway. The round-trip check downstream cannot catch it either,
    because it compares the uploaded file against this same short list. The
    one failure the design cannot survive, reintroduced by the paging that was
    written to prevent it.

    obs_id is the primary key, so `order=obs_id.asc` is a total order and
    `obs_id=gt.<last>` cannot skip or repeat. It is also O(1) per page instead
    of O(offset), which matters at 600k rows.

    Streams into the CSV rather than accumulating a list of dicts: the same
    export held ~600 MB of Python objects before being copied into a string and
    then gzipped.

    Returns (gzip blob, row count, earliest valid_at, latest valid_at).
    """
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()

    n, after, lo, hi = 0, None, None, None
    while True:
        params = [
            ("select", ",".join(["obs_id"] + COLUMNS)),
            ("valid_at", f"lt.{cutoff.isoformat()}"),
            ("order", "obs_id.asc"),
            ("limit", str(PAGE)),
        ]
        if after is not None:
            params.append(("obs_id", f"gt.{after}"))
        rows = rest("weather_observations", params)
        if not rows:
            break
        for r in rows:
            w.writerow(r)
            v = r.get("valid_at")
            if v:
                if lo is None or v < lo:
                    lo = v
                if hi is None or v > hi:
                    hi = v
        n += len(rows)
        after = rows[-1]["obs_id"]
        if len(rows) < PAGE:
            break
        print(f"  ... {n:,} rows")

    return gzip.compress(buf.getvalue().encode(), 9), n, lo, hi


def count_rows(blob):
    """Rows in a gzipped CSV, header excluded. Used to verify a round trip.

    Parsed rather than counted by newline: a line count is wrong the day any
    exported field contains one, and this number is the only thing standing
    between a truncated upload and a permanent delete.
    """
    text = gzip.decompress(blob).decode()
    return max(0, sum(1 for _ in csv.reader(io.StringIO(text))) - 1)


def gh(repo, token, method, path, **kw):
    r = requests.request(method, f"{API}/repos/{repo}{path}",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/vnd.github+json"},
                         timeout=300, **kw)
    return r


def ensure_release(repo, token):
    r = gh(repo, token, "GET", f"/releases/tags/{TAG}")
    if r.status_code == 200:
        return r.json()
    r = gh(repo, token, "POST", "/releases", json={
        "tag_name": TAG, "name": "Observation archive",
        "body": ("Cold weather observations, pruned from Supabase to stay inside the free tier.\n\n"
                 "One gzipped CSV per archive run, named by the range it covers. "
                 "Restore with scripts/archive_observations.py --restore <asset>."),
        "prerelease": True,
    })
    r.raise_for_status()
    return r.json()


def upload(repo, token, release, name, blob):
    # Replace an asset of the same name rather than letting GitHub silently
    # refuse the upload with 422.
    for a in release.get("assets", []):
        if a["name"] == name:
            gh(repo, token, "DELETE", f"/releases/assets/{a['id']}")
    url = release["upload_url"].split("{")[0] + f"?name={name}"
    r = requests.post(url, headers={"Authorization": f"Bearer {token}",
                                    "Content-Type": "application/gzip"},
                      data=blob, timeout=600)
    r.raise_for_status()
    return r.json()


def verify(asset, expect_rows, token):
    """Download it back and count. An upload that returns 201 and stored a
    truncated file is the failure this exists to catch."""
    r = requests.get(asset["url"],
                     headers={"Authorization": f"Bearer {token}",
                              "Accept": "application/octet-stream"},
                     timeout=600)
    r.raise_for_status()
    got = count_rows(r.content)
    return got, got == expect_rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep-days", type=int, default=180,
                    help="days of raw observations to leave in Postgres")
    ap.add_argument("--commit", action="store_true",
                    help="actually upload and prune (default is a dry run)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = ap.parse_args()

    if args.keep_days < 30:
        print("--keep-days below 30 leaves nothing to model on", file=sys.stderr)
        return 1

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.keep_days)

    # 1 - the cache must cover what is about to go
    try:
        cache = refresh_feature_cache()
    except Exception as e:
        print(f"refresh_feature_cache failed ({e}). Not archiving - the derived "
              f"rows are what survives the prune.", file=sys.stderr)
        log_run("archive_observations", "attention", 0, {"error": str(e)})
        return 1

    # 2 - export
    print(f"reading observations older than {cutoff.isoformat()} ...")
    blob, n_rows, lo, hi = export_cold(cutoff)
    if not n_rows:
        print(f"nothing older than {cutoff.date()} - nothing to archive.")
        log_run("archive_observations", "ok", 0, {"keep_days": args.keep_days})
        return 0
    # From the min/max seen, not from the first and last row: the export is
    # ordered by obs_id now, which is not chronological.
    name = f"observations-{lo[:10]}-to-{hi[:10]}.csv.gz"
    print(f"{n_rows:,} rows -> {name}  ({len(blob) / 1e6:.1f} MB gzipped, "
          f"~{n_rows * 272 / 1e6:.0f} MB in Postgres)")

    # THE SAME INSTANT, both times. Letting the database recompute its own
    # cutoff deletes the minutes that passed while this ran - unarchived.
    prune_args = {"p_keep_days": args.keep_days, "p_before": cutoff.isoformat()}

    if not args.commit:
        prune = _rpc("prune_observations", {**prune_args, "p_dry_run": True})
        print(f"\n--dry-run: nothing uploaded, nothing deleted.\nprune would say: {prune}")
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token or not args.repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY are required to upload.", file=sys.stderr)
        return 1

    # 3 - upload, then read it back
    rel = ensure_release(args.repo, token)
    asset = upload(args.repo, token, rel, name, blob)
    got, ok = verify(asset, n_rows, token)
    if not ok:
        print(f"VERIFY FAILED: uploaded {n_rows:,} rows, read back {got:,}. "
              f"Nothing pruned.", file=sys.stderr)
        log_run("archive_observations", "attention", 0,
                {"uploaded": n_rows, "read_back": got, "asset": name})
        return 1
    print(f"verified: {got:,} rows read back from the release")

    # 4 - only now, and only as far back as what was actually archived
    prune = _rpc("prune_observations", {**prune_args, "p_dry_run": False})
    print(f"prune: {prune}")
    if not (prune or {}).get("ok"):
        print(f"PRUNE REFUSED: {prune}", file=sys.stderr)
        log_run("archive_observations", "attention", n_rows,
                {"asset": name, "rows": n_rows, "prune": prune})
        return 1

    log_run("archive_observations", "ok", n_rows, {
        "asset": name, "rows": n_rows, "gzip_bytes": len(blob),
        "keep_days": args.keep_days, "archived_through": cutoff.isoformat(),
        "prune": prune,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
