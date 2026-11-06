"""One bounded operational check. No production predictor or capital writes."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import temperature_model as model,temperature_live as live,temperature_learning as learning
import temperature_register

WRITES={'temperature_register','temperature_freeze','temperature_score','temperature_heartbeat','temperature_fit_report'}
READS={'shared_weather_snapshot','temperature_inputs','temperature_learning_inputs','temperature_snapshot'}


def run(rpc, *, bootstrap=False, write=False):
    def bounded(name,params=None,**kw):
        if name not in READS|WRITES or name in WRITES and not write:
            raise ValueError('verification write outside the declared research scope')
        kw['timeout']=min(kw.get('timeout',8),8)
        return rpc(name,params,**kw)
    old=bounded('shared_weather_snapshot')['model']
    inputs=bounded('temperature_inputs',timeout=8)
    receipt=None
    if inputs['state']=='not_registered' and bootstrap:
        if not write:raise ValueError('reference bootstrap requires explicit research writes')
        receipt=temperature_register.register(model.reference(),old['cohort'],bounded)
    result=live.run(bounded,max_packets=3) if write else dict(state=inputs['state'],writes=False)
    eligible=learning.run(bounded,plan=not write)
    after=bounded('shared_weather_snapshot')['model']
    if after!=old:raise ValueError('existing shared experiment registration changed')
    return dict(capture=result,learning=eligible,registration=receipt,old_experiment_unchanged=True,
        source_fingerprints=model.source_hashes(),changes_serving=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bootstrap',action='store_true')
    p.add_argument('--write',action='store_true')
    args=p.parse_args()
    import common
    try:print(json.dumps(run(common.rpc,bootstrap=args.bootstrap,write=args.write),sort_keys=True))
    except Exception as error:
        print(json.dumps(dict(state='error',error=type(error).__name__+': '+str(error)[:200],changes_serving=False)),file=sys.stderr)
        raise SystemExit(1)
