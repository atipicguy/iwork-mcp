"""MCP server: drives Pages, Numbers and Keynote from Claude Code.

The model writes the content; the Apple apps do the layout, the arithmetic and
the exporting. There is no proprietary format to reconstruct: `.pages`,
`.numbers` and `.key` are compressed Protobuf, and reading them by hand is a
dead end — here they are written by the people who invented them.

**What this server deliberately does not do:**

- it does not close documents it did not open (launching an iWork app reopens
  the ones the user had on screen: closing them would destroy someone's work);
- it does not overwrite existing files (irreversible, and invisible in the
  reply);
- it never concatenates received text into the script — it passes it as an
  argument, because otherwise it would be code injection.

Registration in `~/.claude.json`, under `mcpServers`:

    "iwork": {
      "command": "/path/to/iwork-mcp/.venv/bin/python",
      "args": ["-m", "iwork.server_mcp"],
      "cwd": "/path/to/iwork-mcp"
    }
"""
from __future__ import annotations

import logging
import sys

from mcp.server import MCPServer

from . import keynote, numbers, pages
from .applescript import AppleScriptError, check_app

# On stdio, stdout IS the protocol channel: a line printed there corrupts the
# session, and the symptom is a server that "won't connect", with no explanation.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

server = MCPServer(
    name="iwork",
    version="0.2.0",
    instructions=(
        "Drives Pages, Numbers and Keynote on this Mac: generates documents, "
        "spreadsheets and presentations, and exports them to PDF or Word. "
        "Output is laid out, not just filled in: fitted column widths, a real "
        "header row, a heading hierarchy in documents. Created documents stay "
        "open on screen for the user to see. Does not overwrite existing files "
        "and does not close documents that were already open."
    ),
)


@server.tool()
def iwork_status() -> str:
    """Report which iWork apps respond and with what version.

    Call this first when something fails: it separates a missing app from a
    denied automation permission from a wrong script.
    """
    out = []
    for name in ("Pages", "Numbers", "Keynote"):
        try:
            out.append(f"{name}: version {check_app(name)}")
        except AppleScriptError as e:
            out.append(f"{name}: NOT responding — {e}")
    return "\n".join(out)


@server.tool()
def pages_create(text: str, save_in: str = "") -> str:
    """Create a Pages document with this text and open it on screen.

    The document comes out structured rather than flat: the first line becomes
    the title, and lines starting with `# ` or `## ` become headings (the marker
    is removed). Everything else is body text.

    Args:
        text: the document content, with the line breaks it should have.
        save_in: path to a `.pages` file to save to. If empty the document is
            only opened, not written to disk. Never overwrites: if the file
            exists, the call fails.
    """
    return f"Created: {pages.create(text, save_in or None)}"


@server.tool()
def pages_read(path: str) -> str:
    """Extract the text of an existing Pages document.

    This is the only practical way to read a `.pages` file: the format is
    compressed Protobuf and no text tool opens it.

    Args:
        path: path to the `.pages` file.
    """
    return pages.read(path)


@server.tool()
def pages_export(path: str, destination: str, fmt: str = "pdf") -> str:
    """Export a Pages document to PDF, Word or EPUB.

    Args:
        path: the source `.pages` file.
        destination: where to write the exported file.
        fmt: `pdf`, `word` or `epub`.
    """
    return f"Exported to {pages.export(path, destination, fmt)}"


@server.tool()
def pages_replace(path: str, search: str, replacement: str) -> str:
    """Replace text in an existing Pages document, and save it.

    Preserves formatting: only the paragraphs containing the searched string are
    touched, and the others keep their size, colour and style. This is the tool
    for filling in a template — a contract, a letter — by replacing placeholders.

    If the string does not appear, the document is left untouched and the call
    fails: better an error than a "done" on a file that stayed the same.

    Args:
        path: the `.pages` file to modify.
        search: the text to find. Does not cross line breaks: look for something
            that sits inside a single paragraph.
        replacement: the text that takes its place.
    """
    n = pages.replace_text(path, search, replacement)
    return f"Replaced in {n} paragraph{'' if n == 1 else 's'}, document saved."


