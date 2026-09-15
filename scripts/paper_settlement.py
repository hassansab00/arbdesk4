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
    """Open positions first, then the unsettled archive OLDEST day first.

    Venue outcomes are valuable model evidence even when the paper desk held
    no position. Limiting collection to holdings left calibration permanently
    starved, so this bounded sweep fills the independent outcome archive too.

    TWO THINGS DECIDE WHETHER THAT SWEEP EVER ARRIVES ANYWHERE.

    ORDER. It used to walk resolution_date DESCENDING, and a run checks a few
    hundred bands before its time budget stops it. One day of markets is 220
    to 560 bands, so the walk never reached past the newest day or two - which
    are precisely the days UMA has not resolved yet. Nothing resolved, so
    nothing was captured; nothing captured meant `existing` stayed empty, so
    the next run restarted at the identical head and re-asked the identical
    unanswerable questions. paper_resolution_evidence held 0 rows against
    12,868 identified bands, and v_venue_band_resolution - which every
    verified outcome view is built on - was empty because of it.

    Oldest-first spends the budget where an answer definitely exists, and each
    answer is remembered, so the walk advances by everything it captured
    instead of resetting. This is the same correction the weather backlog
    needed: a cap over an unproductive head is a cap that never gets anywhere.

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
    ], order='resolution_date.asc,market_id.asc')
    market_ids = [str(m['market_id']) for m in markets]
    for i in range(0, len(market_ids), 100):
        chunk = market_ids[i:i + 100]
        wanted += rest_all('bands', {
            'market_id': 'in.(' + ','.join(chunk) + ')',
            'select': 'band_id,market_id,condition_id,token_yes,token_no',
        }, order='band_id.asc')

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
    return out


def cycle(budget_seconds=60, days_back=180, max_new_evidence=100):
    from paper_worker import public_json
    started,settled,checked=time.monotonic(),0,set()
    captured,failed,first_failure=0,0,None
    positions=rest_all('paper_positions',{'shares':'gt.0','select':'band_id,account_id,side'},order='band_id,account_id,side')
    position_band_ids=list(dict.fromkeys(str(p['band_id']) for p in positions))
    existing={str(e['condition_id']) for e in rest_all(
        'paper_resolution_evidence',{'select':'condition_id,proof_id'},order='condition_id.asc,proof_id.asc')}

    candidates=_candidate_bands(position_band_ids,days_back,existing)
    unreached=0
    for index,band in enumerate(candidates):
        if time.monotonic()-started>budget_seconds or captured>=max_new_evidence:
            unreached=len(candidates)-index
            break
        band_id=str(band['band_id'])
        if band_id in checked or str(band.get('condition_id')) in existing:
            continue
        checked.add(band_id)
        if not band.get('condition_id') or not band.get('token_yes') or not band.get('token_no'):
            continue
        try:
            gamma_url='https://gamma-api.polymarket.com/markets'
            markets=public_json(gamma_url,{'condition_ids':band['condition_id']})
            gamma=next((m for m in markets if m.get('conditionId')==band['condition_id']),None)
            if not gamma or gamma.get('closed') is not True or gamma.get('umaResolutionStatus')!='resolved':
                continue
            clob_url='https://clob.polymarket.com/markets/'+band['condition_id']
            clob=public_json(clob_url,{})
            winner=verify(band,gamma,clob)
            if not winner:
                continue
            identity=hashlib.sha256(json.dumps({'gamma':gamma,'clob':clob},sort_keys=True,separators=(',',':')).encode()).hexdigest()
            upsert('paper_resolution_evidence',[{'proof_id':identity,'condition_id':band['condition_id'],
                'token_yes':band['token_yes'],'token_no':band['token_no'],'winning_token':winner,'gamma':gamma,'clob':clob,
                'source_urls':[gamma_url+'?condition_ids='+band['condition_id'],clob_url]}],'proof_id')
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
            'candidates':len(candidates),'unreached':unreached}
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
