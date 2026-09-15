import copy
import paper_settlement as settlement
import paper_worker
import pytest
from paper_settlement import verify

def data():
    band={'condition_id':'condition','token_yes':'yes','token_no':'no'}
    gamma={'conditionId':'condition','closed':True,'umaResolutionStatus':'resolved',
        'clobTokenIds':'["yes","no"]','outcomes':'["Yes","No"]','outcomePrices':'["0","1"]'}
    clob={'condition_id':'condition','closed':True,'accepting_orders':False,
        'tokens':[{'token_id':'yes','winner':False},{'token_id':'no','winner':True}]}
    return band,gamma,clob

def test_final_venue_winner_must_agree_across_both_sources():
    assert verify(*data())=='no'

@pytest.mark.parametrize('change',[{'closed':False},{'umaResolutionStatus':'proposed'}, {'outcomePrices':'["0.5","0.5"]'}])
def test_provisional_price_or_void_never_becomes_a_binary_payout(change):
    band,gamma,clob=data();gamma.update(change)
    assert verify(band,gamma,clob) is None

def test_wrong_condition_or_reversed_token_identity_blocks_payment():
    band,gamma,clob=data();gamma['conditionId']='other'
    with pytest.raises(ValueError,match='identity'):verify(band,gamma,clob)
    band,gamma,clob=data();gamma['clobTokenIds']='["no","yes"]'
    with pytest.raises(ValueError,match='identity'):verify(band,gamma,clob)

def test_conflicting_winner_is_not_silently_paid():
    band,gamma,clob=data();clob['tokens'][0]['winner']=True
    with pytest.raises(ValueError,match='disagree'):verify(band,gamma,clob)


def test_closed_markets_build_evidence_without_an_open_position(monkeypatch):
    band,gamma,clob=data()
    band.update({'band_id':'band-1','market_id':'market-1'})
    writes=[];settles=[];logs=[]

    def all_rows(path,params,**kwargs):
        if path=='paper_positions': return []
        if path=='paper_resolution_evidence': return []
        if path=='markets': return [{'market_id':'market-1','resolution_date':'2026-09-12'}]
        if path=='bands': return [band]
        raise AssertionError(path)

    def rows(path,params=None):
        raise AssertionError(path)

    def venue(url,params):
        return [gamma] if 'gamma-api' in url else clob

    monkeypatch.setattr(settlement,'rest_all',all_rows)
    monkeypatch.setattr(settlement,'rest',rows)
    monkeypatch.setattr(settlement,'upsert',lambda table,rows,key:writes.extend(rows) or len(rows))
    monkeypatch.setattr(settlement,'rpc',lambda fn,args:settles.append(args) or 0)
    monkeypatch.setattr(settlement,'log_run',lambda *args:logs.append(args))
    monkeypatch.setattr(paper_worker,'public_json',venue)

    result=settlement.cycle(budget_seconds=30)
    assert result['evidence_captured']==1
    assert result['positions_settled']==0
    assert writes[0]['winning_token']=='no'
    assert settles==[{'p_band':'band-1','p_proof':writes[0]['proof_id']}]
    assert logs[0][1]=='ok'


# --------------------------------------------------------------------------
# ARCHIVE TRAVERSAL
#
# The sweep walked resolution_date DESCENDING under a time budget that reaches
# a few hundred bands. One day of markets is 220-560 bands, so it never got
# past the newest day or two - exactly the days UMA has not resolved yet.
# Nothing resolved, so nothing was captured; nothing captured left `existing`
# empty, so the next run restarted at the identical head and re-asked the
# identical unanswerable questions.
#
# Measured on the live database before the fix: paper_resolution_evidence 0
# rows and v_venue_band_resolution 0 rows against 12,868 bands carrying a full
# condition/token identity, with every run logging "ok, bands_checked 204-333,
# evidence_captured 0, failed 0" - a healthy-looking log for a sweep that was
# never arriving anywhere.
# --------------------------------------------------------------------------
def _band(band_id, market_id, condition=None):
    return {'band_id': band_id, 'market_id': market_id,
            'condition_id': condition or 'c-' + band_id,
            'token_yes': 'y-' + band_id, 'token_no': 'n-' + band_id}


