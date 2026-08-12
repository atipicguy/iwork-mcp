"""Running AppleScript, with user text passed as arguments and never concatenated.

This module exists to enforce one rule: **user text never enters the script
source**. It is handed to `osascript` as an argument and picked up by
`on run argv`.

That is not fussiness. Concatenating means a quote in the text breaks the
script, and a carefully crafted line makes it *run*: code injection, identical
in mechanics to the SQL kind. Verified with text containing
`tell application "Finder" to beep` — passed as an argument it landed in the
document as text and was not executed.
"""
from __future__ import annotations

import functools
import subprocess
import time

TIMEOUT = 180
"""iWork apps can take a while to launch the first time: a tight timeout would
turn a slow start into an incomprehensible error."""


class AppleScriptError(RuntimeError):
    """A script failed. Carries the real message from osascript.

    iWork error messages are localized and often name the exact cause (a
    nonexistent layout, a missing file): they must be propagated intact, not
    replaced by an "operation failed" that helps nobody.
    """


def run(script: str, *arguments: str, timeout: int = TIMEOUT,
        app: str | None = None) -> str:
    """Run the script with these arguments; return cleaned stdout.

    Args:
        script: AppleScript source containing an `on run argv` block.
        arguments: values handed to `argv`, in order. They may contain
            anything — quotes, accents, newlines, lines that look like code:
            they stay data.
        app: iWork app the script talks to. Started first if it is not already
            running, because otherwise the first call of every session fails.
    """
    if app:
        ensure_running(app)
        try:
            return _once(script, arguments, timeout)
        except AppleScriptError as e:
            # -609 "the connection is invalid" turns up on the first scripted
            # call after an app has just been launched: the process is up and
            # answers `version`, but the Apple event port is not settled yet.
            # Observed once on Numbers, and it succeeded on an immediate retry.
            if "-609" not in str(e):
                raise
            time.sleep(1.5)
    return _once(script, arguments, timeout)


def _once(script: str, arguments: tuple[str, ...], timeout: int) -> str:
    try:
        p = subprocess.run(
            ["osascript", "-", *arguments],
            input=script, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise AppleScriptError(
            f"The app did not respond within {timeout}s. If it was closed it "
            f"may still be launching: try again."
        ) from e
    if p.returncode != 0:
        raise AppleScriptError(p.stderr.strip() or "osascript failed with no message")
    return p.stdout.strip()


def rows_to_text(rows: list[list[object]]) -> str:
    """Serialize a table as TSV so it fits in a single argument.

    AppleScript cannot read JSON, and one argument per cell would be
    unmanageable. TSV is split on the other side with `text item delimiters`.
    Tabs and newlines inside a cell would break the grid, so they are replaced
    with a space: a spreadsheet cell containing them is almost always a mistake
    by whoever prepared the data, not an intention.
    """
    out = []
    for r in rows:
        out.append("\t".join(
            str("" if c is None else c).replace("\t", " ").replace("\n", " ")
            for c in r))
    return "\n".join(out)


_VERSION = ('on run argv\n'
            'tell application (item 1 of argv) to return version\n'
            'end run')

_running: set[str] = set()


def ensure_running(app: str) -> None:
    """Start the app if it is not up yet, and wait until it answers.

    A closed iWork app does **not** get launched by `tell application`: the
    script fails with a flat `-600, the application is not running`, and so does
    the first call of every session. AppleScript's own `launch` and `activate`
    do not fix it either — measured on Keynote, both still returned -600.
    Only LaunchServices does, and `-g` keeps the app in the background instead
    of stealing the user's focus mid-task.
    """
    if app in _running:
        return
    p = subprocess.run(["open", "-g", "-a", app], capture_output=True, text=True)
    if p.returncode != 0:
        raise AppleScriptError(
            f"Cannot launch {app}: {p.stderr.strip() or 'not found'}. On disk "
            f"the iWork apps may carry a different name than the one they "
            f"answer to — check that {app} is installed.")
    # Launching is not instant, and a script sent too early fails the same way
    # as one sent to a missing app.
    last = ""
    for _ in range(40):
        try:
            run(_VERSION, app, timeout=15)
            _running.add(app)
            return
        except AppleScriptError as e:
            last = str(e)
            time.sleep(0.5)
    raise AppleScriptError(
        f"{app} started but is not answering AppleScript ({last}). If this is "
        f"the first run, grant automation permission in System Settings › "
        f"Privacy & Security › Automation.")


def check_app(name: str) -> str:
    """Return the app version, or raise if it does not respond.

    This separates "the app is missing" from "automation permission was denied"
    from "the script is wrong": three faults that without this check reach the
    user looking identical.
    """
    return run(_VERSION, name, timeout=60, app=name)


@functools.cache
def decimal_separator() -> str:
    """The decimal separator this Mac uses, asked of the system itself.

    Numbers parses the strings it is given **according to the system locale**,
    and AppleScript coerces them the same way. Measured on an Italian Mac:
    `"1360.5"` lands in the cell as *text* while `"1360,5"` becomes the number
    1360.5. Writing a canonical `1360.5` would therefore silently produce an
    unsummable column — the exact failure this function exists to prevent.

    Asked of the app rather than derived from `AppleLocale`, because what
    matters is the separator the coercion actually uses.
    """
    try:
        return "," if "," in run("return (1.5 as string)") else "."
    except AppleScriptError:
        return "."
