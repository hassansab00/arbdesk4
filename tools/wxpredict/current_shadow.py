"""Freeze the accepted current-forecast candidate before outcomes are known."""
import datetime as dt
import time

from wxpredict import common, current_calls as calls, current_model, current_registry as registry

JOB = 'P7.11_current_forecast_shadow'
MAX_SECONDS, MAX_CALLS = 20, 200


def run(now=None, dry_run=False, deadline=None):
    now=now or dt.datetime.now(common.UTC)
    deadline=min(deadline or time.monotonic()+MAX_SECONDS,time.monotonic()+MAX_SECONDS)
    selected=registry.selected()
    detail=dict(changes_serving=False,selected=0,written=0,excluded={})
    if selected is None:
        return {**detail,'decision':'waiting for accepted current candidate'}
    meta,available=selected
    ops=common.ops()
    def finish(result):
        ignored=('model unavailable at first call','insufficient exact city/station/source/horizon training dates')
        result['attention']=bool(result.get('refused') or result.get('deferred')
            or any(k not in ignored for k in result['excluded'])
            or result['selected']>result['written'] and not dry_run)
        if not dry_run:
            ops.log_run(JOB,'attention' if result['attention'] else 'ok',result['written'],result)
        return result
    first=now-dt.timedelta(minutes=30)
    with ops.request_deadline(deadline):
        served=ops.rest_all('prediction_checkpoints',[
            ('select','*'),('decided_at','gte.'+first.isoformat()),('decided_at','lte.'+now.isoformat()),
            ('checkpoint','in.('+','.join(calls.CHECKPOINTS)+')')],order='decided_at.asc,checkpoint_id.asc')
        held=ops.rest_all('variant_shadow_checkpoints',[
            ('select','city_key,target_date,checkpoint'),('variant','eq.'+registry.VARIANT),
            ('target_date','gte.'+(now.date()-dt.timedelta(days=1)).isoformat())],
            order='city_key.asc,target_date.asc,checkpoint.asc')
        held={(r['city_key'],r['target_date'],r['checkpoint']) for r in held}
        todo=[r for r in calls.arrival.first_calls(served,str(now.date()-dt.timedelta(days=1)),
                                                str(now.date()+dt.timedelta(days=1)),calls.CHECKPOINTS)
              if (r['city_key'],r['target_date'],r['checkpoint']) not in held]
        detail['deferred']=max(0,len(todo)-MAX_CALLS)
        todo=todo[:MAX_CALLS]
        keys=sorted({r['input_key'] for r in todo if r.get('input_key')})
        if not keys:
            if todo:
                detail['excluded']['missing original primary packet']=len(todo)
            return finish({**detail,'decision':'no new source-aligned first calls'})
        receipts=ops.rest_all('live_capture',[
            ('select','capture_id,key,kind,payload,captured_at,written_at'),('kind','eq.wx_features'),
            ('key','in.('+','.join(keys)+')')],order='capture_id.asc')
        rows,parent=[],[]
        def exclude(reason):
            detail['excluded'][reason]=detail['excluded'].get(reason,0)+1
        for call in todo:
            if (not call.get('frozen_at') or calls.inputs.unix(available)>calls.inputs.unix(call['frozen_at'])
                    or meta['fit_last']>=call['target_date']):
                exclude('model unavailable at first call');continue
            choices=[r for r in receipts if r['key']==call.get('input_key')
                     and calls.inputs.unix(r['written_at'])<=calls.inputs.unix(call['frozen_at'])]
            if not choices:
                exclude('missing original primary packet');continue
            receipt=max(choices,key=lambda r:(calls.inputs.unix(r['captured_at']),int(r['capture_id'])))
            row,why=calls.attach(call,calls.object_of(receipt['payload']),receipt['captured_at'],receipt['written_at'])
            if why:
                exclude(why);continue
            key=row['source_identity']+'|'+row['checkpoint']
            if meta['training_dates'].get(key,0)<20:
                exclude('insufficient exact city/station/source/horizon training dates');continue
            rows.append(row);parent.append(call)
        detail['selected']=len(rows)
        if not rows:
            return finish({**detail,'decision':'no eligible current-model inputs'})
        remaining=deadline-time.monotonic()
        if remaining<4:
            return finish({**detail,'decision':'deferred for remaining shadow budget'})
        booster=registry.load(meta,budget_s=min(10,remaining/2))
        predictions=current_model.predict(booster,rows)
        from wxpredict import wxrd_live
        out=[]
        for call,row,p in zip(parent,rows,predictions):
            if max(abs(p[k]-row['incumbent_probs'][k]) for k in row['band_ids'])>current_model.BLEND+1e-12:
                exclude('prediction correction exceeds its bound')
                continue
            frozen=wxrd_live.variant_row(call,row['row']['unit'],row['band_ids'],
                [p[k] for k in row['band_ids']],registry.VARIANT+':'+meta['version'],
                bool(call.get('calibration')),variant=registry.VARIANT)
            frozen.update(decided_at=now.isoformat(),station=row['row']['station'])
            out.append(frozen)
        if not out:
            return finish({**detail,'decision':'all current corrections refused'})
        if time.monotonic()>=deadline-1:
            return finish({**detail,'decision':'deferred before freeze deadline'})
        if not dry_run:
            from frozen_calls import freeze_insert
            refused=[]
            detail['written']=freeze_insert('variant_shadow_checkpoints',out,
                'city_key,target_date,checkpoint,variant',writer=ops.upsert,refused=refused,deadline=deadline)
            detail['refused']=refused
        detail.update(decision='shadow captured',model_sha256=meta['model_sha256'])
    return finish(detail)
