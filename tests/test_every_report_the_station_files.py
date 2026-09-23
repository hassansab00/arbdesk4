"""The station feed asks for routine AND special reports (plan v2 P2.1).

report_type=3 alone is one routine METAR an hour. Stations that file every
half hour (EGLC, EHAM) lost half their day, and US specials were dropped:
LGA's 72F at 01:04Z on 20 Sep, the reading the venue settled on, never
arrived. Measured against IEM and the stored WRH evidence on 23 Sep; the
numbers are in scripts/ingest_observations.py beside the parameter.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ingest_observations as io_  # noqa: E402


def test_the_python_ingest_asks_for_routine_and_special_reports(monkeypatch):
    seen = {}

    class R:
        text = "station,valid,tmpf\n"
        def raise_for_status(self):
            pass

    def fake_get(url, params=None, timeout=None):
        seen.update(params)
        return R()

    monkeypatch.setattr(io_.requests, "get", fake_get)
    import datetime as dt
    io_.fetch_station(["EGLC"], dt.date(2026, 9, 17), dt.date(2026, 9, 18))
    assert sorted(seen["report_type"]) == ["3", "4"], (
        "report_type must carry both 3 (routine) and 4 (specials); 3 alone drops "
        "half-hourly reports and every special")


def test_the_n8n_workflow_asks_for_the_same_reports():
    wf = json.loads((ROOT / "n8n" / "P1.6_iem_observations.template.json").read_text())
    code = "\n".join(n["parameters"].get("jsCode", "") for n in wf["nodes"] if n.get("parameters"))
    assert "['report_type', '3'], ['report_type', '4']" in code, (
        "the hourly n8n feed and the Python ingest must ask IEM for the same reports")
