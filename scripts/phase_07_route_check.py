"""Route check for the 5 rapid-transit stations with the highest sun reduction (300-800 m, 15:00).

    uv run python -m scripts.phase_07_route_check

For each station: all city-sample trips of 300-800 m are re-routed at 06-21 15:00 (dynamic θ).
Figure (dark, in the website's route style: shortest = orange solid, coolest = cyan dashed), left: one
representative trip (a changed path whose sun reduction is closest to the station mean) on the 2025
PASDA orthophoto. Right: all trips — edges used only by shortest paths vs only by shade paths, width = trips.
Table: where the extra shade on the shade paths comes from — building vs tree shade gained, and
the share of the shade-only edge length lying under a building or structure pixel (building_source
1-2 = footprint, 3 = structure outside footprints such as the El viaduct; D-025), and the share
within 15 m of a structure pixel (sidewalks beside the El). The orthophoto uses the image-only
layer (show:3), without the service's tile-grid lines.
"""

import json

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import shapely
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from rasterio.windows import from_bounds
from scipy.ndimage import maximum_filter

from coolpath import load_config
from coolpath.routing import load_city_graph, path_edges, search, theta_params
from scripts.phase_02_spotcheck import ortho

LABEL, BIN, TOP = "0621_15", "300-800", 5
NEAR_M = 15        # "next to a structure": within 15 m of a building_source = 3 pixel


SUN, COOL, BG, INK, DIM = "#ff8a3d", "#35d0e0", "#0d0f14", "#e8e6df", "#8b8a84"   # website colours
DASH = (0, (2.2, 1.6))


def route_lines(ax, geoms, color, lw, dashed=False, label=None):
    """A route as on the website: a dark casing, then the line (solid or dashed)."""
    segs = segments(geoms)
    lw = np.broadcast_to(np.asarray(lw, float), (len(segs),))
    ax.add_collection(LineCollection(segs, colors=BG, lw=lw + 3, alpha=0.9))
    ax.add_collection(LineCollection(segs, colors=color, lw=lw, linestyles=DASH if dashed else "solid",
                                     capstyle="butt" if dashed else "round", label=label))


def segments(geoms):
    return [np.asarray(g.coords)[:, :2] for g in geoms]


