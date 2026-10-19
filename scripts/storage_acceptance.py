"""Measure capacity separately from a successful archive. Never changes data."""
import datetime as dt
import json
import math


def assess(receipt):
    if isinstance(receipt, list) and len(receipt) == 1:
        receipt = receipt[0]
    if not isinstance(receipt, dict):
        raise ValueError('storage pressure receipt is missing')
    values = {}
    for key in ('db_mb', 'tier_mb', 'high_water_pct', 'target_pct'):
        value = receipt.get(key)
        if isinstance(value, bool) or value is None:
            raise ValueError(f'invalid storage measurement: {key}')
        values[key] = float(value)
        if not math.isfinite(values[key]):
            raise ValueError(f'invalid storage measurement: {key}')
    size, tier = values['db_mb'], values['tier_mb']
    high, target = values['high_water_pct'], values['target_pct']
    if size < 0 or tier <= 0 or not 0 < target < high <= 100:
        raise ValueError('invalid storage budget or measurement')
    return dict(values, pct_of_tier=round(100 * size / tier, 1),
                headroom_mb=round(tier - size, 1),
                target_mb=round(tier * target / 100, 1),
                capacity_ok=size < tier * target / 100,
                measured_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                policy='storage-headroom-acceptance-v1')


def main():
    from common import rpc, log_run
    try:
        report = assess(rpc('storage_pressure', timeout=15))
    except Exception as exc:
        log_run('storage_acceptance', 'error', 0,
                {'error': f'{type(exc).__name__}: {exc}'[:300], 'capacity_ok': None})
        raise
    log_run('storage_acceptance', 'ok' if report['capacity_ok'] else 'attention', 0, report)
    print(json.dumps(report, sort_keys=True))
    if not report['capacity_ok']:
        print(f"::warning::Database is {report['db_mb']} MiB; capacity target is below "
              f"{report['target_mb']} MiB. Archive success does not establish headroom.")
    # Pressure is an open capacity finding, not a reason to stop archiving.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
