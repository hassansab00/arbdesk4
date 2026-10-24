"""Daily current-forecast challenger: recorded calls -> fit -> shadow -> validate.

The recipe and gates are fixed before seeing its evaluation outcomes. This
separate lineage never changes a production forecast or capital allocation.
"""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import candidate_cycle as legacy, common, current_calls as calls
from wxpredict import current_model as model, current_registry as registry, cycle_registry

JOB = 'P7.11_current_forecast_cycle'
INPUTS_FROM = '2026-10-24'
MIN_TRAIN_DATES, TRAIN_WINDOW_DAYS = 20, 60
POLICY = dict(recipe=model.RECIPE, inputs_from=INPUTS_FROM, minimum_training_dates=MIN_TRAIN_DATES,
              training_window_days=TRAIN_WINDOW_DAYS, evaluation_dates=legacy.HOLDOUT_DATES,
              minimum_cities_per_checkpoint_date=legacy.MIN_CITIES_PER_DATE,
              minimum_coverage=legacy.MIN_COVERAGE, minimum_local_validation_dates=10,
              evaluation_checkpoints=list(calls.CHECKPOINTS),
              improvement_checkpoints=list(legacy.PREPEAK_CHECKPOINTS),
              maximum_probability_change=legacy.MAX_BUCKET_CHANGE,
              maximum_current_age_hours=calls.MAX_CURRENT_AGE_HOURS,
              minimum_current_models=calls.MIN_CURRENT_MODELS,
              blend=model.BLEND, seed=model.SEED, rounds=model.ROUNDS, parameters=model.PARAMS,
              forward_full_day_margin=2, changes_serving=False)


def window(night):
    today = dt.date.fromisoformat(night)
    last = today-dt.timedelta(days=2)
    first = last-dt.timedelta(days=legacy.HOLDOUT_DATES-1)
    fit_last = first-dt.timedelta(days=2)
    fit_first = max(dt.date.fromisoformat(INPUTS_FROM), fit_last-dt.timedelta(days=TRAIN_WINDOW_DAYS-1))
    return dict(night=night, train_first=str(fit_first), fit_last=str(fit_last),
                evaluation_first=str(first), evaluation_last=str(last), policy=POLICY,
                changes_serving=False)


def availability(rows, coverage, first, last, training=False):
    groups = collections.defaultdict(set)
    for r in rows:
        if first <= r['date'] <= last:
            groups[(r['date'],r['checkpoint'])].add(r['city'])
    dates = []
    if first <= last:
        a,b = dt.date.fromisoformat(first),dt.date.fromisoformat(last)
        dates = [str(a+dt.timedelta(days=i)) for i in range((b-a).days+1)]
    good = [day for day in dates if all(len(groups[(day,cp)]) >= legacy.MIN_CITIES_PER_DATE
                                      for cp in calls.CHECKPOINTS)]
    paired = sum(len(v) for v in groups.values())
    expected = coverage['first_calls']
    fraction = paired/expected if expected else 0.
    needed = MIN_TRAIN_DATES if training else legacy.HOLDOUT_DATES
    return dict(ready=len(good)>=needed and fraction>=legacy.MIN_COVERAGE,
                complete_dates=len(good), required_dates=needed, paired_rows=paired,
                first_calls=expected, coverage=fraction, input_coverage=coverage,
                checkpoint_dates=[dict(date=d,checkpoint=cp,cities=len(groups[(d,cp)]))
                                  for d in dates for cp in calls.CHECKPOINTS])


def load_window(first, last, through):
    if first > last:
        return [], dict(first_calls=0, paired_rows=0, excluded={}, dataset_sha256=calls.digest([]))
    return calls.build(first,last,through)


def prepare(p):
    through = dt.datetime.fromisoformat(p['night']).replace(tzinfo=common.UTC).timestamp()
    train, tc = load_window(p['train_first'],p['fit_last'],through)
    test, vc = load_window(max(INPUTS_FROM,p['evaluation_first']),p['evaluation_last'],through)
    ta = availability(train,tc,p['train_first'],p['fit_last'],training=True)
    va = availability(test,vc,p['evaluation_first'],p['evaluation_last'])
    p = {**p,'training_availability':ta,'evaluation_availability':va}
    p.update(decision='ready' if ta['ready'] and va['ready'] else 'wait',
             reason='recorded first-call coverage is sufficient' if ta['ready'] and va['ready']
                    else 'insufficient recorded training or independent evaluation coverage')
    return p,train,test


