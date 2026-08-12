"""Keynote: generating presentations.

Layout names are **localized**: on an Italian Mac the English "Title & Bullets"
layout is called "Titolo ed elenco", and asking for it by its English name
fails with a -1728. That is why `available_layouts()` asks the app instead of
listing them hard-coded, and why the missing-layout error hands back the real
ones.

One of them even contains an invisible soft hyphen (`Dichiarazio­ne`): one more
reason to copy them from the app rather than type them.
"""
from __future__ import annotations

from .applescript import AppleScriptError, decimal_separator, run
from .paths import existing as _existing_path
from .paths import writable as _writable_path
from .paths import writable_dir as _writable_dir

MAX_BULLETS = 6
MAX_BULLET_CHARS = 120
"""Past these, the text stops fitting the slide and Keynote shrinks it until it
is unreadable — silently, so it is only discovered when presenting. Reported as
a warning rather than an error: the deck is still built, the caller decides."""

_LAYOUTS = '''
on run argv
  set wantedTheme to item 1 of argv
  tell application "Keynote"
    if wantedTheme is not "" then
      set temporary to true
      set d to make new document with properties {document theme:theme wantedTheme}
    else if (count of documents) is 0 then
      set temporary to true
      set d to make new document
    else
      set temporary to false
      set d to document 1
    end if
    set theNames to {}
    repeat with m in (master slides of d)
      set end of theNames to name of m
    end repeat
    if temporary then close d saving no
  end tell
  set savedDelim to AppleScript's text item delimiters
  set AppleScript's text item delimiters to linefeed
  set out to theNames as string
  set AppleScript's text item delimiters to savedDelim
  return out
end run
'''

_THEMES = '''
on run argv
  tell application "Keynote"
    set theNames to {}
    repeat with t in themes
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

# Title and body arrive already separated, two arguments per slide: splitting
# the text inside a `tell` block would have it interpreted by the APP instead of
# by AppleScript (error -1728). Same trap already paid for in Numbers.
_CREATE = '''
on run argv
  set layoutName to item 1 of argv
  set savePath to item 2 of argv
  set howMany to (item 3 of argv) as integer
  set themeName to item 4 of argv
  tell application "Keynote"
    if themeName is "" then
      set d to make new document
    else
      set d to make new document with properties {document theme:theme themeName}
    end if
    tell d
      repeat with i from 1 to howMany
        set theTitle to item (2 + (i * 3)) of argv
        set theBody to item (3 + (i * 3)) of argv
        set theNotes to item (4 + (i * 3)) of argv
        set s to make new slide with properties {base slide:master slide layoutName of d}
        tell s
          set object text of default title item to theTitle
          if theBody is not "" then
            set object text of default body item to theBody
          end if
          if theNotes is not "" then
            set presenter notes to theNotes
          end if
        end tell
      end repeat
      -- Keynote always creates a first slide with the document: it has to go
      -- AFTER ours are added, or the deck opens on an empty one
      if (count of slides) > howMany then delete slide 1
      set n to count of slides
    end tell
    if savePath is not "" then
      save d in file ((POSIX file savePath) as string)
    end if
  end tell
  return n as string
end run
'''

_EXPORT = '''
on run argv
  set destination to item 1 of argv
  set theFormat to item 2 of argv
  set withNotes to (item 3 of argv) is "1"
  tell application "Keynote"
    if (count of documents) is 0 then error "No presentation is open."
    set d to document 1
    if theFormat is "PDF" then
      if withNotes then
        export d to file ((POSIX file destination) as string) as PDF with properties {export style:SlideWithNotes}
      else
        export d to file ((POSIX file destination) as string) as PDF
      end if
    else if theFormat is "PowerPoint" then
      export d to file ((POSIX file destination) as string) as Microsoft PowerPoint
    else
      export d to file ((POSIX file destination) as string) as slide images with properties {image format:PNG}
    end if
  end tell
  return destination
