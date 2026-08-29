"""
AD4 capacity + correlation (Task 7), daily.

The actual computation is genuine SQL (sql/ad4_capacity_correlation.sql):
capacity is a GROUP BY over book_snapshots depth, correlation of forecast
errors uses Postgres's built-in corr() aggregate. Both belong in the
database for the same reason edge computation partly does (see
docs/architecture_deviations.md) - they're joins/aggregations over data
already there, not logic that needs Python. This script is a thin
scheduling/logging wrapper so the job shows up in `ingest_log` next to
every other daily job, consistent with how the rest of AD4 is run.
"""
from common import _cfg, _headers, log_run
import requests


def _call_rpc(fn, params=None):
    r = requests.post(f"{_cfg()['url']}/rest/v1/rpc/{fn}", headers=_headers(),
                      json=params or {}, timeout=120)
    r.raise_for_status()
    return r.json()


def main():
    capacity_rows = _call_rpc("recompute_capacity")
    correlation_rows = _call_rpc("recompute_correlation")
    print(f"derived_capacity: {capacity_rows} rows")
    print(f"derived_city_correlation: {correlation_rows} rows")
    log_run("capacity", "ok", (capacity_rows or 0) + (correlation_rows or 0),
            {"capacity_rows": capacity_rows, "correlation_rows": correlation_rows})


if __name__ == "__main__":
    main()
