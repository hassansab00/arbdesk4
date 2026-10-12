"""When a station report is known (12 Oct). v1 counts a report 20 min after
its reading (IEM, 5 Oct); Aviation Weather's own receipt times put it at 4.47
min (median) to 8.35 min (p90) for the 47 stations, so the v2 table and its
model count it 8 min after. The lag is a setting of a build or a reader,
recorded in the table's meta, and v1 stays the default: the frozen sdyes-v1
pilot reads rows the way it was fitted."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wxpredict import build_table as bt  # noqa: E402
from wxpredict import features  # noqa: E402

T0 = 1_792_000_000


def reports(*minutes_before):
    rows = []
    for m in sorted(minutes_before, reverse=True):
        row = {k: "" for k in ("valid", "tmpf", "t10_c", "td10_c", "dwpf", "drct", "mslp", "alti", "relh", "sknt",
                               "gust", "p01i", "vsby", "skyc1", "skyc2", "skyc3", "skyl1", "skyl2", "skyl3",
                               "wxcodes", "max6_c", "min6_c")}
        import datetime as dt
        row["valid"] = dt.datetime.fromtimestamp(T0 - m * 60, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        row["tmpf"] = "60.0"
        rows.append(row)
    return features.Reports(rows)


def test_v1_is_the_default_and_v2_is_eight_minutes():
    assert features.REPORT_LAG_S == 20 * 60, "the pilot and the recorded table read v1"
    assert features.REPORT_LAG_V2_S == 8 * 60


def test_a_report_ten_minutes_old_is_known_under_v2_only():
    rep = reports(70, 10)
    assert rep.known(T0) == 1, "v1: the 10-minute-old report is not known yet"
    with features.report_lag(features.REPORT_LAG_V2_S):
        assert rep.known(T0) == 2
    assert rep.known(T0) == 1, "the lag comes back after the block"


def test_the_lag_comes_back_after_an_error():
    try:
        with features.report_lag(60):
            raise RuntimeError("a build that fails")
    except RuntimeError:
        pass
    assert features.REPORT_LAG_S == 20 * 60


def test_the_meta_records_the_lag_the_rows_were_made_with():
    with features.report_lag(480):
        assert bt.Coverage({}).result(0)["report_lag_s"] == 480
    assert bt.Coverage({}).result(0)["report_lag_s"] == 1200


def test_the_v2_table_has_its_own_place():
    assert bt.OUT_DIR_V2 != bt.OUT_DIR and bt.META_V2 != bt.META


def test_the_v2_table_meta_records_its_rule_and_its_sources():
    import json
    meta = json.loads((ROOT / "data" / "training" / "wxpredict" / "table_v2_meta.json").read_text())
    assert meta["report_lag_s"] == features.REPORT_LAG_V2_S
    assert meta["columns"] == bt.COLUMNS
    assert meta["sources"], "a v2 table can be rebuilt from exactly what it read (build_table.py --v2 --recorded)"


def test_the_frozen_v2_model_is_the_file_its_record_names():
    import gzip
    import hashlib
    import json
    rec = json.loads((ROOT / "data" / "models" / "wxrd" / "wxrd-v2_remaining.json").read_text())
    raw = gzip.decompress((ROOT / "data" / "models" / "wxrd" / rec["model_file"]).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == rec["model_sha256"]
    assert rec["version"] == "wxrd-v2" and rec["report_lag_s"] == features.REPORT_LAG_V2_S
    ledger = json.loads((ROOT / "data" / "eval" / "wxpredict" / "exposure_ledger.json").read_text())
    assert rec["fit_last"] < ledger["untouched_from"], "fitted only on dates the ledger has seen"


def test_the_latency_summary_counts_every_report():
    sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))
    import report_latency
    reports = [{"icao": "KLGA", "latency_min": v} for v in (3.0, 3.2, 3.1)] + [{"icao": "EGLC", "latency_min": 4.2}]
    s = report_latency.summarise(reports)
    assert s["all"]["n"] == 4 and s["all"]["p50"] == 3.2
    assert s["by_station"]["KLGA"] == {"n": 3, "p50": 3.1, "p75": 3.2, "p90": 3.2, "p95": 3.2}