def local_results(scores, training=()):
    """Separate station/source/city/horizon evidence and reasons for a miss."""
    train = collections.defaultdict(set)
    for r in training:
        train[(r['source_identity'],r['checkpoint'])].add(r['date'])
    groups = collections.defaultdict(list)
    for r in scores:
        groups[(r['source_identity'],r['checkpoint'])].append(r)
    out = []
    for (identity,cp),rs in sorted(groups.items()):
        n = len(rs)
        gains = [r['incumbent_nll']-r['candidate_nll'] for r in rs]
        rng = random.Random(model.SEED)
        boots = sorted(sum(rng.choices(gains,k=n))/n for _ in range(2000))
        current = dict(source_identity=identity,city=rs[0]['city'],checkpoint=cp,calls=n,
                       dates=len({r['date'] for r in rs}),training_dates=len(train[(identity,cp)]),
                       log_loss_gain_lower90=boots[99],
                       candidate_hits=sum(r['candidate_hit'] for r in rs),
                       incumbent_hits=sum(r['incumbent_hit'] for r in rs),
                       market_hits=sum(r['market_hit'] for r in rs))
        for name in ('candidate','incumbent','market'):
            for metric in ('nll','brier'):
                current[name+'_'+metric] = sum(r[name+'_'+metric] for r in rs)/n
        current['supported'] = (current['training_dates']>=MIN_TRAIN_DATES and current['dates']>=10
            and current['candidate_hits']>=current['incumbent_hits']
            and current['candidate_brier']<=current['incumbent_brier'] and boots[99]>0)
        current['reason'] = ('independent local improvement' if current['supported'] else
            'insufficient local dates' if current['training_dates']<MIN_TRAIN_DATES or current['dates']<10 else
            'local improvement did not pass hit-rate, Brier and log-loss gates')
        current.update(candidate_fixed_misses=sum(r['candidate_hit'] and not r['incumbent_hit'] for r in rs),
                       candidate_broke_hits=sum(r['incumbent_hit'] and not r['candidate_hit'] for r in rs),
                       incumbent_miss_current_point_correct=sum(not r['incumbent_hit'] and r['current_point_in_winner'] for r in rs),
                       incumbent_and_current_point_missed=sum(not r['incumbent_hit'] and not r['current_point_in_winner'] for r in rs),
                       mean_current_ensemble_sd_steps=sum(r['ensemble_sd_steps'] for r in rs)/n)
        out.append(current)
    return sorted(out,key=lambda r:(-r['candidate_hits']/r['calls'],r['candidate_nll'],r['city'],r['checkpoint']))


def assess(records, predictions, expected, first, last, training=()):
    scored = model.scores(records,predictions)
    result = legacy.assess(scored,expected,first,last,checkpoints=calls.CHECKPOINTS,
                           improvement_checkpoints=legacy.PREPEAK_CHECKPOINTS)
    brier_regressions = [cp for cp in calls.CHECKPOINTS
        if (group:=[r for r in scored if r['checkpoint']==cp])
        and sum(r['candidate_brier']-r['incumbent_brier'] for r in group)>1e-12]
    if brier_regressions:
        result['decision']='reject'
        result['reasons'].append('checkpoint Brier regression')
    result.update(brier_regressed_checkpoints=brier_regressions,
                  city_horizons=local_results(scored,training),standalone_weather_weight=model.BLEND)
    return result


