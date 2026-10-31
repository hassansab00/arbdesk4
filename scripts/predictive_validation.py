"""Score original served calls; fit bounded city calibration on prior labels.

Run nightly to learn and score, or --score-only after venue confirmation.
No reads of blinded shadow experiments; no rewriting a call or outcome.
"""
import argparse
import datetime as dt
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from common import get_cities, log_run, rest_all, upsert_replace
from tick import CHECKPOINTS, PEAK_OFFSETS_H, decision_times
import wx_city_calibration as cal
import city_model_selection as selector
import early_forecast_audit
import early_city_adaptation as early

JOB = 'P7.10_city_prediction_validation'
HORIZONS = ('day_ahead', *CHECKPOINTS)
OVERALL = tuple(h for h in HORIZONS if h != 'postpeak_1h')
SCOPES = ('served', 'wxrd3', 'wxrd3_raw')
ROOT = Path(__file__).resolve().parents[1]
REPORT_BATCH_BYTES = 512 * 1024  # request-memory bound, not a statistical parameter
REPORT_BATCH_ROWS = 25


def publish_reports(rows):
    """Keep every derived report; bound each idempotent write by UTF-8 bytes.

    A report holds nested evidence, so a row-count-only batch can send many
    megabytes in one request. Reject an oversized single report explicitly;
    never truncate its evidence or apply this writer to original facts.
    """
    batches, batch, size = [], [], 2
    for row in rows:
        row_size = len(json.dumps(row).encode('utf-8'))
        if row_size + 2 > REPORT_BATCH_BYTES:
            raise ValueError('city validation report exceeds its request byte budget')
        added = row_size + (2 if batch else 0)
        if batch and (len(batch) >= REPORT_BATCH_ROWS or size + added > REPORT_BATCH_BYTES):
            batches.append(batch)
            batch, size, added = [], 2, row_size
        batch.append(row)
        size += added
    if batch:
        batches.append(batch)
    return sum(upsert_replace('derived_city_prediction_validation', batch,
               'city_key,checkpoint,scope', chunk=len(batch)) for batch in batches)


def capture_timing(p, city):
    """A reporting-only eligibility test using evidence saved with the call.

    The earliest saved peak estimate is conservative. Neither this estimate
    nor an eventual high hit rate proves the day's final maximum was unknown.
    Never rebuild an old forecast or use today's mutable peak table here.
    """
    zone = ZoneInfo(city['timezone'])
    context = p.get('predictive_context') or {}
    frozen = cal.timestamp(p['frozen_at']) if p.get('frozen_at') else None
    received = frozen or cal.timestamp(p['decided_at'])
    scheduled = dt.datetime.fromisoformat(p['local_decision_time']).replace(tzinfo=zone)
    start = dt.datetime.combine(dt.date.fromisoformat(p['target_date']), dt.time(), zone)
    peaks = []
    # The immutable peak-relative schedule also preserves its original anchor.
    offsets = {'prepeak_2h': -2, 'prepeak_1h': -1, 'postpeak_1h': 1}
    if p['checkpoint'] in offsets:
        peaks.append(('checkpoint schedule', scheduled-dt.timedelta(hours=offsets[p['checkpoint']])))
    for key in ('clim_peak_hour', 'fch_peak_local_hour'):
        value = context.get(key)
        try:
            hour = float(value) if value is not None else None
        except (TypeError, ValueError):
            hour = None
        if hour is not None and math.isfinite(hour) and 0 <= hour < 24:
            peaks.append((key, start+dt.timedelta(minutes=round(hour*60))))
    reference = context.get('timing_reference') or {}
    try:
        hour = float(reference['peak_hour_local'])
        fitted = cal.timestamp(reference['computed_at'])
        if (reference.get('source') == 'derived_weather_peak'
            and reference.get('city_key') == p['city_key']
            and reference.get('timezone') == city['timezone']
            and int(reference['month']) == start.month
            and fitted is not None and fitted <= received
            and math.isfinite(hour) and 0 <= hour < 24):
            peaks.append(('saved monthly peak', start+dt.timedelta(minutes=round(hour*60))))
    except (KeyError, TypeError, ValueError):
        pass
    cutoff = min((stamp for _, stamp in peaks), default=None)
    if not frozen:
        reason = 'missing_server_receipt'
    elif p['checkpoint'] == 'postpeak_1h':
        reason = 'explicit_postpeak_call'
    elif frozen < start:
        reason, cutoff = 'eligible', start
    elif cutoff is None:
        reason = 'missing_saved_peak'
    elif frozen >= cutoff:
        reason = 'at_or_after_expected_peak'
    else:
        reason = 'eligible'
    return dict(scheduled_local=scheduled.isoformat(), received_at=received.isoformat(),
        frozen_at=frozen.isoformat() if frozen else None,
        received_local=received.astimezone(zone).isoformat(),
        delay_minutes=round((received-scheduled.astimezone(dt.timezone.utc)).total_seconds()/60, 2),
        cutoff_local=cutoff.isoformat() if cutoff else None,
        minutes_before_expected_peak=round((cutoff-received).total_seconds()/60, 2) if cutoff else None,
        peak_sources=[name for name, _ in peaks], before_expected_peak=reason=='eligible', reason=reason)


