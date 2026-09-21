"""Leads 1 to 7 read `stale` on every city, and the evidence was not the problem.

WHAT THE BOARD SAID on 2026-09-21. derived_model_promotion, 392 rows:

    lead 0   48 shadow, 1 stale     up to 1 settled day
    lead 1   49 stale               0 settled days
    lead 2   49 stale               0 settled days
    ...
    lead 7   49 stale               0 settled days

`stale` has one meaning in this module and the board prints it: "either the
fit or its forward predictions stopped arriving". Nothing had stopped. Two
separate defects produced that screen, and they have to be fixed together
because either one alone leaves it saying the same wrong thing.

ONE - THE TABLE COULD NOT BE REWRITTEN. It was written through common.upsert,
which sends `resolution=ignore-duplicates`. That is exactly right for a fact:
an observation or a fill must never be rewritten by a later run. It is exactly
wrong for a VERDICT, where the row is not an event but the current answer to a
standing question. Measured:

    rows in the table                                      392
    distinct computed_at                    all 2026-09-20 09:18
    rows the 09-21 run computed, and the database ignored   384

So the 09-20 answer was permanent. Days settled, the script recomputed, and
PostgREST dropped every corrected row on the floor. A promotion gate that
cannot change its mind is not a gate.

TWO - A YOUNG HORIZON WAS CALLED A STALE ONE. decide() returned "stale"
whenever nothing was scorable, and at lead 7 nothing CAN be scorable until
seven days after the forward job's first run. Measured the same morning, the
oldest prediction at each lead:

    lead 0  2026-09-19   ->  98 settled city-days
    lead 1  2026-09-20   ->  24 settled city-days
    lead 2  2026-09-21   ->   0, settles 09-22
    lead 7  2026-09-26   ->   0, settles 09-27

Those leads were three days old. They had not stopped arriving; they had not
come due. The two states want opposite responses - wait, versus go and find
out why the forecast job is not writing that lead - and both arrived as the
same word with the same sentence under it.

And THREE, found while fixing the first two: a (city, lead) the forecast job
never wrote was absent from the table entirely, and an absent row is
indistinguishable from a city nobody looked at.
"""

import datetime as dt
import sys

import common
import model_promotion as mp


# ---------------------------------------------------------------------------
# one - the write
# ---------------------------------------------------------------------------
def capture_prefer(monkeypatch):
    seen = {}

    def fake_post_rows(table, rows, headers, params, chunk, verb):
        seen["table"], seen["prefer"] = table, headers.get("Prefer", "")
        seen["params"], seen["verb"], seen["rows"] = params, verb, rows
        return len(rows)

    monkeypatch.setattr(common, "_post_rows", fake_post_rows)
    monkeypatch.setattr(common, "_headers", lambda: {})
    return seen


def test_upsert_still_ignores_duplicates(monkeypatch):
    """Unchanged, and it must stay unchanged. Every fact table on this desk
    depends on a second sighting of the same event not rewriting the first."""
    seen = capture_prefer(monkeypatch)
    common.upsert("weather_observations", [{"a": 1}], "city_key,valid_at,source")
    assert "resolution=ignore-duplicates" in seen["prefer"]
    assert "merge-duplicates" not in seen["prefer"]


def test_upsert_replace_overwrites_on_the_key(monkeypatch):
    seen = capture_prefer(monkeypatch)
    common.upsert_replace("derived_model_promotion", [{"a": 1}],
                          "city_key,lead_days,target")
    assert "resolution=merge-duplicates" in seen["prefer"]
    assert seen["params"] == {"on_conflict": "city_key,lead_days,target"}


def test_neither_helper_writes_without_an_on_conflict_target():
    """A merge with no key is an append that pretends to be a merge."""
    import inspect
    for fn in (common.upsert, common.upsert_replace):
        params = inspect.signature(fn).parameters
        assert "on_conflict" in params
        assert params["on_conflict"].default is inspect.Parameter.empty


def test_an_empty_write_is_a_no_op_not_a_request(monkeypatch):
    seen = capture_prefer(monkeypatch)
    assert common.upsert_replace("t", [], "k") == 0
    assert seen == {}


