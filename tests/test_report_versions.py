"""Corrections retain their actual receipt and cannot leak into an earlier call."""
import csv
import io
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))
from wxpredict import common, fetch_obs, report_journal as journal, live_reader, build_table
import live_capture


def answer(values):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=["station", "valid", *live_capture.FULL_FIELDS])
    w.writeheader()
    for temp, metar in values:
        w.writerow({"station": "EGLC", "valid": "2026-10-15 12:00", "tmpf": temp, "metar": metar})
    return out.getvalue()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "REPORTS", str(tmp_path / "wx/station_reports"))
    return tmp_path


def add(text, receipt):
    return journal.append_response(text, ["EGLC"], live_capture.FULL_FIELDS, receipt)


def test_same_minute_rows_survive_a_live_capture_and_decode_deterministically():
    rows = live_capture.report_rows(answer([("60", "METAR first"), ("61", "SPECI second"), ("61", "SPECI second")]))
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["schema"] == "iem-report-set-v1" and len(payload["rows"]) == 2
    station, row = live_reader.report_row(rows[0]["key"], payload)
    assert station == "EGLC" and row["tmpf"] == "61"
    with pytest.raises(ValueError, match="unknown station report schema"):
        live_reader.report_row(rows[0]["key"], {"schema": "unknown"})


def test_a_correction_and_reversion_are_available_only_after_their_receipt(isolated):
    a, b = answer([("60", "original")]), answer([("62", "COR corrected")])
    add(a, "2026-10-15T12:09:00Z")
    add(b, "2026-10-15T12:20:00Z")
    add(a, "2026-10-15T12:30:00Z")
    assert journal.asof("EGLC", "2026-10-15T12:08:59Z") == []
    assert journal.asof("EGLC", "2026-10-15T12:19:59Z")[0]["tmpf"] == "60"
    assert journal.asof("EGLC", "2026-10-15T12:20:00Z")[0]["tmpf"] == "62"
    assert journal.asof("EGLC", "2026-10-15T12:30:00Z")[0]["tmpf"] == "60"
    assert len(list(journal.versions())) == 3
    known = build_table.load_reports("EGLC", received_by="2026-10-15T12:21:00Z")
    assert known.rec[0][1] == 62


def test_repeated_answer_keeps_identical_bytes_and_conflicting_receipts_refuse(isolated):
    a = answer([("60", "original"), ("61", "special")])
    add(a, "2026-10-15T12:09:00Z")
    path = pathlib.Path(journal.paths()[1][0])
    before = path.read_bytes()
    assert add(a, "2026-10-15T12:10:00Z")["versions_added"] == 0
    assert path.read_bytes() == before
    assert journal.asof("EGLC", "2026-10-15T12:10:00Z")[0]["metar"] == "special"
    with pytest.raises(ValueError, match="same or earlier receipt"):
        add(answer([("62", "other")]), "2026-10-15T12:09:00Z")


def test_report_lag_and_receipt_are_both_required_and_unknown_history_is_absent(isolated):
    add(answer([("60", "original")]), "2026-10-15T12:01:00Z")
    assert journal.asof("EGLC", "2026-10-15T12:07:59Z") == []
    assert len(journal.asof("EGLC", "2026-10-15T12:08:00Z")) == 1
    assert journal.asof("LFPG", "2026-10-15T12:21:00Z") == []
    with pytest.raises(ValueError, match="after its receipt"):
        add(answer([("60", "future")]), "2026-10-15T11:30:00Z")
