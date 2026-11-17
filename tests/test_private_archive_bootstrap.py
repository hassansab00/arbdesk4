"""No runtime secret or a public-root target can start a private DB export."""
import gzip
import hashlib
import json
from unittest.mock import Mock

import pytest

import private_archive_bootstrap as bootstrap


def test_missing_token_stops_before_any_process_or_snapshot(tmp_path, monkeypatch):
    process, snapshot = Mock(), Mock()
    monkeypatch.setattr(bootstrap.subprocess, "run", process)
    monkeypatch.setattr(bootstrap, "snapshot", snapshot)
    with pytest.raises(bootstrap.SnapshotError, match="AD4_PRIVATE_ARCHIVE_TOKEN"):
        bootstrap.bootstrap(tmp_path / "private", "snapshot", token="")
    process.assert_not_called()
    snapshot.assert_not_called()


def test_public_source_cannot_receive_private_files(monkeypatch):
    process = Mock()
    monkeypatch.setattr(bootstrap.subprocess, "run", process)
    with pytest.raises(bootstrap.SnapshotError, match="outside"):
        bootstrap.bootstrap(bootstrap.SOURCE_ROOT / "cold", "snapshot", token="fixture")
    process.assert_not_called()


def test_clone_failure_is_redacted_and_starts_no_export(tmp_path, monkeypatch):
    process = Mock(return_value=Mock(returncode=128, stderr=b"fixture-secret"))
    snapshot = Mock()
    monkeypatch.setattr(bootstrap.subprocess, "run", process)
    monkeypatch.setattr(bootstrap, "snapshot", snapshot)
    with pytest.raises(bootstrap.SnapshotError, match="runtime Contents") as error:
        bootstrap.bootstrap(tmp_path / "private", "snapshot", token="fixture-secret")
    assert "fixture-secret" not in str(error.value)
    assert "fixture-secret" not in repr(process.call_args.args)
    snapshot.assert_not_called()


def test_access_check_never_starts_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    validate, snapshot = Mock(return_value={"private": True}), Mock()
    monkeypatch.setattr(bootstrap, "validate_destination", validate)
    monkeypatch.setattr(bootstrap, "snapshot", snapshot)
    report = bootstrap.bootstrap(tmp_path / "private", "check-only", token="fixture")
    assert report == {"private": True, "live_retirement_allowed": False, "export_started": False}
    validate.assert_called_once()
    snapshot.assert_not_called()


def test_snapshot_targets_only_the_approved_destination(tmp_path, monkeypatch):
    process = Mock(return_value=Mock(returncode=0))
    snapshot = Mock(return_value={"live_retirement_allowed": False})
    monkeypatch.setattr(bootstrap.subprocess, "run", process)
    monkeypatch.setattr(bootstrap, "snapshot", snapshot)
    monkeypatch.setattr(bootstrap, "validate_destination", Mock())
    monkeypatch.setattr(bootstrap, "legacy_recovery", Mock(return_value=None))
    root = tmp_path / "private"
    bootstrap.bootstrap(root, "snapshot", token="fixture")
    assert process.call_args.args[0][3] == "https://github.com/hassansab00/Arbdesk4-archive-001.git"
    snapshot.assert_called_once_with(root.resolve(), bootstrap.DESTINATION, "fixture", recovery_root=None)


def mirror_fixture(tmp_path, blob):
    source = tmp_path / "source"
    path = source / "data/mirror/cities/old.csv.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)
    (source / bootstrap.MIRROR).write_text(json.dumps({"tables": {"cities": {"files": [{
        "file": "old.csv.gz", "sha256": hashlib.sha256(blob).hexdigest(), "rows": 1,
    }]}}}))
    return source, path


def test_matching_history_needs_no_fetch_or_recovery(tmp_path, monkeypatch):
    source, _ = mirror_fixture(tmp_path, gzip.compress(b"value\noriginal\n", mtime=0))
    process = Mock()
    monkeypatch.setattr(bootstrap.subprocess, "run", process)
    assert bootstrap.legacy_recovery(source, tmp_path / "recovery") is None
    process.assert_not_called()
    assert not (tmp_path / "recovery").exists()


def test_legacy_recovery_keeps_current_bytes_and_recovers_exact_original(tmp_path, monkeypatch):
    original = gzip.compress(b"value\noriginal\n", mtime=0)
    source, path = mirror_fixture(tmp_path, original)
    path.write_bytes(b"later replacement")
    process = Mock(side_effect=[Mock(returncode=0), Mock(returncode=0, stdout=original)])
    monkeypatch.setattr(bootstrap.subprocess, "run", process)
    output = tmp_path / "recovery"
    assert bootstrap.legacy_recovery(source, output) == output
    digest = hashlib.sha256(original).hexdigest()
    assert (output / (digest + ".csv.gz")).read_bytes() == original
    assert path.read_bytes() == b"later replacement"
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["files"][0]["sha256"] == digest
    assert receipt["files"][0]["rows"] == 1


def test_wrong_historical_bytes_cannot_create_a_success_receipt(tmp_path, monkeypatch):
    source, path = mirror_fixture(tmp_path, gzip.compress(b"value\noriginal\n", mtime=0))
    path.write_bytes(b"later replacement")
    monkeypatch.setattr(bootstrap.subprocess, "run", Mock(side_effect=[
        Mock(returncode=0), Mock(returncode=0, stdout=b"wrong history"),
    ]))
    output = tmp_path / "recovery"
    with pytest.raises(bootstrap.SnapshotError, match="recorded digest"):
        bootstrap.legacy_recovery(source, output)
    assert not (output / "receipt.json").exists()