def observed_peak_timing(row, city, review, now):
    """Retrospective timing only: never feeds calibration or model selection.

    The reviewed station high is not a venue finality signal. Missing reviews,
    mismatched winners and legacy calls remain unknown, not pre-peak successes.
    """
    if not row.get('settled'):
        return dict(relation='pending')
    receipt = (row.get('timing') or {}).get('frozen_at')
    if not row.get('verified') or not receipt:
        return dict(relation='unknown', reason='missing_server_receipt')
    try:
        if not review or not row.get('winner') or str(review.get('winner_band_id')) != row['winner']:
            return dict(relation='unknown', reason='missing_matching_review')
        computed = cal.timestamp(review.get('computed_at'))
        hour = float(review['peak_local_hour'])
        if (not computed or computed > now or not math.isfinite(hour) or not 0 <= hour < 24):
            return dict(relation='unknown', reason='missing_reviewed_peak')
        peak = dt.datetime.combine(dt.date.fromisoformat(row['target_date']), dt.time(),
                                   ZoneInfo(city['timezone']))+dt.timedelta(minutes=round(hour*60))
        frozen = cal.timestamp(receipt)
        return dict(relation='before' if frozen < peak else 'at_or_after',
                    peak_local=peak.isoformat(), source='derived_day_review',
                    minutes_before_peak=round((peak-frozen).total_seconds()/60, 2))
    except (KeyError, TypeError, ValueError):
        return dict(relation='unknown', reason='missing_reviewed_peak')


def observed_peak_report(rows, horizon):
    groups = {key: [r for r in rows if (r.get('observed_timing') or {}).get('relation', 'unknown') == key]
              for key in ('before', 'at_or_after', 'unknown', 'pending')}
    return dict(policy='reviewed-station-peak-v1', calls=len(rows),
                before=summarize(groups['before'], horizon),
                before_60m=summarize([r for r in groups['before']
                    if r['observed_timing']['minutes_before_peak'] >= 60], horizon),
                before_120m=summarize([r for r in groups['before']
                    if r['observed_timing']['minutes_before_peak'] >= 120], horizon),
                at_or_after=summarize(groups['at_or_after'], horizon),
                unknown_calls=len(groups['unknown']), pending_calls=len(groups['pending']))


