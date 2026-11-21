"""An observed billing restriction must not cause a failing-request storm."""
import pytest
import requests

import common


class Response:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.headers = {}
        self.text = 'fixture-response-body'
        self.payload = payload if payload is not None else []

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('fixture HTTP error', response=self)


@pytest.fixture
def quota(monkeypatch):
    clock, calls = [100.0], []
    monkeypatch.setattr(common, '_cfg', lambda: {'url': 'https://db.test', 'key': 'fixture-secret'})
    monkeypatch.setattr(common, '_quota_blocks', {})
    monkeypatch.setattr(common.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(common.requests, 'get', lambda url, **kw: calls.append(('GET', url)) or Response(402))
    monkeypatch.setattr(common.requests, 'post', lambda url, **kw: calls.append(('POST', url)) or Response(402))
    return clock, calls


def test_observed_get_restriction_stops_following_reads_writes_and_failure_logging(quota, capsys):
    _, calls = quota
    with pytest.raises(common.SupabaseQuotaError) as first:
        common.rest('cities')
    assert first.value.response.status_code == 402
    for action in [lambda: common.rest('settings'), lambda: common.rpc('heartbeat'),
                   lambda: common.insert('observations', [{'value': 1}])]:
        with pytest.raises(common.SupabaseQuotaError) as blocked:
            action()
        assert blocked.value.response.status_code == 402
    common.log_run('worker', 'error', 0, {'failed': True})
    assert calls == [('GET', 'https://db.test/rest/v1/cities')]
    assert 'fixture-secret' not in str(first.value) + capsys.readouterr().err


def test_observed_write_restriction_is_not_reported_as_a_successful_write(quota):
    _, calls = quota
    with pytest.raises(common.SupabaseQuotaError):
        common.insert('observations', [{'value': 1}])
    with pytest.raises(common.SupabaseQuotaError):
        common.rest('cities')
    assert calls == [('POST', 'https://db.test/rest/v1/observations')]


def test_service_can_recover_after_the_bounded_cooldown(quota, monkeypatch):
    clock, calls = quota
    with pytest.raises(common.SupabaseQuotaError):
        common.rest('cities')
    clock[0] += common.QUOTA_COOLDOWN_SECONDS + 1
    monkeypatch.setattr(common.requests, 'get', lambda url, **kw: calls.append(('GET', url)) or Response(200, [{'city_key': 'fixture'}]))
    assert common.rest('cities') == [{'city_key': 'fixture'}]
    assert common.rest('cities') == [{'city_key': 'fixture'}]
    assert len(calls) == 3


def test_external_weather_calls_continue_during_a_supabase_restriction(quota, monkeypatch):
    _, calls = quota
    with pytest.raises(common.SupabaseQuotaError):
        common.rest('cities')
    monkeypatch.setattr(common.requests, 'get', lambda url, **kw: calls.append(('GET', url)) or Response(200))
    assert common._get('https://weather.example/reports').status_code == 200
    # Even an external service with its own API key is a different origin.
    assert common._get('https://weather.example/reports', headers={'apikey': 'external'}).status_code == 200
    assert len(calls) == 3


def test_permission_errors_do_not_open_a_quota_cooldown(quota, monkeypatch):
    _, calls = quota
    monkeypatch.setattr(common.requests, 'get', lambda url, **kw: calls.append(('GET', url)) or Response(403))
    for _ in range(2):
        with pytest.raises(requests.HTTPError):
            common.rest('cities')
    assert len(calls) == 2
