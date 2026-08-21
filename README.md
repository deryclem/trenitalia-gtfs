# gtfs-trenitalia

A [GTFS](https://gtfs.org) feed for Trenitalia, converted from the NeTEx timetable data
Trenitalia publishes through Italy's National Access Point (NAP).

## Why this exists

Trenitalia publishes its timetables as NeTEx, the EU's data-interchange standard for
public transport, but not as GTFS. NeTEx is comprehensive but not what most transit
tooling (trip planners, real-time tracking apps, GTFS validators) actually consumes, so
this converts the official feed into GTFS instead of scraping timetables from scratch.

This feed also feeds into [Panto](https://getpanto.app), a real-time train tracking app
currently in beta.

## Download

[gtfs-trenitalia.zip](./gtfs-trenitalia.zip)

## Coverage

Trenitalia's full national rail network as published in their NeTEx level 1 feed:
regional trains, Intercity, Frecciarossa/Frecciargento/Frecciabianca high-speed
services, Euronight sleepers, and the replacement bus services Trenitalia runs under
the same lines. See `routes.txt` in the generated zip for the exact list, since it's
pulled straight from the source feed rather than hardcoded here.

## Where the data comes from

| Source | What it provides |
|--------|-------------------|
| [Italy's NAP](https://www.cciss.it/nap/mmtis/public/en/catalog/Dataset/1077621) | Trenitalia's official NeTEx level 1 feed (Italian/EPIP profile), covering stops, lines, calendars, timetabled passing times, and per-segment track geometry (`ServiceLink`). Published under the EU's MMTIS regulation. |
| [MMTIS/badger](https://github.com/MMTIS/badger) | Does the NeTEx → GTFS conversion for everything except shapes (see below). Vendored as a pinned submodule in `vendor/badger`. |

The NAP asset ID this feed downloads from can change if Trenitalia republishes under a
new asset. If the download starts failing, the [catalog page](https://www.cciss.it/nap/mmtis/public/en/catalog/Dataset/1077621)
has the current one.

The NAP actually exposes two downloads for this asset: `resource` (the very latest
publish) and `checkedResource` (a validated version that trails `resource` by roughly
two weeks). This feed uses `checkedResource` deliberately: `resource`'s service dates
start about two weeks after its own publish timestamp, which on a weekly run leaves a
real gap in near-term coverage. `checkedResource`'s coverage starts well before its
publish date, so it doesn't have that gap — at the cost of being a couple of weeks
behind on the very latest schedule changes.

## Generating

Requires Python 3.12 (badger's dependencies don't build on newer versions) and
[uv](https://github.com/astral-sh/uv).

```bash
git submodule update --init --recursive
cd vendor/badger && uv venv --python 3.12 && uv sync && sh scripts/generate-schema.sh && cd ../..
uv run --python 3.12 --with-requirements requirements.txt python3 generate.py
```

Downloads the current feed, runs it through badger's NeTEx → GTFS pipeline, and
corrects a couple of known gaps in the output (see Limitations). Takes about ten
minutes, most of it the NeTEx parsing step. Runs automatically every Monday via
GitHub Actions.

`generate.py` refuses to overwrite the committed feed if the download doesn't look like
genuine Trenitalia data (missing operator id) or the conversion produces a structurally
broken GTFS (missing stops, orphaned stop_time references) — better to keep serving
last week's feed than silently publish a broken one.

## Limitations

- No trip headsigns: Trenitalia's NeTEx feed doesn't include `DestinationDisplay`
  elements, so `trip_headsign` is empty throughout. The information isn't in the
  source feed to begin with.
- `agency_timezone` is hardcoded to `Europe/Rome` in post-processing: badger defaults
  it to `Europe/Amsterdam` for NeTEx `Operator`-sourced agencies rather than reading it
  from the feed's own `FrameDefaults` (a gap its own maintainers have flagged, not
  something specific to this feed).
- `trip_short_name` (train number) and `shapes.txt` (route geometry) aren't produced by
  badger's GTFS export at all — the NeTEx data for both exists (`ServiceJourney/Name`
  and `ServiceLink`/`gml:posList` respectively) but nothing in badger's pipeline reads
  them into GTFS. `scripts/generate_shapes.py` builds shapes.txt directly from the
  intermediate NeTEx database instead of going through badger for this part; the
  train number is pulled from the source feed the same way.
- A handful of station names (fewer than a dozen, always the last accented letter of
  an Italian name — "Cirié", "Palermo Libertà") come through the NeTEx feed already
  corrupted to the Unicode replacement character. Confirmed present in Trenitalia's own
  source file, not introduced by this pipeline; nothing to reconstruct the original
  letter from.

## License

Feed: [CC0](https://creativecommons.org/publicdomain/zero/1.0/). Source data:
© Trenitalia / Italy's National Access Point, published under the EU's MMTIS
regulation.

Not affiliated with Trenitalia S.p.A.
