"""Recover missing event rules before a prospective call, without replacing known text."""
import concurrent.futures
import time
import re
import requests

MAX_MARKETS = 24
BUDGET_S = 12
MAX_BYTES = 2 * 1024 * 1024
MAX_RULES = 256 * 1024


def event_rules(payload, slug):
    events = payload if isinstance(payload,list) else payload.get('events',[payload])
    matches = [e for e in events if isinstance(e,dict) and e.get('slug') == slug]
    if len(matches) != 1:
        raise ValueError('Gamma event identity unavailable or ambiguous')
    event = matches[0]
    texts = []
    # Child rules define settlement. Keep differing descriptions, rather than
    # silently choosing a convenient bucket's interpretation.
    for row in [*(event.get('markets') or []), event]:
        text = str(row.get('rules') or row.get('description') or row.get('rules_text') or '').strip()
        if text and text not in texts:
            texts.append(text)
    rules = '\n\n'.join(texts)
    if not rules or len(rules.encode()) > MAX_RULES:
        raise ValueError('Gamma rules absent or over the receipt size bound')
    import metar_contract as metar
    stations = {metar.source_parameters(metar.source_url(t))[0] for t in texts if metar.source_url(t)}
    if len(stations) > 1:
        raise ValueError('Conflicting settlement stations inside event rules')
    return rules


def fetch(market):
    slug = market.get('event_slug')
    if not slug or not re.fullmatch(r'[a-z0-9-]+',slug):
        raise ValueError('Event slug unavailable or invalid')
    url = 'https://gamma-api.polymarket.com/events/slug/'+slug
    with requests.get(url, timeout=8, stream=True) as response:
        response.raise_for_status()
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > MAX_BYTES:
                raise ValueError('Gamma event exceeds recovery size bound')
    import json
    return event_rules(json.loads(raw),slug)


def recover(markets, needed, *, fetch_event=fetch, write=None, budget_s=BUDGET_S):
    """Only due city-days; one retained initial receipt; no new schedule."""
    from common import rpc
    from contract_inputs import with_rules
    write = write or rpc
    selected = [m for m in markets if (m['city_key'],str(m['resolution_date'])) in needed
                and not (m.get('rules_text') or '').strip()]
    ordered = sorted(selected,key=lambda m:(m['city_key'],str(m['resolution_date'])))
    selected = ordered[:MAX_MARKETS]
    detail = dict(requested=len(selected),stored=0,errors=[],deferred=max(0,len(ordered)-MAX_MARKETS))
    replacements = {}
    if not selected:
        return markets, detail
    deadline = time.monotonic()+budget_s
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    pending = {pool.submit(fetch_event,m):m for m in selected}
    try:
        for future in concurrent.futures.as_completed(pending,timeout=max(.01,deadline-time.monotonic())):
            m = pending[future]
            try:
                text = future.result()
                if time.monotonic() >= deadline:
                    detail['deferred'] += 1
                    continue
                saved = write('record_missing_market_rules',dict(p_market=m['market_id'],p_slug=m['event_slug'],p_rules=text))
                if not saved.get('rules_text'):
                    raise ValueError('No saved rule text returned')
                replacements[m['market_id']] = with_rules(dict(m,rules_received_at=saved.get('received_at')),saved['rules_text'])
                detail['stored'] += int(saved.get('stored',False))
            except Exception as exc:
                detail['errors'].append(dict(city=m['city_key'],date=str(m['resolution_date']),error=str(exc)[:160]))
    except concurrent.futures.TimeoutError:
        detail['deferred'] += sum(not f.done() for f in pending)
    finally:
        for f in pending:
            f.cancel()
        pool.shutdown(wait=False,cancel_futures=True)
    return [replacements.get(m['market_id'],m) for m in markets],detail
