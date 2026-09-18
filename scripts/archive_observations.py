"""
Move cold rows out of Postgres and into a GitHub Release.

THREE TABLES NOW. The archive covered the two weather tables and grew stale
against its own database: at 436 MB of a 500 MB tier, the 180-day window it
defaulted to had nothing left to take, and the table that had overtaken both
was not in the set at all.

    trades_observed         91 MB   156,008 rows    90,640 older than 90 days
    weather_observations    57 MB   239,617 rows   114,283 older than 90 days
    weather_forecasts       33 MB    81,693 rows    31,787 older than 90 days

    (at 180 days those same three give 0, 2,522 and 700 - which is why the
     default is ninety. The window has to be shorter than the history.)

trades_observed is the one that matters most and was the last to be covered.
All 91 MB of it is LIVE rows - n_dead_tup is 0, nothing ever updates or
deletes a trade - and 55 MB of that is indexes. So there is no bloat to
reclaim there, only rows nothing reads: v_band_volume and v_city_volume, its
only two consumers, both cut at settings.volume_thresholds.lookback_hours,
which is 24.

WHY IT SHRINKS SO FAR. A Postgres row carries a 24-byte header, per-column
length bytes, and an entry in every index on the table. Indexes are 78 MB of
weather_forecasts' 124 and 59 MB of weather_observations' 121, so removing 79%
of the rows returns far more than the heap figure alone suggests. The same
data as gzipped CSV is a few megabytes.

  python scripts/archive_observations.py --keep-days 90 --dry-run
  python scripts/archive_observations.py --keep-days 90 --table trades --commit

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

Loading an archive back into local PostgreSQL: docs/local_archive.md.
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

API = "https://api.github.com"
PAGE = 50000

# --- WHAT CAN BE ARCHIVED, AND HOW EACH ONE IS PAGED ----------------------
#
# weather_forecasts is now the LARGER of the two - 124 MB against 121 MB, and
# 79% of it is older than six months - so archiving only observations left the
# bigger half behind. Both follow the identical four steps; only the key, the
# cutoff column and the prune guard differ, so they are data rather than a
# second copy of the script.
#
# `pk` must be the PRIMARY KEY. Keyset paging on it is what makes the export
# safe: the cutoff columns are nowhere near unique (37 cities x 2 sources
# share a timestamp), Postgres gives no order among ties, and OFFSET paging
# over a non-unique order silently skips rows the prune then deletes anyway.
TABLES = {
    "observations": {
        "table": "weather_observations",
        "pk": "obs_id",
        "cutoff_col": "valid_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_observations",
        "tag": "observations-archive",
        "columns": ["city_key", "station", "valid_at", "temp_c", "temp_f",
                    "dewpoint_c", "humidity", "wind_speed", "wind_dir_deg",
                    "precip", "cloud_cover", "pressure_hpa", "source"],
        "bytes_per_row": 272,
    },
    "forecasts": {
        "table": "weather_forecasts",
        "pk": "forecast_id",
        "cutoff_col": "for_date",
        # for_date is a DATE, so the cutoff is sent as one. Passing a
        # timestamp would compare a date to a timestamp and shift the boundary
        # by the time of day the job happened to run.
        "cutoff_is_date": True,
        "prune_rpc": "prune_forecasts",
        "tag": "forecasts-archive",
        "columns": ["city_key", "model", "run_at", "observed_at", "for_date",
                    "lead_days", "forecast_max_c", "variables", "source"],
        "bytes_per_row": 140,
    },
    # THE LARGEST TABLE IN THE DATABASE, and until now the archive did not
    # know it existed. 91 MB of 436, 156,008 rows, 90,640 of them older than
    # ninety days, and never vacuumed once - not by hand, not by autovacuum.
    #
    # Nothing live reads a trade older than a day. v_band_volume and
    # v_city_volume both cut at settings.volume_thresholds.lookback_hours,
    # which is 24. What is permanent is archive_daily_city_presence, written
    # by a trigger on every insert, and prune_trades refuses unless it already
    # covers every city-day being removed.
    "trades": {
        "table": "trades_observed",
        "pk": "trade_id",
        "cutoff_col": "traded_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_trades",
        "tag": "trades-archive",
        "columns": ["band_id", "condition_id", "traded_at", "price", "size",
                    "side", "proxy_wallet", "ingested_at", "city_key",
                    "token_id", "observed_at"],
        "bytes_per_row": 583,
    },
    # THE FASTEST-GROWING TABLE, and the one the 90-day default cannot touch.
    #
    # 78,291 rows and 92 MB in FIVE DAYS - about 18 MB a day, on a 500 MB tier
    # with 46 MB left. Every other table here has months of history and a
    # ninety-day window takes a third of it; research_captures has no row
    # older than the 12th, so ninety days, or thirty, or even seven, archives
    # exactly nothing. That is why keep_days is per table now.
    #
    # It is not duplication - payload_hash already dedupes, and all 78,291
    # payloads are distinct. It is one capture per row of each source
    # relation, six times a day: band_probabilities alone is 30,914 of them.
    # The evidence is real, it just does not need to sit in Postgres to be
    # evidence. It goes to the Release like everything else here and is read
    # back from there.
    #
    # THE GUARD IS DIFFERENT because nothing derived survives this one. There
    # is no feature cache and no presence rollup to check against - what makes
    # a prune safe is the verified round-trip upload, enforced by
    # p_expected_rows, plus a refusal to delete any row a stored integrity
    # manifest has already hashed. See prune_research_captures.
    "research": {
        "table": "research_captures",
        "pk": "capture_id",
        "cutoff_col": "captured_at",
        "cutoff_is_date": False,
        "prune_rpc": "prune_research_captures",
        "tag": "research-archive",
        "columns": ["command_key", "captured_at", "engine_version", "provenance",
                    "source_relation", "source_key", "payload", "payload_hash"],
        "bytes_per_row": 1230,
        # TWO DAYS, which is the floor prune_research_captures allows, and the
        # arithmetic is why. The recent rate is ~25,000 rows a day, about
        # 29 MB: 12,259 on the 12th, then 25,808 on the 15th and 24,510 on the
        # 16th as the board grew. Four days of that is 116 MB steady state
        # against 46 MB of headroom; two days is ~58 MB and holds.
        #
        # Two days is also the shortest window that is still honest - a
        # capture written this morning is still being read - so there is
        # deliberately no room left to tighten it further. If this table needs
        # to be smaller than two days hold, the answer is capturing less, not
        # keeping less: band_probabilities is 30,914 of the 78,291 rows
        # because every band is captured on every one of the six cycles a day,
        # and that is the number to change.
        "keep_days": 2,
        # The other three exist so a model has history to train on, and
        # refresh_feature_cache is what preserves it. This one has no derived
        # form - the capture IS the artefact - so the cache step is not a
        # precondition for it.
        "needs_feature_cache": False,
    },
}


def _rpc(fn, params=None):
    # common.rpc, so a Postgres error reaches the log instead of being replaced
    # by "500 Server Error for url: ...".
    return rpc(fn, params, timeout=600)


def export_cold(spec, cutoff):
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
    cols, pk, cut_col = spec["columns"], spec["pk"], spec["cutoff_col"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()

    n, after, lo, hi = 0, None, None, None
    while True:
        params = [
            ("select", ",".join([pk] + cols)),
            (cut_col, f"lt.{cutoff.isoformat()}"),
            ("order", f"{pk}.asc"),
            ("limit", str(PAGE)),
        ]
        if after is not None:
            params.append((pk, f"gt.{after}"))
        rows = rest(spec["table"], params)
        if not rows:
            break
        for r in rows:
            w.writerow(r)
            v = r.get(cut_col)
            if v:
                if lo is None or v < lo:
                    lo = v
                if hi is None or v > hi:
                    hi = v
        n += len(rows)
        next_after = rows[-1][pk]
        if next_after == after:
            raise RuntimeError(
                f"{spec['table']} archive pagination made no progress at {after}"
            )
        after = next_after
        # PostgREST can silently cap a requested page below PAGE. Keep walking
        # until an empty page proves the keyset is exhausted.
        if n % 25000 < len(rows):
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


def ensure_release(repo, token, spec):
    tag = spec["tag"]
    r = gh(repo, token, "GET", f"/releases/tags/{tag}")
    if r.status_code == 200:
        return r.json()
    r = gh(repo, token, "POST", "/releases", json={
        "tag_name": tag, "name": f"{spec['table']} archive",
        "body": (f"Cold rows from {spec['table']}, pruned from Supabase to stay inside "
                 "the free tier.\n\nOne gzipped CSV per archive run, named by the range "
                 "it covers. The header is the column list, so it loads straight into "
                 "PostgreSQL with \\copy - see docs/local_archive.md."),
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
    # NO GLOBAL DEFAULT ANY MORE. Ninety days is right for three tables with
    # months of history and useless for research_captures, whose entire
    # history is five days - the same flag either archives nothing from it or
    # takes far too much from the others. Each table carries its own window;
    # this flag, when given, overrides every one of them.
    ap.add_argument("--keep-days", type=int, default=None,
                    help="override every table's own window (default: each "
                         "table's own - 90 days, or 4 for research captures)")
    ap.add_argument("--commit", action="store_true",
                    help="actually upload and prune (default is a dry run)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    # "both" predates trades_observed and still means every table, because a
    # saved workflow_dispatch and anything scripted against it still send it.
    ap.add_argument("--table", choices=sorted(TABLES) + ["all", "both"], default="all",
                    help="which archive to run (default: all)")
    args = ap.parse_args()

    if args.keep_days is not None and args.keep_days < 1:
        print("--keep-days must be at least 1", file=sys.stderr)
        return 1

    names = sorted(TABLES) if args.table in ("all", "both") else [args.table]
    worst = 0
    for name in names:
        print(f"\n=== {name} ===")
        rc = run_one(TABLES[name], name, args)
        worst = max(worst, rc)
    return worst


def run_one(spec, name, args):
    """One table, the same four steps: cache, export, verify, prune."""
    job = f"archive_{name}"
    keep_days = args.keep_days if args.keep_days is not None else spec.get("keep_days", 90)
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
    # A date column needs a date cutoff; comparing it to a timestamp shifts the
    # boundary by whatever time of day this happened to run.
    cutoff = stamp.date() if spec["cutoff_is_date"] else stamp

    # 1 - the cache must cover what is about to go
    #
    # Only for the tables whose derived rows are what survives the prune.
    # research_captures has no derived form - the capture IS the artefact, and
    # what survives is the Release asset - so demanding a cache refresh there
    # would block the archive on a step that protects nothing.
    if spec.get("needs_feature_cache", True):
        try:
            refresh_feature_cache()
        except Exception as e:
            print(f"refresh_feature_cache failed ({e}). Not archiving - the derived "
                  f"rows are what survives the prune.", file=sys.stderr)
            log_run(job, "attention", 0, {"error": str(e)})
            return 1

    # 2 - export
    print(f"reading {spec['table']} older than {cutoff.isoformat()} ...")
    blob, n_rows, lo, hi = export_cold(spec, cutoff)
    if not n_rows:
        print(f"nothing older than {cutoff} - nothing to archive.")
        log_run(job, "ok", 0, {"keep_days": keep_days})
        return 0
    # From the min/max seen, not from the first and last row: the export is
    # ordered by obs_id now, which is not chronological.
    asset_name = f"{name}-{str(lo)[:10]}-to-{str(hi)[:10]}.csv.gz"
    print(f"{n_rows:,} rows -> {asset_name}  ({len(blob) / 1e6:.1f} MB gzipped, "
          f"~{n_rows * spec['bytes_per_row'] / 1e6:.0f} MB in Postgres)")

    # THE SAME INSTANT, both times. Letting the database recompute its own
    # cutoff deletes the minutes that passed while this ran - unarchived.
    prune_args = {
        "p_keep_days": keep_days,
        "p_before": cutoff.isoformat(),
        "p_expected_rows": n_rows,
    }

    # The database must independently agree with the exported row count before
    # we upload anything. This catches silent PostgREST page caps and makes a
    # partial export impossible to turn into a wider delete.
    preflight = _rpc(spec["prune_rpc"], {**prune_args, "p_dry_run": True})
    if (
        not (preflight or {}).get("ok")
        or preflight.get("would_delete") != n_rows
    ):
        print(
            f"ARCHIVE COUNT MISMATCH: exported {n_rows:,} rows but prune "
            f"preflight returned {preflight}. Nothing uploaded or deleted.",
            file=sys.stderr,
        )
        log_run(job, "attention", n_rows, {
            "rows": n_rows, "preflight": preflight,
            "cutoff": cutoff.isoformat(),
        })
        return 1

    if not args.commit:
        print(
            "\n--dry-run: nothing uploaded, nothing deleted."
            f"\nprune would say: {preflight}"
        )
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token or not args.repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY are required to upload.", file=sys.stderr)
        return 1

    # 3 - upload, then read it back
    rel = ensure_release(args.repo, token, spec)
    asset = upload(args.repo, token, rel, asset_name, blob)
    got, ok = verify(asset, n_rows, token)
    if not ok:
        print(f"VERIFY FAILED: uploaded {n_rows:,} rows, read back {got:,}. "
              f"Nothing pruned.", file=sys.stderr)
        log_run(job, "attention", 0,
                {"uploaded": n_rows, "read_back": got, "asset": asset_name})
        return 1
    print(f"verified: {got:,} rows read back from the release")

    # 4 - only now, and only as far back as what was actually archived
    prune = _rpc(spec["prune_rpc"], {**prune_args, "p_dry_run": False})
    print(f"prune: {prune}")
    if (
        not (prune or {}).get("ok")
        or prune.get("deleted") != n_rows
    ):
        print(f"PRUNE REFUSED OR COUNT CHANGED: {prune}", file=sys.stderr)
        log_run(job, "attention", n_rows,
                {"asset": asset_name, "rows": n_rows, "prune": prune})
        return 1

    log_run(job, "ok", n_rows, {
        "asset": asset_name, "rows": n_rows, "gzip_bytes": len(blob),
        "keep_days": keep_days, "archived_through": cutoff.isoformat(),
        "prune": prune,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
