#!/usr/bin/env node
/*
test_pickpath_engine.js - proves the browser engine agrees with pick_path.py.

Run it:

    node test_pickpath_engine.js

Needs Node.js and nothing else - no npm install. Exits 0 if every check
passes, 1 if any fails.

WHY THIS FILE EXISTS
    dashboard.html can compute results from a CSV you pick off your own disk,
    entirely in the browser, so nothing has to be sent anywhere. That only
    works because pickpath_engine.js is a hand-written JavaScript port of
    pick_path.py's distance model and route builder.

    A port that quietly disagrees with the Python original is worse than no
    port at all - the map would look identical, the numbers would print
    identical decimal places, and it would still be wrong. This file checks
    that it is not: it runs the JS engine on the same sample data
    pick_path.py ships with, and diffs the result against results.csv,
    against a fresh run of the Python engine, and against dashboard.html's
    own generated markup - not against a hand-typed expectation, so it cannot
    go stale when the sample data changes.
*/

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const engine = require("./pickpath_engine.js");

const HERE = __dirname;
let pass = 0, fail = 0;

function check(label, cond, detail) {
  console.log((cond ? "PASS  " : "FAIL  ") + label + (detail ? "  " + detail : ""));
  cond ? pass++ : fail++;
}

// --------------------------------------------------------------------------
// 1. Run the engine on the sample data and diff against a fresh Python run.
// --------------------------------------------------------------------------

const locText = fs.readFileSync(path.join(HERE, "locations.csv"), "utf8");
const ordText = fs.readFileSync(path.join(HERE, "orders.csv"), "utf8");
const { warehouse: wh, orders, results } = engine.runPickPath(
  locText, "locations.csv", ordText, "orders.csv");

let pythonOk = true, pythonResults = null;
try {
  execFileSync("python", ["pick_path.py"], { cwd: HERE, stdio: "pipe" });
  pythonResults = fs.readFileSync(path.join(HERE, "results.csv"), "utf8")
    .replace(/\r\n/g, "\n").trim().split("\n").slice(1);
} catch (e) {
  pythonOk = false;
  console.log("SKIP  python comparison - could not run `python pick_path.py` (" + e.message.split("\n")[0] + ")");
}

if (pythonOk) {
  const jsRows = results.map(r => [r.order_id, r.line_count, r.baseline_stops, r.optimized_stops,
    r.baseline_distance.toFixed(1), r.optimized_distance.toFixed(1), r.saved.toFixed(1),
    r.pct_improvement.toFixed(1), r.optimized_sequence.join("|")].join(","));
  const mismatches = jsRows.filter((row, i) => row !== pythonResults[i]);
  check("JS results.csv matches a fresh Python run, all 20 orders",
    mismatches.length === 0 && jsRows.length === pythonResults.length,
    mismatches.length ? `${mismatches.length} row(s) differ` : "");
}

// --------------------------------------------------------------------------
// 2. Distance model - the same physical claims test_pick_path.py checks,
//    proven independently in the JS engine.
// --------------------------------------------------------------------------

check("same aisle costs the distance along it", wh.distance("A01-B01-L1", "A01-B09-L1") === 80.0);
check("different aisle routes via the nearer cross-aisle", wh.distance("A01-B01-L1", "A02-B06-L1") === 62.0);
check("takes the back cross-aisle when that is closer", wh.distance("A01-B10-L1", "A02-B10-L1") === 12.0);
check("racking is not passable (92ft, not the 12ft straight line)",
  wh.distance("A01-B05-L1", "A02-B05-L1") === 92.0);
check("two levels of one bay cost no travel", wh.distance("A03-B04-L1", "A03-B04-L2") === 0.0);

// --------------------------------------------------------------------------
// 3. Routing invariants.
// --------------------------------------------------------------------------

