/* pickpath_engine.js — a JavaScript mirror of pick_path.py + make_dashboard.py.

Why this exists: the "upload your own CSV" button has to compute results in the
browser, with no server and no network call — that is the whole point of the
privacy pitch. There is no way to shell out to the Python file from a static
HTML page, so the distance model and route builder are re-implemented here.

KEEP THIS IN SYNC WITH pick_path.py BY HAND. Every function below names the
Python function it mirrors in its comment. If you change the distance model,
the route builder, or the CSV-reading rules in pick_path.py, make the same
change here — a browser engine that quietly disagrees with the CLI is worse
than no browser engine at all.

Fidelity notes (JS vs Python, both use IEEE-754 doubles so arithmetic itself
does not drift — these are about parsing/formatting, not the maths):
  - int(float(x)) truncates toward zero -> Math.trunc(parseFloat(x)) here.
  - sorted(x) on strings compares by code point -> codePointCompare() here,
    never localeCompare, so no locale can reorder an order_id.
  - `row["field"] or 0` treats "" as falsy, any other string (even "0") as
    truthy -> orZero() here does the same.

VERIFIED, NOT ASSUMED: this file's computed results are checked byte-for-byte
against pick_path.py's output for the sample data (results.csv, every SVG
fragment, every table row) as part of this repo's test suite. Run
test_pickpath_engine.js (needs Node) to see the checks yourself.
*/

const EPSILON = 1e-9;
const DEPOT_LOCATION_ID = "STAGING";

// ---------------------------------------------------------------------------
// CSV parsing — a real RFC4180 reader, not split(','), so a quoted field
// containing a comma does not silently misparse. Mirrors csv.DictReader.
// ---------------------------------------------------------------------------

function parseCSV(text) {
  // Strip a UTF-8 BOM, same as Python's encoding="utf-8-sig".
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);

  const rows = [];
  let row = [], field = "", inQuotes = false;
  let i = 0;
  const n = text.length;

  function endField() { row.push(field); field = ""; }
  function endRow() { endField(); rows.push(row); row = []; }

  while (i < n) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
        inQuotes = false; i++; continue;
      }
      field += c; i++; continue;
    }
    if (c === '"') { inQuotes = true; i++; continue; }
    if (c === ',') { endField(); i++; continue; }
    if (c === '\r') { i++; continue; }              // normalise CRLF
    if (c === '\n') { endRow(); i++; continue; }
    field += c; i++;
  }
  // Last line may have no trailing newline.
  if (field.length > 0 || row.length > 0) endRow();

  // Drop fully-blank trailing rows (a trailing newline produces one).
  while (rows.length && rows[rows.length - 1].length === 1 && rows[rows.length - 1][0] === "") {
    rows.pop();
  }
  if (rows.length === 0) return { header: [], records: [] };

  const header = rows[0];
  const records = rows.slice(1).map(r => {
    const obj = {};
    header.forEach((h, idx) => { obj[h] = r[idx] !== undefined ? r[idx] : ""; });
    return obj;
  });
  return { header, records };
}

// ---------------------------------------------------------------------------
// Column matching — mirrors _require_columns() / _column_lookup() in
// pick_path.py: presence is checked case-insensitively, and lookups go
// through the same map so a header of "Location_ID" works correctly rather
// than silently failing later.
// ---------------------------------------------------------------------------

class ColumnError extends Error {
  constructor(missing, found) {
    super(`Missing required column(s): ${missing.join(", ")}`);
    this.missing = missing;
    this.found = found;
  }
}

function columnLookup(header, needed) {
  const have = new Map(header.map(h => [h.trim().toLowerCase(), h]));
  const missing = needed.filter(name => !have.has(name));
  if (missing.length) throw new ColumnError(missing, header);
  const field = {};
  needed.forEach(name => { field[name] = have.get(name); });
  return field;
}

function orZero(raw) {
  // Mirrors Python's `row["x"] or 0`: empty string is falsy, anything else
  // (including the literal string "0") passes through unchanged.
  return raw && raw.length ? raw : "0";
}

const NUMBER_RE = /^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$/;

function requireFloat(raw, context) {
  if (raw === undefined || !NUMBER_RE.test(raw)) {
    throw new Error(`${context}: could not read a number (got ${JSON.stringify(raw)})`);
  }
  return parseFloat(raw);
}

function truncInt(raw, context) {
  return Math.trunc(requireFloat(orZero(raw), context));
}

