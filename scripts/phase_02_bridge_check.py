"""Bridge-deck check (D-019) on the University City Schuylkill bridges; also city-wide counts."""

import json

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from shapely.geometry import box

from coolpath import load_config
from coolpath.acquire import vector
from coolpath.areas import city
from coolpath.network import bridge_deck, load_lines

VIEW = (-75.1890, 39.9445, -75.1720, 39.9600)   # Walnut, Chestnut, Market, JFK, South St bridges


def osm_bridges(cfg):
    br = gpd.read_parquet(cfg["paths"]["raw"] / "osm" / "osm_bridge.parquet")
    br = br[br.geom_type.isin(["LineString", "MultiLineString"])]
    if cfg["bridge_deck"]["osm_bridge_highway_only"]:
        br = br[br.highway.notna()]
    return br.reset_index(drop=True)


def classify(cfg, lines):
    bd = cfg["bridge_deck"]
    r = bridge_deck(lines, osm_bridges(cfg), bd["buffer_m"], bd["min_length_share"], bd["max_angle_deg"])
    out = lines.join(r)
    if bd["include_dvrpc_grade_separated"]:
        out["on_bridge"] |= out.feat_type.eq("GRADE_SEPARATED")
    near = out.bridge_idx >= 0
    out["cls"] = "other"
    out.loc[near & (out.bridge_angle >= bd["max_angle_deg"]), "cls"] = "crosses / under (angle ≥ 20°)"
    out.loc[near & (out.bridge_angle < bd["max_angle_deg"]) & ~out.on_bridge, "cls"] = "aligned, share < 80%"
    out.loc[out.on_bridge, "cls"] = "on bridge deck"
    return out


def main():
    cfg = load_config()
    crs = cfg["project"]["crs"]
    ped = vector.dvrpc_pedestrian(cfg)

    allc = classify(cfg, load_lines(ped, gpd.GeoSeries([city(cfg)], crs=crs), crs))
    view = gpd.GeoSeries([box(*VIEW)], crs=4326).to_crs(crs)
    v = classify(cfg, load_lines(ped, view, crs))
    res = {"city_edges": len(allc), "city_by_class": allc.cls.value_counts().to_dict(),
           "city_on_bridge_km": float(allc[allc.on_bridge].length.sum() / 1e3),
           "view_by_class": v.cls.value_counts().to_dict(), "params": cfg["bridge_deck"]}
    (cfg["paths"]["tables"] / "phase_02_bridge_check.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))

    colors = {"on bridge deck": "#e4002b", "aligned, share < 80%": "#ff9f1c",
              "crosses / under (angle ≥ 20°)": "#00b4d8", "other": "#ffffff"}
    fig, ax = plt.subplots(figsize=(13, 13))
    x0, y0, x1, y1 = view.total_bounds
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    cx.add_basemap(ax, crs=crs, source=cx.providers.Esri.WorldImagery, zoom=17)
    br = osm_bridges(cfg).clip(view.iloc[0])
    br.buffer(cfg["bridge_deck"]["buffer_m"]).plot(ax=ax, color="#ffd60a", alpha=0.25)
    br.plot(ax=ax, color="#ffd60a", lw=1, ls="--")
    for k, c in colors.items():
        s = v[v.cls == k]
        if len(s):
            s.plot(ax=ax, color=c, lw=0.8 if k == "other" else 2.4, alpha=0.6 if k == "other" else 1)
    handles = [Line2D([], [], color=c, lw=3, label=f"{k} ({(v.cls == k).sum()})") for k, c in colors.items()]
    handles.append(Line2D([], [], color="#ffd60a", ls="--", label="OSM highway bridge + 15 m buffer"))
    ax.legend(handles=handles, loc="lower left", fontsize=10, framealpha=0.9)
    ax.set_title("Bridge-deck rule (D-019): ≥ 80% of length within 15 m of an OSM bridge AND angle < 20°\n"
                 "Schuylkill bridges, University City – Center City", fontsize=12)
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(cfg["paths"]["figures"] / "phase_02_bridge_check.png", dpi=120)
    allc[["objectid", "bridge_share", "bridge_angle", "on_bridge", "cls"]].to_parquet(
        cfg["paths"]["interim"] / "dvrpc_bridge_flags.parquet")


if __name__ == "__main__":
    main()
