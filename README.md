# iwork-mcp

An MCP server that drives **Pages, Numbers and Keynote** from Claude Code (or any
MCP client): it generates documents, spreadsheets and presentations, and exports them
to PDF, Word, Excel, CSV, PowerPoint, RTF, plain text or PNG slide images.

The model writes the content; the Apple apps do the layout, the arithmetic and the
exporting. There is no proprietary format to reconstruct — `.pages`, `.numbers` and
`.key` are compressed Protobuf inside a ZIP, and reading them by hand is a dead end.
Here they are written by the people who invented them.

Output is **laid out, not merely filled in**: fitted column widths, real header rows,
merged spanning headers, a heading hierarchy in documents, and any of the 53 Keynote
themes or 111 Pages templates installed on the machine. A generated file should look
like someone made it, not like a data dump that happens to open in Numbers.

## Requirements

macOS with iWork installed, and Python 3.11+.

## Install

```bash
git clone https://github.com/atipicguy/iwork-mcp.git
cd iwork-mcp
uv sync
uv run pytest        # 96 tests, no app windows opened
```

Register it in `~/.claude.json`, under `mcpServers`:

```json
"iwork": {
  "command": "/absolute/path/to/iwork-mcp/.venv/bin/python",
  "args": ["-m", "iwork.server_mcp"],
  "cwd": "/absolute/path/to/iwork-mcp"
}
```

## Local models

The server is a plain stdio MCP server and talks to any MCP host, not only
Claude Code — verified by driving it from a raw JSON-RPC client: `initialize`,
`tools/list`, `tools/call`, no Claude anywhere.

**LM Studio** hosts MCP servers directly. Add it to `~/.lmstudio/mcp.json`:

```json
{
  "mcpServers": {
    "iwork": {
      "command": "/absolute/path/to/iwork-mcp/.venv/bin/python",
      "args": ["-m", "iwork.server_mcp"],
      "cwd": "/absolute/path/to/iwork-mcp",
      "env": { "IWORK_PROFILE": "lean" }
    }
  }
}
```

**Ollama** is a model runner, not an MCP host: it has no way to load this by
itself. It needs a client in between — an MCP-aware front end, or thirty lines
that call `tools/list`, hand the result to `/api/chat` as tools, and post the
model's choice back as `tools/call`. The server side needs nothing.

### `IWORK_PROFILE=lean`

Set it and the server publishes 12 tools with one-line descriptions instead of
20 with full ones. Which of the two you want depends on the model, and the
numbers below are measured on this machine rather than guessed — with the same
three prompts in Italian, asking for a spreadsheet, an Excel export and a sort.

| model | 12 tools (`lean`) | 20 tools (`full`) |
|---|---|---|
| qwen3-8b | works, except the hardest task | **no tool call at all**, 4247 tokens |
| qwen3.6-35b-a3b (MoE) | 4/5 — picked the wrong tool for one | **5/5** |

Two conclusions, both against the obvious guess.

**A 30B-class model does not need a reduced set — it needs the full one.** The
35B scored *worse* on `lean`, because the task needed `numbers_sort` and the
profile did not offer it, so the model reached for the nearest thing it had.
Trimming past what the work requires is its own failure mode. Padded out with
plausible extra tools, the same model stayed correct at 40, 70 and even 117
tools; the cost was latency (14s → 30s) and context (4.5k → 13.5k tokens), not
accuracy. So "too many tools for a local model" is only true of small ones.

**For an 8B, the tool count is not the only wall — the argument schema is.**
Flat-string tools (`numbers_export`, `numbers_sort`, `numbers_read`) are
reliable. `numbers_create`, which takes an array of arrays plus a formats
object, succeeded once in five runs at 9 tools and zero in five at 12: both
effectively unreliable, and the difference between them is noise. It is the
most complex schema in the set by a wide margin, and it is the one that fails.

So `lean` buys an 8B the ability to call *anything*. It does not buy reliable
grid-building, and no amount of trimming will — that needs a simpler input
shape, not a shorter list.

The server cannot pick the profile for you: MCP's `initialize` carries the
*client* name, not which model is behind it. It has to be set in the config.

On first use macOS asks for **automation permission** for each app: grant it, or every
call fails. `iwork_status` is the tool to call first when something is wrong — it
separates a missing app from a denied permission from a broken script.

## Tools

