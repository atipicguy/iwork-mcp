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
    version="0.3.0",
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
def pages_create(text: str, save_in: str = "", template: str = "") -> str:
    """Create a Pages document with this text and open it on screen.

    The document comes out structured rather than flat: the first line becomes
    the title, and lines starting with `# ` or `## ` become headings (the marker
    is removed). Everything else is body text.

    Args:
        text: the document content, with the line breaks it should have.
        save_in: path to a `.pages` file to save to. If empty the document is
            only opened, not written to disk. Never overwrites: if the file
            exists, the call fails.
        template: name of a Pages template (see `pages_templates`). With one,
            the document inherits its typography and layout, and the heading
            face is left to the template instead of being overridden.
    """
    return f"Created: {pages.create(text, save_in or None, template or None)}"


@server.tool()
def pages_templates() -> str:
    """List the Pages templates installed on this Mac (111 on a stock system).

    Names are localized. Creating from one is the difference between a document
    that looks generated and one that looks designed.
    """
    return "\n".join(pages.available_templates())


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
        fmt: `pdf`, `word`, `epub`, `text` (plain) or `rtf`.
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
                   table_name: str = "", column_formats: dict[str, str] | None = None,
                   header_rows: int = 1, merge: list[str] | None = None) -> str:
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
        column_formats: column letter → `auto`, `number`, `currency`, `percent`
            or `text`. The only way to get consistent decimals: AppleScript has
            no decimal-places property, so a column left on `auto` shows 1360
            next to 2349,5.
        header_rows: how many top rows are headers. 2 with `merge` gives a
            two-tier header.
        merge: ranges to merge, like `["H1:I1"]` — one CASSA spanning ENTRATE
            and USCITE.
    """
    return f"Created: {numbers.create(rows, save_in or None, table_name or None, column_formats, header_rows, merge)}"


@server.tool()
def numbers_export(path: str, destination: str, fmt: str = "pdf") -> str:
    """Export a Numbers sheet to PDF, Excel or CSV.

    The CSV is written with the system list separator (`;` on an Italian Mac,
    not a comma) and holds the *formatted* values, so a currency column comes
    out as `100,00 €` rather than `100`.

    Args:
        path: the source `.numbers` file.
        destination: where to write the exported file.
        fmt: `pdf`, `excel` or `csv`.
    """
    return f"Exported to {numbers.export(path, destination, fmt)}"


@server.tool()
def numbers_sort(path: str, column: str, descending: bool = False,
                 header_rows: int = 1, footer_rows: int = 0) -> str:
    """Sort the first table of a Numbers sheet by one column, and save it.

    The header stays put: only the rows below it are reordered.

    Args:
        path: the `.numbers` file to sort.
        column: column letter, as in the app.
        descending: largest first.
        header_rows: how many top rows to leave alone.
        footer_rows: how many bottom rows to leave alone. Set it to 1 when the
            table ends in a TOTAL row: by value that row is the largest in its
            column, so a descending sort lifts it to the top and its formula
            then points at the wrong rows.
    """
    return numbers.sort(path, column, descending, header_rows, footer_rows)


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
                   save_in: str = "", theme: str = "") -> str:
    """Generate a Keynote presentation and open it on screen.

    Args:
        slides: list of `{"title": "...", "bullets": [...], "notes": "..."}`.
            Bullets are optional: without them the slide is title-only. `notes`
            become the presenter notes.
        layout: localized layout name (see `keynote_layouts`). If empty, the
            first one offering a bullet list is used.
        save_in: path to a `.key` file to save to. If empty it is only opened.
        theme: name of a Keynote theme (see `keynote_themes`). Without one the
            deck comes out in the plain default.
    """
    n, warnings = keynote.create(slides, layout or None, save_in or None,
                                 theme or None)
    reply = f"Presentation created: {n} slides"
    if warnings:
        reply += "\nToo much text to fit:\n" + "\n".join(f"  {w}" for w in warnings)
    return reply


@server.tool()
def keynote_themes() -> str:
    """List the Keynote themes installed on this Mac (53 on a stock system).

    Names are localized — the default is "Bianco di base" on an Italian system.
    The theme also sets the slide size: 1920x1080 for some, 1024x768 for others.
    """
    return "\n".join(keynote.available_themes())


@server.tool()
def keynote_export(destination: str, fmt: str = "pdf", notes: bool = False) -> str:
    """Export the frontmost Keynote presentation.

    Args:
        destination: path to write. For `images` this is a new FOLDER of PNGs,
            one per slide, not a single file.
        fmt: `pdf`, `powerpoint` or `images`.
        notes: PDF only — the handout layout with presenter notes printed under
            each slide, which is the form people rehearse from.
    """
    return f"Exported to {keynote.export(destination, fmt, notes)}"


@server.tool()
def keynote_export_pdf(destination: str) -> str:
    """Export the frontmost Keynote presentation to PDF.

    Args:
        destination: path of the PDF to write.
    """
    return f"Exported to {keynote.export_pdf(destination)}"


@server.tool()
def keynote_add_image(path: str, slide: int, image: str, width: int = 0,
                      x: int = -1, y: int = -1) -> str:
    """Put an image on a slide of a saved Keynote deck, and save it.

    Pages cannot do this at all — only Keynote accepts image insertion via
    AppleScript.

    Args:
        path: the `.key` file to modify.
        slide: slide number, counting from 1.
        image: the image file to place.
        width: width in points; 0 keeps the image's own size.
        x: horizontal position from the left edge. Worth setting — left to
            itself Keynote drops the image in the middle, over the bullets.
            An object that would hang off the edge is nudged back inside.
        y: vertical position from the top edge.
    """
    return keynote.add_image(path, slide, image, width, x, y)


@server.tool()
def keynote_add_chart(path: str, slide: int, row_names: list[str],
                      column_names: list[str], data: list[list[float]],
                      chart_type: str = "bar", x: int = -1, y: int = -1) -> str:
    """Put a chart on a slide of a saved Keynote deck, and save it.

    Args:
        path: the `.key` file to modify.
        slide: slide number, counting from 1.
        row_names: one label per data row — these become the series.
        column_names: one label per value inside each row.
        data: one list per row name, each holding one number per column name.
        chart_type: `bar`, `horizontal_bar`, `stacked_bar`, `line`, `pie`, `area`.
        x: horizontal position; without it the chart lands over the bullets.
        y: vertical position.
    """
    return keynote.add_chart(path, slide, row_names, column_names, data,
                             chart_type, x, y)


@server.tool()
def keynote_slide_size(path: str) -> str:
    """The slide dimensions of a saved deck, in points.

    They come from the theme: "Bianco di base" is 1920x1080, "Bianco" is
    1024x768. Ask before choosing coordinates for an image or a chart.

    Args:
        path: the `.key` file.
    """
    w, h = keynote.slide_size(path)
    return f"{w}x{h}"


if __name__ == "__main__":
    server.run(transport="stdio")
