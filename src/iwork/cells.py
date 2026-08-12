"""How a Python value becomes a Numbers cell, and back.

Kept apart from the app-driving code because none of it touches AppleScript:
it is pure translation between what a caller writes and what the app will
understand, and it is where the two locale traps live — the one on the way in
(what Numbers accepts) and the one on the way out (what it hands back).
"""
from __future__ import annotations

import re

from .applescript import AppleScriptError, decimal_separator

MIN_WIDTH, MAX_WIDTH = 56, 340
"""Column width bounds, in points. Below the minimum a header is unreadable;
above the maximum one long note would push every other column off the screen."""

_GROUPED_COMMA_DECIMAL = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$")
"""1.360,00 — dot groups, comma decimates."""
_GROUPED_DOT_DECIMAL = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
"""1,360.00 — comma groups, dot decimates."""
_PLAIN = re.compile(r"^-?\d+([.,]\d+)?$")
"""1360, 1360.5, 1360,5 — one separator at most, so it is the decimal one."""

def _coordinates(ref: str) -> tuple[int, int]:
    """From "AA12" to (12, 27): needed to know how far to widen the table.

    Writing into a cell outside the grid does not create it — Numbers answers
    -10006 and writes nothing.
    """
    letters = "".join(c for c in ref if c.isalpha())
    number = int("".join(c for c in ref if c.isdigit()))
    col = 0
    for c in letters:
        col = col * 26 + (ord(c) - ord("A") + 1)
    return number, col

def _letter(col: int) -> str:
    """From 27 back to "AA": the inverse of `_coordinates`, for building refs."""
    out = ""
    while col:
        col, rem = divmod(col - 1, 26)
        out = chr(ord("A") + rem) + out
    return out

def _to_number(s: str) -> float | None:
    """Read a number written in any of the common conventions, or None.

    Accepting only `1360.5` would reject everything a person actually types on
    an Italian keyboard, and — worse — accepting it *silently as text* is how a
    column stops being summable without anyone noticing.

    `1.360` stays ambiguous (1360 here, 1.36 in the US). It is read the way this
    Mac would display it, which is the reading that matches what the user sees
    on screen.
    """
    s = s.strip()
    if not s:
        return None
    if _GROUPED_COMMA_DECIMAL.fullmatch(s):
        return float(s.replace(".", "").replace(",", "."))
    if _GROUPED_DOT_DECIMAL.fullmatch(s):
        return float(s.replace(",", ""))
    if _PLAIN.fullmatch(s):
        body = s.replace(",", ".")
        if s.count(".") == 1 and len(s.split(".")[1]) == 3 \
                and decimal_separator() == ",":
            return float(s.replace(".", ""))  # 1.360 read as thousands
        return float(body)
    return None

def _tag(c: object) -> str:
    """Prefix the value with a letter telling Numbers how to treat it.

    `t` = force text format, `x` = let the app decide, `""` = skip the cell
    entirely. Necessary because Numbers, left to itself, turns "Giugno" into a
    full date and "1-2" into a subtraction: silently, discovered only on
    reading back.

    Numbers are re-emitted with this Mac's decimal separator, because the app
    parses what it is handed according to the system locale — a canonical
    `1360.5` lands as text on an Italian Mac. Formulas pass through untouched.
    """
    s = "" if c is None else str(c)
    if s == "":
        # An empty marker would reach AppleScript as a lone prefix letter, and
        # `text 2 thru -1` of a one-character string is a -1728 error. The
        # empty string makes the script skip the cell, which is what we mean.
        return ""
    if s.startswith("="):
        return "x" + s
    n = _to_number(s)
    if n is None:
        return "t" + s
    text = f"{n:.10g}"
    return "x" + text.replace(".", decimal_separator())

def _column_widths(rows: list[list[object]]) -> list[str]:
    """Fit each column to its longest value, within bounds.

    Numbers exposes no "fit to content" to AppleScript, so the width is
    estimated from the character count. Left uniform, a column of dates and a
    column of long notes get the same 98 points: the notes wrap onto three
    lines and drag the whole row's height with them.
    """
    out = []
    for j in range(len(rows[0])):
        longest = max(len(str("" if r[j] is None else r[j])) for r in rows)
        # ~7.2pt per character at the default body size, plus cell padding;
        # the header row is bold, so it is measured with the same allowance.
        out.append(str(int(min(MAX_WIDTH, max(MIN_WIDTH, longest * 7.2 + 24)))))
    return out
