import gzip
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import private_archive_read as private
import weather_history as weather


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Archive fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "remote", "add", "origin", f"https://github.com/{private.REPO}.git")
    relative = "data/archive/forecasts/forecasts-2026-10-01-to-2026-10-01.csv.gz"
    asset = root / relative
    asset.parent.mkdir(parents=True)
    asset.write_bytes(gzip.compress(b"city_key,model,run_at,for_date,forecast_max_c\namsterdam,fixture,2026-09-30T12:00:00Z,2026-10-01,18\n", mtime=0))
    index = root / private.INDEX
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({"datasets": {"forecasts": {"assets": [{"asset": asset.name, "rows": 1}]}}}))
    files = {}
    for path in (asset, index):
        data = path.read_bytes()
        files[path.relative_to(root).as_posix()] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "rows": 1 if path == asset else None}
    (root / private.RECEIPT).write_text(json.dumps({"schema": 1, "destination": {"repo": private.REPO, "private": True}, "files": files}))
    git(root, "add", ".")
    git(root, "commit", "-qm", "Verified fixture snapshot")
    monkeypatch.setenv("AD4_PRIVATE_ARCHIVE_READ_ROOT", str(root))
    monkeypatch.setenv("AD4_PRIVATE_ARCHIVE_READ_COMMIT", git(root, "rev-parse", "HEAD"))
    private._inventory.cache_clear()
    private._verified.clear()
    return root, asset


def test_unconfigured_reader_preserves_legacy_root(monkeypatch, tmp_path):
    monkeypatch.delenv("AD4_PRIVATE_ARCHIVE_READ_ROOT", raising=False)
    assert private.archive_root(tmp_path) == str(tmp_path)


def test_private_forecast_rows_keep_the_existing_parser_values(snapshot, monkeypatch, tmp_path):
    root, asset = snapshot
    assert private.archive_root(tmp_path / "source") == str(root)
    private_rows = weather.file_rows(str(asset))
    monkeypatch.delenv("AD4_PRIVATE_ARCHIVE_READ_ROOT")
    legacy = tmp_path / "legacy.csv.gz"
    legacy.write_bytes(asset.read_bytes())
    assert private_rows == weather.file_rows(str(legacy))
    assert private_rows[0]["forecast_max_c"] == 18


def test_wrong_commit_refuses_before_any_history_is_read(snapshot, monkeypatch, tmp_path):
    monkeypatch.setenv("AD4_PRIVATE_ARCHIVE_READ_COMMIT", "0" * 40)
    with pytest.raises(private.PrivateArchiveUnavailable, match="verified commit"):
        private.archive_root(tmp_path / "source")


def test_tampered_payload_is_rejected_even_after_first_read(snapshot, tmp_path):
    root, asset = snapshot
    private.archive_root(tmp_path / "source")
    private.verify_file(asset)
    asset.write_bytes(b"changed private bytes")
    with pytest.raises(private.PrivateArchiveUnavailable, match="committed receipt"):
        private.verify_file(asset)


def test_missing_private_payload_never_falls_back(snapshot):
    _, asset = snapshot
    asset.unlink()
    with pytest.raises(private.PrivateArchiveUnavailable, match="unavailable"):
        private.verify_file(asset)


def test_uncommitted_receipt_is_not_trusted(snapshot, tmp_path):
    root, _ = snapshot
    (root / private.RECEIPT).write_text("{}")
    with pytest.raises(private.PrivateArchiveUnavailable, match="uncommitted"):
        private.archive_root(tmp_path / "source")


def test_private_read_cannot_use_unregistered_files(snapshot):
    root, _ = snapshot
    other = root / "data/unregistered.csv.gz"
    other.write_bytes(gzip.compress(b"a\n1\n"))
    with pytest.raises(private.PrivateArchiveUnavailable, match="no committed"):
        private.verify_file(other)
