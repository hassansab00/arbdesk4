"""Skill counts days, not models (plan v2 P3.5).

by_lead - the number every sigma is priced from - took every model's row for a
date, so a day two models forecast counted twice. Measured 23 Sep: the newest
derived_forecast_skill n_days ran 1.61-1.78x the distinct settled dates in
fact_forecast_outcome at leads 1-7 (1.08x at lead 0). One row per (date,
lead) now: the newest run, whichever model - the row pricing takes.
"""
import measure_skill as ms


def test_one_row_per_date_and_lead_the_newest_whichever_model():
    rows = [
        {"for_date": "2026-09-20", "lead_days": 1, "model": "nws", "run_at": "2026-09-19T06:00Z", "forecast_max_c": 20.0},
        {"for_date": "2026-09-20", "lead_days": 1, "model": "open_meteo_forecast", "run_at": "2026-09-19T12:00Z", "forecast_max_c": 21.0},
        {"for_date": "2026-09-20", "lead_days": 2, "model": "nws", "run_at": "2026-09-18T06:00Z", "forecast_max_c": 19.0},
        {"for_date": "2026-09-21", "lead_days": 1, "model": "nws", "run_at": "2026-09-20T06:00Z", "forecast_max_c": 22.0},
    ]
    got = {(r["for_date"], r["lead_days"]): r for r in ms.newest_per_date_lead(rows)}
    assert len(got) == 3
    assert got[("2026-09-20", 1)]["model"] == "open_meteo_forecast"


def test_the_priced_number_uses_it_and_the_per_model_breakdown_does_not():
    src = open(ms.__file__).read()
    loop = src[src.index("by_lead = defaultdict(list)"):src.index("for lead, errs in sorted(by_lead.items()):")]
    assert "newest_per_date_lead(" in loop
    assert loop.index("by_model_lead[(f[\"model\"]") < loop.index("newest_per_date_lead(")
