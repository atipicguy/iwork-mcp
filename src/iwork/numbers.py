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

from .applescript import AppleScriptError, run
from .cells import (MAX_WIDTH, _column_widths, _coordinates, _letter,
                    _tag)
from .paths import existing as _existing_path, writable as _writable_path

_REFERENCE = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,4}")
"""A cell reference in A1 notation, as the app writes it."""


# Cells arrive already separated, one per argument: argv is read row by row.
# Splitting the text HERE would be a mistake — inside a `tell application`
# block an expression like `text items of` is sent to the APP instead of being
# evaluated by AppleScript, and Numbers answers -1728 "can't get". Cost one
# debugging session: the split stays in Python, where it belongs.
# Styling goes through the `range` class, one call per range instead of one per
# cell. On a 14x13 sheet the old cell-by-cell loop issued 182 property sets plus
# a full second pass for every formatted column; the same layout is now a
# handful of operations. It also unlocks what no cell property offers:
# `text wrap`, `vertical alignment` and `merge`.
_CREATE = '''
on run argv
  set numRows to (item 1 of argv) as integer
  set numCols to (item 2 of argv) as integer
  set savePath to item 3 of argv
  set tableName to item 4 of argv
  set sheetName to item 5 of argv
  set headerRows to (item 6 of argv) as integer
  set numOps to (item 7 of argv) as integer
  set cellBase to 7 + (numOps * 3)
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
            set v to item (cellBase + ((i - 1) * numCols) + j) of argv
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
        set header row count to headerRows
        set header column count to 0
        set footer row count to 0
        repeat with k from 1 to numOps
          set base to 7 + ((k - 1) * 3)
          -- `ref` is an AppleScript keyword (short for `a reference to`) and
          -- using it as a variable is a syntax error about an unexpected end
          -- of line. Same family of trap as `mod`.
          set theRef to item (base + 1) of argv
          set theOp to item (base + 2) of argv
          set theVal to item (base + 3) of argv
          try
            if theOp is "width" then
              set width of column (theRef as integer) to (theVal as integer)
            else if theOp is "merge" then
              merge range theRef
            else if theOp is "wrap" then
              set text wrap of range theRef to (theVal is "1")
            else if theOp is "valign" then
              if theVal is "center" then set vertical alignment of range theRef to center
            else if theOp is "align" then
              if theVal is "center" then
                set alignment of range theRef to center
              else if theVal is "right" then
                set alignment of range theRef to right
              else
                set alignment of range theRef to left
              end if
            else if theOp is "format" then
              if theVal is "currency" then
                set format of range theRef to currency
              else if theVal is "number" then
                set format of range theRef to number
              else if theVal is "percent" then
                set format of range theRef to percent
              else if theVal is "text" then
                set format of range theRef to text
              end if
            end if
          end try
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
        -- not `ref`: that is an AppleScript keyword and the script would not
        -- even compile. The test suite now compiles every script for exactly
        -- this reason.
        set theRef to item (3 + (k * 2)) of argv
        set v to item (4 + (k * 2)) of argv
        if v is "" then
          -- an explicitly empty value means "empty this cell". Writing "" into
          -- it would leave a text cell that merely looks empty, and would keep
          -- counting in COUNTA and breaking SUM ranges.
          clear cell theRef
        else
          if (text 1 of v) is "t" then
            set format of cell theRef to text
          end if
          set value of cell theRef to (text 2 thru -1 of v)
        end if
      end repeat
    end tell
    save doc
    if not wasOpen then close doc saving no
  end tell
  return "ok"
end run
'''


