"""THE MIRROR HAS IT FIRST (plan v2 P1.6 phase 1, 28 Sep).

Hassan, 28 Sep: "yes keep trades in te mrror to, proceed."

scripts/mirror_to_repo.py copies trades_observed (by ingested_at) and
weather_forecast_features (by captured_at) into data/mirror, one whole UTC day
a night - AFTER the archive's prune, in the same workflow. With trades kept one
day, a trade ingested later than it traded could leave before the mirror had
it: on 28 Sep, 6,137 trades ingested that day had traded before the next
night's cutoff. They were in the archive file, in one copy where every other
trade has two.

So a dataset marked `mirror_first` is not archived at all while the committed
mirror manifest is behind the midnight its prune relies on, and a dataset with
`mirrored_on` exports - and prune_trades deletes - only rows that column puts
before the cutoff's UTC midnight.
"""

import datetime as dt
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import archive_observations as ao  # noqa: E402
import mirror_to_repo as mirror  # noqa: E402

UTC = dt.timezone.utc
MIRRORED = sorted(n for n, s in ao.TABLES.items() if s.get("mirror_first"))


def test_the_datasets_the_mirror_copies_by_day_are_marked():
    assert MIRRORED == ["correlation", "forecast_features", "forecast_models", "probabilities", "trades"]
    assert ao.TABLES["trades"]["mirrored_on"] == "ingested_at"


# A dataset that refuses (rather than holds back) a row the mirror has not had:
# the file its prune is written in, and the column the mirror copies it by.
REFUSES_UNMIRRORED = {
    "forecast_features": ("ad4_93_prune_forecast_features.sql", "captured_at"),
    "forecast_models": ("ad4_95_prune_forecast_models.sql", "observed_at"),
    "probabilities": ("ad4_96_prune_band_probabilities.sql", "computed_at"),
    "correlation": ("ad4_prune_city_correlation.sql", "computed_at"),
}


@pytest.mark.parametrize("name", MIRRORED)
def test_the_mirror_copies_that_table_by_the_column_the_prune_bounds(name):
    """If the mirror copied by another column, the bound would protect
    nothing."""
    spec = ao.TABLES[name]
    m = mirror.TABLES[spec["table"]]
    assert m["kind"] == "append", f"{spec['table']} is not mirrored a day at a time"
    if spec.get("mirrored_on"):
        assert m["time"] == spec["mirrored_on"]
        assert not spec["cutoff_is_date"], "the midnight is taken from a timestamp cutoff"
        sql = (ROOT / "sql" / "ad4_65_prune_trades.sql").read_text(encoding="utf-8")
        assert sql.count(f"{spec['mirrored_on']} < v_mirrored") == 4, (
            "the count, the presence guard, the kept count and the delete must all take "
            "the mirror's bound")
        assert "not (traded_at < v_before and ingested_at < v_mirrored)" in sql, "the kept count"
    else:
        sql_file, column = REFUSES_UNMIRRORED[name]
        assert m["time"] == column
        sql = (ROOT / "sql" / sql_file).read_text(encoding="utf-8")
        assert f"{column} >= v_unmirrored" in sql


def test_the_export_takes_only_what_the_mirror_has(monkeypatch):
    seen = []
    monkeypatch.setattr(ao, "rest", lambda relation, params: seen.append(params) or [])
    cutoff = dt.datetime(2026, 9, 28, 2, 41, 7, tzinfo=UTC)
    ao.export_cold(ao.TABLES["trades"], cutoff)
    assert ("traded_at", f"lt.{cutoff.isoformat()}") in seen[0]
    assert ("ingested_at", "lt.2026-09-28T00:00:00+00:00") in seen[0]


def test_a_dataset_without_the_bound_gets_no_extra_filter(monkeypatch):
    seen = []
    monkeypatch.setattr(ao, "rest", lambda relation, params: seen.append(params) or [])
    ao.export_cold(ao.TABLES["book_evidence"], dt.datetime(2026, 9, 28, 2, 41, tzinfo=UTC))
    assert not any(k == "ingested_at" for k, _ in seen[0])


