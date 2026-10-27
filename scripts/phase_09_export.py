"""Phase 9: export website data into site/app/data/.

    uv run python -m scripts.phase_09_export

- demo_network.json : University City demo network for the in-browser router (coolpath.export)
- demo_layers.json  : demo square, annotated buildings (PennCard), entrances
- hero.json         : precomputed walks for the home page: three station -> building walks (outdoor network,
                      opening animation) and one campus walk through buildings (indoor step)
- city_shade.pmtiles: every sidewalk edge with its shade share (%) at the 24 times (tippecanoe)
- grid.geojson, stations.geojson, summary.json : phase 7 layers and headline numbers
"""

import json
import subprocess

import geopandas as gpd
import numpy as np
import pandas as pd

from coolpath import ROOT, load_config
from coolpath.export import load_network_json, network_json, scenario_csr
from coolpath.routing import path_edges, search
from coolpath.transit import station_display
from scripts.phase_08_demo import demo_square, load_indoor

OUT = ROOT / "site" / "app" / "data"
CLUSTER_NAMES = {0: "Sunny, no cooler alternative", 1: "Sunny but avoidable", 2: "Cooler, few alternatives"}


def clean(o):
    """NaN / inf -> null (browsers reject them in JSON)."""
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def write(name, obj):
    p = OUT / name
    p.write_text(json.dumps(clean(obj), separators=(",", ":"), allow_nan=False))
    return round(p.stat().st_size / 1e6, 2)


def route(net, scenario, o, d, lab):
    """Shortest path on the outdoor network and shade path (dynamic θ) on the scenario network, as file edge
    indices (the same definitions as phase 8 and the route tool)."""
    go, so = scenario_csr(net, "outdoor")
    z = np.zeros(len(so))
    _, _, p0, e0 = search(go, net["len"][so], z, z, z, o, "shortest")
    g, sel = scenario_csr(net, scenario)
    t = net["times"].index(lab)
    ls, lt, lb, li = (net["L"][k][t][sel] for k in ("sun", "tree", "bldg", "indoor"))
    p = net["params"]
    tp = np.array([p["theta"][k] for k in ("a", "k", "x0", "b")], float)
    _, _, p1, e1 = search(g, net["len"][sel], ls, lt, lb, o, "shade", tp=tp, tree_factor=p["tree_factor"], l_indoor=li,
                          indoor_factor=p["indoor_factor"])
    return so[path_edges(p0, e0, d)], sel[path_edges(p1, e1, d)]


def chain(geom, u, v, idx, start):
    """Ordered lon/lat coordinates of a path (file edge indices from its start node)."""
    out, node = [], start
    for i in idx:
        c = np.asarray(geom[i]).reshape(-1, 2)
        if u[i] != node:
            c = c[::-1]
        node = v[i] if u[i] == node else u[i]
        out.extend(c.tolist() if not out else c[1:].tolist())
    return np.round(out, 6).tolist()


def totals(net, idx, lab):
    t = net["times"].index(lab)
    return {"length": round(float(net["len"][idx].sum()), 1),
            **{k: round(float(net["L"][k][t][idx].sum()), 1) for k in ("sun", "tree", "bldg", "indoor")}}


