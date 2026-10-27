"""T5: shadow casting — synthetic check, test-area run (2 m points x 24 times), timing, 15:00 map."""

import json
import pickle
import time

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap

from coolpath import load_config
from coolpath.acquire import vector
from coolpath.areas import test_area
from coolpath.shadow import BUILDING, TREE, cast, local_max, sample_points
from coolpath.sun import sun_table
from coolpath.surface import read_dm

from tests.test_shadow import H, shadow_length


def main():
    cfg = load_config()
    sc = cfg["shadow"]
    figs, out = cfg["paths"]["figures"], cfg["paths"]["interim"] / "test"

    # 1) synthetic block
    syn = []
    for obs in (0.0, 1.5):
        for el in (10, 15, 20, 30, 45, 60, 70):
            for az in (90.0, 180.0):
                L = shadow_length(az, float(el), obs)
                exp = (H - obs) / np.tan(np.radians(el))
                syn.append({"obs_h": obs, "elev": el, "az": az, "L": L, "analytic": exp, "err": L - exp})
    syn = pd.DataFrame(syn)
    print(syn.to_string())

    # 2) rasters and sample points
    with rasterio.open(out / "dem.tif") as src:
        dem = src.read(1).astype(np.float32)
        tr = src.transform
    hb = read_dm(out / "surface_building.tif")
    ht = read_dm(out / "surface_tree_lidar.tif")
    x0, y1, res = tr.c, tr.f, tr.a
    t0 = time.time()
    lmax = local_max(dem + np.maximum(hb, ht), res, sc["localmax_block_m"], sc["max_ray_m"])
    t_lmax = time.time() - t0
    with open(out / "graph_test.pkl", "rb") as f:
        G = pickle.load(f)
    edges = ox.graph_to_gdfs(ox.convert.to_undirected(G), nodes=False).reset_index()
    px, py, eid, seg = sample_points(edges, sc["sample_spacing_m"])
    sun = sun_table(cfg)
    az, el = sun.azimuth.to_numpy(), sun.elevation.to_numpy()
    print(f"{px.size} sample points, {len(sun)} times")

    args = (dem, hb, ht, lmax, x0, y1, res)
    cast(px[:10], py[:10], *args, az, el, sc["observer_height_m"], float(sc["max_ray_m"]), 1.0)  # JIT
    t0 = time.time()
    st = cast(px, py, *args, az, el, sc["observer_height_m"], float(sc["max_ray_m"]), 1.0)
    t_cast = time.time() - t0
    # cost of the early-termination bound: same run with a global max
    t0 = time.time()
    st_g = cast(px, py, dem, hb, ht, np.full_like(lmax, lmax.max()), x0, y1, res, az, el,
                sc["observer_height_m"], float(sc["max_ray_m"]), 1.0)
    t_global = time.time() - t0
    assert (st_g == st).all()
    np.savez_compressed(out / "shadow_points.npz", x=px, y=py, eid=eid, seg=seg, state=st,
                        labels=sun.label.to_numpy())
    edges.drop(columns="geometry").to_parquet(out / "edges_undirected_attrs.parquet")

    share = pd.DataFrame({"label": sun.label, "elev": el.round(1), "az": az.round(1),
                          "sun": (st == 0).mean(0), "tree": (st == TREE).mean(0), "bldg": (st == BUILDING).mean(0)})
    print(share.to_string())

    # 3) city-wide estimate
    ped = gpd.read_parquet(vector.dvrpc_pedestrian(cfg))
    phl = ped[ped.county.str.upper().str.strip() == "PHILADELPHIA"].to_crs(cfg["project"]["crs"])
    city_km = float(phl.length.sum() / 1e3)
    test_km = float(edges.length.sum() / 1e3)
    city_pts = city_km * 1e3 / sc["sample_spacing_m"]
    per_pt_time = t_cast / (px.size * len(sun))
    # centre-city high rises make rays longer: also time the 25 % of points closest to tall buildings
    tall = lmax[((y1 - py) / res).astype(int), ((px - x0) / res).astype(int)]
    hi = tall >= np.quantile(tall, 0.75)
    t0 = time.time()
    cast(px[hi], py[hi], *args, az, el, sc["observer_height_m"], float(sc["max_ray_m"]), 1.0)
    per_pt_time_hi = (time.time() - t0) / (hi.sum() * len(sun))
    res_json = {
        "synthetic_max_abs_err_m": float(syn.err.abs().max()),
        "synthetic_pass": bool(syn.err.abs().max() <= 1.0),
        "synthetic": syn.to_dict("records"),
        "n_points": int(px.size), "n_times": len(sun), "test_network_km": test_km,
        "local_max_s": t_lmax, "cast_s": t_cast, "cast_s_with_global_max": t_global,
        "early_termination_speedup": t_global / t_cast,
        "us_per_point_time": per_pt_time * 1e6, "us_per_point_time_tallest_quartile": per_pt_time_hi * 1e6,
        "share_by_time": share.to_dict("records"),
        "city_network_km_dvrpc": city_km, "city_points_est": city_pts,
        "city_cast_hours_est": city_pts * 24 * per_pt_time / 3600,
        "city_cast_hours_est_pessimistic": city_pts * 24 * per_pt_time_hi / 3600,
        "threads": int(__import__("numba").get_num_threads()),
    }
    (cfg["paths"]["tables"] / "t5_shadow.json").write_text(json.dumps(res_json, indent=2))
    print(json.dumps({k: v for k, v in res_json.items() if k not in ("synthetic", "share_by_time")}, indent=2))

    # 4) 15:00 map: full-pixel shadow raster for a zoom window + all points
    i15 = int(np.where((sun.date == cfg["study"]["dates"][0]) & (sun.hour == 15))[0][0])
    zx0, zy0, W, Hh = 483300, 4422150, 900, 700          # Penn campus core
    gx, gy = np.meshgrid(np.arange(zx0, zx0 + W) + 0.5, np.arange(zy0 + Hh, zy0, -1) - 0.5)
    ras = cast(gx.ravel(), gy.ravel(), *args, az[i15:i15 + 1], el[i15:i15 + 1],
               sc["observer_height_m"], float(sc["max_ray_m"]), 1.0)[:, 0].reshape(gy.shape).astype(float)
    r0, c0 = int((y1 - (zy0 + Hh)) / res), int((zx0 - x0) / res)
    ras[hb[r0:r0 + Hh, c0:c0 + W] > 0] = np.nan        # roofs: not a pedestrian surface
    cmap = ListedColormap([(1, 0.85, 0.2, 0.0), (0.1, 0.6, 0.2, 0.55), (0.15, 0.15, 0.5, 0.6)])
    fig, ax = plt.subplots(1, 2, figsize=(20, 9))
    ext = (zx0, zx0 + W, zy0, zy0 + Hh)
    ax[0].set_xlim(ext[:2])
    ax[0].set_ylim(ext[2:])
    cx.add_basemap(ax[0], crs=cfg["project"]["crs"], source=cx.providers.Esri.WorldImagery, zoom=17)
    ax[0].imshow(ras, cmap=cmap, vmin=0, vmax=2, extent=ext, interpolation="nearest")
    bl = gpd.read_parquet(out / "buildings_heights.parquet").cx[zx0:zx0 + W, zy0:zy0 + Hh]
    bl.boundary.plot(ax=ax[0], color="white", lw=0.4)
    ax[0].set_title(f"Ground shadow at {sun.label[i15]} (az {az[i15]:.0f}°, elev {el[i15]:.0f}°): "
                    "blue = building, green = tree; Esri imagery")
    ax[0].set_axis_off()
    colors = np.array(["#f2b705", "#2a9d3a", "#2b2d7a"])
    ta = test_area(cfg)
    ax[1].scatter(px, py, c=colors[st[:, i15]], s=0.3)
    ta.boundary.plot(ax=ax[1], color="k", lw=0.5)
    ax[1].set_title(f"{px.size:,} sample points at {sun.label[i15]}: "
                    f"sun {share.sun[i15]:.0%} / tree {share.tree[i15]:.0%} / bldg {share.bldg[i15]:.0%}")
    ax[1].set_aspect("equal")
    ax[1].set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "t5_shadow_1500.png", dpi=110)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    xs = np.arange(len(sun))
    ax.stackplot(xs, share.sun, share.tree, share.bldg, labels=["sun", "tree", "building"],
                 colors=["#f2b705", "#2a9d3a", "#2b2d7a"])
    ax.set_xticks(xs, sun.label, rotation=90, fontsize=8)
    ax.set(ylabel="share of sample points", title="Test area: shade state by time")
    ax.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(figs / "t5_shadow_by_time.png", dpi=110)


if __name__ == "__main__":
    main()
