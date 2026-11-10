"""1Click client: fully mocked (httpx.MockTransport), no network. One opt-in real dry quote (-m network)."""
import json
from decimal import Decimal

import httpx
import pytest

from anchorium.near import intents
from anchorium.near.intents import (FUNDS_LOCATION, OneClickClient, OneClickError, Quote, QuoteRequest, Token,
                                    worst_case_slippage_bps)

USDC = 'nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1'
WNEAR = 'nep141:wrap.near'
TOKENS = [
    {'assetId': USDC, 'decimals': 6, 'blockchain': 'near', 'symbol': 'USDC', 'price': 1.0,
     'priceUpdatedAt': '2026-11-10T00:00:00Z', 'contractAddress': USDC.removeprefix('nep141:')},
    {'assetId': WNEAR, 'decimals': 24, 'blockchain': 'near', 'symbol': 'wNEAR', 'price': 4.0,
     'priceUpdatedAt': '2026-11-10T00:00:00Z', 'contractAddress': 'wrap.near'},
]


def quote_body(dry=True, amount_in='2000000', amount_out='495000000000000000000000',
               min_out='490050000000000000000000', deposit_address=None, fee=20):
    quote = {'amountIn': amount_in, 'amountInFormatted': '2.0', 'amountInUsd': '2.0', 'minAmountIn': amount_in,
             'amountOut': amount_out, 'amountOutFormatted': '0.495', 'amountOutUsd': '1.98',
             'minAmountOut': min_out, 'timeEstimate': 22}
    if deposit_address:
        quote['depositAddress'] = deposit_address
    return {'correlationId': 'corr-1', 'timestamp': '2026-11-10T00:00:00Z', 'signature': 'ed25519:sig',
            'quoteRequest': {'dry': dry, 'appFees': [{'recipient': 'x', 'fee': fee}]}, 'quote': quote}


def client(handler, jwt=None):
    return OneClickClient('https://1click.test', jwt=jwt, transport=httpx.MockTransport(handler))


def request(dry=True):
    return QuoteRequest(origin_asset=USDC, destination_asset=WNEAR, amount=2_000_000, recipient='user.near',
                        refund_to='user.near', slippage_tolerance_bps=50, dry=dry)


# ----- tokens -----

def test_tokens_are_parsed_and_cached():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json=TOKENS)
    c = client(handler)
    assert c.token(WNEAR).decimals == 24
    assert c.token(USDC).price_usd == Decimal('1.0')
    c.tokens()
    assert calls == ['/v0/tokens']
    with pytest.raises(OneClickError, match='not supported'):
        c.token('nep141:nope')


def test_token_unit_conversion_rounds_down():
    t = Token.from_api(TOKENS[0])
    assert t.to_units(Decimal('2.0000019')) == 2_000_001
    assert t.from_units(2_000_000) == Decimal('2')


# ----- quote -----

def test_quote_request_matches_the_documented_shape():
    seen = {}

    def handler(req):
        seen['body'] = json.loads(req.content)
        seen['auth'] = req.headers.get('authorization')
        return httpx.Response(200, json=quote_body())
    q = client(handler).quote(request())
    body = seen['body']
    required = {'dry', 'swapType', 'slippageTolerance', 'originAsset', 'depositType', 'destinationAsset', 'amount',
                'refundTo', 'refundType', 'recipient', 'recipientType', 'deadline'}
    assert required <= body.keys()
    assert body['dry'] is True and body['swapType'] == 'EXACT_INPUT' and body['amount'] == '2000000'
    assert body['depositType'] == 'ORIGIN_CHAIN' and body['recipientType'] == 'DESTINATION_CHAIN'
    assert body['deadline'].endswith('.000Z')
    assert seen['auth'] is None, 'no JWT -> no Authorization header'
    assert q.quote_id == 'corr-1' and q.app_fee_bps == 20 and q.deposit_address is None


def test_jwt_is_sent_as_bearer_token():
    seen = {}

    def handler(req):
        seen['auth'] = req.headers.get('authorization')
        return httpx.Response(200, json=quote_body())
    c = client(handler, jwt='abc.def.ghi')
    c.quote(request())
    assert seen['auth'] == 'Bearer abc.def.ghi' and c.authenticated


