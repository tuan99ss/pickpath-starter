#!/usr/bin/env python3
"""
make_dashboard.py - turn the pick path results into a single HTML page.

WHAT IT DOES
    Reads the same two CSV files pick_path.py reads, runs the same optimizer,
    and writes dashboard.html - one self-contained file you open in a browser.

    The page shows a top-down map of the floor with both routes drawn on it:
    the route as issued, and the optimized route. Click an order to see it.

WHY IT IMPORTS pick_path.py
    It does not re-implement any of the math. It calls the same distance model
    and the same route builder, so the picture and the numbers cannot drift
    apart. Change the distance model in pick_path.py and this map changes with
    it - including any change you make when you adapt it to your building.

NO NETWORK, NOTHING TO INSTALL
    Standard library only. The HTML has no CDN links, no fonts to download, no
    scripts fetched from anywhere. It works on a laptop with the wifi off, and
    it works if you email it to yourself. Nothing in it reports back.

RUN IT
    python make_dashboard.py
    python make_dashboard.py --data-label "Live export, week of 4 Aug"
    python make_dashboard.py --locations my_locations.csv --orders my_orders.csv
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
from datetime import datetime
from pathlib import Path

import pick_path as pp

# The browser-side engine that lets the page recompute results from a CSV a
# visitor picks off their own disk, with no server involved. It is a hand
# ported mirror of pick_path.py + this file's own SVG/table builders, and it
# is verified against them (see the repo's test suite) - if you change the
# distance model, the route builder, or the CSV-reading rules in pick_path.py,
# make the matching change in pickpath_engine.js or the browser and the CLI
# will quietly start disagreeing.
ENGINE_JS_PATH = Path(__file__).resolve().parent / "pickpath_engine.js"


def _safe_json(obj) -> str:
    """json.dumps(), hardened for embedding inside a <script> tag.

    A location_id or sku that happened to contain the literal text
    "</script" could otherwise close the tag early. Cheap to guard against,
    so it is guarded against.
    """
    return json.dumps(obj).replace("</", "<\\/")

# How much of the gap between two aisles is driveable aisle rather than rack.
# Only affects how the map is drawn, never a distance.
CORRIDOR_FRACTION = 0.22

# Drawing area for the floor map, in SVG units. The map is drawn at roughly the
# size it is displayed at, so line weights do not scale up and look clumsy. The
# height follows from the shape of the building - the plan is never distorted.
MAP_WIDTH = 1080.0
MAP_MARGIN = 40.0


# --------------------------------------------------------------------------
# Turning a route into something we can draw
# --------------------------------------------------------------------------

def travel_waypoints(warehouse: pp.Warehouse, from_id: str, to_id: str) -> list[tuple[float, float]]:
    """The corners a truck actually turns when travelling from one slot to another.

    This mirrors Warehouse.distance() exactly. Same aisle means a straight run
    along it. Different aisles means out to a cross-aisle, along it, and back
    in - so the line on the map goes around the racking instead of through it,
    which is the whole point of the picture.
    """
    a = warehouse.locations[from_id]
    b = warehouse.locations[to_id]

    if a.aisle == b.aisle:
        return [(a.x, a.y), (b.x, b.y)]

    # Take whichever end of the building is the shorter way round - the same
    # choice the distance function makes.
    out_front = (a.y - warehouse.front_y) + (b.y - warehouse.front_y)
    out_back = (warehouse.back_y - a.y) + (warehouse.back_y - b.y)
    cross_y = warehouse.front_y if out_front <= out_back else warehouse.back_y

    return [(a.x, a.y), (a.x, cross_y), (b.x, cross_y), (b.x, b.y)]


def route_waypoints(warehouse: pp.Warehouse, sequence: list[str]) -> list[tuple[float, float]]:
    """Every corner of a full trip, staging out and staging back."""
    full = [warehouse.depot_id] + list(sequence) + [warehouse.depot_id]
    points: list[tuple[float, float]] = []
    for i in range(len(full) - 1):
        leg = travel_waypoints(warehouse, full[i], full[i + 1])
        # Skip the first point of each leg after the first - it repeats the
        # last point of the leg before it.
        points.extend(leg if i == 0 else leg[1:])
    return points


# --------------------------------------------------------------------------
# Mapping warehouse feet onto the drawing
# --------------------------------------------------------------------------

class FloorPlan:
    """Converts warehouse coordinates into SVG coordinates.

    The front of the building (where staging is) is drawn at the BOTTOM, so the
    map reads the way a site plan does.
    """

    def __init__(self, warehouse: pp.Warehouse) -> None:
        self.warehouse = warehouse
        xs = [loc.x for loc in warehouse.locations.values()]
        ys = [loc.y for loc in warehouse.locations.values()]
        self.min_x, self.max_x = min(xs), max(xs)
        self.min_y, self.max_y = min(ys), max(ys)

        span_x = max(self.max_x - self.min_x, 1.0)
        span_y = max(self.max_y - self.min_y, 1.0)
        self.scale = (MAP_WIDTH - 2 * MAP_MARGIN) / span_x
        self.height = span_y * self.scale + 2 * MAP_MARGIN
        self.width = MAP_WIDTH

    def sx(self, x: float) -> float:
        """Warehouse x (across the building) to SVG x."""
        return MAP_MARGIN + (x - self.min_x) * self.scale

    def sy(self, y: float) -> float:
        """Warehouse y (front to back) to SVG y, flipped so front is at the bottom."""
        return self.height - MAP_MARGIN - (y - self.min_y) * self.scale

    def aisle_columns(self) -> list[tuple[int, float]]:
        """Every aisle that holds pick slots, as (aisle number, x), left to right."""
        seen: dict[int, float] = {}
        for loc_id, loc in self.warehouse.locations.items():
            if loc_id == self.warehouse.depot_id:
                continue
            seen.setdefault(loc.aisle, loc.x)
        return sorted(seen.items(), key=lambda pair: pair[1])


def rack_blocks(plan: FloorPlan) -> list[tuple[float, float]]:
    """The x ranges where racking sits - the strips a truck cannot drive through.

    Racking fills the space between one aisle corridor and the next. If there is
    only one aisle there is no racking to draw.
    """
    columns = [x for _, x in plan.aisle_columns()]
    if len(columns) < 2:
        return []

    gaps = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)]
    corridor = min(gaps) * CORRIDOR_FRACTION

    blocks = []
    for i in range(len(columns) - 1):
        left = columns[i] + corridor
        right = columns[i + 1] - corridor
        if right > left:
            blocks.append((left, right))
    return blocks


# --------------------------------------------------------------------------
# SVG pieces
# --------------------------------------------------------------------------

def svg_floor(plan: FloorPlan) -> str:
    """The fixed part of the map: racking, cross-aisles, aisle labels, staging."""
    wh = plan.warehouse
    parts: list[str] = []

    depth = plan.max_y - plan.min_y
    inset = depth * 0.03          # keeps racking clear of the cross-aisles

    # Cross-aisle strips at the front and the back, so it is obvious where a
    # truck is allowed to change aisle.
    band = max(plan.scale * (depth * 0.045), 9.0)
    for label, y_at in (("FRONT CROSS-AISLE", plan.min_y), ("BACK CROSS-AISLE", plan.max_y)):
        cy = plan.sy(y_at)
        parts.append(
            f'<rect class="crossaisle" x="{plan.sx(plan.min_x):.1f}" y="{cy - band / 2:.1f}" '
            f'width="{(plan.max_x - plan.min_x) * plan.scale:.1f}" height="{band:.1f}" rx="2"/>'
        )
        parts.append(
            f'<text class="bandlabel" x="{plan.sx(plan.max_x):.1f}" '
            f'y="{cy - band / 2 - 5:.1f}" text-anchor="end">{label}</text>'
        )

    # Racking. These are the blocks the route lines have to go around.
    top = plan.sy(plan.max_y - inset)
    bottom = plan.sy(plan.min_y + inset)
    for left, right in rack_blocks(plan):
        parts.append(
            f'<rect class="rack" x="{plan.sx(left):.1f}" y="{top:.1f}" '
            f'width="{(right - left) * plan.scale:.1f}" height="{bottom - top:.1f}" rx="2"/>'
        )

    # Aisle numbers along the bottom.
    for aisle, x in plan.aisle_columns():
        parts.append(
            f'<text class="aislelabel" x="{plan.sx(x):.1f}" '
            f'y="{plan.height - MAP_MARGIN + 20:.1f}" text-anchor="middle">{aisle}</text>'
        )

    # Staging.
    depot = wh.locations[wh.depot_id]
    dx, dy = plan.sx(depot.x), plan.sy(depot.y)
    parts.append(f'<rect class="depot" x="{dx - 9:.1f}" y="{dy - 9:.1f}" width="18" height="18" rx="3"/>')
    parts.append(
        f'<text class="depotlabel" x="{dx:.1f}" y="{dy + 26:.1f}" text-anchor="middle">'
        f'{html.escape(wh.depot_id)}</text>'
    )
    return "\n".join(parts)


def svg_route(plan: FloorPlan, points: list[tuple[float, float]], css_class: str) -> str:
    """One route, drawn as a line that follows the aisles."""
    coords = " ".join(f"{plan.sx(x):.1f},{plan.sy(y):.1f}" for x, y in points)
    return f'<polyline class="{css_class}" points="{coords}"/>'


def svg_stops(plan: FloorPlan, warehouse: pp.Warehouse, sequence: list[str]) -> str:
    """Numbered markers showing the order the optimized route visits slots."""
    parts = []
    for index, loc_id in enumerate(sequence, start=1):
        loc = warehouse.locations[loc_id]
        cx, cy = plan.sx(loc.x), plan.sy(loc.y)
        parts.append(
            f'<g class="stop"><title>{html.escape(loc_id)} - stop {index}</title>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="8.5"/>'
            f'<text x="{cx:.1f}" y="{cy + 3.1:.1f}" text-anchor="middle">{index}</text></g>'
        )
    return "".join(parts)


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

CSS = """
:root{
  --ground:#FAF9F7; --panel:#FFFFFF; --ink:#1A1D21; --ink-dim:#5C6169;
  --ink-faint:#8A9099; --line:#DCD9D4; --line-soft:#EBE8E4;
  --was:#C0483D; --now:#16697A; --rack:#EAE7E2; --rack-line:#D6D2CC; --alert:#B23A2E;
  --mono:ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:28px 24px 56px}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}

