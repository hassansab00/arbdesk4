"""The intraday pipeline banks what the queue just confirmed (plan v2.2 P4.7).

fact_band_outcome, which /predictive's hit/miss record reads, was written only
by pipeline_daily at 04:36Z; a US day ends 04:00-07:00Z, so it waited a day.
`databank.py --bands-only` runs the same bank_bands() in the intraday pipeline
after the confirmation queue. These tests hold it to: the same whole-ladder
rule, nothing but the ladder record written, the already-banked read limited
to the markets' own dates (it read all 22,022 rows every run), the observed
maxima read only when a ladder is bankable, and its own log row.
"""
import argparse

import databank

MARKET = "30000000-0000-0000-0000-000000000001"


def _ladder(n=3, winner=1, market=MARKET):
    bands, res = [], []
    for i in range(n):
        bid = f"20000000-0000-0000-0000-0000000000{i:02d}"
        bands.append({"band_id": bid, "market_id": market, "band_lo": 20 + i, "band_hi": 21 + i,
                      "open_low": False, "open_high": False})
        res.append({"band_id": bid, "market_id": market, "settled_yes": i == winner,
                    "resolution_state": "confirmed", "confirmed_at": "now"})
    return bands, res


def _wire(monkeypatch, bands, res, market_state="confirmed", frozen=()):
    calls = {"reads": [], "writes": [], "logs": [], "rpcs": [], "observed_loads": 0}

    def rows(path, params=None):
        calls["reads"].append((path, list(params.items()) if isinstance(params, dict) else list(params or [])))
        if path == "fact_band_outcome":
            return [{"band_id": b} for b in frozen]
        if path == "markets":
            return [{"market_id": MARKET, "city_key": "nyc", "resolution_date": "2026-09-29"}]
        if path == "v_venue_market_resolution":
            return [{"market_id": MARKET, "resolution_state": market_state, "city_key": "nyc",
                     "resolution_date": "2026-09-29", "confirmed_at": "now"}][:0 if params and any(
                         k == "confirmed_at" for k, _ in (params.items() if isinstance(params, dict) else params)) else 1]
        if path == "v_venue_band_resolution":
            return res
        if path == "v_canonical_bands":
            return bands
        if path == "band_probabilities":
            return [{"band_id": b["band_id"], "raw_prob": 0.3, "computed_at": "now"} for b in bands]
        if path == "v_latest_edge":
            return []
        raise AssertionError(path)

    def observed(span):
        calls["observed_loads"] += 1
        return {("nyc", "2026-09-29"): {"max_c": 21.4, "source": "station:KNYC"}}

    monkeypatch.setattr(databank, "rest", rows)
    monkeypatch.setattr(databank, "rest_all", lambda path, params=None, **kw: rows(path, params))
    monkeypatch.setattr(databank, "observed_with_fallback", observed)
    monkeypatch.setattr(databank, "upsert", lambda t, r, k: calls["writes"].append((t, len(r), k)) or len(r))
    monkeypatch.setattr(databank, "rpc", lambda fn, args=None: calls["rpcs"].append(fn) or 2)
    monkeypatch.setattr(databank, "log_run", lambda *a: calls["logs"].append(a))
    monkeypatch.setattr(databank, "bank_forecasts", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("bands only: forecast outcomes stay with the daily run")))
    monkeypatch.setattr(databank, "bank_signals", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("bands only: signal outcomes stay with the daily run")))
    return calls


def _args(**kw):
    return argparse.Namespace(**dict({"days": 3, "proof_days": 3, "force": False, "bands_only": True}, **kw))


def test_a_confirmed_ladder_is_banked_whole_and_nothing_else_is_written(monkeypatch):
    bands, res = _ladder()
    calls = _wire(monkeypatch, bands, res)
    d = databank.bank_bands_only(_args())
    assert calls["writes"] == [("fact_band_outcome", 3, "band_id")]
    assert calls["rpcs"] == ["bank_checkpoint_outcomes"]
    assert d["bands"] == 3 and d["city_days"] == 1 and d["days"] == ["2026-09-29"]
    assert calls["logs"][0][:3] == ("databank_bands", "ok", 5)
    assert calls["observed_loads"] == 1


def test_an_incomplete_ladder_is_not_banked_and_no_observation_is_read(monkeypatch):
    bands, res = _ladder()
    res[1]["resolution_state"] = "pending"
    calls = _wire(monkeypatch, bands, res, market_state="partial")
    d = databank.bank_bands_only(_args())
    assert calls["writes"] == [] and d["bands"] == 0
    assert calls["observed_loads"] == 0, "the ~1.3 s station read is only for a bankable ladder"


def test_a_second_run_banks_nothing_new(monkeypatch):
    bands, res = _ladder()
    calls = _wire(monkeypatch, bands, res, frozen=[b["band_id"] for b in bands])
    d = databank.bank_bands_only(_args())
    assert calls["writes"] == [] and d["bands"] == 0


def test_the_already_banked_read_is_the_markets_dates_not_the_whole_table(monkeypatch):
    bands, res = _ladder()
    calls = _wire(monkeypatch, bands, res)
    databank.bank_bands_only(_args())
    reads = [p for path, p in calls["reads"] if path == "fact_band_outcome"]
    assert reads == [[("select", "band_id"), ("for_date", "gte.2026-09-29")]]


def test_the_cli_routes_bands_only_to_its_own_pass(monkeypatch):
    seen = {}
    monkeypatch.setattr(databank, "bank_bands_only", lambda args: seen.setdefault("args", args))
    monkeypatch.setattr("sys.argv", ["databank.py", "--bands-only", "--days", "3", "--proof-days", "3"])
    databank.main()
    assert seen["args"].bands_only and seen["args"].days == 3 and seen["args"].proof_days == 3