def test_live_quote_requires_deposit_address():
    c = client(lambda req: httpx.Response(200, json=quote_body(dry=False)))
    with pytest.raises(OneClickError, match='no deposit address'):
        c.quote(request(dry=False))
    ok = client(lambda req: httpx.Response(200, json=quote_body(dry=False, deposit_address='abc123')))
    assert ok.quote(request(dry=False)).deposit_address == 'abc123'


def test_dry_flag_mismatch_is_rejected():
    c = client(lambda req: httpx.Response(200, json=quote_body(dry=False, deposit_address='x')))
    with pytest.raises(OneClickError, match='dry flag'):
        c.quote(request(dry=True))


def test_inconsistent_min_out_is_rejected():
    c = client(lambda req: httpx.Response(200, json=quote_body(min_out='999999999999999999999999999')))
    with pytest.raises(OneClickError, match='minAmountOut'):
        c.quote(request())


def test_http_400_surfaces_the_api_message():
    c = client(lambda req: httpx.Response(400, json={'message': 'Temporary swap limits: minimum swap amount is $1,000'}))
    with pytest.raises(OneClickError, match='minimum swap amount') as err:
        c.quote(request())
    assert err.value.status_code == 400


def test_invalid_requests_fail_before_any_http_call():
    with pytest.raises(ValueError):
        QuoteRequest(USDC, WNEAR, 0, 'a', 'b', 50).to_api()
    with pytest.raises(ValueError):
        QuoteRequest(USDC, WNEAR, 1, 'a', 'b', 10_001).to_api()


# ----- slippage maths -----

def tok(asset, decimals, price):
    return Token(asset, 'X', 'near', decimals, Decimal(price), None)


def make_quote(amount_in, amount_out, min_out):
    return Quote.from_api(quote_body(amount_in=str(amount_in), amount_out=str(amount_out), min_out=str(min_out)))


def test_worst_case_slippage_counts_fees_impact_and_tolerance():
    usdc, wnear = tok(USDC, 6, '1'), tok(WNEAR, 24, '4')
    # $2.00 in; guaranteed out 0.49005 wNEAR * $4 = $1.9602 -> 1.99% worst case -> 199 bps
    q = make_quote(2_000_000, 495 * 10**21, 49005 * 10**19)
    assert worst_case_slippage_bps(q, usdc, wnear) == 199


def test_worst_case_slippage_rounds_up_and_clamps_at_zero():
    usdc, wnear = tok(USDC, 6, '1'), tok(WNEAR, 24, '4')
    q = make_quote(2_000_000, 5 * 10**23, 49999 * 10**19)  # $1.99996 -> 0.2 bps -> rounds up to 1
    assert worst_case_slippage_bps(q, usdc, wnear) == 1
    better = make_quote(2_000_000, 6 * 10**23, 6 * 10**23)  # better than reference
    assert worst_case_slippage_bps(better, usdc, wnear) == 0


# ----- status -----

def status_body(status, details=None):
    return {'correlationId': 'corr-1', 'quoteResponse': {}, 'status': status, 'updatedAt': '2026-11-10T00:00:00Z',
            'swapDetails': details or {'intentHashes': [], 'nearTxHashes': [], 'originChainTxHashes': [],
                                       'destinationChainTxHashes': []}}


@pytest.mark.parametrize('status', intents.ALL_STATUSES)
def test_every_documented_status_has_a_funds_location(status):
    c = client(lambda req: httpx.Response(200, json=status_body(status)))
    update = c.status('dep-addr')
    assert update.status == status
    assert update.funds_location == FUNDS_LOCATION[status]
    assert update.terminal == (status in intents.TERMINAL)


def test_status_sends_deposit_address_and_memo():
    seen = {}

    def handler(req):
        seen.update(dict(req.url.params))
        return httpx.Response(200, json=status_body('PROCESSING'))
    client(handler).status('dep-addr', memo='m1')
    assert seen == {'depositAddress': 'dep-addr', 'depositMemo': 'm1'}