def timing_report(rows, horizon):
    """Preserve every scheduled call and expose all strict exclusions."""
    eligible = [r for r in rows if (r.get('timing') or {}).get('before_expected_peak')]
    if horizon == 'overall':
        required = {r['checkpoint'] for r in rows}
        dates = [{r['target_date'] for r in eligible if r['checkpoint']==h} for h in required]
        common = set.intersection(*dates) if dates else set()
        eligible = [r for r in eligible if r['target_date'] in common]
    included = {id(r) for r in eligible}
    reasons = Counter()
    for r in rows:
        if id(r) not in included:
            reason = (r.get('timing') or {}).get('reason', 'missing_server_receipt')
            reasons['incomplete_strict_date' if reason=='eligible' else reason] += 1
    recent = sorted(rows, key=lambda r: (r['target_date'],
        (r.get('timing') or {}).get('received_at', ''), r['checkpoint']), reverse=True)[:14]
    return dict(policy='before-expected-peak-v1', calls=len(rows), eligible_calls=len(eligible),
        excluded_calls=len(rows)-len(eligible), exclusions=dict(reasons),
        strict=dict(summarize(eligible, horizon), recent_dates=daily_scores(eligible)),
        recent_calls=[dict(date=r['target_date'], checkpoint=r['checkpoint'],
            hit=r['metrics']['hit'] if r.get('settled') else None,
            observed_timing=r.get('observed_timing'),
            forecast_input=(r.get('paired_context') or {}).get('forecast_input'),
            **(r.get('timing') or dict(reason='missing_server_receipt', before_expected_peak=False)))
            for r in recent])


def wilson(hits, n):
    if not n:
        return None
    p, z = hits/n, 1.95996398454
    d = 1+z*z/n
    mid, delta = (p+z*z/(2*n))/d, z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,mid-delta),min(1,mid+delta)]


def saved_capture_plan(p, city):
    """Validate the immutable city-local schedule, never today's peak table.

    Fixed checkpoints still have exact clocks. A relative checkpoint retains
    its original peak anchor in its saved schedule. Receipt/window/outcome
    checks remain separate; legacy calls never gain a server receipt here.
    """
    try:
        day = dt.date.fromisoformat(p['target_date'])
        local = dt.datetime.fromisoformat(p['local_decision_time'])
        if local.tzinfo is not None or local.second or local.microsecond:
            return None
        fixed = decision_times(p['target_date'], city['timezone'], None)
        if p['checkpoint'] in fixed:
            return fixed[p['checkpoint']] if local == fixed[p['checkpoint']][0] else None
        offset = PEAK_OFFSETS_H.get(p['checkpoint'])
        if offset is None:
            return None
        peak = local-dt.timedelta(hours=offset)
        if peak.date() != day:
            return None
        return local, local.replace(tzinfo=ZoneInfo(city['timezone'])).astimezone(dt.timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def collect(checkpoints, outcomes, day_ahead, cities, peaks, now):
    """A later perfect replacement never wins selection over an original miss."""
    city_of = {c['city_key']:c for c in cities}
    facts = {f['checkpoint_id']:f for f in outcomes}
    first = {}
    for p in checkpoints:
        key = (p['city_key'],p['target_date'],p['checkpoint'])
        if key not in first or (p['decided_at'],p['checkpoint_id']) < (first[key]['decided_at'],first[key]['checkpoint_id']):
            first[key] = p
    out = []
    for p in first.values():
        c = city_of.get(p['city_key'])
        if c is None:
            continue
        day = dt.date.fromisoformat(p['target_date'])
        end = dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),ZoneInfo(c['timezone']))
        received = cal.timestamp(p.get('frozen_at') or p['decided_at'])
        # `peaks` is retained for callers' compatibility, not historical eligibility.
        plan = saved_capture_plan(p,c)
        if (not plan or not plan[1] <= received <= plan[1]+dt.timedelta(minutes=75)
            or received >= end or received > now or cal.timestamp(p['decided_at']) > received):
            continue
        f = facts.get(p['checkpoint_id'])
        settled = (f and f.get('settled_at') and received < cal.timestamp(f['settled_at']) <= now
                   and cal.timestamp(f['banked_at']) <= now and now >= end)
        winner = f['winner_band_id'] if settled else None
        probs = p['probs']
        top = min(probs,key=lambda k:(-float(probs[k]),k))
        r = dict(city_key=p['city_key'],target_date=p['target_date'],checkpoint=p['checkpoint'],
                 version=p.get('priced_from') or p['engine_version'],settled=bool(settled),
                 top_prob=float(probs[top]),verified=p.get('frozen_at') is not None,
                 metrics=cal.metrics(probs,winner) if settled else None,
                 baseline=None,raw=None,
                 market_metrics=({'hit': int(f['market_hit']), 'log_loss': f.get('log_loss_market'),
                                  'brier': f.get('brier_market')}
                                 if settled and f.get('market_complete') and f.get('market_hit') is not None else None),
                 raw_model_version=(p.get('predictive_context') or {}).get('raw_model_version') or p.get('priced_from'),
                 timing=capture_timing(p,c),
                 paired_context=p.get('predictive_context') or {},probs=probs,
                 frozen_at=p.get('frozen_at'),settled_at=f.get('settled_at') if f else None,
                 label_received_at=f.get('banked_at') if f else None,
                 raw_probs=p.get('raw_probs'),baseline_probs=p.get('baseline_probs'),winner=winner)
        if settled:
            if p['checkpoint'] in early.CHECKPOINTS and p.get('frozen_at'):
                r['early_comparison'] = early_forecast_audit.score(
                    (p.get('predictive_context') or {}).get('early_forecast_audit') or {}, probs, winner)
            for src,dest in [('baseline_probs','baseline'),('raw_probs','raw')]:
                q=p.get(src)
                if q and set(q)==set(probs):
                    r[dest]=cal.metrics(q,winner)
        out.append(r)
    for p in day_ahead:
        c=city_of.get(p['city_key'])
        if not c or not p.get('called_at') or p.get('hit') is None:
            continue
        start=dt.datetime.combine(dt.date.fromisoformat(p['for_date']),dt.time(),ZoneInfo(c['timezone']))
        if not cal.timestamp(p['called_at']) < start or now < start+dt.timedelta(days=1):
            continue
        prob=p.get('prob_on_winner')
        out.append(dict(city_key=p['city_key'],target_date=p['for_date'],checkpoint='day_ahead',
            version=p.get('engine_version') or 'day-ahead served',settled=True,verified=False,
            top_prob=float(p.get('predicted_pct') or 0)/100,
            metrics={'hit':int(p['hit']),'log_loss':-math.log(max(1e-6,float(prob))) if prob is not None else None,
                     'brier':None},baseline=None,raw=None))
    return out


