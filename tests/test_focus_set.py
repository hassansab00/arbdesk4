"""The Seasonal Focus 10 is one record in three places, and they must agree.

WXPredict build F.1 (Hassan, 6 Oct: "Record the shortlist and its start date
now"). The study's output (data/eval/focus/), the pre-registration
(docs/FOCUS_PREREG.md) and the database row (migration 20261006150000) name the
same cities in the same order, the row carries the hash of the file that chose
them, and the study never read the sealed test. Membership filters what is shown
and scored; nothing that prices may read it.
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "data" / "eval" / "focus" / "season_predictability_2026-10-06.json"
TOOL = ROOT / "tools" / "focus" / "season_predictability.py"
PREREG = ROOT / "docs" / "FOCUS_PREREG.md"
MIGRATION = ROOT / "supabase" / "migrations" / "20261006150000_the_focus_set_is_recorded.sql"


def _migration_cities():
    sql = MIGRATION.read_text()
    m = re.search(r"array\[([^\]]+)\]", sql)
    return [c.strip().strip("'") for c in m.group(1).split(",")]


def test_the_study_the_record_and_the_row_name_the_same_ten():
    study = json.loads(STUDY.read_text())
    ten = study["focus_10"]
    assert len(ten) == 10 and len(set(ten)) == 10
    assert ten == study["ranked"][:10], "the ten are the top of the study's own ranking"
    assert _migration_cities() == ten
    line = next(l for l in PREREG.read_text().splitlines() if l.startswith("- **Cities, in rank order:**"))
    assert [c.strip(" .") for c in line.split(":**")[1].split(",")] == ten


def test_the_row_carries_the_hash_of_the_file_that_chose_the_cities():
    digest = hashlib.sha256(STUDY.read_bytes()).hexdigest()
    sql = MIGRATION.read_text()
    assert f"'{digest}'" in sql, "the committed study file is the one the database row names"
    assert "'data/eval/focus/season_predictability_2026-10-06.json'" in sql
    assert digest in PREREG.read_text()


def test_the_window_and_the_start_agree_everywhere():
    sql = MIGRATION.read_text()
    assert "date '2026-10-06', date '2026-11-30', date '2026-10-08'" in sql
    text = PREREG.read_text()
    assert "6 Oct - 30 Nov 2026" in text and "Evaluated from target date 8 Oct 2026" in text


def test_the_study_never_read_the_sealed_test():
    study = json.loads(STUDY.read_text())
    assert study["cutoff_exclusive"] == "2026-09-01"
    for source, newest in study["newest_date_used_per_source"].items():
        assert newest < "2026-09-01", f"{source} reached {newest}"
    src = TOOL.read_text()
    assert "CUTOFF = dt.date(2026, 9, 1)" in src or "CUTOFF = datetime.date(2026, 9, 1)" in src
    for blinded in ("rd3", "da_floor", "sd_corr", "fec_v1"):
        # named only in the rule that forbids them, never opened
        assert not re.search(rf"open\([^)]*{blinded}|glob\([^)]*{blinded}", src), blinded


def test_membership_never_reaches_a_price():
    """No script reads focus_sets: the set filters the page and the comparison,
    it never moves a probability (the owner: "Seasonal membership alone must
    not increase the predicted probability")."""
    # mirror_to_repo.py only classifies the table (NOT_MIRRORED); it never reads it.
    not_readers = {"scripts/mirror_to_repo.py"}
    readers = [p.relative_to(ROOT).as_posix()
               for p in sorted((ROOT / "scripts").rglob("*.py")) if "focus_sets" in p.read_text()]
    assert [r for r in readers if r not in not_readers] == [], f"a script reads the focus set: {readers}"
    assert '"focus_sets": "seeded from the repository' in (ROOT / "scripts" / "mirror_to_repo.py").read_text()
