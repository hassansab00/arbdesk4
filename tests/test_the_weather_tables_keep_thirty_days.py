"""The weather tables keep 30 days (plan v2 P1.6 phase 2, step 5 part (b), 29 Sep).

Observations keep 32 days, both forecast tables 30, wanted and floor alike:
steps 1-4, 6 and 5 part (a) moved every longer reader onto the repository or
onto a cache the prunes refuse to outrun. prune_observations refuses under 32,
not 30: the climb profile reads the 30 whole local days before today, the
oldest can begin 14 hours before its UTC date, and the prune cuts part-way
through a day. Its migration changes that one line and nothing else of the
body live since 20260929180000.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import archive_observations as ao  # noqa: E402

MIG = ROOT / "supabase" / "migrations"
BEFORE = MIG / "20260929180000_the_station_days_and_forecast_leads_outlast_the_keep.sql"
AFTER = MIG / "20260929210000_the_weather_tables_keep_thirty_days.sql"


def _prune_observations(path):
    text = path.read_text(encoding="utf-8")
    i = text.index("create or replace function public.prune_observations(")
    j = text.index("to service_role;", i)
    return text[i:j]


def test_the_keeps():
    assert (ao.TABLES["observations"]["keep_days"], ao.TABLES["observations"]["min_keep_days"]) == (32, 32)
    assert (ao.TABLES["forecasts"]["keep_days"], ao.TABLES["forecasts"]["min_keep_days"]) == (30, 30)
    # A week since 7 Oct (WXPredict build 2.A): the hit forecasts and the
    # ingest take each table's own oldest day (tests/database/model-forecasts-week.cjs).
    # Two days since 8 Oct (Fresh Supabase): every live reader takes yesterday on.
    assert (ao.TABLES["forecast_models"]["keep_days"], ao.TABLES["forecast_models"]["min_keep_days"]) == (2, 2)


WEEK = MIG / "20261007210000_each_models_forecasts_keep_a_week.sql"
LIVE_BEFORE = MIG / "20260929160000_the_evidence_outlasts_the_weather_tables.sql"


def _prune_models_without_floor(text):
    """prune_forecast_models' body with its floor block (the comment above it
    and the if ... end if) taken out."""
    i = text.index("create or replace function public.prune_forecast_models(")
    body = text[i:text.index("$function$;", i)]
    start = body.index("begin\n") + len("begin\n")
    end = body.index("end if;", body.index("p_keep_days <")) + len("end if;")
    return body[:start] + body[end:]


def test_the_model_forecast_prune_changes_its_floor_and_nothing_else():
    week = WEEK.read_text(encoding="utf-8")
    assert re.search(r"if p_keep_days < 7 then", week)
    assert _prune_models_without_floor(week) == _prune_models_without_floor(LIVE_BEFORE.read_text(encoding="utf-8")), (
        "the migration changed more of prune_forecast_models than its floor")



TWO = MIG / "20261008190000_the_prices_keep_three_days_and_models_two.sql"


def test_the_two_day_floor_changes_the_floor_and_nothing_else():
    two, week = TWO.read_text(encoding="utf-8"), WEEK.read_text(encoding="utf-8")
    assert re.search(r"if p_keep_days < 2 then", two)
    assert _prune_models_without_floor(two) == _prune_models_without_floor(week), (
        "the migration changed more of prune_forecast_models than its floor")
    repo = (ROOT / "sql" / "ad4_95_prune_forecast_models.sql").read_text(encoding="utf-8")
    assert "p_keep_days < 2 then" in repo and "p_keep_days < 7" not in repo and "p_keep_days < 30" not in repo


def test_the_observation_prune_changes_its_floor_and_nothing_else():
    before, after = _prune_observations(BEFORE), _prune_observations(AFTER)
    assert re.search(r"p_keep_days < 30\b", before) and re.search(r"p_keep_days < 32\b", after)
    strip = lambda s: re.sub(r"'keep_days must be at least \d+ - [^']*'", "MSG",
                             re.sub(r"p_keep_days < \d+", "FLOOR", s))
    assert strip(before) == strip(after), "the migration changed more of prune_observations than its floor"


def test_the_repo_body_refuses_under_32_too():
    sql = (ROOT / "sql" / "ad4_29_retention.sql").read_text(encoding="utf-8")
    assert "p_keep_days < 32" in sql and "p_keep_days < 30" not in sql