function codePointCompare(a, b) {
  // Python's default string comparison is by code point. localeCompare is
  // locale-sensitive and must never be used here - it can reorder order IDs.
  return a < b ? -1 : (a > b ? 1 : 0);
}

// ---------------------------------------------------------------------------
// Reading the input files — mirrors read_locations() / read_orders().
// ---------------------------------------------------------------------------

function readLocations(text, filename) {
  const { header, records } = parseCSV(text);
  const field = columnLookup(header, ["location_id", "aisle", "bay", "level", "x", "y"]);

  const locations = new Map();
  records.forEach((row, idx) => {
    const rowNo = idx + 2;
    const locId = (row[field.location_id] || "").trim();
    if (!locId) return;                              // blank padding row
    const ctx = `${filename} line ${rowNo}`;
    locations.set(locId, {
      location_id: locId,
      aisle: truncInt(row[field.aisle], ctx),
      bay: truncInt(row[field.bay], ctx),
      level: truncInt(row[field.level], ctx),
      x: requireFloat(row[field.x], ctx),
      y: requireFloat(row[field.y], ctx),
    });
  });

  if (locations.size === 0) throw new Error(`${filename} contained no usable rows.`);
  return locations;
}

function readOrders(text, filename) {
  const { header, records } = parseCSV(text);
  const field = columnLookup(header, ["order_id", "line", "location_id", "sku", "qty"]);

  const orders = new Map();
  records.forEach((row, idx) => {
    const rowNo = idx + 2;
    const orderId = (row[field.order_id] || "").trim();
    const locId = (row[field.location_id] || "").trim();
    if (!orderId || !locId) return;
    const ctx = `${filename} line ${rowNo}`;
    const line = {
      order_id: orderId,
      line: truncInt(row[field.line], ctx),
      location_id: locId,
      sku: (row[field.sku] || "").trim(),
      qty: requireFloat(orZero(row[field.qty]), ctx),
    };
    if (!orders.has(orderId)) orders.set(orderId, []);
    orders.get(orderId).push(line);
  });

  orders.forEach(lines => lines.sort((a, b) => a.line - b.line));
  if (orders.size === 0) throw new Error(`${filename} contained no usable rows.`);
  return orders;
}

// ---------------------------------------------------------------------------
// The warehouse and its distance model — mirrors class Warehouse.
// ---------------------------------------------------------------------------

function makeWarehouse(locations) {
  const ys = Array.from(locations.values(), l => l.y);
  const frontY = Math.min(...ys);
  const backY = Math.max(...ys);

  let depotId;
  if (locations.has(DEPOT_LOCATION_ID)) {
    depotId = DEPOT_LOCATION_ID;
  } else {
    let corner = null;
    for (const loc of locations.values()) {
      if (!corner || loc.y < corner.y || (loc.y === corner.y && loc.x < corner.x)) corner = loc;
    }
    depotId = corner.location_id;
  }

  function distance(fromId, toId) {
    const a = locations.get(fromId), b = locations.get(toId);
    if (a.aisle === b.aisle) return Math.abs(a.y - b.y);
    const across = Math.abs(a.x - b.x);
    const outFront = (a.y - frontY) + (b.y - frontY);
    const outBack = (backY - a.y) + (backY - b.y);
    return across + Math.min(outFront, outBack);
  }

  function routeDistance(sequence) {
    const route = [depotId, ...sequence, depotId];
    let total = 0;
    for (let i = 0; i < route.length - 1; i++) total += distance(route[i], route[i + 1]);
    return total;
  }

  function pickSlotCount() {
    let n = 0;
    for (const id of locations.keys()) if (id !== depotId) n++;
    return n;
  }

  function aisleCount() {
    const seen = new Set();
    for (const [id, loc] of locations) if (id !== depotId) seen.add(loc.aisle);
    return seen.size;
  }

  return { locations, frontY, backY, depotId, distance, routeDistance, pickSlotCount, aisleCount };
}

// ---------------------------------------------------------------------------
// Route construction — mirrors serpentine_sequence / nearest_neighbour_sequence / two_opt.
// ---------------------------------------------------------------------------