let worse = 0, wrongStops = 0;
for (const r of results) {
  if (r.optimized_distance > r.baseline_distance + 1e-9) worse++;
  const required = new Set(orders.get(r.order_id).map(l => l.location_id));
  const got = new Set(r.optimized_sequence);
  if (got.size !== required.size || ![...required].every(x => got.has(x))) wrongStops++;
  if (new Set(r.optimized_sequence).size !== r.optimized_sequence.length) wrongStops++;
}
check("optimized route is never worse than the one issued", worse === 0, `${worse} order(s) worse`);
check("optimized route visits every required slot exactly once", wrongStops === 0, `${wrongStops} order(s) wrong`);

// --------------------------------------------------------------------------
// 4. The map cannot flatter the numbers: drawn length == reported distance,
//    and no drawn segment cuts through racking.
// --------------------------------------------------------------------------

let diagonal = 0, throughRack = 0, lengthMismatch = 0;
const TOL = 1e-6;
for (const r of results) {
  const baseline = orders.get(r.order_id).map(l => l.location_id);
  for (const seq of [baseline, r.optimized_sequence]) {
    const pts = engine.routeWaypoints(wh, seq);
    let drawn = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      const [x1, y1] = pts[i], [x2, y2] = pts[i + 1];
      const dx = Math.abs(x2 - x1), dy = Math.abs(y2 - y1);
      drawn += dx + dy;
      if (dx > TOL && dy > TOL) diagonal++;
      if (dx > TOL && !(Math.abs(y1 - wh.frontY) < TOL || Math.abs(y1 - wh.backY) < TOL)) throughRack++;
    }
    if (Math.abs(drawn - wh.routeDistance(seq)) > TOL) lengthMismatch++;
  }
}
check("no diagonal segments on the map", diagonal === 0, `${diagonal} found`);
check("every sideways move on the map is on a cross-aisle", throughRack === 0, `${throughRack} found`);
check("drawn length equals reported distance, all 40 routes", lengthMismatch === 0, `${lengthMismatch} mismatch(es)`);

// --------------------------------------------------------------------------
// 5. Rendered markup matches what make_dashboard.py actually generated -
//    not a re-implementation of the check, a byte diff against the real file.
// --------------------------------------------------------------------------

const dashboardPath = path.join(HERE, "dashboard.html");
if (fs.existsSync(dashboardPath)) {
  const pyHtml = fs.readFileSync(dashboardPath, "utf8").replace(/\r\n/g, "\n");
  const plan = engine.buildFloorPlan(wh);

  function extractRouteGroup(html, orderId) {
    // The map's markup is the ONE place this marker appears as real HTML;
    // pickpath_engine.js's own source is embedded later in the page and
    // contains the same literal text inside a template literal, so "find the
    // next occurrence" has to be bounded by the real </svg> or it walks
    // straight through the closing tag into the embedded script.
    const startTag = `<g class="route" data-order="${orderId}">`;
    const start = html.indexOf(startTag);
    if (start === -1) return null;
    const svgClose = html.indexOf("</svg>", start);
    const nextGroup = html.indexOf('<g class="route" data-order="', start + startTag.length);
    const end = (nextGroup !== -1 && nextGroup < svgClose) ? nextGroup : svgClose;
    return html.slice(start, end).trimEnd();
  }

  let groupsOk = true, rowsOk = true;
  const pcts = results.map(r => r.pct_improvement);
  const bestPct = Math.max(...pcts);
  for (const r of results) {
    const baseline = orders.get(r.order_id).map(l => l.location_id);
    const jsGroup = engine.buildRouteGroup(plan, wh, r.order_id, baseline, r.optimized_sequence);
    const pyGroup = extractRouteGroup(pyHtml, r.order_id);
    if (pyGroup !== jsGroup) groupsOk = false;

    const jsRow = engine.buildTableRow(r, bestPct);
    const rowMatch = pyHtml.match(new RegExp(`<tr data-order="${r.order_id}"[\\s\\S]*?</tr>`));
    if (!rowMatch || rowMatch[0] !== jsRow) rowsOk = false;
  }
  check("JS-rendered route groups match dashboard.html, all 20 orders", groupsOk);
  check("JS-rendered table rows match dashboard.html, all 20 orders", rowsOk);
} else {
  console.log("SKIP  dashboard.html not found - run `python make_dashboard.py` first to check rendering too");
}

