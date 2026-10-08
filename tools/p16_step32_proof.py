#!/usr/bin/env python3
"""Does scripts/book_history.py give the backtest the book it would have had
if nothing had been pruned? (plan v2 P1.6 phase 3, step 3.2; 29 Sep)

The repository holds every book snapshot once: data/archive/books the rows the
prune took, data/mirror/book_snapshots the rows Postgres keeps, for every
market resolved 16 days ago or more (scripts/mirror_to_repo.py). For those
markets the whole history is on disk, so:

  past     1. SQL: for each resolution date the mirror holds, book_as_of over
              that date's bands at the backtest's instant (12:00 UTC, lead 0
              and 1) against the newest kept row the mirror holds, all 29
              columns. Rows back: dates that differ. Empty = the database
              answers with exactly the mirror's rows.
           2. Here: book_history.as_of, with those answers standing in for the
              database and the live cut, against a plain scan of mirror +
              archive for the newest row, every band. Prints how many bands
              the archive answers and how much newer their book is.

  reference / check   the next real prune. `reference` writes SQL that
              fingerprints book_as_of now, for bands whose id starts with 0 or
              1 (about one in eight), at instants the prune has not reached.
              After the prune has taken those rows, `check` writes SQL that
              takes the archive's newest row per band from book_history (the
              half of as_of that reads the files) and the database's answer,
              keeps the newer, and fingerprints that. The two must be equal.

It cannot reach the database (no key in the sandbox), so it writes SQL: run
each file through the Supabase SQL tool.

Since 7 Oct (WXPredict build 2.A) a decided band's last book loses its ladder
once data/archive/ladders holds it (prune_dead_book_detail, after the ladders
dataset), and book_history.with_ladder puts it back by snapshot_id. The SQL
side reads the database, so on a DEAD_LOSER / DEAD_WINNER row whose
ladder_archived_at is set, a raw_book or no_book that reads 'null' where the
mirror says 'set' is that move, not a lost row; tests/test_book_history.py
holds the restore.

  python tools/p16_step32_proof.py past --sql-dir DIR --cut-before 2026-09-25 \\
      --cut-file data/archive/books/books-2026-09-20-to-2026-09-25.csv.gz
  python tools/p16_step32_proof.py reference --sql-dir DIR --date 2026-09-26 --date 2026-09-27
  python tools/p16_step32_proof.py check --sql-dir DIR      (reads tools/p16_step32_reference.json)
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import book_history as bh  # noqa: E402
import weather_history as wh  # noqa: E402
from backtest.engine import evaluation_instant  # noqa: E402

EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
INTS = ("snapshot_hour_utc", "bid_levels", "ask_levels")
NUMS = ("best_bid", "best_ask", "mid", "spread", "ask_usd_1c", "bid_usd_1c", "ask_usd_2c", "bid_usd_2c",
        "ask_usd_5c", "bid_usd_5c", "ask_usd_10c", "bid_usd_10c", "ask_usd_25c", "bid_usd_25c",
        "ask_total_usd", "bid_total_usd", "band_volume", "band_volume_24hr", "no_best_bid", "no_best_ask")
PREFIXES = ("0", "1")


def micros(ts):
    return (bh._ts(ts) - EPOCH) // dt.timedelta(microseconds=1)


def num(v):
    """numeric as trim_scale(round(x, 6))::text writes it."""
    if v is None:
        return "null"
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def line(r):
    """Every column the archive holds, as LINE_SQL writes it. A ladder is
    compared as present or not: the archive's are all empty (29 Sep)."""
    parts = [r["band_id"], str(micros(r["observed_at"]))]
    parts += ["null" if r.get(c) is None else str(r[c]) for c in INTS[:1]]
    parts += [r.get("market_state") or "null",
              "null" if r.get("tradeable") is None else str(r["tradeable"]).lower()]
    parts += [num(r.get(c)) for c in NUMS[:4]]
    parts += ["null" if r.get(c) is None else str(r[c]) for c in INTS[1:]]
    parts += [num(r.get(c)) for c in NUMS[4:18]]
    parts += ["null" if r.get("raw_book") is None else "set"]
    parts += [num(r.get(c)) for c in NUMS[18:]]
    parts += ["null" if r.get("no_book") is None else "set"]
    return "|".join(parts)


def _n(c):
    return f"coalesce(trim_scale(round(x.{c}, 6))::text, 'null')"


def _i(c):
    return f"coalesce(x.{c}::text, 'null')"