function serpentineSequence(wh, stops) {
  const byAisle = new Map();
  stops.forEach(stop => {
    const aisle = wh.locations.get(stop).aisle;
    if (!byAisle.has(aisle)) byAisle.set(aisle, []);
    byAisle.get(aisle).push(stop);
  });

  const aisleOrder = Array.from(byAisle.keys()).sort((a, b) => {
    const ax = Math.min(...byAisle.get(a).map(s => wh.locations.get(s).x));
    const bx = Math.min(...byAisle.get(b).map(s => wh.locations.get(s).x));
    return ax - bx;
  });

  const sequence = [];
  aisleOrder.forEach((aisle, index) => {
    const goingBack = index % 2 === 0;
    const sorted = byAisle.get(aisle).slice().sort((s1, s2) => {
      const d = wh.locations.get(s1).y - wh.locations.get(s2).y;
      return goingBack ? d : -d;
    });
    sequence.push(...sorted);
  });
  return sequence;
}

function nearestNeighbourSequence(wh, stops) {
  const remaining = stops.slice();
  const sequence = [];
  let current = wh.depotId;
  while (remaining.length) {
    let bestIdx = 0, bestDist = Infinity;
    for (let i = 0; i < remaining.length; i++) {
      const d = wh.distance(current, remaining[i]);
      if (d < bestDist) { bestDist = d; bestIdx = i; }
    }
    const nearest = remaining.splice(bestIdx, 1)[0];
    sequence.push(nearest);
    current = nearest;
  }
  return sequence;
}

function twoOpt(wh, sequence) {
  const route = [wh.depotId, ...sequence, wh.depotId];
  let improved = true;
  while (improved) {
    improved = false;
    for (let i = 1; i < route.length - 2; i++) {
      for (let j = i + 1; j < route.length - 1; j++) {
        const a = route[i - 1], b = route[i], c = route[j], d = route[j + 1];
        const before = wh.distance(a, b) + wh.distance(c, d);
        const after = wh.distance(a, c) + wh.distance(b, d);
        if (after < before - EPSILON) {
          const seg = route.slice(i, j + 1).reverse();
          route.splice(i, seg.length, ...seg);
          improved = true;
        }
      }
    }
  }
  return route.slice(1, -1);
}

function optimizeOrder(wh, lines) {
  const baselineSequence = lines.map(l => l.location_id);
  const baselineDistance = wh.routeDistance(baselineSequence);

  const stops = [];
  const seen = new Set();
  baselineSequence.forEach(id => { if (!seen.has(id)) { seen.add(id); stops.push(id); } });

  const candidates = [
    twoOpt(wh, serpentineSequence(wh, stops)),
    twoOpt(wh, nearestNeighbourSequence(wh, stops)),
  ];
  let best = candidates[0], bestDist = wh.routeDistance(candidates[0]);
  for (let i = 1; i < candidates.length; i++) {
    const d = wh.routeDistance(candidates[i]);
    if (d < bestDist) { best = candidates[i]; bestDist = d; }
  }

  const optimizedDistance = wh.routeDistance(best);
  const saved = baselineDistance - optimizedDistance;
  const pct = baselineDistance <= EPSILON ? 0.0 : (100.0 * saved / baselineDistance);

  return {
    order_id: lines[0].order_id,
    line_count: lines.length,
    baseline_stops: baselineSequence.length,
    optimized_stops: best.length,
    baseline_distance: baselineDistance,
    optimized_distance: optimizedDistance,
    optimized_sequence: best,
    saved, pct_improvement: pct,
  };
}

// ---------------------------------------------------------------------------
// Waypoints for drawing — mirrors travel_waypoints() / route_waypoints().
// ---------------------------------------------------------------------------

function travelWaypoints(wh, fromId, toId) {
  const a = wh.locations.get(fromId), b = wh.locations.get(toId);
  if (a.aisle === b.aisle) return [[a.x, a.y], [b.x, b.y]];
  const outFront = (a.y - wh.frontY) + (b.y - wh.frontY);
  const outBack = (wh.backY - a.y) + (wh.backY - b.y);
  const crossY = outFront <= outBack ? wh.frontY : wh.backY;
  return [[a.x, a.y], [a.x, crossY], [b.x, crossY], [b.x, b.y]];
}

function routeWaypoints(wh, sequence) {
  const full = [wh.depotId, ...sequence, wh.depotId];
  const points = [];
  for (let i = 0; i < full.length - 1; i++) {
    const leg = travelWaypoints(wh, full[i], full[i + 1]);
    points.push(...(i === 0 ? leg : leg.slice(1)));
  }
  return points;
}

// ---------------------------------------------------------------------------
// Full run — mirrors main()'s middle section: read, join-check, optimize all.
// ---------------------------------------------------------------------------

