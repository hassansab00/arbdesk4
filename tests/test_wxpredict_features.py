"""WXPredict phase 2.3, step 1 (10 Oct): one implementation of the features.

tools/wxpredict/features.py computes every feature of a decision from inputs
it is handed; tools/wxpredict/build_table.py loads the committed sources into
those shapes and computes nothing itself, and the live reader is to load the
live sources into the same shapes. These tests hold the split: the builder's
feature names are the module's own objects, the module opens no file and asks
no network, and one decision built alone is the row the whole event gives.
The table built from the split is byte for byte the table before it (the
sha256 in data/training/wxpredict/table_meta.json; checked when the split was
made, not here: a full build takes minutes).
"""
import ast
import datetime as dt
import importlib.util
import pathlib
import sys
from array import array

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wxpredict import build_table as bt  # noqa: E402
from wxpredict import features as fx  # noqa: E402

FEATURES = ROOT / "tools" / "wxpredict" / "features.py"
BUILDER = ROOT / "tools" / "wxpredict" / "build_table.py"

# everything that says what a row may know, and every function that computes one
SHARED = ("REPORT_LAG_S", "PUBLISH_H", "SNAPSHOT_S", "DAY_HOURS", "EVE_HOURS", "MARKET_MAX_AGE_S", "PRICE_FLOOR",
          "CLIM_HALF_WINDOW", "BIAS_WINDOWS", "MODELS", "COLUMNS", "NOT_WHOLE", "UNFINISHED",
          "unix_of", "num", "unit_reading", "fmt", "bucket_of", "bucket_rep", "sky", "wx_bits", "Reports",
          "whole_day", "climatology", "hourly_known", "daily_known", "pick_daily", "ladder_at", "implied",
          "activity", "decision_instants", "venue_label", "event_context", "decision_row", "build_event",
          "obs_features", "fc_features", "bias_features", "market_features")


def _table_tests():
    spec = importlib.util.spec_from_file_location("wx_table_tests", ROOT / "tests" / "test_wxpredict_table.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_builder_computes_no_feature_itself():
    defined = {n.name for n in ast.parse(BUILDER.read_text()).body
               if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assigned = {t.id for n in ast.parse(BUILDER.read_text()).body if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    for name in SHARED:
        assert getattr(bt, name) is getattr(fx, name), name
        assert name not in defined and name not in assigned, f"build_table.py defines {name} again"


def test_the_features_open_no_file_and_ask_no_network():
    tree = ast.parse(FEATURES.read_text())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0])
    assert imported <= {"bisect", "calendar", "collections", "datetime", "math", "os", "statistics", "sys",
                        "wxpredict"}, imported
    called = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
              for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}
    # no network module is importable (above); dict.get is a dict's
    for io in ("open", "read_csv", "write_csv", "listdir", "urlopen", "cities", "active_cities"):
        assert io not in called, f"features.py calls {io}"
    source = FEATURES.read_text()
    for path in ("common.MH", "common.PR", "common.WX", "common.REPORTS", "common.STATION_DAILY"):
        assert path not in source, path


def test_one_decision_alone_is_the_row_the_whole_event_gives():
    t = _table_tests()
    day, tz, bands = t.DAY, t.LONDON, t.BANDS
    e = {"event_id": "1", "source": "venue", "city": "london", "date": day.isoformat(), "unit": "C",
         "station": "EGLC", "bands": bands, "winner": 4}
    after = t._report(t._unix(day + dt.timedelta(days=1), 0, 20), 15.0)
    rep = bt.Reports(t._day_reports(peak=23.0) + [after])
    sdays = {(day - dt.timedelta(days=k)).isoformat(): (21.0 + k % 3, 70.0, 14.0) for k in range(1, 400)}
    row = {"tmax_c": "22.5", "tmax_00_17_c": "22.0", "cloud_09_17_pct": "40", "shortwave_06_17_wh_m2": "5000",
           "wind_09_17_kmh": "12", "precip_00_17_mm": "0", "td_09_17_c": "12"}
    bm = {("london", lead, (day - dt.timedelta(days=k)).isoformat()): row for lead in (1, 2) for k in range(0, 40)}
    md = {key: {"ecmwf_ifs025": (22.0, 21.5), "gfs_seamless": (23.0, 22.5)} for key in bm}
    d0, d1 = t._unix(day, 0), t._unix(day + dt.timedelta(days=1), 0)
    hours = list(range(d0 - 86400, d1, 3600))
    hourly = {"london": (hours, [(15.0 + (u - d0) / 3600 % 24 / 3, 9.0, 30.0, 200.0, 10.0, 0.0, 1015.0)
                                 for u in hours])}
    ts = array("q", range(d0 - 2 * 86400, d1, 3600))
    ladder = (0.05, 0.1, 0.2, 0.3, 0.3, 0.05)
    prices = {"1": [(ts, array("d", [ladder[b]] * len(ts))) for b in range(6)]}

    rows = bt.build_event(e, rep, sdays, bm, md, hourly, prices, tz)
    assert len(rows) == 32
    assert any(r.get("t_now_c") is not None for r in rows) and any(r.get("fc_bm_tmax_c") for r in rows)
    assert any(r.get("mkt_complete") for r in rows) and any(r.get("fch_now_c") is not None for r in rows)
    assert {r["label_unit"] for r in rows} == {23}

    ctx = fx.event_context(e, rep, sdays, tz)
    series = prices["1"]
    alone = [fx.decision_row(e, ctx, off, local, rep, sdays, bm, md, hourly, series, tz)
             for off, local in fx.decision_instants(day, tz)]
    assert alone == rows
