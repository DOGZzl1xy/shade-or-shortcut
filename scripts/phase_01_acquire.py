"""Phase 1: acquire every data source for the city (re-runnable; finished downloads are skipped).

    uv run python -m scripts.phase_01_acquire [source ...]

Writes data/raw/manifest.json (URL, date, size) and a copy in reports/tables/phase_01_manifest.json.
"""

import json
import shutil
import sys
import time
import traceback

import geopandas as gpd

from coolpath import load_config
from coolpath.acquire import boundaries, chm, landcover, lidar, osm, tabular, vector
from coolpath.acquire.manifest import record
from coolpath.areas import city, city_grid


def step_boundaries(cfg):
    c, t = boundaries.county(cfg), boundaries.tracts(cfg)
    url = f"Census TIGER/Line {cfg['sources']['tiger_year']} via pygris (county {cfg['sources']['census_fips_county']})"
    raw = cfg["paths"]["raw"] / "boundaries"
    return record(cfg, "boundaries", url, [raw / "philadelphia_county.parquet", raw / "philadelphia_tracts.parquet"],
                  n_tracts=len(t), city_area_km2=float(c.area.iloc[0] / 1e6))


def step_lidar(cfg):
    area = city(cfg).buffer(cfg["shadow"]["max_ray_m"])
    m = lidar.city_manifest(cfg, area)
    return record(cfg, "lidar_manifest", cfg["sources"]["lidar_noaa_stac"], cfg["paths"]["raw"] / "lidar" / "tiles_city.parquet",
                  n_tiles=len(m), laz_total_gb=float(m.bytes.sum() / 1e9), points=int(m["count"].sum()),
                  acquisition=sorted(set(m.datetime.str[:10])),
                  note="LAZ downloaded tile by tile in phase 2 and deleted after rasterising (D-018)")


def step_buildings(cfg):
    dest = cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet"
    g = vector.buildings_city(cfg)
    return record(cfg, "building_footprints", cfg["sources"]["building_footprints_service"], dest,
                  n=len(g), approx_hgt_missing=int((g.approx_hgt.isna() | (g.approx_hgt <= 0)).sum()),
                  note="approx_hgt in US feet (metadata)")


def step_dvrpc(cfg):
    p = vector.dvrpc_pedestrian(cfg)
    g = gpd.read_parquet(p, columns=["county", "line_type", "geometry"])
    phl = g[g.county.str.upper().str.strip() == "PHILADELPHIA"]
    return record(cfg, "dvrpc_pedestrian", cfg["sources"]["dvrpc_ped_geoparquet"], p, n_region=len(g),
                  n_philadelphia=len(phl), line_types={str(k): int(v) for k, v in phl.line_type.value_counts().items()})


def step_osm(cfg):
    poly = gpd.GeoSeries([city(cfg)], crs=cfg["project"]["crs"]).to_crs(4326).iloc[0]
    f = osm.features(cfg, poly)
    files = sorted((cfg["paths"]["raw"] / "osm").glob("osm_*.parquet"))
    return record(cfg, "osm", "Overpass API via osmnx.features_from_polygon (city polygon)", files,
                  counts={k: len(v) for k, v in f.items()},
                  tags={k: str(v) for k, v in osm.GROUPS.items()})


def step_gtfs(cfg):
    feeds = tabular.gtfs(cfg)
    stops = {k: sum(1 for _ in open(v / "stops.txt")) - 1 for k, v in feeds.items()}
    return record(cfg, "septa_gtfs", cfg["sources"]["septa_gtfs"], cfg["paths"]["raw"] / "gtfs",
                  feeds=list(feeds), stops=stops)


def step_opa(cfg):
    g = tabular.opa(cfg)
    res = g[g.category_code.isin(cfg["sources"]["opa_residential_categories"])]
    return record(cfg, "opa_properties", f"{cfg['sources']['opa_carto_sql']} table {cfg['sources']['opa_table']}",
                  cfg["paths"]["raw"] / "opa" / "opa_properties.parquet", n=len(g), n_residential=len(res),
                  missing_geom=int(g.lon.isna().sum()), note="parcel_number, category, census_tract, point only")


def step_acs(cfg):
    a = tabular.acs(cfg)
    return record(cfg, "acs5", cfg["sources"]["acs_api"].format(year=cfg["acs"]["year"]),
                  cfg["paths"]["raw"] / "acs" / f"acs5_{cfg['acs']['year']}_tracts.parquet",
                  n_tracts=len(a), variables=list(cfg["acs"]["variables"]))


def step_chmv2(cfg):
    grid = city_grid(cfg)
    poly = gpd.GeoSeries([city(cfg).buffer(cfg["shadow"]["max_ray_m"])], crs=cfg["project"]["crs"]).to_crs(4326).iloc[0]
    tiles = chm.tiles_for(cfg, poly)
    dates = {t: sorted(set(chm.acq_dates(cfg, t).acq_date.astype(str).str[:10])) for t in tiles}
    p = chm.city_cog(cfg, grid, tiles)
    return record(cfg, "chmv2", cfg["sources"]["chmv2_http_base"], [p, *(cfg["paths"]["raw"] / "chmv2").glob("meta_*.geojson")],
                  tiles=tiles, acq_dates=dates, role="reference only (D-012)", licence="CC BY 4.0")


def step_landcover(cfg):
    p = landcover.city_cog(cfg, city_grid(cfg))
    return record(cfg, "landcover2018", cfg["sources"]["landcover_pasda"] + " (layer 2)", p)


STEPS = {"boundaries": step_boundaries, "lidar": step_lidar, "buildings": step_buildings, "dvrpc": step_dvrpc,
         "osm": step_osm, "gtfs": step_gtfs, "opa": step_opa, "acs": step_acs,
         "landcover": step_landcover, "chmv2": step_chmv2}


def main(names):
    cfg = load_config()
    status = {}
    for name in names or STEPS:
        t0 = time.time()
        try:
            entry = STEPS[name](cfg)
            status[name] = {"ok": True, "s": round(time.time() - t0, 1)}
            print(f"[ok] {name} ({time.time() - t0:.0f} s): {json.dumps(entry, default=str)[:300]}")
        except Exception as e:  # noqa: BLE001  keep going; report at the end
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
    man = cfg["paths"]["raw"] / "manifest.json"
    if man.exists():
        shutil.copy(man, cfg["paths"]["tables"] / "phase_01_manifest.json")
    st = cfg["paths"]["tables"] / "phase_01_status.json"
    old = json.loads(st.read_text()) if st.exists() else {}
    st.write_text(json.dumps({**old, **status}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
