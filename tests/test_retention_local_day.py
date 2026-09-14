"""Regression checks for the observation archive's local-day safety guard."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "sql" / "ad4_29_retention.sql"
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260914100000_observation_prune_local_day_guard.sql"
)


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_prune_guard_uses_the_feature_cache_local_day_key():
    """The guard must group rows exactly like v_city_day_features does."""
    for path in (CANONICAL, MIGRATION):
        sql = _normalized(path)
        assert "join public.cities c on c.city_key = o.city_key" in sql or (
            "join cities c on c.city_key = o.city_key" in sql
        )
        assert (
            "o.valid_at at time zone coalesce(c.timezone, 'utc')"
            in sql
        )


def test_prune_guard_no_longer_keys_cached_days_in_utc():
    migration = _normalized(MIGRATION)
    candidate_days = migration.split("select count(*) into v_uncovered", 1)[1]
    candidate_days = candidate_days.split("if v_uncovered > 0", 1)[0]
    assert "o.valid_at at time zone 'utc'" not in candidate_days
