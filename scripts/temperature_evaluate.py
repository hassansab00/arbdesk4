"""Evaluate a private export of one registered temperature trial. No promotion."""
import argparse
import json
import datetime as dt
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict.temperature_evaluation import evaluate_trial
from wxpredict.temperature_points import evaluate_points

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path)
    args=p.parse_args()
    payload=json.loads(args.input.read_text())
    result=evaluate_trial(payload['registration'],payload['calls'],payload['as_of'])
    first,last=payload['registration']['evaluation_first'],payload['registration']['evaluation_last']
    items=[x for x in payload['calls'] if first<=x['call']['target_date']<=last]
    first_date=dt.date.fromisoformat(first)
    expected=[(city,(first_date+dt.timedelta(days=i)).isoformat()) for city in payload['registration']['cohort'] for i in range(20)]
    result['point_metrics']=evaluate_points(items,payload.get('points') or [],payload['as_of'],expected)
    result['eligible_for_promotion']=bool(result['distribution_gates_passed'] and result['point_metrics']['point_gates_passed'])
    result['manual_promotion_required']=True
    print(json.dumps(result,sort_keys=True))
