"""Diagnostic (before choosing the city-sample design): how far are residential parcels from
"home stops" under three stop definitions?

    uv run python -m scripts.diag_home_stops

Frequency: SEPTA GTFS (fall 2026 timetable), one representative weekday (REP_DATE; calendar +
calendar_dates exceptions), departures between 15:00 and 19:00, per stop x route x direction,
averaged per hour. A stop is high-frequency if ANY single route-direction reaches the threshold
(routes are not added together).
Definitions:
  1. rapid transit only: Regional Rail, MFL (L1), BSL (B1-B3), trolley-tunnel stations
  2. rapid transit + surface stops with >= 4 departures / hour
  3. rapid transit + surface stops with >= 6 departures / hour
Every residential OPA parcel is snapped to the nearest graph node and assigned its network-nearest
stop (multi-source Dijkstra on the phase-4 graph, largest component).
GTFS helpers are shared with the city sample (coolpath.transit, D-038).
"""

import json
from datetime import date

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from coolpath import load_config
from coolpath.areas import city
from coolpath.routing import load_city_graph, nearest_source
from coolpath.transit import SUBWAY, TUNNEL, active_services, frequencies
from scripts.phase_06_trips import snap

REP_DATE = date(2026, 10, 28)          # a Wednesday valid in both feeds, no holiday nearby
WINDOW = ("15:00:00", "19:00:00")
THRESHOLDS = (4, 6)
WITHIN_M = 1200


