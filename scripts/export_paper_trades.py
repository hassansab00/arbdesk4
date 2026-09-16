"""Closed paper trades become a file in the repo; open ones stay in Postgres.

WHY A FILE AND NOT JUST A TABLE. A closed trade never changes again. It is the
permanent record of what the desk decided, what it paid and what it got back,
and it is the only thing a future version of the strategy can be measured
against. Keeping it in a 500 MB Postgres tier means it competes for space with
live order books, and it disappears the day the tier is cleared. Keeping it in
the repository means it is versioned, diffable, reviewable in a pull request,
free, and already backed up everywhere the repo is cloned.

WHERE IT LIVES, and why under web/public. The obvious home is data/, but the
platform has to DISPLAY this, and the browser cannot read data/ - it can only
fetch a URL. Putting the canonical file under web/public/paper-trades/ makes
one file that is both the repository's record and the page's data source, with
no build step to copy it and nothing that can drift between the two. It is
still ordinary repo content: git log shows every trade the desk has ever
closed, one line at a time.

JSON LINES, one object per trade, because it is append-only in the git sense:
a day's export adds lines and touches nothing above them, so the diff is the
trades and the history is readable. A single JSON array would rewrite the last
line every time and a CSV would lose types.

SELF-CONTAINED ROWS. band_label and city_key are written into each line rather
than left as a band_id to join later. The bands table is pruned; the archive
must still say "Milan 26C, 16 Sep" in five years.

    python scripts/export_paper_trades.py                 # write the files
    python scripts/export_paper_trades.py --prune-days 30 # ...and trim Postgres

Pruning is opt-in and never removes a trade the file does not already contain -
it re-reads what it just wrote and matches trade ids before deleting anything,
the same order the observations archive uses.
"""
import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

from common import rest, rest_all, rpc, log_run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "public", "paper-trades")

# What a closed trade needs to stand on its own, in a stable order so the JSON
# a line is written as does not churn between runs.
FIELDS = [
    "trade_id", "account_id", "strategy_id", "signal_id",
    "city_key", "band_id", "band_label", "resolution_date",
    "side", "action", "opened_at", "shares", "avg_fill_price", "quoted_price",
    "slippage_paid", "fee_paid", "partial_fill", "requested_shares",
    "legs_requested", "closed_at", "close_price", "close_reason",
    "gross_pnl", "net_pnl", "approved_by_user",
]


def month_of(row):
    """The file a trade belongs in: the month it CLOSED.

    Closing is when the row stops changing, so a trade can never need to move
    between files afterwards. Filing by opened_at would let a position opened
    in one month and resolved in the next rewrite an already-published file.
    """
    return str(row["closed_at"])[:7]


def fetch_closed():
    rows = rest_all("paper_trades", [("closed_at", "not.is.null")], order="closed_at,trade_id")
    ids = sorted({r["band_id"] for r in rows if r.get("band_id")})
    labels = {}
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        for b in rest("bands", {"band_id": "in.(" + ",".join(chunk) + ")",
                                "select": "band_id,band_label"}):
            labels[b["band_id"]] = b.get("band_label")
    out = []
    for r in rows:
        row = {k: r.get(k) for k in FIELDS}
        row["band_label"] = labels.get(r.get("band_id"))
        out.append(row)
    return out


def read_existing():
    """Every trade already on disk, by id, so an export is idempotent."""
    seen = {}
    if not os.path.isdir(OUT):
        return seen
    for name in sorted(os.listdir(OUT)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(OUT, name)) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                seen[row["trade_id"]] = row
    return seen


