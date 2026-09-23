"""
Mirror the proprietary record into the repository, every night (plan v2.1 P1.7).

THE ARCHIVE EXPORTS ONLY WHAT IT IS ABOUT TO PRUNE. Everything the platform
produces and never prunes lived in Postgres alone - measured 23 Sep, 7-day
averages:

    band_probabilities      +6,766 rows a day   every price the engine made
    fact_forecast_outcome     +955              every forecast, scored
    fact_band_outcome         +831              every bucket, settled
    fact_signal_outcome       +517              every signal, settled
    derived_*, model_versions, signals, markets, bands, cities, ...

research_captures held JSON copies of six of those, and only since 12 Sep.
That is the proprietary record, and it had no second copy.

THIS DELETES NOTHING. It writes gzipped CSVs into data/mirror/<table>/ and a
manifest, data/mirror/manifest.json, which the workflow commits. Three kinds
of table, one rule each:

  append    rows are inserted, not rewritten, and carry an insert time
            (a `now()` / `clock_timestamp()` default). Each run exports the
            rows whose insert time falls in [previous cutoff, this cutoff),
            the cutoff being this UTC midnight, so a day is exported once it
            has ended and never twice.

  closed    rows are rewritten for a trailing window (databank.py rebuilds
            the last 7 days of fact_*), so a row is exported only once its
            date is CLOSED_AFTER_DAYS old - after the last rewrite it can get.

  snapshot  small tables whose rows change in place (cities, settings,
            the derived_* tables a refit replaces, the paper desk). The whole
            table, sorted by its key; a new file only when the content hash
            differs from the last one.

What this cannot see, stated rather than hidden: an UPDATE to an append row
after its day was mirrored, or to a closed row after its window closed. For
the six research tables those updates are in research_captures, which the
archive exports; for the rest, the mirror holds the row as it was.

KEYSET PAGING ON THE PRIMARY KEY, never OFFSET - the reason is in
archive_observations.export_cold: OFFSET over a non-unique order skips rows
silently. Composite keys page with a PostgREST tuple comparison.

VERIFIED BEFORE IT IS RECORDED: each file is read back off disk and must
parse to the rows exported, and PostgREST's exact count over the same filter
must agree - which is what proves the paging skipped nothing.

  python scripts/mirror_to_repo.py              # every table
  python scripts/mirror_to_repo.py --table cities --dry-run
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import sys

import requests

from common import rest, log_run, _cfg, _headers
from archive_observations import _cell, count_rows

PAGE = 5000
CLOSED_AFTER_DAYS = 9          # databank.py rewrites --days 7; two days' margin
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIRROR = os.path.join(ROOT, "data", "mirror")
MANIFEST = os.path.join(MIRROR, "manifest.json")

# Keys of `settings` that never leave the database: webhook URLs carry
# secrets, operators are identities. Everything else in settings is a tuning
# value the platform's history needs.
SETTINGS_WITHHELD = ("n8n_webhooks", "operators")


def append(time, *pk):
    return {"kind": "append", "time": time, "pk": list(pk)}


def closed(date, *pk, via=None):
    return {"kind": "closed", "date": date, "pk": list(pk), "via": via}


def snapshot(*pk, where=None):
    return {"kind": "snapshot", "pk": list(pk), "where": where}


TABLES = {
    # --- what the engine decided --------------------------------------------
    "band_probabilities":            append("computed_at", "prob_id"),
    "signals":                       append("fired_at", "signal_id"),
    "model_versions":                append("created_at", "version_id"),
    "strategy_config_history":       append("changed_at", "history_id"),
    "strategy_conflicts":            append("detected_at", "conflict_id"),
    "anomalies":                     append("detected_at", "anomaly_id"),
    # --- what it learned ----------------------------------------------------
    "derived_forecast_skill":        append("computed_at", "city_key", "computed_at", "lead_days"),
    "derived_forecast_skill_model":  append("computed_at", "city_key", "model", "computed_at", "lead_days"),
    "derived_capacity":              append("computed_at", "city_key", "computed_at", "hour_utc"),
    "derived_city_correlation":      append("computed_at", "city_a", "city_b", "computed_at"),
    "derived_market_peak":           append("computed_at", "city_key", "computed_at"),
    "derived_weather_peak":          append("computed_at", "city_key", "month", "computed_at"),
    "derived_model_forecast":        append("predicted_at", "city_key", "for_date", "run_at"),
    "derived_forecast_postprocess":  snapshot("city_key", "lead_days"),
    "derived_trajectory":            snapshot("city_key", "local_hour"),
    "derived_climb_profile":         snapshot("city_key", "local_hour"),
    "derived_calibration_adjustment": snapshot("city_key"),
    "derived_model_promotion":       snapshot("city_key", "lead_days", "target"),
    "derived_weather_model":         snapshot("city_key", "target"),
    "derived_city_climate":          snapshot("city_key"),
    # --- what happened: inputs and settled outcomes -------------------------
    "weather_observations":          append("observed_at", "obs_id"),
    "weather_forecasts":             append("observed_at", "forecast_id"),
    "weather_forecast_models":       append("observed_at", "city_key", "model", "run_at", "for_date"),
    "weather_forecast_features":     append("captured_at", "city_key", "for_date", "run_at"),
    "weather_resolution_evidence":   append("captured_at", "evidence_id"),
    "weather_resolution_attempts":   append("captured_at", "attempt_id"),
    "weather_events":                append("detected_at", "event_id"),
    "trades_observed":               append("ingested_at", "trade_id"),
    "fact_band_outcome":             closed("for_date", "band_id"),
    "fact_forecast_outcome":         closed("for_date", "city_key", "for_date", "model", "lead_days"),
    "fact_signal_outcome":           closed("for_date", "signal_id"),
    "derived_city_day_features":     closed("obs_date", "city_key", "obs_date"),
    "derived_band_day_volume":       closed("trade_date", "band_id", "trade_date"),
    "derived_city_day_volume":       closed("trade_date", "city_key", "trade_date"),
    "markets":                       closed("resolution_date", "market_id"),
    "bands":                         closed("resolution_date", "band_id", via="markets"),
    # --- the integrity trail ------------------------------------------------
    "proprietary_data_corrections":  append("recorded_at", "correction_id"),
    "proprietary_data_quality_flags": append("detected_at", "flag_id"),
    "proprietary_data_manifests":    append("captured_at", "manifest_id"),
    "city_metadata_evidence":        append("recorded_at", "evidence_id"),
    "ingest_log":                    append("logged_at", "log_id"),
    # --- reference and configuration ----------------------------------------
    "cities":                        snapshot("city_key"),
    "settings":                      snapshot("key", where=("key", "not.in.(%s)" % ",".join(SETTINGS_WITHHELD))),
    "strategies":                    snapshot("strategy_id"),
    "cost_params":                   snapshot("version_id"),
    "synthesis_thresholds":          snapshot("kind"),
    "anomaly_rules":                 snapshot("rule_id"),
    "data_freshness_spec":           snapshot("table_name"),
    "deployments":                   snapshot("deployment_id"),
    # --- the paper desk and the backtests -----------------------------------
    "paper_accounts":                snapshot("account_id"),
    "paper_trade_plans":             snapshot("plan_id"),
    "paper_orders":                  snapshot("order_id"),
    "paper_positions":               snapshot("account_id", "band_id", "side"),
    "paper_position_settlements":    snapshot("account_id", "band_id", "side"),
    "paper_activity":                snapshot("event_id"),
    "paper_trades":                  snapshot("trade_id"),
    "paper_book_evidence":           append("captured_at", "snapshot_id"),
    "ledger":                        append("recorded_at", "entry_id"),
    "backtest_runs":                 snapshot("run_id"),
    "backtest_results":              snapshot("result_id"),
    "backtest_trades":               snapshot("bt_trade_id"),
}

# Every public table the mirror does NOT copy, and why. A table in neither
# TABLES nor here fails tests/test_every_table_has_a_home.py.
NOT_MIRRORED = {
    "research_captures": "JSON copies of six mirrored tables; the archive exports it at 2 days",
    "book_snapshots": "22,074 rows a day (23 Sep); prunable rows are archived; which book detail "
                      "to keep beyond that is plan step P5.13",
    "edges": "13,473 rows a day (23 Sep); superseded pricings are archived at 14 days; the model "
             "side of every price is band_probabilities, which is mirrored; the rest is P5.13",
    "paper_resolution_evidence": "archived at 3 days once its outcome is frozen in fact_band_outcome",
    "live_weather": "one row per city, overwritten every run; its readings are weather_observations",
    "book_ladder_cache": "a cache of book_snapshots, rebuilt by trigger",
    "archive_daily_city_presence": "rebuildable from data/archive; the prune's own coverage proof",
    "archive_daily_rollup": "rebuildable from data/archive",
    "archive_city_rollup": "rebuildable from data/archive",
    "desk_members": "auth user ids; no research content",
    "ad4_view_restore": "copies of view DDL whose source is sql/",
    "regimes": "empty (0 rows, 23 Sep); P1.6 may drop it",
    "ensemble_forecasts": "empty (0 rows, 23 Sep); P1.6 may drop it",
    "book_capture_attempts": "empty (0 rows, 23 Sep); P1.6 may drop it",
}


# --- reading ---------------------------------------------------------------

def _quote(v):
    s = str(v)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def after_filter(pk, last):
    """PostgREST filter for (pk...) > (last...), as an `or` tree."""
    if len(pk) == 1:
        return (pk[0], f"gt.{last[0]}")
    terms = []
    for i, col in enumerate(pk):
        eqs = [f"{c}.eq.{_quote(last[j])}" for j, c in enumerate(pk[:i])]
        gt = f"{col}.gt.{_quote(last[i])}"
        terms.append(f"and({','.join(eqs + [gt])})" if eqs else gt)
    return ("or", f"({','.join(terms)})")


def exact_count(table, filters):
    r = requests.head(f"{_cfg()['url']}/rest/v1/{table}",
                      headers={**_headers(), "Prefer": "count=exact"},
                      params=filters + [("limit", "1")], timeout=120)
    r.raise_for_status()
    return int(r.headers["Content-Range"].split("/")[-1])


def pk_list(spec):
    # a composite key may name the time column too; keep the order, drop repeats
    seen, out = set(), []
    for c in spec["pk"]:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def read_rows(table, spec, filters):
    """Every row matching `filters`, in primary-key order, keyset-paged."""
    pk = pk_list(spec)
    select = "*"
    if spec.get("via"):
        select = f"*,{spec['via']}!inner({spec['date']})"
    rows, last = [], None
    while True:
        params = [("select", select)] + list(filters)
        if last is not None:
            params.append(after_filter(pk, last))
        params += [("order", ",".join(f"{c}.asc" for c in pk)), ("limit", str(PAGE))]
        page = rest(table, params)
        if not page:
            return rows
        nxt = [page[-1][c] for c in pk]
        if nxt == last:
            raise RuntimeError(f"{table}: paging made no progress at {last}")
        last = nxt
        if spec.get("via"):
            for r in page:
                r.pop(spec["via"], None)
        rows.extend(page)


# --- writing ---------------------------------------------------------------

def to_csv(rows):
    cols = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: _cell(v) for k, v in r.items()})
    return buf.getvalue()


def write_file(table, name, text):
    path = os.path.join(MIRROR, table, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    blob = gzip.compress(text.encode(), 9, mtime=0)   # same rows, same bytes
    with open(path, "wb") as fh:
        fh.write(blob)
    return path, blob


def read_back(path, expect):
    with open(path, "rb") as fh:
        got = count_rows(fh.read())
    if got != expect:
        raise RuntimeError(f"{path}: wrote {expect} rows, read back {got}")


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as fh:
            return json.load(fh)
    return {"schema": 1, "tables": {}}


def save_manifest(m):
    m["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    os.makedirs(MIRROR, exist_ok=True)
    with open(MANIFEST, "w") as fh:
        json.dump(m, fh, indent=1, sort_keys=True)
        fh.write("\n")


# --- one table ---------------------------------------------------------------

def window(spec, state, today):
    """(filters, lower, upper, label) for this run, or None if nothing is due."""
    if spec["kind"] == "append":
        col = spec["time"]
        upper = dt.datetime.combine(today, dt.time(), dt.timezone.utc).isoformat()
        lower = state.get("through")
        if lower is not None and lower >= upper:
            return None
        f = [(col, f"lt.{upper}")]
        if lower is not None:
            f.append((col, f"gte.{lower}"))
        return f, lower, upper
    col = spec["date"]
    if spec.get("via"):
        col = f"{spec['via']}.{col}"
    upper = (today - dt.timedelta(days=CLOSED_AFTER_DAYS)).isoformat()
    lower = state.get("through")
    if lower is not None and lower >= upper:
        return None
    f = [(col, f"lte.{upper}")]
    if lower is not None:
        f.append((col, f"gt.{lower}"))
    return f, lower, upper


def mirror_one(table, spec, manifest, today, dry_run):
    state = manifest["tables"].setdefault(table, {"kind": spec["kind"], "files": []})
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    if spec["kind"] == "snapshot":
        filters = [spec["where"]] if spec.get("where") else []
        rows = read_rows(table, spec, filters)
        if not rows:
            return 0, "empty"
        text = to_csv(rows)
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest == state.get("content_sha256"):
            return 0, "unchanged"
        n = exact_count(table, filters)
        if n != len(rows):
            raise RuntimeError(f"{table}: read {len(rows)} rows, the table has {n}")
        if dry_run:
            return len(rows), "would snapshot"
        name = f"{table}-{today.isoformat()}.csv.gz"
        path, blob = write_file(table, name, text)
        read_back(path, len(rows))
        state["files"].append({"file": name, "rows": len(rows), "exported_at": now,
                               "sha256": hashlib.sha256(blob).hexdigest()})
        state["content_sha256"] = digest
        return len(rows), "snapshot"

    w = window(spec, state, today)
    if w is None:
        return 0, "up to date"
    filters, lower, upper = w
    rows = read_rows(table, spec, filters)
    count_filters = filters
    if spec.get("via"):
        # the count needs the same inner join the read used
        count_filters = [("select", f"{pk_list(spec)[0]},{spec['via']}!inner({spec['date']})")] + filters
    n = exact_count(table, count_filters)
    if n != len(rows):
        raise RuntimeError(f"{table}: read {len(rows)} rows in the window, the table has {n} - "
                           "paging skipped or repeated rows")
    if dry_run:
        return len(rows), f"would export ({lower} .. {upper})"
    if rows:
        start = (lower or "start")[:10]
        name = f"{table}-{start}-to-{upper[:10]}.csv.gz"
        path, blob = write_file(table, name, to_csv(rows))
        read_back(path, len(rows))
        state["files"].append({"file": name, "rows": len(rows), "from": lower, "to": upper,
                               "exported_at": now, "sha256": hashlib.sha256(blob).hexdigest()})
    state["through"] = upper
    return len(rows), "exported"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="all")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    names = sorted(TABLES) if a.table == "all" else [a.table]
    today = dt.datetime.now(dt.timezone.utc).date()
    manifest = load_manifest()
    failed, total = [], 0
    for name in names:
        try:
            n, what = mirror_one(name, TABLES[name], manifest, today, a.dry_run)
            total += n
            print(f"{name:34s} {n:>9,}  {what}")
        except Exception as e:                      # one table must not stop the rest
            failed.append(name)
            print(f"{name:34s} FAILED  {e}", file=sys.stderr)
        if not a.dry_run:
            save_manifest(manifest)                 # a later failure keeps what succeeded
    if not a.dry_run:
        log_run("mirror_to_repo", "error" if failed else "ok", total,
                {"tables": len(names), "failed": failed})
    if failed:
        print(f"::error::mirror failed for {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
