"""Path checks done before the app is ever called.

Both checks exist because AppleScript's own failures here are unhelpful: a
missing file is a bare `-43` that does not say which file, and an overwrite is
not a failure at all — it succeeds, silently, and the reply says "done".
"""
from __future__ import annotations

from pathlib import Path

from .applescript import AppleScriptError


def existing(p: str) -> str:
    """Check the file is there, and say which one if it is not."""
    q = Path(p).expanduser()
    if not q.exists():
        raise AppleScriptError(f"No such file: {q}")
    return str(q)


def writable(p: str, extension: str) -> str:
    """Normalize the destination, never overwriting silently.

    Overwriting is irreversible and there is no way to notice it from the tool's
    reply: better an explicit error and a different name.
    """
    q = Path(p).expanduser()
    if q.suffix.lower() != extension:
        q = q.with_suffix(extension)
    if q.exists():
        raise AppleScriptError(
            f"Already exists: {q}. Pick another name — this tool does not "
            f"overwrite, because the previous file would not be recoverable.")
    if not q.parent.is_dir():
        raise AppleScriptError(f"No such folder: {q.parent}")
    return str(q)


def writable_dir(p: str) -> str:
    """A destination folder that must not already exist.

    Keynote's slide-image export writes a *folder* of files, not one file, so
    the no-overwrite rule has to cover directories too.
    """
    q = Path(p).expanduser()
    if q.exists():
        raise AppleScriptError(
            f"Already exists: {q}. Slide images are written into a new folder; "
            f"pick a name that is still free.")
    if not q.parent.is_dir():
        raise AppleScriptError(f"No such folder: {q.parent}")
    return str(q)