_SORT = '''
on run argv
  set thePath to item 1 of argv
  set theColumn to (item 2 of argv) as integer
  -- not `descending`: that is also the name of the sort-direction enumerator,
  -- and AppleScript resolves it to the constant, then fails coercing it to a
  -- boolean. Compiles fine; breaks only at run time.
  set goDown to (item 3 of argv) is "1"
  set firstRow to (item 4 of argv) as integer
  set skipLast to (item 5 of argv) as integer
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
      set lastRow to (row count) - skipLast
      set lastCol to column count
      -- Sorting the whole table would drag the header into the ordering and
      -- leave "Giorno" filed under G somewhere in the middle. The range starts
      -- below the header rows.
      set theRange to range ((name of cell 1 of row firstRow) & ":" & (name of cell lastCol of row lastRow))
      if goDown then
        sort by column theColumn direction descending in rows theRange
      else
        sort by column theColumn direction ascending in rows theRange
      end if
    end tell
    save doc
    if not wasOpen then close doc saving no
  end tell
  return "ok"
end run
'''


def sort(path: str, column: str, descending: bool = False,
         header_rows: int = 1, footer_rows: int = 0) -> str:
    """Sort the first table by one column, leaving header and footer in place.

    `column` is a letter, as in the app. Only the rows between `header_rows` and
    the last `footer_rows` are reordered.

    `footer_rows` matters more than it looks: a TOTAL row holding `=SUM(C2:C3)`
    is, by value, the largest in its column, so a descending sort lifts it to
    the top and its formula then points at the wrong rows. Measured — the totals
    came back empty. Exclude it.
    """
    clean = column.strip().upper()
    if not clean.isalpha() or not 1 <= len(clean) <= 3:
        raise AppleScriptError(
            f"Invalid column: '{column}'. Expected a letter, like B or AA.")
    if header_rows < 0 or footer_rows < 0:
        raise AppleScriptError("Row counts cannot be negative.")
    _, col = _coordinates(f"{clean}1")
    run(_SORT, _existing_path(path), str(col), "1" if descending else "0",
        str(header_rows + 1), str(footer_rows), app="Numbers")
    return f"sorted by {clean} {'descending' if descending else 'ascending'}"


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


FORMATS = ("auto", "number", "currency", "percent", "text")
_RANGE = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,4}:[A-Z]{1,3}[1-9][0-9]{0,4}")