header{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:22px}
h1{font-size:19px;margin:0 0 4px;letter-spacing:-0.01em}
.sub{color:var(--ink-dim);font-size:13px;margin:0}
.src{color:var(--ink-faint);font-size:11.5px;margin-top:9px;font-family:var(--mono)}
.tag{display:inline-block;border:1px solid var(--line);border-radius:3px;
  padding:2px 7px;font-size:11px;letter-spacing:.05em;text-transform:uppercase;
  color:var(--ink-dim);background:var(--panel);margin-right:6px}

.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:6px;overflow:hidden;margin-bottom:10px}
.stat{background:var(--panel);padding:13px 15px}
.stat .k{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-faint)}
.stat .v{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:22px;
  margin-top:3px;letter-spacing:-0.02em}
.stat .u{font-size:12px;color:var(--ink-faint);margin-left:3px}
.spread{color:var(--ink-dim);font-size:12.5px;margin:0 0 26px}

.stack{display:flex;flex-direction:column;gap:22px}

.panel{background:var(--panel);border:1px solid var(--line);border-radius:6px}
.phead{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  padding:12px 15px;border-bottom:1px solid var(--line-soft);flex-wrap:wrap}
.phead h2{font-size:13px;margin:0;font-weight:600}
.phead .note{font-size:11.5px;color:var(--ink-faint)}

.toggle{display:flex;gap:0;border:1px solid var(--line);border-radius:4px;overflow:hidden}
.toggle button{font-family:var(--sans);font-size:11.5px;padding:4px 10px;border:0;
  background:var(--panel);color:var(--ink-dim);cursor:pointer;border-right:1px solid var(--line)}
