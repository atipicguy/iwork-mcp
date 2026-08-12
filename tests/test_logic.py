"""Logic tests, without opening any app.

These separate two faults that look identical from outside: a coding mistake and
a whim of AppleScript. Everything decidable in Python — which cells to force to
text, which paths to refuse, how the arguments are packed — is checked here, in
milliseconds and without windows popping open.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from iwork.applescript import AppleScriptError, rows_to_text
from iwork.numbers import _column_widths, _tag, _to_number
from iwork.pages import _existing_path, _spaced, _style_of, _writable_path


@pytest.fixture(autouse=True)
def italian_locale(monkeypatch):
    """Pin the decimal separator so the suite does not depend on the Mac it
    runs on. The locale-dependent behaviour is the thing under test, so it must
    be an input, not an ambient condition."""
    monkeypatch.setattr("iwork.numbers.decimal_separator", lambda: ",")


class TestCellTyping:
    """Numbers infers value types, and gets it wrong silently."""

    @pytest.mark.parametrize("value", ["Giugno", "Totale", "Mese", "1-2", "3/4",
                                       "40+140", "20-27 Giugno"])
    def test_text_is_forced_to_text(self, value):
        """"Giugno" became "lunedì 1 giugno 2026 alle ore 00:00:00", and "1-2" a
        subtraction. No error, wrong data."""
        assert _tag(value) == "t" + value

    def test_formulas_stay_formulas(self):
        assert _tag("=SUM(B2:B3)") == "x=SUM(B2:B3)"

    def test_an_empty_cell_is_skipped_entirely(self):
        """Regression: an empty cell used to be tagged "x", and AppleScript's
        `text 2 thru -1` of a one-character string is a -1728 that aborted the
        whole sheet. An empty tag makes the script skip the cell."""
        assert _tag("") == ""
        assert _tag(None) == ""


class TestNumberParsing:
    """A number that lands as text is a column you cannot sum over — and
    nothing in the tool's reply says so."""

    @pytest.mark.parametrize("value,expected", [
        ("1360", 1360.0),
        ("-3", -3.0),
        ("0", 0.0),
        ("1360,5", 1360.5),
        ("1360.5", 1360.5),
        ("1.360,00", 1360.0),
        ("1,360.00", 1360.0),
        ("2.349,50", 2349.5),
        ("1.437,50", 1437.5),
    ])
    def test_common_conventions_are_all_numbers(self, value, expected):
        assert _to_number(value) == expected

    @pytest.mark.parametrize("value", ["Giugno", "40+140", "1-2", "", "3/4",
                                       "€ 6.000,00", "1.2.3"])
    def test_non_numbers_stay_text(self, value):
        assert _to_number(value) is None

    def test_thousand_separators_reach_the_app_as_numbers(self):
        """The bug that produced an unsummable "Prima nota": `1.360,00` failed
        `float("1.360.00")` and was written as text, left-aligned next to the
        right-aligned real numbers."""
        assert _tag("1.360,00") == "x1360"
        assert _tag("2.349,50") == "x2349,5"

    def test_the_separator_matches_the_system_one(self):
        """Numbers parses what it is handed by system locale: a canonical
        `1360.5` lands as *text* on an Italian Mac. Verified with osascript."""
        assert _tag("1360.5") == "x1360,5"

    def test_a_lone_dotted_thousand_follows_the_system_reading(self):
        """`1.360` is genuinely ambiguous — 1360 here, 1.36 in the US. It is
        read the way this Mac displays it, matching what the user sees."""
        assert _to_number("1.360") == 1360.0


class TestColumnWidths:
    """Uniform 98pt columns are why a notes column wraps onto three lines while
    an empty one takes the same room."""

    def test_a_long_column_gets_more_room_than_a_short_one(self):
        w = _column_widths([["Giorno", "Descrizione operazione"],
                            ["1", "Prenotazione diretta (Jacopo)"]])
        assert int(w[1]) > int(w[0])

    def test_widths_are_bounded(self):
        w = _column_widths([["x", "y"], ["a" * 400, ""]])
        assert int(w[0]) <= 340
        assert int(w[1]) >= 56

    def test_one_width_per_column(self):
        assert len(_column_widths([["a", "b", "c"], ["1", "2", "3"]])) == 3

    def test_widths_are_integers_for_applescript(self):
        """`as number` on "98.0" fails on an Italian Mac — the coercion is
        locale-dependent. `as integer` on "98" is not."""
        for w in _column_widths([["header"], ["value"]]):
            assert w.isdigit()


class TestDocumentStructure:
    """A generated document arriving as a flat wall of 12pt text is the thing
    this is meant to prevent."""

    def test_the_first_line_becomes_the_title(self):
        size, bold, text = _style_of("Contract", first=True)
        assert bold and size == 26 and text == "Contract"

    def test_markers_are_stripped(self):
        assert _style_of("# Heading", first=False)[2] == "Heading"
        assert _style_of("## Sub", first=False)[2] == "Sub"

    def test_heading_levels_are_ordered(self):
        h1 = _style_of("# A", first=False)[0]
        h2 = _style_of("## B", first=False)[0]
        body = _style_of("C", first=False)[0]
        assert h1 > h2 > body

    def test_body_text_is_not_bold(self):
        assert _style_of("Just a sentence.", first=False)[1] is False

    def test_headings_get_breathing_room(self):
        """Pages has no `space before`, so the only separator available is an
        empty paragraph."""
        assert _spaced(["Title", "Body", "# Heading"]) == [
            "Title", "Body", "", "# Heading"]

    def test_no_double_blank_line(self):
        assert _spaced(["Title", "", "# Heading"]) == ["Title", "", "# Heading"]

    def test_no_blank_line_before_a_leading_heading(self):
        assert _spaced(["# Heading", "Body"]) == ["# Heading", "Body"]