def create(rows: list[list[object]], save_in: str | None = None,
           table_name: str | None = None,
           column_formats: dict[str, str] | None = None,
           header_rows: int = 1, merge: list[str] | None = None) -> str:
    """Create a sheet from this data. The first row becomes the header.

    A cell starting with `=` is inserted as a formula and evaluated by the app.

    The result is laid out rather than merely filled: header rows marked as
    such, no phantom header column, column widths fitted to the content, and
    wrapping switched off wherever the content already fits on one line.

    `column_formats` maps a column letter to one of `FORMATS` and is the only
    way to get consistent decimals: AppleScript exposes no decimal-places
    property, so a column left on `auto` shows 1360 next to 2349,5.

    `header_rows` together with `merge` reproduces a two-tier header:
    `header_rows=2, merge=["H1:I1"]` puts one CASSA spanning ENTRATE and USCITE.
    """
    if not rows or not rows[0]:
        raise AppleScriptError("At least one row and one column are required.")
    widths = {len(r) for r in rows}
    if len(widths) > 1:
        raise AppleScriptError(
            f"Rows have different lengths ({sorted(widths)}): the grid would "
            f"come out misaligned. Pad them with empty cells.")
    if not 0 <= header_rows < len(rows):
        raise AppleScriptError(
            f"header_rows={header_rows} does not fit {len(rows)} rows: there "
            f"would be no data left under the header.")
    where = _writable_path(save_in, ".numbers") if save_in else ""
    name = table_name or (Path(where).stem if where else "")
    cells = [_tag(c) for r in rows for c in r]
    ops = _layout_ops(rows, column_formats, header_rows, merge)
    return run(_CREATE, str(len(rows)), str(len(rows[0])), where,
               name, name, str(header_rows), str(len(ops) // 3), *ops, *cells,
               app="Numbers")


def _layout_ops(rows: list[list[object]], formats: dict[str, str] | None,
                header_rows: int, merge: list[str] | None) -> list[str]:
    """Build the range operations that turn a filled grid into a laid-out one.

    Flattened into (reference, operation, value) triples because AppleScript
    reads argv positionally; keeping every operation the same width is what
    makes the loop on the other side readable.
    """
    n_rows, n_cols = len(rows), len(rows[0])
    ops: list[str] = []
    for j, width in enumerate(_column_widths(rows), 1):
        letter = _letter(j)
        ops += [str(j), "width", width]
        # Wrapping is what turned a notes column into three-line rows. It stays
        # on only where the content genuinely cannot fit the widest column we
        # allow; everywhere else the row stays one line tall.
        ops += [f"{letter}1:{letter}{n_rows}", "wrap",
                "0" if int(width) < MAX_WIDTH else "1"]
    if header_rows:
        ops += [f"A1:{_letter(n_cols)}{header_rows}", "valign", "center"]
    for ref, fmt in (formats or {}).items():
        clean = fmt.strip().lower()
        if clean not in FORMATS:
            raise AppleScriptError(
                f"Unknown format '{fmt}'. Available: {', '.join(FORMATS)}.")
        if clean == "auto":
            continue
        _, col = _coordinates(f"{ref.strip().upper()}1")
        if not 1 <= col <= n_cols:
            raise AppleScriptError(
                f"Column '{ref}' is outside the grid ({n_cols} columns).")
        # Data rows only: formatting the header too would turn its text into a
        # currency amount.
        letter = _letter(col)
        ops += [f"{letter}{header_rows + 1}:{letter}{n_rows}", "format", clean]
    for ref in (merge or []):
        clean = ref.strip().upper()
        if not _RANGE.fullmatch(clean):
            raise AppleScriptError(
                f"Invalid range to merge: '{ref}'. Expected the app's own "
                f"format, like H1:I1.")
        ops += [clean, "merge", ""]
    return ops


EXPORT_FORMATS = {"pdf": ("PDF", ".pdf"),
                  "excel": ("Microsoft Excel", ".xlsx"),
                  "csv": ("CSV", ".csv")}
"""What Numbers can write out. Verified by exporting each and checking the
result: the PDF opens, the .xlsx is a real Excel 2007+ file, the CSV is text.

The CSV is worth a warning — it is written with the **system list separator**
(`;` on an Italian Mac, not a comma) and holds the *formatted* values, so a
currency column comes out as `100,00 €` rather than `100`."""

_EXPORT = '''
on run argv
  set thePath to item 1 of argv
  set destination to item 2 of argv
  set theFormat to item 3 of argv
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
    if theFormat is "PDF" then
      export doc to file ((POSIX file destination) as string) as PDF
    else if theFormat is "Microsoft Excel" then
      export doc to file ((POSIX file destination) as string) as Microsoft Excel
    else
      export doc to file ((POSIX file destination) as string) as CSV
    end if
    if not wasOpen then close doc saving no
  end tell
  return destination
end run
'''


def export(path: str, destination: str, fmt: str = "pdf") -> str:
    """Export a `.numbers` file to PDF, Excel or CSV."""
    chosen = EXPORT_FORMATS.get(fmt.lower())
    if chosen is None:
        raise AppleScriptError(
            f"Format '{fmt}' is not handled by Numbers. Available: "
            f"{', '.join(EXPORT_FORMATS)}.")
    name, ext = chosen
    return run(_EXPORT, _existing_path(path), _writable_path(destination, ext),
               name, app="Numbers")


def read(path: str) -> list[list[str]]:
    """Read back the first table of a `.numbers` file as rows of strings.

    The values are the *computed* ones: a cell holding a formula comes back with
    its result. They are localized — on an Italian Mac decimals use a comma.
    """
    raw = run(_READ, _existing_path(path), app="Numbers")
    return [r.split("\t") for r in raw.splitlines() if r.strip()]
