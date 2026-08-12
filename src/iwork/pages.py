"""Pages: creating, reading and exporting documents.

One rule runs through this module: **only close what you opened.** Launching
Pages via AppleScript reopens the documents the user had on screen — two real
ones came back during development of this server. An indiscriminate `close`
would throw away somebody else's work, so every function that opens a file
checks first whether it was already open and in that case leaves it exactly as
it was.
"""
from __future__ import annotations

from pathlib import Path

from .applescript import AppleScriptError, run
from .paths import existing as _existing_path, writable as _writable_path

FORMATS = {"pdf": ("PDF", ".pdf"),
           "word": ("Microsoft Word", ".docx"),
           "epub": ("EPUB", ".epub"),
           "text": ("unformatted text", ".txt"),
           "rtf": ("formatted text", ".rtf")}
"""Everything Pages will write out. `text` throws the formatting away, `rtf`
keeps it — useful when the destination is not a word processor.

Not listed because it does not work: **inserting images**. Both
`make new image` on the document and on its `images` element fail
(`Non so come creare TMAScriptImageInfoProxy`, and an AppleEvent handler
error). Keynote accepts images; Pages does not."""

TITLE_SIZE, H1_SIZE, H2_SIZE, BODY_SIZE = 26, 18, 14, 12
BOLD_FONT = "HelveticaNeue-Bold"
"""Point sizes and heading face for the generated hierarchy.

Pages exposes almost nothing to AppleScript: `paragraph style` is not settable,
`space before` and `line spacing` are not properties at all, and **`bold` is a
trap** — `set bold of paragraph 1 ... to true` raises no error and replaces the
paragraph's text with the word "true". Only `size`, `font` and `color` both
apply and leave the text intact, each verified by reading the paragraph back.
Hence weight comes from a bold face, and `HelveticaNeue-Bold` is the one that
matches the default template (whose body font is `HelveticaNeue`)."""

# Styles are applied paragraph by paragraph AFTER the text is in place: the
# whole point is that a generated document should not arrive as one flat wall
# of 12pt text.
_TEMPLATES = '''
on run argv
  tell application "Pages"
    set theNames to {}
    repeat with t in templates
      set end of theNames to name of t
    end repeat
  end tell
  set savedDelim to AppleScript's text item delimiters
  set AppleScript's text item delimiters to linefeed
  set out to theNames as string
  set AppleScript's text item delimiters to savedDelim
  return out
end run
'''

_CREATE = '''
on run argv
  set theText to item 1 of argv
  set savePath to item 2 of argv
  set howMany to (item 3 of argv) as integer
  set templateName to item 4 of argv
  tell application "Pages"
    if templateName is "" then
      set d to make new document
    else
      set d to make new document with properties {document template:template templateName}
    end if
    set body text of d to theText
    repeat with k from 1 to howMany
      set idx to (item (2 + (k * 3)) of argv) as integer
      set sz to (item (3 + (k * 3)) of argv) as integer
      set faceName to item (4 + (k * 3)) of argv
      try
        set size of paragraph idx of body text of d to sz
        if faceName is not "" then
          set font of paragraph idx of body text of d to faceName
        end if
      end try
    end repeat
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
  tell application "Pages"
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
    set t to (body text of doc) as string
    -- closed only if we opened it: if it was already on the user's screen it
    -- stays where it was
    if not wasOpen then close doc saving no
  end tell
  return t
end run
'''

_EXPORT = '''
on run argv
  set thePath to item 1 of argv
  set destination to item 2 of argv
  set theFormat to item 3 of argv
  set f to POSIX file thePath
  tell application "Pages"
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
    else if theFormat is "Microsoft Word" then
      export doc to file ((POSIX file destination) as string) as Microsoft Word
    else if theFormat is "unformatted text" then
      export doc to file ((POSIX file destination) as string) as unformatted text
    else if theFormat is "formatted text" then
      export doc to file ((POSIX file destination) as string) as formatted text
    else
      export doc to file ((POSIX file destination) as string) as EPUB
    end if
    if not wasOpen then close doc saving no
  end tell
  return destination
end run
'''


# Targeted edit: ONLY the affected paragraphs are rewritten.
#
# Rewriting `body text` wholesale appears to work and does not: the text comes
# out right but every other paragraph's formatting is flattened onto the first
# one's. Measured — a 9pt paragraph came back at 28pt after a replacement made
# elsewhere in the document. On a contract template that means shipping a file
# that reads correctly and is laid out wrong.
# Assigning `paragraph N of body text` leaves the others' styles intact.
_MODIFY = '''
on run argv
  set thePath to item 1 of argv
  set howMany to (item 2 of argv) as integer
  set f to POSIX file thePath
  tell application "Pages"
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
    tell doc
      repeat with k from 1 to howMany
        set idx to (item (1 + (k * 2)) of argv) as integer
        set theText to item (2 + (k * 2)) of argv
        set paragraph idx of body text to theText
      end repeat
    end tell
    save doc
    if not wasOpen then close doc saving no
  end tell
  return "ok"
end run
'''


