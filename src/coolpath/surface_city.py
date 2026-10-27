"""City-wide surface pipeline (phase 2).

A  per LAZ tile: download -> rasterise DSM max / ground sum / ground count -> delete LAZ
B  mosaic the tile rasters onto the city grid (raw DSM and ground-point DEM, NaN holes)
C  per block (with a margin wide enough for every building touching the block):
   DEM fill, footprint-aware DSM fill (D-003), nDSM, building heights incl. approx_hgt
   extrusion (D-015) and unmapped structures (D-013), tree heights (D-012)
"""

import json
import time
from pathlib import Path

import geopandas as gpd
import laspy
import numpy as np
import rasterio
import shapely
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds
from scipy import ndimage

from coolpath.acquire import lidar
from coolpath.surface import (Grid, _accumulate, building_mask, despike, fill_dsm, fill_holes, gap_fill_median,
                              per_label)

FT = 0.3048006096
SRC_CODES = {"footprint_lidar": 1, "footprint_approx_hgt": 2, "unmapped_structure": 3}


# ---------------------------------------------------------------- A: tiles
def tile_raster(cfg: dict, tile_id: str, out_dir: Path, keep_laz: bool = False) -> dict:
    """Download one LAZ tile, rasterise it on its own 1 m grid, write a 3-band GTiff, delete the LAZ."""
    out = out_dir / f"{tile_id}.tif"
    meta = out.with_suffix(".json")
    if out.exists() and meta.exists():
        return json.loads(meta.read_text())
    sc = cfg["surface"]
    t0 = time.time()
    laz = lidar.download_tile(cfg, tile_id)
    t_dl = time.time() - t0
    with laspy.open(laz) as f:
        h = f.header
        grid = Grid.from_bounds(h.mins[0], h.mins[1], h.maxs[0], h.maxs[1])
    dsm = np.full(grid.shape, -np.inf)
    gsum = np.zeros(grid.shape)
    gcnt = np.zeros(grid.shape, np.int32)
    cnt = np.zeros(grid.shape, np.int32)
    noise = np.asarray(sc["noise_classes"], np.uint8)
    hist = np.zeros(256, np.int64)
    t0 = time.time()
    with laspy.open(laz, laz_backend=laspy.LazBackend.LazrsParallel) as f:
        for pts in f.chunk_iterator(sc["laz_chunk_points"]):
            c = np.asarray(pts.classification, np.uint8)
            hist += np.bincount(c, minlength=256)
            _accumulate(np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z), c, grid.x0, grid.y1,
                        grid.res, grid.nx, grid.ny, noise, np.uint8(sc["ground_class"]), dsm, gsum, gcnt, cnt)
    t_r = time.time() - t0
    bands = np.stack([np.where(np.isfinite(dsm), dsm, np.nan), gsum, gcnt]).astype(np.float32)
    out_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out.with_suffix(".part.tif"), "w", driver="GTiff", height=grid.ny, width=grid.nx,
                       count=3, dtype="float32", crs=grid.crs, transform=grid.transform, nodata=np.nan,
                       compress="DEFLATE", predictor=3, tiled=True) as dst:
        dst.write(bands)
    out.with_suffix(".part.tif").rename(out)
    m = {"tile": tile_id, "bytes": laz.stat().st_size, "points": int(hist.sum()), "nonnoise": int(cnt.sum()),
         "download_s": t_dl, "rasterise_s": t_r, "empty_px_share": float((cnt == 0).mean()),
         "classes": {int(k): int(v) for k, v in enumerate(hist) if v}}
    meta.write_text(json.dumps(m))
    if not keep_laz:
        laz.unlink()
    return m


# ---------------------------------------------------------------- B: mosaic
def mosaic_block(tile_paths: list[Path], tile_boxes, sub: Grid):
    """Raw DSM (max) and ground-point DEM (sum / count) on a sub-grid from overlapping tiles."""
    dsm = np.full(sub.shape, np.nan, np.float32)
    gsum = np.zeros(sub.shape, np.float64)
    gcnt = np.zeros(sub.shape, np.float64)
    bx = shapely.box(*sub.bounds)
    for i in shapely.STRtree(tile_boxes).query(bx, predicate="intersects"):
        with rasterio.open(tile_paths[i]) as src:
            w = from_bounds(*sub.bounds, transform=src.transform)
            w = Window(round(w.col_off), round(w.row_off), sub.nx, sub.ny)
            a = src.read(window=w, boundless=True, fill_value=np.nan)
        d = a[0]
        dsm = np.fmax(dsm, d)
        gsum += np.nan_to_num(a[1])
        gcnt += np.nan_to_num(a[2])
    with np.errstate(invalid="ignore", divide="ignore"):
        dem = np.where(gcnt > 0, gsum / gcnt, np.nan).astype(np.float32)
    return {"dsm_raw": dsm, "dem_pts": dem}


