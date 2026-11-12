"""Synthetic independent CTF RPC/ABI fixtures; never live trade history."""
import datetime as dt

import pytest

from original_side_payouts import capture, word, SELECTORS, PROVIDERS, CTF

IDENTITY = dict(condition_id='0x'+'ab'*32, token_yes='101', token_no='202')


def reader(*, disagreement=False, wrong_token=False, wrong_chain=False, unresolved=False):
    def read(provider, method, params):
        assert provider in PROVIDERS
        if method == 'eth_chainId':
            return '0x1' if wrong_chain else '0x89'
        if method == 'eth_getBlockByNumber':
            return dict(number='0x1000' if params[0] == 'latest' else params[0],
                hash='0x'+'cc'*32, timestamp=hex(int(dt.datetime.now(dt.timezone.utc).timestamp())))
        assert method == 'eth_call' and params[0]['to'] == CTF and params[1] == hex(4096-64)
        data = params[0]['data']
        if data.startswith('0x'+SELECTORS['denominator']):
            result = 0 if unresolved else 2
        elif data.startswith('0x'+SELECTORS['numerator']):
            result = 1
        elif data.startswith('0x'+SELECTORS['collection']):
            result = 11 if int(data[-64:], 16) == 1 else 22
        else:
            assert data.startswith('0x'+SELECTORS['position'])
            result = 101 if int(data[-64:], 16) == 11 else 202
            if wrong_token:
                result += 1
        if disagreement and provider == PROVIDERS[1] and data.startswith('0x'+SELECTORS['denominator']):
            result = 3
        return '0x'+word(result)
    return read


def test_direct_original_position_and_two_provider_receipts():
    proof = capture(IDENTITY, read=reader())
    assert proof['protocol'] == 'polygon-legacy-ctf-v1'
    assert len(proof['providers']) == 2
    assert proof['providers'][0]['calls'] == proof['providers'][1]['calls']
    assert len(proof['providers'][0]['calls']) == 7


@pytest.mark.parametrize('options,reason', [
    ({'wrong_chain': True}, 'wrong_chain'),
    ({'wrong_token': True}, 'original_token'),
    ({'unresolved': True}, 'not_resolved'),
    ({'disagreement': True}, 'not_resolved'),
])
def test_unknown_or_unverified_payouts_stay_held(options, reason):
    with pytest.raises(ValueError, match=reason):
        capture(IDENTITY, read=reader(**options))


def test_unknown_protocol_identity_never_guesses_a_payout():
    with pytest.raises(ValueError, match='condition_identity'):
        capture({**IDENTITY, 'condition_id': 'unknown'}, read=reader())
