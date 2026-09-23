"""The nightly mirror (plan v2.1 P1.7): every row once, nothing deleted.

The mirror copies what the archive never exports - every price, every settled
outcome, every learned parameter - into data/mirror. These tests run it
against an in-memory PostgREST that evaluates the same filters the real one
would, including the tuple comparison the composite-key paging sends, with a
page size of 2 so every table crosses many page boundaries.
"""
import datetime as dt
import gzip
import hashlib
import json
import re

import pytest

import mirror_to_repo as m


# --- a PostgREST that understands what the mirror sends ---------------------

def _val(s):
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


def _split(body):
    out, depth, cur, quoted, i = [], 0, "", False, 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and quoted:
            cur += body[i:i + 2]
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        if not quoted:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append(cur)
                cur = ""
                i += 1
                continue
        cur += ch
        i += 1
    out.append(cur)
    return out


def _cmp(a, op, b):
    a = "" if a is None else str(a)
    return {"eq": a == b, "gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def _leaf(row, text):
    col, op, val = re.match(r"([\w.]+)\.(eq|gt|gte|lt|lte)\.(.*)$", text, re.S).groups()
    return _cmp(row.get(col), op, _val(val))


def _tree(row, text):
    text = text.strip()
    if text.startswith("and("):
        return all(_tree(row, t) for t in _split(text[4:-1]))
    if text.startswith("("):
        return any(_tree(row, t) for t in _split(text[1:-1]))
    return _leaf(row, text)


def _match(row, col, expr):
    if col == "or":
        return _tree(row, expr)
    if expr.startswith("not.in.("):
        return str(row.get(col)) not in expr[len("not.in.("):-1].split(",")
    op, val = expr.split(".", 1)
    return _cmp(row.get(col), op, val)


class FakeDB:
    def __init__(self, tables):
        self.tables = tables
        self.pages = 0

    def rows(self, table, params):
        rows = [dict(r) for r in self.tables[table]]
        order, limit = None, None
        for k, v in params:
            if k in ("select",):
                continue
            if k == "order":
                order = [c.split(".")[0] for c in v.split(",")]
            elif k == "limit":
                limit = int(v)
            else:
                rows = [r for r in rows if _match(r, k, v)]
        if order:
            rows.sort(key=lambda r: tuple(str(r[c]) for c in order))
        return rows[:limit] if limit else rows

    def rest(self, table, params):
        self.pages += 1
        return self.rows(table, params)

    def count(self, table, filters):
        return len(self.rows(table, [f for f in filters if f[0] not in ("select", "limit")]))


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = FakeDB({})
    monkeypatch.setattr(m, "rest", db.rest)
    monkeypatch.setattr(m, "exact_count", db.count)
    monkeypatch.setattr(m, "PAGE", 2)
    monkeypatch.setattr(m, "MIRROR", str(tmp_path / "mirror"))
    monkeypatch.setattr(m, "MANIFEST", str(tmp_path / "mirror" / "manifest.json"))
    return db


def run(table, spec, manifest, today):
    return m.mirror_one(table, spec, manifest, today, dry_run=False)


def file_rows(path):
    import csv, io
    return list(csv.DictReader(io.StringIO(gzip.decompress(open(path, "rb").read()).decode())))


# --- the tests --------------------------------------------------------------

def test_composite_key_paging_returns_every_row_exactly_once(world):
    """The case OFFSET gets wrong: many rows share every value of the order's
    first column. Tuple keyset paging must neither skip nor repeat."""
    rows = [{"city_key": c, "computed_at": "2026-09-20T04:00:00+00:00", "lead_days": l, "mae_c": l}
            for c in ("a", "b", "c") for l in range(5)]
    world.tables["skill"] = rows
    spec = m.append("computed_at", "city_key", "computed_at", "lead_days")
    got = m.read_rows("skill", spec, [])
    assert len(got) == 15
    assert {(r["city_key"], r["lead_days"]) for r in got} == {(r["city_key"], r["lead_days"]) for r in rows}


def test_a_value_with_punctuation_pages_correctly(world):
    """Timestamps carry ':' '+' '.' - the tuple filter must quote them."""
    rows = [{"k": f"x,{i}", "t": f"2026-09-2{i}T01:02:03.5+00:00", "v": i} for i in range(6)]
    world.tables["t"] = rows
    got = m.read_rows("t", m.append("t", "k", "t"), [])
    assert sorted(r["v"] for r in got) == list(range(6))


def test_an_append_day_is_exported_once_it_has_ended_and_never_twice(world):
    world.tables["band_probabilities"] = [
        {"prob_id": i, "computed_at": f"2026-09-{d:02d}T12:00:00+00:00", "raw_prob": 0.1}
        for i, d in enumerate([20, 21, 21, 22, 23], start=1)]
    spec = m.append("computed_at", "prob_id")
    man = {"schema": 1, "tables": {}}

    n, _ = run("band_probabilities", spec, man, dt.date(2026, 9, 23))
    assert n == 4, "everything before 23 Sep 00:00Z, and nothing of 23 Sep"
    n, what = run("band_probabilities", spec, man, dt.date(2026, 9, 23))
    assert (n, what) == (0, "up to date")
    n, _ = run("band_probabilities", spec, man, dt.date(2026, 9, 24))
    assert n == 1, "the next day brings only the day that ended"

    files = man["tables"]["band_probabilities"]["files"]
    assert sum(f["rows"] for f in files) == 5
    ids = [int(r["prob_id"]) for f in files
           for r in file_rows(f"{m.MIRROR}/band_probabilities/{f['file']}")]
    assert sorted(ids) == [1, 2, 3, 4, 5]


def test_a_rewritten_window_is_exported_only_once_it_closes(world):
    today = dt.date(2026, 9, 23)
    world.tables["fact_band_outcome"] = [
        {"band_id": f"b{i}", "for_date": (today - dt.timedelta(days=d)).isoformat()}
        for i, d in enumerate([20, 10, 9, 8, 1])]
    man = {"schema": 1, "tables": {}}
    n, _ = run("fact_band_outcome", m.closed("for_date", "band_id"), man, today)
    assert n == 3, "for_date at least CLOSED_AFTER_DAYS old: 20, 10 and 9 days"
    n, _ = run("fact_band_outcome", m.closed("for_date", "band_id"), man, today + dt.timedelta(days=1))
    assert n == 1, "the 8-day-old row, a day later"


def test_a_snapshot_is_written_only_when_the_content_changes(world):
    world.tables["cities"] = [{"city_key": "nyc", "observation_trust": 0.99}]
    man = {"schema": 1, "tables": {}}
    assert run("cities", m.snapshot("city_key"), man, dt.date(2026, 9, 23))[0] == 1
    assert run("cities", m.snapshot("city_key"), man, dt.date(2026, 9, 24)) == (0, "unchanged")
    world.tables["cities"][0]["observation_trust"] = 0.97
    assert run("cities", m.snapshot("city_key"), man, dt.date(2026, 9, 25))[0] == 1
    assert len(man["tables"]["cities"]["files"]) == 2


def test_the_manifest_hash_is_the_file(world):
    world.tables["cities"] = [{"city_key": "nyc", "v": 1}]
    man = {"schema": 1, "tables": {}}
    run("cities", m.snapshot("city_key"), man, dt.date(2026, 9, 23))
    f = man["tables"]["cities"]["files"][0]
    blob = open(f"{m.MIRROR}/cities/{f['file']}", "rb").read()
    assert hashlib.sha256(blob).hexdigest() == f["sha256"]


def test_the_same_rows_give_the_same_bytes():
    assert m.gzip.compress(b"a,b\n1,2\n", 9, mtime=0) == m.gzip.compress(b"a,b\n1,2\n", 9, mtime=0)


def test_a_count_that_disagrees_refuses(world, monkeypatch):
    world.tables["signals"] = [{"signal_id": 1, "fired_at": "2026-09-20T00:00:00+00:00"}]
    monkeypatch.setattr(m, "exact_count", lambda t, f: 2)
    with pytest.raises(RuntimeError, match="paging skipped"):
        run("signals", m.append("fired_at", "signal_id"), {"schema": 1, "tables": {}},
            dt.date(2026, 9, 23))


def test_secrets_in_settings_never_leave(world):
    world.tables["settings"] = [{"key": "n8n_webhooks", "value": {"u": "secret"}},
                                {"key": "operators", "value": ["x"]},
                                {"key": "bankroll", "value": {"usd": 1}}]
    man = {"schema": 1, "tables": {}}
    run("settings", m.TABLES["settings"], man, dt.date(2026, 9, 23))
    f = man["tables"]["settings"]["files"][0]
    keys = [r["key"] for r in file_rows(f"{m.MIRROR}/settings/{f['file']}")]
    assert keys == ["bankroll"]


def test_the_mirror_deletes_nothing():
    src = open(m.__file__).read()
    for verb in ("delete", "prune", "DELETE", "truncate"):
        assert f'"{verb}' not in src and f"'{verb}" not in src
    assert "requests.delete" not in src and "rpc(" not in src


def test_a_view_source_is_read_and_counted_in_place_of_the_table(world):
    """book_snapshots and edges are mirrored from the views that hold only what
    the archive never takes - the read AND the count must use the view."""
    today = dt.date(2026, 9, 30)
    world.tables["v_mirror_edge_latest"] = [
        {"edge_id": i, "mirror_resolution_date": (today - dt.timedelta(days=d)).isoformat()}
        for i, d in enumerate([20, 9, 8], start=1)]
    world.tables["edges"] = [{"edge_id": i} for i in range(1, 100)]   # must not be read
    man = {"schema": 1, "tables": {}}
    n, _ = run("edges", m.TABLES["edges"], man, today)
    assert n == 2
    assert man["tables"]["edges"]["files"][0]["file"].startswith("edges-")


def test_kept_books_wait_past_the_edge_prune(world):
    today = dt.date(2026, 9, 30)
    world.tables["v_mirror_book_kept"] = [
        {"snapshot_id": i, "mirror_resolution_date": (today - dt.timedelta(days=d)).isoformat()}
        for i, d in enumerate([17, 16, 15, 10], start=1)]
    man = {"schema": 1, "tables": {}}
    n, _ = run("book_snapshots", m.TABLES["book_snapshots"], man, today)
    assert n == 2, "16 days or older only: the edges' 14-day prune can still un-cite the rest"