| Tool | What it does |
|---|---|
| `iwork_status` | which apps respond, and with what version |
| `pages_create` / `pages_read` | documents, with a real heading hierarchy |
| `pages_templates` | the 111 installed templates, by name |
| `pages_export` | PDF, Word, EPUB, plain text, RTF |
| `pages_replace` / `pages_append` | edit an existing document **without wrecking its styles** |
| `numbers_create` / `numbers_read` | spreadsheets, with **real formulas**; read them back as text or as results |
| `numbers_set` | write specific cells in an existing sheet; the app recalculates |
| `numbers_sort` | sort by a column, header left in place |
| `numbers_export` | PDF, Excel, CSV |
| `keynote_themes` / `keynote_layouts` | the 53 installed themes and their layouts |
| `keynote_create` | presentations, with presenter notes |
| `keynote_add_image` / `keynote_add_chart` | put an image or one of 6 chart types on a slide |
| `keynote_slide_size` | the theme's slide dimensions, before choosing coordinates |
| `keynote_export` / `keynote_export_pdf` | PDF (optionally with notes), PowerPoint, PNG slide images |

Filling in a document you already have — a contract, a letter — is a sequence of
`pages_replace` calls on its placeholders. Updating a quote is `numbers_set` on three
cells: the formulas that depend on them recalculate by themselves, because Numbers does
it, not us.

Formulas are written as in the app: `=SUM(B2:B3)` is inserted and **computed**. Reading
the cell back gives the result, not the formula text. That is the difference between
driving a spreadsheet and producing a CSV that looks like one.

### Design comes from the app, not from us

The apps ship a lot of design, and none of it is reachable by writing a file by hand.
`keynote_themes` and `pages_templates` list what is installed — 53 and 111 respectively
on a stock Mac — and `keynote_create(theme=...)` / `pages_create(template=...)` build on
one. This is the single biggest difference in how the result looks: the default blank
theme is what makes a generated deck read as generated.

With a Pages template the heading face is left alone deliberately. Forcing Helvetica
headings onto a serif letterhead looks like two documents glued together, so only the
sizes are imposed and the typeface stays the template's.

### Spreadsheets that look like the one you were copying

`header_rows=2` plus `merge=["H1:I1"]` gives the two-tier header real bookkeeping uses:
one CASSA spanning ENTRATE and USCITE. Column widths are fitted to the content and
wrapping is switched off wherever the content already fits, so a notes column stops
dragging every row to three lines tall.

`numbers_sort` leaves the header in place — and takes `footer_rows`, which matters more
than it sounds: a TOTAL row is by value the largest in its column, so a descending sort
lifts it to the top and its formula then points at the wrong rows.

```python
numbers_create(
    rows=[["Guest", "Nights", "CASH", "",      "TOTAL"],
          ["",      "",       "IN",   "OUT",   ""],
          ["Maja Miletic", "7", "1.360,00", "", "=C3-D3"],
          ["Martin Richardson", "5", "1.220,00", "", "=C4-D4"],
          ["TOTAL", "=SUM(B3:B4)", "=SUM(C3:C4)", "", "=SUM(E3:E4)"]],
    save_in="~/Desktop/bookings.numbers",
    header_rows=2,                       # two-tier header
    merge=["C1:D1"],                     # one CASH spanning IN and OUT
    column_formats={"C": "currency", "D": "currency", "E": "currency"},
)
```

### Slides that carry more than bullets

Each slide accepts `notes`, which become the presenter notes;
`keynote_export(fmt="pdf", notes=True)` then produces the handout with the notes printed
under each slide, which is the form people actually rehearse from. `keynote_add_chart`
places one of six chart types, `keynote_add_image` an image. Both take coordinates —
worth passing, because left to itself Keynote centres the object on top of the bullets —
and anything that would hang off the edge is nudged back inside.


## What it deliberately does not do

- **It does not close documents it did not open.** Launching an iWork app reopens the
  ones you had on screen: two real ones came back during development. An
  indiscriminate `close` would throw away someone else's work.
- **It does not overwrite existing files.** That is irreversible and invisible in the
  reply: whoever reads "done" has no way to know what was there before.
- **It never concatenates text into the script.** See below: that would be injection.

## The traps, all paid for

**AppleScript injection.** Interpolating user text into the script source is the same
vulnerability as in SQL: a quote breaks it, a carefully crafted line *executes*. Here
everything goes through `osascript - arg1 arg2` and `on run argv`. Verified with text
containing `tell application "Finder" to beep`: it landed in the document as text and
was not executed.

**`text items of` inside a `tell application` block.** It is sent to the **app** instead
of being evaluated by AppleScript, and the app answers `-1728 can't get`. Cost one
debugging session on Numbers and a second one avoided on Keynote. All text splitting
stays in Python; the apps receive values already separated, one per argument.

**Numbers parses what it is handed according to the system locale.** Measured on an
Italian Mac: `"1360.5"` lands in the cell as *text*, `"1360,5"` becomes the number
1360.5. A canonical decimal therefore produces a column that cannot be summed — and
nothing in the reply says so. Values are parsed in Python (accepting `1360.5`,
`1360,5`, `1.360,00` and `1,360.00`) and re-emitted with this Mac's separator.

