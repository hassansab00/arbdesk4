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


def _candidate_bands(position_band_ids, days_back):
    """Open positions first, then bands on recently closed markets.

    Venue outcomes are valuable model evidence even when the paper desk held
    no position. Limiting collection to holdings left calibration permanently
    starved, so this bounded sweep fills the independent outcome archive too.
    """
    wanted = []
    if position_band_ids:
        for i in range(0, len(position_band_ids), 100):
            chunk = position_band_ids[i:i + 100]
            wanted += rest_all('bands', {
                'band_id': 'in.(' + ','.join(chunk) + ')',
                'select': 'band_id,market_id,condition_id,token_yes,token_no',
            }, order='band_id.asc')

    since = (dt.date.today() - dt.timedelta(days=days_back)).isoformat()
    markets = rest_all('markets', {
        'closed': 'eq.true', 'resolution_date': 'gte.' + since,
        'select': 'market_id,resolution_date',
    }, order='resolution_date.desc,market_id.asc')
    market_ids = [str(m['market_id']) for m in markets]
    for i in range(0, len(market_ids), 100):
        chunk = market_ids[i:i + 100]
        wanted += rest_all('bands', {
            'market_id': 'in.(' + ','.join(chunk) + ')',
            'select': 'band_id,market_id,condition_id,token_yes,token_no',
        }, order='band_id.asc')

    # Stable de-duplication; position bands were appended first and therefore
    # remain first when they also appear in the closed-market sweep.
    out, seen = [], set()
    for band in wanted:
        key = str(band['band_id'])
        if key not in seen:
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

    for band in _candidate_bands(position_band_ids,days_back):
        if time.monotonic()-started>budget_seconds or captured>=max_new_evidence:
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
    detail={'positions_settled':settled,'evidence_captured':captured,
            'bands_checked':len(checked),'failed':failed,'first_failure':first_failure}
    log_run('paper_settlement',status,settled+captured,detail)
    return detail


if __name__=='__main__':
    print(cycle())
