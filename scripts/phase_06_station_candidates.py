"""Phase 6, step 1 (CP1): SEPTA station features within 500 m and 8-10 contrasting candidates.

    uv run python -m scripts.phase_06_station_candidates

Pool: Regional Rail stations and Market-Frankford / Broad Street (incl. Broad-Ridge Spur)
subway stations inside the city. Features within station_buffer_m (500 m): mean building height
(over building pixels), canopy cover, building coverage, plus the 15:00 sun share of the
pedestrian network for reference. Candidates: farthest-point sampling in the standardised
3-feature space (seed 42), so the picks span the range of urban form.
"""

import json

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds

from coolpath import load_config
from coolpath.areas import city

SUBWAY = ["L1", "B1", "B2", "B3"]


def stations(cfg):
    raw, crs = cfg["paths"]["raw"] / "gtfs", cfg["project"]["crs"]
    rail = pd.read_csv(raw / "google_rail" / "stops.txt").assign(mode="Regional Rail")
    bs = pd.read_csv(raw / "google_bus" / "stops.txt", dtype={"stop_id": str})
    rs = pd.read_csv(raw / "google_bus" / "route_stops.txt", dtype={"stop_id": str})
    sub = bs[bs.stop_id.isin(rs[rs.route_id.isin(SUBWAY)].stop_id)].copy()
    lines = rs[rs.route_id.isin(SUBWAY)].groupby("stop_id").route_id.agg(lambda s: "/".join(sorted(set(s))))
    sub["mode"] = sub.stop_id.map(lines).map(lambda r: "MFL" if r == "L1" else "BSL")
    # one row per station: subway platforms of both directions share a name
    sub["key"] = sub.stop_name.str.replace(r"\s+-\s+(NB|SB|EB|WB|MFL|BSL|[BL]\d).*$", "", regex=True).str.strip()
    sub = sub.groupby(["key", "mode"]).agg(stop_lat=("stop_lat", "mean"), stop_lon=("stop_lon", "mean"),
                                           stop_ids=("stop_id", lambda s: list(s))).reset_index()
    sub = sub.rename(columns={"key": "stop_name"})
    rail["stop_ids"] = rail.stop_id.map(lambda s: [s])
    st = pd.concat([rail[["stop_name", "mode", "stop_lat", "stop_lon", "stop_ids"]],
                    sub[["stop_name", "mode", "stop_lat", "stop_lon", "stop_ids"]]], ignore_index=True)
    g = gpd.GeoDataFrame(st, geometry=gpd.points_from_xy(st.stop_lon, st.stop_lat), crs=4326).to_crs(crs)
    return g[g.within(city(cfg))].reset_index(drop=True)


MIN_NETWORK_KM = 2.0   # stations need sidewalks around them to sample trips (e.g. excludes the airport)


