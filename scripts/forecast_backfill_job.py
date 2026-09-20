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
    # missing_chunks is refusals only, so a source declining a window still
    # stops the chain; a chunk that merely timed out does not, because the
    # continuation is the retry that closes it and the whole chain stays
    # bounded by the declared budget either way.
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
    # AND NEITHER IS A TIMEOUT A GAP. `missing_chunks` used to mean "the
    # source did not return data", which pooled a 400 refusal with a read
    # timeout, and the failure text then told whoever read it that the gap
    # "will not close by retrying blindly" - untrue of a timeout, which is the
    # one thing retrying does close. Two timed-out chunks out of 49 cities
    # failed this job every day from 16 Sep while 47 cities took all 77 rows
    # from the same source in the same window.
    #
    # `missing_chunks` is now refusals only. Unreached chunks keep the run
    # incomplete - the next run asks for them again, least-covered first - and
    # are reported rather than raised. A source that is actually down still
    # fails below, on zero dates completed, which is the measurement that says
    # so without guessing.
    made_progress = result['completed_dates'] > 0
    gaps = result['missing_chunks']
    unreached = result.get('unreached_chunks', 0)

    if made_progress and not gaps:
        print(f"Paused with progress: {result['completed_dates']} date(s) completed, "
              f"{result['rows_offered']:,} rows offered, checkpoint retained. "
              + (f"{unreached} chunk(s) unreached and will be asked for again. " if unreached else "")
              + f"Budget {'exhausted' if depth + 1 >= budget // 25 else 'held'} at depth {depth}; "
              f"re-run to continue.")
        return

    if gaps:
        raise RuntimeError(
            f'Backfill stopped with {gaps} refused chunk(s): the source answered and declined to '
            f'serve that window. {result["completed_dates"]} date(s) still completed and the '
            f'checkpoint is retained, but the gap is real and will not close by retrying blindly.')

    raise RuntimeError(
        'Backfill made no progress: zero dates completed. The checkpoint is retained, but '
        'a resume will repeat this result until the source or the window changes.')


if __name__ == '__main__':
    main()
