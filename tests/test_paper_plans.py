import copy
import datetime as dt
from decimal import Decimal
import pytest
from paper_plans import prepare, command_key

NOW=dt.datetime(2026,9,12,12,tzinfo=dt.timezone.utc)

def fixture():
    account={'account_id':'desk','cash':100,'reserved_cash':0,'policy':{'cities':['london'],'max_plan_usd':10,'min_edge':'.03'}}
    inputs={bid:{'unit':'C','model_prob_yes':.45,'decision_evidence':{'band':{'band_id':bid,'band_label':f'{20+i}°C','band_lo':20+i,'band_hi':21+i,'token_yes':bid,'token_no':'no-'+bid},
        'market':{'city_key':'london','unit':'C','resolution_date':'2026-09-12'},'forecast':{'for_date':'2026-09-12','run_at':'2026-09-12T06:00:00Z'}}} for i,bid in enumerate(['a','b'])}
    signal={'signal_id':1,'band_id':'a','side':'YES','payload':{'cycle_id':'cycle','basket_group':'a:b','band_ids':['a','b'],'decision_at':NOW.isoformat(),'decision_inputs':inputs}}
    def capture(order):
        return {'token_id':order['token_id'],'observed_at':NOW.isoformat(),'tradeable':True,'snapshot_id':order['token_id'],
            'tick_size':'.01','fee_rate':'.05','min_order_size':1,'min_order_size_unit':'USDC',
            'asks':[{'price':'.30','size':'1000'}],'bids':[{'price':'.29','size':'1000'}]}
    return account,signal,capture

def test_basket_reserves_fees_with_equal_shares_and_keeps_original_decision():
    account,signal,capture=fixture()
    legs,evidence=prepare(account,signal,capture,now=NOW)
    assert legs[0]['shares']==legs[1]['shares']
    assert sum(Decimal(x['cash_ceiling']) for x in legs)<=10
    assert evidence['signal']==signal and len(evidence['quotes'])==2
    assert Decimal(evidence['net_edge_per_share'])==Decimal('.279')

def test_two_separate_leg_signals_share_one_account_command():
    account,signal,_=fixture()
    second=copy.deepcopy(signal);second.update(signal_id=2,band_id='b')
    assert command_key(account,signal)==command_key(account,second)
    assert command_key(account,signal)!=command_key({**account,'account_id':'other'},signal)

@pytest.mark.parametrize('change',[{'unit':'F'},{'model_prob_yes':None},{'model_prob_yes':-1}])
def test_bad_units_or_missing_probability_block_proposals(change):
    account,signal,capture=fixture();signal['payload']['decision_inputs']['a'].update(change)
    with pytest.raises((ValueError,TypeError,ArithmeticError)):
        prepare(account,signal,capture,now=NOW)

def test_stale_matching_forecast_cannot_authorize_new_entry():
    account,signal,capture=fixture()
    signal['payload']['decision_inputs']['b']['decision_evidence']['forecast']['run_at']='2026-09-10T06:00:00Z'
    with pytest.raises(ValueError,match='forecast'):
        prepare(account,signal,capture,now=NOW)

def test_no_complete_depth_never_presents_fully_executable_basket():
    account,signal,capture=fixture()
    def thin(order):
        book=capture(order);book['asks'][0]['size']='1';return book
    with pytest.raises(ValueError,match='not executable'):
        prepare(account,signal,thin,now=NOW)


# ---------------------------------------------------------------------------
# CONTRACT LABELS THAT DO NOT SPELL OUT THEIR UNIT
#
# The unit check demanded that every band's own label carry a degree sign and
# a matching C or F, and treated a label that merely did not say as a
# DISAGREEMENT - identical treatment to a label saying the opposite. Live
# Polymarket labels are written "85-86F", which carries no degree sign, so on
# 15 Sep every proposal for the three Texas desks would have been published
# blocked with "unit unverified" while the board itself was healthy: 43 cities
# priced, 405 bands with an edge.
#
# markets.unit is derived by P0.2 from those same labels market-wide, so a
# single silent label contradicts nothing. A label that names the OTHER unit
# still does, and still raises - that one would size a trade against bounds
# meaning something else.

@pytest.mark.parametrize('label', ['20C', '20°C', '25℃', '20 Celsius',
                                   'Below 21C', '20-21', ''])
