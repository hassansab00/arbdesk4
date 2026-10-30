"""Bounded city/horizon correction strength, learned from frozen candidate ladders.

All eleven candidates are priced before receipt, with the same width, floor
and probability calibration. Labels select a strength only on older dates;
later dates must beat both the declared prior and the incumbent. No historical
forecast is reconstructed, no market price is an input, and no frozen call is
updated. Compact state shares the existing service-only calibration store.
"""
import datetime as dt
import hashlib
import json
import math
from collections import defaultdict
from zoneinfo import ZoneInfo

import wx_city_calibration as cal

POLICY = 'early-city-strength-v1'
PREFIX = POLICY + ':'
CHECKPOINTS = ('d1_eve', 'morning')
GRID = tuple(round(i/10, 1) for i in range(11))
PRIOR = 1.0
PRIORS = dict(window_days=90, validation_days=14, min_train_dates=20,
              min_validation_dates=10, shrink_dates=30, max_step=.1,
              strength_bounds=[0., 1.], max_age_days=3)
_contexts, _states = {}, {}


def older_mirrored_calls(root, as_of, report_first):
    """Extend learning with verified local exports, without extra DB scans.

    The live 30-day reads remain the reporting source. Only older target
    dates come from the mirror; its original receipt and banked label remain
    mandatory. Missing/incomplete exports cannot approve a parameter.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
    from wxpredict import arrival_dataset as arrival
    midnight = dt.datetime.combine(as_of, dt.time(), dt.timezone.utc)
    start = midnight-dt.timedelta(days=PRIORS['window_days']+2)
    first = str(as_of-dt.timedelta(days=PRIORS['window_days']))
    where = lambda r: first <= r['target_date'] < report_first and r.get('checkpoint') in CHECKPOINTS
    calls = arrival.mirrored_rows('prediction_checkpoints',start.timestamp(),midnight.timestamp(),root=str(root),where=where)
    # Facts have no checkpoint name, so retain only the selected original IDs.
    ids = {p['checkpoint_id'] for p in calls}
    facts = arrival.mirrored_rows('fact_checkpoint_outcome',start.timestamp(),midnight.timestamp(),root=str(root),
                                 where=lambda r:r['checkpoint_id'] in ids)
    for p in calls:
        for k in ('probs','predictive_context','raw_probs','baseline_probs'):
            p[k] = json.loads(p[k]) if p.get(k) else None
        p['frozen_at'] = p.get('frozen_at') or None
    facts = [{k:r.get(k) or None for k in ('checkpoint_id','winner_band_id','settled_at','banked_at')} for r in facts]
    return calls, facts


def family(contract, station, unit, recipe):
    if contract not in ('awc-routine-v1', 'legacy') or not station or unit not in ('C','F') or not recipe:
        return None
    digest = hashlib.sha256(json.dumps([contract, station, unit, recipe], sort_keys=True).encode()).hexdigest()[:20]
    return PREFIX + digest


def prepare(markets, cities, now=None, read=None):
    """One bounded compact-state read before workers; city clocks scope usage."""
    global _contexts, _states
    now = now or dt.datetime.now(dt.timezone.utc)
    from common import rest_all
    read = read or rest_all
    _contexts, _states = {}, {}
    city_of = {c['city_key']: c for c in cities}
    for m in markets:
        c = city_of.get(m['city_key'])
        if not c:
            continue
        local = now.astimezone(ZoneInfo(c['timezone']))
        day = dt.date.fromisoformat(str(m['resolution_date']))
        for checkpoint, offset, hour in [('d1_eve', -1, 18), ('morning', 0, 9)]:
            scheduled = dt.datetime.combine(day+dt.timedelta(days=offset), dt.time(hour), ZoneInfo(c['timezone']))
            if dt.timedelta(0) <= local-scheduled <= dt.timedelta(minutes=75):
                _contexts[(m['city_key'], str(day))] = dict(checkpoint=checkpoint,
                    contract=m.get('observation_contract'), station=m.get('contract_station') or c.get('icao'),
                    unit=m.get('unit') or c.get('unit'), timezone=c['timezone'], scheduled_at=scheduled.isoformat(),
                    rules_sha256=m.get('rules_sha256'),rules_observed_at=now.isoformat(),rules_received_at=m.get('rules_received_at'))
    if not _contexts:
        return
    try:
        cells = read('derived_wx_city_calibration', [('select','state'),('model_version','like.'+PREFIX+'*'),
                     ('computed_at','lte.'+now.isoformat())], order='model_version.asc,city_key.asc,phase.asc', page_size=1000)
        _states = {(s['state']['city_key'], s['state']['phase'], s['state']['model_version']): s['state'] for s in cells}
    except Exception:
        _states = {}  # A failed learning-state read cannot block or alter pricing.


def usable(state, target, model_version, checkpoint):
    try:
        day = dt.date.fromisoformat(str(target))
        return (state['policy'] == POLICY and state['model_version'] == model_version and state['phase'] == checkpoint
                and state['decision'] in ('applied','revalidated') and state['validation']['passed']
                and state['train_dates'] >= PRIORS['min_train_dates']
                and state['validation_dates'] >= PRIORS['min_validation_dates']
                and 0 <= (day-dt.date.fromisoformat(state['as_of'])).days <= PRIORS['max_age_days']
                and 0 < (day-dt.date.fromisoformat(state['validated_through'])).days <= PRIORS['max_age_days']
                and float(state['strength']) in GRID and bool(state['version']))
    except (KeyError, TypeError, ValueError):
        return False


def distribution(pe, centre, sigma, unit, bands, floor, q_down, q_up):
    raw = {str(k): round(pe.clamp_prob(p), 6) for k,p in
           pe.compute_band_probabilities(centre, sigma, unit, bands, floor_c=floor, q_down=q_down, q_up=q_up)}
    q = {str(k): round(pe.clamp_prob(pe._calibrate(p)), 6) for k,p in
         pe.compute_band_probabilities(centre, sigma, unit, bands, floor_c=floor, q_down=q_down, q_up=q_up)}
    total = sum(q.values())
    return raw, {k: round(pe.clamp_prob(p/total), 6) for k,p in q.items()}


def price(pe, city, target, rows, bands, unit, raw_c, corrected_c, sigma, floor, q_down, q_up, recipe):
    """Return a pre-receipt packet and a replacement only with fresh validation."""
    context = _contexts.get((city, str(target)))
    if not context or context['unit'] != unit:
        return None, None
    model_version = family(context['contract'], context['station'], unit, recipe)
    if not model_version or not all(math.isfinite(float(x)) for x in (raw_c, corrected_c, sigma)) or sigma <= 0:
        return None, None
    candidates, raw_candidates = {}, {}
    for strength in GRID:
        raw, q = distribution(pe, raw_c+strength*(corrected_c-raw_c), sigma, unit, bands, floor, q_down, q_up)
        candidates[str(strength)], raw_candidates[str(strength)] = q, raw
    base = {str(r['band_id']): r['calibrated_prob'] for r in rows}
    if set(base) != set(candidates['1.0']) or any(abs(base[k]-candidates['1.0'][k]) > 1e-4 for k in base):
        return None, None
    # Preserve the precise served baseline at the prior, including normal rounding.
    candidates['1.0'] = dict(base)
    state = _states.get((city, context['checkpoint'], model_version), {})
    strength = float(state['strength']) if usable(state, target, model_version, context['checkpoint']) else PRIOR
    served = candidates[str(strength)]
    packet = dict(policy=POLICY, model_version=model_version, **context, recipe=recipe,
                  raw_centre_c=raw_c, corrected_centre_c=corrected_c, sigma_c=sigma,
                  observed_floor_c=floor, q_down=q_down, q_up=q_up,
                  input_forecast_run=rows[0].get('input_forecast_run'),
                  candidates=candidates, raw_centre_probs=candidates['0.0'], corrected_probs=base,
                  served_probs=served, strength=strength,
                  version=state['version'] if strength != PRIOR else POLICY+':prior',
                  fitted_as_of=state.get('as_of') if strength != PRIOR else None)
    replacement = None
    if strength != PRIOR:
        replacement = [dict(r, raw_prob=raw_candidates[str(strength)][str(r['band_id'])],
                            calibrated_prob=served[str(r['band_id'])],
                            centre_c=round(raw_c+strength*(corrected_c-raw_c),4),
                            bias_applied_c=round(raw_c-(raw_c+strength*(corrected_c-raw_c)),4)) for r in rows]
    return packet, replacement


def valid_packet(packet, served):
    try:
        if (packet['policy'] != POLICY or packet['served_probs'] != served
                or packet['model_version'] != family(packet['contract'],packet['station'],packet['unit'],packet['recipe'])
                or packet['checkpoint'] not in CHECKPOINTS or set(packet['candidates']) != set(map(str,GRID))
                or packet['corrected_probs'] != packet['candidates']['1.0']
                or packet['raw_centre_probs'] != packet['candidates']['0.0']
                or packet['candidates'][str(float(packet['strength']))] != served):
            return False
        for q in packet['candidates'].values():
            cal.transform(q, list(served), cal.IDENTITY)
        return True
    except (KeyError, TypeError, ValueError):
        return False


def eligible(rows, as_of):
    cutoff = dt.datetime.combine(as_of, dt.time(), dt.timezone.utc)
    out = []
    first = {}
    for row in rows:
        key = (row['city_key'],row['target_date'],row['checkpoint'])
        if key not in first or str(row.get('frozen_at') or '') < str(first[key].get('frozen_at') or ''):
            first[key] = row
    for row in first.values():
        packet = (row.get('paired_context') or {}).get('early_forecast_audit') or {}
        try:
            day = dt.date.fromisoformat(row['target_date'])
            if (not row['settled'] or not row['verified'] or row['checkpoint'] not in CHECKPOINTS
                    or not as_of-dt.timedelta(days=PRIORS['window_days']) <= day < as_of
                    or not valid_packet(packet, row['probs']) or row['checkpoint'] != packet['checkpoint']):
                continue
            timing = row.get('timing') or {}
            if row['checkpoint'] == 'morning' and (not timing.get('before_expected_peak')
                    or float(timing.get('minutes_before_expected_peak') or 0) < 60):
                continue
            frozen, issued = cal.timestamp(row['frozen_at']), cal.timestamp(packet['input_forecast_run'])
            if any(cal.timestamp(packet[k]) > frozen for k in ('rules_observed_at','rules_received_at') if packet.get(k)):
                continue
            end = dt.datetime.combine(day+dt.timedelta(days=1), dt.time(), ZoneInfo(packet['timezone']))
            scheduled = cal.timestamp(packet['scheduled_at'])
            if (not issued <= frozen < end <= cutoff
                    or not scheduled <= frozen <= scheduled+dt.timedelta(minutes=75)
                    or not frozen < cal.timestamp(row['settled_at']) <= cal.timestamp(row['label_received_at']) < cutoff
                    or row['winner'] not in row['probs']):
                continue
            if packet['fitted_as_of'] and dt.date.fromisoformat(packet['fitted_as_of']) > day:
                continue
            out.append(dict(row, packet=packet))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _validation(rows, candidate, current):
    # Same date-cluster bootstrap and hit/Brier safeguards as city calibration.
    daily = []
    for strength in (PRIOR, current, candidate):
        daily.append([cal.metrics(r['packet']['candidates'][str(strength)], r['winner']) for r in rows])
    import random
    if not rows:
        return dict(dates=0, passed=False)
    by_date = defaultdict(list)
    for i,r in enumerate(rows):
        by_date[r['target_date']].append([daily[j][i] for j in range(3)])
    means = [[{k:sum(v[j][k] for v in group)/len(group) for k in ('hit','log_loss','brier')}
              for j in range(3)] for group in by_date.values()]
    avg = [{k:sum(v[j][k] for v in means)/len(means) for k in ('hit','log_loss','brier')} for j in range(3)]
    def lower(j):
        values = [v[j]['log_loss']-v[2]['log_loss'] for v in means]
        rng = random.Random(cal.PRIORS['bootstrap_seed'])
        boots = sorted(sum(rng.choices(values,k=len(values)))/len(values) for _ in range(cal.PRIORS['bootstrap_samples']))
        return boots[int(.05*len(boots))]
    low = [lower(j) for j in (0,1)]
    return dict(dates=len(means), prior=avg[0], current=avg[1], candidate=avg[2],
                prior_gain_lower90=low[0], gain_lower90=low[1],
                passed=all(x > 0 for x in low) and all(avg[2]['hit'] >= avg[j]['hit']-1e-12
                    and avg[2]['brier'] <= avg[j]['brier']+1e-12 for j in (0,1)))


def fit(rows, as_of, previous=()):
    as_of = dt.date.fromisoformat(str(as_of))
    evidence = eligible(rows, as_of)
    groups = defaultdict(list)
    for r in evidence:
        groups[(r['city_key'],r['checkpoint'],r['packet']['model_version'])].append(r)
    old_of = {(s['city_key'],s['phase'],s['model_version']):s for s in previous if s.get('policy') == POLICY}
    states = []
    for key in sorted(set(groups)|set(old_of)):
        city, checkpoint, model_version = key
        old = old_of.get(key, {})
        if (str(old.get('as_of') or '') > str(as_of) or old.get('strength',PRIOR) not in GRID
                or old.get('anchor',PRIOR) not in GRID):
            old = {}
        current = float(old.get('strength',PRIOR))
        anchor = old.get('anchor',current) if old.get('as_of') == str(as_of) else current
        split = str(as_of-dt.timedelta(days=PRIORS['validation_days']))
        train = [r for r in groups[key] if r['target_date'] < split]
        val = [r for r in groups[key] if r['target_date'] >= split]
        nt,nv = len({r['target_date'] for r in train}),len({r['target_date'] for r in val})
        digest = hashlib.sha256(json.dumps([(r['target_date'],r['packet']['candidates'],r['winner'])
                               for r in groups[key]],sort_keys=True).encode()).hexdigest()
        state = dict(policy=POLICY,model_version=model_version,city_key=city,phase=checkpoint,as_of=str(as_of),
                     priors=PRIORS,anchor=anchor,strength=PRIOR,version=POLICY+':prior',decision='held',
                     reason='insufficient independent older training and later validation dates',validated_through=None,
                     train_dates=nt,validation_dates=nv,input_digest=digest,
                     readiness=cal.readiness(nt,nv,PRIORS))
        if nt >= PRIORS['min_train_dates'] and nv >= PRIORS['min_validation_dates']:
            # Seasonal local loss, with prior pseudodates. No cross-city labels
            # substitute for a city's own independent minimum sample.
            def objective(b):
                weights = [cal.seasonal_weight(r['target_date'],str(as_of)) for r in train]
                loss = sum(w*cal.metrics(r['packet']['candidates'][str(b)],r['winner'])['log_loss'] for r,w in zip(train,weights))
                return (loss+PRIORS['shrink_dates']*(1-b)**2)/sum(weights)
            grid = [b for b in GRID if abs(b-float(anchor)) <= PRIORS['max_step']+1e-10]
            candidate = min(grid,key=lambda b:(objective(b),-b))
            result = _validation(val,candidate,current)
            state['validation'] = result
            if result['passed']:
                state.update(strength=candidate,decision='applied',reason='bounded strength passed later unseen dates',
                             validated_through=max(r['target_date'] for r in val))
                state['version'] = PREFIX+str(as_of)+':'+hashlib.sha256(json.dumps([key,candidate,digest]).encode()).hexdigest()[:12]
            elif current != PRIOR:
                keep = _validation(val,current,PRIOR)
                if keep['passed']:
                    state.update(strength=current,decision='revalidated',reason='current strength passed fresh later dates',
                                 validation=keep,version=old['version'],validated_through=max(r['target_date'] for r in val))
                else:
                    state.update(decision='reverted',reason='current strength failed fresh validation; baseline restored')
            else:
                state['reason'] = 'candidate failed unseen log-loss, Brier or hit-rate gates'
        states.append(state)
    return states
