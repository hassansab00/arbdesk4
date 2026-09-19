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
        return

    # A PAUSE IS NOT A FAILURE, and this used to report them identically.
    #
    # `incomplete` is three different things at once - the deadline was
    # reached, a source returned gaps, or not every city was covered - and
    # every one of them raised the same RuntimeError, so a backfill that
    # walked its full budget, saved its checkpoint and made real progress
    # exited 1 and went red. That is the normal end of a bounded job. It ran
    # every day, cost its eighteen minutes, did its work and reported failure,
    # which is how a genuine source outage would have gone unnoticed among the
    # noise.
    #
    # Now only a stop with nothing to show for it fails. Progress with budget
    # left to spend on another day is an ordinary result, announced clearly so
    # the backlog is still visible.
    made_progress = result['completed_dates'] > 0
    gaps = result['missing_chunks']

    if made_progress and not gaps:
        print(f"Paused with progress: {result['completed_dates']} date(s) completed, "
              f"{result['rows_offered']:,} rows offered, checkpoint retained. "
              f"Budget {'exhausted' if depth + 1 >= budget // 25 else 'held'} at depth {depth}; "
              f"re-run to continue.")
        return

    if gaps:
        raise RuntimeError(
            f'Backfill stopped with {gaps} missing chunk(s): the source did not return data it '
            f'was asked for. {result["completed_dates"]} date(s) still completed and the '
            f'checkpoint is retained, but the gap is real and will not close by retrying blindly.')

    raise RuntimeError(
        'Backfill made no progress: zero dates completed. The checkpoint is retained, but '
        'a resume will repeat this result until the source or the window changes.')


if __name__ == '__main__':
    main()
