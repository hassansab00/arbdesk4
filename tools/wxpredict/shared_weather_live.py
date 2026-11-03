"""Registered-model inference using only new original early checkpoints."""
import collections
import datetime as dt
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from wxpredict import shared_weather as model, forecast_state as base, pre_day_hourly as hourly
from wxpredict import pre_day_pairs as pp
import common
BUDGET_SECONDS=20.


def prepare(packet,artifact,registered_at,now):
    call=packet['call'];city=packet['city'];weather=packet.get('weather')
    if not weather:return None,'missing_received_current_weather'
    definitions={str(b['band_id']):b for b in packet.get('bands') or []}
    context=call.get('predictive_context') or {};audit=context.get('early_forecast_audit') or {}
    original,reason=base.attach(dict(call=call,city=city,fact=None,
        timing_reference=context.get('timing_reference'),contract=audit.get('contract') or 'unknown',
        rules_sha256=audit.get('rules_sha256')),definitions,now)
    if original is None:return None,reason
    if original['checkpoint']=='morning' and original['peak_status']!='before_as_of_reference_peak':
        return None,'missing_original_morning_peak_proof'
    received,_=hourly.History([weather]).select(original['city'],original['date'],original['timezone'],original['as_of'])
    record=model.attach(original,received)
    if record is None:return None,'incomplete_received_local_weather_day'
    peak=max(received['hours'],key=lambda h:h['values'][0])['utc']
    weather_lead=(peak-pp.timestamp(original['as_of']).timestamp())/3600
    if original['checkpoint']=='morning' and weather_lead<1:
        return None,'less_than_one_hour_before_forecast_peak'
    q=model.predict(artifact,[record],forward=True,registered_at=registered_at,now=now)[0]
    top=min(q,key=lambda b:(-q[b],b))
    metadata=dict(source_commit=os.environ.get('GITHUB_SHA') or 'local',display_name=city.get('display_name') or original['city'],timezone=original['timezone'],
        unit=original['unit'],station=original['station'],feature_sha256=record['feature_sha256'],
        weather_sha256=received['sha256'],weather_capture_id=weather['capture_id'],
        peak_status=original['peak_status'],peak_lead_h=original['peak_lead_h'],weather_peak_lead_h=weather_lead,
        candidate_label=definitions[top].get('label') or top,
        incumbent_label=definitions[original['incumbent_top']].get('label') or original['incumbent_top'])
    return dict(source_checkpoint_id=original['checkpoint_id'],weather_capture_id=weather['capture_id'],
                probs=q,market=base.market(original),context=metadata),None


def run():
    started=time.monotonic();skipped=collections.Counter();rows=[]
    with common.request_deadline(started+BUDGET_SECONDS):
        inputs=common.rpc('shared_weather_inputs',timeout=10)
        if inputs['state']!='registered':return dict(state=inputs['state'],new=0)
        artifact=inputs['artifact'];model.model(artifact)
        for packet in inputs['calls']:
            if time.monotonic()>started+BUDGET_SECONDS-5:skipped['deadline']+=1;continue
            now=dt.datetime.now(dt.timezone.utc).isoformat()
            try:row,reason=prepare(packet,artifact,inputs['registered_at'],now)
            except (KeyError,ValueError,TypeError) as error:
                skipped['invalid_evidence:'+str(error)[:100]]+=1;continue
            if row:rows.append(row)
            else:skipped[reason]+=1
        frozen=common.rpc('shared_weather_freeze',dict(p_model=artifact['sha256'],p_rows=rows),timeout=5) if rows else {'new':0}
        scored=common.rpc('shared_weather_score',timeout=5)
        result=dict(state='forward_test',model_sha256=artifact['sha256'],due=len(inputs['calls']),new=frozen['new'],
            scored=scored,skipped=dict(skipped),evaluation_first=inputs['evaluation_first'],evaluation_last=inputs['evaluation_last'],
            elapsed_seconds=round(time.monotonic()-started,3),changes_serving=False)
        common.rpc('shared_weather_heartbeat',dict(p_detail=result),timeout=3)
        return result
