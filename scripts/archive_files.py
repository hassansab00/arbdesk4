"""Atomic publication for immutable archive bytes."""
import os
import tempfile


class ImmutableConflict(RuntimeError):
    pass


def publish_immutable(path, blob):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, staged = tempfile.mkstemp(prefix=".mirror-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staged, path)
        except FileExistsError:
            with open(path, "rb") as existing:
                if existing.read() != blob:
                    raise ImmutableConflict(
                        f"refusing to overwrite a different archive file: {os.path.basename(path)}")
    finally:
        os.unlink(staged)
