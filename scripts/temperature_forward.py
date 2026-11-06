"""Bounded research capture in the existing Tick; no capital or serving write."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict.temperature_live import run

if __name__ == '__main__':
    try:
        print(json.dumps(run(), sort_keys=True))
    except Exception as error:
        detail=dict(state='error', error=type(error).__name__+': '+str(error)[:200], changes_serving=False)
        print(json.dumps(detail), file=sys.stderr)
        try:
            import time
            import common
            with common.request_deadline(time.monotonic()+2):
                common.rpc('temperature_heartbeat', dict(p_detail=detail), timeout=1)
        except Exception:
            pass  # Database failure leaves the previous heartbeat stale and this job failed.
        raise SystemExit(1)
