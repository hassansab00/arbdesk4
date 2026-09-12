#!/usr/bin/env python3
"""Create a tamper-evident manifest for ArbDesk's proprietary datasets.

Rows are read in a stable order up to one explicit cutoff and hashed without
loading an entire archive into memory. The source rows are never modified.
"""
import argparse
import datetime as dt
import hashlib
import json
import os

from common import log_run, rest, upsert


DATASETS = {
    # Every order ends in a primary key. A timestamp/city/source tuple is not
    # guaranteed unique, and tied rows can otherwise move between REST pages.
    "weather_observations": ("valid_at", "valid_at.asc,obs_id.asc"),
    "weather_forecasts": ("run_at", "run_at.asc,forecast_id.asc"),
    "book_snapshots": ("observed_at", "observed_at.asc,snapshot_id.asc"),
    "trades_observed": ("ingested_at", "ingested_at.asc,trade_id.asc"),
    "band_probabilities": ("computed_at", "computed_at.asc,prob_id.asc"),
    "signals": ("fired_at", "fired_at.asc,signal_id.asc"),
    "fact_forecast_outcome": (
        "captured_at", "captured_at.asc,city_key.asc,for_date.asc,model.asc,lead_days.asc"
    ),
    "fact_band_outcome": ("captured_at", "captured_at.asc,band_id.asc"),
    "fact_signal_outcome": ("captured_at", "captured_at.asc,signal_id.asc"),
    "research_captures": ("captured_at", "captured_at.asc,capture_id.asc"),
}


def canonical_bytes(row):
    """Stable JSON plus a length prefix, avoiding concatenation ambiguity."""
    encoded = json.dumps(
        row, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return len(encoded).to_bytes(8, "big") + encoded


def fingerprint_dataset(dataset, cutoff, page_size=500):
    if dataset not in DATASETS:
        raise ValueError(f"Unknown proprietary dataset: {dataset}")
    if page_size < 1:
        raise ValueError("page_size must be positive")
    time_column, order = DATASETS[dataset]
    digest = hashlib.sha256()
    offset = 0
    previous_page_hash = None
    while True:
        rows = rest(dataset, [
            ("select", "*"),
            (time_column, f"lt.{cutoff}"),
            ("order", order),
            ("offset", str(offset)),
            ("limit", str(page_size)),
        ])
        if not rows:
            break
        page_hash = hashlib.sha256(b"".join(canonical_bytes(row) for row in rows)).hexdigest()
        if page_hash == previous_page_hash:
            raise RuntimeError(f"{dataset}: pagination did not advance")
        previous_page_hash = page_hash
        for row in rows:
            digest.update(canonical_bytes(row))
        offset += len(rows)
    return {
        "dataset": dataset,
        "scope": {"time_column": time_column, "cutoff": cutoff, "order": order},
        "row_count": offset,
        "sha256": digest.hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", action="append", choices=sorted(DATASETS))
    parser.add_argument("--cutoff", help="exclusive ISO timestamp; defaults to the current UTC time")
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--code-version", default=os.getenv("GITHUB_SHA", "local-uncommitted"))
    args = parser.parse_args()
    cutoff = args.cutoff or dt.datetime.now(dt.timezone.utc).isoformat()
    selected = args.dataset or list(DATASETS)
    manifests = []
    for dataset in selected:
        manifest = fingerprint_dataset(dataset, cutoff, args.page_size)
        manifest["code_version"] = args.code_version
        manifest["command_key"] = (
            f"integrity:{dataset}:{cutoff}:{manifest['sha256']}"
        )
        manifests.append(manifest)
        print(f"{dataset}: {manifest['row_count']} rows {manifest['sha256']}")
    # A verification retry with the same cutoff and digest is a no-op rather
    # than a duplicate-key failure.
    upsert("proprietary_data_manifests", manifests, "command_key", chunk=50)
    log_run("data_integrity", "ok", len(manifests), {
        "cutoff": cutoff,
        "datasets": {m["dataset"]: {"rows": m["row_count"], "sha256": m["sha256"]}
                     for m in manifests},
    })


if __name__ == "__main__":
    main()
