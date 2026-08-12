"""Numbers: filling in and reading back spreadsheets.

Formulas are written the way you write them in Numbers, starting with `=`: the
app really evaluates them, and reading the cell back gives the computed result,
not the formula. That is the difference between driving a spreadsheet and
generating a CSV that looks like one.

Two things about numbers, both learned the hard way:

- **What goes in must match the system locale.** Numbers parses the string it
  is given the way the Mac is configured. On an Italian Mac `"1360.5"` becomes
  *text* and `"1360,5"` becomes the number 1360.5. So values are parsed here,
  in Python, and re-emitted with the separator this Mac actually uses.
- **What comes out is localized too.** `1250.5` reads back as `1250,5`. Anyone
  converting it to float needs to know.
"""
from __future__ import annotations

import re
from pathlib import Path

from .applescript import AppleScriptError, decimal_separator, run
from .pages import _existing_path, _writable_path

_REFERENCE = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,4}")
"""A cell reference in A1 notation, as the app writes it."""

MIN_WIDTH, MAX_WIDTH = 56, 340
"""Column width bounds, in points. Below the minimum a header is unreadable;
above the maximum one long note would push every other column off the screen."""

# Cells arrive already separated, one per argument: argv is read row by row.
# Splitting the text HERE would be a mistake — inside a `tell application`
# block an expression like `text items of` is sent to the APP instead of being
# evaluated by AppleScript, and Numbers answers -1728 "can't get". Cost one
# debugging session: the split stays in Python, where it belongs.
_CREATE = '''
on run argv
  set numRows to (item 1 of argv) as integer
  set numCols to (item 2 of argv) as integer
  set savePath to item 3 of argv
  set tableName to item 4 of argv
  set sheetName to item 5 of argv
  tell application "Numbers"
    set d to make new document
    tell sheet 1 of d
      if sheetName is not "" then set name to sheetName
      tell table 1
        -- the default table is smaller than typical data: it must be resized
        -- BEFORE writing, or every out-of-grid cell is an error
        set row count to numRows
        set column count to numCols
        repeat with i from 1 to numRows
          repeat with j from 1 to numCols
            set v to item (5 + numCols + ((i - 1) * numCols) + j) of argv
            if v is not "" then
              set payload to (text 2 thru -1 of v)
              -- The prefix is decided in Python. "t" = keep it text.
              -- Without it Numbers infers the type and "Giugno" becomes
              -- "lunedì 1 giugno 2026 alle ore 00:00:00" — no error, wrong
              -- data. The format must be imposed BEFORE the value.
              if (text 1 of v) is "t" then
                set format of cell j of row i to text
              end if
              set value of cell j of row i to payload
            end if
          end repeat
        end repeat
        -- Tidiness. The default table ships with a header COLUMN, which turns
        -- the first data column into grey bold row labels — wrong for almost
        -- any real dataset, and it is the first thing you notice in a PDF.
        set header row count to 1
        set header column count to 0
        set footer row count to 0
        -- Numbers has no "fit to content" in AppleScript, so widths are
        -- computed in Python from the actual strings and applied here.
        repeat with j from 1 to numCols
          set width of column j to ((item (5 + j) of argv) as integer)
        end repeat
        -- Column formats, applied last and to the DATA rows only: applying
        -- them to the whole column would reformat the header text too.
        set numFormats to (item (5 + numCols + (numRows * numCols) + 1) of argv) as integer
        repeat with k from 1 to numFormats
          set base to 5 + numCols + (numRows * numCols) + 1 + ((k - 1) * 2)
          set col to (item (base + 1) of argv) as integer
          set fmt to item (base + 2) of argv
          repeat with i from 2 to numRows
            try
              if fmt is "currency" then
                set format of cell col of row i to currency
              else if fmt is "number" then
                set format of cell col of row i to number
              else if fmt is "percent" then
                set format of cell col of row i to percent
              else if fmt is "text" then
                set format of cell col of row i to text
              end if
            end try
          end repeat
        end repeat
        if tableName is not "" then set name to tableName
      end tell
    end tell
    if savePath is not "" then
      save d in file ((POSIX file savePath) as string)
    end if
    set docName to name of d
  end tell
  return docName
end run
'''

_READ = '''
on run argv
  set thePath to item 1 of argv
  set f to POSIX file thePath
  tell application "Numbers"
    set wasOpen to false
    set doc to missing value
    repeat with d in documents
      try
        if ((file of d) as string) is (f as string) then
          set wasOpen to true
          set doc to d
          exit repeat
        end if
      end try
    end repeat
    if doc is missing value then set doc to open f
    set out to ""
    tell table 1 of sheet 1 of doc
      set nr to row count
      set nc to column count
      repeat with i from 1 to nr
        repeat with j from 1 to nc
          try
            set raw to value of cell j of row i
            -- an empty cell is `missing value`, which coerced to a string
            -- becomes the text "missing value": it would land in the data as
            -- if someone had actually typed it
            if raw is missing value then
              set v to ""
            else
              set v to raw as string
            end if
          on error
            set v to ""
          end try
          -- explicit concatenation: `row as string` with text item delimiters
          -- in here would go to the app, not to AppleScript
          if j is nc then
            set out to out & v & linefeed
          else
            set out to out & v & tab
          end if
        end repeat
      end repeat
    end tell
    if not wasOpen then close doc saving no
  end tell
  return out
end run
'''


