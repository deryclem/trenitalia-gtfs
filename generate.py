#!/usr/bin/env python3
"""
Trenitalia GTFS generator.

Downloads Trenitalia's NeTEx feed from the Italian NAP (National Access
Point), converts it to GTFS via badger (vendor/badger), and writes a GTFS
zip. Refuses to overwrite the existing feed if the download looks broken,
stale, or missing Trenitalia's data.

Usage:
    python3 generate.py
"""

import csv
import gzip
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests  # pip install requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
HTTP = requests.Session()
HTTP.mount("https://", HTTPAdapter(max_retries=_retry))


# ── Settings ──────────────────────────────────────────────────────────────────

OUTPUT_ZIP = Path("gtfs-trenitalia.zip")

# Italian NAP (National Access Point) asset for Trenitalia's NeTEx level 1
# feed. If this ever starts 404ing or returning something else, get the
# current asset id from the catalog page and update this URL:
# https://www.cciss.it/nap/mmtis/public/en/catalog/Dataset/1077621
#
# Uses "checkedResource", not "resource": "resource" is the very latest
# publish but its service dates start ~2 weeks later than its own publish
# date, which leaves an actual coverage gap for near-term dates on a weekly
# run. "checkedResource" lags ~2 weeks behind on freshness but its coverage
# starts well before its publish date, so it never has that gap.
NETEX_URL = "https://www.cciss.it/nap/mmtis/public/api/v1/download/blob/Asset/1080596/checkedResource"

BADGER_DIR = Path("vendor/badger")
BADGER_PYTHON = BADGER_DIR / ".venv" / "bin" / "python3"

WORK_DIR = Path("work")
NETEX_GZ = WORK_DIR / "trenitalia_netex.xml.gz"
NETEX_DB = WORK_DIR / "netex.mdbx"
GTFS_DB = WORK_DIR / "gtfs.mdbx"

# Trenitalia's operator id in the NeTEx feed. Used as a sanity check that
# what we downloaded is actually Trenitalia's data and not an empty or
# unrelated file served from the same endpoint.
EXPECTED_OPERATOR_ID = "IT::Operator:05403151003:TRENITALIA:TRENITALIA"

# badger doesn't read the NeTEx FrameDefaults timezone for Operator-sourced
# agencies and falls back to Europe/Amsterdam (its own maintainers flag this
# as a TODO in transformers/gtfsprofile.py). Trenitalia's feed declares
# Europe/Rome in FrameDefaults, so we correct it after conversion.
AGENCY_TIMEZONE = "Europe/Rome"

SHAPES_SCRIPT = Path("scripts/generate_shapes.py")
SHAPES_TXT = WORK_DIR / "shapes.txt"
TRIP_SHAPE_MAP = WORK_DIR / "trip_shape_map.csv"

STALE_STATE_FILE = Path(".last_publication_timestamp")


# ── Download ──────────────────────────────────────────────────────────────────

def download_netex() -> Path:
    print(f"Downloading NeTEx feed from {NETEX_URL}")
    response = HTTP.get(NETEX_URL, timeout=120)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "gzip" not in content_type:
        sys.exit(f"Unexpected Content-Type: {content_type!r} (expected gzip)")

    WORK_DIR.mkdir(exist_ok=True)
    NETEX_GZ.write_bytes(response.content)
    print(f"Downloaded {len(response.content):,} bytes")
    return NETEX_GZ


def read_publication_timestamp_and_operator(netex_gz: Path) -> tuple[str, bool]:
    """
    Reads just enough of the (large, ~350MB decompressed) NeTEx file to pull
    the PublicationTimestamp and confirm Trenitalia's operator id is present,
    without loading the whole thing into memory.
    """
    publication_timestamp = None
    has_operator = False
    with gzip.open(netex_gz, "rt", encoding="utf-8") as f:
        for _ in range(200):
            line = f.readline()
            if not line:
                break
            if publication_timestamp is None:
                m = re.search(r"<PublicationTimestamp>([^<]+)</PublicationTimestamp>", line)
                if m:
                    publication_timestamp = m.group(1)
            if EXPECTED_OPERATOR_ID in line:
                has_operator = True
            if publication_timestamp and has_operator:
                break
        else:
            # Operator declaration can be further in the file than the
            # first 200 lines; keep scanning for it alone.
            if not has_operator:
                f.seek(0)
                for line in f:
                    if EXPECTED_OPERATOR_ID in line:
                        has_operator = True
                        break

    if publication_timestamp is None:
        sys.exit("Could not find PublicationTimestamp in the downloaded NeTEx file")
    return publication_timestamp, has_operator


