"""Phase 1 overview figure and summary counts (reads data/raw only)."""

import json

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

from coolpath import load_config
from coolpath.acquire import boundaries
from coolpath.areas import city


def main():
    cfg = load_config()
    raw, crs = cfg["paths"]["raw"], cfg["project"]["crs"]
    c = city(cfg)
    tracts = boundaries.tracts(cfg)
    tiles = gpd.read_parquet(raw / "lidar" / "tiles_city.parquet")
    ped = gpd.read_parquet(raw / "dvrpc" / "pedestriannetwork_lines.parquet")
    ped = ped[ped.county.str.upper().str.strip() == "PHILADELPHIA"].to_crs(crs)
    stops = {}
    for feed in ("google_bus", "google_rail"):
        s = pd.read_csv(raw / "gtfs" / feed / "stops.txt")
        s = gpd.GeoDataFrame(s, geometry=gpd.points_from_xy(s.stop_lon, s.stop_lat), crs=4326).to_crs(crs)
        stops[feed] = s[s.within(c)]
    br = gpd.read_parquet(raw / "osm" / "osm_bridge.parquet")
    br_lines = br[br.geom_type.isin(["LineString", "MultiLineString"])]
    summary = {
        "gtfs_stops_in_city": {k: len(v) for k, v in stops.items()},
        "dvrpc_city_km": float(ped.length.sum() / 1e3),
        "dvrpc_city_by_line_type": {str(k): int(v) for k, v in ped.line_type.value_counts().items()},
        "dvrpc_grade_separated": int((ped.feat_type == "GRADE_SEPARATED").sum()),
        "osm_bridge_lines": int(len(br_lines)),
        "osm_bridge_highway": int(br_lines.highway.notna().sum()),
        "osm_bridge_railway_only": int((br_lines.highway.isna() & br_lines.railway.notna()).sum()),
        "osm_bridge_pedestrian": int(br_lines.highway.isin(["footway", "path", "pedestrian", "steps", "cycleway"]).sum()),
        "opa_by_category": {k: int(v) for k, v in pd.read_parquet(raw / "opa" / "opa_properties.parquet",
                                                                     columns=["category_code"]).category_code.value_counts().items()},
    }
    (cfg["paths"]["tables"] / "phase_01_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

    fig, ax = plt.subplots(1, 3, figsize=(21, 8))
    tiles.boundary.plot(ax=ax[0], color="#bbb", lw=0.3)
    tracts.boundary.plot(ax=ax[0], color="#4477aa", lw=0.3)
    gpd.GeoSeries([c]).boundary.plot(ax=ax[0], color="k", lw=1.2)
    ax[0].set_title(f"{len(tiles)} LiDAR tiles (grey), {len(tracts)} tracts (blue)")
    ped.plot(ax=ax[1], color="k", lw=0.1)
    stops["google_bus"].plot(ax=ax[1], color="#e07b00", markersize=0.3)
    stops["google_rail"].plot(ax=ax[1], color="#c00", markersize=8)
    ax[1].set_title(f"DVRPC network {summary['dvrpc_city_km']:.0f} km; SEPTA stops in city "
                    f"(bus/trolley/subway {len(stops['google_bus'])}, rail {len(stops['google_rail'])})")
    gpd.GeoSeries([c]).boundary.plot(ax=ax[2], color="k", lw=0.8)
    br_lines.plot(ax=ax[2], color="#7a3fa0", lw=0.8)
    for name, col in [("covered", "#2a9d3a"), ("building_passage", "#0066cc"), ("entrance", "#e07b00")]:
        g = gpd.read_parquet(raw / "osm" / f"osm_{name}.parquet")
        g.representative_point().plot(ax=ax[2], color=col, markersize=2, label=f"{name} ({len(g)})")
    ax[2].legend(loc="lower right", markerscale=4)
    ax[2].set_title(f"OSM: bridges ({len(br_lines)} lines, purple) and covered / passage / entrance")
    for a in ax:
        a.set_axis_off()
    plt.tight_layout()
    plt.savefig(cfg["paths"]["figures"] / "phase_01_overview.png", dpi=110)


if __name__ == "__main__":
    main()
