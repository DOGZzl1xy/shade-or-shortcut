"""Bridge-deck check on the phase-3 network (split at bridge-buffer edges, D-027 + D-019)."""

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from shapely.geometry import box

from coolpath import load_config
from scripts.phase_02_bridge_check import VIEW, osm_bridges


def main():
    cfg = load_config()
    crs, bd = cfg["project"]["crs"], cfg["bridge_deck"]
    e = gpd.read_parquet(cfg["paths"]["processed"] / "network_edges.parquet")
    view = gpd.GeoSeries([box(*VIEW)], crs=4326).to_crs(crs)
    v = e[e.intersects(view.iloc[0])].copy()
    near = v.bridge_idx >= 0
    v["cls"] = "other"
    v.loc[near & (v.bridge_angle >= bd["max_angle_deg"]), "cls"] = "crosses / under (angle ≥ 20°)"
    v.loc[near & (v.bridge_angle < bd["max_angle_deg"]) & ~v.on_bridge, "cls"] = "aligned, share < 80%"
    v.loc[v.on_bridge, "cls"] = "on bridge deck"
    colors = {"on bridge deck": "#e4002b", "aligned, share < 80%": "#ff9f1c",
              "crosses / under (angle ≥ 20°)": "#00b4d8", "other": "#ffffff"}
    fig, ax = plt.subplots(figsize=(13, 13))
    x0, y0, x1, y1 = view.total_bounds
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    cx.add_basemap(ax, crs=crs, source=cx.providers.Esri.WorldImagery, zoom=17)
    br = osm_bridges(cfg).clip(view.iloc[0])
    br.buffer(bd["buffer_m"]).plot(ax=ax, color="#ffd60a", alpha=0.25)
    br.plot(ax=ax, color="#ffd60a", lw=1, ls="--")
    for k, c in colors.items():
        s = v[v.cls == k]
        if len(s):
            s.plot(ax=ax, color=c, lw=0.8 if k == "other" else 2.4, alpha=0.6 if k == "other" else 1)
    ends = v[v.on_bridge].boundary.explode(index_parts=False)
    ends.plot(ax=ax, color="white", markersize=6, zorder=5)
    handles = [Line2D([], [], color=c, lw=3, label=f"{k} ({(v.cls == k).sum()})") for k, c in colors.items()]
    handles.append(Line2D([], [], color="#ffd60a", ls="--", label="OSM highway bridge + 15 m buffer"))
    handles.append(Line2D([], [], color="white", marker="o", ls="", label="ends of on-deck segments"))
    ax.legend(handles=handles, loc="lower left", fontsize=10, framealpha=0.9)
    ax.set_title("Phase-3 network: segments split at bridge-buffer edges (D-027), then D-019\n"
                 "Schuylkill bridges, University City – Center City", fontsize=12)
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(cfg["paths"]["figures"] / "phase_03_bridge_check.png", dpi=120)
    print(v.cls.value_counts().to_dict())


if __name__ == "__main__":
    main()
