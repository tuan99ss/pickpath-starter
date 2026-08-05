#!/usr/bin/env python3
"""
pick_path.py - pick-path optimization for a parallel-aisle warehouse.

WHAT IT DOES
    Reads a location master (where every slot sits on the floor) and a set of
    pick orders (which slots each order has to visit). For every order it
    compares two routes:

        BASELINE   the pick lines in the order the system handed them over,
                   which is the route the operator actually drives today.
        OPTIMIZED  the same stops, re-sequenced to cut travel distance.

    It prints a table of the results and writes results.csv.

    Nothing is estimated or assumed about the improvement. Both routes are
    measured with the same distance model and the percentage is arithmetic.

HOW THE WAREHOUSE IS MODELLED
    Parallel aisles running front-to-back, with racking between them. A truck
    cannot drive through racking, so travel between two different aisles has to
    go out to a cross-aisle at the front or the back of the building. Travel is
    rectilinear ("Manhattan") - along an aisle, or along a cross-aisle, never
    diagonally through a rack.

    Pick level does not change TRAVEL distance - the truck stands in the same
    spot on the floor whether the pallet is at floor level or six beams up. It is
    read and reported but not used in the distance model. Raising and lowering the
    mast is real time and this tool does not measure it.

REQUIREMENTS
    Python 3.10 or newer. Standard library only - nothing to install.

RUN IT
    python pick_path.py                      # uses the sample data in this folder
    python pick_path.py --detail             # also prints every optimized sequence
    python pick_path.py --locations my_locations.csv --orders my_orders.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

# The location_id in locations.csv that represents the start and end of every
# pick trip - the dock, the staging lane, wherever the operator sets off from and
# drops the completed order. If this id is not present in locations.csv the
# program falls back to the front-left corner of the rack block.
DEPOT_LOCATION_ID = "STAGING"

# Anything smaller than this is floating-point noise, not a real improvement.
EPSILON = 1e-9


# --------------------------------------------------------------------------
# Data containers
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Location:
    """One slot on the floor, straight out of locations.csv."""
    location_id: str
    aisle: int
    bay: int
    level: int
    x: float          # feet, across the building (which aisle you are in)
    y: float          # feet, front-to-back (how far down the aisle you are)


@dataclass(frozen=True)
class OrderLine:
    """One line of a pick order, straight out of orders.csv."""
    order_id: str
    line: int
    location_id: str
    sku: str
    qty: float


@dataclass
class OrderResult:
    """Everything we worked out about one order."""
    order_id: str
    line_count: int
    baseline_stops: int
    optimized_stops: int
    baseline_distance: float
    optimized_distance: float
    optimized_sequence: list[str]

    @property
    def saved(self) -> float:
        """Feet of travel removed."""
        return self.baseline_distance - self.optimized_distance

    @property
    def pct_improvement(self) -> float:
        """Feet saved as a percentage of the current route."""
        # An order with a single stop has a baseline of zero-ish; there is
        # nothing to improve and dividing by it would blow up.
        if self.baseline_distance <= EPSILON:
            return 0.0
        return 100.0 * self.saved / self.baseline_distance


# --------------------------------------------------------------------------
# Reading the input files
# --------------------------------------------------------------------------

def read_locations(path: Path) -> dict[str, Location]:
    """Load locations.csv into a lookup keyed by location_id.

    Expected columns: location_id, aisle, bay, level, x, y
    Blank aisle/bay/level values are tolerated and read as 0, because plenty of
    WMS exports leave them empty for staging and dock rows.
    """
    locations: dict[str, Location] = {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader.fieldnames, ["location_id", "aisle", "bay", "level", "x", "y"], path)
        for row_no, row in enumerate(reader, start=2):
            loc_id = (row["location_id"] or "").strip()
            if not loc_id:
                continue  # skip blank padding rows at the end of an export
            try:
                loc = Location(
                    location_id=loc_id,
                    aisle=int(float(row["aisle"] or 0)),
                    bay=int(float(row["bay"] or 0)),
                    level=int(float(row["level"] or 0)),
                    x=float(row["x"]),
                    y=float(row["y"]),
                )
            except ValueError as exc:
                raise SystemExit(f"{path} line {row_no}: could not read a number ({exc})")
            locations[loc_id] = loc

    if not locations:
        raise SystemExit(f"{path} contained no usable rows.")
    return locations


def read_orders(path: Path) -> dict[str, list[OrderLine]]:
    """Load orders.csv, grouped by order_id, with the lines in sequence.

    Expected columns: order_id, line, location_id, sku, qty

    The line order matters - it is the sequence the operator works today, and it
    is what the optimized route gets compared against. Lines are sorted by the
    `line` column so the result does not depend on how the file happens to be
    sorted on disk.
    """
    orders: dict[str, list[OrderLine]] = {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader.fieldnames, ["order_id", "line", "location_id", "sku", "qty"], path)
        for row_no, row in enumerate(reader, start=2):
            order_id = (row["order_id"] or "").strip()
            loc_id = (row["location_id"] or "").strip()
            if not order_id or not loc_id:
                continue
            try:
                line = OrderLine(
                    order_id=order_id,
                    line=int(float(row["line"] or 0)),
                    location_id=loc_id,
                    sku=(row["sku"] or "").strip(),
                    qty=float(row["qty"] or 0),
                )
            except ValueError as exc:
                raise SystemExit(f"{path} line {row_no}: could not read a number ({exc})")
            orders.setdefault(order_id, []).append(line)

    for order_id in orders:
        orders[order_id].sort(key=lambda item: item.line)

    if not orders:
        raise SystemExit(f"{path} contained no usable rows.")
    return orders


def _require_columns(found: list[str] | None, needed: list[str], path: Path) -> None:
    """Stop with a readable message if the file is missing a column we need."""
    have = {name.strip().lower() for name in (found or [])}
    missing = [name for name in needed if name not in have]
    if missing:
        raise SystemExit(
            f"{path} is missing required column(s): {', '.join(missing)}\n"
            f"Found: {', '.join(found or ['(no header row)'])}"
        )


# --------------------------------------------------------------------------
# The warehouse and its distance model
# --------------------------------------------------------------------------

class Warehouse:
    """Holds the floor layout and answers 'how far is it from A to B?'.

    The geometry is taken from the data, not hard-coded:
      - the front cross-aisle runs level with the frontmost bay in the file
      - the back cross-aisle runs level with the rearmost bay in the file
    """

    def __init__(self, locations: dict[str, Location]) -> None:
        self.locations = locations
        all_y = [loc.y for loc in locations.values()]
        self.front_y = min(all_y)
        self.back_y = max(all_y)

        # Where the operator starts and finishes. Prefer the named staging row;
        # otherwise fall back to the front-left corner of the rack block.
        if DEPOT_LOCATION_ID in locations:
            self.depot_id = DEPOT_LOCATION_ID
        else:
            corner = min(locations.values(), key=lambda loc: (loc.y, loc.x))
            self.depot_id = corner.location_id

    def pick_slot_count(self) -> int:
        """How many real pick slots there are (staging is not a pick slot)."""
        return sum(1 for loc_id in self.locations if loc_id != self.depot_id)

    def aisle_count(self) -> int:
        """How many distinct aisles hold pick slots."""
        return len({loc.aisle for loc_id, loc in self.locations.items() if loc_id != self.depot_id})

    def distance(self, from_id: str, to_id: str) -> float:
        """Travel feet between two locations, respecting the racking.

        Same aisle  -> drive straight along the aisle.
        Different aisle -> you cannot cut through the rack, so you travel out to
        a cross-aisle, along it, and back in. There is a cross-aisle at the
        front and one at the back; the operator takes whichever is shorter.
        """
        a = self.locations[from_id]
        b = self.locations[to_id]

        if a.aisle == b.aisle:
            return abs(a.y - b.y)

        across = abs(a.x - b.x)                       # travel along a cross-aisle
        out_front = (a.y - self.front_y) + (b.y - self.front_y)
        out_back = (self.back_y - a.y) + (self.back_y - b.y)
        return across + min(out_front, out_back)

    def route_distance(self, sequence: list[str]) -> float:
        """Total feet for a full trip, including out from and back to staging."""
        route = [self.depot_id] + sequence + [self.depot_id]
        return sum(self.distance(route[i], route[i + 1]) for i in range(len(route) - 1))


# --------------------------------------------------------------------------
# Route construction
# --------------------------------------------------------------------------

def serpentine_sequence(warehouse: Warehouse, stops: list[str]) -> list[str]:
    """Classic 'boustrophedon' route: down one aisle, back up the next.

    Aisles are visited left to right. The first aisle is run front-to-back,
    the next back-to-front, and so on - so the truck finishes each aisle at
    the end where the next aisle starts, instead of doubling back.
    """
    by_aisle: dict[int, list[str]] = {}
    for stop in stops:
        by_aisle.setdefault(warehouse.locations[stop].aisle, []).append(stop)

    # Order the aisles by their position across the building, not by number,
    # so an odd aisle-numbering scheme cannot scramble the route.
    aisle_order = sorted(
        by_aisle,
        key=lambda aisle: min(warehouse.locations[s].x for s in by_aisle[aisle]),
    )

    sequence: list[str] = []
    for index, aisle in enumerate(aisle_order):
        going_back = index % 2 == 0            # even aisles: front to back
        sequence.extend(
            sorted(by_aisle[aisle],
                   key=lambda s: warehouse.locations[s].y,
                   reverse=not going_back)
        )
    return sequence


def nearest_neighbour_sequence(warehouse: Warehouse, stops: list[str]) -> list[str]:
    """Greedy route: from where you are, always go to the closest stop left.

    Fast and usually decent, but it can strand a far-away stop until the end,
    which is exactly the kind of mistake the 2-opt pass below cleans up.
    """
    remaining = list(stops)
    sequence: list[str] = []
    current = warehouse.depot_id
    while remaining:
        nearest = min(remaining, key=lambda stop: warehouse.distance(current, stop))
        sequence.append(nearest)
        remaining.remove(nearest)
        current = nearest
    return sequence


def two_opt(warehouse: Warehouse, sequence: list[str]) -> list[str]:
    """Improvement pass: repeatedly un-cross a route that crosses itself.

    A 2-opt move takes a stretch of the route and reverses it. If the two edges
    at the ends of that stretch get shorter as a result, the whole route got
    shorter, so we keep the reversal. Repeat until no reversal helps any more.

    The route worked on here includes staging at both ends, and those two
    endpoints are never moved - the truck has to start and finish there.
    """
    route = [warehouse.depot_id] + list(sequence) + [warehouse.depot_id]
    dist = warehouse.distance

    improved = True
    while improved:
        improved = False
        for i in range(1, len(route) - 2):
            for j in range(i + 1, len(route) - 1):
                a, b = route[i - 1], route[i]      # edge entering the stretch
                c, d = route[j], route[j + 1]      # edge leaving the stretch
                before = dist(a, b) + dist(c, d)
                after = dist(a, c) + dist(b, d)
                if after < before - EPSILON:
                    route[i:j + 1] = reversed(route[i:j + 1])
                    improved = True

    return route[1:-1]


def optimize_order(warehouse: Warehouse, lines: list[OrderLine]) -> OrderResult:
    """Work out the current route and the best route we can find for one order.

    The baseline keeps every line exactly as the system issued it, duplicates
    included - if the pick list sends the truck back to a slot it already
    visited, that travel is real and it counts.

    The optimized route visits each distinct slot once. That does not flatter
    the numbers: two picks at the same slot are zero feet apart, so a route that
    visited it twice in a row would measure the same. It only means the stop
    count reflects trips, not lines.
    """
    baseline_sequence = [line.location_id for line in lines]
    baseline_distance = warehouse.route_distance(baseline_sequence)

    # Distinct stops, keeping first-seen order for stable, reproducible output.
    stops: list[str] = []
    for loc_id in baseline_sequence:
        if loc_id not in stops:
            stops.append(loc_id)

    # Build two starting routes and improve both, then keep whichever wins.
    # Serpentine is strong when the picks are spread over many aisles; nearest
    # neighbour is strong when they cluster. Trying both costs milliseconds.
    candidates = [
        two_opt(warehouse, serpentine_sequence(warehouse, stops)),
        two_opt(warehouse, nearest_neighbour_sequence(warehouse, stops)),
    ]
    best = min(candidates, key=warehouse.route_distance)

    return OrderResult(
        order_id=lines[0].order_id,
        line_count=len(lines),
        baseline_stops=len(baseline_sequence),
        optimized_stops=len(best),
        baseline_distance=baseline_distance,
        optimized_distance=warehouse.route_distance(best),
        optimized_sequence=best,
    )


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def print_report(warehouse: Warehouse, results: list[OrderResult], detail: bool) -> None:
    """Print the results table, a total line, and a short spread summary."""
    width = 84
    print("=" * width)
    print("PICK PATH OPTIMIZATION - RESULTS")
    print("=" * width)
    print(
        f"Layout read from file: {warehouse.aisle_count()} aisles, "
        f"{warehouse.pick_slot_count()} pick slots."
    )
    print(
        f"Every trip starts and ends at '{warehouse.depot_id}'. "
        f"Cross-aisles at y={warehouse.front_y:g} and y={warehouse.back_y:g}."
    )
    print("Distances are in the same units as the x/y columns (feet in the sample data).")
    print()

    header = f"{'ORDER':<12}{'LINES':>6}{'STOPS':>7}{'BASELINE':>12}{'OPTIMIZED':>12}{'SAVED':>10}{'IMPROVED':>11}"
    print(header)
    print("-" * width)
    for result in results:
        print(
            f"{result.order_id:<12}"
            f"{result.line_count:>6}"
            f"{result.optimized_stops:>7}"
            f"{result.baseline_distance:>12,.0f}"
            f"{result.optimized_distance:>12,.0f}"
            f"{result.saved:>10,.0f}"
            f"{result.pct_improvement:>10.1f}%"
        )

    total_lines = sum(r.line_count for r in results)
    total_stops = sum(r.optimized_stops for r in results)
    total_base = sum(r.baseline_distance for r in results)
    total_opt = sum(r.optimized_distance for r in results)
    total_saved = total_base - total_opt
    total_pct = 100.0 * total_saved / total_base if total_base > EPSILON else 0.0

    print("-" * width)
    print(
        f"{'TOTAL':<12}"
        f"{total_lines:>6}"
        f"{total_stops:>7}"
        f"{total_base:>12,.0f}"
        f"{total_opt:>12,.0f}"
        f"{total_saved:>10,.0f}"
        f"{total_pct:>10.1f}%"
    )
    print()

    pcts = [r.pct_improvement for r in results]
    print(
        f"Per-order improvement: best {max(pcts):.1f}%, "
        f"median {statistics.median(pcts):.1f}%, worst {min(pcts):.1f}%"
    )
    print(f"Total travel removed across {len(results)} orders: {total_saved:,.0f}")
    print()

    if detail:
        print("OPTIMIZED PICK SEQUENCES")
        print("-" * width)
        for result in results:
            print(f"{result.order_id}  ({result.optimized_stops} stops, "
                  f"{result.optimized_distance:,.0f} vs {result.baseline_distance:,.0f})")
            print("   " + " -> ".join(result.optimized_sequence))
            print()
    elif results:
        example = results[0]
        print(f"Example - optimized sequence for {example.order_id}:")
        print("   " + " -> ".join(example.optimized_sequence))
        print("   (run with --detail to print all of them; results.csv has every one)")
        print()


def write_results_csv(results: list[OrderResult], path: Path) -> None:
    """Write one row per order, with the optimized sequence as a pipe-joined list."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "order_id", "lines", "baseline_stops", "optimized_stops",
            "baseline_distance", "optimized_distance", "distance_saved",
            "pct_improvement", "optimized_sequence",
        ])
        for result in results:
            writer.writerow([
                result.order_id,
                result.line_count,
                result.baseline_stops,
                result.optimized_stops,
                f"{result.baseline_distance:.1f}",
                f"{result.optimized_distance:.1f}",
                f"{result.saved:.1f}",
                f"{result.pct_improvement:.1f}",
                "|".join(result.optimized_sequence),
            ])


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> None:
    """Read the command line, run every order, print and save the results."""
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Compare the current pick sequence against an optimized route.",
    )
    parser.add_argument("--locations", type=Path, default=here / "locations.csv",
                        help="location master CSV (default: locations.csv next to this script)")
    parser.add_argument("--orders", type=Path, default=here / "orders.csv",
                        help="pick order CSV (default: orders.csv next to this script)")
    parser.add_argument("--out", type=Path, default=here / "results.csv",
                        help="where to write the results CSV (default: results.csv)")
    parser.add_argument("--detail", action="store_true",
                        help="print the optimized sequence for every order")
    args = parser.parse_args()

    for path in (args.locations, args.orders):
        if not path.exists():
            raise SystemExit(f"File not found: {path}")

    locations = read_locations(args.locations)
    orders = read_orders(args.orders)
    warehouse = Warehouse(locations)

    # Catch a bad join early: a pick line pointing at a slot the location master
    # does not contain is a data problem, not something to silently skip.
    unknown = sorted({
        line.location_id
        for lines in orders.values()
        for line in lines
        if line.location_id not in locations
    })
    if unknown:
        shown = ", ".join(unknown[:10])
        more = f" (+{len(unknown) - 10} more)" if len(unknown) > 10 else ""
        raise SystemExit(
            f"{len(unknown)} location_id(s) in {args.orders} are not in {args.locations}: {shown}{more}"
        )

    results = [optimize_order(warehouse, lines) for _, lines in sorted(orders.items())]

    print_report(warehouse, results, detail=args.detail)
    write_results_csv(results, args.out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
