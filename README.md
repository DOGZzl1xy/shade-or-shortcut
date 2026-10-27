# Shade or Shortcut — cooling-path-philly

Shade-aware pedestrian routing for Philadelphia (MUSA 5500 final project). For every sidewalk
segment and 24 summer hours, we model sun / tree shade / building shade from 2022 LiDAR, then compare
the shortest path with the shade-weighted path (Wen et al. cost, dynamic θ) on walks home from transit.

- **Website: <https://dogzzl1xy.github.io/shade-or-shortcut/>** (story, route tool, city shade map, data / methods / results)
- [Technical notes](https://dogzzl1xy.github.io/shade-or-shortcut/technical.html): pipeline, parameters, processing decisions and validation
- All parameters and data sources: [`config.yaml`](config.yaml)
- Code: MIT licence ([`LICENSE`](LICENSE)); data keep their own terms (see [Data sources and licences](#data-sources-and-licences))

## Pipeline

| Phase | What |
| --- | --- |
| 0 | Feasibility tests in a University City test area |
| 1 | Data acquisition |
| 2 | 1 m surfaces: ground, building and tree heights |
| 3 | Sun positions and shadow casting (sample points every 2 m, 24 hours) |
| 4 | Pedestrian graph and edge shade table |
| 5 | Shortest and shade-weighted routing |
| 6 | Walks from transit stops to homes, and their metrics |
| 7 | Summaries by mode and walk length, rapid-transit stations, 500 m grid clusters |
| 8 | University City demo with indoor passages (41 annotated buildings, three scenarios) |
| 9 | Website |

## Key results so far (15:00, dynamic θ, trips of 300–800 m)

City sample: walks home from the nearest rapid-transit station or high-frequency bus stop (≥ 6 departures
per hour on one route-direction, weekday 15:00–19:00, SEPTA GTFS fall 2026, 2026-10-28), every residential
parcel within 1.2 km network distance (431,100 trips; 17.5% of parcels have no such stop within 1.2 km).

| | Sun reduction | Detour | Weighted-exposure reduction | No cooler alternative (< 2%) |
| --- | --- | --- | --- | --- |
| High-frequency bus | 9.7% | 0.44% | 1.08% | 61% |
| Rapid transit | 16.0% | 0.64% | 1.90% | 50% |

Network density is the strongest correlate of whether sun can be avoided; building height mainly sets
how sunny the walk is to begin with. Full results: the [website](https://dogzzl1xy.github.io/shade-or-shortcut/results.html).

## Layout

```
cooling-path-philly/
├── config.yaml            # paths, parameters, data sources
├── src/coolpath/          # acquire/, areas, surface(_city), sun, shadow, network, routing, transit
├── scripts/               # one entry point per phase step (python -m scripts.<name>); templates/
├── annotate/              # phase 8 annotation page (index.html) and the exported annotations (3 GeoJSON files)
├── tests/                 # pytest: synthetic shadows, routing, bridges, surfaces
├── reports/               # figures/ (check and result figures); tables/ (json / csv / txt written by the scripts)
├── data/                  # not in git: raw/ (downloads), interim/ (tiles, checks, logs), processed/ (final rasters and tables)
├── notebooks/             # sources of the data / methods / results pages
├── site/                  # Quarto website; site/app = hand-written story, route tool, city map; site/app/data = exported web data
```

## Reproducing from scratch

**Requirements.** macOS or Linux, [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned in `.python-version`),
an internet connection, about 20 GB of free disk and 18 GB of RAM. The website steps also need
[Quarto](https://quarto.org/) (1.10), [tippecanoe](https://github.com/felt/tippecanoe) (2.79) and, for the
JS / Python consistency test, Node.js (20+). Developed on macOS (Apple silicon).

**Outline.** No data is stored in this repository; every acquisition script downloads what it needs into
`data/` (gitignored). Raw downloads are about 3.6 GB; the 92 GB of LiDAR is streamed tile by tile in phase 2
(download → rasterise → delete, about an hour with 4 processes), so the disk peak stays around 15 GB.

```bash
git clone https://github.com/DOGZzl1xy/shade-or-shortcut.git && cd shade-or-shortcut
uv sync
uv run pytest                                   # 42 tests; the JS consistency test is skipped until phase 9 data exist
uv run python -m scripts.phase_01_acquire       # all sources (phase 1)
caffeinate -i uv run python -m scripts.phase_02_surface      # 1 m surfaces (on Linux drop `caffeinate -i`)
caffeinate -i uv run python -m scripts.phase_03_shadow       # network, sample points, 24-hour shadows
uv run python -m scripts.phase_04_edges
uv run python -m scripts.phase_06_trips && uv run python -m scripts.phase_06_summary
uv run python -m scripts.phase_07_grid distribution && uv run python -m scripts.phase_07_grid
mkdir -p data/raw/indoor && cp annotate/*.geojson data/raw/indoor/     # the phase 8 annotations shipped with the repo
uv run python -m scripts.phase_08_demo build && uv run python -m scripts.phase_08_demo trips
uv run python -m scripts.phase_09_export
cd site && QUARTO_PYTHON=../.venv/bin/python quarto render --execute && cd ..
uv run python -m scripts.serve_site             # http://127.0.0.1:8770
```

The checks, figures and diagnostics of each phase are listed below. Every script reads its parameters from
`config.yaml` and writes its numbers to `reports/tables/`, so a rerun can be compared with the committed
reports. Results depend on the downloaded data versions: SEPTA replaces its GTFS feed every few months
(this analysis used the fall 2026 timetable, v202609271), and OpenStreetMap changes continuously.

`CENSUS_API_KEY` (in the environment or a gitignored `.env`) is only needed for the ACS download in phase 1,
which the analysis no longer uses; without it, run phase 1 with the other source names, e.g.
`uv run python -m scripts.phase_01_acquire boundaries lidar buildings dvrpc osm gtfs opa landcover chmv2`.

## Running the pipeline, phase by phase

Long steps run under `caffeinate`; phase 2 and 3 are resumable (finished tiles are skipped).

### Phase 0 — feasibility (University City test area)

```bash
uv run python -m scripts.phase_00_t0_env
uv run python -m scripts.phase_00_t1_lidar
uv run python -m scripts.phase_00_t2_surface
uv run python -m scripts.phase_00_t3_chm
uv run python -m scripts.phase_00_t4_network
uv run python -m scripts.phase_00_t5_shadow
uv run python -m scripts.phase_00_t6_routing
```

### Phase 1 — data acquisition

```bash
uv run python -m scripts.phase_01_acquire      # all sources; or name some: boundaries lidar buildings dvrpc osm gtfs opa acs landcover chmv2
uv run python -m scripts.phase_01_overview
```

### Phase 2 — surfaces

```bash
caffeinate -i uv run python -m scripts.phase_02_surface      # stages A (LAZ tiles) B (mosaic) C (surfaces)
uv run python -m scripts.phase_02_bridge_check
uv run python -m scripts.phase_02_qa
uv run python -m scripts.phase_02_spotcheck                  # extrusion and rail-underpass spot checks
uv run python -m scripts.phase_02_2716_check                 # 2716 South St shadow check
uv run python -m scripts.fix_cog_overviews <tif ...>         # only for COGs written before overviews were fixed
```

Outputs in `data/processed/`: `dem.tif`, `dsm.tif`, `surface_building.tif`, `surface_tree.tif` (uint16 decimetres), `building_source.tif`.

### Phase 3 — sun and shadows

```bash
caffeinate -i uv run python -m scripts.phase_03_shadow       # network -> sample points -> shadow casting
uv run python -m scripts.phase_03_bridge_check
uv run python -m scripts.phase_03_qa
```

Outputs: `network_edges.parquet`, `network_nodes.parquet`, `sun.parquet`, `shadow_points.parquet`.

### Phase 4 — edge shade table, phase 5 — routing

```bash
uv run python -m scripts.phase_04_edges       # graph_edges / graph_nodes / edges_shade.parquet
uv run python -m scripts.phase_05_routing     # validation on the city graph
```

### Phase 6 — trips and metrics, phase 7 — summaries

```bash
uv run python -m scripts.phase_06_station_candidates        # station-sample candidates (add `plot` to redraw from the CSV)
uv run python -m scripts.diag_home_stops                    # diagnostic: three home-stop definitions
caffeinate -i uv run python -m scripts.phase_06_trips       # station + city samples, metrics incl. weighted exposure (~80 s)
uv run python -m scripts.phase_06_summary
uv run python -m scripts.phase_07_grid distribution         # threshold figure for "no cooler alternative"
uv run python -m scripts.phase_07_grid                      # mode × length bin, rapid stations, 500 m grid, K-means (k = 3)
uv run python -m scripts.phase_07_route_check               # top-5 rapid stations: shortest vs shade path figures
```

Outputs: `trips_metrics.parquet`, `trips_city_parcels.parquet`, `trips_city_uncovered.parquet`,
`grid_metrics.parquet`, `stations_metrics.parquet`.

### Phase 8 — University City demo

```bash
uv run python -m scripts.phase_08_annotate                  # writes annotate/index.html
uv run python -m scripts.phase_08_demo build                # demo graph: OSM footways, entrance connectors, shadows
uv run python -m scripts.phase_08_demo trips                # three scenarios × campus / station / home samples, ICI
```

Open `annotate/index.html` directly in Chrome or Safari (no server, no CDN; only the aerial imagery needs internet). For each building: answer whether it
needs a PennCard, place entrances (snapped to the outline) and draw indoor passages between two entrances.
Progress is kept in the browser (use "Back up JSON" regularly). Export the three GeoJSON files
(`buildings`, `entrances`, `indoor_edges`) into `data/raw/indoor/`. The annotations used for the results are
committed in `annotate/`.

### Phase 9 — website

```bash
uv run python -m scripts.phase_09_export                    # site/app/data: demo network JSON, city PMTiles, layers
uv run python -m scripts.phase_09_heat                      # site/app/data: Landsat surface temperature PNG, airport heat numbers
cd site && QUARTO_PYTHON=../.venv/bin/python quarto render  # site/_site (analysis pages are frozen in site/_freeze)
uv run python -m scripts.serve_site                         # local preview at http://127.0.0.1:8770 (supports Range requests)
uv run pytest tests/test_js_consistency.py                  # browser router == Python router on 100 random trips
scripts/publish_site.sh prepare                             # local repo in data/publish/repo: main = code, gh-pages = build
scripts/publish_site.sh scan                                # check main for keys, local paths, .env, files > 50 MB
scripts/publish_site.sh push                                # create DOGZzl1xy/shade-or-shortcut, push both, turn on Pages
```

Hand-written pages live in `site/app/` (story, route tool, city map); data, methods and results pages are rendered from
`notebooks/`. The code and the site are published in their own public
repository (`DOGZzl1xy/shade-or-shortcut`: `main` = code, `gh-pages` = website). When the analysis notebooks change,
render them with `quarto render <page>.qmd --execute`: `freeze: auto` does not notice edits inside included files.

## Data sources and licences

The code in this repository is released under the MIT licence. Data are downloaded from the providers
below and keep their own terms; the derived files committed here (`site/app/data/`, `reports/`) inherit them.
In particular, the University City demo network (`site/app/data/demo_network.json`) contains OpenStreetMap
footways and is therefore available under the ODbL.

| Data | Provider | Terms / licence |
| --- | --- | --- |
| Airborne LiDAR, March 2022 (dataset 9848) | City of Philadelphia Office of Innovation and Technology (collected by Merrick); distributed by [NOAA Digital Coast](https://coast.noaa.gov/dataviewer/#/lidar/search/where:ID=9848) and [PASDA](https://www.pasda.psu.edu/) | no access constraints; NOAA's use note warns that conditions may have changed since collection |
| Building footprints; OPA properties | City of Philadelphia, [OpenDataPhilly](https://opendataphilly.org/datasets/building-footprints) | City of Philadelphia License (terms of use), data provided as is |
| Land cover 2018; orthophotos (checks and annotation only) | [PASDA](https://www.pasda.psu.edu/) | public download; credit PASDA |
| Canopy height (CHMv2), 1 km buffer outside the city | Meta and World Resources Institute | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), attribution required |
| Pedestrian network (sidewalks, crosswalks) | [DVRPC](https://catalog.dvrpc.org/dataset/dvrpc-pedestrian-network) | public download; the catalog lists no licence; credit DVRPC |
| Bridges, covered walkways, building passages, campus footways | © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) |
| Transit stops and timetable (GTFS, fall 2026) | [SEPTA](https://www3.septa.org/developer/) | public feed; no licence specified in the OpenDataPhilly listing |
| City boundary (TIGER/Line 2024), ACS | U.S. Census Bureau | public domain |
| Land surface temperature, Landsat 8 Collection 2 Level-2, 2024-07-17 (Story page only) | [USGS](https://www.usgs.gov/landsat-missions), read through [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2) | public domain; credit USGS |
| Daily maximum air temperature, Philadelphia International Airport (Story page only) | NOAA Regional Climate Centers, [ACIS](https://www.rcc-acis.org/) | public domain |
| Website basemap (World Dark Gray) | Esri | Esri terms of use, attribution shown on every map |

Bundled web libraries: MapLibre GL JS 4.7.1 and PMTiles 3.2.1 (both BSD-3-Clause, in `site/app/vendor/`).
