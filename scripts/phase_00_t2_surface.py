"""T2: 1 m DSM / DEM / nDSM for the test-area tiles; building heights vs approx_hgt."""

import json
import time

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy import ndimage

from coolpath import load_config
from coolpath.acquire import lidar, vector
from coolpath.areas import test_area, test_area_bbox4326
from coolpath.surface import Grid, building_mask, fill_dsm, fill_holes, per_label, rasterize_laz, write_cog

FT = 0.3048006096  # US survey foot


def main():
    cfg = load_config()
    sc, figs = cfg["surface"], cfg["paths"]["figures"]
    out = cfg["paths"]["interim"] / "test"
    sel = lidar.tiles_for_bbox(cfg, *test_area_bbox4326(cfg))
    paths = [lidar.download_tile(cfg, i) for i in sel.id]
    xmin, ymin, xmax, ymax = sel.to_crs(cfg["project"]["crs"]).total_bounds
    grid = Grid.from_bounds(xmin, ymin, xmax, ymax)
    print("grid", grid.shape, grid.bounds)

    t0 = time.time()
    dsm, dem_pts, cnt, _ = rasterize_laz(paths, grid, sc["noise_classes"], sc["ground_class"], sc["laz_chunk_points"])
    t_raster = time.time() - t0
    n_pts = int(cnt.sum())
    print(f"rasterised {n_pts / 1e6:.0f} M non-noise points in {t_raster:.0f} s")

    # holes inside the test area only (outer tile edges are not square)
    ta = building_mask(test_area(cfg).to_frame("geometry"), grid) > 0
    holes = ~np.isfinite(dsm) & ta
    lab, nlab = ndimage.label(holes)
    sizes = np.bincount(lab.ravel())[1:] if nlab else np.array([0])
    # building footprints
    bld = vector.buildings(cfg, test_area_bbox4326(cfg, 0.012), "test").to_crs(grid.crs)
    bld = bld[bld.geometry.notna() & bld.is_valid].reset_index(drop=True)
    bld["bid"] = np.arange(1, len(bld) + 1)
    bld["approx_hgt_m"] = bld["approx_hgt"] * FT
    bmask = building_mask(bld, grid, "bid")

    t0 = time.time()
    dem_f = fill_holes(dem_pts, max_search_px=300)
    dsm_f, fill_stats = fill_dsm(dsm, bmask, dem_f, bld["approx_hgt_m"].to_numpy())
    t_fill = time.time() - t0
    ndsm = np.clip(dsm_f - dem_f, 0, None)
    valid_share = per_label(bmask, np.isfinite(dsm).astype(np.float32), len(bld), np.mean)

    # cross-check ground DEM against NOAA 9849 (3 ft, ftUS)
    noaa = np.full(grid.shape, np.nan, np.float32)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open("/vsicurl/" + cfg["sources"]["dem_noaa_9849"]) as src:
            reproject(rasterio.band(src, 1), noaa, src_crs="EPSG:6565", dst_transform=grid.transform,
                      dst_crs=grid.crs, resampling=Resampling.bilinear, src_nodata=src.nodata, dst_nodata=np.nan)
    noaa *= FT
    d = (dem_f - noaa)[ta & np.isfinite(noaa)]

    in_ta = bld.centroid.within(test_area(cfg).iloc[0])
    bld["lidar_med_m"] = per_label(bmask, ndsm, len(bld), np.median)
    bld["lidar_p90_m"] = per_label(bmask, ndsm, len(bld), lambda v: np.percentile(v, 90))
    bld["npx"] = np.bincount(bmask.ravel(), minlength=len(bld) + 1)[1:]
    bld["lidar_valid_share"] = valid_share
    ok = in_ta & (bld.npx >= 20) & (bld.lidar_valid_share >= 0.2) & (bld.approx_hgt > 0) & bld.lidar_med_m.notna()
    b = bld[ok]
    err = b.lidar_med_m - b.approx_hgt_m
    err_raw = b.lidar_med_m - b.approx_hgt     # if approx_hgt were metres
    r = float(np.corrcoef(b.lidar_med_m, b.approx_hgt_m)[0, 1])
    r90 = float(np.corrcoef(b.lidar_p90_m, b.approx_hgt_m)[0, 1])
    bheight = np.where(bmask > 0, ndsm, 0)

    write_cog(out / "dsm.tif", dsm_f, grid, nodata=None)
    write_cog(out / "dem.tif", dem_f, grid, nodata=None)
    write_cog(out / "ndsm.tif", ndsm, grid, scale_dm=True)
    write_cog(out / "surface_building.tif", bheight, grid, scale_dm=True)
    write_cog(out / "building_id.tif", bmask, grid, nodata=0)
    bld.to_parquet(out / "buildings_heights.parquet")

    res = {
        "grid_shape": grid.shape, "grid_bounds": grid.bounds, "non_noise_points": n_pts,
        "rasterise_s": t_raster, "fill_s": t_fill,
        "points_per_s": n_pts / t_raster,
        "dsm_hole_share_test_area": float(holes.sum() / ta.sum()),
        "dsm_hole_count": int(nlab), "dsm_largest_hole_m2": int(sizes.max()),
        "dsm_holes_ge_100m2": int((sizes >= 100).sum()),
        "dem_ground_px_share_test_area": float((np.isfinite(dem_pts) & ta).sum() / ta.sum()),
        "dem_vs_noaa_median_m": float(np.median(d)), "dem_vs_noaa_mad_m": float(np.median(np.abs(d))),
        "dem_vs_noaa_p95_abs_m": float(np.percentile(np.abs(d), 95)),
        **fill_stats,
        "n_buildings_total": int(len(bld)), "n_buildings_compared": int(ok.sum()),
        "approx_hgt_units": "feet (metadata: 'All measurements are in feet'; confirmed by fit below)",
        "median_error_m_ft_assumption": float(err.median()), "mae_m": float(err.abs().median()),
        "median_error_if_metres": float(err_raw.median()),
        "pearson_r_median": r, "pearson_r_p90": r90,
        "spearman_r_median": float(b.lidar_med_m.corr(b.approx_hgt_m, method="spearman")),
        "ols_slope_lidar_on_approx": float(np.polyfit(b.approx_hgt_m, b.lidar_med_m, 1)[0]),
        "max_building_height_m": float(np.nanmax(bld.lidar_p90_m)),
    }
    (cfg["paths"]["tables"] / "t2_surface.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))

    fig, ax = plt.subplots(2, 2, figsize=(14, 12))
    ext = (grid.bounds[0], grid.bounds[2], grid.bounds[1], grid.bounds[3])
    ls = plt.matplotlib.colors.LightSource(315, 45)
    ax[0, 0].imshow(ls.hillshade(dsm_f, vert_exag=2), cmap="gray", extent=ext)
    test_area(cfg).boundary.plot(ax=ax[0, 0], color="red")
    ax[0, 0].set_title("DSM hillshade (holes filled); red = test area")
    im = ax[0, 1].imshow(np.clip(ndsm, 0, 60), cmap="viridis", extent=ext)
    plt.colorbar(im, ax=ax[0, 1], shrink=0.7, label="nDSM (m)")
    ax[0, 1].imshow(np.where(holes, 1, np.nan), cmap="autumn", extent=ext, interpolation="nearest")
    ax[0, 1].set_title("nDSM (0–60 m); holes before fill in orange")
    ax[1, 0].scatter(b.approx_hgt_m, b.lidar_med_m, s=3, alpha=0.4)
    lim = [0, max(b.approx_hgt_m.max(), b.lidar_med_m.max()) * 1.02]
    ax[1, 0].plot(lim, lim, "k--", lw=1)
    ax[1, 0].set(xlabel="approx_hgt × 0.3048 (m)", ylabel="LiDAR nDSM median in footprint (m)",
                 title=f"Building height, n={len(b)}, r={r:.3f}, median err={err.median():.2f} m")
    ax[1, 1].hist(np.clip(d, -2, 2), bins=100)
    ax[1, 1].set(title=f"DEM (ground pts) − NOAA DEM 9849, median {np.median(d):.3f} m", xlabel="m")
    plt.tight_layout()
    plt.savefig(figs / "t2_surface.png", dpi=110)


if __name__ == "__main__":
    main()