// --------------------------------------------------------------------------
// 6. Error paths - a bad upload must fail clearly, never silently.
// --------------------------------------------------------------------------

try {
  engine.readLocations("WHSE,LOCN_ID,AISLE,BAY,TIER,LOCN_TYPE,PICK_SEQ\n01,A01-01-A,A01,01,A,CASE,1001\n", "wms_slots.csv");
  check("a WMS export with no x/y throws a clear error", false, "did not throw");
} catch (e) {
  check("a WMS export with no x/y throws a clear error",
    e instanceof engine.ColumnError && e.missing.includes("x") && e.missing.includes("y"));
}

try {
  // "A01" is a label, not garbage - a real WMS export writes aisles this
  // way, so this must resolve to aisle 1, not throw. This is the same
  // tolerance readLocationsCoreOnly() uses for the "build my floor plan"
  // recovery path below, applied everywhere aisle/bay is read.
  const locs = engine.readLocations("location_id,aisle,bay,level,x,y\nA01-B01-L1,A01,1,1,0,0\n", "locations.csv");
  check("an alphanumeric aisle label like 'A01' resolves to aisle 1", locs.get("A01-B01-L1").aisle === 1);
} catch (e) {
  check("an alphanumeric aisle label like 'A01' resolves to aisle 1", false, e.message);
}

try {
  engine.readLocations("location_id,aisle,bay,level,x,y\nA01-B01-L1,AA,1,1,0,0\n", "locations.csv");
  check("an aisle label with no digits at all still throws a clear error", false, "did not throw");
} catch (e) {
  check("an aisle label with no digits at all still throws a clear error", e.message.includes("no number found"));
}

try {
  const locText2 = "location_id,aisle,bay,level,x,y\nSTAGING,0,0,0,0,0\nA01-B01-L1,1,1,1,0,0\n";
  const ordText2 = "order_id,line,location_id,sku,qty\nORD-1,1,A01-B01-L1,S,1\nORD-1,2,A99-B99-L1,S,1\n";
  engine.runPickPath(locText2, "locations.csv", ordText2, "orders.csv");
  check("a pick line pointing at an unknown slot throws a clear error", false, "did not throw");
} catch (e) {
  check("a pick line pointing at an unknown slot throws a clear error",
    e instanceof engine.JoinError && e.missing[0] === "A99-B99-L1");
}

try {
  const locs = engine.readLocations(
    "Location_ID,Aisle,Bay,Level,X,Y\nSTAGING,0,0,0,0,0\nA01-B01-L1,1,1,1,0,0\n", "locations.csv");
  check("mixed-case headers are read correctly", locs.size === 2);
} catch (e) {
  check("mixed-case headers are read correctly", false, e.message);
}

// --------------------------------------------------------------------------
// 7. deriveXY() - the "build my floor plan from two numbers" recovery path.
//    Proven against the sample data's own REAL coordinates, not a hand-typed
//    expectation: strip x/y from the sample, re-derive with the spacing the
//    sample was actually built on (12ft aisles, 10ft bays), and every one of
//    the 200 slots must land exactly where it started.
// --------------------------------------------------------------------------

{
  const realLocations = engine.readLocations(locText, "locations.csv");
  const coreRows = engine.readLocationsCoreOnly(locText, "locations.csv")
    .filter(r => r.location_id !== "STAGING");         // hand-placed at the dock, not on the grid
  const derived = engine.deriveXY(coreRows, 12, 10);
  let driftCount = 0;
  for (const d of derived) {
    const real = realLocations.get(d.location_id);
    if (Math.abs(real.x - d.x) > 1e-9 || Math.abs(real.y - d.y) > 1e-9) driftCount++;
  }
  check("deriveXY(spacing=12, pitch=10) exactly reproduces all 200 real sample coordinates",
    driftCount === 0 && derived.length === 200, `${driftCount} slot(s) drifted`);
}