end run
'''

# Images and charts land on a slide of a SAVED deck: they need a document to
# attach to, and addressing it by path is the only reference a tool call can
# carry across invocations.
_SIZE = '''
on run argv
  set thePath to item 1 of argv
  set f to POSIX file thePath
  tell application "Keynote"
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
    set out to (width of doc as string) & tab & (height of doc as string)
    if not wasOpen then close doc saving no
  end tell
  return out
end run
'''

_ADD_IMAGE = '''
on run argv
  set thePath to item 1 of argv
  set slideNo to (item 2 of argv) as integer
  set imagePath to item 3 of argv
  set theWidth to (item 4 of argv) as integer
  set posX to (item 5 of argv) as integer
  set posY to (item 6 of argv) as integer
  set f to POSIX file thePath
  tell application "Keynote"
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
    if slideNo > (count of slides of doc) then error "That slide does not exist."
    set slideW to width of doc
    set slideH to height of doc
    if posX >= slideW or posY >= slideH then
      error "Position {" & posX & ", " & posY & "} is off a " & slideW & "x" & slideH & " slide."
    end if
    tell slide slideNo of doc
      set im to make new image with properties {file:(POSIX file imagePath)}
      if theWidth > 0 then set width of im to theWidth
      if posX >= 0 then set position of im to {posX, posY}
      my fitInside(im, slideW, slideH)
    end tell
    save doc
    if not wasOpen then close doc saving no
  end tell
  return "ok"
end run

-- An object placed near the right edge hangs off it: the position is the
-- top-left corner and the width is only known after creation. Rather than
-- fail on a deck already saved, the object is nudged back inside.
on fitInside(obj, slideW, slideH)
  tell application "Keynote"
    set {px, py} to position of obj
    set ow to width of obj
    set oh to height of obj
    if px + ow > slideW then set px to slideW - ow - 20
    if py + oh > slideH then set py to slideH - oh - 20
    if px < 0 then set px to 20
    if py < 0 then set py to 20
    set position of obj to {px, py}
  end tell
end fitInside
'''

_ADD_CHART = '''
on run argv
  set thePath to item 1 of argv
  set slideNo to (item 2 of argv) as integer
  set chartKind to item 3 of argv
  set posX to (item 6 of argv) as integer
  set posY to (item 7 of argv) as integer
  set nRows to (item 4 of argv) as integer
  set nCols to (item 5 of argv) as integer
  set rowNames to {}
  repeat with i from 1 to nRows
    set end of rowNames to item (7 + i) of argv
  end repeat
  set colNames to {}
  repeat with j from 1 to nCols
    set end of colNames to item (7 + nRows + j) of argv
  end repeat
  set theData to {}
  repeat with i from 1 to nRows
    set oneRow to {}
    repeat with j from 1 to nCols
      set end of oneRow to (item (7 + nRows + nCols + ((i - 1) * nCols) + j) of argv) as number
    end repeat
    set end of theData to oneRow
  end repeat
  set f to POSIX file thePath
  tell application "Keynote"
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
    if slideNo > (count of slides of doc) then error "That slide does not exist."
    set slideW to width of doc
    set slideH to height of doc
    if posX >= slideW or posY >= slideH then
      error "Position {" & posX & ", " & posY & "} is off a " & slideW & "x" & slideH & " slide."
    end if
    tell slide slideNo of doc
      if chartKind is "line" then
        add chart row names rowNames column names colNames data theData type line_2d group by chart row
      else if chartKind is "pie" then
        add chart row names rowNames column names colNames data theData type pie_2d group by chart row
      else if chartKind is "horizontal_bar" then
        add chart row names rowNames column names colNames data theData type horizontal_bar_2d group by chart row
      else if chartKind is "stacked_bar" then
        add chart row names rowNames column names colNames data theData type stacked_vertical_bar_2d group by chart row
      else if chartKind is "area" then
        add chart row names rowNames column names colNames data theData type area_2d group by chart row
      else
        add chart row names rowNames column names colNames data theData type vertical_bar_2d group by chart row
      end if
      set madeChart to chart (count of charts)
      if posX >= 0 then set position of madeChart to {posX, posY}
      my fitInside(madeChart, slideW, slideH)
    end tell
    save doc
    if not wasOpen then close doc saving no
  end tell
  return "ok"
