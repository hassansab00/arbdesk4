"""Optional shared-weather forward capture, bounded within the existing Tick."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict.shared_weather_live import run
if __name__=='__main__':
    try:
        print(json.dumps(run(),sort_keys=True))
    except Exception as error:
        import time
        import common
        detail=dict(state='error',error=type(error).__name__+': '+str(error)[:200],changes_serving=False)
        print(json.dumps(detail),file=sys.stderr)
        try:
            with common.request_deadline(time.monotonic()+2):
                common.rpc('shared_weather_heartbeat',dict(p_detail=detail),timeout=1)
        except Exception:
            pass  # The stale heartbeat stays visible when the database itself is unreachable.
        raise SystemExit(1)
