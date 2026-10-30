"""Follow original frozen city calls on a separate, fixed-stake paper ledger.

No fitting, mutable probabilities, settlement reads or exchange orders. The
database repeats the source, timing, scope and daily-allocation checks.
"""
import argparse
import datetime as dt
import math
import time
from collections import Counter
from zoneinfo import ZoneInfo

from common import rest, rest_all, rpc, log_run, request_deadline, get_cities
from paper_execution import instant
from paper_plans import prepare
from paper_worker import capture_book, fill_order
from predictive_validation import capture_timing

STRATEGY = 's14_day_max_prediction'
JOB = 'P7.11_day_max_prediction'
POLICY = 'day-max-frozen-fixed-stake-v1'
STAKE = 10
MAX_AGE_MIN = 90
MAX_SECONDS = 45
CHECKPOINTS = ('day_ahead', 'd1_eve', 'morning', 'noon', 'prepeak_2h', 'prepeak_1h')


def choices(config):
    rows = config.get('cities') or []
    if config.get('policy') != POLICY or not rows:
        raise ValueError('Day Max selection is not configured')
    out = {}
    for row in rows:
        city = row['city_key']
        if city in out or row.get('checkpoint') not in CHECKPOINTS or row.get('stake_usd') != STAKE:
            raise ValueError('Invalid city, checkpoint or fixed stake configuration')
        out[city] = row
    instant(config['activated_at'])
    return out


def original_calls(rows, config, cities, now):
    selected = choices(config)
    first, out, skipped = {}, [], Counter()
    for row in sorted(rows, key=lambda p: (p['decided_at'], p['checkpoint_id'])):
        first.setdefault((row['city_key'], row['target_date'], row['checkpoint']), row)
    for p in first.values():
        city = p['city_key']
        if city not in selected or p['checkpoint'] != selected[city]['checkpoint']:
            continue
        try:
            frozen = instant(p['frozen_at'])
            if not p.get('inputs_ok') or not instant(config.get('selection_changed_at') or config['activated_at']) <= frozen <= now:
                raise ValueError('unavailable_or_before_activation')
            if now-frozen > dt.timedelta(minutes=MAX_AGE_MIN):
                raise ValueError('stale_frozen_call')
            timing = capture_timing(p, cities[city])
            cutoff = instant(timing['cutoff_local']) if timing.get('cutoff_local') else None
            if not timing['before_expected_peak'] or cutoff is None or now >= cutoff:
                raise ValueError('at_or_after_saved_peak')
            scheduled = instant(timing['scheduled_local'])
            if not scheduled <= instant(p['decided_at']) <= frozen <= scheduled+dt.timedelta(minutes=75):
                raise ValueError('late_checkpoint_capture')
            if p['checkpoint']=='day_ahead':
                ctx=p.get('predictive_context') or {}
                source=instant(ctx['source_computed_at'])
                boundary=instant(ctx['cutoff_at'])
                run=instant(p.get('input_as_of') or p['forecast_issued_at'])
                if (ctx.get('source_kind')!='day_max_day_ahead_calls' or boundary!=scheduled
                    or not boundary-dt.timedelta(hours=8)<=source<boundary
                    or run>source or p.get('running_max_c') is not None):
                    raise ValueError('invalid_original_day_ahead_source')
            probs = p.get('probs') or {}
            values = [float(x) for x in probs.values()]
            if not values or not all(math.isfinite(x) and 0 <= x <= 1 for x in values):
                raise ValueError('invalid_probability_ladder')
            if not .999 <= sum(values) <= 1.001:
                raise ValueError('invalid_probability_mass')
            if float(probs[p['top_band_id']]) != max(values):
                raise ValueError('top_bucket_not_modal')
            out.append(p)
        except (KeyError, ValueError, TypeError) as exc:
            skipped[str(exc)[:160]] += 1
    return out, dict(skipped)


def signal_for(p, band, market, now):
    if (str(band['band_id']) != str(p['top_band_id']) or band['market_id'] != market['market_id']
        or market['city_key'] != p['city_key'] or market['resolution_date'] != p['target_date']
        or market.get('closed') is not False):
        raise ValueError('Frozen bucket market identity mismatch or closed')
    context = p.get('predictive_context') or {}
    run_at = p.get('input_as_of') or p.get('forecast_issued_at') or (context.get('forecast_input') or {}).get('run_at')
    if not run_at or instant(run_at) > instant(p['frozen_at']):
        raise ValueError('Original forecast input timestamp unavailable or future')
    prob = p['probs'][p['top_band_id']]
    bid = str(band['band_id'])
    return dict(signal_id=p['checkpoint_id'], strategy_id=STRATEGY, band_id=bid, side='YES', payload=dict(
        decision_at=now.isoformat(), band_ids=[bid], decision_inputs={bid: dict(unit=market['unit'],
            model_prob_yes=prob, decision_evidence=dict(band=band, market=market,
                forecast=dict(for_date=p['target_date'], run_at=run_at)))}))