def test_a_label_that_agrees_or_stays_silent_is_accepted(label):
    account, signal, capture = fixture()
    for leg in signal['payload']['decision_inputs'].values():
        leg['decision_evidence']['band']['band_label'] = label
    legs, _ = prepare(account, signal, capture, now=NOW)
    assert len(legs) == 2


@pytest.mark.parametrize('label', ['85-86F', '85-86°F', '86F or above', '85-86'])
def test_the_fahrenheit_shape_the_texas_desks_actually_trade(label):
    """The live shape. Austin, Dallas and Houston all settle in Fahrenheit and
    their labels carry no degree sign, which is exactly the case the old check
    rejected."""
    account, signal, capture = fixture()
    for leg in signal['payload']['decision_inputs'].values():
        leg['unit'] = 'F'
        leg['decision_evidence']['market']['unit'] = 'F'
        leg['decision_evidence']['band']['band_label'] = label
    legs, _ = prepare(account, signal, capture, now=NOW)
    assert len(legs) == 2


@pytest.mark.parametrize('label', ['85-86F', '20°F', '77℉', '20 Fahrenheit', '20°C to 21°F'])
def test_a_label_naming_the_other_unit_still_blocks(label):
    account, signal, capture = fixture()
    signal['payload']['decision_inputs']['a']['decision_evidence']['band']['band_label'] = label
    with pytest.raises(ValueError, match='temperature unit disagree'):
        prepare(account, signal, capture, now=NOW)


def test_the_market_unit_itself_is_still_required():
    """Absence is tolerated on the LABEL, never on the market."""
    account, signal, capture = fixture()
    signal['payload']['decision_inputs']['a']['decision_evidence']['market']['unit'] = None
    with pytest.raises(ValueError, match='unit unverified'):
        prepare(account, signal, capture, now=NOW)


# ---------------------------------------------------------------------------
# A THIN TOUCH IS A SMALLER TRADE, NOT A REFUSED ONE
#
# The limit on each leg is that leg's best ask, so only the touch level sits
# inside it. Sizing purely off the budget and then demanding a complete fill
# discarded the plan whenever the touch was thinner than the budget, which is
# the ordinary case on a band trading a few hundred dollars. On 16 Sep both
# in-policy proposals the Texas desks produced died exactly there, reason
# "insufficient_depth_within_limit", while the edge behind them was sound.

def thin(size):
    account, signal, _ = fixture()
    def capture(order):
        return {'token_id': order['token_id'], 'observed_at': NOW.isoformat(), 'tradeable': True,
                'snapshot_id': order['token_id'], 'tick_size': '.01', 'fee_rate': '.05',
                'min_order_size': 1, 'min_order_size_unit': 'USDC',
                'asks': [{'price': '.30', 'size': size}], 'bids': [{'price': '.29', 'size': '1000'}]}
    return account, signal, capture


def test_a_touch_thinner_than_the_budget_sizes_the_plan_down():
    account, signal, capture = thin('5')
    legs, evidence = prepare(account, signal, capture, now=NOW)
    assert [leg['shares'] for leg in legs] == ['5', '5'], 'both legs take the thinnest leg'
    assert all(q['preview']['status'] == 'filled' for q in evidence['quotes'])


def test_the_budget_still_binds_when_the_book_is_deep():
    account, signal, capture = thin('100000')
    legs, _ = prepare(account, signal, capture, now=NOW)
    assert Decimal(legs[0]['shares']) < Decimal('100000')
    assert sum(Decimal(x['cash_ceiling']) for x in legs) <= 10


def test_no_depth_at_the_quoted_price_is_still_refused():
    account, signal, capture = thin('0.001')   # below one share step
    with pytest.raises(ValueError, match='No depth at the quoted price'):
        prepare(account, signal, capture, now=NOW)


# ---------------------------------------------------------------------------
# A CITY THE DESK CANNOT TRADE MUST NOT COST IT A PLAN SLOT

def test_the_signals_own_city_is_readable_without_a_request():
    from paper_plans import signal_cities
    assert signal_cities({'payload': {'decision_inputs': {'a': {'city_key': 'austin'}}}}) == {'austin'}
    assert signal_cities({'payload': {'decision_inputs': {
        'a': {'decision_evidence': {'market': {'city_key': 'dallas'}}}}}}) == {'dallas'}
    assert signal_cities({'payload': {}}) == set(), 'absence is not a city, and must not drop the signal'