def paired_diagnostic(rows):
    pairs=[r for r in rows if r.get('raw') and r.get('baseline') and r.get('verified')]
    result=dict(calls=len(pairs),dates=len({r['target_date'] for r in pairs}),
        raw_hits=sum(r['raw']['hit'] for r in pairs),baseline_hits=sum(r['baseline']['hit'] for r in pairs),
        raw_only_hits=0,baseline_only_hits=0,both_missed=0,raw_warmer_misses=0,raw_cooler_misses=0,
        interpretation='Matched original calls; input associations are not causal attribution.')
    ages=[]
    for r in pairs:
        a,b=r['raw']['hit'],r['baseline']['hit']
        result['raw_only_hits']+=int(a and not b)
        result['baseline_only_hits']+=int(b and not a)
        result['both_missed']+=int(not a and not b)
        ids=r.get('paired_context',{}).get('band_ids') or []
        q=r.get('raw_probs') or {}
        top=min(q,key=lambda k:(-float(q[k]),k)) if q else None
        if not a and top in ids and r.get('winner') in ids:
            result['raw_warmer_misses']+=int(ids.index(top)>ids.index(r['winner']))
            result['raw_cooler_misses']+=int(ids.index(top)<ids.index(r['winner']))
        age=r.get('paired_context',{}).get('obs_age_min')
        if age is not None and math.isfinite(float(age)):
            ages.append(float(age))
    result['mean_report_age_min']=statistics.fmean(ages) if ages else None
    return result


def average_metrics(rows, key='metrics'):
    pairs=[r[key] for r in rows if r.get(key)]
    return dict(n=len(pairs),**{k:statistics.fmean(p[k] for p in pairs if p[k] is not None)
                if any(p[k] is not None for p in pairs) else None for k in ('hit','log_loss','brier')})


