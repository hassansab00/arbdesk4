"""Read-only confirmed legacy Polygon CTF payout receipts, never transactions.

Unknown collateral/protocol/token derivations stay unresolved. Two independent
public RPCs must agree on the same block and the original YES/NO position IDs.
"""
import datetime as dt
import re

from common import _post

PROVIDERS = ('https://polygon-bor-rpc.publicnode.com', 'https://polygon.drpc.org')
CTF = '0x4d97dcd97ec945f40cf65f87097ace5ea0476045'
COLLATERAL = '0x2791bca1f2de4661ed88a30c99a7a9449aa84174'
SELECTORS = {'denominator': 'dd34de67', 'numerator': '0504c814',
             'collection': '856296f7', 'position': '39dd7530'}


def word(value):
    return format(int(value), '064x')


def uint(value):
    if not isinstance(value, str) or not re.fullmatch('0x[0-9a-fA-F]{64}', value):
        raise ValueError('invalid_ctf_uint256_receipt')
    return int(value, 16)


def request(provider, method, params):
    if provider not in PROVIDERS or method not in ('eth_chainId', 'eth_getBlockByNumber', 'eth_call'):
        raise ValueError('read_only_payout_rpc_required')
    # Use the existing single request deadline/retry boundary. No wallet,
    # signing key, exchange order or transaction RPC exists in this adapter.
    response = _post(provider, json={'jsonrpc': '2.0', 'id': 1,
        'method': method, 'params': params}, timeout=5)
    response.raise_for_status()
    packet = response.json()
    if packet.get('error') or 'result' not in packet:
        raise ValueError('payout_rpc_receipt_unavailable')
    return packet['result']


def capture(identity, *, read=request):
    condition = identity.get('condition_id', '')
    if not re.fullmatch('0x[0-9a-fA-F]{64}', condition):
        raise ValueError('unsupported_ctf_condition_identity')
    if not all(str(identity.get(k, '')).isdigit() for k in ('token_yes', 'token_no')):
        raise ValueError('unsupported_ctf_position_identity')
    latest = []
    for provider in PROVIDERS:
        if read(provider, 'eth_chainId', []) != '0x89':
            raise ValueError('payout_wrong_chain')
        header = read(provider, 'eth_getBlockByNumber', ['latest', False])
        latest.append(int(header['number'], 16))
    block_number = min(latest)-64
    if block_number <= 0:
        raise ValueError('unconfirmed_payout_block')
    condition_word = condition[2:].lower()
    providers = []
    for provider, head in zip(PROVIDERS, latest):
        header = read(provider, 'eth_getBlockByNumber', [hex(block_number), False])
        block = {k: header[k] for k in ('number', 'hash', 'timestamp')}
        calls = []

        def call(key, data):
            result = read(provider, 'eth_call', [{'to': CTF, 'data': data}, hex(block_number)])
            uint(result)
            calls.append({'key': key, 'to': CTF, 'data': data, 'result': result})
            return result

        den = uint(call('denominator', '0x'+SELECTORS['denominator']+condition_word))
        nums = [uint(call('numerator_'+str(i), '0x'+SELECTORS['numerator']+condition_word+word(i))) for i in (0, 1)]
        if den <= 0 or sum(nums) != den:
            raise ValueError('ctf_payout_not_resolved')
        for i, side in enumerate(('YES', 'NO')):
            collection = call('collection_'+side,
                '0x'+SELECTORS['collection']+word(0)+condition_word+word(1 << i))
            position = call('position_'+side,
                '0x'+SELECTORS['position']+COLLATERAL[2:].rjust(64, '0')+collection[2:])
            if uint(position) != int(identity['token_yes' if side == 'YES' else 'token_no']):
                raise ValueError('original_token_not_proven_by_ctf')
        providers.append({'source': provider, 'chain_id': '0x89', 'latest_block': head,
            'block': block, 'calls': calls})
    if providers[0]['block'] != providers[1]['block'] or providers[0]['calls'] != providers[1]['calls']:
        raise ValueError('independent_payout_providers_disagree')
    return {'protocol': 'polygon-legacy-ctf-v1', 'ctf': CTF, 'collateral': COLLATERAL,
        'providers': providers, 'received_at': dt.datetime.now(dt.timezone.utc).isoformat()}