// --------------------------------------------------------------------------
// 8. Mid-building cross-aisle - a wide tunnel that splits the racking, not
//    representable at all before this was added. Two claims, checked
//    computationally rather than assumed: it shortens a route for a pair
//    near the middle, and it never makes anything worse.
// --------------------------------------------------------------------------

{
  const whDefault = engine.makeWarehouse(wh.locations);
  const whMid = engine.makeWarehouse(wh.locations, [45]);

  check("cross-aisle list defaults to just [front, back]",
    JSON.stringify(whDefault.crossAisles) === "[0,90]", JSON.stringify(whDefault.crossAisles));
  check("adding a mid cross-aisle keeps front/back and inserts the new one",
    JSON.stringify(whMid.crossAisles) === "[0,45,90]", JSON.stringify(whMid.crossAisles));

  const nearMiddleDefault = whDefault.distance("A01-B05-L1", "A02-B05-L1");
  const nearMiddleMid = whMid.distance("A01-B05-L1", "A02-B05-L1");
  check("a mid cross-aisle shortens a route for a pair near the middle (92ft -> 22ft)",
    nearMiddleDefault === 92 && nearMiddleMid === 22, `${nearMiddleDefault} -> ${nearMiddleMid}`);

  const nearFrontDefault = whDefault.distance("A01-B01-L1", "A02-B01-L1");
  const nearFrontMid = whMid.distance("A01-B01-L1", "A02-B01-L1");
  check("a mid cross-aisle does not disturb a pair for which front is still shorter",
    nearFrontDefault === nearFrontMid, `${nearFrontDefault} vs ${nearFrontMid}`);

  const totalDefault = defaultResults => defaultResults.reduce((s, r) => s + r.optimized_distance, 0);
  const midResults = Array.from(orders.keys()).sort(engine.codePointCompare)
    .map(id => engine.optimizeOrder(whMid, orders.get(id)));
  check("adding a mid cross-aisle never increases total optimized distance",
    totalDefault(midResults) <= totalDefault(results));
}

// --------------------------------------------------------------------------
// 9. locations_no_xy.csv - the sample that exercises the browser's "Build
//    floor plan" recovery path with a real file, not a hand-built test
//    string. Same location_id/aisle/bay/level values as the real sample
//    (so it joins against the SAME orders.csv unchanged), alphanumeric
//    aisle labels ("A01") to exercise the tolerant parser too, and no x/y
//    or STAGING row - the shape of a real WMS export. Not a hardcoded
//    percentage (that would go stale the moment orders.csv changes) - just
//    "does it complete, and does the class of result stay plausible."
// --------------------------------------------------------------------------

{
  const noXyPath = path.join(HERE, "locations_no_xy.csv");
  if (fs.existsSync(noXyPath)) {
    const noXyText = fs.readFileSync(noXyPath, "utf8");
    let coreRows, sampleResults;
    try {
      coreRows = engine.readLocationsCoreOnly(noXyText, "locations_no_xy.csv");
      const derivedLocations = engine.locationsMapFrom(engine.deriveXY(coreRows, 12, 10));
      const wh2 = engine.makeWarehouse(derivedLocations);
      const orderIds = Array.from(orders.keys()).sort(engine.codePointCompare);
      sampleResults = orderIds.map(id => engine.optimizeOrder(wh2, orders.get(id)));
    } catch (e) {
      check("locations_no_xy.csv runs through the recovery path cleanly", false, e.message);
      sampleResults = null;
    }
    if (sampleResults) {
      check("locations_no_xy.csv has an alphanumeric aisle label ('A01' -> 1)",
        coreRows[0].aisle === 1, coreRows[0].aisle);
      const totalWas = sampleResults.reduce((s, r) => s + r.baseline_distance, 0);
      const totalNow = sampleResults.reduce((s, r) => s + r.optimized_distance, 0);
      const pct = 100 * (totalWas - totalNow) / totalWas;
      check("locations_no_xy.csv + orders.csv, recovered at 12ft/10ft, gives a plausible result",
        pct > 20 && pct < 60, `${pct.toFixed(1)}%`);
    }
  } else {
    console.log("SKIP  locations_no_xy.csv not found");
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
