# gtfs-trenitalia

[![GTFS Validator](https://img.shields.io/badge/MobilityData%20Validator-0%20errors-brightgreen)](https://github.com/MobilityData/gtfs-validator)
[![Updated: Weekly](https://img.shields.io/badge/Updated-Weekly%20(Mondays)-blue)](https://github.com/deryclem/trenitalia-gtfs/actions)
[![License: CC0-1.0](https://img.shields.io/badge/License-CC0_1.0-lightgrey.svg)](https://creativecommons.org/publicdomain/zero/1.0/)

Official [GTFS](https://gtfs.org) schedule feed for **Trenitalia**, converted from official NeTEx timetable data published through Italy's National Access Point (NAP).

This feed also feeds into [Panto](https://getpanto.app), a real-time train tracking app currently in beta.

📦 **[Download latest GTFS package (`gtfs-trenitalia.zip`)](./gtfs-trenitalia.zip)**

---

## 🚆 Services Covered

The feed covers Trenitalia's full national rail and connecting network published in their NeTEx level 1 feed:

| Category | Service Name | Type | Description |
|---|---|---|---|
| **FR** | Frecciarossa | High-speed rail | Flagship high-speed services operating on dedicated AV corridors |
| **FA** | Frecciargento | High-speed rail | High-speed services connecting main lines and high-speed corridors |
| **FB** | Frecciabianca | Intercity rail | Long-distance services outside high-speed corridors |
| **IC** | Intercity | Intercity rail | Daytime domestic long-distance connections |
| **ICN** | InterCityNotte | Sleeper train | Overnight domestic sleeper services |
| **EC** | Eurocity | International rail | Cross-border daytime services (Switzerland, Austria, Germany) |
| **EN** | Euronight | International sleeper | Cross-border overnight sleeper services |
| **REG** | Regionale | Regional rail | Local and regional stopping services |
| **RV** | Regionale Veloce | Regional rail | Fast regional and semi-direct interurban services |
| **SFM** | Servizio Ferroviario Metropolitano | Commuter rail | Suburban commuter rail networks (e.g. Turin) |
| **MET** | Metropolitano | Urban rail | Urban heavy rail services (e.g. Naples Line 2) |
| **EXP** | Espresso | Tourist rail | Special tourist services (FS Treni Turistici Italiani) |
| **BUS** | Autobus | Bus | Connecting bus routes and rail replacement services |
| **FL** | FrecciaLink | Bus | Dedicated high-speed connection buses to tourist destinations |

---

## 📊 Where the data comes from

| Source | What it provides |
|---|---|
| [Italy's NAP](https://www.cciss.it/nap/mmtis/public/en/catalog/Dataset/1077621) | Trenitalia's official NeTEx level 1 feed (Italian/EPIP profile), covering stops, lines, calendars, timetabled passing times, and track geometry (`ServiceLink`). |
| [MMTIS/badger](https://github.com/MMTIS/badger) | Core NeTEx -> GTFS conversion engine (pinned submodule in `vendor/badger`). |
| `generate.py` | Pipeline orchestration: streaming metadata extraction, shape deduplication, station code mapping, and post-processing corrections. |

The feed uses the NAP's `checkedResource` download asset to avoid service date gaps for near-term dates.

---

## 🔍 Features & NeTEx Augmentations

- **Boarding & Alighting restrictions**: `pickup_type` and `drop_off_type` are populated in `stop_times.txt` directly from NeTEx `StopPointInJourneyPattern` restrictions (`<ForBoarding>false` -> `pickup_type=1`, `<ForAlighting>false` -> `drop_off_type=1`).
- **Station Codes**: `stop_code` is populated for all parent stations in `stops.txt` using the official 9-digit Italian UIC codes from `<StopPlace><PrivateCode>`.
- **Train Numbers**: `trip_short_name` is populated from NeTEx `ServiceJourney/Name`.
- **Deduplicated Shapes**: `scripts/generate_shapes.py` builds canonical geometries from `ServiceLink` coordinates, reducing redundant shapes by 79% (shrunk `shapes.txt` from 55 MB to 14.5 MB).
- **Metadata**: Standard `feed_info.txt` generated with publisher metadata, validity dates, and publication timestamp.
- **Station Names Unicode Fix**: Trailing accented characters corrupted to `\ufffd` in Trenitalia source (e.g. "Annà", "Palermo Libertà", "Cirié") are automatically restored.
- **No trip headsigns**: Trenitalia's NeTEx does not include `DestinationDisplay` elements, so `trip_headsign` remains empty.

---

## ⚙️ Generating

Requires Python 3.12 and [uv](https://github.com/astral-sh/uv).

```bash
git submodule update --init --recursive
cd vendor/badger && uv venv --python 3.12 && uv sync && sh scripts/generate-schema.sh && cd ../..
uv run --python 3.12 --with-requirements requirements.txt python3 generate.py
```

Runs automatically every Monday via GitHub Actions.

---

## 📄 License

Feed: [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/). Source data: © Trenitalia / Italy's National Access Point, published under the EU MMTIS regulation.  
Not affiliated with Trenitalia S.p.A.
