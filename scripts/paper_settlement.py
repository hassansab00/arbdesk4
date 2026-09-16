"""Paper payouts only after Gamma resolution and CLOB winner identity agree.

Mirrors the venue's binary payout; does not claim a weather-station parser has
been validated or change the legacy settlement_verified switch.
"""
import datetime as dt
import hashlib
import json
import time
from common import rest, rest_all, upsert, rpc, log_run


def array(value):
    parsed=json.loads(value) if isinstance(value,str) else value
    if not isinstance(parsed,list):
        raise ValueError('Expected a venue outcome array')
    return parsed


def verify(band,gamma,clob):
    if gamma.get('conditionId')!=band['condition_id'] or clob.get('condition_id')!=band['condition_id']:
        raise ValueError('Resolution condition identity mismatch')
    if gamma.get('closed') is not True or gamma.get('umaResolutionStatus')!='resolved' or clob.get('closed') is not True or clob.get('accepting_orders') is not False:
        return None
    tokens,labels,prices=array(gamma['clobTokenIds']),array(gamma['outcomes']),array(gamma['outcomePrices'])
    if not len(tokens)==len(labels)==len(prices)==2:
        raise ValueError('Binary resolution required')
    mapped={str(label).upper():(str(token),str(price)) for label,token,price in zip(labels,tokens,prices)}
    if set(mapped)!={'YES','NO'} or mapped['YES'][0]!=band['token_yes'] or mapped['NO'][0]!=band['token_no']:
        raise ValueError('Gamma outcome identity mismatch')
    if sorted(p for _,p in mapped.values())!=['0','1']:
        return None  # split/void payouts require a separate explicitly tested adapter
    ct=array(clob['tokens'])
    if len(ct)!=2 or {str(t.get('token_id')) for t in ct}!={band['token_yes'],band['token_no']}:
        raise ValueError('CLOB token identity mismatch')
    winners=[str(t['token_id']) for t in ct if t.get('winner') is True]
    gamma_winner=next(token for token,p in mapped.values() if p=='1')
    if winners!=[gamma_winner]:
        raise ValueError('Venue resolution sources disagree')
    return gamma_winner


# A market that closed an hour ago has not been through UMA yet. Days at or
# past this age are the ones where asking the venue can actually produce an
# answer, so the archive sweep starts there.
SETTLE_LAG_DAYS = 1


