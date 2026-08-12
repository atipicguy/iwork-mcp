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

from .applescript import AppleScriptError, run
from .pages import _writable_path

MAX_BULLETS = 6
MAX_BULLET_CHARS = 120
"""Past these, the text stops fitting the slide and Keynote shrinks it until it
is unreadable — silently, so it is only discovered when presenting. Reported as
a warning rather than an error: the deck is still built, the caller decides."""

_LAYOUTS = '''
on run argv
  tell application "Keynote"
    if (count of documents) is 0 then
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

# Title and body arrive already separated, two arguments per slide: splitting
# the text inside a `tell` block would have it interpreted by the APP instead of
# by AppleScript (error -1728). Same trap already paid for in Numbers.
_CREATE = '''
on run argv
  set layoutName to item 1 of argv
  set savePath to item 2 of argv
  set howMany to (item 3 of argv) as integer
  tell application "Keynote"
    set d to make new document
    tell d
      repeat with i from 1 to howMany
        set theTitle to item (2 + (i * 2)) of argv
        set theBody to item (3 + (i * 2)) of argv
        set s to make new slide with properties {base slide:master slide layoutName of d}
        tell s
          set object text of default title item to theTitle
          if theBody is not "" then
            set object text of default body item to theBody
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
  tell application "Keynote"
    if (count of documents) is 0 then error "No presentation is open."
    export document 1 to file ((POSIX file destination) as string) as PDF
  end tell
  return destination
end run
'''


def available_layouts() -> list[str]:
    """The layout names as the app knows them on THIS Mac."""
    return [r for r in run(_LAYOUTS, app="Keynote").splitlines() if r.strip()]


def create(slides: list[dict], layout: str | None = None,
           save_in: str | None = None) -> tuple[str, list[str]]:
    """Generate a presentation. Each entry is `{"title": ..., "bullets": [...]}`.

    The layout, if not given, is the first available one that has a bullet list.

    Returns the slide count and any tidiness warnings — slides carrying more
    text than fits.
    """
    if not slides:
        raise AppleScriptError("At least one slide is required.")
    available = available_layouts()
    if layout is None:
        layout = next((n for n in available if "elenc" in n.lower()
                       or "bullet" in n.lower()), available[0])
    elif layout not in available:
        raise AppleScriptError(
            f"Layout '{layout}' does not exist. The names are localized; on "
            f"this Mac they are: {', '.join(available)}")

    pairs: list[str] = []
    warnings: list[str] = []
    for i, s in enumerate(slides, 1):
        title = str(s.get("title", s.get("titolo", ""))).replace("\n", " ")
        bullets = [str(p) for p in (s.get("bullets") or s.get("punti") or [])]
        warnings += _overflow_warnings(i, title, bullets)
        pairs.append(title)
        pairs.append("\n".join(bullets))
    where = _writable_path(save_in, ".key") if save_in else ""
    return run(_CREATE, layout, where, str(len(slides)), *pairs,
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


def export_pdf(destination: str) -> str:
    """Export the frontmost presentation to PDF."""
    return run(_EXPORT, _writable_path(destination, ".pdf"), app="Keynote")