@pytest.fixture
def venue_tables(monkeypatch):
    """Scripted reads that honour `order` and `in.()` the way PostgREST does.

    Modelling the ordering matters here: bands are fetched in chunks of 100
    MARKETS, so which bands a budget-limited run reaches is decided by the
    order of the markets read, not by anything in the bands read.
    """
    calls = []

    def go(markets, bands):
        def all_rows(path, params=None, *, order, **kwargs):
            calls.append((path, params, order))
            if path == 'markets':
                keys = [k.split('.')[0] for k in order.split(',')]
                rows = [m for m in markets if _in_window(m, params)]
                for key in reversed(keys):
                    rows = sorted(rows, key=lambda m: m[key],
                                  reverse=order.endswith('.desc'))
                return rows
            if path == 'bands':
                lookup = dict(params.items() if isinstance(params, dict)
                              else params)
                field = 'market_id' if 'market_id' in lookup else 'band_id'
                wanted = lookup[field][len('in.('):-1].split(',')
                return sorted((b for b in bands if b[field] in wanted),
                              key=lambda b: b['band_id'])
            raise AssertionError(path)

        monkeypatch.setattr(settlement, 'rest_all', all_rows)
        return calls

    def _in_window(market, params):
        bounds = dict((v.split('.', 1)[0], v.split('.', 1)[1])
                      for k, v in (params or []) if k == 'resolution_date')
        return bounds['gte'] <= market['resolution_date'] <= bounds['lte']

    return go


def test_the_walk_starts_at_the_newest_day_the_venue_can_still_answer(monkeypatch):
    """Bands are fetched in chunks of 100 MARKETS, so the order of the markets
    read decides which bands a budget-limited run ever sees - about one day's
    worth per run.

    Both ends of the range are unproductive, and BOTH have been measured:

      too new   UMA has not ruled. The original walked newest-first into this
                and captured nothing, ever.
      too old   Polymarket no longer serves the event. The first repair walked
                oldest-first into this: 14,212 candidates, 267 checked, 0
                captured, 0 failed, spent on April markets.

    SETTLE_LAG_DAYS removes the too-new end; the walk then takes the freshest
    day that remains, which is both answerable and the most useful evidence.
    """
    markets = [{'market_id': 'm%03d' % i,
                'resolution_date': '2026-04-01' if i < 150 else '2026-09-13'}
               for i in range(200)]
    asked = []

    def all_rows(path, params=None, *, order, **kwargs):
        if path == 'markets':
            return sorted(markets, key=lambda m: m['resolution_date'],
                          reverse=order.startswith('resolution_date.desc'))
        asked.append(params['market_id'])
        return []

    monkeypatch.setattr(settlement, 'rest_all', all_rows)
    settlement._candidate_bands([], days_back=180)

    # Chunks are 100 markets wide, so a small fixture puts both days in the
    # first chunk; what matters is which comes FIRST, because that is the end
    # the budget is spent from.
    first = asked[0]
    assert first.index('m199') < first.index('m000'), \
        'the freshest answerable day must lead the walk, not April'


def test_the_markets_read_is_ordered_newest_first(venue_tables):
    calls = venue_tables([], [])
    settlement._candidate_bands([], days_back=180)
    order = next(order for path, _, order in calls if path == 'markets')
    assert order.startswith('resolution_date.desc')


def test_each_candidate_carries_the_day_it_settles_on(venue_tables):
    """Without the day attached, a skipped band cannot say WHICH end of the
    window it failed at - which is the whole diagnosis."""
    venue_tables([{'market_id': 'm', 'resolution_date': '2026-09-11'}],
                 [_band('b', 'm')])
    picked = settlement._candidate_bands([], days_back=180)
    assert picked[0]['resolution_date'] == '2026-09-11'


def test_markets_are_selected_by_age_not_by_the_local_closed_flag(venue_tables):
    """markets.closed is maintained by the ingest side and goes stale: 100
    markets on 2026-09-04/05 still read false long after the venue had settled
    them. verify() re-checks Gamma and the CLOB, so the stale local column must
    not decide what is even asked about."""
    calls = venue_tables([], [])
    settlement._candidate_bands([], days_back=180)
    params = next(p for path, p, _ in calls if path == 'markets')
    assert not any(k == 'closed' for k, _ in params), \
        'the venue is the authority, not our column'
    bounds = sorted(v.split('.', 1)[0] for k, v in params
                    if k == 'resolution_date')
    assert bounds == ['gte', 'lte'], 'the window is bounded at both ends'


