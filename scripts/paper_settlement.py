"""Paper payouts only after Gamma resolution and CLOB winner identity agree.

Mirrors the venue's binary payout; does not claim a weather-station parser has
been validated or change the legacy settlement_verified switch.
"""
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


def cycle(budget_seconds=60):
    from paper_worker import public_json
    started,settled,checked=time.monotonic(),0,set()
    positions=rest_all('paper_positions',{'shares':'gt.0','select':'band_id,account_id,side'},order='band_id,account_id,side')
    for pos in positions:
        if time.monotonic()-started>budget_seconds:
            break
        if pos['band_id'] in checked:
            continue
        checked.add(pos['band_id'])
        rows=rest('bands',{'band_id':'eq.'+pos['band_id'],'select':'band_id,condition_id,token_yes,token_no'})
        if not rows:
            continue
        band=rows[0]
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
        settled+=rpc('settle_paper_inventory',{'p_band':band['band_id'],'p_proof':identity})
    log_run('paper_settlement','ok',settled,{'positions_settled':settled,'bands_checked':len(checked)})
    return {'positions_settled':settled}


if __name__=='__main__':
    print(cycle())
