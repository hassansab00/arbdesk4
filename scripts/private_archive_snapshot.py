"""Prepare and remotely verify a private recovery snapshot. Never retire rows.

Run --check-only first. --snapshot seeds the committed public archive/mirror,
exports eligible cold rows using the existing guards, and pushes only to the
verified private destination. App and job read-through are a separate gate.
"""
import argparse
import base64
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import requests

from archive_files import ImmutableConflict, publish_immutable

SOURCE_REPO = "hassansab00/arbdesk4"
DEFAULT_REPO = "hassansab00/arbdesk4-private-archive"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
INDEX = "web/public/archive/index.json"
MIRROR = "data/mirror/manifest.json"
RECEIPT = "data/private_archive_snapshot.json"
PATHS = ("data/archive", "data/mirror", INDEX, RECEIPT)


class SnapshotError(RuntimeError):
    pass


def git(root, *args, env=None):
    result = subprocess.run(["git", "-C", str(root), *args], env=env,
                            capture_output=True, text=True, timeout=180)
    if result.returncode:
        # Do not echo remote diagnostics, environment or authentication headers.
        raise SnapshotError(f"git {args[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def git_environment(repo, token):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("GIT_CONFIG_", "GIT_TRACE")) or key in {
            "GIT_CURL_VERBOSE", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        }:
            env.pop(key, None)
    header = "AUTHORIZATION: basic " + base64.b64encode(
        ("x-access-token:" + token).encode()).decode()
    key = f"http.https://github.com/{repo}.git.extraheader"
    env.update(GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0=key, GIT_CONFIG_VALUE_0="",
               GIT_CONFIG_KEY_1=key, GIT_CONFIG_VALUE_1=header,
               GIT_CONFIG_KEY_2="core.hooksPath", GIT_CONFIG_VALUE_2="/dev/null",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_TERMINAL_PROMPT="0")
    return env


def validate_destination(root, repo, token, source=SOURCE_ROOT, require_clean=True):
    if (not re.fullmatch(r"hassansab00/[A-Za-z0-9_.-]+", repo)
            or repo.lower() == SOURCE_REPO.lower()):
        raise SnapshotError("Use a separate approved owner's private repository")
    if not token:
        raise SnapshotError("AD4_PRIVATE_ARCHIVE_TOKEN is required; no public-token fallback")
    root, source = Path(root).resolve(), Path(source).resolve()
    env = git_environment(repo, token)
    if root.is_relative_to(source) or source.is_relative_to(root):
        raise SnapshotError("Keep private data outside the public source checkout")
    if Path(git(root, "rev-parse", "--show-toplevel", env=env)).resolve() != root:
        raise SnapshotError("Private root must be the destination checkout's root")
    allowed_urls = {
        f"https://github.com/{repo}", f"https://github.com/{repo}.git",
    }
    if (git(root, "remote", "get-url", "origin", env=env) not in allowed_urls
            or git(root, "remote", "get-url", "--push", "origin", env=env) not in allowed_urls):
        raise SnapshotError("Destination origin does not match the private repository")
    try:
        response = requests.get(f"https://api.github.com/repos/{repo}",
                                headers={"Authorization": f"Bearer {token}",
                                         "Accept": "application/vnd.github+json",
                                         "X-GitHub-Api-Version": "2022-11-28"},
                                timeout=20, allow_redirects=False)
    except requests.RequestException:
        raise SnapshotError("Could not verify private repository access") from None
    if response.status_code != 200:
        raise SnapshotError(f"Private repository access returned HTTP {response.status_code}")
    meta = response.json()
    if (meta.get("full_name") != repo or meta.get("private") is not True
            or meta.get("visibility") != "private" or meta.get("archived") is not False
            or meta.get("disabled") is not False
            or meta.get("permissions", {}).get("push") is not True
            or meta.get("permissions", {}).get("pull") is not True):
        raise SnapshotError("Destination must be private, active and readable/writable")
    branch = meta.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise SnapshotError("Initialize the private repository before snapshotting")
    git(root, "check-ref-format", "--branch", branch, env=env)
    if git(root, "symbolic-ref", "--short", "HEAD", env=env) != branch:
        raise SnapshotError("Check out the private repository's default branch")
    if require_clean and git(root, "status", "--porcelain", "--untracked-files=all", env=env):
        raise SnapshotError("Destination checkout has uncommitted changes")
    # User-level push=true metadata does not prove this token has contents
    # write permission. Authenticate the receive-pack path without updates.
    git(root, "push", "--dry-run", "origin", f"HEAD:refs/heads/{branch}", env=env)
    return {"repo": repo, "branch": branch, "private": True}


def checked_path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if (not isinstance(relative, str) or Path(relative).is_absolute()
            or not path.resolve().is_relative_to(root) or path.is_symlink()):
        raise SnapshotError("Catalog path escapes its archive root")
    return path


