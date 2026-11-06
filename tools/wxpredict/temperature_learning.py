"""Bounded fitting from original private packets; no registration or serving."""
import collections
import time

from wxpredict import temperature_model as model, pre_day_pairs as pp


def prepare(items, cutoff, training_first=None):
    rows = []
    counts = collections.Counter()
    first = {}
    for item in items:
        c = item['call']
        key = (c['city_key'], c['target_date'], c['checkpoint'])
        if key not in first or (pp.timestamp(c['frozen_at']), c['call_id']) < (pp.timestamp(first[key]['call']['frozen_at']), first[key]['call']['call_id']):
            first[key] = item
    for item in first.values():
        c, s = item['call'], item.get('score')
        if training_first and c['target_date']<training_first:
            counts['warmup_before_predeclared_training_start']+=1
            continue
        if not s:
            counts['pending_venue_outcome'] += 1
            continue
        r = dict(c['record'], frozen_at=c['frozen_at'], label=dict(winner=s['winner_band_id'], settled_at=s['confirmed_at'], available_at=s['scored_at']))
        try:
            model.validate(r)
            if (not pp.timestamp(r['as_of']) <= pp.timestamp(r['frozen_at']) < pp.timestamp(s['confirmed_at']) <= pp.timestamp(s['scored_at']) < pp.timestamp(cutoff)
                    or s['winner_band_id'] not in r['band_ids']):
                raise ValueError('invalid original label chronology')
            hi=r['bounds'][r['band_ids'].index(s['winner_band_id'])][1]
            if r.get('floor_c') is not None and hi is not None and model.edge(hi,r['unit'])<=r['floor_c']:
                raise ValueError('winner contradicts received contractual floor')
        except (ValueError, KeyError, TypeError) as error:
            counts['invalid:'+str(error)[:100]] += 1
            continue
        rows.append(r)
    rows.sort(key=lambda r:(r['date'],r['city'],r['checkpoint']))
    dates = collections.defaultdict(set)
    for r in rows:dates[r['date']].add(r['city'])
    # Do not silently discard sparse dates to reach the training minimum.
    ready = len(rows) >= model.MIN_ROWS and len(dates) >= model.MIN_DATES and all(len(c) >= model.MIN_CITIES for c in dates.values())
    return rows, dict(state='eligible_to_fit' if ready else 'insufficient_legitimate_history',
                      input_calls=len(items), first_calls=len(first), eligible_rows=len(rows), dates=len(dates),
                      cities_per_date_min=min(map(len, dates.values())) if dates else 0,
                      first_date=min(dates) if dates else None, last_date=max(dates) if dates else None,
                      exclusions=dict(counts), evidence_cutoff=cutoff, changes_serving=False,
                      validated=False, reached_serving=False)


def run(rpc=None, *, plan=False):
    import common
    rpc = rpc or common.rpc
    started = time.monotonic()
    with common.request_deadline(started+100):
        inputs = rpc('temperature_learning_inputs', timeout=10)
        if inputs['state'] != 'ready':
            detail = dict(state=inputs['state'], changes_serving=False, validated=False, reached_serving=False)
            if not plan:rpc('temperature_fit_report', dict(p_artifact=None, p_detail=detail), timeout=3)
            return detail
        rows, detail = prepare(inputs['calls'], inputs['cutoff'], inputs.get('training_first'))
        if plan:
            return detail
        artifact = None
        if detail['state'] == 'eligible_to_fit':
            last=inputs.get('last_fit') or {}
            if last.get('evidence_sha256')==pp.digest(rows) and last.get('sources')==model.source_hashes():
                detail['state']='unchanged_eligible_evidence'
            else:
                artifact = model.fit(rows, inputs['cutoff'], budget_s=min(60, max(1, started+90-time.monotonic())))
                detail.update(state='fitted_unregistered_candidate', model_sha256=artifact['sha256'], training=artifact['training'])
        detail['elapsed_seconds'] = round(time.monotonic()-started, 3)
        rpc('temperature_fit_report', dict(p_artifact=artifact, p_detail=detail), timeout=5)
        return detail
