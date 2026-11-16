"""Transport pagination and receipt ordering for server-side as-of reads."""
import datetime as dt
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from wxpredict import capture_asof

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 16, 12, tzinfo=UTC)


def test_pages_keep_scope_and_restore_first_seen_key_order():
    seen = []
    pages = iter([
        dict(rows=[dict(capture_id=3,first_capture_id=2),dict(capture_id=5,first_capture_id=1)],
             has_more=True,next_after_id=5),
        dict(rows=[dict(capture_id=8,first_capture_id=2)],has_more=False,next_after_id=8)])
    def rpc(name, body):
        seen.append((name,body))
        return next(pages)
    result = capture_asof.read(SimpleNamespace(rpc=rpc),
        {"nyc":{"icao":"KLGA"}},{"nyc"},NOW-dt.timedelta(hours=40),[NOW,NOW])
    assert [r["capture_id"] for r in result] == [5,3,8]
    assert [p["p_after_id"] for _,p in seen] == [0,5]
    assert all(name=="live_capture_asof" for name,_ in seen)
    assert seen[0][1]["p_prefixes"] == ["LGA|","nyc|"]
    assert seen[0][1]["p_times"] == [NOW.isoformat()]
    assert seen[0][1]["p_current_receipts"] is True
    assert seen[1][1]["p_first"] == seen[0][1]["p_first"]


@pytest.mark.parametrize("page",[
    None, {}, dict(rows=[]),
    dict(rows=[],has_more=True,next_after_id=1),
    dict(rows=[dict(capture_id=2,first_capture_id=1),dict(capture_id=1,first_capture_id=1)],has_more=False),
    dict(rows=[dict(capture_id=1,first_capture_id=1)],has_more=True,next_after_id=2)])
def test_incomplete_or_nonadvancing_pages_fail_explicitly(page):
    with pytest.raises((ValueError,KeyError,TypeError)):
        capture_asof.read(SimpleNamespace(rpc=lambda *a:page),
            {"city":{}},{"city"},NOW-dt.timedelta(hours=40),[NOW])


def test_shadow_instants_keep_the_original_interpretation_and_upper_window():
    sent=[]
    ops=SimpleNamespace(rpc=lambda name,p:sent.append(p) or dict(rows=[],has_more=False,next_after_id=0))
    capture_asof.read(ops,{"city":{}},{"city"},NOW-dt.timedelta(hours=46),
        [NOW-dt.timedelta(hours=6)],current_receipts=False,until=NOW)
    assert sent[0]["p_current_receipts"] is False
    assert sent[0]["p_until"]==NOW.isoformat()
    assert sent[0]["p_times"]==[(NOW-dt.timedelta(hours=6)).isoformat()]


def test_transport_failure_is_not_retried_as_an_expensive_full_history_download():
    def fail(*a):
        raise TimeoutError("budget")
    with pytest.raises(TimeoutError):
        capture_asof.read(SimpleNamespace(rpc=fail),{"city":{}},{"city"},
            NOW-dt.timedelta(hours=40),[NOW])