_SET = '''
on run argv
  set thePath to item 1 of argv
  set howMany to (item 2 of argv) as integer
  set f to POSIX file thePath
  tell application "Numbers"
    set wasOpen to false
    set doc to missing value
    repeat with d in documents
      try
        if ((file of d) as string) is (f as string) then
          set wasOpen to true
          set doc to d
          exit repeat
        end if
      end try
    end repeat
    if doc is missing value then set doc to open f
    tell table 1 of sheet 1 of doc
      -- writing outside the grid does not "extend" the sheet: it is a flat
      -- -10006 error. The table is widened first, never narrowed.
      set needRows to (item 3 of argv) as integer
      set needCols to (item 4 of argv) as integer
      if row count < needRows then set row count to needRows
      if column count < needCols then set column count to needCols
      repeat with k from 1 to howMany
        set ref to item (3 + (k * 2)) of argv
        set v to item (4 + (k * 2)) of argv
        if v is "" then
          -- an explicitly empty value means "empty this cell". Writing "" into
          -- it would leave a text cell that merely looks empty, and would keep
          -- counting in COUNTA and breaking SUM ranges.
          clear cell ref
        else
          if (text 1 of v) is "t" then
            set format of cell ref to text
          end if
          set value of cell ref to (text 2 thru -1 of v)
        end if
      end repeat
    end tell
    save doc
    if not wasOpen then close doc saving no
  end tell
  return "ok"
end run
'''


def set_cells(path: str, cells: dict[str, object]) -> int:
    """Write specific cells in an existing sheet, leaving the rest untouched.

    Cells are addressed as in the app: `{"B7": 1200, "C7": "=B7*0.22"}`.
    Formulas are evaluated, and the sheet is saved. An empty value empties the
    cell.

    Recalculating the formulas that depend on the touched cells is Numbers'
    job: that is the reason this goes through the app instead of writing the
    file.
    """
    if not cells:
        raise AppleScriptError("No cells to set.")
    pairs: list[str] = []
    max_row = max_col = 1
    for ref, value in cells.items():
        clean = ref.strip().upper()
        if not _REFERENCE.fullmatch(clean):
            raise AppleScriptError(
                f"Invalid cell reference: '{ref}'. Expected the app's own "
                f"format, like B7 or AA12.")
        row, col = _coordinates(clean)
        max_row, max_col = max(max_row, row), max(max_col, col)
        pairs += [clean, _tag(value)]
    run(_SET, _existing_path(path), str(len(cells)),
        str(max_row), str(max_col), *pairs, app="Numbers")
    return len(cells)


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


FORMATS = ("auto", "number", "currency", "percent", "text")


def create(rows: list[list[object]], save_in: str | None = None,
           table_name: str | None = None,
           column_formats: dict[str, str] | None = None) -> str:
    """Create a sheet from this data. The first row becomes the header.

    A cell starting with `=` is inserted as a formula and evaluated by the app.

    The result is laid out rather than merely filled: one header row, no phantom
    header column, and column widths fitted to the content.

    `column_formats` maps a column letter to one of `FORMATS` and is the only
    way to get consistent decimals: AppleScript exposes no decimal-places
    property, so a column left on `auto` shows 1360 next to 2349,5.
    """
    if not rows or not rows[0]:
        raise AppleScriptError("At least one row and one column are required.")
    widths = {len(r) for r in rows}
    if len(widths) > 1:
        raise AppleScriptError(
            f"Rows have different lengths ({sorted(widths)}): the grid would "
            f"come out misaligned. Pad them with empty cells.")
    where = _writable_path(save_in, ".numbers") if save_in else ""
    name = table_name or (Path(where).stem if where else "")
    cells = [_tag(c) for r in rows for c in r]
    formats = _column_formats(column_formats, len(rows[0]))
    return run(_CREATE, str(len(rows)), str(len(rows[0])), where,
               name, name, *_column_widths(rows), *cells,
               str(len(formats) // 2), *formats, app="Numbers")


def _column_formats(wanted: dict[str, str] | None, columns: int) -> list[str]:
    """Validate the requested formats and flatten them into argv pairs.

    Refused rather than ignored: a silently dropped format looks like the app
    not supporting it, and sends the caller debugging the wrong layer.
    """
    if not wanted:
        return []
    out: list[str] = []
    for ref, fmt in wanted.items():
        clean = fmt.strip().lower()
        if clean not in FORMATS:
            raise AppleScriptError(
                f"Unknown format '{fmt}'. Available: {', '.join(FORMATS)}.")
        if clean == "auto":
            continue
        _, col = _coordinates(f"{ref.strip().upper()}1")
        if not 1 <= col <= columns:
            raise AppleScriptError(
                f"Column '{ref}' is outside the grid ({columns} columns).")
        out += [str(col), clean]
    return out


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


_GROUPED_COMMA_DECIMAL = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$")
"""1.360,00 — dot groups, comma decimates."""
_GROUPED_DOT_DECIMAL = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
"""1,360.00 — comma groups, dot decimates."""
_PLAIN = re.compile(r"^-?\d+([.,]\d+)?$")
"""1360, 1360.5, 1360,5 — one separator at most, so it is the decimal one."""


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


def read(path: str) -> list[list[str]]:
    """Read back the first table of a `.numbers` file as rows of strings.

    The values are the *computed* ones: a cell holding a formula comes back with
    its result. They are localized — on an Italian Mac decimals use a comma.
    """
    raw = run(_READ, _existing_path(path), app="Numbers")
    return [r.split("\t") for r in raw.splitlines() if r.strip()]