def daily_scores(rows):
    """Show recent deterioration without claiming a target date is complete."""
    by_date = defaultdict(list)
    for r in rows:
        by_date[r['target_date']].append(r)
    result = []
    for day in sorted(by_date, reverse=True)[:7]:
        group = by_date[day]
        settled = [r for r in group if r['settled']]
        hits = sum(r['metrics']['hit'] for r in settled)
        result.append(dict(date=day, n=len(settled), hits=hits,
            hit_rate=hits/len(settled) if settled else None,
            pending=None if any(r['checkpoint']=='day_ahead' for r in group) else sum(not r['settled'] for r in group),
            claimed=statistics.fmean(r['top_prob'] for r in settled) if settled else None,
            metrics=average_metrics(settled),early_comparison=early_forecast_audit.summarize(settled)))
    return result


def summarize(rows, horizon):
    settled=[r for r in rows if r['settled']]
    hits=sum(r['metrics']['hit'] for r in settled)
    n=len(settled)
    interval=wilson(hits,n)
    if horizon=='overall' and settled:
        by_date=defaultdict(list)
        for r in settled:
            by_date[r['target_date']].append(r['metrics']['hit'])
        values=[statistics.fmean(v) for v in by_date.values()]
        rng=random.Random(20261017)
        boot=sorted(statistics.fmean(rng.choices(values,k=len(values))) for _ in range(1000))
        interval=[boot[25],boot[974]]
    bins=[]
    for lo in range(0,100,10):
        bucket=[r for r in settled if lo/100<=r['top_prob']<(lo+10)/100 or lo==90 and r['top_prob']==1]
        if bucket:
            bins.append(dict(lo=lo/100,n=len(bucket),stated=statistics.fmean(r['top_prob'] for r in bucket),
                             actual=statistics.fmean(r['metrics']['hit'] for r in bucket)))
    gap=sum(b['n']*abs(b['stated']-b['actual']) for b in bins)/n if n else None
    paired = [r for r in settled if r.get('market_metrics')]
    market_comparison = dict(n=len(paired), dates=len({r['target_date'] for r in paired}),
                             model=average_metrics(paired),
                             market=average_metrics(paired,'market_metrics'),
                             policy='same original call; saved complete market; no executable-return claim')
    return dict(n=n,hits=hits,hit_rate=hits/n if n else None,interval95=interval,
                dates=len({r['target_date'] for r in settled}),pending=None if horizon in ('day_ahead','overall') else sum(not r['settled'] for r in rows),
                verified=sum(r['verified'] for r in settled),legacy=sum(not r['verified'] for r in settled),
                metrics=average_metrics(settled),baseline=average_metrics(settled,'baseline'),
                raw=average_metrics(settled,'raw'),calibration_gap=gap,reliability=bins,
                versions=dict(Counter(r['version'] for r in settled)),market_comparison=market_comparison)


def diagnostic(rows,reviews):
    misses={r['target_date'] for r in rows if r['settled'] and not r['metrics']['hit']}
    checked=[r for r in reviews if r['target_date'] in misses]
    weather=Counter()
    shifts=[]
    errors=[]
    for r in checked:
        for s in r.get('surprises') or []:
            if abs(float(s.get('z') or 0))>=1:
                weather[s['what']]+=1
        if r.get('peak_local_hour') is not None and r.get('usual_peak_hour') is not None:
            shifts.append(abs(float(r['peak_local_hour'])-float(r['usual_peak_hour'])))
        if r.get('forecast_error_c') is not None:
            errors.append(float(r['forecast_error_c']))
    return dict(missed_dates=len(misses),reviewed_dates=len(checked),weather=dict(weather.most_common()),
                mean_peak_shift_h=statistics.fmean(shifts) if shifts else None,
                public_forecast_mae_c=statistics.fmean(abs(x) for x in errors) if errors else None,
                public_forecast_bias_c=statistics.fmean(errors) if errors else None,
                interpretation='Observed associations on reviewed misses; not causal attribution to WXPredict.')