**`set bold of paragraph 1 of body text to true` replaces the paragraph's text with the
word "true".** No error. Pages exposes only `size`, `font` and `color` as usable text
properties — `paragraph style`, `alignment`, `space before` and `line spacing` are not
settable at all. Weight comes from a bold face instead. The lesson generalises: with
these apps, checking that a command did not raise is not evidence that it did what you
meant. Read the value back.

**Rewriting `body text` wholesale flattens the formatting.** The worst of the lot,
because the first test hides it: replace one word and rewrite the whole body, and the
text comes out right and looks done — but a 9pt paragraph came back at 28pt, having
inherited the first one's style. On a contract template that means shipping a file that
reads correctly and is laid out wrong. Edits are therefore **targeted**: only the
affected `paragraph N of body text` is reassigned, and the others stay intact (verified:
26 stays 26, 8 stays 8 after a replacement of a different length).

**A closed iWork app is not launched by `tell application`.** The script fails with a
flat `-600, the application is not running`, and so does the first call of every
session. AppleScript's own `launch` and `activate` do not fix it — measured on Keynote,
both still returned -600. Only LaunchServices does: `open -g -a`, with `-g` so the app
does not steal focus mid-task.

**Writing outside the grid does not widen the table.** In Numbers a cell beyond
`row count` is not created: it is a flat `-10006`. The table has to be widened first,
which means translating `AA12` into row 12, column 27.

**Empty cells are `missing value`**, which coerced to a string becomes the *text*
"missing value" — and would land in the data as if someone had typed it.

**Numbers infers types, and gets it wrong silently.** Writing `Giugno` into a cell reads
back `lunedì 1 giugno 2026 alle ore 00:00:00`. No error: just wrong data. The format
must be imposed **before** the value (`set format of cell … to text`), and only on what
is neither a number nor a formula — forcing numbers too would make them unsummable and
break the formulas using them.

**Keynote layout names are localized.** "Title & Bullets" does not exist on an Italian
Mac: it is called "Titolo ed elenco". `keynote_layouts` asks the app instead of listing
them hard-coded, and the missing-layout error hands back the real ones. One of them even
contains an invisible soft hyphen (`Dichiarazio­ne`) — one more reason to copy them from
the app rather than type them.

**Numbers read back are localized.** `1250.5` comes back as `1250,5`. Anyone converting
to float needs to know.

**`ref` and `descending` cannot be used as variable names.** `ref` is short for
`a reference to`, so a script using it does not compile — and the parse error
points at the *following* statement, not the guilty one. `descending` is worse:
it is also the sort-direction enumerator, so the script compiles fine and fails
at run time trying to coerce a constant to a boolean. `mod` is a third one, the
modulo operator. The test suite runs `osacompile` over every script in the
package, which catches the whole first family in milliseconds without opening an
app; the second kind only surfaces when the script actually runs.

**Formulas can be read back, and come back localized.** The `cell` class has a
read-only `formula` property, so `numbers_read(formulas=True)` returns
`=SOMMA(B2:B4)` for a cell written as `=SUM(B2:B4)`. Both halves matter: it is
worth knowing that reading is possible at all, and worth knowing that a
round-trip which rewrites what it reads produces formulas that work in one
language only.

**A CSV export is not comma-separated.** Numbers writes it with the system list
separator — `;` here — and with the *formatted* values, so a currency column
comes out as `100,00 €`.

**Pages cannot insert images.** `make new image` fails on the document
(`Non so come creare TMAScriptImageInfoProxy`) and on its `images` element (an
AppleEvent handler error). Keynote accepts them without complaint. Similarly
**Numbers cannot create sheets**: `make new sheet` fails, though a second
*table* inside an existing sheet works.

**The slide size comes from the theme, not from Keynote.** "Bianco di base" is
1920x1080 and "Bianco" is 1024x768, so coordinates that centre an image in one
put it off the edge of the other. `keynote_slide_size` asks, and anything placed
too close to an edge is nudged back inside — the object's own width is only
knowable after it exists.

**There is no decimal-places property.** Column format (`number`, `currency`, `percent`,
`text`) is settable; the number of decimals is not. A column left on `auto` shows 1360
next to 2349,5. `column_formats={"C": "currency"}` is the way to get consistent
decimals.

**The apps are not named what they seem.** On this machine they are
`Pages Creator Studio.app`, bundle id `com.apple.Pages` — not `Pages.app` nor
`com.apple.iWork.Pages`. Searching for the historical names returns nothing and leads
to the wrong conclusion that iWork is not installed.

## Boundaries

- No writing outside the paths given explicitly in the calls.
- No commits, no pushes.

## License

MIT — see [LICENSE](LICENSE).
