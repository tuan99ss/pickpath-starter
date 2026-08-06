# Adapting this to your warehouse

This file contains a prompt you hand to an AI assistant. It does the adaptation work
with you — mapping your WMS export onto the starter code, correcting the model to match
how your building actually works, and validating the result against real orders.

**You do not need to know how to code.** The prompt tells the assistant to explain
everything in plain English and to hand you complete files with instructions on where
to put them.

---

## How to use it

1. Open **Claude** — either [claude.ai](https://claude.ai) in a browser, or Claude Code
   if your team already uses it.
2. **Give it the files.**
   - *Claude Code:* open a terminal in this folder and start `claude` there — it can already
     read every file, so there is nothing to attach.
   - *claude.ai in a browser:* attach four files from this folder — `pick_path.py`,
     `pickpath_engine.js`, `make_dashboard.py` and `README.md`.
3. **Copy everything inside the box below** — from `You are helping me…` down to the
   last line — and paste it as your first message, along with those attachments.
4. Answer the questions it asks. It will ask about your building, your export, and your
   picking rules. Plain answers are fine; "I'll have to check" is a fine answer too.

Expect it to take a couple of sessions rather than one. The interview about your layout
is the part that decides whether the numbers mean anything.

---

## Before you start, have these to hand

You do not need all of it up front — the assistant will ask — but the work goes faster
if you know where to find:

- **A sample export of your location master** from your WMS (slot list). A few hundred
  rows is plenty.
- **A sample export of pick orders**, ideally a week's worth, with the line sequence as
  it was issued to the operator.
- **A rough site plan or a sketch.** Aisle numbering, roughly how long the aisles are,
  where the dock and staging sit. A photo of a whiteboard sketch works.
- **Whoever knows the floor rules** — one-way aisles, zones, which areas the standard
  truck never enters. Often a lead or a supervisor rather than IT.

### On the data you share

The prompt instructs the assistant to ask for a **small, anonymized** sample — not your
production files. Practically, that means: strip customer names and addresses, and
replace real SKU numbers with fake ones if your SKU list is commercially sensitive.
Slot labels and coordinates are usually fine as-is, but that is your call to make.

If you are running Claude Code locally, your files stay on your machine. If you are
using claude.ai in a browser, anything you attach is uploaded to Anthropic — so send
the anonymized sample there, and keep the full production run local. Either way, the
optimizer itself never sends anything anywhere.

---

## The prompt — copy everything in this box

````text
You are helping me adapt a pick-path optimization script to my real warehouse. I work
in warehouse operations, not IT. I can run a command if you tell me exactly what to
type, and I can open a CSV in Excel, but I do not write code. Please work with me on
that basis.

You have four files to work from: pick_path.py (the optimizer), pickpath_engine.js (the
same optimizer, ported to JavaScript so it can run inside a browser with no server),
make_dashboard.py (builds a floor map and an upload box as one HTML page), and README.md
(what it does and how it models the warehouse). They are either attached to this message
or in the folder you are running in. Read all four before you ask me anything.

One thing to get right every time you rebuild the dashboard on my data: pass
--data-label with something that identifies my export, for example
`python make_dashboard.py --data-label "Our export, week of 4 Aug"`. The label defaults
to "Synthetic sample data" and prints on the page, so a run on my real data that skips
the label says the opposite of the truth.

## How I need you to work with me

- Explain things in plain English. Warehouse terms are fine; programming terms are not,
  unless you explain them the first time.
- Ask me no more than three or four questions at a time, and tell me why each one
  matters — what it changes about the answer. If a question does not change anything,
  do not ask it.
- When you give me a file, give me the complete file, not a fragment to splice in. Tell
  me its exact filename, which folder to save it in, and the exact command to run.
- Ask me early whether I am on Windows or a Mac, and give me commands that match. I
  will paste what you give me exactly as written, so it has to be right for my machine.
- If I give you an answer that does not make sense or contradicts something I said
  earlier, tell me. I would rather be corrected than get a confident wrong number.
- Do not require me to install anything beyond Python. If something would genuinely
  work much better with an extra package, say so and let me decide.
- If you are unsure about something, say you are unsure. Do not fill a gap with a
  plausible guess — a wrong assumption about my floor produces numbers that look fine
  and are wrong, which is worse than no numbers.

## What must not change

Do not rewrite the optimizer. The route-building logic in pick_path.py — the serpentine
sweep, the nearest-neighbour route, and the 2-opt improvement pass — stays as it is.

make_dashboard.py imports pick_path.py rather than repeating any of the math, so the map
and the numbers always come from the same model. Keep it that way. If you change how
travel works, change it once in pick_path.py and let the map follow. Never copy a
distance calculation into the dashboard — a map that disagrees with the numbers is worse
than no map, because it looks like evidence.

There is a third copy of the same logic: pickpath_engine.js, a JavaScript port that runs
inside dashboard.html so I can drag a CSV into my browser and see results with no Python
running at all. If you change the distance model or the route builder in pick_path.py,
make the identical change in pickpath_engine.js, then run `node test_pickpath_engine.js`
(if I have Node) to prove the two still agree. If I do not have Node, tell me plainly that
the browser upload box is unverified until I do, rather than silently leaving it stale —
a browser page that quietly computes different numbers from the CLI is the same failure
mode as a map that disagrees with the numbers, just harder for me to notice.

Instead, write a separate adapter script that reads my WMS export and produces the two
CSV files pick_path.py already expects (locations.csv and orders.csv, with the columns
described in README.md). Keep the adapter in its own file so I can re-run it each time
I pull a fresh export, and so the optimizer stays comparable to the original.

The one exception: the warehouse distance model may need to change, because my building
may not travel the way the starter assumes. Where that is needed, change it inside the
distance function and comment plainly what you changed and why, so anyone reading it
later can see which parts are mine and which came with the starter.

## Step 1 — Get a sample of my data

Ask me for a SMALL, ANONYMIZED sample export from my WMS. Not my full production data.

Tell me specifically:
- roughly how many rows you need (few hundred slots, a week of orders is plenty),
- which columns you actually need and which I can delete before sending,
- what to strip or fake out — customer names, addresses, anything commercially
  sensitive — and what you need left intact for the math to work,
- how to get it out of a WMS as a CSV, in general terms, since I may need to ask IT.

Wait for the sample before writing the adapter. Do not invent column names.

When I send it, tell me what you found: how many rows, which columns you recognised,
which you could not interpret, and anything that looks wrong — blank coordinates,
duplicate slot labels, orders whose lines point at slots that are not in the location
file. Ask me about those rather than quietly dropping them.

## Step 2 — Interview me about my actual layout

The starter assumes a simple grid: parallel aisles, cross-aisles at the front and back
only, trucks start and end at one staging point, and travel is along aisles or along
cross-aisles. My building probably breaks some of that. Work through this with me and
adjust the model to match what I tell you:

- **Coordinates.** Does my export already have x/y positions for each slot, or only
  aisle/bay/level labels? Almost always the latter — a WMS tracks labels, not physical
  positions.
  - **If my aisles are evenly spaced and numbered in physical left-to-right order**, I do
    not need you for this part: dashboard.html has a "Build floor plan" box that appears
    automatically once my locations file has location_id/aisle/bay/level but no x/y. I
    type the aisle spacing and bay pitch in feet, it derives the coordinates itself, and
    I can change the numbers and rebuild instantly if a pace-out check disagrees. Tell me
    this exists rather than doing it for me, if that is all my building needs — asking me
    to describe a uniform grid to you when I could just build it myself is a wasted turn.
  - **If I have an actual site plan, CAD export, or even a photo of a whiteboard
    sketch**, ask me to attach it and READ it yourself rather than only asking me to
    describe it in words — you can see a drawing directly. Pull out what you can: aisle
    count, rough spacing, orientation, where staging/docks sit, any zones or irregular
    areas. If the drawing has no scale or dimension marked, ask me for exactly ONE
    physical measurement to calibrate against — the width of one aisle, or the length of
    one wall — rather than guessing a scale from how the drawing looks.
  - **If neither of those fits** — uneven spacing, aisles not in numeric left-right
    order, a shape too irregular to describe as a grid — that is genuinely an interview,
    and this is where it happens: ask me the centre-to-centre distance between aisles,
    the length of a bay, and whatever else the drawing or my answers do not resolve.
- **Letter-coded aisles or levels — this one is not self-service, and never guess at
  it.** A real slot label like `LL13F` (two letters for the aisle, a number for how deep
  in it, one letter for the level) is fine as-is for `location_id` — that column takes
  any string. But if my export ALSO carries `LL` and `F` as separate aisle/level column
  values, dashboard.html's tools cannot read them: the aisle field needs a digit
  somewhere in it, and the level field needs to already be a number. Do not assume `LL`
  means the 12th aisle by treating letters as base-26 digits (A=1, B=2, ... AA=27) — that
  is a guess, it will look like it worked, and it will be silently wrong if my actual
  aisle order does not follow that pattern (odd/even split down each side of the
  building is common, and not alphabetical). Ask me for the real mapping — which letter
  code is physically where, and which level letter is which height — and write the
  adapter from what I tell you, not from an assumed alphabet order.
- **Aisle spacing.** Are all my aisles the same distance apart? Wide bulk aisles and
  narrow case-pick aisles change the cross-aisle math.
- **Cross-aisles.** Are there mid-building cross-overs, or only at the ends? One or more,
  at any position, is already self-service — I do not need you for this: repeated
  `--cross-aisle Y_FEET` on the command line (`pick_path.py` and `make_dashboard.py`
  both take it), or the "Mid-building cross-aisles" box in dashboard.html (Section 1's
  floor-plan builder, and Section 2's from-scratch warehouse builder), where I can add
  as many as I need and see each one drawn as a real gap in the racking before trusting
  it. What genuinely needs you: a cross-aisle that does not run the full width of the
  building (this tool assumes every declared one does), or anything past "one or more
  straight tunnels, full width."
- **One-way aisles.** Any aisles that can only be entered from one end, or travelled in
  one direction? Forklift traffic rules count here. One-way aisles break the assumption
  that distance is the same in both directions, and that changes how the route is built,
  not just what it costs.
- **Zones.** Is picking split into zones worked by different people, so one order gets
  split across operators rather than run end to end? If so, tell me the routing has to
  be done per zone and the totals mean something different — do not just optimize across
  the whole building and hand me a number that no single truck would ever drive.
- **Staging and docks.** One start point or several? Do trucks return to the same place
  they started? Do they drop off mid-order when the cart is full? A cart-capacity
  drop-off is a real constraint and it changes the route materially.
- **Equipment — assume stand-up high-reach forklifts unless I tell you otherwise.**
  Confirm what actually does the picking, then ask the questions that follow from it:
  - **How many pallets can one truck carry per trip?** This is the big one. If a
    truck carries two pallets and an order has fourteen pallet lines, that order is
    seven trips, not one route — and a single optimized loop through all fourteen
    stops is a number nobody will ever drive. Ask me directly, and if capacity binds,
    say plainly that the model must split an order into trips before the routing
    means anything.
  - **Can a truck turn around inside an aisle, or must it back out?** A reach truck
    in a narrow aisle often cannot turn. If it has to reverse to the end it came
    from, the distance model is wrong for any aisle it enters and leaves at the same
    end, and needs fixing.
  - **Wire- or rail-guided very narrow aisle?** If so, entry and exit are at the aisle
    ends only, which is what the starter already assumes — confirm it rather than
    assume it.
  - **Mixed fleet?** If order pickers, pallet jacks and reach trucks share the floor,
    ask which one this analysis is about. Do not average them.
  - Travel speed matters only if I want time rather than distance — ask whether I do.
- **Areas off the grid.** Bulk floor storage, mezzanines, cold rooms, hazmat cages —
  anywhere that is not neatly aisle-and-bay. Ask me how a truck gets there and how far
  it is, and handle it explicitly rather than pretending it sits on the grid.
- **Sequencing rules that beat distance.** Heavy items picked first so they sit at the
  bottom of the pallet, crushables last, cold chain last, hazmat segregation. These
  override the shortest path. Ask me which ones apply, and build them in as constraints
  the route must respect — a shorter route that arrives with crushed product is not an
  improvement.

For each thing I tell you, say what you changed in the model. If something I describe
cannot be represented properly without a rewrite you have been told not to do, say that
out loud and tell me what the resulting number will and will not capture.

## Step 3 — Keep everything on my machine

Everything runs locally. Do not suggest uploading my data to any online service, cloud
tool, mapping API, or hosted database. Do not add code that makes network calls of any
kind. Do not suggest I email files anywhere.

If we hit something that would normally be solved with an online service, find a local
way or tell me it cannot be done locally and let me decide.

## Step 4 — Validate it before I believe any of it

This step is not optional and it is the part I care most about. A pick-path tool that
produces confident wrong numbers is worse than no tool, because I will make staffing
decisions with it. Do not tell me it is working until we have done all five of these:

1. **Show me the map first.** Have me look at the floor map on my own data before we
   discuss a single number — the fastest way is dragging my adapted CSVs into the upload
   box on dashboard.html directly, no command needed, so I can do this myself and
   immediately again after any change you make. I know what my building looks like. If
   the aisles are the wrong way round, an area is missing, staging is in the wrong place,
   or a route line cuts through racking, I will see it in seconds — and any of those means
   the geometry is wrong and every number is wrong with it. This is the cheapest check we
   have, so it goes first. Ask me directly: does this look like your warehouse?

2. **Measure one real run.** Have me run one order and measure the route — a wheeled
   tape, the truck's own hour/distance readout if it has one, or a known distance like
   the length of one aisle rack line. Compare
   that against what the model says. If the model is off by more than about ten percent,
   the geometry is wrong. Fix it before going any further. Tell me exactly which run to
   measure and what to write down.

3. **Run a week of historical orders.** Not a handful. Use real issued orders with their
   real line sequences, so the baseline is what my operators actually drove.

4. **Compare the optimized sequence against what the operator actually did**, order by
   order, and look hard at the biggest disagreements. Sometimes the tool is right and
   there is real waste. Sometimes the operator is right and the model is missing a rule —
   a one-way aisle, a blocked bay, congestion at shift change, a heavy-item rule nobody
   wrote down. Bring me the ten biggest disagreements and let me tell you which is which.
   Every time the operator turns out to be right, that is a missing constraint: add it.

5. **Give me a range, not a single number.** Report the median improvement, the best and
   worst orders, and how many orders improved by essentially nothing. Then tell me
   plainly what the model still does not capture — pick time, congestion, cart capacity,
   put-away, anything we could not represent — so I know what the number does not
   include. If the honest answer is that the gain is small, tell me that.

Also tell me how to re-run the whole thing on a fresh export a month from now, in a
short set of steps I can follow without you.

## Start here

Read the two attached files. Then ask me your first round of questions — start with the
data sample and the handful of layout questions that matter most. Do not write any code
until you have seen my actual export.
````

---

## What "done" looks like

When the adaptation is finished you should have, in one folder on your own machine:

- the original `pick_path.py`, with only the distance model changed, and the changes
  commented,
- an adapter script that turns a fresh WMS export into the two input CSVs,
- your own `locations.csv` and `orders.csv` built from a real export,
- a results file covering a week of real orders,
- a short note of what was measured against a real run, and how close the model came,
- a written list of what the model still does not account for.

That last item is the one worth keeping. It is what stops the number being quoted in a
meeting as more than it is.
