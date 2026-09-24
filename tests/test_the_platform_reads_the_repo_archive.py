"""The platform reads back every archive file the repo holds (plan v2.1 P1.8).

Rows leave Postgres only through the archive, "so the platform can still read
them". Since #87 (21 Sep) an archive file is committed to data/archive and
never uploaded to a Release, but /api/archive read Releases only. On 23 Sep
the repo held 9 observation files and the observations-archive Release held
6: observations-2026-06-23-to-2026-06-23, -06-23-to-2026-06-24 and
-06-24-to-2026-07-25 were readable by nobody but git, and the first two were
already pruned from Postgres.

The route now reads data/archive/<dataset>/<asset> from the repo and falls
back to the Release. These tests hold the repo to the index: every file the
index names is on disk, where the route will ask for it, with the rows the
index says.
"""
import json
import pathlib
import re

import pytest

from archive_observations import count_rows

ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = json.loads((ROOT / "web" / "public" / "archive" / "index.json").read_text())
ROUTE = (ROOT / "web" / "app" / "api" / "archive" / "route.ts").read_text()

ASSETS = [(name, a) for name, ds in INDEX["datasets"].items() for a in ds["assets"]]


def test_the_index_is_not_empty():
    assert len(ASSETS) >= 29, "the index lost entries it had on 23 Sep"


@pytest.mark.parametrize("dataset,asset", ASSETS, ids=[a["asset"] for _, a in ASSETS])
def test_every_indexed_file_is_in_the_repo_with_its_rows(dataset, asset):
    path = ROOT / "data" / "archive" / dataset / asset["asset"]
    assert path.exists(), f"{path} is in the index but not in the repo"
    assert count_rows(path.read_bytes()) == asset["rows"], (
        f"{asset['asset']}: the file and the index disagree on its rows")


def test_the_route_asks_for_the_same_path():
    m = re.search(r"return `data/archive/\$\{dataset\}/\$\{assetName\}`;", ROUTE)
    assert m, "the route no longer builds data/archive/<dataset>/<asset>"


def test_the_repo_comes_first_and_the_release_is_the_fallback():
    body = ROUTE[ROUTE.index("async function assetRows("):]
    body = body[:body.index("\n}\n")]
    repo_at = body.index("fromRepo(")
    release_at = body.index("fromRelease(")
    assert repo_at < release_at
    assert "if (!gz) gz = await fromRelease(" in body


def test_the_manifest_proves_it_can_read_the_repo():
    """One request to /api/archive answers whether every archived file is
    reachable from this deployment - a token that exists is not a token that
    reads this repository."""
    assert "repo_read: await probeRepo()" in ROUTE
    probe = ROUTE[ROUTE.index("async function probeRepo("):ROUTE.index("async function assetRows(")]
    assert "contents/data/archive" in probe and "contents: read" in probe


def test_the_index_is_read_from_the_repo_never_from_the_site_itself():
    """24 Sep: every /api/archive call was a 500. The route fetched
    `${origin}/archive/index.json` - the deployment asking itself - and behind
    Vercel Authentication that answer is the sign-in page, HTML with a 200, on
    which r.json() threw. The index lives in the repo beside the files; the
    build-time copy is the fallback; no body is parsed as JSON unchecked."""
    body = ROUTE[ROUTE.index("async function manifest("):ROUTE.index("/** Minimal CSV reader.")]
    assert "contents/web/public/archive/index.json" in body
    assert "${origin}" not in ROUTE.replace("`${origin}/archive/index.json` - the site asking", "")
    assert "bundledIndex" in body and "r.json()" not in body
    assert "manifest_source: manifestSource" in ROUTE