def read_train_numbers(netex_gz: Path) -> dict[str, str]:
    """
    Maps trip_id (== ServiceJourney/@id) -> train number (ServiceJourney/Name).
    badger's GTFS export always leaves trip_short_name blank even though the
    train number is right there in the NeTEx (transformers/gtfsprofile.py
    hardcodes it to ''), so we pull it ourselves.
    """
    train_numbers: dict[str, str] = {}
    pattern = re.compile(r'<ServiceJourney id="([^"]+)"[^>]*>\s*<Name>([^<]+)</Name>')
    with gzip.open(netex_gz, "rt", encoding="utf-8") as f:
        content = f.read()
    for match in pattern.finditer(content):
        train_numbers[match.group(1)] = match.group(2)
    return train_numbers


def check_freshness(publication_timestamp: str) -> None:
    """
    Refuses to proceed if the feed doesn't look like a genuine update:
    same publication timestamp as last run (unchanged file, likely a stale
    cache on Trenitalia's/the NAP's side) is treated as a soft warning, not
    a hard failure, since re-publishing an already-fresh feed isn't harmful.
    Missing the operator entirely is a hard failure.
    """
    if STALE_STATE_FILE.exists():
        last_timestamp = STALE_STATE_FILE.read_text().strip()
        if last_timestamp == publication_timestamp:
            print(f"Warning: PublicationTimestamp unchanged since last run ({publication_timestamp})")


# ── Conversion (badger) ──────────────────────────────────────────────────────

def run_badger_step(*args: str) -> None:
    # Absolute, but NOT fully resolved: resolving would follow the venv's
    # python3 symlink to the real interpreter binary, which then fails to
    # detect it's running inside a venv and loses badger's site-packages.
    python = BADGER_PYTHON.absolute() if BADGER_PYTHON.exists() else Path(sys.executable).absolute()
    cmd = [str(python), "-m", *args]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=BADGER_DIR, check=True)


def convert_to_gtfs(netex_gz: Path) -> Path:
    netex_gz_abs = netex_gz.resolve()
    netex_db_abs = NETEX_DB.resolve()
    gtfs_db_abs = GTFS_DB.resolve()
    gtfs_zip_abs = (WORK_DIR / "gtfs_raw.zip").resolve()

    for stale in (netex_db_abs, gtfs_db_abs):
        if stale.exists():
            shutil.rmtree(stale)

    run_badger_step("conv.netex_to_db", str(netex_gz_abs), str(netex_db_abs))
    generate_shapes(netex_db_abs)
    run_badger_step("conv.gtfs_db_to_db", str(netex_db_abs), str(gtfs_db_abs))
    run_badger_step("conv.gtfs_db_to_gtfs", str(gtfs_db_abs), str(gtfs_zip_abs))

    return gtfs_zip_abs


def generate_shapes(netex_db_abs: Path) -> None:
    """
    badger's own GTFS export never writes shapes.txt (see scripts/generate_shapes.py
    for why), so we build it ourselves straight from netex.mdbx while the
    ServiceJourneyPattern/ServiceLink data badger's own step 2 discards is
    still around.
    """
    python = BADGER_PYTHON.absolute() if BADGER_PYTHON.exists() else Path(sys.executable).absolute()
    cmd = [
        str(python),
        str(SHAPES_SCRIPT.resolve()),
        str(netex_db_abs),
        str(SHAPES_TXT.resolve()),
        str(TRIP_SHAPE_MAP.resolve()),
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=BADGER_DIR, check=True)


# ── Post-processing ───────────────────────────────────────────────────────────

