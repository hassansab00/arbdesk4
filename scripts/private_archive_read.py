"""Read a pinned, verified private snapshot. No network or retention actions.

The caller clones the approved private repository and supplies its verified
commit. Missing private evidence fails closed; public history is not a fallback.
"""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

REPO = "hassansab00/Arbdesk4-archive-001"
RECEIPT = "data/private_archive_snapshot.json"
INDEX = "web/public/archive/index.json"
SOURCE = Path(__file__).resolve().parents[1]


class PrivateArchiveUnavailable(RuntimeError):
    pass


def _git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            timeout=20)
    if result.returncode:
        raise PrivateArchiveUnavailable("Private snapshot Git identity could not be verified")
    return result.stdout


def _path(root, relative):
    path = root / relative
    if (not isinstance(relative, str) or Path(relative).is_absolute()
            or path.is_symlink() or not path.resolve().is_relative_to(root)):
        raise PrivateArchiveUnavailable("Private catalog path escapes its snapshot")
    return path


@lru_cache(maxsize=4)
def _inventory(root_text, commit):
    root = Path(root_text).resolve()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise PrivateArchiveUnavailable("A verified private snapshot commit is required")
    if root.is_relative_to(SOURCE) or SOURCE.is_relative_to(root):
        raise PrivateArchiveUnavailable("Private data must stay outside the public source checkout")
    if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise PrivateArchiveUnavailable("Private read root must be the destination checkout root")
    if _git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise PrivateArchiveUnavailable("Private checkout differs from its verified commit")
    origin = _git(root, "remote", "get-url", "origin").decode().strip()
    if origin not in {f"https://github.com/{REPO}", f"https://github.com/{REPO}.git"}:
        raise PrivateArchiveUnavailable("Unexpected private snapshot origin")
    raw = _path(root, RECEIPT).read_bytes()
    if raw != _git(root, "show", f"HEAD:{RECEIPT}"):
        raise PrivateArchiveUnavailable("Private snapshot receipt has uncommitted changes")
    receipt = json.loads(raw)
    destination = receipt.get("destination", {})
    if destination.get("repo") != REPO or destination.get("private") is not True:
        raise PrivateArchiveUnavailable("Private snapshot receipt identifies another destination")
    files = receipt.get("files")
    if not isinstance(files, dict) or INDEX not in files:
        raise PrivateArchiveUnavailable("Private snapshot file inventory is missing")
    for relative, item in files.items():
        _path(root, relative)
        if (not isinstance(item, dict)
                or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))
                or type(item.get("bytes")) is not int or item["bytes"] < 0):
            raise PrivateArchiveUnavailable("Invalid private file receipt")
    return files


_verified = {}


def verify_file(path):
    """Verify bytes before the existing parser reads a private file."""
    configured = os.environ.get("AD4_PRIVATE_ARCHIVE_READ_ROOT")
    if not configured:
        return
    root, path = Path(configured).resolve(), Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise PrivateArchiveUnavailable("Private reads cannot fall back to another archive root")
    relative = path.resolve().relative_to(root).as_posix()
    inventory = _inventory(str(root), os.environ.get("AD4_PRIVATE_ARCHIVE_READ_COMMIT", ""))
    expected = inventory.get(relative)
    if expected is None:
        raise PrivateArchiveUnavailable("Private file has no committed verification receipt")
    actual_path = _path(root, relative)
    try:
        stat = actual_path.stat()
        stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        key = (str(root), relative, expected["sha256"])
        if _verified.get(key) != stamp:
            data = actual_path.read_bytes()
            if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise PrivateArchiveUnavailable("Private file bytes differ from their committed receipt")
            _verified[key] = stamp
    except OSError:
        raise PrivateArchiveUnavailable("Verified private file is unavailable") from None


def archive_root(default):
    configured = os.environ.get("AD4_PRIVATE_ARCHIVE_READ_ROOT")
    if not configured:
        return str(default)
    root = Path(configured).resolve()
    try:
        _inventory(str(root), os.environ.get("AD4_PRIVATE_ARCHIVE_READ_COMMIT", ""))
        verify_file(root / INDEX)
    except (OSError, ValueError) as exc:
        raise PrivateArchiveUnavailable("Private snapshot metadata is unreadable") from exc
    return str(root)