def reports(rows,cities,reviews,states,now,model_version=None,selections=(),early_states=(),model_versions=None):
    period_start=(now.date()-dt.timedelta(days=29)).isoformat()
    period_end=now.date().isoformat()
    rows=[r for r in rows if period_start<=r['target_date']<=period_end]
    city_of = {c['city_key']: c for c in cities}
    review_of = {(r['city_key'], r['target_date']): r for r in reviews}
    rows = [dict(r, observed_timing=observed_peak_timing(r, city_of[r['city_key']],
                review_of.get((r['city_key'], r['target_date'])), now))
            for r in rows if r['city_key'] in city_of]
    result=[]
    for c in cities:
        city=c['city_key']
        active_version=(model_versions or {}).get(city,model_version)
        local_target=now.astimezone(ZoneInfo(c['timezone'])).date().isoformat()
        for scope in SCOPES:
            group=[]
            for r in rows:
                if r['city_key']!=city:
                    continue
                if scope=='served' or scope=='wxrd3' and r['version'].startswith('wxrd3:'):
                    group.append(r)
                elif (scope=='wxrd3_raw' and r.get('verified') and r.get('raw_probs') and r.get('baseline_probs')
                      and set(r['raw_probs'])==set(r['baseline_probs']) and str(r.get('raw_model_version') or '').startswith('wxrd3:')):
                    q=r['raw_probs'];top=min(q,key=lambda k:(-float(q[k]),k))
                    group.append(dict(r,metrics=r['raw'],top_prob=float(q[top]),version=r['raw_model_version']))
            for horizon in (*HORIZONS,'overall'):
                if horizon=='overall':
                    overall=OVERALL if scope=='served' else tuple(h for h in OVERALL if h in selector.SUPPORTED)
                    dates=[{r['target_date'] for r in group if r['checkpoint']==h and r['settled']} for h in overall]
                    common=set.intersection(*dates) if dates else set()
                    chosen=[r for r in group if r['checkpoint'] in overall and r['target_date'] in common]
                else:
                    chosen=[r for r in group if r['checkpoint']==horizon]
                report=summarize(chosen,horizon)
                report.update(city=city,display_name=c.get('display_name') or city,timezone=c['timezone'],
                    capture_timing=timing_report(chosen,horizon),capture_schedule_policy='saved-city-clock-v1',
                    observed_peak_timing=observed_peak_report(chosen,horizon),
                    scope=scope,horizon=horizon,period_start=period_start,period_end=period_end,
                    why=diagnostic(chosen,[r for r in reviews if r['city_key']==city]),
                    paired_audit=paired_diagnostic(chosen),
                    recent_dates=daily_scores(chosen),
                    early_comparison=early_forecast_audit.summarize(chosen),
                    early_adaptation=[dict(s,active=early.usable(s,local_target,s['model_version'],s['phase']),
                        effective_strength=s['strength'] if early.usable(s,local_target,s['model_version'],s['phase']) else early.PRIOR)
                        for s in states if s.get('policy') == early.POLICY and s['city_key'] == city
                        and scope == 'served' and horizon in (s['phase'],'overall')],
                    active_model_version=active_version,
                    learning=[dict(s,active=cal.usable(s,local_target,active_version),
                                   readiness=(cal.readiness(s['train_dates'],s['validation_dates'],
                                       s.get('priors') or cal.PRIORS,s.get('pool_rows'),s.get('pool_dates'))
                                       if 'train_dates' in s and 'validation_dates' in s else None),
                                   effective_params=s['params'] if cal.usable(s,local_target,active_version) else cal.IDENTITY)
                              for s in states if s['city_key']==city and s['model_version']==active_version
                              and horizon in (*selector.SUPPORTED, 'overall')],
                    early_learning=[s for s in early_states if s['city_key']==city]
                        if scope=='served' and horizon in ('day_ahead','d1_eve') else [])
                state=next((s for s in selections if s['city_key']==city and s['checkpoint']==horizon and s['model_version']==active_version),None)
                report['model_selection']=dict(state,
                    readiness=cal.readiness(state['train_dates'],state['validation_dates'],state.get('priors') or selector.PRIORS),
                    effective=selector.choose(state,city,horizon,active_version,local_target,now)) if state else None
                result.append(dict(city_key=city,checkpoint=horizon,scope=scope,as_of=now.isoformat(),report=report))
    return result


