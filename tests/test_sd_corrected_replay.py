"""The same-day station-corrected candidate's design replay is reproducible.

tools/sd_corrected_replay.py scores `sd_corr` (the station-corrected centre and
width live at each same-day checkpoint, cut by the served floor) beside the
served ladder and `da_floor`, on P1.1's recorded rows. These tests hold what
it may read (the row the tick would have read, no later one), that the
committed report is what the committed inputs give, and that the documents
quote it."""
import datetime as dt
import glob
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import sd_corrected_replay as sd   # noqa: E402

ROWS = sorted(glob.glob(os.path.join(ROOT, "data", "eval", "p11", "rows_*.json.gz")))
FROZEN = os.path.join(ROOT, "data", "eval", "sd_corr", "corrected_rows.json.gz")
REPORT = os.path.join(ROOT, "data", "eval", "sd_corr", "report.json")
DOC = os.path.join(ROOT, "docs", "SD_CORR_REPLAY_2026-10-05.md")


def _row(at, centre=20.0, width=1.0):
    return {"computed_at": sd._ts(at), "lead": 0, "centre": centre, "width": width,
            "version": "v", "width_version": "w", "n_sources": 7}


def test_a_checkpoint_reads_the_row_the_tick_would_have_read():
    rows = [_row("2026-10-01T05:00:00+00:00", 20.0), _row("2026-10-02T05:00:00+00:00", 21.0)]
    # before the second night's fit: the first; after it: the second
    assert sd.live_row(rows, "2026-10-02T04:36:00+00:00")[0]["centre"] == 20.0
    assert sd.live_row(rows, "2026-10-02T05:36:00+00:00")[0]["centre"] == 21.0
    # never a row computed after the decision
    assert sd.live_row(rows, "2026-10-01T04:59:59+00:00") == (None, "no_row_computed_before")


def test_a_row_older_than_the_engine_s_max_age_is_not_read():
    rows = [_row("2026-10-01T05:00:00+00:00")]
    assert sd.live_row(rows, "2026-10-02T16:59:00+00:00")[0] is not None        # 35.98 h
    assert sd.live_row(rows, "2026-10-02T17:01:00+00:00") == (None, "row_older_than_max_age")
    assert sd.MAX_AGE_H == 36.0


def test_the_committed_report_is_what_the_committed_inputs_give():
    with gzip.open(FROZEN, "rt") as f:
        frozen = json.load(f)
    fresh = sd.run(sd.p11.load(ROWS), sd.corrected_rows(frozen))
    committed = json.load(open(REPORT))
    assert json.loads(json.dumps(fresh, sort_keys=True)) == committed
    assert committed["replay"]["rows_off_by_1e-3"] == 0, "the served ladders come back: the replay is the engine"


def test_the_frozen_rows_are_the_mirror_s():
    """While the mirror still holds the nights these dates need, the frozen
    copy is exactly what it holds for them."""
    snaps = sorted(glob.glob(os.path.join(sd.SNAPSHOTS, "derived_corrected_forecast-*.csv.gz")))
    names = {os.path.basename(p)[len("derived_corrected_forecast-"):][:10] for p in snaps}
    if not {"2026-09-28", "2026-10-04"} <= names:
        return
    keys = {(r["city"], r["date"]) for r in sd.p11.load(ROWS)}
    with gzip.open(FROZEN, "rt") as f:
        frozen = json.load(f)
    later = [r for r in sd.mirror_rows(sd.SNAPSHOTS, keys)
             if r["computed_at"] < "2026-10-05"]          # nights after the freeze add nothing older
    assert [r for r in frozen if r["computed_at"] < "2026-10-05"] == later


def test_the_document_quotes_the_report():
    doc = open(DOC).read()
    rep = json.load(open(REPORT))
    for cp in ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h"):
        v = rep["by_checkpoint"][cp]
        for name in ("served", "da_floor", "sd_corr"):
            assert f"{v['variants'][name]['log_loss']['mean']:.3f}" in doc, (cp, name)
        for k in ("served_minus_sd_corr", "da_floor_minus_sd_corr"):
            d = v["differences_log_loss"][k]
            assert f"{d['mean']:+.3f} [{d['ci90'][0]:+.3f}, {d['ci90'][1]:+.3f}]" in doc, (cp, k)
    p = rep["pooled"]["differences_log_loss"]["da_floor_minus_sd_corr"]
    assert f"{p['mean']:+.3f} [{p['ci90'][0]:+.3f}, {p['ci90'][1]:+.3f}]" in doc


def test_the_pre_registration_quotes_the_replay_and_names_the_version():
    pre = open(os.path.join(ROOT, "docs", "SD_CORR_PREREG.md")).read()
    rep = json.load(open(REPORT))
    cps = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
    served = [rep["by_checkpoint"][c]["differences_log_loss"]["served_minus_sd_corr"]["mean"] for c in cps]
    da = [rep["by_checkpoint"][c]["differences_log_loss"]["da_floor_minus_sd_corr"]["mean"] for c in cps[:4]]
    assert f"{min(served):.3f} – {max(served):.3f}" in pre
    assert f"{min(da):.3f} – {max(da):.3f}" in pre, "before the peak"
    assert f"{rep['pooled']['rows']:,} rows" in pre
    assert "`sd_corr:v1`" in pre and "**No date before the first forward row counts" in pre
    # the rule's top-1 condition is per checkpoint, because the replay was worse after the peak
    assert "At no checkpoint is `sd_corr`'s top-1 rate below" in pre