def test_today_is_not_asked_about_because_uma_has_not_ruled_yet(venue_tables):
    import datetime
    calls = venue_tables([], [])
    settlement._candidate_bands([], days_back=180)
    params = next(p for path, p, _ in calls if path == 'markets')
    newest = next(v[len('lte.'):] for k, v in params
                  if k == 'resolution_date' and v.startswith('lte.'))
    assert datetime.date.fromisoformat(newest) <= \
        datetime.date.today() - datetime.timedelta(days=1)


def test_a_proved_condition_never_costs_the_budget_again(venue_tables):
    venue_tables([{'market_id': 'm', 'resolution_date': '2026-08-20'}],
                 [_band('done', 'm', 'proved'), _band('todo', 'm', 'open')])
    picked = settlement._candidate_bands([], days_back=180,
                                         settled_conditions={'proved'})
    assert [b['band_id'] for b in picked] == ['todo'], \
        'a settled day must not consume the run it no longer needs'


def test_open_positions_still_outrank_the_archive(venue_tables):
    venue_tables([{'market_id': 'm', 'resolution_date': '2026-08-20'}],
                 [_band('archive', 'm'), _band('held', 'other')])
    picked = settlement._candidate_bands(['held'], days_back=180)
    assert picked[0]['band_id'] == 'held'


def test_a_budget_limited_run_says_what_it_did_not_reach(monkeypatch):
    """"0 captured" read as "nothing to collect" for days while it actually
    meant "the budget ran out first". The log must tell those apart."""
    monkeypatch.setattr(settlement, 'rest_all',
                        lambda path, params=None, **kw: [])
    monkeypatch.setattr(settlement, '_candidate_bands',
                        lambda *args, **kw: [_band('b%d' % i, 'm')
                                             for i in range(50)])
    logged = []
    monkeypatch.setattr(settlement, 'log_run',
                        lambda *args: logged.append(args))
    monkeypatch.setattr(paper_worker, 'public_json',
                        lambda url, params: [])

    detail = settlement.cycle(budget_seconds=-1)
    assert detail['candidates'] == 50
    assert detail['unreached'] == 50
    assert detail['evidence_captured'] == 0


def test_a_run_that_captures_nothing_names_the_reason(monkeypatch):
    """Zero has now been misread twice: once as "nothing to collect" when the
    budget had run out, and once as "the venue disagrees" when the venue had
    simply forgotten the market. The log must distinguish them."""
    bands = [dict(_band('b%d' % i, 'm'), resolution_date='2026-04-0%d' % (i + 1))
             for i in range(3)]
    monkeypatch.setattr(settlement, 'rest_all',
                        lambda path, params=None, **kw: [])
    monkeypatch.setattr(settlement, '_candidate_bands',
                        lambda *args, **kw: bands)
    monkeypatch.setattr(settlement, 'log_run', lambda *args: None)
    # Gamma returns an empty list: the market is past the venue's retention.
    monkeypatch.setattr(paper_worker, 'public_json', lambda url, params: [])

    detail = settlement.cycle(budget_seconds=30)
    assert detail['evidence_captured'] == 0
    assert detail['skips'] == {'venue_has_no_record': 3}
    assert detail['skip_day_spans']['venue_has_no_record'] == \
        ['2026-04-01', '2026-04-03']
    assert detail['checked_span'] == ['2026-04-01', '2026-04-03']


def test_a_market_awaiting_uma_is_not_confused_with_a_forgotten_one(monkeypatch):
    """Both produce no proof, and they mean opposite things: one is worth
    asking again tomorrow, the other never is."""
    band = dict(_band('b', 'm'), resolution_date='2026-09-14')
    monkeypatch.setattr(settlement, 'rest_all',
                        lambda path, params=None, **kw: [])
    monkeypatch.setattr(settlement, '_candidate_bands',
                        lambda *args, **kw: [band])
    monkeypatch.setattr(settlement, 'log_run', lambda *args: None)
    monkeypatch.setattr(paper_worker, 'public_json', lambda url, params: [
        {'conditionId': band['condition_id'], 'closed': False,
         'umaResolutionStatus': 'proposed'}])

    detail = settlement.cycle(budget_seconds=30)
    assert detail['skips'] == {'not_resolved_yet': 1}