def file_receipt(path):
    blob = path.read_bytes()
    rows = None
    if path.name.endswith(".csv.gz"):
        previous_limit = csv.field_size_limit()
        try:
            text = gzip.decompress(blob).decode()
            csv.field_size_limit(max(previous_limit, len(text)))
            rows = sum(1 for _ in csv.DictReader(io.StringIO(text), strict=True))
        except (OSError, EOFError, UnicodeError, csv.Error):
            raise SnapshotError("Archive file is not a complete readable CSV gzip") from None
        finally:
            csv.field_size_limit(previous_limit)
    return {"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob), "rows": rows}


def verify_catalogs(root):
    """Verify every indexed archive and mirror file, not just a sample."""
    root = Path(root)
    index = json.loads((root / INDEX).read_text())
    mirror = json.loads((root / MIRROR).read_text())
    receipts = {}
    def check(relative, rows, digest=None):
        if type(rows) is not int or rows < 0:
            raise SnapshotError("Invalid catalog row count")
        receipt = file_receipt(checked_path(root, relative))
        if receipt["rows"] != rows or (digest and receipt["sha256"] != digest):
            raise SnapshotError(f"Catalog row count or digest differs from its file: {relative}")
        receipts[relative] = receipt
    for dataset, entry in index["datasets"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", dataset):
            raise SnapshotError("Invalid archive dataset name")
        for asset in entry.get("assets", []):
            name = asset["asset"]
            if Path(name).name != name or not name.endswith(".csv.gz"):
                raise SnapshotError("Invalid archive asset name")
            check(f"data/archive/{dataset}/{name}", asset["rows"])
    for table, entry in mirror["tables"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise SnapshotError("Invalid mirror table name")
        for item in entry.get("files", []):
            name = item["file"]
            if Path(name).name != name or not name.endswith(".csv.gz"):
                raise SnapshotError("Invalid mirror file name")
            check(f"data/mirror/{table}/{name}", item["rows"], item.get("sha256"))
    for catalog in (INDEX, MIRROR):
        receipts[catalog] = file_receipt(root / catalog)
    return receipts


def recover_mirror_catalog(root, recovery_root=None):
    """Restore exact recorded bytes privately; retain the old files/catalog.

    No network lookup and no guessing: each replacement must match the digest
    and row count already recorded in the committed source manifest.
    """
    root = Path(root)
    original = (root / MIRROR).read_bytes()
    mirror = json.loads(original)
    evidence, plans, cache = None, [], {}
    for table, state in mirror["tables"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise SnapshotError("Invalid mirror table name")
        for item in state.get("files", []):
            name, digest = item["file"], item.get("sha256")
            if Path(name).name != name or not name.endswith(".csv.gz"):
                raise SnapshotError("Invalid mirror file name")
            relative = f"data/mirror/{table}/{name}"
            if relative not in cache:
                path = checked_path(root, relative)
                cache[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if not digest or cache[relative] == digest:
                continue
            if not recovery_root or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise SnapshotError(f"Mirror digest needs exact historical recovery: {relative}")
            if evidence is None:
                packet = json.loads(checked_path(recovery_root, "receipt.json").read_text())
                if packet.get("source_repo") != SOURCE_REPO:
                    raise SnapshotError("Recovery packet is for another source repository")
                evidence = {(x["path"], x["sha256"]): x for x in packet["files"]}
            proof = evidence.get((relative, digest), {})
            if (not re.fullmatch(r"[a-f0-9]{40}", proof.get("commit", ""))
                    or proof.get("rows") != item["rows"]):
                raise SnapshotError("Recovery packet has no matching original receipt")
            payload = checked_path(recovery_root, digest + ".csv.gz")
            verified = file_receipt(payload)
            if verified["sha256"] != digest or verified["rows"] != item["rows"]:
                raise SnapshotError("Recovered payload differs from the recorded version")
            restored = name[:-7] + "-recovered-" + digest + ".csv.gz"
            plans.append((item, payload, f"data/mirror/{table}/{restored}", restored, proof))
    if not plans:
        return 0
    before = root / "data/mirror/manifest.before-private-recovery.json"
    if before.exists() and before.read_bytes() != original:
        raise SnapshotError("Do not replace earlier recovery evidence")
    publish_immutable(before, original)
    for item, payload, relative, restored, proof in plans:
        path = checked_path(root, relative)
        blob = payload.read_bytes()
        if path.exists() and path.read_bytes() != blob:
            raise SnapshotError("Recovered filename already holds different bytes")
        publish_immutable(path, blob)
        item.update(original_file=item["file"], file=restored,
                    recovered_from_git_commit=proof["commit"])
    fd, staged = tempfile.mkstemp(prefix=".mirror-", dir=root / "data/mirror")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(mirror, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, root / MIRROR)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)
    return len(plans)


def seed_committed_history(source, root, recovery_root=None):
    source, root = Path(source).resolve(), Path(root).resolve()
    if git(source, "remote", "get-url", "origin") not in {
        f"https://github.com/{SOURCE_REPO}", f"https://github.com/{SOURCE_REPO}.git",
    }:
        raise SnapshotError("Source is not the approved ArbDesk4 repository")
    if git(root, "ls-tree", "--name-only", "HEAD", "--", *PATHS):
        raise SnapshotError("Private history already exists; do not overwrite or re-export it")
    if git(source, "diff", "--name-only", "HEAD", "--", *PATHS):
        raise SnapshotError("Source archive/mirror has uncommitted changes")
    if recovery_root and Path(recovery_root).resolve().is_relative_to(source):
        raise SnapshotError("Keep recovered packets outside the public source checkout")
    # Only committed files: untracked packets in the source are never swept up.
    entries = git(source, "ls-tree", "-r", "-z", "HEAD", "--", *PATHS[:3])
    for entry in entries.split("\0"):
        if not entry:
            continue
        mode_type, relative = entry.split("\t", 1)
        if mode_type.split()[:2] not in (["100644", "blob"], ["100755", "blob"]):
            raise SnapshotError("Archive seed must contain regular files only")
        src, dest = checked_path(source, relative), checked_path(root, relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
    recover_mirror_catalog(root, recovery_root)
    verify_catalogs(root)
    return git(source, "rev-parse", "HEAD")


def snapshot(root, repo, token, source=SOURCE_ROOT, table="all", recovery_root=None):
    root, source = Path(root).resolve(), Path(source).resolve()
    destination = validate_destination(root, repo, token, source)
    source_head = seed_committed_history(source, root, recovery_root)
    env = git_environment(repo, token)
    env.update(AD4_PRIVATE_ARCHIVE_ROOT=str(root), AD4_PRIVATE_ARCHIVE_TOKEN=token,
               GITHUB_REPOSITORY=repo, GITHUB_REF_NAME=destination["branch"], GITHUB_TOKEN=token)
    command = [sys.executable, str(source / "scripts/archive_observations.py"),
               "--export-only", "--repo", repo, "--table", table]
    result = subprocess.run(command, cwd=source, env=env, timeout=1800)
    receipts = verify_catalogs(root)
    # Capture all copied/exported bytes, including pending candidate receipts.
    for directory in ("data/archive", "data/mirror"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                receipts[relative] = file_receipt(checked_path(root, relative))
    receipt = dict(schema=1, created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                   destination=destination, source_repo=SOURCE_REPO, source_commit=source_head,
                   export_returncode=result.returncode, files=receipts,
                   private_readers_verified=False, live_retirement_allowed=False)
    (root / RECEIPT).write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    validate_destination(root, repo, token, source, require_clean=False)
    git(root, "add", "--", *PATHS, env=env)
    git(root, "-c", "user.name=ArbDesk4 archive", "-c",
        "user.email=archive@users.noreply.github.com", "commit", "-m",
        "Private recovery snapshot; live retirement disabled", env=env)
    git(root, "push", "origin", f"HEAD:refs/heads/{destination['branch']}", env=env)
    git(root, "fetch", "--quiet", "origin", f"refs/heads/{destination['branch']}", env=env)
    # Compare exact remote Git blobs, not just local existence or upload status.
    if git(root, "diff", "--name-only", "HEAD", "FETCH_HEAD", "--", *PATHS):
        raise SnapshotError("Remote private files differ from the verified snapshot")
    validate_destination(root, repo, token, source)
    return {"repo": repo, "remote_commit": git(root, "rev-parse", "FETCH_HEAD", env=env),
            "verified_files": len(receipts), "export_returncode": result.returncode,
            "live_retirement_allowed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--source-root", default=str(SOURCE_ROOT))
    parser.add_argument("--recovery-root", help="verified historical recovery packet outside public source")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--snapshot", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ.get("AD4_PRIVATE_ARCHIVE_TOKEN", "")
    try:
        if args.check_only:
            report = validate_destination(args.private_root, args.repo, token, args.source_root)
        else:
            report = snapshot(args.private_root, args.repo, token, args.source_root,
                              recovery_root=args.recovery_root)
        print(json.dumps(report, sort_keys=True))
        return int(report.get("export_returncode", 0) != 0)
    except (SnapshotError, ImmutableConflict, OSError, ValueError, subprocess.SubprocessError) as exc:
        # Authentication/remote diagnostics are deliberately not included.
        reason = str(exc) if isinstance(exc, SnapshotError) else type(exc).__name__
        print(f"Private snapshot stopped: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