def test_unknown_status_is_an_error():
    c = client(lambda req: httpx.Response(200, json=status_body('TELEPORTED')))
    with pytest.raises(OneClickError, match='unknown 1Click status'):
        c.status('dep-addr')


def sequence_client(statuses):
    it = iter(statuses)

    def handler(req):
        item = next(it)
        if isinstance(item, int):
            return httpx.Response(item, json={'message': 'busy'})
        return httpx.Response(200, json=status_body(item))
    return client(handler)


def fake_clock():
    t = [0.0]
    return (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))


@pytest.mark.parametrize('final,outcome', [('SUCCESS', 'SUCCESS'), ('REFUNDED', 'REFUNDED'), ('FAILED', 'FAILED'),
                                           ('INCOMPLETE_DEPOSIT', 'FAILED')])
def test_track_records_the_timeline_and_maps_the_outcome(final, outcome):
    clock, sleep = fake_clock()
    seen = []
    c = sequence_client(['PENDING_DEPOSIT', 'PENDING_DEPOSIT', 'KNOWN_DEPOSIT_TX', 'PROCESSING', final])
    t = c.track('dep', poll_s=1, timeout_s=60, sleep=sleep, clock=clock, on_update=lambda u: seen.append(u.status))
    assert [u.status for u in t.timeline] == ['PENDING_DEPOSIT', 'KNOWN_DEPOSIT_TX', 'PROCESSING', final]
    assert seen == [u.status for u in t.timeline]
    assert t.final_status == final and t.outcome_status == outcome and not t.timed_out


def test_track_times_out_without_guessing_an_outcome():
    clock, sleep = fake_clock()
    c = sequence_client(['PROCESSING'] * 100)
    t = c.track('dep', poll_s=10, timeout_s=30, sleep=sleep, clock=clock)
    assert t.timed_out and t.final_status == 'TIMEOUT' and t.outcome_status is None


def test_track_retries_transient_errors_then_succeeds():
    clock, sleep = fake_clock()
    c = sequence_client(['PROCESSING', 503, 429, 'SUCCESS'])
    t = c.track('dep', poll_s=1, timeout_s=60, sleep=sleep, clock=clock)
    assert t.final_status == 'SUCCESS'


def test_track_raises_on_client_errors_and_on_persistent_outages():
    clock, sleep = fake_clock()
    with pytest.raises(OneClickError) as err:
        sequence_client([404]).track('dep', sleep=sleep, clock=clock)
    assert err.value.status_code == 404
    with pytest.raises(OneClickError):
        sequence_client([503] * 10).track('dep', poll_s=1, timeout_s=600, sleep=sleep, clock=clock,
                                           max_transient_errors=3)


def test_transport_errors_are_wrapped():
    def handler(req):
        raise httpx.ConnectError('down')
    with pytest.raises(OneClickError, match='ConnectError') as err:
        client(handler).tokens()
    assert err.value.status_code is None


def test_submit_deposit_body():
    seen = {}

    def handler(req):
        seen['path'], seen['body'] = req.url.path, json.loads(req.content)
        return httpx.Response(200, json={})
    client(handler).submit_deposit('dep', '0xhash', near_sender='me.near')
    assert seen == {'path': '/v0/deposit/submit',
                    'body': {'depositAddress': 'dep', 'txHash': '0xhash', 'nearSenderAccount': 'me.near'}}


# ----- opt-in: one real dry quote against the production 1Click API -----

@pytest.mark.network
def test_real_dry_quote_usdc_to_wnear():
    c = OneClickClient()
    usdc, wnear = c.token(USDC), c.token(WNEAR)
    q = c.quote(QuoteRequest(USDC, WNEAR, usdc.to_units(Decimal('2')), 'intents.near', 'intents.near', 50, dry=True))
    assert q.dry and q.amount_in == 2_000_000 and q.amount_out > 0 and q.deposit_address is None
    assert 0 <= worst_case_slippage_bps(q, usdc, wnear) < 500