class JoinError extends Error {
  constructor(missing) {
    super(`${missing.length} location_id(s) in the orders file are not in the locations file`);
    this.missing = missing;
  }
}

function runPickPath(locationsText, locationsName, ordersText, ordersName) {
  const locations = readLocations(locationsText, locationsName);
  const orders = readOrders(ordersText, ordersName);
  const wh = makeWarehouse(locations);

  const missing = new Set();
  for (const lines of orders.values()) {
    for (const line of lines) if (!locations.has(line.location_id)) missing.add(line.location_id);
  }
  if (missing.size) {
    throw new JoinError(Array.from(missing).sort(codePointCompare));
  }

  const orderIds = Array.from(orders.keys()).sort(codePointCompare);
  const results = orderIds.map(id => optimizeOrder(wh, orders.get(id)));

  return { warehouse: wh, orders, results };
}

// ---------------------------------------------------------------------------
// Drawing — mirrors FloorPlan / rack_blocks / svg_floor / svg_route / svg_stops
// in make_dashboard.py. Pure string builders; same output shape as the
// Python-generated markup so the existing pick()/sortBy()/filt() JS (which
// was written against that markup) keeps working unmodified after a re-render.
// ---------------------------------------------------------------------------

const CORRIDOR_FRACTION = 0.22;
const MAP_WIDTH = 1080.0;
const MAP_MARGIN = 40.0;

function escapeHtml(s, quote) {
  // Mirrors Python's html.escape(s, quote=...).
  let out = String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  if (quote) out = out.replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
  return out;
}

function buildFloorPlan(wh) {
  const xs = Array.from(wh.locations.values(), l => l.x);
  const ys = Array.from(wh.locations.values(), l => l.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1.0);
  const spanY = Math.max(maxY - minY, 1.0);
  const scale = (MAP_WIDTH - 2 * MAP_MARGIN) / spanX;
  const height = spanY * scale + 2 * MAP_MARGIN;

  const plan = {
    warehouse: wh, minX, maxX, minY, maxY, scale, height, width: MAP_WIDTH,
    sx(x) { return MAP_MARGIN + (x - minX) * scale; },
    sy(y) { return height - MAP_MARGIN - (y - minY) * scale; },
    aisleColumns() {
      const seen = new Map();
      for (const [id, loc] of wh.locations) {
        if (id === wh.depotId) continue;
        if (!seen.has(loc.aisle)) seen.set(loc.aisle, loc.x);
      }
      return Array.from(seen.entries()).sort((a, b) => a[1] - b[1]);
    },
  };
  return plan;
}

function rackBlocks(plan) {
  const columns = plan.aisleColumns().map(([, x]) => x);
  if (columns.length < 2) return [];
  const gaps = [];
  for (let i = 0; i < columns.length - 1; i++) gaps.push(columns[i + 1] - columns[i]);
  const corridor = Math.min(...gaps) * CORRIDOR_FRACTION;
  const blocks = [];
  for (let i = 0; i < columns.length - 1; i++) {
    const left = columns[i] + corridor, right = columns[i + 1] - corridor;
    if (right > left) blocks.push([left, right]);
  }
  return blocks;
}

function svgFloor(plan) {
  const wh = plan.warehouse;
  const parts = [];
  const depth = plan.maxY - plan.minY;
  const inset = depth * 0.03;

  const band = Math.max(plan.scale * (depth * 0.045), 9.0);
  for (const [label, yAt] of [["FRONT CROSS-AISLE", plan.minY], ["BACK CROSS-AISLE", plan.maxY]]) {
    const cy = plan.sy(yAt);
    parts.push(`<rect class="crossaisle" x="${plan.sx(plan.minX).toFixed(1)}" y="${(cy - band / 2).toFixed(1)}" ` +
      `width="${((plan.maxX - plan.minX) * plan.scale).toFixed(1)}" height="${band.toFixed(1)}" rx="2"/>`);
    parts.push(`<text class="bandlabel" x="${plan.sx(plan.maxX).toFixed(1)}" ` +
      `y="${(cy - band / 2 - 5).toFixed(1)}" text-anchor="end">${label}</text>`);
  }

  const top = plan.sy(plan.maxY - inset), bottom = plan.sy(plan.minY + inset);
  for (const [left, right] of rackBlocks(plan)) {
    parts.push(`<rect class="rack" x="${plan.sx(left).toFixed(1)}" y="${top.toFixed(1)}" ` +
      `width="${((right - left) * plan.scale).toFixed(1)}" height="${(bottom - top).toFixed(1)}" rx="2"/>`);
  }

  for (const [aisle, x] of plan.aisleColumns()) {
    parts.push(`<text class="aislelabel" x="${plan.sx(x).toFixed(1)}" ` +
      `y="${(plan.height - MAP_MARGIN + 20).toFixed(1)}" text-anchor="middle">${aisle}</text>`);
  }

  const depot = wh.locations.get(wh.depotId);
  const dx = plan.sx(depot.x), dy = plan.sy(depot.y);
  parts.push(`<rect class="depot" x="${(dx - 9).toFixed(1)}" y="${(dy - 9).toFixed(1)}" width="18" height="18" rx="3"/>`);
  parts.push(`<text class="depotlabel" x="${dx.toFixed(1)}" y="${(dy + 26).toFixed(1)}" text-anchor="middle">` +
    `${escapeHtml(wh.depotId)}</text>`);
  return parts.join("\n");
}

