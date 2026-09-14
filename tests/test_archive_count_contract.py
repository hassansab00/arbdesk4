"""Regression checks for count-bound, complete archive exports."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive_observations.py"
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260914110000_archive_prune_count_contract.sql"
)


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_archive_does_not_trust_a_short_postgrest_page():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "if len(rows) < PAGE" not in source


def test_archive_binds_prune_to_the_verified_row_count():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"p_expected_rows": n_rows' in source
    assert "ARCHIVE COUNT MISMATCH" in source


def test_committed_prunes_require_and_lock_the_expected_count():
    sql = _normalized(MIGRATION)
    for table in ("weather_observations", "weather_forecasts"):
        assert f"lock table public.{table} in share row exclusive mode" in sql
    assert sql.count("p_expected_rows is required for a committed prune") == 2
    assert sql.count("v_doomed <> p_expected_rows") == 2