@server.tool()
def pages_append(path: str, text: str) -> str:
    """Append text at the end of an existing Pages document, and save it.

    The added text inherits the last paragraph's style. The rest of the document
    is not touched.

    Args:
        path: the `.pages` file to modify.
        text: the text to append; line breaks become new paragraphs.
    """
    n = pages.append(path, text)
    return f"Appended {n} line{'' if n == 1 else 's'}, document saved."


@server.tool()
def numbers_set(path: str, cells: dict[str, str]) -> str:
    """Write specific cells in an existing Numbers sheet, and save it.

    Cells are addressed as in the app: `{"B7": "1200", "C7": "=B7*0.22"}`. The
    rest of the sheet is left intact, and Numbers recalculates every formula
    that depends on the touched cells — that is the reason this goes through the
    app instead of rewriting the file. An empty value empties the cell.

    Args:
        path: the `.numbers` file to modify.
        cells: A1-notation reference → value. A value starting with `=` is
            inserted as a formula.
    """
    n = numbers.set_cells(path, cells)
    return f"Set {n} cell{'' if n == 1 else 's'}, sheet saved and recalculated."


@server.tool()
def numbers_create(rows: list[list[str]], save_in: str = "",
                   table_name: str = "") -> str:
    """Create a Numbers sheet from a grid of values. First row = headers.

    A cell starting with `=` is inserted as a REAL formula and computed by the
    app: `=SUM(B2:B10)` works. That is the reason to use this instead of writing
    a CSV.

    The result is laid out, not merely filled: one header row, no phantom header
    column, and column widths fitted to the content. Numbers written in any
    common convention (`1360.5`, `1360,5`, `1.360,00`, `1,360.00`) go in as real
    numbers you can sum over.

    Args:
        rows: grid of values; every row must have the same length.
        save_in: path to a `.numbers` file to save to. If empty it is only opened.
        table_name: name for the table and the sheet. Defaults to the file name.
    """
    return f"Created: {numbers.create(rows, save_in or None, table_name or None)}"


@server.tool()
def numbers_read(path: str) -> str:
    """Read back the first table of a Numbers sheet, computed values included.

    Formulas come back with their result, not with the formula text. Numbers are
    localized: on an Italian Mac decimals use a comma.

    Args:
        path: path to the `.numbers` file.
    """
    rows = numbers.read(path)
    return "\n".join(" | ".join(r) for r in rows) or "(empty table)"


@server.tool()
def keynote_layouts() -> str:
    """List the slide layouts available on this Mac.

    Call this BEFORE `keynote_create` if a specific layout is needed: the names
    are localized and the English ones fail on a non-English system.
    """
    return "\n".join(keynote.available_layouts())


@server.tool()
def keynote_create(slides: list[dict], layout: str = "",
                   save_in: str = "") -> str:
    """Generate a Keynote presentation and open it on screen.

    Args:
        slides: list of `{"title": "...", "bullets": ["...", "..."]}`. Bullets
            are optional: without them the slide is title-only.
        layout: localized layout name (see `keynote_layouts`). If empty, the
            first one offering a bullet list is used.
        save_in: path to a `.key` file to save to. If empty it is only opened.
    """
    n, warnings = keynote.create(slides, layout or None, save_in or None)
    reply = f"Presentation created: {n} slides"
    if warnings:
        reply += "\nToo much text to fit:\n" + "\n".join(f"  {w}" for w in warnings)
    return reply


@server.tool()
def keynote_export_pdf(destination: str) -> str:
    """Export the frontmost Keynote presentation to PDF.

    Args:
        destination: path of the PDF to write.
    """
    return f"Exported to {keynote.export_pdf(destination)}"


if __name__ == "__main__":
    server.run(transport="stdio")