def main():
    cfg = load_config()
    raw, crs = cfg["paths"]["raw"] / "gtfs", cfg["project"]["crs"]
    res = {"gtfs": {}, "rep_date": str(REP_DATE), "window": WINDOW}
    for feed in ("google_bus", "google_rail"):
        fi = pd.read_csv(raw / feed / "feed_info.txt")
        res["gtfs"][feed] = {"start": str(fi.feed_start_date[0]), "end": str(fi.feed_end_date[0]),
                             "version": str(fi.feed_version[0])}
    fb = frequencies(raw / "google_bus", REP_DATE, WINDOW)
    trips = pd.read_csv(raw / "google_bus" / "trips.txt", dtype={"service_id": str}, usecols=["service_id"])
    res["active_bus_trips_rep_date"] = int(trips.service_id.isin(active_services(raw / "google_bus", REP_DATE)).sum())
    routes = pd.read_csv(raw / "google_bus" / "routes.txt", dtype={"route_id": str})
    fb = fb.merge(routes[["route_id", "route_type"]], on="route_id")
    stops = pd.read_csv(raw / "google_bus" / "stops.txt", dtype={"stop_id": str})
    stops = stops[stops.location_type.fillna(0).astype(int) == 0]
    rs = pd.read_csv(raw / "google_bus" / "route_stops.txt", dtype={"stop_id": str, "route_id": str})

    # rapid transit: rail stations, subway platforms, trolley-tunnel stations
    rail = pd.read_csv(raw / "google_rail" / "stops.txt", dtype={"stop_id": str}).assign(kind="Regional Rail")
    sub_ids = set(rs[rs.route_id.isin(SUBWAY)].stop_id)
    trolley_ids = set(rs[rs.route_id.str.match(r"T\d")].stop_id)
    sub = stops[stops.stop_id.isin(sub_ids)].assign(kind="MFL / BSL")
    tun = stops[stops.stop_id.isin(trolley_ids) & stops.stop_name.isin(TUNNEL) & ~stops.stop_id.isin(sub_ids)]
    tun = tun.assign(kind="trolley tunnel")
    rapid = pd.concat([rail, sub, tun], ignore_index=True)[["stop_id", "stop_name", "stop_lat", "stop_lon", "kind"]]

    # surface stops: max over route-direction of departures / hour (rapid routes excluded)
    surf = fb[~fb.route_id.isin(SUBWAY) & ~fb.stop_id.isin(set(rapid.stop_id))]
    best = surf.groupby("stop_id").per_hour.max()
    surface = stops[stops.stop_id.isin(best.index)][["stop_id", "stop_name", "stop_lat", "stop_lon"]].copy()
    surface["max_route_dir_per_hour"] = surface.stop_id.map(best)
    surface["kind"] = "surface"

    def gdf(d):
        g = gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.stop_lon, d.stop_lat), crs=4326).to_crs(crs)
        return g[g.within(city(cfg).buffer(200))].reset_index(drop=True)

    rapid, surface = gdf(rapid), gdf(surface)
    defs = {"1_rapid_only": rapid,
            "2_rapid_plus_ge4": pd.concat([rapid, surface[surface.max_route_dir_per_hour >= THRESHOLDS[0]]], ignore_index=True),
            "3_rapid_plus_ge6": pd.concat([rapid, surface[surface.max_route_dir_per_hour >= THRESHOLDS[1]]], ignore_index=True)}
    res["stops"] = {"rapid_by_kind": rapid.kind.value_counts().to_dict(), "surface_in_city": int(len(surface)),
                    "surface_ge4": int((surface.max_route_dir_per_hour >= 4).sum()),
                    "surface_ge6": int((surface.max_route_dir_per_hour >= 6).sum()),
                    "surface_per_hour_quantiles": surface.max_route_dir_per_hour.quantile([.25, .5, .75, .9]).round(2).to_dict()}

    # all residential parcels
    g, L, _ = load_city_graph(cfg)
    opa = gpd.read_parquet(cfg["paths"]["raw"] / "opa" / "opa_properties.parquet")
    opa = opa[opa.category_code.isin(cfg["sources"]["opa_residential_categories"]) & opa.geometry.notna()]
    opa = opa[~opa.geometry.is_empty & opa.within(city(cfg))]
    p_node, p_snap = snap(g, opa.geometry.x.to_numpy(), opa.geometry.y.to_numpy())
    res["parcels"] = {"n": int(len(opa)), "snap_m_median": float(np.median(p_snap)), "snap_m_p90": float(np.quantile(p_snap, 0.9))}
    out = {}
    for name, s in defs.items():
        s_node, s_snap = snap(g, s.geometry.x.to_numpy(), s.geometry.y.to_numpy())
        dist, _ = nearest_source(g, L, s_node, 20000)
        d = dist[p_node]
        fin = np.isfinite(d)
        out[name] = {"stops": int(len(s)), "stop_snap_m_median": float(np.median(s_snap)),
                     "parcels_reached_20km": float(fin.mean()),
                     "median_m": float(np.median(d[fin])), "q25_m": float(np.quantile(d[fin], 0.25)),
                     "q75_m": float(np.quantile(d[fin], 0.75)), "p90_m": float(np.quantile(d[fin], 0.90)),
                     "share_within_1200m": float((d <= WITHIN_M).mean())}
        opa[f"d_{name}"] = d
    # current design (all stops, phase 6) for reference
    all_st = pd.concat([rapid, surface], ignore_index=True)
    s_node, _ = snap(g, all_st.geometry.x.to_numpy(), all_st.geometry.y.to_numpy())
    dist, _ = nearest_source(g, L, s_node, 20000)
    d = dist[p_node]
    out["0_all_stops_with_service_on_rep_date"] = {"stops": int(len(all_st)), "median_m": float(np.nanmedian(d)),
                                                    "q25_m": float(np.nanquantile(d, .25)), "q75_m": float(np.nanquantile(d, .75)),
                                                    "p90_m": float(np.nanquantile(d, .9)), "share_within_1200m": float((d <= WITHIN_M).mean())}
    res["definitions"] = out
    (cfg["paths"]["tables"] / "diag_home_stops.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps(res, indent=2, default=float))

    # map: stops under the three definitions + parcels farther than 1.2 km
    fig, ax = plt.subplots(1, 3, figsize=(24, 9))
    cb = gpd.GeoSeries([city(cfg)], crs=crs)
    par = opa.sample(min(len(opa), 60000), random_state=cfg["project"]["seed"])
    for a, (name, s) in zip(ax, defs.items()):
        far = par[par[f"d_{name}"] > WITHIN_M]
        far.plot(ax=a, color="#d62728", markersize=0.3, alpha=0.4)
        cb.boundary.plot(ax=a, color="k", lw=0.7)
        surf_s = s[s.kind == "surface"]
        if len(surf_s):
            surf_s.plot(ax=a, color="#1f77b4", markersize=3, label=f"high-frequency surface stops ({len(surf_s)})")
        for k, col in (("Regional Rail", "#2a9d3a"), ("MFL / BSL", "#ff7f0e"), ("trolley tunnel", "#9467bd")):
            ss = s[s.kind == k]
            ss.plot(ax=a, color=col, markersize=18, label=f"{k} ({len(ss)})")
        cx.add_basemap(a, crs=crs, source=cx.providers.Esri.WorldGrayCanvas, zoom=11)
        o = out[name]
        a.set_title(f"{name.replace('_', ' ')}: {o['stops']} stops\nnetwork distance median {o['median_m']:.0f} m, "
                    f"P90 {o['p90_m']:.0f} m; within 1.2 km {o['share_within_1200m']:.0%}\n"
                    "red dots = residential parcels > 1.2 km (60k sample)", fontsize=10)
        a.legend(loc="lower right", fontsize=8, markerscale=1.5)
        a.set_axis_off()
    fig.suptitle(f"Home-stop definitions — SEPTA GTFS fall 2026 timetable ({res['gtfs']['google_bus']['version']}), "
                 f"weekday {REP_DATE} 15:00–19:00, max departures/hour of any single route-direction", fontsize=12, y=0.995)
    plt.tight_layout(rect=(0, 0, 1, 0.94))
    plt.savefig(cfg["paths"]["figures"] / "diag_home_stops.png", dpi=100)


if __name__ == "__main__":
    main()
