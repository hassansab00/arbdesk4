import hashlib

import pytest

import data_integrity


def test_canonical_hash_is_independent_of_object_key_order():
    left = data_integrity.canonical_bytes({"city": "london", "temp": 21})
    right = data_integrity.canonical_bytes({"temp": 21, "city": "london"})
    assert left == right


def test_fingerprint_pages_without_loading_the_archive(monkeypatch):
    rows = [{"signal_id": 1, "fired_at": "2026-09-01T00:00:00Z"},
            {"signal_id": 2, "fired_at": "2026-09-01T01:00:00Z"},
            {"signal_id": 3, "fired_at": "2026-09-01T02:00:00Z"}]

    def fake_rest(_table, params):
        p = dict(params)
        start = int(p["offset"])
        return rows[start:start + 2]

    monkeypatch.setattr(data_integrity, "rest", fake_rest)
    result = data_integrity.fingerprint_dataset(
        "signals", "2026-09-02T00:00:00Z", page_size=500
    )
    expected = hashlib.sha256()
    for row in rows:
        expected.update(data_integrity.canonical_bytes(row))
    assert result["row_count"] == 3
    assert result["sha256"] == expected.hexdigest()
    assert result["scope"]["cutoff"] == "2026-09-02T00:00:00Z"


def test_repeated_page_is_rejected(monkeypatch):
    monkeypatch.setattr(data_integrity, "rest", lambda *_args: [{"signal_id": 1}])
    with pytest.raises(RuntimeError, match="pagination did not advance"):
        data_integrity.fingerprint_dataset("signals", "2026-09-02T00:00:00Z")


def test_unknown_dataset_is_rejected():
    with pytest.raises(ValueError, match="Unknown proprietary dataset"):
        data_integrity.fingerprint_dataset("temporary_table", "2026-09-02T00:00:00Z")


def test_migration_never_rewrites_or_deletes_source_rows():
    migration = open(
        "supabase/migrations/20260912200000_proprietary_data_safeguards.sql"
    ).read().lower()
    for table in data_integrity.DATASETS:
        assert f"delete from public.{table}" not in migration
        assert f"update public.{table}" not in migration
        assert f"truncate public.{table}" not in migration
    assert "proprietary_fact_immutable" in migration
    assert "on conflict do nothing" in migration