def _candidate_bands(position_band_ids, days_back, settled_conditions=frozenset()):
    """Open positions first, then the unsettled archive, newest answerable day first.

    Venue outcomes are valuable model evidence even when the paper desk held
    no position. Limiting collection to holdings left calibration permanently
    starved, so this bounded sweep fills the independent outcome archive too.

    TWO THINGS DECIDE WHETHER THAT SWEEP EVER ARRIVES ANYWHERE.

    ORDER. A run checks a few hundred bands before its time budget stops it,
    and one day of markets is 220 to 560 bands - so the walk covers roughly a
    day per run and the end it starts from decides what it ever sees. The
    original walked newest-first and spent every run on the day or two UMA had
    not ruled on yet; a first repair walked oldest-first instead. Both
    captured nothing, but NEITHER ordering was the reason - see the note on
    closed=true at the Gamma request in cycle(), which is why no ordering
    could have worked.

    Ordering still matters once that is fixed, and the right end is the
    NEWEST day old enough to have been ruled on: SETTLE_LAG_DAYS drops the
    too-new end, and the freshest remaining day is both answerable and the
    most valuable, being what advances fact_band_outcome's frontier and widens
    the backtest window. Each capture is remembered, so the walk marches
    backwards day by day through history.

    NOTHING HERE IS INFERRED ANY MORE. `skips` counts WHY each band produced
    no proof and over which days, because a bare zero was read wrong three
    times: as "nothing to collect" when the budget had run out, as "the venue
    forgot it" when the request was filtering it out, and as a retention wall
    that does not exist. The counters are what finally located the real
    fault.

    AUTHORITY. It also required markets.closed to be true. That column is
    maintained by the ingest side and goes stale on days it did not run: 225
    markets between 2026-05-20 and 09-14 - 2,431 bands - still read false long
    after the venue had settled them, and were unreachable forever. The venue
    is the authority on whether a market is resolved, and verify() already
    refuses anything Gamma and the CLOB do not both call closed and resolved.
    So age selects the candidates and the venue decides the outcome; the local
    flag is neither consulted nor trusted.

    KNOWN LIMIT: a band the venue will never resolve cleanly - a void or split
    payout - is re-asked on every run, because only a capture is remembered.
    That is bounded (a handful of bands against a per-run budget of a few
    hundred) and it degrades throughput rather than stalling it, but the real
    repair is an attempts table mirroring weather_resolution_attempts.
    """
    wanted = []
    if position_band_ids:
        for i in range(0, len(position_band_ids), 100):
            chunk = position_band_ids[i:i + 100]
            wanted += rest_all('bands', {
                'band_id': 'in.(' + ','.join(chunk) + ')',
                'select': 'band_id,market_id,condition_id,token_yes,token_no',
            }, order='band_id.asc')

    today = dt.date.today()
    since = (today - dt.timedelta(days=days_back)).isoformat()
    until = (today - dt.timedelta(days=SETTLE_LAG_DAYS)).isoformat()
    markets = rest_all('markets', [
        ('select', 'market_id,resolution_date'),
        ('resolution_date', 'gte.' + since),
        ('resolution_date', 'lte.' + until),
    ], order='resolution_date.desc,market_id.asc')
    day_of = {str(m['market_id']): m.get('resolution_date') for m in markets}
    market_ids = list(day_of)
    for i in range(0, len(market_ids), 100):
        chunk = market_ids[i:i + 100]
        for band in rest_all('bands', {
            'market_id': 'in.(' + ','.join(chunk) + ')',
            'select': 'band_id,market_id,condition_id,token_yes,token_no',
        }, order='band_id.asc'):
            band['resolution_date'] = day_of.get(str(band['market_id']))
            wanted.append(band)

    # Stable de-duplication; position bands were appended first and therefore
    # remain first when they also appear in the archive sweep. Bands whose
    # condition is already proved drop out HERE, before the budget is counted
    # against them - a settled day must not cost the run its remaining time.
    out, seen = [], set()
    for band in wanted:
        key = str(band['band_id'])
        if key in seen or str(band.get('condition_id')) in settled_conditions:
            continue
        seen.add(key)
        out.append(band)

    # ONE MARKET AT A TIME, because a HALF-PROVED MARKET IS WORTH NOTHING.
    #
    # v_venue_market_resolution calls a market 'confirmed' only when EVERY one
    # of its bands is confirmed and exactly one of them settled yes - which is
    # the right rule, since a market missing a band cannot say which band won.
    # fact_band_outcome, calibration and every scorecard hang off that.
    #
    # Bands were fetched 100 market ids at a time and ordered by band_id, so a
    # run's budget landed on ~50 bands spread across ~50 different markets. On
    # 16 Sep that showed as 45 to 64 proofs a day against 561 bands, and
    # confirmed_markets stuck at 0 for every day in the window: the sweep had
    # captured 156 proofs and completed nothing.
    #
    # Sorting by (day desc, market, band) spends the same budget finishing
    # markets. 11 bands proves one market and advances the outcome record;
    # 11 bands spread over 11 markets advances nothing.
    # Four stable passes, least significant first, which is the readable way
    # to say: open positions, then newest answerable day, then market by
    # market, then band by band inside each market.
    held = {str(x) for x in position_band_ids}
    out.sort(key=lambda b: str(b['band_id']))
    out.sort(key=lambda b: str(b.get('market_id') or ''))
    out.sort(key=lambda b: str(b.get('resolution_date') or ''), reverse=True)
    out.sort(key=lambda b: str(b['band_id']) not in held)
    return out


