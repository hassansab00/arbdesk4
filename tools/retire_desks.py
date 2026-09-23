"""Retire every existing paper desk, keeping all of its history (plan v2, P0.3).

Hassan decided on 23 Sep to retire every paper desk and rebuild the trading
engine (docs/AD4_IMPROVEMENT_PLAN.md, P0.3 and P5). Retiring deletes nothing:
every row stays in Postgres. It also goes into the repository first, so the
desks' whole record exists somewhere a later cleanup cannot reach.

Three stages, in this order, never fused:

    python tools/retire_desks.py export    # every desk's rows -> data/archive/paper_desks/<date>/
    python tools/retire_desks.py verify    # re-read the files: sha256, row counts, unique keys,
                                           # and each count against the live table
    python tools/retire_desks.py retire    # only if the export is on origin/main: retire each
                                           # desk, then disable s1-s9 with the reason on record

`retire` refuses unless the manifest and every file it names are on the
remote branch. A local commit is not an archive: the runner that made it can
be discarded (plan P1.5 found exactly that gap in the observation archive).

The database side is 20260923100000_a_retired_desk_stays_retired.sql:
paper_desk_retire() refuses a desk that still holds shares, a live order or an
open plan, and a retired row cannot change afterwards.

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY. .github/workflows/retire_desks.yml
runs all three stages by hand (workflow_dispatch only).
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

OUT = os.path.join("data", "archive", "paper_desks")
REASON = "plan v2: engine rebuild"
MANIFEST = "manifest.json"

# Every table that holds a desk's record, with a unique, stable ordering so
# the export is complete under pagination and byte-identical when re-run.
# paper_activity is the cash history: every cash movement is one row there.
TABLES = [
    ("paper_accounts", "account_id"),
    ("paper_trade_plans", "plan_id"),
    ("paper_orders", "order_id"),
    ("paper_positions", "account_id,band_id,side"),
    ("paper_position_settlements", "account_id,band_id,side"),
    ("paper_trades", "trade_id"),
    ("paper_activity", "event_id"),
]

# The strategies P0.3 switches off: the old nine, s1_... to s9_...
OLD_STRATEGY = re.compile(r"^s[1-9]_")


def encode(rows):
    """JSON Lines, gzipped with a fixed mtime, so the same rows give the same bytes."""
    body = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
    return gzip.compress(body.encode("utf-8"), mtime=0)


def decode(blob):
    return [json.loads(line) for line in gzip.decompress(blob).decode("utf-8").splitlines() if line]


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def key_of(row, order):
    return tuple(str(row.get(k)) for k in order.split(","))


def check_file(path, entry, order):
    """What is wrong with one exported file, or [] when it is what the manifest says."""
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError as e:
        return [f"{path}: cannot read ({e})"]
    problems = []
    if sha256(blob) != entry["sha256"]:
        problems.append(f"{path}: sha256 {sha256(blob)[:12]} is not the manifest's {entry['sha256'][:12]}")
    try:
        rows = decode(blob)
    except (OSError, ValueError, EOFError) as e:
        return problems + [f"{path}: does not decode ({e})"]
    if len(rows) != entry["rows"]:
        problems.append(f"{path}: {len(rows)} rows, the manifest says {entry['rows']}")
    keys = [key_of(r, order) for r in rows]
    if len(set(keys)) != len(keys):
        problems.append(f"{path}: {len(keys) - len(set(keys))} duplicate key(s) on ({order})")
    return problems


def check_manifest(directory):
    """Every problem with an export directory; [] means it verifies."""
    try:
        with open(os.path.join(directory, MANIFEST), encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as e:
        return None, [f"{directory}: no readable {MANIFEST} ({e})"]
    problems = []
    orders = dict(TABLES)
    missing = sorted(set(orders) - set(manifest["files"]))
    if missing:
        problems.append(f"{directory}: the manifest has no file for {missing}")
    for table, entry in sorted(manifest["files"].items()):
        problems += check_file(os.path.join(directory, entry["file"]), entry, orders[table])
    return manifest, problems


def on_remote(paths, branch="main", root=ROOT):
    """True only if every path exists on origin/<branch> as of a fresh fetch."""
    fetch = subprocess.run(["git", "-C", root, "fetch", "--quiet", "origin", branch],
                           capture_output=True, text=True)
    if fetch.returncode != 0:
        print(f"git fetch origin {branch} failed: {fetch.stderr.strip()}", file=sys.stderr)
        return False
    # FETCH_HEAD, not origin/<branch>: a shallow CI checkout need not have a
    # remote-tracking ref for the branch it fetched.
    for p in paths:
        ok = subprocess.run(["git", "-C", root, "cat-file", "-e", f"FETCH_HEAD:{p}"],
                            capture_output=True)
        if ok.returncode != 0:
            print(f"{p} is not on origin/{branch}", file=sys.stderr)
            return False
    return True


def latest_export(root=ROOT):
    base = os.path.join(root, OUT)
    dirs = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) \
        if os.path.isdir(base) else []
    return os.path.join(base, dirs[-1]) if dirs else None


# ---- database I/O (service key) -------------------------------------------
def live_count(table):
    """The table's exact row count, from PostgREST's Content-Range header."""
    import requests
    from common import _cfg, _headers
    headers = dict(_headers(), Prefer="count=exact", Range="0-0")
    r = requests.get(f"{_cfg()['url']}/rest/v1/{table}", headers=headers,
                     params={"select": "*"}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"count {table} -> HTTP {r.status_code}: {r.text[:300]}")
    return int(r.headers["Content-Range"].rsplit("/", 1)[1])


def export(stamp):
    from common import rest_all
    directory = os.path.join(ROOT, OUT, stamp)
    os.makedirs(directory, exist_ok=True)
    manifest = {"exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "purpose": "plan v2 P0.3: every paper desk's record before retirement",
                "format": "gzip JSON Lines, one row per line, keys sorted",
                "files": {}}
    for table, order in TABLES:
        before = live_count(table)
        rows = rest_all(table, [("select", "*")], order=order)
        after = live_count(table)
        if not (before == len(rows) == after):
            raise SystemExit(f"{table}: counted {before}, read {len(rows)}, counted {after} - "
                             f"it changed during the export; run it again")
        blob = encode(rows)
        name = f"{table}.jsonl.gz"
        with open(os.path.join(directory, name), "wb") as fh:
            fh.write(blob)
        manifest["files"][table] = {"file": name, "rows": len(rows), "order": order,
                                    "sha256": sha256(blob), "gzip_bytes": len(blob)}
        print(f"{table}: {len(rows):,} rows -> {name} ({len(blob):,} bytes)")
    with open(os.path.join(directory, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return directory


def verify(directory, against_live=True):
    manifest, problems = check_manifest(directory)
    if manifest and against_live:
        for table, entry in sorted(manifest["files"].items()):
            n = live_count(table)
            if n != entry["rows"]:
                problems.append(f"{table}: the file holds {entry['rows']} rows, the table has {n}")
    for p in problems:
        print(p, file=sys.stderr)
    if not problems:
        print(f"{os.path.relpath(directory, ROOT)}: every file matches its sha256 and row count"
              + (" and the live table" if against_live else ""))
    return not problems


def retire(directory, reason):
    from common import rest, rpc
    manifest, problems = check_manifest(directory)
    if problems:
        raise SystemExit("the export does not verify; nothing retired:\n  " + "\n  ".join(problems))
    rel = os.path.relpath(directory, ROOT)
    paths = [f"{rel}/{MANIFEST}"] + [f"{rel}/{e['file']}" for e in manifest["files"].values()]
    if not on_remote(paths):
        raise SystemExit("the export is not on origin/main yet; nothing retired")

    desks = rest("paper_accounts", [("select", "account_id,name,status"), ("order", "created_at")])
    for d in desks:
        out = rpc("paper_desk_retire", {"p_account_id": d["account_id"], "p_reason": reason})
        print(f"{d['name']}: {'already retired' if out.get('already') else 'retired'}")

    old = [s["strategy_id"] for s in rest("strategies", [("select", "strategy_id,enabled")])
           if OLD_STRATEGY.match(s["strategy_id"]) and s["enabled"]]
    changed = rpc("set_strategies_enabled", {"p_ids": old, "p_enabled": False,
                                             "p_reason": f"{reason} (P0.3)"}) if old else 0
    print(f"strategies disabled: {changed} ({', '.join(old) or 'none were enabled'})")

    left = [d for d in rest("paper_accounts", [("select", "name,status")]) if d["status"] != "retired"]
    live = rest("paper_orders", [("select", "order_id"), ("status", "in.(queued,working)")])
    print(f"acceptance: {len(left)} desk(s) not retired, {len(live)} queued or working order(s)")
    if left or live:
        raise SystemExit("acceptance failed")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", choices=["export", "verify", "retire"])
    ap.add_argument("--dir", help="export directory (default: the newest under data/archive/paper_desks)")
    ap.add_argument("--reason", default=REASON)
    args = ap.parse_args()
    if args.stage == "export":
        directory = export(dt.date.today().isoformat())
        if not verify(directory):
            raise SystemExit("the export did not verify")
        return
    directory = args.dir or latest_export()
    if not directory:
        raise SystemExit(f"no export under {OUT}")
    if args.stage == "verify":
        raise SystemExit(0 if verify(directory) else 1)
    retire(directory, args.reason)


if __name__ == "__main__":
    main()