def test_the_midnight_each_prune_relies_on():
    cutoff = dt.datetime(2026, 9, 28, 2, 41, tzinfo=UTC)
    now = dt.datetime(2026, 9, 29, 2, 41, tzinfo=UTC)
    assert ao.mirror_needs(ao.TABLES["trades"], cutoff, now) == dt.datetime(2026, 9, 28, tzinfo=UTC)
    assert ao.mirror_needs(ao.TABLES["forecast_features"], cutoff.date(), now) == dt.datetime(2026, 9, 28, tzinfo=UTC)
    assert ao.mirror_needs(ao.TABLES["forecast_models"], cutoff.date(), now) == dt.datetime(2026, 9, 28, tzinfo=UTC)
    assert ao.mirror_needs(ao.TABLES["probabilities"], cutoff.date(), now) == dt.datetime(2026, 9, 28, tzinfo=UTC)


def test_the_manifest_is_read_from_the_checkout(monkeypatch, tmp_path):
    (tmp_path / "data" / "mirror").mkdir(parents=True)
    (tmp_path / "data" / "mirror" / "manifest.json").write_text(json.dumps(
        {"tables": {"trades_observed": {"kind": "append", "through": "2026-09-28T00:00:00+00:00"}}}))
    monkeypatch.setattr(ao, "_root", lambda: str(tmp_path))
    assert ao.mirrored_through("trades_observed") == dt.datetime(2026, 9, 28, tzinfo=UTC)
    assert ao.mirrored_through("weather_forecast_features") is None


def test_no_manifest_means_not_mirrored(monkeypatch, tmp_path):
    monkeypatch.setattr(ao, "_root", lambda: str(tmp_path))
    assert ao.mirrored_through("trades_observed") is None


def _export(monkeypatch, name, through):
    logged = []
    monkeypatch.setattr(ao, "effective_keep_days", lambda spec, override: (spec["keep_days"], {"over": False}))
    monkeypatch.setattr(ao, "refresh_feature_cache", lambda *a, **k: None)
    monkeypatch.setattr(ao, "finish_stranded_export", lambda *a, **k: None)
    monkeypatch.setattr(ao, "mirrored_through", lambda table: through)
    monkeypatch.setattr(ao, "log_run", lambda job, status, rows, detail: logged.append((status, detail)))
    reads = []
    monkeypatch.setattr(ao, "export_cold", lambda spec, cutoff: reads.append(cutoff) or (b"", 0, None, None))
    rc = ao.export_one(ao.TABLES[name], name, types.SimpleNamespace(keep_days=None))
    return rc, reads, logged


@pytest.mark.parametrize("name", MIRRORED)
def test_a_mirror_that_is_behind_stops_the_dataset(monkeypatch, capsys, name):
    """Last night's mirror did not reach the midnight: nothing is read,
    written or deleted, and the log says why."""
    yesterday = ao.utc_midnight(dt.datetime.now(UTC)) - dt.timedelta(days=1)
    rc, reads, logged = _export(monkeypatch, name, yesterday - dt.timedelta(days=1))
    assert rc == 1
    assert reads == [], "the export ran with the mirror behind"
    assert logged and logged[0][0] == "attention"
    assert logged[0][1]["mirror_needed"] == yesterday.isoformat()
    assert "THE MIRROR IS BEHIND" in capsys.readouterr().err


@pytest.mark.parametrize("name", MIRRORED)
def test_no_manifest_entry_stops_the_dataset(monkeypatch, name):
    rc, reads, logged = _export(monkeypatch, name, None)
    assert rc == 1 and reads == []
    assert logged[0][1]["mirror_through"] is None


@pytest.mark.parametrize("name", MIRRORED)
def test_a_mirror_that_has_caught_up_lets_the_export_run(monkeypatch, name):
    today = ao.utc_midnight(dt.datetime.now(UTC))
    rc, reads, logged = _export(monkeypatch, name, today)
    assert rc == 0 and len(reads) == 1
    assert logged[0][0] == "ok"