def run(dry_run=False):
    started = time.monotonic()
    detail = dict(policy=POLICY, candidates=0, allocated=0, fills={}, skipped={})
    with request_deadline(started+MAX_SECONDS):
        cfgs = rest('strategies', {'strategy_id': 'eq.'+STRATEGY, 'select': 'enabled,extra'})
        if not cfgs or not cfgs[0]['enabled']:
            detail['waiting'] = 'strategy_disabled'
        else:
            config = (cfgs[0].get('extra') or {}).get('day_max') or {}
            selected = choices(config)
            now = dt.datetime.now(dt.timezone.utc)
            city_rows = [c for c in get_cities(require_coords=False) if c['city_key'] in selected]
            cities = {c['city_key']:c for c in city_rows}
            detail['day_ahead_capture'] = []
            if not dry_run:
                for city,choice in selected.items():
                    if choice['checkpoint']!='day_ahead':
                        continue
                    local=now.astimezone(ZoneInfo(cities[city]['timezone']))
                    if local.hour*60+local.minute>75:
                        continue
                    try:
                        receipt=rpc('capture_day_max_day_ahead',{'p_city':city,'p_target':local.date().isoformat()})
                        detail['day_ahead_capture'].append(dict(city=city,**receipt))
                    except Exception as exc:
                        detail['day_ahead_capture'].append(dict(city=city,error=str(exc)[:200]))
            rows = rest_all('v_day_max_original_calls', {'select':'*',
                'checkpoint':'in.('+','.join(sorted({c['checkpoint'] for c in selected.values()}))+')',
                'target_date':'gte.'+(now.date()-dt.timedelta(days=1)).isoformat(),
                'city_key':'in.('+','.join(selected)+')'}, order='checkpoint_id')
            # Capture RPCs stamp receipts on the server after the first clock read.
            # Validate against the clock after reading them, so a new receipt can
            # execute this run rather than appearing to be from the future.
            now = dt.datetime.now(dt.timezone.utc)
            candidates, detail['skipped'] = original_calls(rows, config, cities, now)
            allocated = rest('day_max_prediction_entries', {'select':'city_key,target_date',
                'target_date':'gte.'+(now.date()-dt.timedelta(days=1)).isoformat(), 'limit':'100'})
            done = {(p['city_key'],p['target_date']) for p in allocated}
            candidates = [p for p in candidates if (p['city_key'],p['target_date']) not in done]
            detail['candidates'] = len(candidates)
            accounts = rest('paper_accounts', {'strategy_id':'eq.'+STRATEGY, 'kind':'eq.shadow',
                'status':'eq.active','select':'*','limit':'1'})
            if not accounts or accounts[0]['mode'] != 'automatic' or accounts[0]['entries_paused']:
                detail['waiting'] = 'own_paper_ledger_paused_or_unavailable'
            elif not dry_run:
                for p in candidates:
                    if time.monotonic()+5 >= started+MAX_SECONDS:
                        detail['waiting'] = 'bounded_run_deferred_remaining_candidates'
                        break
                    try:
                        band = rest('v_canonical_bands', {'band_id':'eq.'+p['top_band_id'],'select':'*'})[0]
                        market = rest('v_canonical_markets', {'market_id':'eq.'+band['market_id'],'select':'*'})[0]
                        # Refresh available cash after each allocation; the DB locks it again.
                        account = rest('paper_accounts', {'account_id':'eq.'+accounts[0]['account_id'],'select':'*'})[0]
                        signal = signal_for(p, band, market, dt.datetime.now(dt.timezone.utc))
                        legs, evidence = prepare(account, signal, capture_book, fixed_stake_usd=STAKE)
                        result = rpc('publish_day_max_prediction', {'p_checkpoint':p['checkpoint_id'],
                            'p_legs':legs,'p_evidence':evidence})
                        if result.get('allocated'):
                            detail['allocated'] += 1
                        order = rpc('claim_account_order', {'p_account':account['account_id']})
                        if order:
                            status = fill_order(order)['status']
                            detail['fills'][status] = detail['fills'].get(status,0)+1
                    except Exception as exc:  # report a refusal without skipping other cities
                        reason = f"{p['city_key']}: {exc}"[:300]
                        detail['skipped'][reason] = detail['skipped'].get(reason,0)+1
    detail['seconds'] = round(time.monotonic()-started,2)
    detail['dry_run'] = dry_run
    if not dry_run:
        attention=any(r.get('error') for r in detail.get('day_ahead_capture',[])) or detail.get('waiting')=='bounded_run_deferred_remaining_candidates'
        log_run(JOB, 'attention' if attention else 'ok', detail['allocated'], detail)
    return detail


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(run(args.dry_run))