def fit_and_evaluate(p, train, test, work):
    ta=availability(train,p['training_availability']['input_coverage'],p['train_first'],p['fit_last'],True)
    va=availability(test,p['evaluation_availability']['input_coverage'],p['evaluation_first'],p['evaluation_last'])
    if p['decision']!='ready' or not ta['ready'] or not va['ready']:
        raise ValueError('current candidate cannot fit before its coverage gates pass')
    if (any(r['date']>p['fit_last'] for r in train)
            or any(not p['evaluation_first']<=r['date']<=p['evaluation_last'] for r in test)
            or p['fit_last']>=p['evaluation_first']):
        raise ValueError('current training/evaluation overlap')
    booster = model.fit(train)
    predictions = model.predict(booster,test)
    result = {**p,**assess(test,predictions,p['evaluation_availability']['first_calls'],
                         p['evaluation_first'],p['evaluation_last'],train)}
    raw = booster.model_to_string().encode()
    sha = hashlib.sha256(raw).hexdigest()
    model_id = 'wxrd-auto-'+p['night'].replace('-','')+'-'+sha[:12]
    os.makedirs(work,exist_ok=True)
    weight = os.path.join(work,model_id+'.txt.gz')
    with open(weight,'wb') as fh:
        fh.write(gzip.compress(raw,mtime=0))
    counts = collections.defaultdict(set)
    for r in train:
        counts[r['source_identity']+'|'+r['checkpoint']].add(r['date'])
    meta = dict(version=model_id,model_sha256=sha,recipe=model.RECIPE,blend=model.BLEND,
                fit_last=p['fit_last'],features=booster.feature_name(),parameters=model.PARAMS,
                seed=model.SEED,rounds=model.ROUNDS,training_calls=len(train),
                training_dates={k:len(v) for k,v in sorted(counts.items())},
                evaluation_report=registry.REPORTS+'/'+p['night']+'.json')
    result.update(model_sha256=sha,training_dataset_sha256=calls.digest(train),
                  evaluation_dataset_sha256=calls.digest(test),
                  model_features=meta['features'],training_calls=len(train))
    source_hashes=model.source_hashes()
    result.update(recipe_sources=source_hashes,
                  code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=common.ROOT,text=True).strip(),
                  feature_gain=dict(zip(booster.feature_name(),map(float,booster.feature_importance(importance_type='gain')))))
    meta['recipe_sources_sha256']=calls.digest(source_hashes)
    return result,meta,weight


