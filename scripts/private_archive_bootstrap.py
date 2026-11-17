"""Bootstrap the approved private destination without retiring live records.

The GitHub connector's installation credential is distinct from the credential
available to Actions. A missing runtime credential stops before any DB export.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from private_archive_snapshot import (
    SOURCE_ROOT, SOURCE_REPO, MIRROR, SnapshotError, checked_path, file_receipt,
    git_environment, snapshot, validate_destination,
)

DESTINATION = "hassansab00/Arbdesk4-archive-001"
# Original committed bytes independently verified in the 16 November recovery.
LEGACY_HISTORY = "780a9246f3492814f584a1dec19d5d3959f70436"


def legacy_recovery(source, output):
    """Recover only exact recorded digests; never repair a value by inference."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise SnapshotError("Keep recovery files outside the source checkout")
    manifest = json.loads((source / MIRROR).read_text())
    missing = []
    for table, state in manifest["tables"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise SnapshotError("Invalid mirror table name")
        for item in state.get("files", []):
            if Path(item["file"]).name != item["file"]:
                raise SnapshotError("Invalid mirror filename")
            relative = f"data/mirror/{table}/{item['file']}"
            path = checked_path(source, relative)
            expected = item.get("sha256")
            if (not isinstance(expected, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", expected)):
                raise SnapshotError("Missing or invalid mirror digest")
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
                continue
            missing.append((relative, item))
    if not missing:
        return None
    if output.exists():
        raise SnapshotError("Recovery output already exists; preserve it for diagnosis")
    fetched = subprocess.run(
        ["git", "-C", str(source), "fetch", "--no-tags", "origin", LEGACY_HISTORY],
        capture_output=True, timeout=180,
    )
    if fetched.returncode:
        raise SnapshotError("Could not fetch exact legacy archive history")
    output.mkdir(parents=True, mode=0o700)
    receipt = {"source_repo": SOURCE_REPO, "files": []}
    for relative, item in missing:
        data = subprocess.run(
            ["git", "-C", str(source), "show", f"{LEGACY_HISTORY}:{relative}"],
            capture_output=True, timeout=180,
        )
        digest = hashlib.sha256(data.stdout).hexdigest()
        if data.returncode or digest != item["sha256"]:
            raise SnapshotError("Original legacy file does not match its recorded digest")
        target = checked_path(output, digest + ".csv.gz")
        if not target.exists():
            with target.open("xb") as handle:
                handle.write(data.stdout)
            target.chmod(0o600)
        verified = file_receipt(target)
        if verified["rows"] != item["rows"]:
            raise SnapshotError("Recovered legacy CSV row count differs from its receipt")
        receipt["files"].append(dict(path=relative, sha256=digest,
                                      rows=item["rows"], commit=LEGACY_HISTORY))
    (output / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return output


def bootstrap(root, mode, *, token=None):
    if mode not in {"check-only", "snapshot"}:
        raise SnapshotError("Only access checks and first snapshots are supported")
    token = token if token is not None else os.environ.get("AD4_PRIVATE_ARCHIVE_TOKEN", "")
    if not token:
        raise SnapshotError("Set AD4_PRIVATE_ARCHIVE_TOKEN for the private destination")
    root = Path(root).resolve()
    source = SOURCE_ROOT.resolve()
    if root.is_relative_to(source) or source.is_relative_to(root):
        raise SnapshotError("Keep private files outside the public source checkout")
    if root.exists():
        raise SnapshotError("Private checkout already exists; preserve it for diagnosis")
    # No credentials in the URL, git config, command arguments or diagnostics.
    env = git_environment(DESTINATION, token)
    try:
        result = subprocess.run(
            ["git", "clone", "--no-tags", f"https://github.com/{DESTINATION}.git", str(root)],
            env=env, capture_output=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        raise SnapshotError("Private checkout failed; no database export ran") from None
    if result.returncode:
        raise SnapshotError("Private checkout failed; verify runtime Contents access")
    if mode == "check-only":
        report = validate_destination(root, DESTINATION, token)
        return dict(report, live_retirement_allowed=False, export_started=False)
    # Validate destination before looking at any recovery or database payload.
    validate_destination(root, DESTINATION, token)
    recovery = legacy_recovery(source, root.parent / (root.name + "-legacy-recovery"))
    return snapshot(root, DESTINATION, token, recovery_root=recovery)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--mode", choices=["check-only", "snapshot"], default="check-only")
    args = parser.parse_args(argv)
    try:
        report = bootstrap(args.private_root, args.mode)
    except (SnapshotError, OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, SnapshotError) else type(exc).__name__
        print(json.dumps({"ok": False, "reason": reason, "live_retirement_allowed": False}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return int(report.get("export_returncode", 0) != 0)


if __name__ == "__main__":
    raise SystemExit(main())
