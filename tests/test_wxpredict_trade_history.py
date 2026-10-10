"""WXPredict phase 2.4 (10 Oct): tools/wxpredict/trade_history.py asks the venue
for every trade of a settled event, and a short answer is never taken for a
whole one.

No network: the venue is a fake that pages like the data API, refuses an
offset over 10,000 and filters by `start`/`end`."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wxpredict import trade_history as th  # noqa: E402


class FakeVenue:
    """The data API's paging over a fixed list of trades, newest first."""

    def __init__(self, trades):
        self.trades = sorted(trades, key=lambda t: -t["timestamp"])
        self.asked = []

    def get(self, url, params):
        self.asked.append(dict(params))
        if params["offset"] > th.MAX_OFFSET:
            raise AssertionError("asked past the cap")
        rows = [t for t in self.trades
                if params.get("start", 0) <= t["timestamp"] <= params.get("end", 10**12)]
        return rows[params["offset"]:params["offset"] + params["limit"]]


def _trades(n, cid="0xa", t0=1_000_000):
    return [{"conditionId": cid, "timestamp": t0 + i, "price": 0.5, "size": 1.0, "proxyWallet": "0xW"}
            for i in range(n)]


def test_an_answer_that_reaches_the_cap_is_not_called_whole():
    whole, ok = th.pages(FakeVenue(_trades(2_500)), {"market": "0xa"})
    assert len(whole) == 2_500 and ok
    capped, ok = th.pages(FakeVenue(_trades(12_000)), {"market": "0xa"})
    assert len(capped) == 11_000 and not ok, "11 pages of 1,000 is all the API gives: short, and said so"


def test_a_bucket_over_the_cap_is_asked_in_halves_of_its_life_and_comes_back_whole():
    venue = FakeVenue(_trades(30_000))
    got, ok = th.bucket_trades(venue, "0xa", 1_000_000, 1_030_000)
    assert ok and len(got) == 30_000
    assert len({t["timestamp"] for t in got}) == 30_000, "the halves meet without a gap or an overlap"
    assert all("start" in a and "end" in a for a in venue.asked)


def test_three_kinds_of_bucket():
    # a match with Gamma's volume is agreement, not proof every trade came back (Codex on #364)
    assert th.kind({"volume_match": True, "gamma_volume": 5.0}) == "volume_match"
    assert th.kind({"volume_match": False, "gamma_volume": 0.0}) == "no_gamma_volume", \
        "Gamma gave no volume: nothing to compare, not short"
    assert th.kind({"volume_match": False, "gamma_volume": 5.0}) == "volume_differs"


def test_the_summary_counts_both_directions_of_a_mismatch():
    rows = [{"date": "2026-04-04", "trades": 3, "asked_by": "event", "before_created": 0, "after_day_plus_1": 0,
             "buckets": [{"condition_id": "a", "trades": 1, "size": 9.0, "gamma_volume": 10.0, "volume_match": False},
                         {"condition_id": "b", "trades": 1, "size": 12.0, "gamma_volume": 10.0, "volume_match": False},
                         {"condition_id": "c", "trades": 1, "size": 4.0, "gamma_volume": 4.0, "volume_match": True}]},
            {"date": "2026-03-06", "trades": 1, "asked_by": "event", "before_created": 0, "after_day_plus_1": 0,
             "buckets": [{"condition_id": "d", "trades": 1, "size": 7.0, "gamma_volume": 0.0, "volume_match": False}]}]
    s = th.summarise(rows, {"2026-03": 1, "2026-04": 1})
    assert s["kinds"] == {"volume_differs": 2, "volume_match": 1, "no_gamma_volume": 1}
    assert s["volume_differs_ratio"]["above_1"] == 1 and s["volume_differs_ratio"]["min"] == 0.9
    assert s["volume_differs_net_shares_short"] == -1.0, "one short by 1, one over by 2"
    assert s["per_month"]["2026-03"]["no_gamma_volume"] == 1


def test_the_committed_result_says_what_the_docs_say():
    doc = json.loads((ROOT / "data" / "eval" / "wxpredict" / "trade_history_probe.json").read_text())
    s, a = doc["summary"], doc["archive"]
    assert (s["events"], s["buckets"], s["trades"]) == (108, 1092, 246108)
    assert s["kinds"] == {"volume_match": 863, "no_gamma_volume": 56, "volume_differs": 173}
    assert (a["api_trades"], a["never_stored"], a["stored"], a["stored_no_longer_served"]) == (15726, 2506, 13113, 0)
    # the rows we hold fall short by the never-stored AND the merged (Codex on #364)
    assert (a["merged_by_dedupe_key"], a["row_deficit"], a["row_deficit_share"]) == (107, 2613, 0.1662)
    assert a["row_deficit"] == a["never_stored"] + a["merged_by_dedupe_key"]
    text = (ROOT / "docs" / "WXPREDICT.md").read_text()
    for figure in ("863", "1,092", "246,108", "2,506", "15.9%", "2,613", "16.6%", "107", "13,113", "74,386"):
        assert figure in text, figure