def main():
    cfg = load_config()
    proc, figs, rep = cfg["paths"]["processed"], cfg["paths"]["figures"], cfg["paths"]["tables"]
    st = pd.read_csv(rep / "phase_07_rapid_stations.csv")
    top = st[st.length_bin == BIN].nlargest(TOP, "sun_red").station.tolist()
    g, L, shade = load_city_graph(cfg)
    ls, lt, lb = shade[LABEL]
    tp, tf = theta_params(cfg), cfg["routing"]["tree_factor"]
    fac = cfg["routing"]["search_radius_factor"]
    zero = np.zeros_like(L)
    e = gpd.read_parquet(proc / "graph_edges.parquet", columns=["edge_id", "geometry"]).set_index("edge_id").loc[g.edge_ids]
    geom = e.geometry.to_numpy()
    par = pd.read_parquet(proc / "trips_city_parcels.parquet")
    par["trip_id"] = "P" + par.index.astype(str)
    m = pd.read_parquet(proc / "trips_metrics.parquet",
                        columns=["trip_id", "station", "length_bin", "time", "theta", "sun_red", "same_path"])
    m = m[(m.time == LABEL) & (m.theta == "dynamic") & (m.length_bin == BIN) & m.station.isin(top)]
    m = m.merge(par[["trip_id", "node", "stop_node", "stop_dist"]], on="trip_id")
    bsrc = rasterio.open(proc / "building_source.tif")
    out = []
    for name in top:
        t = m[m.station == name]
        use_s = np.zeros(L.size)
        use_c = np.zeros(L.size)
        paths = {}
        for sn, grp in t.groupby("stop_node"):
            rad = grp.stop_dist.max() * fac + 1
            _, _, p0, e0 = search(g, L, zero, zero, zero, int(sn), "shortest", max_dist=rad)
            _, _, p1, e1 = search(g, L, ls, lt, lb, int(sn), "shade", tp=tp, tree_factor=tf, max_dist=rad)
            for tid, k in zip(grp.trip_id, grp.node):
                a, c = path_edges(p0, e0, int(k)), path_edges(p1, e1, int(k))
                np.add.at(use_s, a, 1)
                np.add.at(use_c, c, 1)
                paths[tid] = (a, c)
        only_c = np.where((use_c > 0) & (use_s == 0))[0]
        only_s = np.where((use_s > 0) & (use_c == 0))[0]
        # sources of the shade gained, summed over trips
        A = np.concatenate([p[0] for p in paths.values()])
        C = np.concatenate([p[1] for p in paths.values()])
        gain_b, gain_t = lb[C].sum() - lb[A].sum(), lt[C].sum() - lt[A].sum()
        # pixels under the shade-only edges (2 m samples)
        pts = [ln.interpolate(np.arange(0, ln.length, 2.0)) for ln in geom[only_c]]
        pts = np.concatenate([np.atleast_1d(p) for p in pts]) if pts else np.array([])
        vals = np.array([v[0] for v in bsrc.sample([(p.x, p.y) for p in pts])]) if len(pts) else np.array([])
        near_struct = np.nan
        if len(pts):
            xy = np.array([(p.x, p.y) for p in pts])
            w = from_bounds(xy[:, 0].min() - 50, xy[:, 1].min() - 50, xy[:, 0].max() + 50, xy[:, 1].max() + 50, bsrc.transform)
            sm = maximum_filter(bsrc.read(1, window=w) == 3, size=2 * NEAR_M + 1)
            r_, c_ = rasterio.transform.rowcol(bsrc.window_transform(w), xy[:, 0], xy[:, 1])
            near_struct = float(sm[np.clip(r_, 0, sm.shape[0] - 1), np.clip(c_, 0, sm.shape[1] - 1)].mean())
        rec = {"station": name, "trips": int(t.trip_id.nunique()), "sun_red_mean": float(t.sun_red.mean()),
               "changed_share": float(1 - t.same_path.mean()),
               "bldg_shade_gained_m_per_trip": float(gain_b / len(paths)), "tree_shade_gained_m_per_trip": float(gain_t / len(paths)),
               "shade_only_edges_km": float(L[only_c].sum() / 1e3),
               "shade_only_under_footprint_share": float(np.isin(vals, [1, 2]).mean()) if vals.size else 0.0,
               "shade_only_under_structure_share": float((vals == 3).mean()) if vals.size else 0.0,
               "shade_only_within_15m_of_structure_share": near_struct,
               "shade_only_bldg_shade_share": float(lb[only_c].sum() / max(L[only_c].sum(), 1e-9)),
               "short_only_sun_share": float(ls[only_s].sum() / max(L[only_s].sum(), 1e-9))}
        out.append(rec)

        # representative trip
        ch = t[~t.same_path]
        r = ch.iloc[(ch.sun_red - t.sun_red.mean()).abs().argsort().iloc[0]]
        a, c = paths[r.trip_id]
        allg = shapely.union_all(np.r_[geom[a], geom[c]])
        x0, y0, x1, y1 = allg.buffer(60).bounds
        h = max(x1 - x0, y1 - y0) / 2
        cx_, cy_ = (x0 + x1) / 2, (y0 + y1) / 2
        view = (cx_ - h, cy_ - h, cx_ + h, cy_ + h)
        fig, ax = plt.subplots(1, 2, figsize=(20, 11.5), facecolor=BG)
        ax[0].imshow(ortho(2025, view, 900, layers="show:3"), extent=(view[0], view[2], view[1], view[3]))
        route_lines(ax[0], geom[a], SUN, 4.5)
        route_lines(ax[0], geom[c], COOL, 4.5, dashed=True)
        o = np.array([g.x[int(r.stop_node)], g.y[int(r.stop_node)]])
        d = np.array([g.x[int(r.node)], g.y[int(r.node)]])
        ax[0].scatter(*o, s=120, c="white", edgecolors="k", zorder=5, marker="s")
        ax[0].scatter(*d, s=120, c="white", edgecolors="k", zorder=5, marker="o")
        ax[0].set_xlim(view[0], view[2])
        ax[0].set_ylim(view[1], view[3])
        ax[0].set_axis_off()
        ax[0].legend(handles=[Line2D([], [], color=SUN, lw=4, label=f"shortest: {L[a].sum():.0f} m, {ls[a].sum():.0f} m in sun"),
                              Line2D([], [], color=COOL, lw=4, ls=DASH, label=f"coolest: {L[c].sum():.0f} m, {ls[c].sum():.0f} m in sun"),
                              Line2D([], [], color="white", marker="s", ls="", mec="k", ms=9, label="station"),
                              Line2D([], [], color="white", marker="o", ls="", mec="k", ms=9, label="home")],
                     loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=2, fontsize=19, facecolor=BG, edgecolor=DIM, labelcolor=INK, framealpha=0.85)
        ax[0].set_title(f"{name.split(': ')[1].replace('-', '–')}: one walk home, June 21, 15:00", fontsize=21, color=INK)
        b = shapely.union_all(geom[np.r_[only_c, only_s, np.where(use_c > 0)[0]]]).buffer(80).bounds
        near = np.where(shapely.intersects(geom, shapely.box(*b)))[0]
        ax[1].set_facecolor(BG)
        ax[1].add_collection(LineCollection(segments(geom[near]), colors="#3a3d45", lw=0.7))
        common = np.where((use_s > 0) & (use_c > 0))[0]
        umax = np.maximum(use_s, use_c).max()
        width = lambda idx: 0.8 + 4 * np.sqrt(np.maximum(use_s, use_c)[idx] / umax)
        ax[1].add_collection(LineCollection(segments(geom[common]), colors="#9a988f", lw=width(common), label="both"))
        route_lines(ax[1], geom[only_s], SUN, width(only_s), label="shortest walks only")
        route_lines(ax[1], geom[only_c], COOL, width(only_c), dashed=True, label="coolest walks only")
        ax[1].scatter(g.x[t.stop_node.unique()], g.y[t.stop_node.unique()], s=80, c="white", edgecolors="k", marker="s", zorder=5,
                      label="station")
        ax[1].set_xlim(b[0], b[2])
        ax[1].set_ylim(b[1], b[3])
        ax[1].set_aspect("equal")
        ax[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=2, fontsize=19, facecolor=BG, edgecolor=DIM, labelcolor=INK, framealpha=0.85)
        ax[1].set_axis_off()
        ax[1].set_title(f"All {len(paths)} walks of 300–800 m (line width = number of walks)", fontsize=21, color=INK)
        plt.tight_layout()
        slug = name.split(": ")[1].lower().replace(" ", "_").replace("/", "_")
        plt.savefig(figs / f"phase_07_route_{slug}.png", dpi=90, facecolor=BG)
        plt.close(fig)
        print(rec, flush=True)
    (rep / "phase_07_route_check.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