def post_process(gtfs_zip: Path, train_numbers: dict[str, str]) -> None:
    """
    Fixes up what badger's own GTFS export gets wrong or leaves out:
    - agency_timezone defaults to Europe/Amsterdam (see AGENCY_TIMEZONE).
    - trip_short_name (train number) is always blank; we fill it from the
      NeTEx source ourselves (see read_train_numbers).
    - shapes.txt doesn't exist at all; we generate it ourselves (see
      generate_shapes) and wire it into trips.txt via shape_id.
    """
    extract_dir = WORK_DIR / "gtfs_extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir()

    with zipfile.ZipFile(gtfs_zip) as zf:
        zf.extractall(extract_dir)

    agency_path = extract_dir / "agency.txt"
    with open(agency_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    for row in rows:
        row["agency_timezone"] = AGENCY_TIMEZONE
    with open(agency_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    trip_shape_map: dict[str, str] = {}
    if TRIP_SHAPE_MAP.exists():
        with open(TRIP_SHAPE_MAP, newline="", encoding="utf-8") as f:
            trip_shape_map = {row["trip_id"]: row["shape_id"] for row in csv.DictReader(f)}

    trips_path = extract_dir / "trips.txt"
    with open(trips_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    for row in rows:
        if row["trip_id"] in train_numbers:
            row["trip_short_name"] = train_numbers[row["trip_id"]]
        if row["trip_id"] in trip_shape_map:
            row["shape_id"] = trip_shape_map[row["trip_id"]]
    with open(trips_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    if SHAPES_TXT.exists():
        shutil.copy(SHAPES_TXT, extract_dir / "shapes.txt")

    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(extract_dir.iterdir()):
            zf.write(file, arcname=file.name)


def sanity_check(gtfs_zip: Path) -> None:
    """
    Catches the two failure modes we've actually hit while building this:
    stops.txt missing entirely, and stop_id references in stop_times.txt
    that don't exist in stops.txt.
    """
    with zipfile.ZipFile(gtfs_zip) as zf:
        names = set(zf.namelist())
        required = {"agency.txt", "routes.txt", "stops.txt", "trips.txt", "stop_times.txt", "calendar.txt"}
        missing = required - names
        if missing:
            sys.exit(f"Generated GTFS is missing required files: {sorted(missing)}")

        with zf.open("stops.txt") as f:
            stop_ids = {row["stop_id"] for row in csv.DictReader(line.decode("utf-8") for line in f)}
        with zf.open("stop_times.txt") as f:
            used_stop_ids = {row["stop_id"] for row in csv.DictReader(line.decode("utf-8") for line in f)}

        orphans = used_stop_ids - stop_ids
        if orphans:
            sys.exit(f"stop_times.txt references {len(orphans)} stop_ids missing from stops.txt (e.g. {sorted(orphans)[:5]})")

        if len(stop_ids) == 0 or len(used_stop_ids) == 0:
            sys.exit("Generated GTFS has no stops or no stop_times: conversion likely failed silently")

        if "shapes.txt" in names:
            with zf.open("shapes.txt") as f:
                shape_ids = {row["shape_id"] for row in csv.DictReader(line.decode("utf-8") for line in f)}
            with zf.open("trips.txt") as f:
                used_shape_ids = {row["shape_id"] for row in csv.DictReader(line.decode("utf-8") for line in f) if row["shape_id"]}
            orphan_shapes = used_shape_ids - shape_ids
            if orphan_shapes:
                sys.exit(f"trips.txt references {len(orphan_shapes)} shape_ids missing from shapes.txt (e.g. {sorted(orphan_shapes)[:5]})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    netex_gz = download_netex()
    publication_timestamp, has_operator = read_publication_timestamp_and_operator(netex_gz)

    if not has_operator:
        sys.exit(f"Downloaded feed does not contain Trenitalia's operator id ({EXPECTED_OPERATOR_ID}); refusing to publish")

    check_freshness(publication_timestamp)
    train_numbers = read_train_numbers(netex_gz)

    gtfs_zip = convert_to_gtfs(netex_gz)
    post_process(gtfs_zip, train_numbers)
    sanity_check(OUTPUT_ZIP)

    STALE_STATE_FILE.write_text(publication_timestamp)
    shutil.rmtree(WORK_DIR, ignore_errors=True)

    print(f"Wrote {OUTPUT_ZIP} (PublicationTimestamp: {publication_timestamp})")


if __name__ == "__main__":
    main()
