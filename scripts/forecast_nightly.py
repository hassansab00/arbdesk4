"""The night's forecast ingest, inside pipeline_daily, with a bounded second pass.

Until 4 Oct, forecasts.yml ran this ingest at 03:36 as a chain of links:
when a pass left chunks unreached (Open-Meteo read timeouts), the next link
asked for them again. It ran two or three links a night, and the second wrote
150-400 archive rows (ingest_log, 28 Sep - 4 Oct). pipeline_daily then ran the
same script at 04:36 and found nothing left to write on 7 of 7 nights. Plan v2
P6.1 (4 Oct) took forecasts.yml off the clock to fit the Pro plan's minutes,
so this keeps the continuation inside the daily job, without another job's
setup and without fetching every city's current run again:

  pass 1   ingest_forecasts.py as before: the catch-up window and every city's
           current run, soft deadline 20 minutes.
  pass 2+  only while the last pass left the run incomplete, refused nothing
           (a refusal will not close by asking again) and completed at least
           one date (no progress means stop, as forecast_backfill_job.py). It
           asks for what is still missing, and for the current runs of only
           the cities the passes before it missed. Soft deadline 6 minutes.

At most MAX_PASSES (the chain's own bound: 75 job-minutes over 25-minute
links), and no pass starts after START_BY_MIN minutes, so the step stays
inside its timeout. Each pass logs its own ingest_forecasts row.

THE STEP GOES RED WHEN THE CHAIN WOULD HAVE (forecast_backfill_job.py): a pass
that crashed or timed out, a source that refused a chunk (asking again will
not close it), or a last pass that left the run incomplete having completed
no date. A run left incomplete by the bounds after making progress is a pause,
as the chain's "Paused with progress": the next night asks again. The other
steps of pipeline_daily run either way (`if: !cancelled()`).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

MAX_PASSES = 3
FIRST_DEADLINE_MIN = 20
NEXT_DEADLINE_MIN = 6
START_BY_MIN = 26
FIRST_TIMEOUT_S = 23 * 60      # forecast_backfill_job.py's own bound on a link
NEXT_TIMEOUT_S = 10 * 60       # so the step ends inside 26 + 10 minutes


def should_continue(result):
    return bool(result.get("incomplete")) and result.get("missing_chunks", 0) == 0 \
        and result.get("completed_dates", 0) > 0


def main(run=subprocess.run, clock=time.monotonic):
    t0 = clock()
    result_path = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "arbdesk-forecast-nightly.json"
    missing_current = None          # None: every city's current run
    passes = []
    for n in range(1, MAX_PASSES + 1):
        if n > 1 and (clock() - t0) / 60 > START_BY_MIN:
            print(f"pass {n} not started: {START_BY_MIN} minutes have gone")
            break
        result_path.unlink(missing_ok=True)
        env = {**os.environ, "FORECAST_RESULT_PATH": str(result_path),
               "FORECAST_DEADLINE_MINUTES": str(FIRST_DEADLINE_MIN if n == 1 else NEXT_DEADLINE_MIN)}
        if missing_current is not None:
            env["FORECAST_CURRENT_CITIES"] = ",".join(missing_current)
        print(f"--- pass {n}" + ("" if missing_current is None
                                  else f" (current runs for {len(missing_current)} city(ies))"), flush=True)
        try:
            proc = run([sys.executable, "scripts/ingest_forecasts.py"], env=env,
                       timeout=FIRST_TIMEOUT_S if n == 1 else NEXT_TIMEOUT_S)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
        result = None
        if code == 0 and result_path.exists():
            result = json.loads(result_path.read_text())
        passes.append({"pass": n, "exit": code, "result": result})
        if code != 0 or result is None:
            break
        missing_current = list(result.get("current_cities_missing") or [])
        if not should_continue(result):
            break
    print("passes: " + json.dumps(passes))
    why = verdict(passes)
    if why:
        print(f"::error::the night's forecast ingest failed: {why}")
        return 1
    return 0


def verdict(passes):
    """Why the step fails, or None. The chain's rule (forecast_backfill_job)."""
    if not passes:
        return "no pass ran"
    for p in passes:
        if p["exit"] != 0 or p["result"] is None:
            return f"pass {p['pass']} ended with {p['exit']} and no result"
    last = passes[-1]["result"]
    refused = sum(p["result"].get("missing_chunks", 0) for p in passes)
    if refused:
        return (f"{refused} chunk(s) refused: the source answered and declined that window, "
                "and asking again will not close it")
    if last.get("incomplete") and last.get("completed_dates", 0) == 0:
        return f"pass {passes[-1]['pass']} completed no date and left the run incomplete"
    return None


if __name__ == "__main__":
    sys.exit(main())
