"""Shadow check around 2716 South St (D-033 update): ground shadow at 06-21 15:00 before / after
the approx_hgt extrusion, on the 2025 orthophoto, plus the sample points whose state changed."""

import json

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap
from rasterio.windows import from_bounds

from coolpath import load_config
from coolpath.shadow import cast, local_max
from scripts.phase_02_spotcheck import ortho

HALF = 250


def read(path, b, scale=1.0):
    with rasterio.open(path) as s:
        w = from_bounds(*b, transform=s.transform)
        return s.read(1, window=w).astype(np.float32) * scale, s.window_transform(w)


def main():
    cfg = load_config()
    proc, sc = cfg["paths"]["processed"], cfg["shadow"]
    before = cfg["paths"]["interim"] / "before_2716"
    rev = pd.read_csv(cfg["paths"]["tables"] / "phase_02_demo_extrusion_review.csv")
    oid = int(rev.loc[rev.address == "2716 SOUTH ST", "objectid"].iloc[0])
    b = gpd.read_parquet(cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet").to_crs(cfg["project"]["crs"])
    geom = b.loc[b.objectid == oid, "geometry"].iloc[0]
    c = geom.centroid
    view = (c.x - HALF, c.y - HALF, c.x + HALF, c.y + HALF)
    big = (c.x - HALF - 1000, c.y - HALF - 1000, c.x + HALF + 1000, c.y + HALF + 1000)
    sun = pd.read_parquet(proc / "sun.parquet")
    i15 = int(np.where(sun.label == "0621_15")[0][0])
    dem, tr = read(proc / "dem.tif", big)
    ht, _ = read(proc / "surface_tree.tif", big, 0.1)
    rasters = {}
    for name, path in [("before", before / "surface_building.tif"), ("after", proc / "surface_building.tif")]:
        hb, _ = read(path, big, 0.1)
        lmax = local_max(dem + np.maximum(hb, ht), 1.0, sc["localmax_block_m"], sc["max_ray_m"])
        gx, gy = np.meshgrid(np.arange(view[0], view[2]) + 0.5, np.arange(view[3], view[1], -1) - 0.5)
        s = cast(gx.ravel(), gy.ravel(), dem, hb, ht, lmax, tr.c, tr.f, 1.0, sun.azimuth.to_numpy()[i15:i15 + 1],
                 sun.elevation.to_numpy()[i15:i15 + 1], sc["observer_height_m"], float(sc["max_ray_m"]), 1.0,
                 sc["terrain_occlusion"])[:, 0].reshape(gx.shape).astype(float)
        r0 = int(round(tr.f - view[3]))
        c0 = int(round(view[0] - tr.c))
        s[hb[r0:r0 + gx.shape[0], c0:c0 + gx.shape[1]] > 0] = np.nan       # roofs: not walkable
        rasters[name] = (s, hb[r0:r0 + gx.shape[0], c0:c0 + gx.shape[1]])
    col = f"s_{sun.label[i15]}"
    p0 = pd.read_parquet(before / "shadow_points.parquet", columns=["x", "y", col])
    p1 = pd.read_parquet(proc / "shadow_points.parquet", columns=["x", "y", col])
    inv = (p1.x > view[0]) & (p1.x < view[2]) & (p1.y > view[1]) & (p1.y < view[3])
    ch = inv & (p0[col] != p1[col])
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    inside = ~geometry_mask([geom], rasters["after"][1].shape, from_origin(view[0], view[3], 1, 1))
    hb_after = np.where(inside, rasters["after"][1], np.nan)
    hb_before = np.where(inside, rasters["before"][1], np.nan)
    res = {"objectid": oid, "approx_hgt_ft": float(b.loc[b.objectid == oid].iloc[0].get("approx_hgt", np.nan))
           if "approx_hgt" in b else None,
           "height_in_footprint_before_m": float(np.nanmedian(hb_before)),
           "roof_height_after_m": float(np.nanmedian(hb_after)),
           "points_in_view": int(inv.sum()), "points_changed_1500": int(ch.sum()),
           "changed_to_building": int((ch & (p1[col] == 2)).sum())}
    (cfg["paths"]["tables"] / "phase_02_2716_check.json").write_text(json.dumps(res, indent=2))
    print(res)

    img = ortho(2025, view, 600)
    cmap = ListedColormap([(1, 0.85, 0.2, 0.0), (0.1, 0.6, 0.2, 0.5), (0.15, 0.15, 0.5, 0.6)])
    fig, ax = plt.subplots(1, 2, figsize=(18, 9))
    for a, name in zip(ax, ("before", "after")):
        a.imshow(img, extent=(view[0], view[2], view[1], view[3]))
        a.imshow(rasters[name][0], cmap=cmap, vmin=0, vmax=2, extent=(view[0], view[2], view[1], view[3]),
                 interpolation="nearest")
        gpd.GeoSeries([geom]).boundary.plot(ax=a, color="yellow", lw=2)
        a.set_xlim(view[0], view[2])
        a.set_ylim(view[1], view[3])
        a.set_axis_off()
    z = p1[ch]
    ax[1].scatter(z.x, z.y, c="red", s=6, label=f"sample points that changed ({int(ch.sum())})")
    ax[1].legend(loc="lower left")
    ax[0].set_title("Before: 2716 South St excluded (LiDAR height ≈ 0)\n06-21 15:00 ground shadow (blue building, green tree)")
    ax[1].set_title(f"After: extruded to approx_hgt ({res['roof_height_after_m']:.0f} m)\n"
                    "06-21 15:00 ground shadow; 2025 PASDA orthophoto")
    plt.tight_layout()
    plt.savefig(cfg["paths"]["figures"] / "phase_02_2716_check.png", dpi=100)


if __name__ == "__main__":
    main()
