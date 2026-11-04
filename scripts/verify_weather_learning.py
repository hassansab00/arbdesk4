"""Bounded read-only rehearsal of the merged point-label learner.

The operator sees counts and source fingerprints. Fitted parameters stay in
memory and no artifact, calibration row, trading setting or forecast is written.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time


def summarize(output):
    labels = re.search(r'^point-label eligibility: (.+)$', output, re.M)
    counts = json.loads(labels.group(1)) if labels else None
    cells = re.search(r'^(\d+) cell\(s\), (\d+) applied, (\d+) shadow$', output, re.M)
    return {'label_eligibility': counts,
            'fit_cells': int(cells[1]) if cells else 0,
            'validated_cells': int(cells[2]) if cells else 0,
            'held_cells': int(cells[3]) if cells else 0}


def verify(lookback=30, budget_seconds=120):
    import common
    import forecast_postprocess as learner
    if not 1 <= lookback <= 90 or not 1 <= budget_seconds <= 180:
        raise ValueError('Verification bounds exceeded')
    started = time.monotonic()
    output = io.StringIO()
    old_argv = sys.argv
    try:
        sys.argv = ['forecast_postprocess.py', '--lookback', str(lookback), '--dry-run']
        with common.request_deadline(started + budget_seconds), redirect_stdout(output), redirect_stderr(output):
            code = learner.main()
    finally:
        sys.argv = old_argv
    result = summarize(output.getvalue())
    if code != 0 or not result['label_eligibility'] or not result['label_eligibility']['accepted_point_rows']:
        raise ValueError('Learning verification did not complete with eligible evidence')
    root = Path(learner.__file__).resolve().parent
    result.update(state='completed', read_only=True, fits_written=0, changes_serving=False,
                  elapsed_seconds=round(time.monotonic() - started, 3),
                  sources={name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                           for name in ('weather_outcomes.py', 'forecast_postprocess.py')})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lookback', type=int, default=30)
    parser.add_argument('--budget-seconds', type=int, default=120)
    args = parser.parse_args()
    try:
        result = verify(args.lookback, args.budget_seconds)
    except Exception as error:
        # Source replies, fitted coefficients and authentication details never
        # travel into a public workflow's failure output.
        print(json.dumps(dict(state='failed', error_type=type(error).__name__, read_only=True,
                              fits_written=0, changes_serving=False)))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