def cycle(budget_seconds=60, days_back=180, max_new_evidence=100):
    from paper_worker import public_json
    started,settled,checked=time.monotonic(),0,set()
    captured,failed,first_failure=0,0,None
    days_checked=[]
    positions=rest_all('paper_positions',{'shares':'gt.0','select':'band_id,account_id,side'},order='band_id,account_id,side')
    position_band_ids=list(dict.fromkeys(str(p['band_id']) for p in positions))
    existing={str(e['condition_id']) for e in rest_all(
        'paper_resolution_evidence',{'select':'condition_id,proof_id'},order='condition_id.asc,proof_id.asc')}

    candidates=_candidate_bands(position_band_ids,days_back,existing)
    unreached=0
    skips,spans={},{}

    def skip(reason,band):
        """Why this band produced no proof, and over which days.

        A zero with no reason attached has now been misread twice: once as
        "nothing to collect" when the budget had run out, and once as "the
        venue disagrees" when the venue had simply forgotten the market.
        """
        skips[reason]=skips.get(reason,0)+1
        day=band.get('resolution_date')
        if day:
            span=spans.setdefault(reason,[day,day])
            span[0],span[1]=min(span[0],day),max(span[1],day)
    for index,band in enumerate(candidates):
        if time.monotonic()-started>budget_seconds or captured>=max_new_evidence:
            unreached=len(candidates)-index
            break
        band_id=str(band['band_id'])
        if band_id in checked or str(band.get('condition_id')) in existing:
            continue
        checked.add(band_id)
        if band.get('resolution_date'): days_checked.append(band['resolution_date'])
        if not band.get('condition_id') or not band.get('token_yes') or not band.get('token_no'):
            skip('no_identity',band)
            continue
        try:
            gamma_url='https://gamma-api.polymarket.com/markets'
            # closed=true IS LOAD-BEARING, and its absence is why this job had
            # never once produced a proof.
            #
            # Gamma's /markets excludes closed markets BY DEFAULT. Asking for a
            # settled condition without it returns [] - not an error, not a
            # 404, an empty list - so every band looked like a market the venue
            # had no record of. The one thing this job exists to find is the
            # one thing the request was filtering out.
            #
            # Measured against the live API on a real settled band (tokyo
            # 24C, 2026-09-13):
            #
            #   ?condition_ids=X                -> []
            #   ?condition_ids=X&closed=true    -> the market, closed true,
            #                                      umaResolutionStatus resolved
            #   ?condition_ids=X&active=false   -> []
            #
            # verify() still re-checks closed and umaResolutionStatus itself,
            # so this widens what can be SEEN without widening what is trusted.
            gamma_params={'condition_ids':band['condition_id'],'closed':'true'}
            markets=public_json(gamma_url,gamma_params)
            gamma=next((m for m in markets if m.get('conditionId')==band['condition_id']),None)
            if not gamma:
                skip('venue_has_no_record',band)  # past Polymarket's retention
                continue
            if gamma.get('closed') is not True or gamma.get('umaResolutionStatus')!='resolved':
                skip('not_resolved_yet',band)
                continue
            clob_url='https://clob.polymarket.com/markets/'+band['condition_id']
            clob=public_json(clob_url,{})
            winner=verify(band,gamma,clob)
            if not winner:
                skip('void_or_split_payout',band)
                continue
            identity=hashlib.sha256(json.dumps({'gamma':gamma,'clob':clob},sort_keys=True,separators=(',',':')).encode()).hexdigest()
            upsert('paper_resolution_evidence',[{'proof_id':identity,'condition_id':band['condition_id'],
                'token_yes':band['token_yes'],'token_no':band['token_no'],'winning_token':winner,'gamma':gamma,'clob':clob,
                'source_urls':[gamma_url+'?condition_ids='+band['condition_id']+'&closed=true',clob_url]}],'proof_id')
            existing.add(str(band['condition_id']))
            captured+=1
            settled+=rpc('settle_paper_inventory',{'p_band':band['band_id'],'p_proof':identity})
        except Exception as e:
            failed+=1
            if first_failure is None:
                first_failure={'band_id':band_id,'error':str(e)[:300]}

    status='attention' if failed else 'ok'
    # `unreached` is the difference between "there was nothing to collect" and
    # "the budget ran out before I got to it". Reading 0 captured without it is
    # what let an unreachable archive look healthy for days.
    detail={'positions_settled':settled,'evidence_captured':captured,
            'bands_checked':len(checked),'failed':failed,'first_failure':first_failure,
            'candidates':len(candidates),'unreached':unreached,
            'skips':skips,'skip_day_spans':spans,
            'checked_span':[min(days_checked),max(days_checked)] if days_checked else None}
    log_run('paper_settlement',status,settled+captured,detail)
    return detail


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-seconds',type=int,default=60,
                        help='wall clock the venue sweep may spend (default 60)')
    parser.add_argument('--days-back',type=int,default=180)
    parser.add_argument('--max-evidence',type=int,default=100,
                        help='new proofs captured in one run (default 100)')
    options=parser.parse_args()
    print(cycle(budget_seconds=options.budget_seconds,days_back=options.days_back,
                max_new_evidence=options.max_evidence))
