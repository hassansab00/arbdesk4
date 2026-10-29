"""Grade one fixed remaining-day challenger on its first full forward dates.

No refit, reconstructed prediction, or late shadow receipt enters this gate.
An incomplete window returns counts only. This module never changes serving.
"""
import datetime as dt
import json
import math

from wxpredict import common, arrival_dataset as arrival, decision_inputs as inputs

POLICY = 'remaining-challenger-fixed-forward-v1'


def decoded(value):
    return json.loads(value) if isinstance(value,str) else value


def collect(calls, outcomes, shadows, zones, version, available, first, last, now):
    from wx_city_calibration import metrics, transform, IDENTITY
    from tick import market_price
    from zoneinfo import ZoneInfo
    from wxpredict import candidate_cycle as cycle
    calls=arrival.first_calls(calls,first,last,cycle.REMAINING_CHECKPOINTS)
    facts={r['checkpoint_id']:r for r in outcomes}
    shadow={}
    for r in sorted(shadows,key=lambda r:inputs.unix(r.get('frozen_at') or r['decided_at'])):
        if r.get('variant')=='wxrd_auto' and r.get('variant_version')==version:
            shadow.setdefault((r['city_key'],r['target_date'],r['checkpoint']),r)
    scores=[]
    for p in calls:
        try:
            s=shadow[(p['city_key'],p['target_date'],p['checkpoint'])]
            o=facts[p['checkpoint_id']]
            if not p.get('frozen_at') or not s.get('frozen_at'):
                continue
            if not p.get('station') or s.get('station')!=p['station']:
                continue
            receipt, frozen=inputs.unix(s['frozen_at']),inputs.unix(p['frozen_at'])
            end=common.local_day_bounds(dt.date.fromisoformat(p['target_date']),ZoneInfo(zones[p['city_key']]))[1]
            if not (inputs.unix(available)<=frozen<=receipt<=frozen+30*60
                    and inputs.unix(s['decided_at'])==inputs.unix(p['decided_at'])
                    and receipt<end<=now and receipt<inputs.unix(o['settled_at'])
                    <=inputs.unix(o['banked_at'])<=now):
                continue
            base,candidate=decoded(p['probs']),decoded(s['probs'])
            ids=sorted(base)
            transform(base,ids,IDENTITY);transform(candidate,ids,IDENTITY)
            winner=o['winner_band_id']
            if winner not in ids:
                continue
            book=decoded(p.get('market')) or {}
            if not isinstance(book,dict) or any(not isinstance(book.get(k),dict) for k in ids):
                continue
            if any(any(v is not None and (not isinstance(v,(int,float)) or isinstance(v,bool)
                       or not math.isfinite(v) or not 0<=v<=1)
                       for v in (book[k].get('bid'),book[k].get('ask'),book[k].get('last')))
                   or (book[k].get('bid') is not None and book[k].get('ask') is not None
                       and book[k]['bid']>book[k]['ask']) for k in ids):
                continue
            prices={k:market_price(book.get(k) or {}) for k in ids}
            if any(v is None for v in prices.values()) or not sum(prices.values()):
                continue
            total=sum(prices.values())
            market={k:v/total for k,v in prices.items()}
            a,b,m=(metrics(q,winner) for q in (candidate,base,market))
            scores.append(dict(city=p['city_key'],date=p['target_date'],checkpoint=p['checkpoint'],
                candidate_hit=bool(a['hit']),incumbent_hit=bool(b['hit']),market_hit=bool(m['hit']),
                candidate_nll=a['log_loss'],incumbent_nll=b['log_loss'],market_nll=m['log_loss'],
                candidate_brier=a['brier'],incumbent_brier=b['brier'],market_brier=m['brier'],
                jump=max(abs(candidate[k]-base[k]) for k in ids)))
        except (KeyError, TypeError, ValueError):
            continue
    return scores,len(calls)


def review(meta, available, night):
    from wxpredict import candidate_cycle as cycle, wxrd_live
    first=str(dt.date.fromisoformat(available[:10])+dt.timedelta(days=2))
    last=str(dt.date.fromisoformat(first)+dt.timedelta(days=cycle.HOLDOUT_DATES-1))
    if meta['fit_last']>=first:
        raise ValueError('forward candidate trained inside its fixed evaluation window')
    completed=str(dt.date.fromisoformat(night)-dt.timedelta(days=2))
    result=dict(night=night,decision='wait forward',changes_serving=False,forward_policy=POLICY,
                model_sha256=meta['model_sha256'],forward_first=first,forward_last=last)
    if completed<last:
        return dict(result,reason='fixed full forward window not yet complete')
    through=inputs.unix(night+'T00:00:00Z')
    start=inputs.unix(first+'T00:00:00Z')-2*86400
    calls=arrival.mirrored_rows('prediction_checkpoints',start,through)
    outcomes=arrival.mirrored_rows('fact_checkpoint_outcome',start,through)
    shadows=arrival.mirrored_rows('variant_shadow_checkpoints',start,through,
        where=lambda r:r['variant']=='wxrd_auto' and r['variant_version']==wxrd_live.variant_version(meta,'wxrd_auto'))
    zones={k:c['timezone'] for k,c in common.cities().items()}
    scores,expected=collect(calls,outcomes,shadows,zones,wxrd_live.variant_version(meta,'wxrd_auto'),
                            available,first,last,through)
    result.update(cycle.assess(scores,expected,first,last,checkpoints=cycle.REMAINING_CHECKPOINTS,
                              improvement_checkpoints=cycle.PREPEAK_CHECKPOINTS))
    result.update(cycle.probability_quality(scores))
    reasons=list(result['reasons'])+result['quality_reasons']
    objective=[r for r in scores if r['checkpoint'] in cycle.PREPEAK_CHECKPOINTS]
    if (not objective or sum(r['candidate_nll']-r['market_nll'] for r in objective)>0
            or sum(r['candidate_brier']-r['market_brier'] for r in objective)>0):
        reasons.append('forward candidate has not matched saved market proper scores')
    return dict(result,decision='forward rejected' if reasons else 'forward validated',reasons=reasons,
                paired_forward_calls=len(scores),expected_forward_calls=expected,
                reason='fixed original forward calls; production promotion remains separate')
