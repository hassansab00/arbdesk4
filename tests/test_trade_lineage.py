"""A trade has to carry the decision that produced it.

Measured on the live desk, 2026-09-19: 65 fills, and 0 of them with
forecast_version, calibration_version, cost_version or fill_quality. Zero rows
in ledger. The columns have existed since the first paper migration; nothing
ever wrote them, because a trade is built from the ORDER and an order does not
know what the desk believed when it decided.

Every one of those inputs is a moving target. band_probabilities is rewritten
by every pricing run, calibration is refitted from settled outcomes, cost
parameters change when the fee model is edited, and the book moves by the
second. By the time a trade settles, none of the rows that produced it still
say what they said - so a post-mortem is guesswork and "which forecast version
made money" is unanswerable.

The SQL half - the trigger that stamps a trade, the ledger links and the
write-once guard - runs against a real Postgres in
tests/database/paper-contracts.cjs. This is the half that freezes the snapshot
in the first place.
"""

import re
from pathlib import Path

import pytest

import signal_engine as se

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260919180000_trade_decision_lineage.sql"

# The plan's list, verbatim: raw probability, calibrated probability,
# executable price, model centre, sigma, confidence, and all four versions.
REQUIRED = {"raw_prob", "calibrated_prob", "executable_price", "model_centre_c",
            "sigma_c", "confidence", "forecast_version", "calibration_version",
            "cost_version"}


def _prob_row(**over):
    row = {
        "band_id": "b1", "prob_id": "p-1", "computed_at": "2026-09-19T12:00:00+00:00",
        "raw_prob": 0.3102, "calibrated_prob": 0.3400, "sigma_c": 1.83,
        "forecast_max_c": 19.2, "confidence": 0.4588, "lead_days": 1,
        "forecast_version": "11111111-1111-1111-1111-111111111111",
        "calibration_version": "22222222-2222-2222-2222-222222222222",
        "input_forecast_run": "2026-09-19T12:00:45+00:00",
    }
    row.update(over)
    return row


def test_the_snapshot_carries_every_field_a_post_mortem_needs():
    snap = se._snapshot(_prob_row(), 0.08, 0.98, "33333333-3333-3333-3333-333333333333")
    assert REQUIRED <= set(snap), sorted(REQUIRED - set(snap))
    assert snap["raw_prob"] == 0.3102
    assert snap["calibrated_prob"] == 0.3400, (
        "the raw and calibrated probabilities are different numbers and a trade "
        "made on one cannot be judged against the other")
    assert snap["model_centre_c"] == 19.2
    assert snap["sigma_c"] == 1.83


def test_the_executable_price_is_recorded_per_side():
    """A snapshot holding one price could not say which side it belonged to,
    and the two are not complements once the spread is real."""
    snap = se._snapshot(_prob_row(), 0.08, 0.98, None)
    assert snap["executable_price"] == {"YES": 0.08, "NO": 0.98}


def test_the_snapshot_names_the_exact_probability_row():
    """So it can be checked against band_probabilities for as long as that row
    survives - and stays readable after it does not."""
    snap = se._snapshot(_prob_row(), 0.08, 0.98, None)
    assert snap["prob_id"] == "p-1"
    assert snap["priced_at"] == "2026-09-19T12:00:00+00:00"


def test_an_unpriced_band_produces_an_empty_snapshot_not_invented_numbers():
    snap = se._snapshot({}, None, None, None)
    assert all(snap[k] is None for k in
               ("raw_prob", "calibrated_prob", "sigma_c", "forecast_version"))


# ---------------------------------------------------------------------------
# the cost version
# ---------------------------------------------------------------------------
def test_the_cost_version_is_the_uuid_not_the_label(monkeypatch):
    """paper_trades.cost_version is a uuid into cost_params. Writing the
    readable label there fails with 22P02, which is exactly how the first
    forecast_version went out."""
    monkeypatch.setattr(se, "rest", lambda *a, **k: [
        {"version_id": "68bdc8a9-88ec-43a3-8b47-1dd1b64e3132",
         "label": "polymarket_weather_2026_03"}])
    v = se._cost_version()
    assert v == "68bdc8a9-88ec-43a3-8b47-1dd1b64e3132"