def main():
    cfg = load_config()
    proc, tabs = cfg["paths"]["processed"], cfg["paths"]["tables"]
    OUT.mkdir(parents=True, exist_ok=True)
    labels = list(pd.read_parquet(proc / "sun.parquet").label)
    sizes = {}

    # ---- demo network + layers
    e = gpd.read_parquet(proc / "demo_edges.parquet")
    tc = cfg["routing"]["theta_curve"]
    sizes["demo_network.json"] = write("demo_network.json", network_json(e, labels, cfg["routing"], tc))
    bld, ent, _ = load_indoor(cfg)
    sq = gpd.GeoSeries([demo_square(cfg)], crs=cfg["project"]["crs"]).to_crs(4326)
    b4 = bld.reset_index().to_crs(4326)
    b4["geometry"] = b4.geometry.simplify(1e-6)
    layers = {"demo": json.loads(sq.to_json(drop_id=True)),
              "buildings": json.loads(b4[["building_id", "name", "address", "needs_penncard", "geometry"]].to_json(drop_id=True)),
              "entrances": json.loads(ent.to_crs(4326)[["entrance_id", "building_id", "geometry"]].to_json(drop_id=True))}
    sizes["demo_layers.json"] = write("demo_layers.json", layers)

    # ---- hero (D-048). Opening: station -> building walks on the outdoor network at 06-21 15:00, 500-900 m, not
    # longer by more than 5%, largest sun reduction, one per line (the first three lines in that order); stored with
    # their rank among all station walks at that time. Indoor step: the campus walk with the largest sun reduction
    # among those that use indoor passages (all buildings open).
    net = load_network_json(OUT / "demo_network.json")
    m = pd.read_parquet(proc / "demo_trips_metrics.parquet").merge(
        pd.read_parquet(proc / "demo_trips.parquet")[["trip_id", "o", "d"]], on="trip_id")
    lab = "0621_15"
    geom = json.loads((OUT / "demo_network.json").read_text())["edges"]["geom"]

    def pair(row, scenario):
        short, cool = route(net, scenario, int(row.o), int(row.d), lab)
        return {"time": lab,
                "shortest": {"coords": chain(geom, net["u"], net["v"], short, int(row.o)), **totals(net, short, lab)},
                "coolest": {"coords": chain(geom, net["u"], net["v"], cool, int(row.o)), **totals(net, cool, lab),
                            "indoor_segs": [np.round(np.asarray(geom[i]).reshape(-1, 2), 6).tolist()
                                       for i in cool if net["kind"][i] == 2]}}

    allst = m[(m.set == "station") & (m.scenario == "outdoor") & (m.time == lab)]
    c = allst[allst.out_short_len.between(500, 900) & (allst.len_vs_outdoor < 0.05)].sort_values("sun_red_vs_outdoor", ascending=False)
    c = c.assign(station=c.trip_id.str[1:].str.split("|").str[0])
    c = c.assign(line=c.station.str.split(": ").str[0]).drop_duplicates("line").head(3)
    opener = []
    for row in c.itertuples():
        name, line = station_display(row.station)
        opener.append({**pair(row, "outdoor"), "station": name, "line": line,
                       "top_share": round(float((allst.sun_red_vs_outdoor >= row.sun_red_vs_outdoor).mean()), 4)})
    ci = m[(m.set == "campus") & (m.scenario == "all") & (m.time == lab) & m.shade_uses_indoor
           & m.out_short_len.between(500, 900) & (m.len_vs_outdoor < 0.05)].sort_values("sun_red_vs_outdoor", ascending=False)
    hero = {"opener": opener, "indoor": pair(ci.iloc[0], "all"), "station_walks": int(len(allst))}
    sizes["hero.json"] = write("hero.json", hero)

    # ---- city sidewalk shade -> PMTiles
    ge = gpd.read_parquet(proc / "graph_edges.parquet", columns=["edge_id", "geometry"])
    tab = pd.read_parquet(proc / "edges_shade.parquet")
    ge = ge.merge(tab[["edge_id", "length"] + [f"L_sun_{l}" for l in labels]], on="edge_id")
    for i, l in enumerate(labels):
        ge[f"s{i}"] = np.clip(np.round(100 * (1 - ge[f"L_sun_{l}"] / ge.length.clip(lower=1e-6))), 0, 100).astype(np.uint8)
    ge = ge[["edge_id"] + [f"s{i}" for i in range(len(labels))] + ["geometry"]].to_crs(4326)
    seq = cfg["paths"]["interim"] / "city_shade.geojsonl"
    seq.unlink(missing_ok=True)
    ge.to_file(seq, driver="GeoJSONSeq")
    pm = OUT / "city_shade.pmtiles"
    subprocess.run(["tippecanoe", "-o", str(pm), "--force", "-l", "sidewalks", "--minimum-zoom=13", "--maximum-zoom=16",
                    "--drop-densest-as-needed", "--no-tile-size-limit", "-q", str(seq)], check=True)
    sizes["city_shade.pmtiles"] = round(pm.stat().st_size / 1e6, 2)

    # ---- phase 7 layers
    g = gpd.read_parquet(proc / "grid_metrics.parquet").to_crs(4326)
    cb = cfg["metrics"]["cluster_length_bin"]
    keep = g[f"trips_{cb}"] >= cfg["metrics"]["min_trips_per_cell"]
    gg = g[keep].copy()
    out = gpd.GeoDataFrame({
        "cluster": gg.cluster.astype(int), "cluster_name": gg.cluster.astype(int).map(CLUSTER_NAMES),
        "trips": gg[f"trips_{cb}"].astype(int),
        **{k: gg[f"{k}_{cb}"].round(3) for k in ("sun_share", "sun_red", "detour", "no_alt_share", "exposure_red")},
        "bldg_height_m": gg.bldg_height_mean_m.round(1), "canopy": gg.canopy_cover.round(3),
        "network_km_km2": gg.network_km_per_km2.round(1)}, geometry=gg.geometry, crs=4326)
    sizes["grid.geojson"] = write("grid.geojson", json.loads(out.to_json(drop_id=True)))
    s = gpd.read_parquet(proc / "stations_metrics.parquet").to_crs(4326)
    wide = s.pivot_table(index="station", columns="length_bin", values=["sun_red", "sun_share", "no_alt_share", "trips"], observed=True)
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    geo = s.drop_duplicates("station").set_index("station").geometry
    st = gpd.GeoDataFrame(wide.round(3).reset_index(), geometry=geo.loc[wide.index].values, crs=4326)
    disp = st.station.map(station_display)
    st["name"], st["line"] = disp.str[0], disp.str[1]
    st["small"] = st["trips_300-800"].fillna(0) < cfg["metrics"]["min_trips_station_rank"]
    sizes["stations.geojson"] = write("stations.geojson", json.loads(st.to_json(drop_id=True)))
    s6, s7, s8 = (json.loads((tabs / n).read_text()) for n in ("phase_06_summary.json", "phase_07_stats.json", "phase_08_scenarios.json"))
    s4 = json.loads((tabs / "phase_04_stats.json").read_text())
    s6s = json.loads((tabs / "phase_06_stats.json").read_text())
    sizes["summary.json"] = write("summary.json", {
        "phase6_at_15h": s6["at_15h"], "phase7_by_mode_bin_15h": s7["by_mode_bin_15h"], "phase8_at_15h": s8["at_15h"],
        "sidewalk_km": s4["km"], "city_trips": s6s["city"]["trips"], "annotated_buildings": int(len(bld)),
        "min_trips_station_rank": cfg["metrics"]["min_trips_station_rank"]})
    (tabs / "phase_09_export.json").write_text(json.dumps(
        {"sizes_mb": sizes, "opener": [{k: h[k] for k in ("station", "line", "top_share")} for h in hero["opener"]]}, indent=2))
    print(json.dumps(sizes, indent=2))


if __name__ == "__main__":
    main()