end run

-- An object placed near the right edge hangs off it: the position is the
-- top-left corner and the width is only known after creation. Rather than
-- fail on a deck already saved, the object is nudged back inside.
on fitInside(obj, slideW, slideH)
  tell application "Keynote"
    set {px, py} to position of obj
    set ow to width of obj
    set oh to height of obj
    if px + ow > slideW then set px to slideW - ow - 20
    if py + oh > slideH then set py to slideH - oh - 20
    if px < 0 then set px to 20
    if py < 0 then set py to 20
    set position of obj to {px, py}
  end tell
end fitInside
'''


def available_layouts(theme: str | None = None) -> list[str]:
    """The layout names as the app knows them on THIS Mac.

    Layouts belong to a theme, so asking without one answers for whatever is
    already open — and a name valid there may not exist in the theme actually
    used. Pass the theme to get the list that will apply.
    """
    return [r for r in run(_LAYOUTS, theme or "", app="Keynote").splitlines()
            if r.strip()]


def available_themes() -> list[str]:
    """The installed themes, by name. 53 of them on a stock Mac.

    Localized like everything else here: the default is "Bianco di base" on an
    Italian system, "Basic White" on an English one. Hence asking the app.
    """
    return [r for r in run(_THEMES, app="Keynote").splitlines() if r.strip()]


def create(slides: list[dict], layout: str | None = None,
           save_in: str | None = None,
           theme: str | None = None) -> tuple[str, list[str]]:
    """Generate a presentation. Each entry is `{"title": ..., "bullets": [...]}`.

    The layout, if not given, is the first available one that has a bullet list.

    Returns the slide count and any tidiness warnings — slides carrying more
    text than fits.
    """
    if not slides:
        raise AppleScriptError("At least one slide is required.")
    if theme is not None:
        themes = available_themes()
        if theme not in themes:
            raise AppleScriptError(
                f"Theme '{theme}' does not exist. The names are localized; on "
                f"this Mac there are {len(themes)}, for example: "
                f"{', '.join(themes[:6])}...")
    available = available_layouts(theme)
    if layout is None:
        layout = next((n for n in available if "elenc" in n.lower()
                       or "bullet" in n.lower()), available[0])
    elif layout not in available:
        raise AppleScriptError(
            f"Layout '{layout}' does not exist. The names are localized; on "
            f"this Mac they are: {', '.join(available)}")

    triples: list[str] = []
    warnings: list[str] = []
    for i, s in enumerate(slides, 1):
        title = str(s.get("title", s.get("titolo", ""))).replace("\n", " ")
        bullets = [str(p) for p in (s.get("bullets") or s.get("punti") or [])]
        warnings += _overflow_warnings(i, title, bullets)
        triples += [title, "\n".join(bullets), str(s.get("notes", ""))]
    where = _writable_path(save_in, ".key") if save_in else ""
    return run(_CREATE, layout, where, str(len(slides)), theme or "", *triples,
               app="Keynote"), warnings


def _overflow_warnings(n: int, title: str, bullets: list[str]) -> list[str]:
    """Flag slides that will not fit, before they are presented to a room.

    Keynote does not refuse overlong text: it shrinks it, and a deck that looked
    fine in the tool's reply turns out to have 9pt bullets on the projector.
    """
    out = []
    if len(bullets) > MAX_BULLETS:
        out.append(f"slide {n}: {len(bullets)} bullets (over {MAX_BULLETS}), "
                   f"Keynote will shrink the text")
    long = [b for b in bullets if len(b) > MAX_BULLET_CHARS]
    if long:
        out.append(f"slide {n}: {len(long)} bullet(s) over {MAX_BULLET_CHARS} "
                   f"characters")
    return out


EXPORT_FORMATS = {"pdf": ("PDF", ".pdf"),
                  "powerpoint": ("PowerPoint", ".pptx"),
                  "images": ("images", "")}
"""What Keynote will write out. `images` produces a **folder** of PNGs, one per
slide, not a single file — hence the empty extension and the separate
no-overwrite check."""

CHART_TYPES = ("bar", "horizontal_bar", "stacked_bar", "line", "pie", "area")


def export(destination: str, fmt: str = "pdf", notes: bool = False) -> str:
    """Export the frontmost presentation.

    `notes=True` on a PDF gives the handout layout with the presenter notes
    printed under each slide, which is the form people actually rehearse from.
    """
    chosen = EXPORT_FORMATS.get(fmt.lower())
    if chosen is None:
        raise AppleScriptError(
            f"Format '{fmt}' is not handled by Keynote. Available: "
            f"{', '.join(EXPORT_FORMATS)}.")
    name, ext = chosen
    where = (_writable_dir(destination) if name == "images"
             else _writable_path(destination, ext))
    if notes and name != "PDF":
        raise AppleScriptError(
            f"Presenter notes can only be included in a PDF, not in {fmt}.")
    return run(_EXPORT, where, name, "1" if notes else "0", app="Keynote")


def export_pdf(destination: str) -> str:
    """Export the frontmost presentation to PDF. Kept for the obvious case."""
    return export(destination, "pdf")


def slide_size(path: str) -> tuple[int, int]:
    """The slide dimensions of a saved deck, in points.

    They come from the theme, not from Keynote: "Bianco di base" is 1920x1080
    while "Bianco" is 1024x768. Coordinates that fit one theme land off the
    edge of the other, silently, so positions are checked against the real size.
    """
    w, h = run(_SIZE, _existing_path(path), app="Keynote").split("\t")
    return int(w), int(h)


def add_image(path: str, slide: int, image: str, width: int = 0,
              x: int = -1, y: int = -1) -> str:
    """Put an image on a slide of a saved deck, and save it.

    `width` in points; 0 keeps the image's own size. `x`/`y` position it from
    the top-left corner — **worth setting**, because left to itself Keynote
    drops the object in the middle of the slide, on top of the bullet text.

    Pages cannot do this at all: only Keynote accepts `make new image`.
    """
    if slide < 1:
        raise AppleScriptError("Slides are numbered from 1.")
    run(_ADD_IMAGE, _existing_path(path), str(slide), _existing_path(image),
        str(int(width)), str(int(x)), str(int(y)), app="Keynote")
    return f"image added to slide {slide}"


def add_chart(path: str, slide: int, row_names: list[str],
              column_names: list[str], data: list[list[float]],
              chart_type: str = "bar", x: int = -1, y: int = -1) -> str:
    """Put a chart on a slide of a saved deck, and save it.

    `data` is one list per row name, each holding one number per column name.
    `x`/`y` position it; without them Keynote centres the chart over the body
    text.
    """
    if chart_type not in CHART_TYPES:
        raise AppleScriptError(
            f"Unknown chart type '{chart_type}'. Available: "
            f"{', '.join(CHART_TYPES)}.")
    if not row_names or not column_names:
        raise AppleScriptError("A chart needs at least one row and one column.")
    if len(data) != len(row_names):
        raise AppleScriptError(
            f"{len(data)} data rows for {len(row_names)} row names: the chart "
            f"would be built against the wrong labels.")
    for i, row in enumerate(data):
        if len(row) != len(column_names):
            raise AppleScriptError(
                f"Row {i + 1} has {len(row)} values for "
                f"{len(column_names)} columns.")
    # `as number` inside AppleScript is locale-dependent exactly like the cell
    # values in Numbers: "9161.5" is not coercible on an Italian Mac. Same trap,
    # second place — the separator has to match the system's here too.
    sep = decimal_separator()
    flat = [f"{float(v):.10g}".replace(".", sep) for row in data for v in row]
    run(_ADD_CHART, _existing_path(path), str(slide), chart_type,
        str(len(row_names)), str(len(column_names)), str(int(x)), str(int(y)),
        *row_names, *column_names, *flat, app="Keynote")
    return f"{chart_type} chart added to slide {slide}"
