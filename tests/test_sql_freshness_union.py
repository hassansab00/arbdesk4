"""v_data_freshness is assembled, not written, and assembly can go wrong.

sql/ad4_39_freshness.sql builds the view by looping over data_freshness_spec
and emitting one SELECT per table, glued with UNION ALL. Three branches:
absent (no such table), no-timestamp-column, and the ordinary one. UNION ALL
matches columns BY POSITION, so the three have to agree exactly - same
columns, same order - and nothing in the file makes them.

They did not agree. The absent branch omitted rows_estimated, so a database
missing even one of the forty-eight tracked tables failed the create view
with "each UNION query must have the same number of columns", the view did
not exist, and the UI printed "Freshness tracking is not installed - run
sql/ad4_39_freshness.sql". The file the message names was the file that could
not run. The branch that carries the bug only fires when a table is missing,
which is exactly the install where nobody has run the file before, so no
install that worked would ever have found it.

This reads the three branch templates out of the file and compares the
columns they project. It does not need a database.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "sql", "ad4_39_freshness.sql")


def _branches():
    """The three $q$...$q$ SELECT templates of the assembly block."""
    src = open(FILE).read()
    start = src.index("create view v_data_freshness")
    block = src[: start]                      # the DO block that builds it
    block = block[block.rindex("do $ad4$") :]
    found = re.findall(r"\$q\$(select.*?)\$q\$", block, re.S | re.I)
    assert len(found) == 3, f"expected 3 branch templates, found {len(found)}"
    return found


def _projections(sql):
    """Output column names of a SELECT list, in order.

    Walks the text tracking paren depth and quotes so that a comma inside
    round(...), a case ... end, or 'a string' is not read as a separator, and
    so that the subselect's own FROM does not end the list early.
    """
    body = sql[sql.lower().index("select") + 6 :]
    out, buf, depth, quote = [], [], 0, False
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            buf.append(ch)
            if ch == "'":
                quote = False
        elif ch == "'":
            quote = True
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif depth == 0 and ch == "," :
            out.append("".join(buf))
            buf = []
        elif depth == 0 and re.match(r"\s+from\b", body[i : i + 12], re.I):
            break
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf))
    return [_name(p) for p in out]


def _name(projection):
    p = " ".join(projection.split())
    m = re.search(r"\bas\s+([A-Za-z_][A-Za-z_0-9]*)\s*$", p, re.I)
    if m:
        return m.group(1).lower()
    # No alias: the column name is whatever the expression's last identifier
    # is - t.newest projects as "newest".
    return p.split(".")[-1].strip().lower()


EXPECTED = [
    "table_name", "layer", "plain_english",
    "rows", "rows_estimated", "newest", "age_hours", "fresh_hours", "state",
]


def test_every_union_branch_projects_the_same_columns_in_the_same_order():
    lists = [_projections(b) for b in _branches()]
    for got in lists:
        assert got == EXPECTED, (
            "a v_data_freshness branch projects "
            f"{got}\nbut UNION ALL matches by position and the view needs\n{EXPECTED}"
        )


def test_the_absent_branch_exists_and_is_typed():
    """The branch only a missing table reaches still has to be well typed.

    Every column it projects is a literal null, so nothing but an explicit
    cast tells Postgres what type it is - and an untyped null next to a
    boolean is not the same failure as a missing column, it is a worse one:
    it resolves to text and the view builds with the wrong type.
    """
    absent = [b for b in _branches() if "'absent'" in b]
    assert len(absent) == 1
    body = absent[0]
    for col, typ in [("rows", "bigint"), ("rows_estimated", "boolean"),
                     ("newest", "timestamptz"), ("age_hours", "numeric")]:
        assert re.search(rf"null::{typ} as {col}\b", body), \
            f"{col} in the absent branch must be an explicit null::{typ}"