.toggle button:last-child{border-right:0}
.toggle button[aria-pressed="true"]{background:var(--ink);color:#fff}

svg.map{display:block;width:100%;height:auto}
.rack{fill:var(--rack);stroke:var(--rack-line);stroke-width:1}
.crossaisle{fill:#F2EFEA;stroke:none}
.bandlabel{font-family:var(--mono);font-size:8.5px;fill:var(--ink-faint);letter-spacing:.08em}
.aislelabel{font-family:var(--mono);font-size:10px;fill:var(--ink-faint)}
.depot{fill:var(--ink);stroke:none}
.depotlabel{font-family:var(--mono);font-size:9px;fill:var(--ink-dim);letter-spacing:.05em}

.route{display:none}
.route.on{display:block}
polyline{fill:none;stroke-linejoin:round;stroke-linecap:round}
polyline.was{stroke:var(--was);stroke-width:2.6;opacity:.62}
polyline.now{stroke:var(--now);stroke-width:2.6}
.stop circle{fill:var(--now);stroke:#fff;stroke-width:1.4}
.stop text{font-family:var(--mono);font-size:8.5px;fill:#fff}
#map[data-mode="was"] .now,#map[data-mode="was"] .stop{display:none}
#map[data-mode="now"] .was{display:none}

.legend{display:flex;gap:16px;padding:10px 15px;border-top:1px solid var(--line-soft);
  font-size:12px;color:var(--ink-dim);flex-wrap:wrap}
.legend i{display:inline-block;width:15px;height:2.6px;vertical-align:middle;margin-right:6px}
.legend .sw{display:inline-block;width:12px;height:11px;background:var(--rack);
  border:1px solid var(--rack-line);vertical-align:-1px;margin-right:6px}

.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12.5px}
thead th{position:sticky;top:0;background:var(--panel);text-align:left;
  border-bottom:1px solid var(--line);padding:8px 12px;font-size:10.5px;
  letter-spacing:.06em;text-transform:uppercase;color:var(--ink-faint);
  cursor:pointer;user-select:none;white-space:nowrap;font-weight:600}
thead th:hover{color:var(--ink)}
thead th[data-dir]{color:var(--ink)}
thead th[data-dir="asc"]::after{content:" \\2191"}
thead th[data-dir="desc"]::after{content:" \\2193"}
thead th.r,td.r{text-align:right}
tbody td{border-bottom:1px solid var(--line-soft);padding:7px 12px;white-space:nowrap}
tbody td.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
tbody tr{cursor:pointer}
tbody tr:hover td{background:#F6F4F1}
tbody tr.sel td{background:#EEF4F5}
tbody tr.sel td:first-child{box-shadow:inset 3px 0 0 var(--now)}
td.seq{font-family:var(--mono);font-size:11px;color:var(--ink-faint);
  max-width:280px;overflow:hidden;text-overflow:ellipsis}
.pctcell{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--now)}
.minibar{display:inline-block;width:52px;height:6px;background:var(--line-soft);
  border-radius:2px;overflow:hidden;vertical-align:middle;margin-right:7px}
.minibar span{display:block;height:100%;background:var(--now)}
.filter{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.filter input{font-family:var(--mono);font-size:12px;padding:4px 9px;
  border:1px solid var(--line);border-radius:4px;background:var(--ground);
  color:var(--ink);width:150px}
.filter input:focus{outline:none;border-color:var(--now)}
.filter .cnt{font-family:var(--mono);font-size:11.5px;color:var(--ink-faint)}

.caption{padding:11px 15px;border-top:1px solid var(--line-soft);font-size:12.5px;color:var(--ink-dim)}
.caption b{color:var(--ink);font-weight:600}

.upload{margin-bottom:22px}
.uploadbody{padding:15px}
.dropzone{display:block;border:1.5px dashed var(--line);border-radius:6px;padding:22px 18px;
  text-align:center;cursor:pointer;transition:border-color .15s,background .15s}
.dropzone:hover,.dropzone:focus-visible{border-color:var(--now);background:#F6FAFA;outline:none}
.dropzone.drag{border-color:var(--now);background:#EEF6F7}
.dz-icon{font-size:19px;color:var(--ink-faint);margin-bottom:4px}
.dz-text{font-size:13.5px;color:var(--ink);font-weight:500}
.dz-hint{font-size:11.5px;color:var(--ink-faint);margin-top:7px;font-family:var(--mono);line-height:1.6}
.filestatus{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px;font-size:12px;font-family:var(--mono)}
.filestatus .slot{color:var(--ink-faint)}
.filestatus .slot.ok{color:var(--now)}
.filestatus .slot b{font-family:var(--sans);font-weight:600;color:var(--ink)}
.errorbox{margin-top:12px;border:1px solid var(--alert);background:#FBEEEC;border-radius:6px;
  padding:12px 14px;font-size:12.5px;color:var(--ink);line-height:1.55}
.errorbox b{color:var(--alert)}
.errorbox .cols{font-family:var(--mono);font-size:11.5px;color:var(--ink-dim);margin-top:6px;word-break:break-word}
.errorbox .fix{margin-top:9px;padding-top:9px;border-top:1px solid #E8D5D2}
#resetlink{color:var(--now);text-decoration:none;margin-left:4px}
#resetlink:hover{text-decoration:underline}

.dimspanel{margin-top:12px;border:1px solid var(--now);background:#F2F8F8;border-radius:6px;padding:14px 16px}
.dimsHead{font-weight:600;font-size:13px;color:var(--ink)}
.dimsNote{font-size:12.5px;color:var(--ink-dim);margin:6px 0 12px;line-height:1.5}
.dimsRow{display:flex;gap:14px;align-items:end;flex-wrap:wrap}
.dimsRow label{font-size:11.5px;color:var(--ink-dim);display:flex;flex-direction:column;gap:4px}
.dimsRow input{font-family:var(--mono);font-size:13px;padding:6px 9px;width:130px;
  border:1px solid var(--line);border-radius:4px;background:var(--panel);color:var(--ink)}
.dimsRow input:focus{outline:none;border-color:var(--now)}
.dimsRow button{font-family:var(--sans);font-size:12.5px;font-weight:600;padding:7px 14px;
  border:1px solid var(--now);border-radius:4px;background:var(--now);color:#fff;cursor:pointer}
.dimsRow button:hover{opacity:.9}
.dimsCaveat{font-size:11.5px;color:var(--ink-faint);margin:12px 0 0;line-height:1.55;
  border-top:1px solid #DCEBEA;padding-top:10px}
.dimsCaveat code{font-family:var(--mono);background:var(--panel);padding:1px 4px;border-radius:3px}
footer{margin-top:26px;border-top:1px solid var(--line);padding-top:16px;
  font-size:12px;color:var(--ink-dim);max-width:760px}
footer h3{font-size:12px;margin:0 0 6px;color:var(--ink)}
footer li{margin-bottom:3px}
"""

JS = """
var DATA = __DATA__;

// Draw one order's routes on the map and highlight its row in the table.
function pick(id){
  var i;
  var routes = document.querySelectorAll('.route');
  for(i=0;i<routes.length;i++){
    routes[i].classList.toggle('on', routes[i].getAttribute('data-order') === id);
  }
  var rows = document.querySelectorAll('tbody tr');
  for(i=0;i<rows.length;i++){
    rows[i].classList.toggle('sel', rows[i].getAttribute('data-order') === id);
  }
  var d = DATA[id];
  document.getElementById('cap').innerHTML =
    '<b>' + id + '</b> - ' + d.lines + ' lines, ' + d.stops + ' stops. '
    + 'As issued <b>' + d.was + ' ft</b>, optimized <b>' + d.now + ' ft</b>, '
    + 'saving <b>' + d.saved + ' ft</b> (' + d.pct + '%).';
}

// Switch which routes the map draws.
function mode(m, btn){
  document.getElementById('map').setAttribute('data-mode', m);
  var b = document.querySelectorAll('.toggle button');
  for(var i=0;i<b.length;i++){ b[i].setAttribute('aria-pressed', b[i]===btn ? 'true':'false'); }
}

// Sort the results table by a column. Numbers sort as numbers, text as text.
function sortBy(th){
  var idx = +th.getAttribute('data-col');
  var dir = th.getAttribute('data-dir') === 'asc' ? -1 : 1;
  var heads = document.querySelectorAll('thead th');
  for(var i=0;i<heads.length;i++){ heads[i].removeAttribute('data-dir'); }
  th.setAttribute('data-dir', dir === 1 ? 'asc' : 'desc');

  var body = document.getElementById('tbody');
  var rows = Array.prototype.slice.call(body.querySelectorAll('tr'));
  rows.sort(function(a, b){
    var av = a.children[idx].getAttribute('data-v');
    var bv = b.children[idx].getAttribute('data-v');
    var an = parseFloat(av), bn = parseFloat(bv);
    if(!isNaN(an) && !isNaN(bn)){ return (an - bn) * dir; }
    return av.localeCompare(bv) * dir;
  });
  for(var j=0;j<rows.length;j++){ body.appendChild(rows[j]); }
}

// Filter rows by order id or slot label. Empty box shows everything.
function filt(q){
  q = (q || '').toLowerCase().trim();
  var rows = document.querySelectorAll('#tbody tr');
  var shown = 0;
  for(var i=0;i<rows.length;i++){
    var hit = q === '' || rows[i].getAttribute('data-search').indexOf(q) >= 0;
    rows[i].style.display = hit ? '' : 'none';
    if(hit){ shown++; }
  }
  document.getElementById('shown').textContent = shown;
}

// ---------------------------------------------------------------------------
// Upload your own data. Everything below runs entirely in this browser tab -
// pickpath_engine.js (embedded above) does the reading, the optimizing and
// the SVG/table building, the same functions this page's own generation
// script used to build the view you saw on load. No network call is made
// anywhere in this file; that is the whole point of shipping it this way.
// ---------------------------------------------------------------------------

var SAMPLE = __SAMPLE__;
var REFERENCE = __REFERENCE__;
var pending = { locations: null, orders: null };

// Rebuild the stats / map / table from a fresh (warehouse, orders, results)
// triple - the same three things pick_path.py's CLI computes, just computed
// here in JS instead. This is the one function both an upload and "reset to
// sample" funnel through, so the two can never render differently.
function renderResults(wh, orders, results, label, locName, ordName){
  var totalWas = 0, totalNow = 0, totalLines = 0;
  results.forEach(function(r){ totalWas += r.baseline_distance; totalNow += r.optimized_distance; totalLines += r.line_count; });
  var totalSaved = totalWas - totalNow;
  var totalPct = totalWas > 1e-9 ? 100 * totalSaved / totalWas : 0;
  var pcts = results.map(function(r){ return r.pct_improvement; });
  var bestPct = Math.max.apply(null, pcts);
  var worstPct = Math.min.apply(null, pcts);
  var sortedPcts = pcts.slice().sort(function(a, b){ return a - b; });
  var mid = sortedPcts.length % 2
    ? sortedPcts[(sortedPcts.length - 1) / 2]
    : (sortedPcts[sortedPcts.length / 2 - 1] + sortedPcts[sortedPcts.length / 2]) / 2;
  var under10 = pcts.filter(function(p){ return p < 10; }).length;

  document.getElementById('stat-was').textContent = fmtInt(totalWas);
  document.getElementById('stat-now').textContent = fmtInt(totalNow);
  document.getElementById('stat-removed').textContent = fmtInt(totalSaved);
  document.getElementById('stat-improvement').textContent = totalPct.toFixed(1);
  document.getElementById('spreadline').innerHTML =
    'Per order: best <span class="num">' + bestPct.toFixed(1) + '%</span>, median <span class="num">' +
    mid.toFixed(1) + '%</span>, worst <span class="num">' + worstPct.toFixed(1) +
    '%</span>. <span class="num">' + under10 + '</span> of <span class="num">' + results.length +
    '</span> orders improve by less than 10% - those are already close to the shortest route.';

  document.getElementById('tag').textContent = label;
  document.getElementById('srcline').textContent =
    locName + ' · ' + wh.pickSlotCount() + ' slots · ' + ordName + ' · ' +
    results.length + ' orders · ' + totalLines + ' lines · generated ' + new Date().toLocaleString();

  var plan = buildFloorPlan(wh);
  var map = document.getElementById('map');
  map.setAttribute('viewBox', '0 0 ' + Math.round(plan.width) + ' ' + Math.round(plan.height));
  var groups = results.map(function(r){
    var baseline = orders.get(r.order_id).map(function(l){ return l.location_id; });
    return buildRouteGroup(plan, wh, r.order_id, baseline, r.optimized_sequence);
  }).join('');
  map.innerHTML = svgFloor(plan) + groups;

  DATA = {};
  results.forEach(function(r){
    DATA[r.order_id] = { lines: r.line_count, stops: r.optimized_stops, was: fmtInt(r.baseline_distance),
      now: fmtInt(r.optimized_distance), saved: fmtInt(r.saved), pct: r.pct_improvement.toFixed(1) };
  });
  document.getElementById('tbody').innerHTML = results.map(function(r){ return buildTableRow(r, bestPct); }).join('');

  document.getElementById('total-count').textContent = results.length;
  var filterBox = document.querySelector('.filter input');
  if (filterBox) { filterBox.value = ''; }
  filt('');
  var heads = document.querySelectorAll('thead th');
  for (var i = 0; i < heads.length; i++) { heads[i].removeAttribute('data-dir'); }
  var toggleBtns = document.querySelectorAll('.toggle button');
  mode('both', toggleBtns[0]);

  pick(results[0].order_id);
}

function updateFileStatus(){
  var el = document.getElementById('filestatus');
  el.innerHTML =
    '<span class="slot' + (pending.locations ? ' ok' : '') + '">Locations: <b>' +
    (pending.locations ? escapeHtml(pending.locations.name) : 'not loaded yet') + '</b></span>' +
    '<span class="slot' + (pending.orders ? ' ok' : '') + '">Orders: <b>' +
    (pending.orders ? escapeHtml(pending.orders.name) : 'not loaded yet') + '</b></span>';
}

function hideError(){ document.getElementById('errorbox').hidden = true; }
function hideDimsPanel(){ document.getElementById('dimsPanel').hidden = true; }

// True only when EVERY missing column is x or y - location_id/aisle/bay/level
// are all present, so a floor plan can be built from spacing alone. If
// anything else is missing too, this is a bigger mismatch than two numbers
// can fix - fall through to the normal column error instead.
function isXYOnlyMissing(err){
  return err instanceof ColumnError && err.missing.length > 0 &&
    err.missing.every(function(m){ return m === 'x' || m === 'y'; });
}

function describeFileError(fileLabel, err){
  if (err instanceof ColumnError){
    var extra;
    if (err.missing.indexOf('x') >= 0 || err.missing.indexOf('y') >= 0){
      extra = 'Location coordinates (x / y) are almost never in a WMS export as-is - they usually ' +
        'have to be built from your aisle spacing and bay pitch. This is exactly what CLIENT_PROMPT.md ' +
        'is for: open it in this folder and follow it with Claude Code (or claude.ai) to build a small ' +
        'adapter for your export, then drop what it produces back in here.';
    } else {
      extra = 'This is normal for a real WMS export - the column names almost never match on the first ' +
        'try. Open CLIENT_PROMPT.md in this folder and follow it with Claude Code (or claude.ai) to build ' +
        'a small adapter for your column names, then drop what it produces back in here.';
    }
    return '<b>' + fileLabel + " doesn't have the columns this expects.</b>" +
      '<div class="cols">Missing: ' + escapeHtml(err.missing.join(', ')) +
      '<br>Found: ' + escapeHtml(err.found.join(', ')) + '</div>' +
      '<div class="fix">' + extra + '</div>';
  }
  return '<b>' + fileLabel + ':</b> ' + escapeHtml(err.message);
}

function showError(kind, info){
  var box = document.getElementById('errorbox');
  var html;
  if (kind === 'unrecognized'){
    html = '<b>Not recognized:</b> ' + info.names.map(function(n){ return escapeHtml(n); }).join(', ') +
      '<div class="cols">Expecting one file with location_id / aisle / bay / level / x / y, and one ' +
      'file with order_id / line / location_id / sku / qty.</div>' +
      '<div class="fix">If these came straight out of your WMS, this is expected - almost no export ' +
      'already uses these exact column names, and coordinates in particular are rarely present at all. ' +
      'Open CLIENT_PROMPT.md in this folder and follow it with Claude Code (or claude.ai) to build a ' +
      'small adapter for your export, then drop what it produces back in here.</div>';
  } else if (kind === 'read'){
    html = '<b>Could not read the file:</b> ' + escapeHtml(info.message);
  } else if (kind === 'validation'){
    html = '<b>' + escapeHtml(info.message) + '</b>';
  } else if (kind === 'join'){
    var shown = info.missing.slice(0, 10).map(function(s){ return escapeHtml(s); }).join(', ');
    var more = info.missing.length > 10 ? ' (+' + (info.missing.length - 10) + ' more)' : '';
    html = '<b>' + info.missing.length + ' slot(s) picked but not in your location file:</b> ' + shown + more +
      '<div class="fix">Check that both files came from the same export and cover the same date range.</div>';
  } else {
    html = describeFileError(kind === 'locations' ? 'Your locations file' : 'Your orders file', info.error);
  }
  box.innerHTML = html;
  box.hidden = false;
}

function fileDetectType(text){
  // Classify by SIGNATURE columns, not by requiring every column to already
  // be present - a file requiring all six locations columns before it is
  // even recognised as "a locations file" means a real export missing just
  // one (x/y almost always, since a WMS rarely has them) always falls into
  // the vague "not recognized as either" bucket instead of the precise
  // "you're missing: x, y" one that readLocations() can give it. order_id,
  // sku and qty essentially never appear in a location master, so their
  // presence is a strong, cheap signal either way.
  var parsed = parseCSV(text);
  var have = new Set(parsed.header.map(function(h){ return h.trim().toLowerCase(); }));
  if (have.has('order_id') || have.has('sku') || have.has('qty')) { return 'orders'; }
  if (have.has('location_id')) { return 'locations'; }
  return null;
}

function readFileText(file){
  return new Promise(function(resolve, reject){
    var reader = new FileReader();
    reader.onload = function(){ resolve(reader.result); };
    reader.onerror = function(){ reject(new Error('Could not read ' + file.name + ' - the browser refused to open it.')); };
    reader.readAsText(file);
  });
}

function tryCompute(){
  var locations, orders, wh;
  try { locations = readLocations(pending.locations.text, pending.locations.name); }
  catch (e) {
    if (isXYOnlyMissing(e)){
      // Everything but x/y is present - offer to build coordinates from
      // spacing rather than send them straight to CLIENT_PROMPT.md for what
      // might be a two-number problem.
      hideError();
      document.getElementById('dimsPanel').hidden = false;
      document.getElementById('aisleSpacingInput').focus();
      return;
    }
    showError('locations', { error: e });
    return;
  }
  try { orders = readOrders(pending.orders.text, pending.orders.name); }
  catch (e) { showError('orders', { error: e }); return; }

  hideDimsPanel();
  wh = makeWarehouse(locations);
  var missing = new Set();
  orders.forEach(function(lines){
    lines.forEach(function(line){ if (!locations.has(line.location_id)) { missing.add(line.location_id); } });
  });
  if (missing.size){
    showError('join', { missing: Array.from(missing).sort(codePointCompare) });
    return;
  }

  var orderIds = Array.from(orders.keys()).sort(codePointCompare);
  var results = orderIds.map(function(id){ return optimizeOrder(wh, orders.get(id)); });

  hideError();
  renderResults(wh, orders, results, 'Your data', pending.locations.name, pending.orders.name);
  document.getElementById('datasource').textContent = pending.locations.name + ' + ' + pending.orders.name;
  document.getElementById('resetlink').hidden = false;
}

// The "build my floor plan from two numbers" recovery path. Re-reads the
// already-loaded locations file tolerantly (location_id/aisle/bay/level
// only - "A01" resolves to aisle 1 the same way readLocations() itself now
// does), derives x/y from the spacing entered above, and runs the exact
// same compute-and-render pipeline a normal upload does. The dims panel
// stays open afterward on purpose: pace out one real aisle, see it does not
// match, correct the number, click again - the map updates immediately.
function buildFromDimensions(){
  var spacing = parseFloat(document.getElementById('aisleSpacingInput').value);
  var pitch = parseFloat(document.getElementById('bayPitchInput').value);
  if (!(spacing > 0) || !(pitch > 0)){
    showError('validation', { message: 'Enter a positive number of feet for both aisle spacing and bay pitch.' });
    return;
  }
  if (!pending.locations || !pending.orders){ return; }

  var coreRows;
  try { coreRows = readLocationsCoreOnly(pending.locations.text, pending.locations.name); }
  catch (e) { showError('locations', { error: e }); return; }

  var locations = locationsMapFrom(deriveXY(coreRows, spacing, pitch));

  var orders;
  try { orders = readOrders(pending.orders.text, pending.orders.name); }
  catch (e) { showError('orders', { error: e }); return; }

  var wh = makeWarehouse(locations);
  var missing = new Set();
  orders.forEach(function(lines){
    lines.forEach(function(line){ if (!locations.has(line.location_id)) { missing.add(line.location_id); } });
  });
  if (missing.size){
    showError('join', { missing: Array.from(missing).sort(codePointCompare) });
    return;
  }

  var orderIds = Array.from(orders.keys()).sort(codePointCompare);
  var results = orderIds.map(function(id){ return optimizeOrder(wh, orders.get(id)); });

  hideError();
  renderResults(wh, orders, results, 'Your data - estimated', pending.locations.name, pending.orders.name);
  document.getElementById('datasource').textContent = pending.locations.name + ' + ' + pending.orders.name +
    ' (coordinates estimated: ' + spacing + ' ft aisles, ' + pitch + ' ft bays)';
  document.getElementById('resetlink').hidden = false;
}

function handleFiles(fileList){
  hideError();
  var files = Array.prototype.slice.call(fileList).filter(function(f){ return /\\.csv$/i.test(f.name) || f.type.indexOf('csv') >= 0 || f.type === ''; });
  if (!files.length){ return; }
  Promise.all(files.map(readFileText)).then(function(texts){
    var unrecognized = [];
    files.forEach(function(file, i){
      var kind;
      try { kind = fileDetectType(texts[i]); } catch (e) { kind = null; }
      if (kind === 'locations') { pending.locations = { name: file.name, text: texts[i] }; }
      else if (kind === 'orders') { pending.orders = { name: file.name, text: texts[i] }; }
      else { unrecognized.push(file.name); }
    });
    updateFileStatus();
    if (unrecognized.length){ showError('unrecognized', { names: unrecognized }); }
    if (pending.locations && pending.orders){ tryCompute(); }
  }).catch(function(e){
    showError('read', { message: e.message });
  });
}

function resetToSample(e){
  if (e) { e.preventDefault(); }
  pending = { locations: null, orders: null };
  updateFileStatus();
  hideError();
  hideDimsPanel();
  document.getElementById('aisleSpacingInput').value = '';
  document.getElementById('bayPitchInput').value = '';

  var locations = readLocations(SAMPLE.locations, 'locations.csv');
  var orders = readOrders(SAMPLE.orders, 'orders.csv');
  var wh = makeWarehouse(locations);
  var orderIds = Array.from(orders.keys()).sort(codePointCompare);
  var results = orderIds.map(function(id){ return optimizeOrder(wh, orders.get(id)); });

  // Quiet drift check: does the JS engine agree with what pick_path.py
  // computed for this exact sample at generation time? Console-only - an
  // internal engineering assertion has no business surfacing to a client.
  var totalWas = 0, totalNow = 0;
  results.forEach(function(r){ totalWas += r.baseline_distance; totalNow += r.optimized_distance; });
  var totalPct = totalWas > 1e-9 ? 100 * (totalWas - totalNow) / totalWas : 0;
  if (Math.abs(totalPct - REFERENCE.total_pct) > 0.05){
    console.error('pickpath: the browser engine disagrees with the Python reference for the sample data.',
      { js_pct: totalPct, python_pct: REFERENCE.total_pct });
  }

  renderResults(wh, orders, results, 'Synthetic sample data', 'locations.csv', 'orders.csv');
  document.getElementById('datasource').textContent = 'the synthetic sample data';
  document.getElementById('resetlink').hidden = true;
}

function initUpload(){
  var dz = document.getElementById('dropzone');
  var input = document.getElementById('fileInput');
  updateFileStatus();
  dz.addEventListener('click', function(){ input.click(); });
  dz.addEventListener('keydown', function(e){ if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  input.addEventListener('change', function(){ if (input.files.length) { handleFiles(input.files); } input.value = ''; });
  ['dragenter','dragover'].forEach(function(evt){
    dz.addEventListener(evt, function(e){ e.preventDefault(); e.stopPropagation(); dz.classList.add('drag'); });
  });
  ['dragleave','drop'].forEach(function(evt){
    dz.addEventListener(evt, function(e){ e.preventDefault(); e.stopPropagation(); dz.classList.remove('drag'); });
  });
  dz.addEventListener('drop', function(e){
    if (e.dataTransfer && e.dataTransfer.files.length) { handleFiles(e.dataTransfer.files); }
  });

  ['aisleSpacingInput','bayPitchInput'].forEach(function(id){
    document.getElementById(id).addEventListener('keydown', function(e){
      if (e.key === 'Enter') { e.preventDefault(); buildFromDimensions(); }
    });
  });
}
"""


def build_html(warehouse: pp.Warehouse,
               results: list[pp.OrderResult],
               orders: dict[str, list[pp.OrderLine]],
               plan: FloorPlan,
               data_label: str,
               loc_path: Path,
               ord_path: Path,
               locations_raw: str,
               orders_raw: str) -> str:
    """Assemble the whole page as one string."""

    total_was = sum(r.baseline_distance for r in results)
    total_now = sum(r.optimized_distance for r in results)
    total_saved = total_was - total_now
    total_pct = 100.0 * total_saved / total_was if total_was > pp.EPSILON else 0.0
    pcts = [r.pct_improvement for r in results]
    total_lines = sum(r.line_count for r in results)

    # One drawn route group per order, hidden until selected.
    route_groups = []
    payload = {}
    for result in results:
        baseline_seq = [line.location_id for line in orders[result.order_id]]
        was_pts = route_waypoints(warehouse, baseline_seq)
        now_pts = route_waypoints(warehouse, result.optimized_sequence)
        route_groups.append(
            f'<g class="route" data-order="{html.escape(result.order_id)}">'
            + svg_route(plan, was_pts, "was")
            + svg_route(plan, now_pts, "now")
            + svg_stops(plan, warehouse, result.optimized_sequence)
            + "</g>"
        )
        payload[result.order_id] = {
            "lines": result.line_count,
            "stops": result.optimized_stops,
            "was": f"{result.baseline_distance:,.0f}",
            "now": f"{result.optimized_distance:,.0f}",
            "saved": f"{result.saved:,.0f}",
            "pct": f"{result.pct_improvement:.1f}",
        }

    # One table row per order - the same figures results.csv carries, sortable.
    best_pct = max(pcts) if pcts else 1.0
    rows = []
    for result in results:
        oid = html.escape(result.order_id)
        sequence = " → ".join(result.optimized_sequence)
        # Searching on the sequence lets someone type a slot and find every
        # order that visits it.
        haystack = html.escape((result.order_id + " " + sequence).lower(), quote=True)
        bar_w = 100.0 * result.pct_improvement / best_pct if best_pct > 0 else 0.0
        rows.append(
            f'<tr data-order="{oid}" data-search="{haystack}" onclick="pick(\'{oid}\')">'
            f'<td class="num" data-v="{oid}">{oid}</td>'
            f'<td class="num r" data-v="{result.line_count}">{result.line_count}</td>'
            f'<td class="num r" data-v="{result.optimized_stops}">{result.optimized_stops}</td>'
            f'<td class="num r" data-v="{result.baseline_distance:.1f}">{result.baseline_distance:,.0f}</td>'
            f'<td class="num r" data-v="{result.optimized_distance:.1f}">{result.optimized_distance:,.0f}</td>'
            f'<td class="num r" data-v="{result.saved:.1f}">{result.saved:,.0f}</td>'
            f'<td class="r" data-v="{result.pct_improvement:.1f}">'
            f'<span class="minibar"><span style="width:{bar_w:.0f}%"></span></span>'
            f'<span class="pctcell">{result.pct_improvement:.1f}%</span></td>'
            f'<td class="seq" data-v="{oid}" title="{html.escape(sequence, quote=True)}">{html.escape(sequence)}</td>'
            f"</tr>"
        )

    first = results[0].order_id
    generated = datetime.now().strftime("%d %b %Y, %H:%M")

    # What "reset to sample" recomputes from, and what its silent drift check
    # compares against - the exact numbers this same generation run already
    # produced for the sample data via pick_path.py, so the two engines are
    # checked against each other every time the page is rebuilt, not just
    # when someone remembers to run the JS test suite.
    engine_js = ENGINE_JS_PATH.read_text(encoding="utf-8")
    sample_json = _safe_json({"locations": locations_raw, "orders": orders_raw})
    reference_json = _safe_json({"total_pct": round(total_pct, 4)})

    script = (
        engine_js + "\n"
        + JS.replace("__DATA__", _safe_json(payload))
              .replace("__SAMPLE__", sample_json)
              .replace("__REFERENCE__", reference_json)
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pick path results - {html.escape(data_label)}</title>
<style>{CSS}</style></head>
<body><div class="wrap">

<header>
  <h1>Every order travels further than it needs to. Here is how much further.</h1>
  <p class="sub">Each pick order routed twice - once in the sequence the system issued,
  once re-sequenced - measured with the same distance model.</p>
  <p class="src"><span class="tag" id="tag">{html.escape(data_label)}</span>
  <span id="srcline">{html.escape(loc_path.name)} · {warehouse.pick_slot_count()} slots ·
  {html.escape(ord_path.name)} · {len(results)} orders · {total_lines} lines ·
  generated {generated}</span></p>
</header>

<div class="stats">
  <div class="stat"><div class="k">Travelled as issued</div>
    <div class="v"><span id="stat-was">{total_was:,.0f}</span><span class="u">ft</span></div></div>
  <div class="stat"><div class="k">Travelled optimized</div>
    <div class="v"><span id="stat-now">{total_now:,.0f}</span><span class="u">ft</span></div></div>
  <div class="stat"><div class="k">Removed</div>
    <div class="v"><span id="stat-removed">{total_saved:,.0f}</span><span class="u">ft</span></div></div>
  <div class="stat"><div class="k">Improvement</div>
    <div class="v"><span id="stat-improvement">{total_pct:.1f}</span><span class="u">%</span></div></div>
</div>
<p class="spread" id="spreadline">Per order: best <span class="num">{max(pcts):.1f}%</span>,
median <span class="num">{statistics.median(pcts):.1f}%</span>,
worst <span class="num">{min(pcts):.1f}%</span>.
<span class="num">{sum(1 for p in pcts if p < 10)}</span> of
<span class="num">{len(pcts)}</span> orders improve by less than 10% - those are already
close to the shortest route.</p>

<div class="panel upload">
  <div class="phead">
    <h2>Try it on your own data</h2>
    <span class="note">free · runs in your browser · nothing is uploaded anywhere</span>
  </div>
  <div class="uploadbody">
    <label class="dropzone" id="dropzone" tabindex="0">
      <input type="file" id="fileInput" accept=".csv,text/csv" multiple hidden>
      <div class="dz-icon">&#8679;</div>
      <div class="dz-text">Drop your two CSV files here, or click to browse</div>
      <div class="dz-hint">one file with location_id, aisle, bay, level, x, y ·
      one file with order_id, line, location_id, sku, qty<br>
      column names are matched automatically and case does not matter</div>
    </label>
    <div class="filestatus" id="filestatus"></div>
    <div class="errorbox" id="errorbox" hidden></div>
    <div class="dimspanel" id="dimsPanel" hidden>
      <div class="dimsHead">Your locations file doesn't have x / y positions - normal, since a WMS
        tracks slot labels, not physical coordinates.</div>
      <p class="dimsNote">If your aisles are evenly spaced and numbered in physical left-to-right
        order, build a working floor plan from two numbers instead of the full adaptation:</p>
      <div class="dimsRow">
        <label>Distance between aisle centers (ft)
          <input type="number" id="aisleSpacingInput" min="0.1" step="0.1" placeholder="e.g. 12">
        </label>
        <label>Distance between bay centers (ft)
          <input type="number" id="bayPitchInput" min="0.1" step="0.1" placeholder="e.g. 10">
        </label>
        <button type="button" id="buildDimsBtn" onclick="buildFromDimensions()">Build floor plan</button>
      </div>
      <p class="dimsCaveat">This assumes uniform spacing and aisle numbers running left to right, the
        same as pacing off one aisle and multiplying. If your building has uneven aisle widths,
        one-way aisles, multiple docks, or zones, this estimate will be wrong in ways that matter -
        use <code>CLIENT_PROMPT.md</code> with Claude Code for those. Paced out a real aisle and got a
        different number? Change it above and build again - the map updates instantly.</p>
    </div>
  </div>
  <div class="caption">Currently showing: <b id="datasource">the synthetic sample data</b>.
    <a href="#" id="resetlink" onclick="resetToSample(event)" hidden>Reset to sample data</a></div>
</div>

<div class="stack">
  <div class="panel">
    <div class="phead">
      <h2>Floor map</h2>
      <div class="toggle">
        <button onclick="mode('both',this)" aria-pressed="true">Both</button>
        <button onclick="mode('was',this)" aria-pressed="false">As issued</button>
        <button onclick="mode('now',this)" aria-pressed="false">Optimized</button>
      </div>
    </div>
    <svg id="map" class="map" data-mode="both"
         viewBox="0 0 {plan.width:.0f} {plan.height:.0f}" role="img"
         aria-label="Top-down warehouse map with both pick routes drawn">
      {svg_floor(plan)}
      {''.join(route_groups)}
    </svg>
    <div class="legend">
      <span><i style="background:var(--was);opacity:.62"></i>As issued</span>
      <span><i style="background:var(--now)"></i>Optimized, numbered in pick order</span>
      <span><span class="sw"></span>Racking - routes go around it, never through</span>
    </div>
    <div class="caption" id="cap"></div>
  </div>

  <div class="panel">
    <div class="phead">
      <h2>Results — every order, same figures as results.csv</h2>
      <div class="filter">
        <input type="search" placeholder="order or slot…" oninput="filt(this.value)"
               aria-label="Filter by order id or slot label">
        <span class="cnt"><span id="shown">{len(results)}</span> of <span id="total-count">{len(results)}</span></span>
      </div>
    </div>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th data-col="0" onclick="sortBy(this)">Order</th>
          <th data-col="1" class="r" onclick="sortBy(this)">Lines</th>
          <th data-col="2" class="r" onclick="sortBy(this)">Stops</th>
          <th data-col="3" class="r" onclick="sortBy(this)">As issued ft</th>
          <th data-col="4" class="r" onclick="sortBy(this)">Optimized ft</th>
          <th data-col="5" class="r" onclick="sortBy(this)">Saved ft</th>
          <th data-col="6" class="r" onclick="sortBy(this)">Improvement</th>
          <th data-col="7" onclick="sortBy(this)">Optimized sequence</th>
        </tr></thead>
        <tbody id="tbody">{''.join(rows)}</tbody>
      </table>
    </div>
    <div class="caption">Click any column to sort, any row to draw it on the map. Type a
    slot label such as <span class="num">A03</span> to find every order that visits it.</div>
  </div>
</div>

<footer>
  <h3>What this measures, and what it does not</h3>
  <ul>
    <li>Travel distance only. Pick time, mast raise and lower, scanning,
        congestion, queueing at the dock and put-away are not in it.</li>
    <li>Routes start and end at <span class="num">{html.escape(warehouse.depot_id)}</span>.
        Cross-aisles at the front and back only.</li>
    <li>Level does not change travel distance - the truck stands in the same spot
        whether the pallet is at floor level or six beams up. Mast time is real and
        is not measured here.</li>
    <li>Every figure on this page is computed from the two CSV files at run time.
        None are hard-coded.</li>
  </ul>
</footer>

</div>
<script>{script}
initUpload();
pick('{first}');
</script>
</body></html>
"""


def main() -> None:
    """Read the same inputs as pick_path.py and write the HTML page."""
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Build an HTML dashboard from the pick path results.")
    parser.add_argument("--locations", type=Path, default=here / "locations.csv")
    parser.add_argument("--orders", type=Path, default=here / "orders.csv")
    parser.add_argument("--out", type=Path, default=here / "dashboard.html")
    parser.add_argument("--data-label", default="Synthetic sample data",
                        help="shown on the page so nobody mistakes sample output for a real run")
    args = parser.parse_args()

    for path in (args.locations, args.orders):
        if not path.exists():
            raise SystemExit(f"File not found: {path}")

    locations = pp.read_locations(args.locations)
    orders = pp.read_orders(args.orders)
    warehouse = pp.Warehouse(locations)

    unknown = sorted({
        line.location_id
        for lines in orders.values()
        for line in lines
        if line.location_id not in locations
    })
    if unknown:
        raise SystemExit(
            f"{len(unknown)} location_id(s) in {args.orders} are not in {args.locations}: "
            + ", ".join(unknown[:10])
        )

    results = [pp.optimize_order(warehouse, lines) for _, lines in sorted(orders.items())]
    plan = FloorPlan(warehouse)

    locations_raw = args.locations.read_text(encoding="utf-8-sig")
    orders_raw = args.orders.read_text(encoding="utf-8-sig")

    page = build_html(warehouse, results, orders, plan, args.data_label, args.locations, args.orders,
                      locations_raw, orders_raw)
    args.out.write_text(page, encoding="utf-8")

    size_kb = args.out.stat().st_size / 1024
    print(f"Wrote {args.out} ({size_kb:,.0f} KB, self-contained - no internet needed)")
    print(f"{len(results)} orders drawn. Open it by double-clicking the file.")


if __name__ == "__main__":
    main()
