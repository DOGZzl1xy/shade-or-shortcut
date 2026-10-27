"""Phase 3 QA: shade shares by time / edge kind, bridge decks vs underpasses, phase-0 comparison,
maps at 06-21 15:00."""

import json

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from coolpath import load_config
from coolpath.areas import city, test_area

COLORS = ["#f2b705", "#2a9d3a", "#2b2d7a"]


def main():
    cfg = load_config()
    proc, figs = cfg["paths"]["processed"], cfg["paths"]["figures"]
    crs = cfg["project"]["crs"]
    pts = pd.read_parquet(proc / "shadow_points.parquet")
    sun = pd.read_parquet(proc / "sun.parquet")
    cols = [f"s_{l}" for l in sun.label]
    S = pts[cols].to_numpy()
    edges = gpd.read_parquet(proc / "network_edges.parquet")
    kind = edges.set_index("edge_id").kind.reindex(pts.edge_id).to_numpy()
    w = pts.seg_m.to_numpy()
    res = {"points": len(pts), "network_km": float(w.sum() / 1e3)}

    def shares(mask):
        ww = w[mask][:, None]
        return {"sun": (ww * (S[mask] == 0)).sum(0) / ww.sum(), "tree": (ww * (S[mask] == 1)).sum(0) / ww.sum(),
                "solid": (ww * (S[mask] == 2)).sum(0) / ww.sum()}

    allc = shares(np.ones(len(pts), bool))
    by_time = pd.DataFrame({"label": sun.label, "elev": sun.elevation.round(1), **{k: v.round(4) for k, v in allc.items()}})
    res["length_weighted_share_by_time"] = by_time.to_dict("records")
    i15 = int(np.where(sun.label == "0621_15")[0][0])
    res["by_kind_0621_15"] = {k: {kk: float(vv[i15]) for kk, vv in shares(kind == k).items()}
                              for k in ("sidewalk", "crosswalk", "other")}
    res["daily_mean"] = {k: float(v.mean()) for k, v in allc.items()}

    # bridge decks vs points under a highway bridge (within 15 m buffer, not on deck)
    from scripts.phase_03_shadow import osm_highway_bridges
    br = osm_highway_bridges(cfg)
    buf = br.buffer(cfg["bridge_deck"]["buffer_m"]).union_all()
    p = gpd.GeoSeries(gpd.points_from_xy(pts.x, pts.y), crs=crs)
    near = p.within(buf).to_numpy()
    on = pts.on_bridge.to_numpy()
    res["bridge"] = {"deck_points": int(on.sum()), "deck_sun_share_15h": float(shares(on)["sun"][i15]),
                     "deck_solid_share_15h": float(shares(on)["solid"][i15]),
                     "under_or_beside_points": int((near & ~on).sum()),
                     "under_or_beside_solid_share_15h": float(shares(near & ~on)["solid"][i15])}

    # phase-0 comparison on the test area at 06-21 15:00 (different surfaces: phase 0 had no
    # unmapped structures and the plain LiDAR tree fallback)
    ta = test_area(cfg).iloc[0]
    in_ta = p.within(ta).to_numpy()
    s_ta = shares(in_ta)
    res["test_area_0621_15"] = {"phase3": {k: float(v[i15]) for k, v in s_ta.items()},
                                "phase0": {"sun": 0.603, "tree": 0.229, "building": 0.168}}
    (cfg["paths"]["tables"] / "phase_03_qa.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps({k: v for k, v in res.items() if k != "length_weighted_share_by_time"}, indent=2, default=float))
    print(by_time.to_string())

    # stacked shares by time
    fig, ax = plt.subplots(figsize=(11, 4.5))
    x = np.arange(len(sun))
    ax.stackplot(x, allc["sun"], allc["tree"], allc["solid"], colors=COLORS,
                 labels=["sun", "tree shade", "building / solid shade"])
    ax.set_xticks(x, sun.label, rotation=90, fontsize=8)
    ax.set(ylabel="share of network length", title="Philadelphia pedestrian network: shade by time (length-weighted)")
    ax.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(figs / "phase_03_by_time.png", dpi=110)

    # city map: per-edge shaded share at 06-21 15:00
    e = pd.DataFrame({"edge_id": pts.edge_id, "w": w, "shade": w * (S[:, i15] > 0)}).groupby("edge_id").sum()
    edges = edges.set_index("edge_id").join(e)
    edges["shade_share"] = edges.shade / edges.w
    fig, ax = plt.subplots(figsize=(13, 13))
    edges.plot(ax=ax, column="shade_share", cmap="cividis_r", lw=0.25, vmin=0, vmax=1, legend=True,
               legend_kwds={"shrink": 0.5, "label": "shaded share of edge length"})
    gpd.GeoSeries([city(cfg)], crs=crs).boundary.plot(ax=ax, color="k", lw=0.6)
    ax.set_title("Shaded share per sidewalk / crosswalk edge, 2026-06-21 15:00")
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_03_city_1500.png", dpi=110)

    # University City zoom: points on imagery
    x0, y0, x1, y1 = 482900, 4422000, 484300, 4423100
    z = pts[(pts.x > x0) & (pts.x < x1) & (pts.y > y0) & (pts.y < y1)]
    fig, ax = plt.subplots(figsize=(14, 11))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    cx.add_basemap(ax, crs=crs, source=cx.providers.Esri.WorldImagery, zoom=17)
    ax.scatter(z.x, z.y, c=np.array(COLORS)[z[cols[i15]].to_numpy()], s=2)
    ax.set_title(f"Sample points at 06-21 15:00 (sun az {sun.azimuth[i15]:.0f}°, elev {sun.elevation[i15]:.0f}°): "
                 "yellow sun, green tree, blue building/solid")
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_03_ucity_1500.png", dpi=110)


if __name__ == "__main__":
    main()