def forward(meta, available, night):
    first = str(dt.date.fromisoformat(available[:10])+dt.timedelta(days=POLICY['forward_full_day_margin']))
    last = str(dt.date.fromisoformat(night)-dt.timedelta(days=2))
    p = dict(night=night,decision='wait forward',changes_serving=False,policy=POLICY,
             model_sha256=meta['model_sha256'],forward_first=first,forward_last=last)
    if first>last:
        return {**p,'reason':'no completed full forward dates yet'}
    through = dt.datetime.fromisoformat(night).replace(tzinfo=common.UTC).timestamp()
    # Grade the fixed first fourteen complete target dates, never a selected
    # favourable subset or a fresh model fitted after their predictions.
    last_eval = str(dt.date.fromisoformat(first)+dt.timedelta(days=legacy.HOLDOUT_DATES-1))
    records, coverage = calls.build(first,min(last,last_eval),through)
    held = calls.arrival.mirrored_rows('variant_shadow_checkpoints',calls.inputs.unix(first+'T00:00:00Z')-2*86400,
                                      min(through,calls.inputs.unix(str(dt.date.fromisoformat(last_eval)+dt.timedelta(days=2))+'T00:00:00Z')),
                                      where=lambda r:r['variant']==registry.VARIANT
                                      and r['variant_version']==registry.VARIANT+':'+meta['version'])
    held = {(r['city_key'],r['target_date'],r['checkpoint']):r for r in held}
    scored_records,predictions = [],[]
    for r in records:
        if r['date']>last_eval:
            continue
        shadow = held.get((r['city'],r['date'],r['checkpoint']))
        if shadow is None or not shadow.get('frozen_at'):
            continue
        receipt = calls.inputs.unix(shadow['frozen_at'])
        end = common.local_day_bounds(dt.date.fromisoformat(r['date']), calls.ZoneInfo(r['timezone']))[1]
        if not (calls.inputs.unix(available)<=calls.inputs.unix(r['frozen_at'])
                <=calls.inputs.unix(shadow['decided_at'])<=receipt<end
                and receipt<calls.inputs.unix(r['settled_at'])<=calls.inputs.unix(r['label_received_at'])):
            continue
        scored_records.append(r)
        predictions.append(calls.distribution(shadow['probs'],r['band_ids']))
    p.update(paired_shadow_calls=len(scored_records),first_calls=coverage['first_calls'])
    if last<last_eval:
        return {**p,'reason':'fourteen fixed forward target dates have not completed'}
    # Rebuild the denominator for the exact registered window if later dates
    # are now available. No missing prediction disappears from this count.
    records,coverage = calls.build(first,last_eval,through)
    result = assess(scored_records,predictions,coverage['first_calls'],first,last_eval)
    for row in result['city_horizons']:
        row['training_dates']=meta['training_dates'].get(row['source_identity']+'|'+row['checkpoint'],0)
        row['supported']=(row['training_dates']>=MIN_TRAIN_DATES and row['dates']>=10
                          and row['candidate_hits']>=row['incumbent_hits']
                          and row['candidate_brier']<=row['incumbent_brier'] and row['log_loss_gain_lower90']>0)
        row['reason']=('independent local improvement' if row['supported'] else
                       'insufficient local training/validation dates or local improvement unproven')
    return {**p,**result,'decision':'forward validated' if result['decision']=='accept shadow' else 'forward rejected',
            'reason':'forward test complete; production promotion requires a separate reviewed decision',
            'forward_evaluation_last':last_eval,'input_coverage':coverage}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan',action='store_true')
    ap.add_argument('--pause',action='store_true')
    ap.add_argument('--resume',action='store_true')
    args=ap.parse_args(argv)
    if args.pause and args.resume:
        raise ValueError('choose pause or resume')
    if args.pause or args.resume:
        state=registry.state();state['paused']=args.pause
        cycle_registry.write(os.path.join(common.ROOT,registry.STATE),state)
        return 0
    night=str(dt.datetime.now(common.UTC).date())
    path=registry.report_path(night)
    saved=cycle_registry.read(path)
    ready=False
    if saved is not None:
        report=saved
    elif registry.state()['paused']:
        report=dict(night=night,decision='paused',changes_serving=False,policy=POLICY)
    elif (active:=registry.selected()) is not None:
        prior=registry.state().get('forward_validated')
        if prior:
            proof=cycle_registry.read(os.path.join(common.ROOT,prior['report']))
            if (proof is None or calls.digest(proof)!=prior['report_sha256']
                    or proof.get('model_sha256')!=active[0]['model_sha256']
                    or proof.get('decision')!='forward validated'):
                raise ValueError('current forward validation evidence hash mismatch')
        report=(dict(night=night,decision='forward validated',changes_serving=False,
                     validation_report=prior['report'],reason='fixed forward test passed; ready for reviewed promotion')
                if prior else forward(*active,night))
    else:
        report,train,test=prepare(window(night))
        ready=report['decision']=='ready'
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as fh:
            fh.write('fit='+str(ready).lower()+'\n')
    if args.plan and ready:
        print(json.dumps(report));return 0
    fitted=None
    if ready:
        from wxpredict import model_artifacts
        work=os.path.join(common.ROOT,'.cache/current-forecast',night)
        report,meta,weight=fit_and_evaluate(report,train,test,work)
        if report['decision']=='accept shadow':
            meta['asset_url']=model_artifacts.publish(weight,meta['version'],meta['model_sha256'])
            fitted=meta
    if saved is None:
        registry.record(report,fitted)
    if os.environ.get('SUPABASE_URL'):
        def summary(key):
            value=report.get(key) or {}
            return {k:v for k,v in value.items() if k not in ('checkpoint_dates','input_coverage')}
        common.ops().log_run(JOB,'ok',report.get('training_calls',0),
            dict(decision=report['decision'],changes_serving=False,reason=report.get('reason'),
                 training_availability=summary('training_availability'),
                 evaluation_availability=summary('evaluation_availability')))
    print(json.dumps(report,indent=1))
    return 0


if __name__=='__main__':
    sys.exit(main())
