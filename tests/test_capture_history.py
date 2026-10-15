"""A cold-input move preserves the shadow's actual inputs or refuses."""
import csv
import gzip
import hashlib
import io
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from wxpredict import capture_history as history, live_reader as lr


def fixture(tmp_path):
    first, last = history.unix("2026-10-13T00:00:00Z"), history.unix("2026-10-14T00:00:00Z")
    rows = [("hourly", "london|2026-10-13T10:00", "2026-10-13T00:36:00Z", "[12,8,50,200,5,0,1015]"),
            ("hourly", "paris|2026-10-13T10:00", "2026-10-13T00:36:00Z", "[13]"),
            ("books", "all", "2026-10-13T00:36:00Z", "{}")]
    text = io.StringIO(); w = csv.writer(text)
    w.writerow(["kind", "key", "captured_at", "payload"]); w.writerows(rows)
    blob = gzip.compress(text.getvalue().encode(), mtime=0)
    folder = tmp_path / "data/mirror/live_capture"; folder.mkdir(parents=True)
    file = folder / "part.csv.gz"; file.write_bytes(blob)
    state = {"through": "2026-10-14T00:00:00Z", "files": [{"from": "2026-10-13T00:00:00Z",
             "to": "2026-10-14T00:00:00Z", "file": file.name, "rows": len(rows),
             "sha256": hashlib.sha256(blob).hexdigest()}]}
    manifest = folder.parent / "manifest.json"
    manifest.write_text(json.dumps({"tables": {"live_capture": state}}))
    return first, last, rows, file, manifest


def test_moving_rows_to_the_mirror_keeps_identical_known_inputs(tmp_path):
    first, last, rows, _, _ = fixture(tmp_path)
    cold = history.read(first, last, kinds=("hourly",), prefixes={"london|"}, root=tmp_path)
    hot = [("hourly", "london|2026-10-13T10:00", "2026-10-14T00:36:00Z", [12.5,8,50,200,5,0,1015])]
    before, after = lr.Record([rows[0], *hot]), lr.Record([*cold, *hot])
    for at in ("2026-10-13T00:35:00Z", "2026-10-13T12:00:00Z", "2026-10-14T01:00:00Z"):
        assert before.values("hourly", history.unix(at)) == after.values("hourly", history.unix(at))
    assert len(cold) == 1


def test_an_old_manifest_missing_file_or_changed_bytes_refuses(tmp_path):
    first, last, _, file, manifest = fixture(tmp_path)
    with pytest.raises(ValueError, match="boundary"):
        history.read(first, last + 1, root=tmp_path)
    before = file.read_bytes(); file.write_bytes(before + b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        history.read(first, last, root=tmp_path)
    file.unlink()
    with pytest.raises(ValueError, match="file missing"):
        history.read(first, last, root=tmp_path)
    manifest.unlink()
    with pytest.raises(ValueError, match="unavailable"):
        history.read(first, last, root=tmp_path)


def test_the_verified_count_must_match(tmp_path):
    first, last, _, _, manifest = fixture(tmp_path)
    data = json.loads(manifest.read_text()); data["tables"]["live_capture"]["files"][0]["rows"] += 1
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="row count"):
        history.read(first, last, root=tmp_path)