function svgRoute(plan, points, cssClass) {
  const coords = points.map(([x, y]) => `${plan.sx(x).toFixed(1)},${plan.sy(y).toFixed(1)}`).join(" ");
  return `<polyline class="${cssClass}" points="${coords}"/>`;
}

function svgStops(plan, wh, sequence) {
  const parts = [];
  sequence.forEach((locId, i) => {
    const index = i + 1;
    const loc = wh.locations.get(locId);
    const cx = plan.sx(loc.x), cy = plan.sy(loc.y);
    parts.push(`<g class="stop"><title>${escapeHtml(locId)} - stop ${index}</title>` +
      `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="8.5"/>` +
      `<text x="${cx.toFixed(1)}" y="${(cy + 3.1).toFixed(1)}" text-anchor="middle">${index}</text></g>`);
  });
  return parts.join("");
}

function buildRouteGroup(plan, wh, orderId, baselineSeq, optSeq) {
  const wasPts = routeWaypoints(wh, baselineSeq);
  const nowPts = routeWaypoints(wh, optSeq);
  return `<g class="route" data-order="${escapeHtml(orderId)}">`
    + svgRoute(plan, wasPts, "was")
    + svgRoute(plan, nowPts, "now")
    + svgStops(plan, wh, optSeq)
    + "</g>";
}

function fmtInt(n) {
  // Mirrors Python's f"{n:,.0f}" - thousands separators, no decimals.
  return Math.round(n).toLocaleString("en-US");
}

function buildTableRow(result, bestPct) {
  const oid = escapeHtml(result.order_id);
  const sequence = result.optimized_sequence.join(" → ");
  const haystack = escapeHtml((result.order_id + " " + sequence).toLowerCase(), true);
  const barW = bestPct > 0 ? 100.0 * result.pct_improvement / bestPct : 0.0;
  return `<tr data-order="${oid}" data-search="${haystack}" onclick="pick('${oid}')">`
    + `<td class="num" data-v="${oid}">${oid}</td>`
    + `<td class="num r" data-v="${result.line_count}">${result.line_count}</td>`
    + `<td class="num r" data-v="${result.optimized_stops}">${result.optimized_stops}</td>`
    + `<td class="num r" data-v="${result.baseline_distance.toFixed(1)}">${fmtInt(result.baseline_distance)}</td>`
    + `<td class="num r" data-v="${result.optimized_distance.toFixed(1)}">${fmtInt(result.optimized_distance)}</td>`
    + `<td class="num r" data-v="${result.saved.toFixed(1)}">${fmtInt(result.saved)}</td>`
    + `<td class="r" data-v="${result.pct_improvement.toFixed(1)}">`
    + `<span class="minibar"><span style="width:${barW.toFixed(0)}%"></span></span>`
    + `<span class="pctcell">${result.pct_improvement.toFixed(1)}%</span></td>`
    + `<td class="seq" data-v="${oid}" title="${escapeHtml(sequence, true)}">${escapeHtml(sequence)}</td>`
    + `</tr>`;
}

if (typeof module !== "undefined") {
  module.exports = {
    parseCSV, ColumnError, JoinError, readLocations, readOrders, makeWarehouse,
    serpentineSequence, nearestNeighbourSequence, twoOpt, optimizeOrder,
    travelWaypoints, routeWaypoints, runPickPath, codePointCompare,
    buildFloorPlan, rackBlocks, svgFloor, svgRoute, svgStops, buildRouteGroup,
    buildTableRow, escapeHtml, fmtInt,
  };
}
