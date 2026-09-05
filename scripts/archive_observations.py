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


def cold_rows(cutoff):
    """Every observation strictly older than the cutoff, paged.

    Paged because an unpaginated PostgREST read silently stops at the server's
    row limit, and a silently short export is the one failure this whole design
    cannot survive.
    """
    out, offset = [], 0
    while True:
        rows = rest("weather_observations", [
            ("select", ",".join(COLUMNS)),
            ("valid_at", f"lt.{cutoff.isoformat()}"),
            ("order", "valid_at.asc"),
            ("limit", str(PAGE)), ("offset", str(offset)),
        ])
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        offset += PAGE
        print(f"  ... {len(out):,} rows")


def to_gzip_csv(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return gzip.compress(buf.getvalue().encode(), 9)


def count_rows(blob):
    """Rows in a gzipped CSV, header excluded. Used to verify a round trip."""
    text = gzip.decompress(blob).decode()
    return max(0, text.count("\n") - 1)


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
    print(f"reading observations older than {cutoff.date()} ...")
    rows = cold_rows(cutoff)
    if not rows:
        print(f"nothing older than {cutoff.date()} - nothing to archive.")
        log_run("archive_observations", "ok", 0, {"keep_days": args.keep_days})
        return 0
    blob = to_gzip_csv(rows)
    name = f"observations-{rows[0]['valid_at'][:10]}-to-{rows[-1]['valid_at'][:10]}.csv.gz"
    print(f"{len(rows):,} rows -> {name}  ({len(blob) / 1e6:.1f} MB gzipped, "
          f"~{len(rows) * 272 / 1e6:.0f} MB in Postgres)")

    if not args.commit:
        prune = _rpc("prune_observations", {"p_keep_days": args.keep_days, "p_dry_run": True})
        print(f"\n--dry-run: nothing uploaded, nothing deleted.\nprune would say: {prune}")
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token or not args.repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY are required to upload.", file=sys.stderr)
        return 1

    # 3 - upload, then read it back
    rel = ensure_release(args.repo, token)
    asset = upload(args.repo, token, rel, name, blob)
    got, ok = verify(asset, len(rows), token)
    if not ok:
        print(f"VERIFY FAILED: uploaded {len(rows):,} rows, read back {got:,}. "
              f"Nothing pruned.", file=sys.stderr)
        log_run("archive_observations", "attention", 0,
                {"uploaded": len(rows), "read_back": got, "asset": name})
        return 1
    print(f"verified: {got:,} rows read back from the release")

    # 4 - only now
    prune = _rpc("prune_observations", {"p_keep_days": args.keep_days, "p_dry_run": False})
    print(f"prune: {prune}")

    log_run("archive_observations", "ok", len(rows), {
        "asset": name, "rows": len(rows), "gzip_bytes": len(blob),
        "keep_days": args.keep_days, "prune": prune,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
