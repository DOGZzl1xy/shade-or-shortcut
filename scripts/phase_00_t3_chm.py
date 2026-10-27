"""T3: CHMv2 tiles, resolution and acquisition dates; compare with LiDAR non-building nDSM."""

import json
import time

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from scipy import ndimage

from coolpath import load_config
from coolpath.acquire import chm, landcover
from coolpath.areas import test_area
from coolpath.surface import Grid, building_mask, read_dm, write_cog


def main():
    cfg = load_config()
    figs, out = cfg["paths"]["figures"], cfg["paths"]["interim"] / "test"
    min_h = cfg["tree"]["min_height_m"]
    with rasterio.open(out / "ndsm.tif") as src:
        b = src.bounds
    grid = Grid.from_bounds(b.left, b.bottom, b.right, b.top)
    ndsm = read_dm(out / "ndsm.tif")
    with rasterio.open(out / "building_id.tif") as src:
        bid = src.read(1)
    ta_geom = test_area(cfg)
    ta = building_mask(ta_geom.to_frame("geometry"), grid) > 0
    ta4326 = ta_geom.to_crs(4326).iloc[0]

    philly_tiles = chm.tiles_for(cfg, gpd.GeoSeries.from_xy([-75.28, -74.955], [39.867, 40.138]).union_all().envelope)
    test_tiles = chm.tiles_for(cfg, ta4326)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open("/vsicurl/" + cfg["sources"]["chmv2_http_base"] + f"chm/{test_tiles[0]}.tif") as src:
            native = {"crs": "EPSG:3857 (WGS 84 / Pseudo-Mercator)", "res_m": src.res[0], "dtype": src.dtypes[0],
                      "size_px": [src.width, src.height], "res_ground_m_at_40N": src.res[0] * np.cos(np.radians(39.95))}

    dates = []
    for t in test_tiles:
        g = chm.acq_dates(cfg, t)
        g = g[g.intersects(ta4326)].copy()
        g["share"] = g.to_crs(cfg["project"]["crs"]).intersection(ta_geom.iloc[0]).area / ta_geom.iloc[0].area
        g["tile"] = t
        dates.append(g)
    dates = gpd.GeoDataFrame(__import__("pandas").concat(dates, ignore_index=True), crs=4326)
    dates["acq_date"] = dates.acq_date.astype(str).str[:10]
    dates["month"] = dates.acq_date.str[5:7].astype(int)
    leaf_on_share = float(dates.loc[dates.month.between(5, 10), "share"].sum())

    t0 = time.time()
    chm_g = chm.read_to_grid(cfg, test_tiles, grid)
    t_read = time.time() - t0

    # comparison on non-building pixels (footprints dilated 2 px to drop wall edges)
    nb = ta & ~ndimage.binary_dilation(bid > 0, iterations=2)
    tree_chm = nb & (chm_g >= min_h)
    tree_lid = nb & (ndsm >= min_h)
    both = tree_chm & tree_lid
    diff = (ndsm - chm_g)[tree_chm]
    r = float(np.corrcoef(ndsm[both], chm_g[both])[0, 1])
    inter = (tree_chm & tree_lid).sum()
    union = (tree_chm | tree_lid).sum()
    res = {
        "philly_tiles": philly_tiles, "test_tiles": test_tiles, "native": native, "read_s": t_read,
        "acq_dates_test_area": dates[["tile", "acq_date", "share"]].sort_values("share", ascending=False)
                               .to_dict("records"),
        "leaf_on_share_may_oct": leaf_on_share,
        "canopy_share_chm_nonbldg": float(tree_chm.sum() / nb.sum()),
        "canopy_share_lidar_nonbldg": float(tree_lid.sum() / nb.sum()),
        "canopy_iou": float(inter / union),
        "lidar_minus_chm_median_m_on_chm_canopy": float(np.median(diff)),
        "lidar_minus_chm_mean_m_on_chm_canopy": float(diff.mean()),
        "chm_canopy_px_where_lidar_lt2m_share": float((tree_chm & ~tree_lid).sum() / tree_chm.sum()),
        "pearson_r_both_canopy": r,
        "chm_median_height_m": float(np.median(chm_g[tree_chm])),
        "lidar_median_height_m": float(np.median(ndsm[tree_lid])),
    }
    # fallback (CHMv2 imagery is leaf-off in the test area): LiDAR nDSM >= 2 m outside
    # footprints, masked by the 2018 land-cover tree canopy class
    lc = landcover.classes_on_grid(cfg, grid)
    write_cog(out / "landcover2018.tif", lc, grid, nodata=0)
    lc_tree = lc == 1
    fb = ~ndimage.binary_dilation(bid > 0, iterations=2) & (ndsm >= min_h) & lc_tree
    tree_fallback = np.where(fb, ndsm, 0)
    res.update({
        "fallback": "LiDAR non-building nDSM >= 2 m AND land cover 2018 class 1 (tree canopy)",
        "lc2018_canopy_share_nonbldg": float((lc_tree & nb).sum() / nb.sum()),
        "fallback_canopy_share_nonbldg": float((fb & nb).sum() / nb.sum()),
        "lc_canopy_covered_by_fallback": float((fb & nb).sum() / (lc_tree & nb).sum()),
        "lidar_ge2m_removed_by_lc_mask": float((tree_lid & ~lc_tree).sum() / tree_lid.sum()),
        "fallback_vs_chm_iou": float((fb & tree_chm).sum() / ((fb & nb) | tree_chm).sum()),
        "fallback_median_height_m": float(np.median(ndsm[fb & nb])),
    })
    (cfg["paths"]["tables"] / "t3_chm.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))
    write_cog(out / "chm_v2.tif", np.nan_to_num(chm_g), grid, scale_dm=True)
    write_cog(out / "surface_tree_lidar.tif", tree_fallback, grid, scale_dm=True)

    ext = (grid.bounds[0], grid.bounds[2], grid.bounds[1], grid.bounds[3])
    fig, ax = plt.subplots(2, 3, figsize=(21, 11))
    bm = np.where(bid > 0, 1, np.nan)
    for a, arr, title in [(ax[0, 0], chm_g, "CHMv2 (m), buildings grey"),
                          (ax[0, 1], np.where(bid > 0, np.nan, ndsm), "LiDAR nDSM outside footprints (m), Mar 2022"),
                          (ax[0, 2], np.where(fb, ndsm, np.nan), "Fallback tree height: nDSM>=2 m & LC2018 canopy")]:
        im = a.imshow(np.clip(arr, 0, 30), cmap="YlGn", extent=ext, vmin=0, vmax=30)
        a.imshow(bm, cmap="Greys", vmin=0, vmax=3, extent=ext, interpolation="nearest")
        ta_geom.boundary.plot(ax=a, color="red")
        a.set_title(title)
        plt.colorbar(im, ax=a, shrink=0.7)
    dd = dates.to_crs(cfg["project"]["crs"]).clip(ta_geom.iloc[0])
    dd.plot(ax=ax[1, 0], column="acq_date", legend=True, cmap="tab10", edgecolor="k",
            legend_kwds={"loc": "upper left", "bbox_to_anchor": (1, 1)})
    ax[1, 0].set_title(f"CHMv2 imagery date in test area (leaf-on share {leaf_on_share:.0%})")
    ax[1, 1].hist(np.clip(diff, -20, 20), bins=80)
    ax[1, 1].axvline(np.median(diff), color="r")
    ax[1, 1].set(title=f"LiDAR nDSM − CHMv2 on CHM canopy px (median {np.median(diff):.2f} m)", xlabel="m")
    ax[1, 2].imshow(np.where(lc_tree, 1, np.nan), cmap="Greens", vmin=0, vmax=1.3, extent=ext, interpolation="nearest")
    ax[1, 2].imshow(np.where(tree_lid & ~lc_tree, 1, np.nan), cmap="Reds", vmin=0, vmax=1, extent=ext, interpolation="nearest")
    ta_geom.boundary.plot(ax=ax[1, 2], color="k")
    ax[1, 2].set_title("LC2018 canopy (green); nDSM>=2 m removed by mask (red)")
    plt.tight_layout()
    plt.savefig(figs / "t3_chm.png", dpi=110)


if __name__ == "__main__":
    main()
