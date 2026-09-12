"""Bound the entire continuation chain, preserve child failures, no shell input."""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    depth = int(os.environ.get('BACKFILL_DEPTH') or 0)
    budget = int(os.environ.get('BACKFILL_BUDGET') or 75)
    if not 25 <= budget <= 300 or not 0 <= depth < budget // 25:
        raise ValueError('Backfill budget exhausted or invalid (25–300 job minutes)')
    start, end = os.environ.get('BACKFILL_START', ''), os.environ.get('BACKFILL_END', '')
    args = [sys.executable, 'scripts/ingest_forecasts.py']
    if start:
        dt.date.fromisoformat(start)
        end = end or dt.datetime.now(dt.timezone.utc).date().isoformat()
        dt.date.fromisoformat(end)
        args.extend([start, end])
    result_path = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'arbdesk-forecast-result.json'
    result_path.unlink(missing_ok=True)
    env = {**os.environ, 'FORECAST_RESULT_PATH': str(result_path), 'FORECAST_DEADLINE_MINUTES': '20'}
    subprocess.run(args, env=env, check=True, timeout=23*60)
    result = json.loads(result_path.read_text())
    if not result['incomplete']:
        return
    # Completed source/date coverage is measured again after writes. Offered
    # upserts are not proof of progress and cannot justify a paid continuation.
    can_continue = (os.environ.get('BACKFILL_AUTO', 'true') == 'true'
                    and result['completed_dates'] > 0 and result['missing_chunks'] == 0
                    and depth + 1 < budget // 25)
    if can_continue:
        subprocess.run(['gh', 'workflow', 'run', 'forecasts.yml', '--repo', os.environ['GITHUB_REPOSITORY'],
            '--ref', os.environ['GITHUB_REF_NAME'], '-f', 'start='+start, '-f', 'end='+end,
            '-f', 'auto_continue=true', '-f', 'depth='+str(depth+1), '-f', 'chain_budget_minutes='+str(budget)], check=True)
        print('Checkpoint saved; bounded continuation requested.')
    else:
        raise RuntimeError('Backfill incomplete; progress retained. Budget/source/no-progress stop; resume explicitly.')


if __name__ == '__main__':
    main()
