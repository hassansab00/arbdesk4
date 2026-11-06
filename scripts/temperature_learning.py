"""Report eligibility, fit only legitimate evidence, store private candidates."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict.temperature_learning import run

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', action='store_true')
    args = p.parse_args()
    try:
        detail = run(plan=args.plan)
        if args.plan and os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                f.write('fit='+str(detail['state'] == 'eligible_to_fit').lower()+'\n')
        print(json.dumps(detail, sort_keys=True))
    except Exception as error:
        detail=dict(state='error', error=type(error).__name__+': '+str(error)[:200], changes_serving=False)
        print(json.dumps(detail), file=sys.stderr)
        try:
            import time
            import common
            with common.request_deadline(time.monotonic()+2):
                common.rpc('temperature_fit_report', dict(p_artifact=None,p_detail=detail), timeout=1)
        except Exception:
            pass  # Database failure leaves the previous heartbeat stale and this job failed.
        raise SystemExit(1)