def learning_pairs(checkpoints, outcomes, cities):
    """The SQL paired view's first-original-call join, reusing loaded evidence."""
    first = {}
    for row in checkpoints:
        key = (row['city_key'], row['target_date'], row['checkpoint'])
        if key not in first or (row['decided_at'], row['checkpoint_id']) < (first[key]['decided_at'], first[key]['checkpoint_id']):
            first[key] = row
    facts = {r['checkpoint_id']: r for r in outcomes}
    city_of = {c['city_key']: c for c in cities}
    out = []
    for row in first.values():
        fact, city = facts.get(row['checkpoint_id']), city_of.get(row['city_key'])
        version = (row.get('predictive_context') or {}).get('raw_model_version') or row.get('priced_from')
        if not fact or not city or not str(version).startswith('wxrd3:') or not row.get('raw_probs') or not row.get('baseline_probs'):
            continue
        out.append(dict(row, timezone=city['timezone'], unit=city.get('unit'), model_version=version,
                        winning_band_id=fact.get('winner_band_id'), settled_at=fact.get('settled_at'),
                        label_received_at=fact.get('banked_at')))
    return out


def older_predictor_calls(root, as_of, report_first):
    """Read verified mirrored originals; chronology gates remain in each fitter."""
    import sys
    sys.path.insert(0, str(Path(root)/'tools'))
    from wxpredict import arrival_dataset as arrival
    midnight = dt.datetime.combine(as_of, dt.time(), dt.timezone.utc)
    start = midnight-dt.timedelta(days=cal.PRIORS['window_days']+2)
    first = str(as_of-dt.timedelta(days=cal.PRIORS['window_days']))
    calls = arrival.mirrored_rows('prediction_checkpoints', start.timestamp(), midnight.timestamp(), root=str(root),
        where=lambda r: first <= r['target_date'] < report_first)
    ids = {p['checkpoint_id'] for p in calls}
    facts = arrival.mirrored_rows('fact_checkpoint_outcome', start.timestamp(), midnight.timestamp(), root=str(root),
        where=lambda r: r['checkpoint_id'] in ids)
    for row in calls:
        for field in ('probs', 'predictive_context', 'raw_probs', 'baseline_probs'):
            row[field] = json.loads(row[field]) if isinstance(row.get(field), str) and row[field] else row.get(field) or None
        row['frozen_at'] = row.get('frozen_at') or None
    return calls, facts


