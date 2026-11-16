"""Private snapshots cannot fall back to public storage or retire live rows."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

import archive_observations as archive
import private_archive_snapshot as private

REPO = private.DEFAULT_REPO
TOKEN = "fixture-private-token"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def init(root, remote):
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Archive fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "remote", "add", "origin", remote)
    (root / "README.md").write_text("fixture\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")


def catalogs(root):
    asset = "seed.csv.gz"
    blob = gzip.compress(b"value\nseed\n", mtime=0)
    for folder in ("data/archive/observations", "data/mirror/cities"):
        path = root / folder / asset
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    index = root / private.INDEX
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({"datasets": {"observations": {
        "rows_archived": 1, "assets": [{"asset": asset, "rows": 1}]}}}))
    (root / private.MIRROR).write_text(json.dumps({"tables": {"cities": {
        "files": [{"file": asset, "rows": 1, "sha256": hashlib.sha256(blob).hexdigest()}]}}}))


@pytest.fixture
def repos(tmp_path, monkeypatch):
    source, target, bare = tmp_path / "source", tmp_path / "private", tmp_path / "remote.git"
    init(source, f"https://github.com/{private.SOURCE_REPO}.git")
    catalogs(source)
    scripts = source / "scripts"
    scripts.mkdir()
    # A child exporter that only accepts the real export-only invocation.
    (scripts / "archive_observations.py").write_text('''
import gzip,json,os,sys
from pathlib import Path
assert sys.argv[1:]==['--export-only','--repo','hassansab00/arbdesk4-private-archive','--table','all']
r=Path(os.environ['AD4_PRIVATE_ARCHIVE_ROOT'])
assert r!=Path(__file__).resolve().parents[1]
assert os.environ['GITHUB_REPOSITORY']=='hassansab00/arbdesk4-private-archive'
p=r/'data/archive/observations/new.csv.gz'; p.write_bytes(gzip.compress(b'value\\nnew\\n',mtime=0))
i=r/'web/public/archive/index.json'; d=json.loads(i.read_text()); e=d['datasets']['observations']
e['assets'].append({'asset':'new.csv.gz','rows':1}); e['rows_archived']=2; i.write_text(json.dumps(d))
(r/'data/archive/.pending.json').write_text(json.dumps({'observations':{'rows':1}}))
''')
    git(source, "add", ".")
    git(source, "commit", "-qm", "seed")
    init(target, f"https://github.com/{REPO}.git")
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)], check=True)
    meta = dict(full_name=REPO, private=True, visibility="private", archived=False,
                disabled=False, default_branch="main", permissions=dict(push=True, pull=True))
    def get(url, **kwargs):
        assert url == f"https://api.github.com/repos/{REPO}"
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
        return type("Response", (), {"status_code": 200, "json": lambda self: dict(meta)})()
    monkeypatch.setattr(private.requests, "get", get)
    original_git = private.git
    network = []
    def local_network(root, *args, env=None):
        if args[0] in ("push", "fetch"):
            if "--dry-run" not in args:
                network.append(args[0])
            args = (args[0], *[str(bare) if x == "origin" else x for x in args[1:]])
        return original_git(root, *args, env=env)
    monkeypatch.setattr(private, "git", local_network)
    return source, target, bare, meta, network


@pytest.mark.parametrize("change", [
    {"private": False, "visibility": "public"}, {"archived": True}, {"disabled": True},
    {"permissions": {"pull": True, "push": False}}, {"full_name": "someone/else"},
])
def test_invalid_destination_stops_before_database_export(repos, change):
    source, target, _, meta, network = repos
    meta.update(change)
    with pytest.raises(private.SnapshotError):
        private.snapshot(target, REPO, TOKEN, source)
    assert network == [] and not (target / private.RECEIPT).exists()


def test_separate_public_push_url_is_rejected(repos):
    source, target, _, _, network = repos
    git(target, "remote", "set-url", "--push", "origin", f"https://github.com/{private.SOURCE_REPO}.git")
    with pytest.raises(private.SnapshotError, match="origin"):
        private.snapshot(target, REPO, TOKEN, source)
    assert network == []


@pytest.mark.parametrize("status", [302, 403, 404])
def test_redirect_or_missing_private_access_never_exports(repos, monkeypatch, status):
    source, target, _, _, network = repos
    monkeypatch.setattr(private.requests, "get", lambda *a, **k:
                        type("Response", (), {"status_code": status})())
    with pytest.raises(private.SnapshotError, match=f"HTTP {status}"):
        private.snapshot(target, REPO, TOKEN, source)
    assert not (target / "data").exists() and network == []


def test_failed_push_does_not_claim_remote_verification(repos, monkeypatch):
    source, target, bare, _, _ = repos
    original_git = private.git
    def fail_push(root, *args, env=None):
        if args[0] == "push" and "--dry-run" not in args:
            raise private.SnapshotError("git push failed")
        if args[0] == "fetch":
            pytest.fail("push failure was ignored")
        return original_git(root, *args, env=env)
    monkeypatch.setattr(private, "git", fail_push)
    with pytest.raises(private.SnapshotError, match="push failed"):
        private.snapshot(target, REPO, TOKEN, source)
    assert subprocess.run(["git", "-C", str(bare), "show-ref", "--verify", "refs/heads/main"],
                          capture_output=True).returncode != 0


def test_private_snapshot_verifies_actual_remote_blobs_and_never_retires(repos):
    source, target, bare, _, network = repos
    # An untracked proprietary packet must not be swept into the seed.
    (source / "data/mirror/untracked.csv.gz").write_bytes(b"not for this snapshot")
    report = private.snapshot(target, REPO, TOKEN, source)
    assert report["live_retirement_allowed"] is False
    assert network == ["push", "fetch"]
    assert report["remote_commit"] == git(bare, "rev-parse", "refs/heads/main")
    receipt = json.loads(git(bare, "show", f"main:{private.RECEIPT}"))
    assert receipt["live_retirement_allowed"] is False and receipt["private_readers_verified"] is False
    assert receipt["files"]["data/archive/observations/new.csv.gz"]["rows"] == 1
    assert not (target / "data/mirror/untracked.csv.gz").exists()
    assert not (source / "data/archive/observations/new.csv.gz").exists()
    assert git(target, "status", "--porcelain") == ""
    with pytest.raises(private.SnapshotError, match="already exists"):
        private.snapshot(target, REPO, TOKEN, source)


def test_broken_source_catalog_is_not_published_or_exported(repos):
    source, target, _, _, network = repos
    (source / "data/archive/observations/seed.csv.gz").write_bytes(gzip.compress(b"value\na\nb\n"))
    git(source, "add", ".")
    git(source, "commit", "-qm", "broken catalog")
    with pytest.raises(private.SnapshotError, match="count"):
        private.snapshot(target, REPO, TOKEN, source)
    assert network == [] and not (target / private.RECEIPT).exists()
    assert not (target / "data/archive/observations/new.csv.gz").exists()


def test_public_visibility_change_stops_push(repos, monkeypatch):
    source, target, _, meta, network = repos
    original_seed = private.seed_committed_history
    def seed(*args):
        head = original_seed(*args)
        meta.update(private=False, visibility="public")
        return head
    monkeypatch.setattr(private, "seed_committed_history", seed)
    with pytest.raises(private.SnapshotError, match="private"):
        private.snapshot(target, REPO, TOKEN, source)
    assert network == []


def test_exact_historical_packet_restores_private_catalog_without_replacing_source(repos, tmp_path):
    source, target, bare, _, _ = repos
    original_blob = (source / "data/mirror/cities/seed.csv.gz").read_bytes()
    digest = hashlib.sha256(original_blob).hexdigest()
    changed = gzip.compress(b"value\nchanged\n", mtime=0)
    (source / "data/mirror/cities/seed.csv.gz").write_bytes(changed)
    git(source, "add", ".")
    git(source, "commit", "-qm", "legacy filename replaced")
    source_manifest = (source / private.MIRROR).read_bytes()
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / (digest + ".csv.gz")).write_bytes(original_blob)
    (packet / "receipt.json").write_text(json.dumps({"source_repo": private.SOURCE_REPO,
        "files": [{"path": "data/mirror/cities/seed.csv.gz", "sha256": digest,
                   "rows": 1, "commit": git(source, "rev-parse", "HEAD~1") }]}))
    report = private.snapshot(target, REPO, TOKEN, source, recovery_root=packet)
    assert report["live_retirement_allowed"] is False
    m = json.loads(git(bare, "show", f"main:{private.MIRROR}"))
    record = m["tables"]["cities"]["files"][0]
    assert record["original_file"] == "seed.csv.gz" and record["file"] != "seed.csv.gz"
    assert hashlib.sha256((target / "data/mirror/cities" / record["file"]).read_bytes()).hexdigest() == digest
    assert (target / "data/mirror/cities/seed.csv.gz").read_bytes() == changed
    assert (target / "data/mirror/manifest.before-private-recovery.json").read_bytes() == source_manifest
    assert (source / "data/mirror/cities/seed.csv.gz").read_bytes() == changed
    assert (source / private.MIRROR).read_bytes() == source_manifest


def test_bad_historical_packet_never_publishes_or_exports(repos, tmp_path):
    source, target, _, _, network = repos
    blob = (source / "data/mirror/cities/seed.csv.gz").read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    (source / "data/mirror/cities/seed.csv.gz").write_bytes(gzip.compress(b"value\nwrong\n"))
    git(source, "add", ".")
    git(source, "commit", "-qm", "legacy mismatch")
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / (digest + ".csv.gz")).write_bytes(b"wrong packet")
    (packet / "receipt.json").write_text(json.dumps({"source_repo": private.SOURCE_REPO,
        "files": [{"path": "data/mirror/cities/seed.csv.gz", "sha256": digest,
                   "rows": 1, "commit": "a"*40}]}))
    with pytest.raises(private.SnapshotError):
        private.snapshot(target, REPO, TOKEN, source, recovery_root=packet)
    assert network == [] and not (target / private.RECEIPT).exists()
    assert not (target / "data/archive/observations/new.csv.gz").exists()


def test_remote_corruption_is_not_a_verified_snapshot(repos, monkeypatch):
    source, target, bare, _, _ = repos
    original_git = private.git
    def corrupt(root, *args, env=None):
        result = original_git(root, *args, env=env)
        if args[0] == "push" and "--dry-run" not in args:
            # A different remote commit removed an archived row after our push.
            parent = git(bare, "rev-parse", "main")
            tree = git(bare, "rev-parse", "main~1^{tree}")
            commit = subprocess.run(["git", "-C", str(bare), "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.test", "commit-tree", tree, "-p", parent,
                "-m", "remote changed"], check=True, capture_output=True, text=True).stdout.strip()
            git(bare, "update-ref", "refs/heads/main", commit)
        return result
    monkeypatch.setattr(private, "git", corrupt)
    with pytest.raises(private.SnapshotError, match="Remote private files differ"):
        private.snapshot(target, REPO, TOKEN, source)


@pytest.mark.parametrize("args", [["--commit"], ["--prune-only"],
                                   ["--export-only", "--commit"], ["--pull-releases"]])
def test_private_root_cannot_enter_any_retirement_path(tmp_path, monkeypatch, args):
    monkeypatch.setenv("AD4_PRIVATE_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["archive_observations.py", *args])
    monkeypatch.setattr(archive, "prune_one", lambda *a: pytest.fail("retirement called"))
    monkeypatch.setattr(archive, "_rpc", lambda *a, **k: pytest.fail("database called"))
    assert archive.main() == 1


def test_private_export_validates_access_before_database(repos, monkeypatch):
    _, target, _, meta, _ = repos
    meta.update(private=False, visibility="public")
    monkeypatch.setenv("AD4_PRIVATE_ARCHIVE_ROOT", str(target))
    monkeypatch.setenv("AD4_PRIVATE_ARCHIVE_TOKEN", TOKEN)
    monkeypatch.setattr("sys.argv", ["archive_observations.py", "--export-only", "--repo", REPO])
    monkeypatch.setattr(archive, "export_one", lambda *a: pytest.fail("database export called"))
    with pytest.raises(private.SnapshotError):
        archive.main()


def test_missing_token_has_no_public_token_fallback(repos, monkeypatch, capsys):
    source, target, _, _, _ = repos
    monkeypatch.delenv("AD4_PRIVATE_ARCHIVE_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert private.main(["--private-root", str(target), "--source-root", str(source), "--check-only"]) == 1
    assert TOKEN not in capsys.readouterr().err


def test_private_data_cannot_be_inside_public_checkout(repos):
    source, _, _, _, _ = repos
    with pytest.raises(private.SnapshotError, match="outside"):
        private.validate_destination(source / "private", REPO, TOKEN, source)


def test_git_auth_is_ephemeral_and_disables_sensitive_tracing(monkeypatch):
    monkeypatch.setenv("GIT_TRACE_CURL", "1")
    monkeypatch.setenv("GIT_DIR", "/wrong/repository")
    env = private.git_environment(REPO, TOKEN)
    assert "GIT_TRACE_CURL" not in env and "GIT_DIR" not in env
    assert env["GIT_CONFIG_VALUE_0"] == ""
    assert env["GIT_CONFIG_VALUE_1"].startswith("AUTHORIZATION: basic ")
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_catalog_cannot_escape_root(repos):
    source, _, _, _, _ = repos
    index = source / private.INDEX
    data = json.loads(index.read_text())
    data["datasets"]["observations"]["assets"][0]["asset"] = "../../secret.csv.gz"
    index.write_text(json.dumps(data))
    with pytest.raises(private.SnapshotError, match="asset"):
        private.verify_catalogs(source)


@pytest.mark.parametrize("broken", [False, True])
def test_large_recorded_payload_fields_and_parser_limit_are_preserved(tmp_path, broken):
    import csv
    path = tmp_path / "large.csv.gz"
    text = "payload\n" + ('"' if broken else '') + "x"*200_000 + "\n"
    path.write_bytes(gzip.compress(text.encode(), mtime=0))
    previous = csv.field_size_limit()
    csv.field_size_limit(32)
    try:
        if broken:
            with pytest.raises(private.SnapshotError):
                private.file_receipt(path)
        else:
            assert private.file_receipt(path)["rows"] == 1
        assert csv.field_size_limit() == 32
    finally:
        csv.field_size_limit(previous)
