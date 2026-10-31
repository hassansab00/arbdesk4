"""Run a pipeline stage under one wall deadline and retain its outcome."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import time


def run(stage, seconds, command):
    if not stage.replace('-', '').replace('_', '').isalnum() or seconds <= 0 or not command:
        raise ValueError('invalid stage or budget')
    started = time.monotonic()
    end = started + seconds
    inherited = os.environ.get('AD4_REQUEST_DEADLINE')
    if inherited:
        end = min(end, float(inherited))
    code, status = 1, 'failed'
    try:
        result = subprocess.run(command, env=dict(os.environ, AD4_REQUEST_DEADLINE=str(end)),
                                timeout=max(.001, end-time.monotonic()), check=False)
        code = result.returncode
        status = 'success' if code == 0 else 'failed'
    except subprocess.TimeoutExpired:
        code, status = 124, 'timeout'
    finally:
        receipt = dict(stage=stage, status=status, exit_code=code, budget_seconds=seconds,
                       elapsed_seconds=round(time.monotonic()-started, 3),
                       finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
        directory = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'ad4-stage-receipts'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (stage+'.json')).write_text(json.dumps(receipt)+'\n')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out:
                out.write(f"Stage `{stage}`: **{status}**, exit {code}, {receipt['elapsed_seconds']} s.\n\n")
        print(json.dumps(receipt), flush=True)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    return run(args.stage, args.seconds, args.command[1:] if args.command[:1] == ['--'] else args.command)


if __name__ == '__main__':
    raise SystemExit(main())