class TestFormats:
    def test_an_unknown_column_format_is_refused(self):
        """Silently dropping it looks like the app not supporting the format,
        and sends the caller debugging the wrong layer."""
        from iwork import numbers
        with pytest.raises(AppleScriptError, match="Unknown format"):
            numbers._column_formats({"A": "money"}, 3)

    def test_a_column_outside_the_grid_is_refused(self):
        from iwork import numbers
        with pytest.raises(AppleScriptError, match="outside the grid"):
            numbers._column_formats({"Z": "currency"}, 3)

    def test_letters_become_column_numbers(self):
        from iwork import numbers
        assert numbers._column_formats({"C": "currency"}, 5) == ["3", "currency"]

    def test_auto_is_a_no_op(self):
        from iwork import numbers
        assert numbers._column_formats({"A": "auto"}, 3) == []


class TestPaths:
    def test_does_not_overwrite_an_existing_file(self, tmp_path: Path):
        """Overwriting is irreversible and invisible in the tool's reply:
        whoever reads "done" has no way to know what was there before."""
        f = tmp_path / "already.pdf"
        f.write_text("precious content")
        with pytest.raises(AppleScriptError, match="Already exists"):
            _writable_path(str(f), ".pdf")
        assert f.read_text() == "precious content"

    def test_fixes_the_extension(self, tmp_path: Path):
        p = _writable_path(str(tmp_path / "rel.txt"), ".pdf")
        assert p.endswith("rel.pdf")

    def test_refuses_a_missing_folder(self, tmp_path: Path):
        with pytest.raises(AppleScriptError, match="No such folder"):
            _writable_path(str(tmp_path / "missing" / "x.pdf"), ".pdf")

    def test_a_missing_file_says_which(self, tmp_path: Path):
        """AppleScript's error for a missing file is a -43 that does not say
        which file: it is checked first, in Python."""
        with pytest.raises(AppleScriptError, match="No such file"):
            _existing_path(str(tmp_path / "ghost.pages"))


class TestSerialization:
    def test_tabs_and_newlines_in_cells_do_not_shift_the_grid(self):
        r = rows_to_text([["a\tb", "c\nd"], ["e", "f"]])
        assert r.splitlines()[0].count("\t") == 1

    def test_none_becomes_an_empty_cell(self):
        assert rows_to_text([[None, "x"]]) == "\tx"


class TestValidation:
    def test_rows_of_different_lengths_are_refused(self):
        """A ragged grid would produce a sheet with cells shifted by one
        column: readable, plausible and wrong."""
        from iwork import numbers
        with pytest.raises(AppleScriptError, match="different lengths"):
            numbers.create([["a", "b"], ["c"]])

    def test_an_empty_grid_is_refused(self):
        from iwork import numbers
        with pytest.raises(AppleScriptError, match="At least one row"):
            numbers.create([])

    def test_a_presentation_with_no_slides_is_refused(self):
        from iwork import keynote
        with pytest.raises(AppleScriptError, match="At least one slide"):
            keynote.create([])

    def test_an_unknown_export_format(self, tmp_path: Path):
        from iwork import pages
        f = tmp_path / "x.pages"
        f.write_text("")
        with pytest.raises(AppleScriptError, match="not handled"):
            pages.export(str(f), str(tmp_path / "y.odt"), "openoffice")


class TestCoordinates:
    """The table is widened before writing: outside the grid is a -10006."""

    @pytest.mark.parametrize("ref,expected", [
        ("A1", (1, 1)), ("B7", (7, 2)), ("Z3", (3, 26)),
        ("AA12", (12, 27)), ("AB1", (1, 28)),
    ])
    def test_a1_notation(self, ref, expected):
        from iwork.numbers import _coordinates
        assert _coordinates(ref) == expected


class TestCellReferences:
    @pytest.mark.parametrize("bad", ["7B", "B", "12", "B-7", "", "A0"])
    def test_invalid_references_are_refused(self, bad, tmp_path: Path):
        """A malformed reference would reach the app as a nonexistent cell name,
        with an error that does not say which of the cells it was."""
        from iwork import numbers
        f = tmp_path / "x.numbers"
        f.write_text("")
        with pytest.raises(AppleScriptError, match="Invalid cell reference"):
            numbers.set_cells(str(f), {bad: "1"})

    def test_no_cells_is_refused(self, tmp_path: Path):
        from iwork import numbers
        f = tmp_path / "x.numbers"
        f.write_text("")
        with pytest.raises(AppleScriptError, match="No cells"):
            numbers.set_cells(str(f), {})


class TestSlideOverflow:
    """Keynote does not refuse overlong text: it shrinks it, and the deck looks
    fine in the reply and unreadable on the projector."""

    def test_too_many_bullets_is_reported(self):
        from iwork.keynote import _overflow_warnings
        assert _overflow_warnings(3, "T", ["b"] * 9)

    def test_an_overlong_bullet_is_reported(self):
        from iwork.keynote import _overflow_warnings
        assert _overflow_warnings(1, "T", ["x" * 200])

    def test_a_reasonable_slide_is_silent(self):
        from iwork.keynote import _overflow_warnings
        assert _overflow_warnings(1, "Title", ["short", "also short"]) == []


class TestReplacement:
    def test_an_empty_search_is_refused(self, tmp_path: Path):
        """Searching for "" would match every paragraph and rewrite the whole
        document — exactly what the targeted edit avoids."""
        from iwork import pages
        f = tmp_path / "x.pages"
        f.write_text("")
        with pytest.raises(AppleScriptError, match="cannot be empty"):
            pages.replace_text(str(f), "", "y")
