# Pick Path Starter

Working code that re-sequences the stops on a pick order to cut the distance a lift truck
travels. It ships with synthetic sample data so you can run it and see real output in
about a minute, before deciding whether it is worth pointing at your own data.

This is a starter, not a product. It is meant to be read, argued with, and adapted.

---

## Your data never leaves your building

Nothing in this package sends anything anywhere. There are no network calls, no
accounts, no keys, no telemetry, no "phone home". It reads two CSV files off your
disk and writes a third one next to them.

That is deliberate, and it is the point of shipping it this way. You do not have to
send us a location master, an order history, a customer list or a SKU file to find out
whether pick-path optimization is worth anything in your building. You run it on your
own machine, on your own data, and you keep the answer.

When you are ready to adapt it to your real warehouse, `CLIENT_PROMPT.md` walks you
through doing that in-house with an AI assistant — again, on your machine, with your
files staying where they are.

---

## The fastest way to look at this: no install, no terminal

**Double-click `dashboard.html`.** It opens in your browser with the sample data already
loaded. Scroll to **"Try it on your own data"** and drag your two CSV files into the box —
or click it to browse for them.

Everything happens on your machine, in that browser tab. There is no server, nothing is
sent anywhere, and it works with the wifi off — you can check that claim yourself in
[About the numbers](#about-the-numbers) below. If your columns already happen to match
the schema this expects, you have your numbers in a couple of seconds. If they do not —
which is normal; see [Adapting this to your warehouse](#adapting-this-to-your-warehouse)
— it tells you exactly what does not match and what to do next.

This needs nothing but a browser. No Python, no command line, nothing to install.

---

## The other way: the command line

Useful if you want the raw CSV output, want to script this into something else, or
just prefer a terminal. Needs Python 3.10 or newer — nothing else, no packages to
install.

To check what you have:

```bash
python --version
```

If that reports 3.10 or higher, you are ready. If the command is not found, try
`python3 --version`, and use `python3` everywhere below.

Put all the files in one folder, open a terminal in that folder, and run:

```bash
python pick_path.py
```

That reads the included `locations.csv` and `orders.csv`, prints a results table, and
writes `results.csv`.

Other things you can do:

```bash
python pick_path.py --detail
```

prints the full optimized pick sequence for every order, not just the first one.

```bash
python pick_path.py --locations my_locations.csv --orders my_orders.csv --out my_results.csv
```

points it at your own files.

---

## The two input files

**`locations.csv`** — one row per pick slot. This is your location master.

| column | meaning |
|---|---|
| `location_id` | the slot label your people and your system use |
| `aisle` | which aisle the slot is in |
| `bay` | position along the aisle |
| `level` | height (floor, second level, …) |
| `x` | position across the building, in feet |
| `y` | position front-to-back along the aisle, in feet |

`x` and `y` are what the distance math actually uses. `aisle` decides whether two
slots are reachable without leaving the aisle. `bay` and `level` are carried through
for readability — `level` does not change travel distance, because the truck stands
in the same spot on the floor whether the pallet is at floor level or six beams up.

One special row: `STAGING`. That is where the truck starts and ends every trip — your
dock, staging lane, or pack-out. Move it in the CSV and every route re-plans around
the new start point.

**`orders.csv`** — one row per pick line.

| column | meaning |
|---|---|
| `order_id` | groups lines into one order |
| `line` | the sequence the line was issued in |
| `location_id` | which slot to go to; must exist in `locations.csv` |
| `sku` | the item |
| `qty` | how many |

The `line` column matters more than it looks. It is the order your system hands the
work to the operator, which is the route they drive today — the thing being measured
against.

---

## How to read the output

```
ORDER        LINES  STOPS    BASELINE   OPTIMIZED     SAVED   IMPROVED
ORD-1003        25     24       1,244         684       560      45.0%
```

- **LINES** — pick lines on the order.
- **STOPS** — distinct slots the optimized route visits. Lower than LINES when two
  lines sit in the same slot and get picked in one visit.
- **BASELINE** — feet travelled following the sequence exactly as issued, starting
  and ending at staging. This is the current-state route.
- **OPTIMIZED** — feet travelled visiting the same slots in the re-sequenced order.
- **SAVED / IMPROVED** — the difference, and the difference as a percentage.

Then a TOTAL line across all orders, and a spread — best, median and worst order.
**Read the spread, not just the total.** Small orders that already sit in one aisle
have almost nothing to gain, and the sample output shows exactly that. A tool that
claimed a uniform improvement on every order would be lying to you.

`results.csv` has the same figures plus the full optimized sequence for each order,
one row per order, ready to open in Excel.

`results.csv` is not checked in — it is created the first time you run the script.

---

## The floor map

A table of distances is hard to argue with and easy to ignore. The map is the part
people react to. `dashboard.html` in this folder is already built, from the sample data,
ready to open. Rebuild it any time with:

```bash
python make_dashboard.py
```

You get a top-down plan of the floor with both routes drawn on it for whichever order you
click — the route as issued in one colour, the optimized route in the other, with the
stops numbered in pick sequence. Racking is drawn in, and **the route lines go around
it**, out to a cross-aisle and back, because that is what the distance model does. A
straight line through a rack would be a picture of something impossible.

Toggle between the two routes to see the difference on its own. The order list on the
right doubles as the selector, and it sorts and filters — type a slot label like `A03`
to find every order that visits it.

Three things worth knowing about how it is built:

- **It imports `pick_path.py` rather than repeating any of the math.** The map and the
  numbers come from the same distance model, so they cannot drift apart when you change
  the model to match your building.
- **The upload box runs on a JavaScript port of the same engine** — `pickpath_engine.js`,
  hand-written and checked byte-for-byte against `pick_path.py`'s own output (that check is
  what `test_pickpath_engine.js` does; see below). That is the only way "drag a CSV into
  your browser" can compute anything without a server sitting behind it.
- **The file is genuinely self-contained.** No CDN, no fonts fetched from anywhere, no
  scripts loaded over the network, nothing that reports back. It works with the wifi off.
  You can email it to a colleague and it will still work — though see
  [If something goes wrong](#if-something-goes-wrong) if your mail system strips `.py`
  attachments; `.html` usually survives.

When you build it from your own CSVs on the command line, label it so nobody mistakes one
for the other:

```bash
python make_dashboard.py --data-label "Live export, week of 4 Aug"
```

The label prints on the page. It defaults to "Synthetic sample data" — deliberately, so
an unlabelled page understates rather than overstates what it is showing. The upload box
does this automatically too: drag your files in and the label switches to "Your data" on
its own.

---

## Don't trust it — check it

```bash
python test_pick_path.py
```

Thirteen checks, standard library only, about a second to run. They are not style
checks. They test the claim the whole tool rests on: that the distances reported are
distances a truck could actually travel.

The one worth reading is `racking is not passable`. It takes two slots twelve feet apart
in a straight line, deep in adjacent aisles, and asserts the model charges **92 feet** —
out to a cross-aisle, along, and back in. A model that quietly allowed the straight line
would report optimistic numbers on every order and nothing downstream would catch it.

Two more check that the picture cannot flatter the arithmetic: every line drawn on the
map is exactly as long as the distance printed in the table, and no drawn segment ever
crosses racking.

If you have Node.js, there is a second suite for the browser engine:

```bash
node test_pickpath_engine.js
```

Seventeen checks. It runs the same physical checks against `pickpath_engine.js`, then
goes further: it runs `pick_path.py` itself and diffs the JavaScript engine's numbers
against a fresh Python run, row for row, and diffs the JavaScript-rendered map and table
markup against what is actually sitting in `dashboard.html`. This is the check that
matters most for the upload box specifically — it is the difference between "the browser
engine looks right" and "the browser engine provably agrees with the Python original,
today, on this machine."

---

## How the warehouse is modelled

Worth reading, because these assumptions are what you will want to change:

- **Parallel aisles.** Slots in the same aisle are reached by travelling along it.
- **You cannot drive through racking.** To get from one aisle to another the truck
  travels out to a cross-aisle, along it, and back in.
- **Cross-aisles at the front and the back only.** For each move between aisles the
  route takes whichever end is shorter. There are no mid-warehouse cross-overs.
- **Rectilinear travel.** Along an aisle or along a cross-aisle, never diagonally.
- **Every trip starts and ends at staging.**
- **Level does not affect travel distance.** The truck stands in the same spot on the
  floor whether the pallet is at floor level or six beams up. Raising and lowering
  the mast is real time — it is simply not travel, and this tool measures travel.

The optimizer builds two candidate routes — a serpentine sweep (down one aisle, back
up the next) and a nearest-neighbour route — improves both with a 2-opt pass that
un-crosses the route, and keeps whichever came out shorter.

Everything the model needs is read from the CSV files. The cross-aisle positions are
taken from the frontmost and rearmost bays in your location master; no dimensions are
hard-coded in the script.

---

## About the numbers

Every figure printed is computed from the two CSV files at run time. Nothing is
hard-coded, and there is no assumed savings percentage anywhere in the script. Delete a
row from `orders.csv` and the totals change accordingly.

**The sample result is not a forecast for your building.** How much there is to gain
depends almost entirely on how good your current sequence already is:

- If your system issues pick lines in no particular order, there is a lot on the table.
- If it already sorts them by location, there is less — though usually not zero, since
  a plain location sort still runs aisles in the wrong direction and takes the wrong
  end of the building.
- If you already run path optimization, this will tell you so.

The sample orders here are built on the middle assumption — broadly slot-sorted, with
about a quarter of the lines added out of sequence, the way add-ons and re-picks land
on a real order. That is a deliberately conservative starting point. A fully unsorted
sample would have produced a much bigger and much less honest number.

The only way to know your figure is to run it on your data. That is what the rest of
this package is for.

---

## If your file has aisle/bay labels but no x/y

This is the normal case — a WMS tracks slot labels, not physical positions. If your
locations file already has `location_id`, `aisle`, `bay` and `level` columns by those
names and is just missing `x`/`y`, the upload box in `dashboard.html` notices and offers
to build a floor plan from two numbers: the distance between aisle centers, and the
distance between bay centers, both in feet.

**`locations_no_xy.csv` is included so you can try this without your own export.** Same
200 slots as the main sample, alphanumeric aisle labels (`A01`, not `1`) so it exercises
the same label-parsing your real export would need, no `x`/`y` columns, no `STAGING` row —
the shape of a real WMS export. Drop it into Section 1 alongside the included
`orders.csv`, enter `12` and `10`, and you have a real, computed result (not the same
figure as the main sample — that one used a hand-placed staging point this file does not
have, so the depot falls back to a corner and the numbers land close but not identical).

This works when your aisles are evenly spaced and numbered in physical left-to-right
order — the same assumption as pacing off one aisle and multiplying. Type the two
numbers, click **Build floor plan**, and the map and every distance are computed from
them, live, right there — no Claude, no adapter, no re-upload. Paced out a real aisle and
the number does not match? Change the spacing and click again; the whole page updates
instantly, which makes this a fast way to sanity-check a guess, not just a one-shot.

Aisle labels like `A01` are handled automatically — `aisle` and `bay` only need a number
in them somewhere, not a bare integer.

**A wide tunnel splitting the racking down the middle — the "Mid-building cross-aisles"
control right below the two numbers.** A lot of bigger buildings are not just front and
back: there is a second crossing partway through, wide enough for a truck to change
aisles without walking to either end. Add its distance from the front in feet, and every
route near it is measured through the middle instead of forced out to an end it does not
need. The floor map draws it as a real gap in the racking, not a line pretending it is
not there.

**This still does not replace the interview below** for anything more irregular. If your
building has uneven aisle spacing, one-way aisles, zones, multiple docks, or aisle
numbers that do not run in physical order, this box will not even offer itself — your
column names would not all match. That is exactly what `CLIENT_PROMPT.md` is for.

---

## Describing a warehouse you do not have a file for yet

**Section 2 of the dashboard, "Describe your warehouse,"** builds a floor plan from
scratch — no CSV needed at all. Type an aisle count, aisle spacing, bays per aisle, bay
pitch, and any mid-building cross-aisles, click **Preview floor plan**, and it draws
immediately so you can check it against what you actually have on the floor.

Click **Download locations.csv** and you get a real file — generic slot labels
(`A01-B01`, and so on) with the coordinates computed — that you can drop straight into
Section 1 alongside a real orders file, open with `pick_path.py`, or hand to Claude Code
as a starting point if the shape needs to get more irregular than this box can describe.

This is for previewing and sanity-checking a layout, not for relabelling one you already
have — if your locations file already has real slot labels and is just missing
coordinates, use Section 1's recovery panel instead of retyping everything here.

---

## Adapting this to your warehouse

The sample warehouse is a clean grid. Yours is not. Real buildings have uneven aisle
spacing, one-way aisles, pick zones, multiple dock doors, mezzanines, bulk areas that
are not on the grid at all — and a WMS export whose columns are named nothing like the
ones above.

**`CLIENT_PROMPT.md` is a prompt you paste into Claude** (at claude.ai, or Claude Code
if your team uses it) to do that adaptation with you. It instructs the assistant to:

1. Ask you for a **small, anonymized** sample export — never your full production data.
2. Write an adapter that maps your WMS column names onto the schema above, leaving the
   optimizer logic alone.
3. Interview you about your actual layout — aisle spacing, one-way aisles, zones,
   staging points, anything that changes how a truck can travel — and adjust the
   model to match. **If you have a site plan, CAD export, or a photo of a whiteboard
   sketch, it can read that directly** instead of you describing the building in
   words — attach it and say so.
4. Keep every file on your machine.
5. Finish by validating the result against a week of real historical orders, and
   sanity-checking the distance model against one run you can measure yourself.

You do not need to write code to use it. Open `CLIENT_PROMPT.md`, follow the short
instructions at the top, and answer the questions it asks you.

---

## Files in this package

| file | what it is |
|---|---|
| `dashboard.html` | **open this first** — floor map + upload box, pre-built with the sample data |
| `pick_path.py` | the optimizer — the whole thing, commented throughout |
| `pickpath_engine.js` | the same optimizer, ported to JavaScript so the upload box can run it |
| `make_dashboard.py` | builds `dashboard.html`; imports the optimizer, repeats none of it |
| `locations.csv` | synthetic location master: 200 pick slots across 10 aisles, plus staging |
| `locations_no_xy.csv` | the same 200 slots, no coordinates — demos the "Build floor plan" box |
| `orders.csv` | synthetic pick orders: 20 orders, 5–25 lines each — used with either locations file |
| `test_pick_path.py` | proves the Python distance model — run it, do not trust it |
| `test_pickpath_engine.js` | proves the JavaScript engine agrees with the Python one |
| `README.md` | this file |
| `CLIENT_PROMPT.md` | the prompt for adapting this to your warehouse |
| `LICENSE` | MIT |

`results.csv` is generated, not checked in — `dashboard.html` is checked in as a ready-to-open
snapshot built from the sample data, and `python make_dashboard.py` refreshes it any time.

The sample data is generated, not taken from anyone's warehouse.

---

## If something goes wrong

**In the browser (the upload box):**

- **"Not recognized"** — neither file you dropped in has the columns this expects. If
  they came straight out of your WMS, that is normal; open `CLIENT_PROMPT.md` and follow
  it with Claude Code or claude.ai to build an adapter, then drop what it produces in here.
- **"Doesn't have the columns this expects", with a Missing: list** — one file was close
  enough to identify (it has `location_id`, or it has `order_id`/`sku`/`qty`) but is
  missing specific columns, named exactly. If the only thing missing is `x`/`y` and
  `location_id`/`aisle`/`bay`/`level` are all present, you get the **"Build floor plan"**
  box instead — see [If your file has aisle/bay labels but no x/y](#if-your-file-has-aislebay-labels-but-no-xy) above.
- **"Enter a positive number of feet for both"** — the floor-plan box needs both numbers
  before it will build anything; a blank or zero/negative value stops there rather than
  guessing.
- **"Slot(s) picked but not in your location file"** — the two files do not agree on
  which slots exist. Check they came from the same export and cover the same locations.
- **Nothing happens when you drop a file** — check it is a `.csv`; other file types are
  ignored rather than guessed at.
- **A `.py` attachment vanished from an email** — Microsoft blocks `.py`/`.pyc`/`.pyw` by
  default in Outlook on the web. `.html` is not on that list, so send `dashboard.html`
  itself if you need to move this by email.

**On the command line:**

- **`File not found: locations.csv`** — run the script from the folder the CSVs are in,
  or pass `--locations` and `--orders` with full paths.
- **`missing required column(s)`** — the CSV header does not match the schema above.
  This is exactly what the adapter in `CLIENT_PROMPT.md` is for.
- **`location_id(s) in orders.csv are not in locations.csv`** — the two files disagree
  about which slots exist. The script names the offending slots rather than silently
  skipping them, because a bad join quietly dropping picks would corrupt every number
  downstream.

---

## Licence

MIT — see `LICENSE`. Use it, change it, ship it in your own systems, no attribution
required and nothing owed back.

It is published openly for a specific reason: the claim that nothing leaves your building
is only worth as much as your ability to verify it. Search the Python source for `http`,
`requests`, `urllib`, `socket` and the JavaScript for `fetch`, `XMLHttpRequest`, `WebSocket`
— there are no matches in either, and you should not have to take that on faith.
