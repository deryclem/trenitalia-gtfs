#!/usr/bin/env python3
"""
Builds shapes.txt and a trip_id -> shape_id mapping from the NeTEx data,
reading directly from netex.mdbx (badger's step 1 output, where
ServiceJourneyPattern and ServiceLink are still intact).

badger's own GTFS export (conv/gtfs_db_to_gtfs.py) never writes shapes.txt:
it has a conversion function for it (transformers/gtfsprofile.py,
projectServiceLinksToShapes) but nothing in the pipeline calls it, and the
ServiceJourneyPattern/ServiceLink data it needs doesn't survive badger's own
step 2 (gtfs_db_to_db.py flattens ServiceJourneyPattern into inline "calls"
and drops the pattern reference). Building shapes here, straight from
netex.mdbx, avoids depending on badger's internals for this.

Must be run with badger's own venv Python, from within vendor/badger (its
imports are relative to that directory).

Usage:
    cd vendor/badger && .venv/bin/python3 /path/to/generate_shapes.py \
        /path/to/netex.mdbx /path/to/shapes.txt /path/to/trip_shape_map.csv
"""

import csv
import sys
from pathlib import Path

# badger's imports are relative to its own directory (vendor/badger), which
# isn't on sys.path when this script is invoked by absolute path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "badger"))

from storage.mdbx.core.implementation import MdbxStorage
from domain.netex.model import ServiceJourney, ServiceJourneyPattern, ServiceLink, StopPointInJourneyPattern


def get_points_in_sequence(sjp: ServiceJourneyPattern) -> list:
    return sjp.points_in_sequence.point_in_journey_pattern_or_stop_point_in_journey_pattern_or_timing_point_in_journey_pattern


def build_shape_points(db, txn, sjp: ServiceJourneyPattern, service_link_cache: dict) -> list[tuple[float, float]]:
    """
    Walks a ServiceJourneyPattern's stop sequence in order, resolving each
    StopPointInJourneyPattern's onward_service_link_ref to the ServiceLink
    connecting it to the next stop, and concatenates their line strings.
    The last stop has no onward link (there's nothing after it).
    """
    points: list[tuple[float, float]] = []

    for pis in get_points_in_sequence(sjp):
        if not isinstance(pis, StopPointInJourneyPattern):
            continue
        if pis.onward_service_link_ref is None:
            continue

        link_ref = pis.onward_service_link_ref.ref
        service_link = service_link_cache.get(link_ref)
        if service_link is None:
            service_link = db.load_object_by_reference(txn, pis.onward_service_link_ref)
            service_link_cache[link_ref] = service_link
        if service_link is None or service_link.line_string is None:
            continue

        coords = service_link.line_string.pos_or_point_property_or_pos_list[0].value
        dimensions = service_link.line_string.srs_dimension or 2
        for i in range(0, len(coords), dimensions):
            lat, lon = coords[i], coords[i + 1]
            if not points or points[-1] != (lat, lon):
                points.append((lat, lon))

    return points


def main(netex_mdbx: str, shapes_out: str, trip_shape_map_out: str) -> None:
    shapes_path = Path(shapes_out)
    trip_shape_map_path = Path(trip_shape_map_out)

    trip_to_pattern: dict[str, str] = {}
    geom_to_canonical: dict[tuple[tuple[float, float], ...], str] = {}
    pattern_to_canonical: dict[str, str] = {}
    canonical_shapes: dict[str, list[tuple[float, float]]] = {}
    service_link_cache: dict[str, ServiceLink | None] = {}

    with MdbxStorage(Path(netex_mdbx), readonly=True) as db:
        with db.env.ro_transaction() as txn:
            patterns_by_id = {sjp.id: sjp for sjp in db.iter_only_objects(txn, ServiceJourneyPattern)}

            for service_journey in db.iter_only_objects(txn, ServiceJourney):
                if service_journey.journey_pattern_ref is None:
                    continue
                pattern_id = service_journey.journey_pattern_ref.ref
                trip_to_pattern[service_journey.id] = pattern_id

            print(f"Building shapes for {len(patterns_by_id)} journey patterns", file=sys.stderr)
            for i, (pattern_id, sjp) in enumerate(patterns_by_id.items()):
                points = build_shape_points(db, txn, sjp, service_link_cache)
                if len(points) >= 2:
                    geom_key = tuple((round(lat, 7), round(lon, 7)) for lat, lon in points)
                    if geom_key not in geom_to_canonical:
                        geom_to_canonical[geom_key] = pattern_id
                        canonical_shapes[pattern_id] = points
                    pattern_to_canonical[pattern_id] = geom_to_canonical[geom_key]

                if (i + 1) % 2000 == 0:
                    print(f"  {i + 1}/{len(patterns_by_id)} patterns processed", file=sys.stderr)

    with open(shapes_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"])
        for shape_id, points in canonical_shapes.items():
            for seq, (lat, lon) in enumerate(points):
                writer.writerow([shape_id, f"{lat:.7f}", f"{lon:.7f}", seq])

    with open(trip_shape_map_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["trip_id", "shape_id"])
        for trip_id, pattern_id in trip_to_pattern.items():
            canonical_shape_id = pattern_to_canonical.get(pattern_id)
            if canonical_shape_id:
                writer.writerow([trip_id, canonical_shape_id])

    print(
        f"Wrote {len(canonical_shapes)} unique shapes (deduplicated from {len(pattern_to_canonical)} patterns), "
        f"{len(trip_to_pattern)} trip mappings",
        file=sys.stderr,
    )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(f"Usage: {sys.argv[0]} <netex.mdbx> <shapes.txt output> <trip_shape_map.csv output>")
    main(sys.argv[1], sys.argv[2], sys.argv[3])