LINE_SQL = ("concat_ws('|', x.band_id::text, ((extract(epoch from x.observed_at) * 1000000)::bigint)::text, "
            + ", ".join([_i(INTS[0]), "coalesce(x.market_state, 'null')", "coalesce(x.tradeable::text, 'null')"]
                        + [_n(c) for c in NUMS[:4]] + [_i(c) for c in INTS[1:]] + [_n(c) for c in NUMS[4:18]]
                        + ["case when x.raw_book is null or x.raw_book = 'null'::jsonb then 'null' else 'set' end"]
                        + [_n(c) for c in NUMS[18:]]
                        + ["case when x.no_book is null or x.no_book = 'null'::jsonb then 'null' else 'set' end"])
            + ")")


def md5(lines):
    return hashlib.md5("\n".join(lines).encode()).hexdigest()


def fingerprint(rows):
    return [len(rows), md5([line(r) for r in sorted(rows, key=lambda r: r["band_id"])])]


def _rows(pattern, extra=()):
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
        with gzip.open(path, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                row = {k: bh.typed(k, v) for k, v in r.items() if k not in extra}
                row.update({k: r[k] for k in extra if k in r})
                out.append(row)
    return out


def _newest(rows, at):
    """The newest row at or before `at`; the first of a tie (the caller lists
    the database's rows first)."""
    best = None
    for r in rows:
        t = bh._ts(r["observed_at"])
        if t <= at and (best is None or t > best[0]):
            best = (t, r)
    return best[1] if best else None


# --------------------------------------------------------------------------
# past: the dates the repository holds whole
# --------------------------------------------------------------------------

PAST_SQL = """-- 1. book_as_of over each resolution date's bands, at 12:00 UTC `lead` days
-- before it, against the newest row the mirror holds for each band (all 29
-- archived columns). Rows back: groups that differ. Empty = equal.
with mine as (select split_part(key, '/', 1)::date as res, split_part(key, '/', 2)::int as lead,
                     (value->>0)::int as n, value->>1 as h from json_each('{mine}'::json)),
db as (
  select m.res, m.lead, count(x.band_id)::int as n,
         md5(string_agg({line}, E'\\n' order by x.band_id)) as h
    from mine m
    cross join lateral public.book_as_of(
      array(select b.band_id from public.bands b join public.markets k on k.market_id = b.market_id
             where k.resolution_date = m.res),
      ((m.res - m.lead)::timestamp + interval '12 hours') at time zone 'UTC') x
   group by m.res, m.lead)
select mine.res, mine.lead, db.n as db_n, mine.n as files_n, db.h = mine.h as same
  from mine left join db using (res, lead)
 where coalesce(db.n, 0) <> mine.n or (mine.n > 0 and db.h is distinct from mine.h)
 order by 1, 2;
"""


def past(args):
    kept = _rows("data/mirror/book_snapshots/*.csv.gz", extra=("snapshot_id", "mirror_resolution_date"))
    archived = _rows("data/archive/books/*.csv.gz")
    by_band_archived = {}
    for r in archived:
        by_band_archived.setdefault(r["band_id"], []).append(r)
    by_res = {}
    for r in kept:
        by_res.setdefault(r["mirror_resolution_date"], {}).setdefault(r["band_id"], []).append(r)
    print(f"mirror: {len(kept)} kept rows, {len(by_res)} resolution dates; archive: {len(archived)} rows")

    cut = {"before": args.cut_before, "file": args.cut_file}
    bh.reset()
    wh._boundary[bh.DATASET] = cut

    mine, report, wrong = {}, [], 0
    for res in sorted(by_res):
        bands = by_res[res]
        for lead in (0, 1):
            at = evaluation_instant(dt.date.fromisoformat(res), lead)
            db = {b: _newest(rows, at) for b, rows in bands.items()}
            db = {b: r for b, r in db.items() if r is not None}
            mine[f"{res}/{lead}"] = fingerprint(list(db.values()))

            got = bh.as_of(sorted(bands), at, rpc_fn=lambda fn, p, db=db: list(db.values()),
                           rest_fn=None)
            got = {r["band_id"]: r for r in got}
            truth = {b: _newest(rows + by_band_archived.get(b, []), at) for b, rows in bands.items()}
            truth = {b: r for b, r in truth.items() if r is not None}
            bad = [b for b in truth if b not in got or line(got[b]) != line(truth[b])] + \
                  [b for b in got if b not in truth]
            wrong += len(bad)
            newer = [(bh._ts(got[b]["observed_at"]) - bh._ts(db[b]["observed_at"])).total_seconds() / 3600
                     for b in got if b in db and got[b] is not db[b]]
            fresh = [b for b in got if b not in db]
            age_db = [(at - bh._ts(r["observed_at"])).total_seconds() / 3600 for r in db.values()]
            age_got = [(at - bh._ts(r["observed_at"])).total_seconds() / 3600 for r in got.values()]
            report.append((res, lead, len(db), len(got), len(newer) + len(fresh), len(bad),
                           statistics.median(age_db) if age_db else None,
                           statistics.median(age_got) if age_got else None,
                           statistics.median(newer) if newer else None, max(newer) if newer else None))

    if args.reference_out:
        # The truth in `reference`'s shape, for `check` to be tried on dates
        # whose rows the archive already holds.
        out = {}
        for res in args.date:
            at = evaluation_instant(dt.date.fromisoformat(res), 1)
            bands = {b: rows for b, rows in by_res.get(res, {}).items() if b[:1] in PREFIXES}
            truth = [t for t in (_newest(rows + by_band_archived.get(b, []), at) for b, rows in bands.items()) if t]
            truth.sort(key=lambda r: r["band_id"])
            out[res] = {"n": len(truth), "h": md5([line(r) for r in truth]),
                        "h2": md5([hashlib.md5(line(r).encode()).hexdigest()[:16] for r in truth]),
                        "bands": sorted(b[:KEY] for b in bands)}
        with open(args.reference_out, "w") as fh:
            json.dump(out, fh, indent=1)
        print(f"wrote {args.reference_out}: " + ", ".join(f"{r} {v['n']}" for r, v in out.items()))

    os.makedirs(args.sql_dir, exist_ok=True)
    path = os.path.join(args.sql_dir, "1_past.sql")
    with open(path, "w") as fh:
        fh.write(PAST_SQL.format(mine=json.dumps(mine, separators=(",", ":")), line=LINE_SQL))
    print(f"wrote {path} ({len(mine)} groups)")
    print("res        lead  db_bands  answered  from_archive  wrong  median_age_db_h  median_age_h  "
          "median_gain_h  max_gain_h")
    for r in report:
        print("  ".join(str(round(v, 2)) if isinstance(v, float) else str(v) for v in r))
    print(f"bands whose answer is not the newest row of mirror + archive: {wrong}")
    print(f"files opened: {bh.stats['files_opened']}")
    return 1 if wrong else 0


# --------------------------------------------------------------------------
# reference / check: the next real prune
# --------------------------------------------------------------------------

# A row as a 16-digit hash of its line, so the check carries 16 characters a
# band rather than the whole row; `h` stays the hash of the lines themselves.
H16 = "left(md5({line}), 16)"
KEY = 13                      # characters of a band id that name it here

REF_SQL = """-- The database's answer now: book_as_of over the bands of each resolution
-- date whose id starts with {prefixes}, at 12:00 UTC the day before (lead 1).
-- Write the rows back into the reference file; `check` must reproduce n, h2.
with d as (select unnest(array[{dates}]::date[]) as res),
bands as (select d.res, bd.band_id
            from d join public.markets k on k.resolution_date = d.res
            join public.bands bd on bd.market_id = k.market_id
           where left(bd.band_id::text, 1) = any(array[{plist}])),
x as (
  select d.res, b.*
    from d cross join lateral public.book_as_of(
      array(select band_id from bands where bands.res = d.res),
      ((d.res - 1)::timestamp + interval '12 hours') at time zone 'UTC') b)
select d.res,
       (select count(*) from x where x.res = d.res) as n,
       (select md5(string_agg({line}, E'\\n' order by x.band_id)) from x where x.res = d.res) as h,
       (select md5(string_agg({h16}, E'\\n' order by x.band_id)) from x where x.res = d.res) as h2,
       (select string_agg(left(band_id::text, {key}), ',' order by band_id) from bands where bands.res = d.res) as bands,
       (select count(distinct left(band_id::text, {key})) = count(*) from bands where bands.res = d.res) as keys_unique
  from d order by 1;
"""

CHECK_SQL = """-- After the prune: per band, the newer of the database's answer and the
-- archive's newest row at or before the instant (book_history's own lookup
-- over the files in this checkout; a tie is the database's), fingerprinted
-- as `reference` did. `same` must be true for every date.
with ref as (select key::date as res, (value->>0)::int as n, value->>1 as h2 from json_each('{ref}'::json)),
a as (select split_part(key, '/', 2) as k, split_part(key, '/', 1)::date as res,
             (value->>0)::bigint as us, value->>1 as h16
        from json_each('{archive}'::json)),
d as (select res from ref),
bands as (select d.res, bd.band_id
            from d join public.markets k on k.resolution_date = d.res
            join public.bands bd on bd.market_id = k.market_id
           where left(bd.band_id::text, 1) = any(array[{plist}])),
x as (
  select d.res, x.band_id, ((extract(epoch from x.observed_at) * 1000000)::bigint) as us, {h16} as h16
    from d cross join lateral public.book_as_of(
      array(select band_id from bands where bands.res = d.res),
      ((d.res - 1)::timestamp + interval '12 hours') at time zone 'UTC') x),
m as (
  select bands.res, bands.band_id,
         case when a.us is not null and (x.us is null or a.us > x.us) then a.h16 else x.h16 end as h16,
         (a.us is not null and (x.us is null or a.us > x.us)) as from_archive
    from bands left join x on x.band_id = bands.band_id and x.res = bands.res
    left join a on a.k = left(bands.band_id::text, {key}) and a.res = bands.res)
select ref.res, count(m.h16) as n, ref.n as ref_n,
       md5(string_agg(m.h16, E'\\n' order by m.band_id) filter (where m.h16 is not null)) = ref.h2 as same,
       count(*) filter (where m.from_archive) as from_archive
  from ref join m using (res) group by ref.res, ref.n, ref.h2 order by 1;
"""


def _dates_sql(dates):
    return ", ".join(f"'{d}'" for d in dates)


def _fmt(sql, **kw):
    return sql.format(prefixes=" or ".join(PREFIXES), plist=", ".join(f"'{p}'" for p in PREFIXES),
                      line=LINE_SQL, h16=H16.format(line=LINE_SQL), key=KEY, **kw)


def reference(args):
    os.makedirs(args.sql_dir, exist_ok=True)
    path = os.path.join(args.sql_dir, "reference.sql")
    with open(path, "w") as fh:
        fh.write(_fmt(REF_SQL, dates=_dates_sql(args.date)))
    print(f"wrote {path}")
    return 0


def check(args):
    """The archive's candidate per reference band: newest_in_file over every
    file, as as_of reads them, if it is from the six hours before the instant.
    Every reference answer is newer than that (25 Sep 09:26Z at the oldest,
    against 12:00Z), so a row the window leaves out could only have lost to
    the database's answer or made the check fail, never made it pass."""
    with open(args.reference) as fh:
        ref = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    files = [p for _, _, p in wh.archive_files(bh.DATASET, ROOT)]
    candidates = {}
    for res, spec in sorted(ref.items()):
        at = evaluation_instant(dt.date.fromisoformat(res), 1)
        keys = set(spec["bands"])
        bands = set()
        for p in files:
            bands.update(b for b in bh._file_index(p) if b[:KEY] in keys)
        for band in sorted(bands):
            best = None
            for p in files:
                hit = bh.newest_in_file(p, band, at)
                if hit and (best is None or hit[0] > best[0]):
                    best = hit
            if best and best[0] >= at - dt.timedelta(hours=6):
                candidates[f"{res}/{band[:KEY]}"] = [micros(best[1]["observed_at"]),
                                                     hashlib.md5(line(best[1]).encode()).hexdigest()[:16]]
    os.makedirs(args.sql_dir, exist_ok=True)
    path = os.path.join(args.sql_dir, "check.sql")
    with open(path, "w") as fh:
        fh.write(_fmt(CHECK_SQL, ref=json.dumps({r: [s["n"], s["h2"]] for r, s in ref.items()}),
                      archive=json.dumps(candidates, separators=(",", ":"))))
    print(f"wrote {path} ({len(candidates)} archive candidates)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=["past", "reference", "check"])
    ap.add_argument("--sql-dir", required=True)
    ap.add_argument("--cut-before", help="the newest books prune's archived_through date (past)")
    ap.add_argument("--cut-file", help="that prune's file (past)")
    ap.add_argument("--date", action="append", default=[], help="resolution date (reference; past with --reference-out)")
    ap.add_argument("--reference-out", help="past: also write the truth for --date in reference's shape")
    ap.add_argument("--reference", default=os.path.join(ROOT, "tools", "p16_step32_reference.json"),
                    help="what `reference` returned (check)")
    a = ap.parse_args()
    sys.exit({"past": past, "reference": reference, "check": check}[a.mode](a))


if __name__ == "__main__":
    main()