def test_missing_cost_params_leaves_the_field_empty_rather_than_failing(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError('relation "cost_params" does not exist')
    monkeypatch.setattr(se, "rest", boom)
    assert se._cost_version() is None


# ---------------------------------------------------------------------------
# and it reaches the signal
# ---------------------------------------------------------------------------
def _opp(band_id="b1", **over):
    row = {
        "band_id": band_id, "side": "YES", "city_key": "london",
        "resolution_date": "2026-09-19", "band_lo": 20, "band_hi": 21,
        "open_low": False, "open_high": False, "band_label": "20C", "unit": "C",
        "model_prob": 0.34, "market_price": 0.08, "edge_net_pp": 9.0,
        "tradeable": True, "block_reason": None, "confidence": 0.62,
        "regime_label": "NORMAL", "market_state": "LIVE",
        "fillable_usd_5c": 250.0, "token_yes": "ty", "token_no": "tn", "spread": 0.02,
    }
    row.update(over)
    return row


@pytest.fixture
def board(monkeypatch):
    def go(probs):
        def fake_rest_all(path, params=None, **kw):
            if path == "v_opportunities":
                return [_opp(), _opp(side="NO", market_price=0.90)]
            if path == "v_latest_prob":
                return probs
            return []
        monkeypatch.setattr(se, "rest_all", fake_rest_all)
        monkeypatch.setattr(se, "rest", lambda *a, **k: [
            {"version_id": "33333333-3333-3333-3333-333333333333"}])
        return se._band_views()
    return go


def test_the_band_view_carries_the_snapshot(board):
    v = board([_prob_row()])[0]
    assert v.decision_snapshot["forecast_version"] == "11111111-1111-1111-1111-111111111111"
    assert v.decision_snapshot["cost_version"] == "33333333-3333-3333-3333-333333333333"
    assert v.decision_snapshot["executable_price"] == {"YES": 0.08, "NO": 0.90}


def test_the_signal_carries_it_at_a_shallow_path_keyed_by_band(board):
    """decision_inputs is the whole BandView and its shape changes every time a
    strategy needs a new field. The trigger that stamps a trade's lineage reads
    a fixed contract instead."""
    views = board([_prob_row()])
    bands = {v.band_id: v for v in views}
    sig = se._enrich(type("S", (), {"payload": {"band_ids": ["b1"]}, "band_id": "b1"})(),
                     bands, "cycle-1", __import__("datetime").datetime(2026, 9, 19))
    snap = sig.payload["decision_snapshot"]["b1"]
    assert REQUIRED <= set(snap)
    assert snap["forecast_version"] == "11111111-1111-1111-1111-111111111111"


def test_the_engine_reads_the_columns_the_snapshot_needs():
    """A narrowed select is a silently empty snapshot: the query succeeds, the
    fields are simply absent, and every trade goes out blank again."""
    src = (ROOT / "scripts" / "signal_engine.py").read_text()
    select = re.search(r'_optional\("v_latest_prob", \[(.*?)\]\)', src, re.S).group(1)
    for col in ("prob_id", "raw_prob", "calibrated_prob", "sigma_c",
                "confidence", "calibration_version", "forecast_version"):
        assert col in select, f"v_latest_prob is not asked for {col}"


# ---------------------------------------------------------------------------
# the SQL contract, in outline - the behaviour is in paper-contracts.cjs
# ---------------------------------------------------------------------------
def _sql():
    return "\n".join(l for l in MIGRATION.read_text().splitlines()
                     if not l.strip().startswith("--"))


def test_the_trade_is_stamped_by_the_trigger_not_by_three_python_hops():
    """plan -> order -> fill in Python is three places a field can be dropped,
    and the failure is invisible until someone asks a question months later."""
    sql = _sql()
    assert "arbdesk_private.record_paper_trade" in sql
    for col in ("fill_quality", "forecast_version", "calibration_version", "cost_version"):
        assert col in sql, f"the trigger does not stamp {col}"


def test_a_bad_version_cannot_take_the_fill_down_with_it():
    """The trade is the money and the version is the paperwork."""
    assert "nullif(snap->>'forecast_version', '')::uuid" in _sql()


def test_the_guard_covers_every_field_that_is_the_decision():
    sql = _sql()
    guard = sql[sql.index("lineage_is_write_once"):]
    for col in ("forecast_version", "calibration_version", "cost_version",
                "fill_quality", "signal_id", "avg_fill_price", "quoted_price"):
        assert f"'{col}'" in guard, f"{col} can be rewritten after the fact"


def test_the_backfill_does_not_invent_what_was_never_recorded():
    """Nothing recorded calibration or cost at the time, and stamping today's
    values onto a trade made a week ago would be a lie with a uuid on it.

    Scoped to what each UPDATE SETS, not to where it appears in the file: the
    ledger backfill below it legitimately COPIES both columns off the trade,
    where they are null, and a positional read called that an invention.
    """
    sql = _sql()
    updates = re.findall(r"update public\.paper_trades\b(.*?);", sql, re.S)
    assert updates, "no backfill found at all"
    for u in updates:
        sets = u[u.index(" set "):] if " set " in u else u
        head = sets.split("from")[0]
        for invented in ("calibration_version", "cost_version"):
            assert f"{invented} =" not in head and f"{invented}=" not in head, (
                f"the backfill writes {invented}, which nothing recorded at the time")
