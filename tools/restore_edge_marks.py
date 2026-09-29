#!/usr/bin/env python3
"""Put back the market prices the edges prune took (plan v2 P1.6 phase 3,
step 3.1b; 29 Sep).

v_hit_ladders reads each band's YES price by 18:00 local on the eve and
v_city_hit_history its YES price before the local day. Until 20260929220000
froze those two rows (derived_edge_marks), the edges prune took them like any
superseded pricing - after the archive had exported every one of them to
data/archive/edges. This sends those files back: every archived YES edge,
whole bands per call (a band's mark is the newest of ALL its rows by the
cutoff), to restore_edge_marks (sql/ad4_97), which writes each band's
missing mark and nothing else.

  python tools/restore_edge_marks.py plan               read the files, touch nothing
  python tools/restore_edge_marks.py run                dry run: what would be written
  python tools/restore_edge_marks.py run --commit       write, then check every restored
                                                        mark against the files, refresh the
                                                        page cache, and dry-run again (0)

run needs SUPABASE_URL and SUPABASE_SERVICE_KEY, which exist only in the
repository's secrets: .github/workflows/restore_edge_marks.yml runs it.
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

ARCHIVE = os.path.join(ROOT, "data", "archive", "edges")
# A call's rows: about 90 bytes each as JSON, so ~0.5 MB a call, well inside
# PostgREST's body limit and the service role's statement timeout.
BATCH_ROWS = 5000


def read_archive(folder=ARCHIVE):
    """{band_id: [(computed_at, market_price or None, source file)]} for every
    archived YES edge. NO rows are not marks; an empty price is NULL, which a
    mark keeps as NULL."""
    bands = {}
    for path in sorted(glob.glob(os.path.join(folder, "edges-*.csv.gz"))):
        name = os.path.basename(path)
        with gzip.open(path, "rt", newline="") as fh:
            for row in csv.DictReader(fh):
                if row["side"] != "YES":
                    continue
                price = row["market_price"]
                bands.setdefault(row["band_id"], []).append(
                    (row["computed_at"], price if price != "" else None, name))
    return bands


def batches(bands, limit=BATCH_ROWS):
    """Whole bands per batch, never split across two calls; a band with more
    rows than the limit goes alone."""
    out, cur = [], []
    for band_id in sorted(bands):
        rows = [{"band_id": band_id, "computed_at": t, "market_price": p, "source": f"archive:{f}"}
                for t, p, f in bands[band_id]]
        if cur and len(cur) + len(rows) > limit:
            out.append(cur)
            cur = []
        cur.extend(rows)
    if cur:
        out.append(cur)
    return out


def ties(bands):
    """Rows of one band at the same instant: the newest would be a guess."""
    return sum(len(r) - len({t for t, _, _ in r}) for r in bands.values())


def _ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def expected_mark(rows, cutoff_at, mark):
    """The newest archived row by the cutoff: <= for the eve, < for the day,
    exactly as v_hit_ladders and v_city_hit_history read edges."""
    cut = _ts(cutoff_at)
    ok = [r for r in rows if _ts(r[0]) < cut or (mark == "eve" and _ts(r[0]) == cut)]
    return max(ok, key=lambda r: _ts(r[0])) if ok else None


def summarise(results):
    keys = ("rows", "bands", "bands_unknown", "marks_found", "already_frozen",
            "edges_holds_it", "to_write", "written")
    return {k: sum(int(r.get(k) or 0) for r in results) for k in keys}


def run(bands, commit, rpc, rest_all):
    parts = batches(bands)
    dry = summarise([rpc("restore_edge_marks", {"p_rows": b, "p_dry_run": True}, timeout=180) for b in parts])
    print(f"dry run: {dry}")
    if not commit:
        return dry
    done = summarise([rpc("restore_edge_marks", {"p_rows": b, "p_dry_run": False}, timeout=180) for b in parts])
    print(f"written: {done}")
    if done["written"] != dry["to_write"]:
        raise SystemExit(f"wrote {done['written']} marks, the dry run said {dry['to_write']}")

    # EVERY RESTORED MARK AGAINST THE FILES: the database chose it; this
    # chooses again from the same rows, with the cutoff the database stored.
    restored = rest_all("derived_edge_marks", {"select": "band_id,mark,cutoff_at,computed_at,market_price,source",
                                               "source": "like.archive:*"}, order="band_id.asc,mark.asc",
                        page_size=1000)
    wrong = 0
    for m in restored:
        e = expected_mark(bands.get(m["band_id"], []), m["cutoff_at"], m["mark"])
        same = (e is not None and _ts(e[0]) == _ts(m["computed_at"])
                and (e[1] is None) == (m["market_price"] is None)
                and (e[1] is None or float(e[1]) == float(m["market_price"]))
                and f"archive:{e[2]}" == m["source"])
        wrong += 0 if same else 1
    print(f"restored marks in the table: {len(restored)}, not the newest archived row by their cutoff: {wrong}")
    if wrong:
        raise SystemExit(f"{wrong} restored marks disagree with data/archive/edges")

    print(f"page cache: {rpc('refresh_page_cache', {}, timeout=180)}")
    again = summarise([rpc("restore_edge_marks", {"p_rows": b, "p_dry_run": True}, timeout=180) for b in parts])
    print(f"dry run after: {again}")
    if again["to_write"] != 0:
        raise SystemExit(f"a second pass would still write {again['to_write']}")
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=["plan", "run"])
    ap.add_argument("--commit", action="store_true", help="write the marks (default: dry run)")
    a = ap.parse_args()
    bands = read_archive()
    rows = sum(len(r) for r in bands.values())
    files = sorted({f for r in bands.values() for _, _, f in r})
    print(f"archive: {rows} YES edges of {len(bands)} bands in {len(files)} files, "
          f"{len(batches(bands))} calls, {ties(bands)} tied instants")
    if a.mode == "plan":
        return
    from common import rest_all, rpc
    run(bands, a.commit, rpc, rest_all)


if __name__ == "__main__":
    main()
