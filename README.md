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

## What you need

- Python 3.10 or newer.
- Nothing else. The script uses only the Python standard library, so there is nothing
  to install and nothing for IT to approve.

To check what you have, open a terminal (Command Prompt or PowerShell on Windows,
Terminal on Mac) and run:

```bash
python --version
```

If that reports 3.10 or higher, you are ready. If the command is not found, try
`python3 --version`, and use `python3` everywhere below.

---

## How to run it

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
people react to. Build it with:

```bash
python make_dashboard.py
```

That writes `dashboard.html`. Double-click it and it opens in your browser.

You get a top-down plan of the floor with both routes drawn on it for whichever order you
click — the route as issued in one colour, the optimized route in the other, with the
stops numbered in pick sequence. Racking is drawn in, and **the route lines go around
it**, out to a cross-aisle and back, because that is what the distance model does. A
straight line through a rack would be a picture of something impossible.

Toggle between the two routes to see the difference on its own. The order list on the
right doubles as the selector.

Two things worth knowing about how it is built:

- **It imports `pick_path.py` rather than repeating any of the math.** The map and the
  numbers come from the same distance model, so they cannot drift apart. When you change
  the model to match your building, the map changes with it.
- **The file is genuinely self-contained.** No CDN, no fonts fetched from anywhere, no
  scripts loaded over the network, nothing that reports back. It works with the wifi off.
  You can email it to a colleague and it will still work.

When you run it on your own data, label it so nobody mistakes one for the other:

```bash
python make_dashboard.py --data-label "Live export, week of 4 Aug"
```

The label prints on the page. It defaults to "Synthetic sample data" — deliberately, so
an unlabelled page understates rather than overstates what it is showing.

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
   model to match.
4. Keep every file on your machine.
5. Finish by validating the result against a week of real historical orders, and
   sanity-checking the distance model against one run you can measure yourself.

You do not need to write code to use it. Open `CLIENT_PROMPT.md`, follow the short
instructions at the top, and answer the questions it asks you.

---

## Files in this package

| file | what it is |
|---|---|
| `pick_path.py` | the optimizer — the whole thing, commented throughout |
| `make_dashboard.py` | builds the floor map page; imports the optimizer, repeats none of it |
| `locations.csv` | synthetic location master: 200 pick slots across 10 aisles, plus staging |
| `orders.csv` | synthetic pick orders: 20 orders, 5–25 lines each |
| `test_pick_path.py` | proves the distance model — run it, do not trust it |
| `README.md` | this file |
| `CLIENT_PROMPT.md` | the prompt for adapting this to your warehouse |
| `LICENSE` | MIT |

`results.csv` and `dashboard.html` are generated, not checked in.

The sample data is generated, not taken from anyone's warehouse.

---

## If something goes wrong

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
is only worth as much as your ability to verify it. Search the source for `http`, `requests`,
`urllib`, `socket` — there are no matches, and you should not have to take that on faith.
