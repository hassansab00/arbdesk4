"""Every table is mirrored into the repo, or excluded with a written reason.

Plan v2.1 P1.7. Until 23 Sep the only way a row reached the repository was
the archive, which exports what it is about to prune - so every price, every
settled outcome and every learned parameter lived in Postgres alone, and no
test noticed, because nothing listed what should be saved. This lists it.

A table created by any .sql file (or the two live tables no file creates)
must be in mirror_to_repo.TABLES or in NOT_MIRRORED with a reason. A new table
that nobody classifies fails here, the day it is added.
"""
import pathlib
import re

import mirror_to_repo as m

ROOT = pathlib.Path(__file__).resolve().parents[1]
CREATE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?\"?(\w+)", re.I)

# In the live database on 23 Sep, created by no file in this repository.
LIVE_ONLY = {"regimes", "ensemble_forecasts"}


def repo_tables():
    names = set(LIVE_ONLY)
    for path in list((ROOT / "sql").glob("*.sql")) + list((ROOT / "supabase" / "migrations").glob("*.sql")):
        text = re.sub(r"--[^\n]*", "", path.read_text())   # a comment is not a table
        names.update(CREATE.findall(text))
    return names


def test_every_table_is_mirrored_or_excused():
    homeless = sorted(repo_tables() - set(m.TABLES) - set(m.NOT_MIRRORED))
    assert not homeless, (
        "these tables would live in Postgres alone - add them to mirror_to_repo.TABLES, "
        f"or to NOT_MIRRORED with the reason: {homeless}")


def test_no_table_is_both():
    assert not set(m.TABLES) & set(m.NOT_MIRRORED)


def test_every_exclusion_says_why():
    for name, why in m.NOT_MIRRORED.items():
        assert len(why) > 20, f"{name}: a reason, not a label"


def test_the_proprietary_core_is_mirrored():
    for t in ("band_probabilities", "fact_band_outcome", "fact_forecast_outcome",
              "fact_signal_outcome", "signals", "model_versions", "markets", "bands",
              "cities", "weather_observations", "weather_forecasts",
              "proprietary_data_corrections"):
        assert t in m.TABLES, t


def test_every_mirrored_append_table_pages_on_a_key_that_includes_no_guess():
    for name, spec in m.TABLES.items():
        assert spec["pk"], name
        if spec["kind"] == "append":
            assert spec["time"], name
        if spec["kind"] == "closed":
            assert spec["date"], name