def features(cfg, g):
    proc, r = cfg["paths"]["processed"], cfg["trips"]["station_buffer_m"]
    pts = pd.read_parquet(proc / "shadow_points.parquet", columns=["x", "y", "seg_m", "s_0621_15"])
    pg = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts.x, pts.y), crs=g.crs)
    rows = []
    for _, s in g.iterrows():
        circ = s.geometry.buffer(r)
        b = circ.bounds
        with rasterio.open(proc / "surface_building.tif") as src:
            w = from_bounds(*b, transform=src.transform)
            hb = src.read(1, window=w) / 10
            tr = src.window_transform(w)
        with rasterio.open(proc / "surface_tree.tif") as src:
            ht = src.read(1, window=from_bounds(*b, transform=src.transform)) / 10
        inside = ~geometry_mask([circ], hb.shape, tr)
        bmask = (hb > 0) & inside
        near = pg.iloc[pg.sindex.query(circ, predicate="contains")]
        rows.append({"bldg_height_mean_m": float(hb[bmask].mean()) if bmask.any() else 0.0,
                     "canopy_cover": float(((ht > 0) & inside).sum() / inside.sum()),
                     "bldg_coverage": float(bmask.sum() / inside.sum()),
                     "sun_share_1500": float((near.seg_m * (near.s_0621_15 == 0)).sum() / near.seg_m.sum()) if len(near) else np.nan,
                     "network_km_500m": float(near.seg_m.sum() / 1e3)})
    return pd.concat([g.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


MIN_SPACING_M = 1500   # candidates at least this far apart (500 m buffers must not overlap much)


def farthest_points(X, xy, k):
    """Start at the station closest to the multivariate median, then repeatedly add the station
    farthest (in feature space) from those picked, among stations >= MIN_SPACING_M from all picks."""
    pick = [int(np.argmin(np.linalg.norm(X - np.median(X, 0), axis=1)))]
    while len(pick) < k:
        d = np.min(np.linalg.norm(X[:, None] - X[pick][None], axis=2), axis=1)
        sp = np.min(np.linalg.norm(xy[:, None] - xy[pick][None], axis=2), axis=1)
        d[sp < MIN_SPACING_M] = -1
        pick.append(int(d.argmax()))
    return pick


def main():
    cfg = load_config()
    feats = ["bldg_height_mean_m", "canopy_cover", "bldg_coverage"]
    g = features(cfg, stations(cfg))
    g["eligible"] = g.network_km_500m >= MIN_NETWORK_KM
    g = g[g.eligible].reset_index(drop=True)
    X = ((g[feats] - g[feats].mean()) / g[feats].std()).to_numpy()
    k = max(cfg["trips"]["station_n_sites"])
    pick = farthest_points(X, np.c_[g.geometry.x, g.geometry.y], k + 2)   # k recommended + 2 alternates
    g["candidate_rank"] = np.nan
    g.loc[pick, "candidate_rank"] = np.arange(1, k + 3)
    out = g.drop(columns="geometry").copy()
    out["stop_ids"] = out.stop_ids.map(lambda s: " ".join(map(str, s)))
    out.sort_values(["candidate_rank", "mode", "stop_name"]).to_csv(cfg["paths"]["tables"] / "phase_06_station_features.csv",
                                                                      index=False, float_format="%.3f")
    sel = {(d["name"], d["mode"]) for d in cfg["trips"]["station_sample"]}
    g["selected"] = [(n, m) in sel for n, m in zip(g.stop_name, g["mode"])]
    out["selected"] = g["selected"].to_numpy()
    out.to_csv(cfg["paths"]["tables"] / "phase_06_station_features.csv", index=False, float_format="%.3f")
    cand = out.dropna(subset=["candidate_rank"]).sort_values("candidate_rank")
    print(f"{len(g)} stations in the pool ({g['mode'].value_counts().to_dict()})")
    print(cand[["candidate_rank", "stop_name", "mode"] + feats + ["sun_share_1500", "network_km_500m"]].round(3).to_string(index=False))
    summ = {"pool": int(len(g)), "pool_by_mode": g["mode"].value_counts().to_dict(),
            "pool_feature_ranges": g[feats + ["sun_share_1500"]].describe().loc[["min", "50%", "max"]].round(3).to_dict()}
    (cfg["paths"]["tables"] / "phase_06_station_candidates.json").write_text(json.dumps(summ, indent=2))

    plot(cfg)


def plot(cfg):
    """Map + feature-space figure from the saved features CSV; numbering follows the CP1 list."""
    g = pd.read_csv(cfg["paths"]["tables"] / "phase_06_station_features.csv")
    g = gpd.GeoDataFrame(g, geometry=gpd.points_from_xy(g.stop_lon, g.stop_lat), crs=4326).to_crs(cfg["project"]["crs"])
    order = {(d["name"], d["mode"]): i + 1 for i, d in enumerate(cfg["trips"]["station_sample"])}
    g["n"] = [order.get((a, b)) for a, b in zip(g.stop_name, g["mode"])]
    c = g[g.n.notna()].sort_values("n")
    fig, ax = plt.subplots(1, 2, figsize=(20, 10))
    gpd.GeoSeries([city(cfg)], crs=g.crs).boundary.plot(ax=ax[0], color="k", lw=0.8)
    for m, col in (("Regional Rail", "#2a9d3a"), ("MFL", "#1f77b4"), ("BSL", "#ff7f0e")):
        g[g["mode"] == m].plot(ax=ax[0], color=col, markersize=14, alpha=0.7, label=m)
    c.plot(ax=ax[0], color="red", markersize=160, marker="*", label="station sample (CP1)")
    for _, r in c.iterrows():
        ax[0].annotate(f"{int(r.n)} {r.stop_name}", (r.geometry.x, r.geometry.y), fontsize=10, fontweight="bold",
                       xytext=(5, 5), textcoords="offset points",
                       bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
    cx.add_basemap(ax[0], crs=g.crs, source=cx.providers.Esri.WorldGrayCanvas, zoom=12)
    ax[0].legend(loc="lower right")
    ax[0].set_axis_off()
    ax[0].set_title("SEPTA rail + subway stations in the city; red stars = station sample (CP1)")
    sc = ax[1].scatter(g.bldg_height_mean_m, g.canopy_cover, c=g.bldg_coverage, cmap="viridis", s=25, alpha=0.6)
    ax[1].scatter(c.bldg_height_mean_m, c.canopy_cover, s=170, facecolors="none", edgecolors="red", lw=2)
    for _, r in c.iterrows():
        ax[1].annotate(f"{int(r.n)} {r.stop_name}", (r.bldg_height_mean_m, r.canopy_cover), fontsize=9, color="red",
                       xytext=(6, 3), textcoords="offset points")
    ax[1].set_xscale("log")
    ax[1].set(xlabel="mean building height within 500 m (m, log)", ylabel="canopy cover within 500 m")
    plt.colorbar(sc, ax=ax[1], label="building coverage within 500 m")
    ax[1].set_title("Station feature space (station sample circled)")
    plt.tight_layout()
    plt.savefig(cfg["paths"]["figures"] / "phase_06_station_candidates.png", dpi=100)


if __name__ == "__main__":
    import sys
    plot(load_config()) if sys.argv[1:] == ["plot"] else main()
