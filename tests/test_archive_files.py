"""Interrupted or repeated writes must not replace archived history."""
import gzip

import pytest

import archive_files
import archive_observations as archive


def test_cold_archive_path_refuses_a_different_version(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "_root", lambda: str(tmp_path))
    first, second = gzip.compress(b"x\nfirst\n"), gzip.compress(b"x\nsecond\n")
    path = archive.write_repo_archive("observations", "dated.csv.gz", first)
    with pytest.raises(archive_files.ImmutableConflict):
        archive.write_repo_archive("observations", "dated.csv.gz", second)
    assert open(path, "rb").read() == first
    assert archive.write_repo_archive("observations", "dated.csv.gz", first) == path


def test_failed_publication_does_not_leave_a_partial_archive(tmp_path, monkeypatch):
    path = tmp_path / "record.csv.gz"
    def interrupt(*args):
        raise OSError("interrupted publication")
    monkeypatch.setattr(archive_files.os, "link", interrupt)
    with pytest.raises(OSError):
        archive_files.publish_immutable(path, b"complete bytes")
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
