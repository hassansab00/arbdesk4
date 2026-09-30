"""A read refused with PGRST303 is retried once (audit repair 7, 30 Sep).

The API gateway's log for 29 Sep 12:36Z - 30 Sep 09:36Z holds seven 401s,
every one a GET, every one "PostgREST; error=PGRST303" in the first seconds of
a :36 process, the same key succeeding on the next request. Two crashed the
tick's trade prints. A refused request never reached the database and a read
is safe to repeat; a write, an RPC, or any other 401 keeps its old behaviour.
"""
import pytest
import requests

import common


class Resp:
    def __init__(self, status, body=None, proxy_status=""):
        self.status_code = status
        self._body = body if body is not None else []
        self.headers = {"proxy-status": proxy_status} if proxy_status else {}
        self.text = str(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")


@pytest.fixture
def wired(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(common, "_cfg", lambda: {"url": "https://x.supabase.co", "key": "k"})
    monkeypatch.setattr(common.time, "sleep", lambda s: sleeps.append(s))

    def script(responses):
        it = iter(responses)
        monkeypatch.setattr(common, "_get", lambda url, **kw: calls.append(url) or next(it))
    return script, calls, sleeps


def test_a_claims_refusal_is_retried_once_and_then_read(wired):
    script, calls, sleeps = wired
    script([Resp(401, {"code": "PGRST303", "message": "JWT issued at future"}), Resp(200, [{"v": 1}])])
    assert common.rest("settings") == [{"v": 1}]
    assert len(calls) == 2 and sleeps == [common.AUTH_RETRY_DELAY_S]


def test_the_gateway_header_alone_identifies_it(wired):
    script, calls, _ = wired
    script([Resp(401, "not json", proxy_status="PostgREST; error=PGRST303"), Resp(200, [])])
    assert common.rest("cities") == [] and len(calls) == 2


def test_only_once(wired):
    script, calls, _ = wired
    script([Resp(401, {"code": "PGRST303"}), Resp(401, {"code": "PGRST303"})])
    with pytest.raises(requests.HTTPError):
        common.rest("settings")
    assert len(calls) == 2


def test_any_other_401_fails_at_once(wired):
    """A wrong or revoked key must not be retried into silence."""
    script, calls, sleeps = wired
    script([Resp(401, {"code": "PGRST301", "message": "No suitable key"})])
    with pytest.raises(requests.HTTPError):
        common.rest("settings")
    assert len(calls) == 1 and sleeps == []


def test_writes_and_rpcs_are_not_given_this_retry():
    import inspect
    assert "_is_claims_refusal" not in inspect.getsource(common._post_batch)
    assert "_is_claims_refusal" not in inspect.getsource(common.rpc)