def write(rows):
    """Rewrite each month file that gained a trade, sorted and deduplicated.

    Rewriting rather than appending is what makes a re-run safe: a run that
    died halfway, or a backfill that arrives out of order, converges on the
    same file instead of leaving a duplicate behind.
    """
    os.makedirs(OUT, exist_ok=True)
    by_month = defaultdict(dict)
    for row in rows:
        by_month[month_of(row)][row["trade_id"]] = row

    written = []
    for month, trades in sorted(by_month.items()):
        path = os.path.join(OUT, f"{month}.jsonl")
        ordered = sorted(trades.values(), key=lambda r: (str(r["closed_at"]), r["trade_id"]))
        body = "".join(json.dumps({k: r.get(k) for k in FIELDS}, sort_keys=False,
                                  separators=(",", ":")) + "\n" for r in ordered)
        before = open(path).read() if os.path.exists(path) else None
        if body != before:
            with open(path, "w") as fh:
                fh.write(body)
            written.append((month, len(ordered)))

    # The index the page fetches first: which months exist, how big, and the
    # totals, so a browser can render a summary without downloading everything.
    months = sorted(f[:-6] for f in os.listdir(OUT) if f.endswith(".jsonl"))
    everything = [r for m in months for r in _read_month(m)]
    index = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "months": months,
        "trades": len(everything),
        "net_pnl": _total(everything, "net_pnl"),
        "gross_pnl": _total(everything, "gross_pnl"),
        "fees": _total(everything, "fee_paid"),
        "wins": sum(1 for r in everything if _num(r.get("net_pnl")) > 0),
        "losses": sum(1 for r in everything if _num(r.get("net_pnl")) < 0),
        "cities": sorted({r["city_key"] for r in everything if r.get("city_key")}),
        "strategies": sorted({r["strategy_id"] for r in everything if r.get("strategy_id")}),
        "first_closed_at": everything[0]["closed_at"] if everything else None,
        "last_closed_at": everything[-1]["closed_at"] if everything else None,
    }
    path = os.path.join(OUT, "index.json")
    body = json.dumps(index, indent=2) + "\n"
    # generated_at alone must not dirty the repo: compare everything else.
    before = json.load(open(path)) if os.path.exists(path) else None
    if before is None or {k: v for k, v in before.items() if k != "generated_at"} != \
            {k: v for k, v in index.items() if k != "generated_at"}:
        open(path, "w").write(body)
        written.append(("index.json", len(everything)))
    return written, index


def _read_month(month):
    path = os.path.join(OUT, f"{month}.jsonl")
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _total(rows, key):
    return round(sum(_num(r.get(key)) for r in rows), 6)


def prune(keep_days):
    """Delete closed trades older than keep_days - but only ones the files on
    disk actually contain, matched by id.

    The ids come from re-reading what was just written, not from what was just
    sent, and the database intersects that list with its own cutoff. A file
    that failed to write therefore cannot become a delete. That is the same
    two-sided contract the observations archive uses, and it is the reason
    this is an RPC rather than a DELETE from here: common.rest is GET-only on
    purpose, and giving every script in scripts/ the ability to delete rows to
    save one function is a bad trade.
    """
    on_disk = sorted(read_existing())
    if not on_disk:
        print("nothing exported yet; not pruning", file=sys.stderr)
        return 0
    preflight = rpc("prune_exported_paper_trades",
                    {"p_keep_days": keep_days, "p_trade_ids": on_disk, "p_dry_run": True})
    if not (preflight or {}).get("ok"):
        print(f"prune preflight refused: {preflight}", file=sys.stderr)
        return -1
    if preflight.get("unexported"):
        print(f"WARNING: {preflight['unexported']} closed trade(s) older than "
              f"{keep_days} days are not in the exported files and will be kept.",
              file=sys.stderr)
    if not preflight.get("would_delete"):
        return 0
    result = rpc("prune_exported_paper_trades",
                 {"p_keep_days": keep_days, "p_trade_ids": on_disk, "p_dry_run": False})
    if not (result or {}).get("ok"):
        print(f"prune refused: {result}", file=sys.stderr)
        return -1
    return int(result.get("deleted") or 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prune-days", type=int, default=None,
                    help="also delete Postgres rows closed longer ago than this "
                         "(only ones already in the files)")
    args = ap.parse_args()

    rows = fetch_closed()
    existing = read_existing()
    merged = {**existing, **{r["trade_id"]: r for r in rows}}
    written, index = write(list(merged.values()))

    print(f"{len(rows)} closed trade(s) in Postgres, {len(merged)} in the archive")
    for name, n in written:
        print(f"  wrote {name}  ({n} rows)")
    if not written:
        print("  files already current")

    pruned = 0
    if args.prune_days is not None:
        pruned = prune(args.prune_days)
        if pruned < 0:
            log_run("export_paper_trades", "attention", len(merged),
                    {"archived": len(merged), "prune": "refused"})
            return 1
        print(f"pruned {pruned} row(s) closed more than {args.prune_days} days ago")

    log_run("export_paper_trades", "ok", len(merged),
            {"archived": len(merged), "new_files": [n for n, _ in written],
             "pruned": pruned, "net_pnl": index["net_pnl"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