def run(score_only=False, now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    first=(now.date()-dt.timedelta(days=30)).isoformat()
    cities=get_cities(require_coords=False,extra=('display_name',))
    p=rest_all('prediction_checkpoints',[('select','checkpoint_id,city_key,target_date,checkpoint,decided_at,local_decision_time,frozen_at,input_as_of,input_sha256,priced_from,engine_version,probs,raw_probs,baseline_probs,predictive_context'),('target_date','gte.'+first)],order='checkpoint_id.asc',page_size=1000)
    outcomes=rest_all('v_checkpoint_outcome',[('select','checkpoint_id,winner_band_id,settled_at,banked_at,market_complete,market_hit,log_loss_market,brier_market'),('target_date','gte.'+first)],order='checkpoint_id.asc',page_size=1000)
    da=rest_all('v_day_ahead_frozen_validation',[('select','*'),('for_date','gte.'+first)],order='city_key.asc,for_date.asc',page_size=1000)
    reviews=rest_all('derived_day_review',[('select','*'),('target_date','gte.'+first)],order='city_key.asc,target_date.asc',page_size=1000)
    states=[r['state'] for r in rest_all('derived_wx_city_calibration',[('select','state')],order='model_version.asc,city_key.asc,phase.asc',page_size=1000)]
    prior_reports=rest_all('derived_city_prediction_validation',[('select','report'),('scope','eq.served')],order='city_key.asc,checkpoint.asc',page_size=1000)
    selections=[r['report']['model_selection'] for r in prior_reports if r.get('report',{}).get('model_selection')]
    early_states=rest_all('derived_mos_coefficients',[
        ('select','city_key,lead_days,as_of,computed_at,version,calibration')],
        order='city_key.asc,lead_days.asc',page_size=1000)
    meta=json.loads((ROOT/'data/models/wxrd/wxrd-v3_remaining.json').read_text())
    # Same version identity as the serving/shadow readers; no ML dependency.
    import sys
    sys.path.insert(0,str(ROOT/'tools'))
    from wxpredict.wxrd_live import variant_version
    version=variant_version(meta,'wxrd3')
    import contract_inputs
    regime_prefix=contract_inputs.version(version,contract_inputs.CONTRACT)
    active_versions={c['city_key']:version for c in cities}
    for checkpoint in sorted(p,key=lambda r:(str(r.get('target_date','')),str(r.get('decided_at','')))):
        raw=(checkpoint.get('predictive_context') or {}).get('raw_model_version') or checkpoint.get('priced_from')
        if raw==version or str(raw).startswith(regime_prefix+':'):
            active_versions[checkpoint['city_key']]=raw
    version_cities={}
    for city,input_version in active_versions.items():
        version_cities.setdefault(input_version,[]).append(city)
    versions=tuple(sorted(version_cities))
    detail={'score_only':score_only,'active_cities':len(cities),'capture_schedule_policy':'saved-city-clock-v1'}
    if not score_only:
        older_p, older_outcomes = [], []
        try:
            older_p, older_outcomes = older_predictor_calls(ROOT, now.date(), first)
            detail['learning_history'] = 'verified mirrored originals plus current original calls'
        except (OSError, ValueError, KeyError) as exc:
            detail['learning_history'] = 'older mirror unavailable: '+type(exc).__name__
        learning = learning_pairs(older_p+p, older_outcomes+outcomes, cities)
        fitted=[]
        selected=[]
        for input_version in versions:
            fitted.extend(cal.fit(learning,version_cities[input_version],input_version,now.date(),states))
            selected.extend(selector.fit(learning,version_cities[input_version],input_version,now,selections))
        selections=selected
        detail['early_history'] = detail['learning_history']
        early_fitted=early.fit(collect(older_p+p,older_outcomes+outcomes,[],cities,{},now),now.date(),states)
        fitted.extend(early_fitted)
        path=ROOT/'data/eval/wxpredict/city_calibration'/f'{now.date()}.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(as_of=now.isoformat(),policy=cal.POLICY,states=fitted,model_selection=selections),sort_keys=True,indent=2)+'\n')
        upsert_replace('derived_wx_city_calibration',[dict(model_version=s['model_version'],city_key=s['city_key'],phase=s['phase'],as_of=s['as_of'],version=s['version'],state=s) for s in fitted], 'model_version,city_key,phase')
        states=[s for s in states if s['model_version'] not in versions and s.get('policy') != early.POLICY]+fitted
        detail.update(calibration_cells=len(fitted),decisions=dict(Counter(s['decision'] for s in fitted)),
                      eligible_learning_calls=sum(len(cal.eligible_rows(learning,now.date(),v)) for v in versions),
                      input_versions=list(versions))
        detail.update(selection_cells=len(selections),selection_decisions=dict(Counter(s['decision'] for s in selections)))
        detail.update(early_adaptation_cells=len(early_fitted),early_adaptation_decisions=dict(Counter(s['decision'] for s in early_fitted)))
    data=reports(collect(p,outcomes,da,cities,{},now),cities,reviews,states,now,version,selections,early_states,
                 model_versions=active_versions)
    from wxpredict import cycle_registry
    registry_state = cycle_registry.registry()
    shadow = registry_state.get('current')
    for row in data:
        row['report']['learning_pipeline'] = dict(raw_model_fit_through=meta.get('fit_last'),
            challenger_status=('forward validated research challenger' if registry_state.get('forward_validated')
                               else 'awaiting forward validation') if shadow else 'no challenger registered',
            challenger_available_at=(shadow or {}).get('available_at'),
            challenger_model_sha256=(shadow or {}).get('model_sha256'))
    n=publish_reports(data)
    log_run(JOB,'ok',n,detail)
    print(json.dumps(dict(rows=n,**detail)),flush=True)
    return detail


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--score-only',action='store_true')
    run(parser.parse_args().score_only)
