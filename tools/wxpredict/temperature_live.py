"""New, independently timed temperature calls; never rewrites served calls."""
import collections
import datetime as dt
import hashlib
import time

from wxpredict import temperature_model as model, pre_day_hourly as hourly, pre_day_pairs as pp

BUDGET_SECONDS = 20.


def prepare(packet, artifact, registered_at, now):
    import contract_inputs
    import metar_contract
    import weather_outcomes
    market, city = packet['market'], packet['city']
    at = pp.timestamp(packet['as_of'])
    rules = packet.get('rules') or {}
    text = rules.get('rules_text') or ''
    if not rules.get('received_at') or pp.timestamp(rules['received_at']) > at:
        return None, 'missing_as_of_rules_receipt'
    annotated = contract_inputs.with_rules(dict(market), text)
    if annotated['observation_contract'] != model.CONTRACT or annotated['contract_station'] != city['icao']:
        return None, 'unknown_or_mismatched_observation_contract'
    spec = weather_outcomes.parse_rule_spec(text, market['unit'])
    if spec.unit != market['unit']:
        return None, 'dated_contract_unit_mismatch'
    station, _ = metar_contract.validate_window(spec.source_url, dt.date.fromisoformat(market['resolution_date']), city['timezone'])
    if station != city['icao']:
        return None, 'dated_contract_station_mismatch'
    weather = packet.get('weather')
    if not weather:
        return None, 'missing_received_weather'
    context, _ = hourly.History([weather]).select(city['city_key'], market['resolution_date'], city['timezone'], packet['as_of'])
    if not context:
        return None, 'incomplete_received_local_weather_day'
    location = context.get('request_location') or {}
    if (location.get('timezone') != city['timezone'] or any(location.get(k) is None or city.get(k) is None
            or abs(float(location[k])-float(city[k])) > .05 for k in ('latitude', 'longitude'))):
        return None, 'missing_or_mismatched_station_weather_location'
    definitions = sorted(packet['bands'], key=lambda b: -float('inf') if b['lo'] is None else float(b['lo']))
    r = dict(policy=model.POLICY, city=city['city_key'], date=market['resolution_date'],
             checkpoint=packet['checkpoint'], station=station, timezone=city['timezone'], unit=market['unit'],
             contract=model.CONTRACT, rules_sha256=hashlib.sha256(text.encode()).hexdigest(),
             rules_received_at=rules['received_at'], rules_receipt_id=rules.get('receipt_id'),
             market_id=market['market_id'], as_of=packet['as_of'], input_received_at=packet['as_of'],
             weather_capture_id=weather['capture_id'], hourly=context,
             forecast_issued_at=None, issuance_basis='provider_initialization_not_supplied; actual_receipts_retained',
             floor_c=None, observation=None, peak_reference=packet.get('peak_reference'),
             band_ids=[str(b['band_id']) for b in definitions],
             bounds=[[None if b[k] is None else float(b[k]) for k in ('lo', 'hi')] for b in definitions])
    if r['checkpoint'] == 'morning' and packet.get('observation'):
        obs = packet['observation']
        q = contract_inputs.qualifying(obs['payload'], obs['captured_at'], annotated, city, at)
        if q and 0 <= (at-pp.timestamp(q['latest_reading_at'])).total_seconds() <= 5400:
            r.update(floor_c=q['rmax_c'], observation=dict(q, capture_id=obs['capture_id']))
    r = model.seal(r)
    prediction = model.predict(artifact, r, registered_at=registered_at, now=now)
    maximum, _ = model.vector(r)
    references = packet.get('references') or {}
    raw = references.get('raw_forecast_c')
    width = references.get('raw_sigma_c')
    raw_normal = model.distribution(r, float(raw), float(width)) if raw is not None and width is not None and float(width) > 0 else None
    # The service-only research record is self-contained, so source-table
    # pruning cannot break its future learning input. Never mirror this private
    # feature packet or fitted artifact into the public repository.
    saved = dict(r)
    return dict(record=saved, prediction=prediction, references=references,
                hourly_reference=model.distribution(r, maximum, model.PRIOR_SIGMA), raw_normal_reference=raw_normal), None


def run(rpc=None, max_packets=64):
    import common
    rpc = rpc or common.rpc
    if not 1 <= max_packets <= 64:
        raise ValueError('bounded temperature capture count required')
    started = time.monotonic()
    skipped = collections.Counter()
    rows = []
    with common.request_deadline(started+BUDGET_SECONDS):
        inputs = rpc('temperature_inputs', timeout=8)
        if inputs['state'] != 'registered':
            return dict(state=inputs['state'], new=0, changes_serving=False)
        model.model(inputs['artifact'])
        skipped['bounded_verification'] += max(0,len(inputs['packets'])-max_packets)
        for packet in inputs['packets'][:max_packets]:
            if time.monotonic() > started+BUDGET_SECONDS-6:
                skipped['deadline'] += 1
                continue
            try:
                row, reason = prepare(packet, inputs['artifact'], inputs['registered_at'], dt.datetime.now(dt.timezone.utc).isoformat())
            except (KeyError, ValueError, TypeError) as error:
                skipped['invalid_input:'+type(error).__name__+':'+str(error)[:80]] += 1
                continue
            if row:
                rows.append(row)
            else:
                skipped[reason] += 1
        frozen = rpc('temperature_freeze', dict(p_model=inputs['artifact']['sha256'], p_rows=rows), timeout=5) if rows else {'new': 0}
        scored = rpc('temperature_score', timeout=4)
        result = dict(state=inputs['artifact']['state'], due=len(inputs['packets']), new=frozen['new'], scored=scored,
                      skipped=dict(skipped), model_sha256=inputs['artifact']['sha256'],
                      elapsed_seconds=round(time.monotonic()-started, 3), changes_serving=False)
        rpc('temperature_heartbeat', dict(p_detail=result), timeout=2)
        return result