def paragraphs(path: str) -> list[str]:
    """The document's paragraphs, in the order AppleScript numbers them."""
    return read(path).split("\n")


def replace_text(path: str, search: str, replacement: str) -> int:
    """Replace text in an existing document, preserving formatting.

    Only the paragraphs containing the searched string are touched; the rest
    keep their size, colour and style. Returns how many paragraphs changed.

    A search that spans a line break finds nothing: paragraphs are separate
    units and the string has to be looked for inside one of them.
    """
    if not search:
        raise AppleScriptError("The string to search for cannot be empty.")
    ps = paragraphs(path)
    pairs: list[str] = []
    for i, p in enumerate(ps, 1):
        if search in p:
            pairs += [str(i), p.replace(search, replacement)]
    if not pairs:
        raise AppleScriptError(
            f"'{search}' appears in no paragraph of {Path(path).name}. The "
            f"document was not touched. Mind wrapped text: the search does not "
            f"cross line breaks.")
    run(_MODIFY, _existing_path(path), str(len(pairs) // 2), *pairs, app="Pages")
    return len(pairs) // 2


def append(path: str, text: str) -> int:
    """Append text at the end of an existing document, touching nothing else.

    The added text inherits the last paragraph's style, which is what naturally
    happens when you keep typing at the end.
    """
    ps = paragraphs(path)
    last = len(ps)
    new = ps[-1] + "\n" + text if ps[-1] else text
    run(_MODIFY, _existing_path(path), "1", str(last), new, app="Pages")
    return len(text.split("\n"))


def available_templates() -> list[str]:
    """The installed Pages templates, by name. 111 on a stock Mac.

    Creating from one is the difference between a document that looks generated
    and one that looks designed — the blank default carries no letterhead, no
    margins worth the name and no typographic scale.
    """
    return [r for r in run(_TEMPLATES, app="Pages").splitlines() if r.strip()]


def create(text: str, save_in: str | None = None,
           template: str | None = None) -> str:
    """Create a document with this text. Leaves it open on screen.

    Staying open is deliberate: whoever generates a document almost always
    wants to see it and touch it up. With `save_in` it is also written to disk.

    The document comes out **structured, not flat**: the first line becomes the
    title, and lines starting with `# ` or `## ` become headings with the marker
    removed. Everything else is body text.
    """
    if template is not None:
        names = available_templates()
        if template not in names:
            raise AppleScriptError(
                f"Template '{template}' does not exist. The names are "
                f"localized; on this Mac there are {len(names)}, for example: "
                f"{', '.join(names[:6])}...")
    lines = _spaced(text.split("\n"))
    clean, styles = [], []
    for i, line in enumerate(lines, 1):
        size, bold, body = _style_of(line, first=i == 1)
        clean.append(body)
        # A template brings its own typeface, and forcing Helvetica headings
        # onto a serif letterhead looks like two documents glued together. With
        # a template only the sizes are imposed; the face is the template's.
        styles += [str(i), str(size),
                   BOLD_FONT if (bold and template is None) else ""]
    where = _writable_path(save_in, ".pages") if save_in else ""
    return run(_CREATE, "\n".join(clean), where,
               str(len(lines)), template or "", *styles, app="Pages")


def _spaced(lines: list[str]) -> list[str]:
    """Put a blank line before each heading that does not already have one.

    Pages offers no paragraph spacing to AppleScript — `space before` is not a
    property — so the only way to keep headings from colliding with the text
    above them is an empty paragraph.
    """
    out: list[str] = []
    for line in lines:
        if line.startswith(("# ", "## ")) and out and out[-1].strip():
            out.append("")
        out.append(line)
    return out


def _style_of(line: str, first: bool) -> tuple[int, bool, str]:
    """Decide a line's level, and hand back the text stripped of its marker.

    The `#` convention is borrowed from Markdown because it is what a model
    writes without being asked, and because a document whose headings are just
    longer sentences is the flat wall of text this exists to avoid.
    """
    if line.startswith("## "):
        return H2_SIZE, True, line[3:]
    if line.startswith("# "):
        return H1_SIZE, True, line[2:]
    if first:
        return TITLE_SIZE, True, line
    return BODY_SIZE, False, line


def read(path: str) -> str:
    """Extract the text of an existing `.pages` file."""
    return run(_READ, _existing_path(path), app="Pages")


def export(path: str, destination: str, fmt: str = "pdf") -> str:
    """Export a `.pages` file to PDF, Word, EPUB, plain text or RTF."""
    chosen = FORMATS.get(fmt.lower())
    if chosen is None:
        raise AppleScriptError(
            f"Format '{fmt}' is not handled by Pages. Available: "
            f"{', '.join(FORMATS)}.")
    name, ext = chosen
    return run(_EXPORT, _existing_path(path),
               _writable_path(destination, ext), name, app="Pages")
