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
    """One share at the touch cannot become a basket.

    It used to reach simulate() and come back "not executable:
    quantity_or_price_increment". The refusal now happens a step earlier and
    says which side is short - the message changed, the guarantee did not."""
    account,signal,capture=fixture()
    def thin(order):
        book=capture(order);book['asks'][0]['size']='1';return book
    with pytest.raises(ValueError,match='venue minimum order size'):
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


# ---------------------------------------------------------------------------
# A SIZE THE VENUE WILL NOT ACCEPT
#
# simulate() refuses anything under the market's orderMinSize with the code
# "quantity_or_price_increment" - which also covers a bad tick and a bad share
# step. Three unrelated causes, one string, and the plan row keeps no quotes
# because prepare() raises before building them. All 19 plans blocked on
# 16 Sep carried it and nothing said whether the touch was thin or the budget
# was small, which are opposite fixes.

def venue(minimum, size='100000', price='.30', unit='USDC'):
    account, signal, _ = fixture()
    def capture(order):
        return {'token_id': order['token_id'], 'observed_at': NOW.isoformat(), 'tradeable': True,
                'snapshot_id': order['token_id'], 'tick_size': '.01', 'fee_rate': '.05',
                'min_order_size': minimum, 'min_order_size_unit': unit,
                'asks': [{'price': price, 'size': size}], 'bids': [{'price': '.29', 'size': '1000'}]}
    return account, signal, capture


def test_a_touch_too_thin_for_the_venue_floor_names_the_depth():
    # $5 floor at $0.30 needs 16.67 shares; the book holds 5.
    account, signal, capture = venue('5', size='5')
    with pytest.raises(ValueError, match='venue minimum'):
        prepare(account, signal, capture, now=NOW)
    try:
        prepare(account, signal, capture, now=NOW)
    except ValueError as exc:
        assert 'book depth' in str(exc), str(exc)


def test_a_budget_too_small_for_the_venue_floor_names_the_budget():
    # Deep book, but max_plan_usd 10 across two legs buys ~16 shares against a
    # $10-per-leg floor needing 33.34.
    account, signal, capture = venue('10')
    with pytest.raises(ValueError, match='venue minimum'):
        prepare(account, signal, capture, now=NOW)
    try:
        prepare(account, signal, capture, now=NOW)
    except ValueError as exc:
        assert 'account budget' in str(exc), str(exc)


def test_the_floor_is_the_leg_that_needs_the_most_shares():
    """A basket buys equal shares, so a cheap leg sets the floor: the same
    dollar minimum costs twenty times the shares at $0.05 as at $1.00."""
    from paper_plans import venue_minimum_shares
    book = lambda p: {'min_order_size': '5', 'min_order_size_unit': 'USDC',
                      'asks': [{'price': p, 'size': '1'}]}
    quoted = [('a', 't', Decimal('.50'), Decimal('.05'), book('.50')),
              ('b', 't', Decimal('.05'), Decimal('.05'), book('.05'))]
    assert venue_minimum_shares(quoted, Decimal('.01')) == Decimal('100')


def test_a_share_denominated_minimum_is_not_divided_by_the_price():
    from paper_plans import venue_minimum_shares
    quoted = [('a', 't', Decimal('.05'), Decimal('.05'),
               {'min_order_size': '7', 'min_order_size_unit': 'shares'})]
    assert venue_minimum_shares(quoted, Decimal('.01')) == Decimal('7')


def test_a_plan_that_clears_the_floor_is_still_published():
    account, signal, capture = venue('1')
    legs, _ = prepare(account, signal, capture, now=NOW)
    assert len(legs) == 2 and Decimal(legs[0]['shares']) > 0


# ---------------------------------------------------------------------------
# A RUN THAT STOPS EARLY IS STILL A RUN
#
# log_run sat below both loops, so the two ordinary early exits returned
# without writing anything - and hitting the 10-plan cap is the NORMAL outcome
# on a busy board, reached in seconds. ingest_log therefore held one
# paper_plans row in twelve hours while the step ran every cycle and wrote
# plans each time, and the desk page showed "Proposals: 0 proposed, 9h ago"
# beside signals fired twenty minutes earlier. A dead-looking step that was
# never dead.

