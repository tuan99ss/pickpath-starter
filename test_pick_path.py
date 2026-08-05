#!/usr/bin/env python3
"""
test_pick_path.py - proves the distance model instead of asking you to trust it.

Run it:

    python test_pick_path.py

Standard library only, like everything else here. No pytest, nothing to install.
It exits 0 if every check passes and 1 if any fails, so it also works in CI.

WHY THIS FILE EXISTS
    The whole tool rests on one claim: that the distances it reports are the
    distances a truck would actually travel. If the model let a route cut
    through racking, every number would be optimistic and nothing downstream
    would notice.

    So the checks below are mostly not about code style. They are about physics:
    routes go around racking, the line drawn on the map is exactly as long as
    the number in the table, and the optimizer never returns something worse
    than what it was given.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import make_dashboard as md
import pick_path as pp

TOL = 1e-6

_failures: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    """Record one result and print it as it happens."""
    print(f"{'PASS' if passed else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    if not passed:
        _failures.append(label)


def main() -> int:
    locations = pp.read_locations(HERE / "locations.csv")
    orders = pp.read_orders(HERE / "orders.csv")
    wh = pp.Warehouse(locations)

    # ---------------------------------------------------------------- geometry
    # Hand-computed against the sample layout: aisles 12 ft apart, bays 10 ft
    # apart, aisle 1 at x=0, bay 1 at y=0.

    d = wh.distance("A01-B01-L1", "A01-B09-L1")
    check("same aisle costs the distance along it", d == 80.0, f"got {d}, expected 80")

    # (0,0) to (12,50): out to the front cross-aisle (0), across 12, in 50.
    d = wh.distance("A01-B01-L1", "A02-B06-L1")
    check("different aisle routes via the nearer cross-aisle", d == 62.0,
          f"got {d}, expected 62")

    # Both slots at the back of the building - crossing at the back is shorter.
    d = wh.distance("A01-B10-L1", "A02-B10-L1")
    check("takes the back cross-aisle when that is closer", d == 12.0,
          f"got {d}, expected 12")

    # THE IMPORTANT ONE. These two slots are 12 ft apart in a straight line but
    # sit deep in adjacent aisles with racking between them. A model that let a
    # truck cut through would say 12.
    d = wh.distance("A01-B05-L1", "A02-B05-L1")
    check("racking is not passable", d == 92.0,
          f"got {d}; a straight line would be 12")

    d = wh.distance("A03-B04-L1", "A03-B04-L2")
    check("two levels of one bay cost no travel", d == 0.0, f"got {d}")

    pairs = [("A01-B02-L1", "A07-B09-L2"), ("STAGING", "A05-B05-L1"),
             ("A02-B10-L2", "A09-B01-L1")]
    check("distance is the same in both directions",
          all(wh.distance(a, b) == wh.distance(b, a) for a, b in pairs))

    # ---------------------------------------------------------------- routing
    worse = wrong_stops = not_converged = 0
    for _, lines in sorted(orders.items()):
        result = pp.optimize_order(wh, lines)

        if result.optimized_distance > result.baseline_distance + TOL:
            worse += 1

        required = {line.location_id for line in lines}
        if set(result.optimized_sequence) != required:
            wrong_stops += 1
        if len(set(result.optimized_sequence)) != len(result.optimized_sequence):
            wrong_stops += 1

        # Running the improvement pass again must find nothing left to improve.
        again = pp.two_opt(wh, result.optimized_sequence)
        if wh.route_distance(again) < result.optimized_distance - TOL:
            not_converged += 1

    check("optimized route is never worse than the one issued", worse == 0,
          f"{worse} order(s) worse")
    check("optimized route visits every required slot exactly once", wrong_stops == 0,
          f"{wrong_stops} order(s) wrong")
    check("2-opt finished - no improving move left", not_converged == 0,
          f"{not_converged} order(s) still improvable")

    first = [pp.optimize_order(wh, l).optimized_sequence for _, l in sorted(orders.items())]
    second = [pp.optimize_order(wh, l).optimized_sequence for _, l in sorted(orders.items())]
    check("same input gives the same answer every run", first == second)

    # ------------------------------------------------- the map matches the math
    # The dashboard draws a line per route. If that line were shorter than the
    # reported distance, the picture would be flattering the number.
    diagonal = through_rack = 0
    length_mismatch = 0
    for _, lines in sorted(orders.items()):
        result = pp.optimize_order(wh, lines)
        for sequence in ([line.location_id for line in lines], result.optimized_sequence):
            points = md.route_waypoints(wh, sequence)

            drawn = 0.0
            for i in range(len(points) - 1):
                (x1, y1), (x2, y2) = points[i], points[i + 1]
                dx, dy = abs(x2 - x1), abs(y2 - y1)
                drawn += dx + dy

                if dx > TOL and dy > TOL:
                    diagonal += 1          # a diagonal cuts a rack corner
                if dx > TOL and not (abs(y1 - wh.front_y) < TOL or abs(y1 - wh.back_y) < TOL):
                    through_rack += 1      # sideways anywhere but a cross-aisle

            if abs(drawn - wh.route_distance(sequence)) > TOL:
                length_mismatch += 1

    check("no diagonal moves on the map", diagonal == 0, f"{diagonal} found")
    check("every sideways move on the map is on a cross-aisle", through_rack == 0,
          f"{through_rack} segment(s) cross racking")
    check("the drawn line is exactly as long as the reported distance",
          length_mismatch == 0, f"{length_mismatch} mismatch(es)")

    # ----------------------------------------------------------------- results
    print()
    if _failures:
        print(f"{len(_failures)} CHECK(S) FAILED:")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