# ---------------------------------------------------------------- C: surfaces
def read_on_grid(path: Path, sub: Grid, fill=np.nan, dtype=np.float32) -> np.ndarray:
    """Read the window of an aligned raster matching `sub` (boundless)."""
    with rasterio.open(path) as src:
        w = from_bounds(*sub.bounds, transform=src.transform)
        w = Window(round(w.col_off), round(w.row_off), sub.nx, sub.ny)
        return src.read(1, window=w, boundless=True, fill_value=fill).astype(dtype)


def noaa_dem(cfg: dict, sub: Grid) -> np.ndarray:
    out = np.full(sub.shape, np.nan, np.float32)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open("/vsicurl/" + cfg["sources"]["dem_noaa_9849"]) as src:
            reproject(rasterio.band(src, 1), out, src_crs="EPSG:6565", dst_transform=sub.transform,
                      dst_crs=sub.crs, resampling=Resampling.bilinear, src_nodata=src.nodata, dst_nodata=np.nan)
    return out * FT


def surface_block(cfg: dict, paths: dict, win: Grid, core: tuple, bld: gpd.GeoDataFrame) -> dict:
    """All phase-2 surfaces for one window; returns arrays cropped to `core` (r0, c0, h, w) + stats."""
    tr, fh, us = cfg["tree"], cfg["footprint_height"], cfg["unmapped_structures"]
    dsm = read_on_grid(paths["dsm_raw"], win)
    dem_pts = read_on_grid(paths["dem_pts"], win)
    lc = read_on_grid(paths["landcover"], win, fill=0, dtype=np.uint8)
    stats = {}

    sf = cfg["surface"]
    dsm, stats["spikes_removed"] = despike(dsm, **sf["despike"])

    # DEM (D-022): ground points -> NOAA 9849 bare earth (water level under bridges / on rivers)
    # -> IDW fill (300 px) -> 0 m where neither covers (New Jersey side)
    dem = dem_pts.copy()
    if np.isnan(dem).any():
        nd = noaa_dem(cfg, win)
        stats["dem_px_from_noaa"] = int((np.isnan(dem) & np.isfinite(nd)).sum())
        dem = np.where(np.isnan(dem), nd, dem)
    if np.isnan(dem).any() and np.isfinite(dem).any():
        f = fill_holes(dem, max_search_px=300)
        dem = np.where(np.isnan(dem) & (f != 0), f, dem)
    stats["dem_px_zero_fill"] = int(np.isnan(dem).sum())
    dem = np.nan_to_num(dem, nan=0.0).astype(np.float32)
    implausible = np.isfinite(dsm) & (dsm - dem > sf["max_ndsm_m"])    # clustered noise returns
    stats["implausible_px"] = int(implausible.sum())
    dsm[implausible] = np.nan

    # DSM holes outside footprints: water -> DEM; small gaps -> IDW (<= dsm_hole_fill_max_px); rest -> DEM
    water = (lc == sf["landcover_water_class"]) & ~np.isfinite(dsm)
    n = len(bld)
    bid = building_mask(bld, win, "wid") if n else np.zeros(win.shape, np.int32)
    dsm = np.where(water & (bid == 0), dem, dsm)
    approx = bld["approx_hgt"].to_numpy(float) * fh["approx_hgt_unit_m"] if n else np.zeros(0)
    approx = np.where(approx > 0, approx, np.nan)
    if n:   # footprint-aware fill inside footprints (D-003)
        dsm_f, fs = fill_dsm(dsm, bid, dem, approx, fh["roof_fill_min_valid_share"], sf["dsm_hole_fill_max_px"])
        stats.update(fs)
    else:
        dsm_f = fill_holes(dsm, max_search_px=sf["dsm_hole_fill_max_px"])
    dsm_f[(dsm_f == 0) & ~np.isfinite(dsm)] = np.nan                  # fillnodata leaves 0 if too far
    dsm_f = np.where(np.isfinite(dsm_f), dsm_f, dem).astype(np.float32)
    ndsm = np.clip(dsm_f - dem, 0, None)

    # building heights; D-015 flat extrusion where LiDAR has (almost) nothing
    hb = np.where(bid > 0, ndsm, 0).astype(np.float32)
    src = np.where(bid > 0, SRC_CODES["footprint_lidar"], 0).astype(np.uint8)
    if n:
        med = per_label(bid, ndsm, n, np.median)
        valid = per_label(bid, np.isfinite(dsm).astype(np.float32), n, np.mean)
        ext = (med < fh["lidar_median_max_m"]) & (approx >= fh["approx_hgt_min_m"])
        ext |= (valid < fh["roof_fill_min_valid_share"]) & np.isfinite(approx)   # D-003 roof fallback
        high = per_label(bid, (ndsm > fh["skip_share_height_m"]).astype(np.float32), n, np.mean)
        lo, hi = fh["skip_share_range"]
        skip = (high >= lo) & (high <= hi) & (valid >= fh["roof_fill_min_valid_share"])        # D-032
        excl = bld["objectid"].isin(paths.get("exclude_ids", ())).to_numpy()                   # D-033
        stats["extrusion_skipped_d032"] = int((ext & skip).sum())
        stats["extrusion_excluded_d033"] = int((ext & ~skip & excl).sum())
        ext &= ~skip & ~excl
        if ext.any():
            m = (bid > 0) & ext[np.maximum(bid - 1, 0)]
            hb[m] = approx[bid[m] - 1]
            src[m] = SRC_CODES["footprint_approx_hgt"]
        bld = bld.assign(lidar_med_m=med, extruded=ext, share_high=high, valid_share=valid, skip_d032=skip)

    # trees (D-012 / D-026): canopy mask outside footprints; nDSM >= 2 m kept, gaps (< 2 m)
    # take the median of valid neighbours in a 3 x 3 m window, otherwise stay (and drop out)
    near_b = ndimage.binary_dilation(bid > 0, iterations=tr["footprint_buffer_px"])
    canopy = (lc == tr["landcover_canopy_class"]) & ~near_b
    ht = np.where(canopy, gap_fill_median(ndsm, canopy, tr["min_height_m"], tr["gap_fill_window_m"]), 0)
    ht[ht < tr["min_height_m"]] = 0
    stats["tree_gap_filled_px"] = int(((ndsm < tr["min_height_m"]) & (ht > 0)).sum())

    # D-028: outside the city (no land cover) trees come from CHMv2 >= 2 m
    outside = rasterize([(paths["city"], 1)], out_shape=win.shape, transform=win.transform, fill=0) == 0
    chm = read_on_grid(paths["chmv2"], win, fill=0)
    buf_tree = outside & (chm >= tr["min_height_m"]) & ~near_b

    # unmapped structures (D-013 / D-025), incl. bridges / viaducts: candidates are cut into smooth
    # segments at DSM jumps > segment_jump_m; a segment counts when area >= min_area_m2 and its
    # DSM std <= max_height_std_m; kept segments regrow 1 px into the cut pixels
    cand = (ndsm >= us["min_height_m"]) & (lc != tr["landcover_canopy_class"]) & ~buf_tree & \
        ~ndimage.binary_dilation(bid > 0, iterations=us["footprint_buffer_px"])
    cand = ndimage.binary_opening(cand, iterations=us["opening_iterations"])
    gx = np.abs(np.diff(dsm_f, axis=1, append=dsm_f[:, -1:]))
    gy = np.abs(np.diff(dsm_f, axis=0, append=dsm_f[-1:, :]))
    lab, nl = ndimage.label(cand & (gx <= us["segment_jump_m"]) & (gy <= us["segment_jump_m"]))
    m = np.zeros(win.shape, bool)
    if nl:
        area = np.bincount(lab.ravel())[1:]
        sd = per_label(lab, dsm_f, nl, np.std)
        keep = (area >= us["min_area_m2"]) & (sd <= us["max_height_std_m"])
        m = (lab > 0) & keep[np.maximum(lab - 1, 0)]
        m = ndimage.binary_dilation(m, iterations=1) & cand
        hb[m] = ndsm[m]
        src[m] = SRC_CODES["unmapped_structure"]
    buf_tree &= hb == 0
    ht = np.where(buf_tree, chm, ht).astype(np.float32)
    stats["buffer_tree_px"] = int(buf_tree.sum())
    r0, c0, h, w = core
    crop = (slice(r0, r0 + h), slice(c0, c0 + w))
    out = {"dem": dem[crop], "dsm": dsm_f[crop], "surface_building": hb[crop], "surface_tree": ht[crop],
           "building_source": src[crop]}
    if nl:
        stats["unmapped_blobs_core"] = int(ndimage.label(m[crop])[1])
    stats["unmapped_px_core"] = int((src[crop] == SRC_CODES["unmapped_structure"]).sum())
    stats["extruded_px_core"] = int((src[crop] == SRC_CODES["footprint_approx_hgt"]).sum())
    stats["canopy_mask_px_core"] = int(canopy[crop].sum())
    stats["tree_px_core"] = int((ht[crop] > 0).sum())
    if n:
        core_box = shapely.box(*win.sub(r0, c0, h, w).bounds)
        own = bld[bld.centroid.within(core_box)]
        stats["buildings_core"] = int(len(own))
        stats["extruded_buildings_core"] = int(own.extruded.sum())
        out["_buildings"] = own[["objectid", "lidar_med_m", "extruded", "share_high", "valid_share", "skip_d032"]].copy()
    return out, stats