def test_every_exit_from_the_cycle_records_the_run():
    import ast
    import inspect
    import textwrap
    import paper_plans

    tree = ast.parse(textwrap.dedent(inspect.getsource(paper_plans.cycle)))
    outer = tree.body[0]

    # cycle() nests two helpers - done() and capture() - and their own returns
    # are not exits from cycle. Walking the source line by line counted both
    # and made this test fail on correct code, so the nested bodies are
    # excluded rather than matched around.
    nested = {id(n) for fn in ast.walk(outer)
              if isinstance(fn, ast.FunctionDef) and fn is not outer
              for n in ast.walk(fn)}

    bad = [ast.unparse(n) for n in ast.walk(outer)
           if isinstance(n, ast.Return) and id(n) not in nested
           and not (isinstance(n.value, ast.Call)
                    and getattr(n.value.func, "id", None) == "done")]
    assert not bad, (
        "these paths leave cycle() without writing to ingest_log, so the desk "
        f"page cannot tell the step ran: {bad}")


def test_the_log_says_why_it_stopped():
    """'0 proposals' means something different when the cap was hit than when
    every signal was considered and none qualified."""
    import inspect
    import paper_plans
    src = inspect.getsource(paper_plans.cycle)
    for note in ("plan cap", "budget", "considered every signal"):
        assert note in src, f"no exit reports {note!r}"


# --------------------------------------------------------------------------
# ONE DESK TOOK THE WHOLE BUDGET AND THE REST GOT NOTHING.
#
# cycle() ran `for account: for signal:` against a single global cap of ten.
# The first desk in account_id order that could act on the board reached ten
# and returned, so every desk behind it got zero - not fewer plans, none, on
# every cycle, for as long as the first desk kept finding signals.
#
# Measured on 16 Sep: 'Wide edge, all US' took 39 plans in three hours while
# 'All Cities', created eleven minutes before a run that published ten, got 0.
# From the page that desk is indistinguishable from one with nothing to trade:
# ACTIVE, funded, zero positions, for ever.
# --------------------------------------------------------------------------
def _cycle_with(monkeypatch, accounts, signals, **kw):
    """Run cycle() against scripted desks and signals, returning the plans."""
    import paper_plans

    published = []

    def rest_all(path, params=None, **rest):
        if path == "paper_accounts":
            return [dict(a) for a in accounts]
        if path == "signals":
            return [dict(s) for s in signals]
        return []

    def rest(path, params=None, **rest):
        if path == "strategies":
            return [{"strategy_id": "s1"}]
        return []                                  # no plan exists for this key yet

    monkeypatch.setattr(paper_plans, "rest_all", rest_all)
    monkeypatch.setattr(paper_plans, "rest", rest)
    monkeypatch.setattr(paper_plans, "prepare", lambda *a, **k: ([], {}))
    monkeypatch.setattr(paper_plans, "rpc",
                        lambda name, args: published.append(args["p_account"]))
    monkeypatch.setattr(paper_plans, "log_run", lambda *a, **k: None)
    monkeypatch.setattr("paper_worker.capture_book", lambda order: {})
    paper_plans.cycle(**kw)
    return published


def _desk(account_id):
    return {"account_id": account_id,
            "policy": {"strategies": ["s1"], "cities": ["ALL"], "max_plan_usd": 10}}


def _signal(n):
    return {"signal_id": n, "band_id": "a", "side": "YES", "strategy_id": "s1",
            "payload": {"cycle_id": f"c{n}", "basket_group": "a", "decision_inputs": {}}}


def test_a_second_desk_is_not_starved_by_the_first(monkeypatch):
    published = _cycle_with(monkeypatch, [_desk("aaa"), _desk("bbb")],
                            [_signal(n) for n in range(20)], max_plans=3)
    assert published.count("aaa") == 3
    assert published.count("bbb") == 3, (
        "the second desk got nothing: the cap is spent by whichever desk the "
        "loop reaches first, which is the bug")


def test_a_desk_created_today_gets_the_freshest_signals_not_the_leftovers(monkeypatch):
    """Signals arrive fired_at.desc. Every desk must be offered the best signal
    on the board before any desk is offered the second-best - otherwise the
    90-second budget always runs out on the same desk, which is starvation
    again wearing a different hat."""
    published = _cycle_with(monkeypatch, [_desk("aaa"), _desk("bbb")],
                            [_signal(n) for n in range(20)], max_plans=5)
    assert published[:2] == ["aaa", "bbb"], \
        "the freshest signal must reach every desk before the next one is tried"


def test_the_cap_still_bounds_what_one_desk_proposes(monkeypatch):
    published = _cycle_with(monkeypatch, [_desk("aaa")],
                            [_signal(n) for n in range(50)], max_plans=4)
    assert len(published) == 4


def test_the_run_stops_once_every_desk_is_capped(monkeypatch):
    """Walking the remaining signals after everyone is full costs venue calls
    for plans that cannot be published."""
    published = _cycle_with(monkeypatch, [_desk("aaa"), _desk("bbb")],
                            [_signal(n) for n in range(40)], max_plans=2)
    assert len(published) == 4