def test_the_promotion_table_is_written_with_the_replacing_write():
    """The table this was written for. Reading the source rather than the
    behaviour because the defect WAS the word - one call, one helper name, and
    every corrected verdict for two days went into a bin."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "model_promotion.py").read_text(encoding="utf-8")
    assert 'upsert_replace("derived_model_promotion"' in src
    assert 'upsert("derived_model_promotion"' not in src


# ---------------------------------------------------------------------------
# two - a young horizon is not a stale one
# ---------------------------------------------------------------------------
NO_DROPS = [("has_an_anchor", 0, ""), ("attributable_to_a_fit", 0, ""),
            ("public_forecast_recorded", 0, "")]


def test_a_lead_waiting_on_a_future_day_is_in_shadow_not_stale():
    state, reasons = mp.decide(None, [], NO_DROPS, 30,
                               pending={"unsettled": 4, "first_pending": "2026-09-26",
                                        "last_pending": "2026-09-29"})
    assert state == "shadow", (
        "a lead whose oldest prediction is still in the future has not stopped "
        "arriving - it has not come due")
    detail = [r["detail"] for r in reasons if r["rule"] == "the_horizon_has_settled"]
    assert detail, "the reason has to say what it is waiting for"
    assert "2026-09-26" in detail[0]


def test_the_waiting_reason_names_the_day_it_can_first_be_scored():
    """A date, not an adjective. 'no settled forward day' is true of a lead
    published this morning and of one whose job died three weeks ago."""
    detail = mp.waiting_detail({"unsettled": 4, "first_pending": "2026-09-26"}, 30)
    assert "2026-09-27" in detail, "a prediction for the 26th is scorable on the 27th"
    assert "2026-10-26" in detail, "and 30 settled days cannot arrive before this"


def test_a_lead_with_no_prediction_at_all_is_still_stale():
    """The complement, and the one case the old word was right about. Nothing
    outstanding and nothing scored means the forecast job is not writing this
    lead, which is a defect to go and find, not a wait."""
    state, reasons = mp.decide(None, [], NO_DROPS, 30, pending={"unsettled": 0})
    assert state == "stale"
    rule = [r for r in reasons if r["rule"] == "anything_to_score"]
    assert rule and "not writing it" in rule[0]["detail"]


def test_a_genuinely_stale_input_still_beats_a_pending_horizon():
    """STALE BEATS EVERYTHING was the existing rule and it still holds: a fit
    that stopped being refitted is unjudgeable however many days are pending."""
    state, _ = mp.decide(None, [("fit_is_current", "the fit is 40 days old")],
                         NO_DROPS, 30,
                         pending={"unsettled": 4, "first_pending": "2026-09-26"})
    assert state == "stale"


def test_scored_days_are_unaffected_by_the_pending_count():
    """Once a lead has settled days the verdict comes from the evidence, and
    the outstanding predictions beyond it say nothing about it."""
    rows = [{"day": f"2026-08-{d:02d}", "observed": 20.0, "predicted": 20.0,
             "public": 22.0, "persistence": 23.0} for d in range(1, 31)]
    scored = mp.score(rows, 30, draws=200)
    state, _ = mp.decide(scored, [], NO_DROPS, 30,
                         pending={"unsettled": 7, "first_pending": "2026-09-26"})
    assert state == "promoted"


def test_assemble_records_which_day_a_lead_is_waiting_on():
    """The count alone cannot name a date, and the date is the whole point."""
    preds = [{"city_key": "nyc", "for_date": "2026-09-26", "run_at": "2026-09-19",
              "lead_days": 7, "predicted_max_c": 20.0, "nws_max_c": 20.0,
              "prev_source": "observed", "model_version": "v",
              "predicted_at": "2026-09-19"},
             {"city_key": "nyc", "for_date": "2026-09-28", "run_at": "2026-09-21",
              "lead_days": 7, "predicted_max_c": 21.0, "nws_max_c": 21.0,
              "prev_source": "observed", "model_version": "v",
              "predicted_at": "2026-09-21"}]
    _, drops, _ = mp.assemble(preds, [], [], today=dt.date(2026, 9, 21))
    d = drops[("nyc", 7)]
    assert d["unsettled"] == 2
    assert d["first_pending"] == "2026-09-26"
    assert d["last_pending"] == "2026-09-28"


# ---------------------------------------------------------------------------
# three - every active city, every lead
# ---------------------------------------------------------------------------
def promotion_run(monkeypatch, capsys, active, preds, existing=()):
    """Run model_promotion.main() against fakes and return (rows, stdout)."""
    written = []

    def fake_rest_all(path, params=None, **kw):
        if path == "derived_model_forecast":
            return list(preds)
        if path == "derived_weather_model":
            return [{"city_key": c, "target": "max_c",
                     "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                     "model_version": "v"} for c in active]
        if path == "derived_model_promotion":
            return list(existing)
        return []

    monkeypatch.setattr(mp, "rest_all", fake_rest_all)
    monkeypatch.setattr(mp, "active_city_keys", lambda: set(active))
    monkeypatch.setattr(mp, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(mp, "upsert_replace",
                        lambda table, rows, key: written.extend(rows) or len(rows))
    monkeypatch.setattr(sys, "argv", ["model_promotion"])
    mp.main()
    return written, capsys.readouterr().out


def one_prediction(city, lead, for_date, run_at):
    return {"city_key": city, "for_date": for_date, "run_at": run_at,
            "lead_days": lead, "predicted_max_c": 20.0, "nws_max_c": 20.0,
            "prev_source": "observed", "model_version": "v",
            "predicted_at": dt.datetime.now(dt.timezone.utc).isoformat()}


def test_every_active_city_gets_a_verdict_at_every_lead(monkeypatch, capsys):
    """48 cities on the board, 8 leads, 384 rows - including the leads the
    forecast job wrote nothing for. An absent row says nothing at all."""
    active = [f"city{i:02d}" for i in range(48)]
    preds = [one_prediction("city00", 0, "2026-09-19", "2026-09-19")]
    rows, out = promotion_run(monkeypatch, capsys, active, preds)

    assert len(rows) == len(active) * len(mp.EXPECTED_LEADS)
    assert {r["city_key"] for r in rows} == set(active)
    for city in active:
        assert {r["lead_days"] for r in rows if r["city_key"] == city} \
            == set(mp.EXPECTED_LEADS), f"{city} is missing a lead"
    assert "coverage across 48 active city/cities" in out


def test_the_coverage_report_separates_scored_leads_from_waiting_ones(monkeypatch, capsys):
    """The state tally cannot show this: zero scored cities and forty-eight
    waiting ones both land under shadow."""
    active = ["nyc", "london"]
    preds = [one_prediction(c, 7, "2030-01-01", "2029-12-25") for c in active]
    _, out = promotion_run(monkeypatch, capsys, active, preds)
    assert "lead 7: 2 city/cities with a verdict, 0 with a settled day" in out


def test_a_retired_city_with_a_frozen_verdict_is_corrected_not_left(monkeypatch, capsys):
    """load() drops retired cities before anything is scored, so their rows
    were never written again and kept their old computed_at for ever - which
    is what per-city freshness then reported the whole table on. Nothing is
    deleted; the row is rewritten to say what is true of it."""
    active = ["nyc"]
    preds = [one_prediction("nyc", 0, "2026-09-19", "2026-09-19")]
    existing = [{"city_key": "dc", "lead_days": 3, "target": "max_c"}]
    rows, _ = promotion_run(monkeypatch, capsys, active, preds, existing)

    dc = [r for r in rows if r["city_key"] == "dc"]
    assert len(dc) == 1, "the retired row has to be rewritten, not dropped"
    assert dc[0]["state"] == "stale"
    assert dc[0]["computed_at"] is not None
    assert any(x["rule"] == "city_is_active" for x in dc[0]["reasons"])
    assert dc[0]["n_days"] is None


def test_a_retired_city_is_never_scored_back_onto_the_board(monkeypatch, capsys):
    """The correction row must not become a route back in: retired stays
    retired, and `stale` is the only state it can hold."""
    active = ["nyc"]
    preds = [one_prediction("nyc", 0, "2026-09-19", "2026-09-19")]
    existing = [{"city_key": "dc", "lead_days": lead, "target": "max_c"}
                for lead in mp.EXPECTED_LEADS]
    rows, _ = promotion_run(monkeypatch, capsys, active, preds, existing)
    assert all(r["state"] == "stale" for r in rows if r["city_key"] == "dc")
    assert all(r["model_version"] is None for r in rows if r["city_key"] == "dc")
