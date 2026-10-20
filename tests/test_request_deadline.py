import pytest
import common


def test_engine_requests_stop_at_their_own_deadline_and_then_reset(monkeypatch):
    now, calls = [100.], []
    monkeypatch.setattr(common.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(common.requests, 'get', lambda *a, **k: calls.append(k))
    with pytest.raises(TimeoutError):
        with common.request_deadline(110.):
            common._get('https://example.test', timeout=90)
            now[0] = 110.
            common._get('https://example.test', timeout=90)
    assert len(calls) == 1 and calls[0]['timeout'] == 5.
    common._get('https://example.test', timeout=90)
    assert calls[-1]['timeout'] == 90


def test_tighter_timeouts_and_nested_deadlines_are_preserved(monkeypatch):
    calls = []
    monkeypatch.setattr(common.time, 'monotonic', lambda: 100.)
    monkeypatch.setattr(common.requests, 'post', lambda *a, **k: calls.append(k))
    with common.request_deadline(110.):
        with common.request_deadline(120.):
            common._post('https://example.test', timeout=(1., None))
        common._post('https://example.test', timeout=2.)
    assert [c['timeout'] for c in calls] == [(1., 5.), 2.]


def test_retry_wait_cannot_consume_the_capture_reserve(monkeypatch):
    waits = []
    monkeypatch.setattr(common.time, 'monotonic', lambda: 100.)
    monkeypatch.setattr(common.time, 'sleep', waits.append)
    monkeypatch.setattr(common, '_config', {'url': 'https://example.test', 'key': 'test'})
    class Response:
        status_code, text, headers = 429, 'limited', {'Retry-After': '60'}
    monkeypatch.setattr(common.requests, 'get', lambda *a, **k: Response())
    with common.request_deadline(110.), pytest.raises(TimeoutError):
        common.rest('anything')
    assert not waits


def test_other_threads_do_not_inherit_the_engine_request_budget():
    from concurrent.futures import ThreadPoolExecutor
    with common.request_deadline(123.):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(common._request_end.get).result() is None


def test_fill_workers_carry_the_earlier_engine_deadline_into_public_requests(monkeypatch):
    import threading
    import engine_orders, paper_worker
    from common import request_deadline
    import common as transport
    monkeypatch.setattr(transport.time, 'monotonic', lambda: 100.)
    barrier = threading.Barrier(2)
    claimed, timeouts = set(), []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {}
    def get(*args, **kwargs):
        timeouts.append(kwargs['timeout'])
        return Response()
    monkeypatch.setattr(transport.requests, 'get', get)
    def claim(aid):
        assert transport._request_end.get() == 110.
        if aid in claimed:
            return None
        claimed.add(aid)
        barrier.wait(timeout=5)
        return {'order_id': aid}
    def fill(order):
        paper_worker.public_json('https://example.test/book', {})
        return {'status': 'filled'}
    with request_deadline(110.):
        result = engine_orders.fill_ledgers(['a', 'b'], 150., claim, fill)
    assert result == {'filled': 2} and timeouts == [5., 5.]
    assert transport._request_end.get() is None


def test_a_failed_claim_is_reported_without_abandoning_other_ledgers():
    import engine_orders, time
    def claim(aid):
        if aid == 'broken':
            raise TimeoutError('claim exhausted its budget')
        return None
    assert engine_orders.fill_ledgers(['broken', 'empty'], time.monotonic()+30,
                                     claim, lambda order: {}) == {'error: TimeoutError': 1}


def test_a_parent_deadline_with_no_fill_time_starts_no_claim(monkeypatch):
    import common as transport, engine_orders
    monkeypatch.setattr(transport.time, 'monotonic', lambda: 100.)
    def claim(aid):
        pytest.fail('claim started without the engine fill allowance')
    with transport.request_deadline(101.):
        assert engine_orders.fill_ledgers(['a'], 150., claim, lambda order: {}) == {}
