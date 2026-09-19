"""An archive nothing can read is indistinguishable from a deletion.

scripts/archive_observations.py moved 78,291 research captures to a GitHub
Release, verified them, and pruned them from Postgres. All of that was
correct. But nothing recorded WHERE they went in any place the platform could
see, and no code path could fetch them back - so from the outside the rows
simply vanished: every reader went empty and there was nothing to point at.

Two pieces close that, and these pin both.

  web/public/archive/index.json   public, no token, committed to the repo like
                                  the paper-trade log. Says which ranges are
                                  archived, how many rows, and which release
                                  asset holds them.
  /api/archive                    server-side, holds the token a private
                                  repo's asset needs, and returns the rows.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive_observations.py"
ROUTE = ROOT / "web" / "app" / "api" / "archive" / "route.ts"
FORMAT = ROOT / "web" / "lib" / "archiveFormat.ts"
INDEX = ROOT / "web" / "public" / "archive" / "index.json"


def _code(path):
    return "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                     if not l.strip().lstrip("*").startswith(("#", "//", "--")))


# --- the archive says, in the repo, that it happened --------------------

def test_the_archive_writes_a_public_manifest():
    code = _code(SCRIPT)
    assert "def record_manifest(" in code
    assert "web" in code and "archive" in code and "index.json" in code
    # written only after the prune is confirmed, so a manifest never claims a
    # range that is still live in Postgres
    assert code.index("record_manifest(name, spec") > code.index('prune.get("deleted")')


def test_the_manifest_exists_and_names_what_was_archived():
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    research = idx["datasets"]["research"]
    assert research["table"] == "research_captures"
    assert research["release_tag"] == "research-archive"
    assert research["assets"], "an archived dataset with no assets points nowhere"
    for a in research["assets"]:
        for key in ("asset", "rows", "from", "to", "archived_through"):
            assert key in a, f"{key} is what a page needs to offer this range"
    assert research["rows_archived"] == sum(a["rows"] for a in research["assets"])


def test_re_archiving_a_range_replaces_rather_than_duplicates():
    """The release overwrites the asset, so two entries would describe one
    file and double every row count derived from the index."""
    code = _code(SCRIPT)
    assert 'a.get("asset") != asset_name' in code


# --- and something can read it back -------------------------------------

def test_the_route_holds_the_token_rather_than_the_browser():
    code = _code(ROUTE)
    assert "runtime = 'nodejs'" in code
    assert "GITHUB_DISPATCH_TOKEN" in code
    assert "process.env" in code
    # the manifest itself must stay fetchable without one
    assert "/archive/index.json" in code


def test_an_unreadable_archive_never_reads_as_an_empty_one():
    """The failure that started this: rows gone, page empty, no explanation.
    Every error path has to point at the release."""
    code = _code(ROUTE)
    assert code.count("releases/tag") >= 2, (
        "a token failure and a fetch failure must both name where the rows are")
    assert "not lost" in code or "not a loss" in code


def test_rows_are_paged_rather_than_handed_over_whole():
    """An asset is ~78,000 rows and ~12 MB gzipped. Returning one whole would
    exhaust the function and freeze the tab."""
    code = _code(ROUTE)
    assert "MAX_LIMIT" in code and "5000" in code
    assert "offset" in code and "matched" in code


def test_only_the_assets_covering_the_window_are_downloaded():
    """Asking for one day must not pull six weeks across the wire."""
    code = _code(ROUTE)
    assert "a.to >= from" in code and "a.from <= to" in code


# --- the first archive wrote payloads Python could read and nothing else ---
#
# csv.DictWriter stringifies with str(), and the exporter handed it a parsed
# dict, so all 78,291 research captures went to the release as PYTHON REPRS:
#
#     {'band_hi': 28, 'sigma_c': None, 'open_low': False}
#
# Single-quoted, None where JSON needs null, False where it needs false. It
# round-trips through ast.literal_eval so nothing was lost, but no JSON parser
# will touch it - which makes an archive unreadable to the browser, and being
# readable is the whole reason it exists. The writer is fixed; those rows are
# already in the release and out of Postgres, so the reader handles both.

def test_the_exporter_writes_json_not_a_python_repr():
    code = _code(SCRIPT)
    assert "def _cell(" in code, "nothing normalises a jsonb column on the way out"
    assert "json.dumps(value" in code
    assert "_cell(v) for k, v in r.items()" in code, (
        "the normaliser must be applied to the row actually written")


def test_a_none_still_writes_as_empty_rather_than_the_string_None():
    """test_weather_model pins this deliberately, and _cell must not break it
    while fixing the dict case."""
    import json as _json
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from archive_observations import _cell
    assert _cell(None) is None, "csv writes None as an empty field; keep that"
    assert _cell("x") == "x"
    assert _cell(3) == 3
    assert _json.loads(_cell({"b": False, "a": None})) == {"b": False, "a": None}
    assert _json.loads(_cell([1, None, True])) == [1, None, True]


def test_the_reader_understands_both_payload_generations():
    # The decoder lives in lib rather than the route: a Next.js route file may
    # only export its handlers, and next build rejects anything else.
    code = _code(FORMAT)
    assert "decodePayload" in _code(ROUTE), "the route must actually use it"
    assert "pythonReprToJson" in code and "decodePayload" in code
    # a scanner, not a regex: replacing ' with " corrupts every apostrophe
    # inside a value, and rewriting bare None/True/False would hit those words
    # where they appear inside text
    assert ".replace(/'/g" not in code, "a blanket quote swap corrupts apostrophes"
    assert "JSON.stringify(s)" in code, "quoted runs must be re-emitted safely"
    # one unreadable row must not cost the caller the rest
    assert "return raw" in code
