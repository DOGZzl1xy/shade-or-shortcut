"""DSM / DEM / nDSM rasterisation from LAZ, and building / tree height rasters.

LAZ tiles (NOAA 9848) are NAD83(2011) / UTM 18N (EPSG:6347) + NAVD88 metres; their
coordinates are used directly on the EPSG:32618 grid.
"""

from dataclasses import dataclass
from pathlib import Path

import laspy
import numpy as np
import rasterio
import rasterio.windows
from numba import njit
from rasterio.features import rasterize
from rasterio.fill import fillnodata
from rasterio.transform import from_origin

NODATA = -9999.0


@dataclass
class Grid:
    x0: float   # left edge (m)
    y1: float   # top edge (m)
    nx: int
    ny: int
    res: float = 1.0
    crs: str = "EPSG:32618"

    @classmethod
    def from_bounds(cls, xmin, ymin, xmax, ymax, res=1.0, crs="EPSG:32618"):
        x0, y0 = np.floor(xmin / res) * res, np.floor(ymin / res) * res
        x1, y1 = np.ceil(xmax / res) * res, np.ceil(ymax / res) * res
        return cls(x0, y1, int(round((x1 - x0) / res)), int(round((y1 - y0) / res)), res, crs)

    @property
    def transform(self):
        return from_origin(self.x0, self.y1, self.res, self.res)

    @property
    def bounds(self):
        return (self.x0, self.y1 - self.ny * self.res, self.x0 + self.nx * self.res, self.y1)

    @property
    def shape(self):
        return (self.ny, self.nx)

    def sub(self, r0: int, c0: int, h: int, w: int) -> "Grid":
        return Grid(self.x0 + c0 * self.res, self.y1 - r0 * self.res, w, h, self.res, self.crs)

    def blocks(self, size: int):
        """(r0, c0, h, w) windows of at most size x size pixels covering the grid."""
        for r0 in range(0, self.ny, size):
            for c0 in range(0, self.nx, size):
                yield r0, c0, min(size, self.ny - r0), min(size, self.nx - c0)


def write_tiled(path: Path, grid: Grid, dtype, block_fn, block: int = 4096, nodata=None, tags=None):
    """Build a large single-band COG block by block: block_fn(subgrid) -> array.

    Writes a tiled GTiff first (resumable: path.with_suffix('.partial.tif')), then a COG copy."""
    from rasterio.shutil import copy as rio_copy
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial.tif")
    done_file = path.with_suffix(".blocks_done")
    done = set(done_file.read_text().split()) if done_file.exists() and tmp.exists() else set()
    prof = dict(driver="GTiff", height=grid.ny, width=grid.nx, count=1, dtype=dtype, crs=grid.crs,
                transform=grid.transform, nodata=nodata, tiled=True, blockxsize=512, blockysize=512,
                compress="DEFLATE", BIGTIFF="YES")
    mode = "r+" if done else "w"
    with rasterio.open(tmp, mode, **({} if done else prof)) as dst:
        for r0, c0, h, w in grid.blocks(block):
            k = f"{r0}_{c0}"
            if k in done:
                continue
            arr = block_fn(grid.sub(r0, c0, h, w))
            dst.write(arr.astype(dtype), 1, window=rasterio.windows.Window(c0, r0, w, h))
            with open(done_file, "a") as f:
                f.write(k + "\n")
        if tags:
            dst.update_tags(**tags)
    rio_copy(tmp, path, driver="COG", compress="DEFLATE", BIGTIFF="YES",
             overview_resampling="NEAREST")
    tmp.unlink()
    done_file.unlink()
    return path


@njit(cache=True)
def _accumulate(x, y, z, cls, x0, y1, res, nx, ny, noise, ground, dsm, gsum, gcnt, cnt):
    for i in range(x.size):
        c = cls[i]
        is_noise = False
        for k in range(noise.size):
            if c == noise[k]:
                is_noise = True
        if is_noise:
            continue
        col = int((x[i] - x0) / res)
        row = int((y1 - y[i]) / res)
        if col < 0 or col >= nx or row < 0 or row >= ny:
            continue
        cnt[row, col] += 1
        if z[i] > dsm[row, col]:
            dsm[row, col] = z[i]
        if c == ground:
            gsum[row, col] += z[i]
            gcnt[row, col] += 1


def rasterize_laz(paths: list[Path], grid: Grid, noise_classes, ground_class, chunk: int):
    """Per-pixel max of non-noise points (DSM) and mean of ground points (DEM).

    Returns dsm, dem (NaN where empty), point count raster, classification histogram.
    """
    dsm = np.full(grid.shape, -np.inf, np.float64)
    gsum = np.zeros(grid.shape, np.float64)
    gcnt = np.zeros(grid.shape, np.int32)
    cnt = np.zeros(grid.shape, np.int32)
    hist = np.zeros(256, np.int64)
    noise = np.asarray(noise_classes, np.uint8)
    for p in paths:
        with laspy.open(p, laz_backend=laspy.LazBackend.LazrsParallel) as f:
            for pts in f.chunk_iterator(chunk):
                c = np.asarray(pts.classification, np.uint8)
                hist += np.bincount(c, minlength=256)
                _accumulate(np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z), c,
                            grid.x0, grid.y1, grid.res, grid.nx, grid.ny, noise,
                            np.uint8(ground_class), dsm, gsum, gcnt, cnt)
    dsm = np.where(np.isfinite(dsm), dsm, np.nan).astype(np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        dem = np.where(gcnt > 0, gsum / gcnt, np.nan).astype(np.float32)
    return dsm, dem, cnt, hist


def fill_holes(arr: np.ndarray, max_search_px: int = 100) -> np.ndarray:
    """Fill NaNs by inverse-distance interpolation from edges (rasterio fillnodata)."""
    valid = np.isfinite(arr)
    out = np.where(valid, arr, 0).astype(np.float32)
    return fillnodata(out, mask=valid.astype(np.uint8), max_search_distance=max_search_px,
                      smoothing_iterations=0)


def per_label(labels: np.ndarray, values: np.ndarray, n: int, fn) -> np.ndarray:
    """Apply fn to the finite values of each label 1..n (NaN where a label has none)."""
    lab, val = labels.ravel(), values.ravel()
    keep = (lab > 0) & np.isfinite(val)
    lab, val = lab[keep], val[keep]
    order = np.argsort(lab, kind="stable")
    lab, val = lab[order], val[order]
    edges = np.searchsorted(lab, np.arange(1, n + 2))
    out = np.full(n, np.nan)
    for i in range(n):
        if edges[i + 1] > edges[i]:
            out[i] = fn(val[edges[i]:edges[i + 1]])
    return out


def despike(dsm: np.ndarray, window_px: int = 5, tol_m: float = 10.0, min_support: int = 2):
    """Remove isolated high returns (birds, aircraft, mast tips): a pixel is a spike when fewer
    than `min_support` of its window neighbours are within `tol_m` below it (or higher).
    Spikes become NaN (holes) so the normal hole filling applies. Returns (dsm, n_spikes)."""
    r = window_px // 2
    ny, nx = dsm.shape
    pad = np.pad(dsm, r, constant_values=np.nan)
    support = np.zeros(dsm.shape, np.int16)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            nb = pad[r + dy:r + dy + ny, r + dx:r + dx + nx]
            support += (nb >= dsm - tol_m)
    spikes = np.isfinite(dsm) & (support < min_support)
    out = dsm.copy()
    out[spikes] = np.nan
    return out, int(spikes.sum())


def gap_fill_median(values: np.ndarray, mask: np.ndarray, valid_min: float, window_px: int = 3) -> np.ndarray:
    """Inside `mask`, pixels < valid_min take the median of the window's pixels that are inside
    `mask` and >= valid_min; pixels with no such neighbour (and all pixels >= valid_min) keep
    their value. Outside `mask` values are returned unchanged."""
    r = window_px // 2
    ny, nx = values.shape
    v = np.where(mask & (values >= valid_min), values, np.nan).astype(np.float32)
    pad = np.pad(v, r, constant_values=np.nan)
    stack = np.stack([pad[r + dy:r + dy + ny, r + dx:r + dx + nx]
                      for dy in range(-r, r + 1) for dx in range(-r, r + 1)])
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(stack, axis=0)
    gap = mask & (values < valid_min) & np.isfinite(med)
    out = values.copy()
    out[gap] = med[gap]
    return out


def fill_dsm(dsm, bmask, dem, approx_hgt_m, min_valid_share=0.2, outside_max_px=100):
    """Footprint-aware DSM hole filling (dark / specular roofs return no points).

    Holes inside a footprint take that footprint's median valid DSM; footprints with
    < min_valid_share valid pixels use DEM + approx_hgt; the rest uses fill_holes.
    Returns filled DSM and the number of footprints that used each rule.
    """
    n = len(approx_hgt_m)
    med = per_label(bmask, dsm, n, np.median)
    npx = np.bincount(bmask.ravel(), minlength=n + 1)[1:]
    nval = np.bincount(bmask.ravel(), weights=np.isfinite(dsm).ravel(), minlength=n + 1)[1:]
    use_med = nval >= np.maximum(min_valid_share * npx, 1)
    holes = ~np.isfinite(dsm) & (bmask > 0)
    b = bmask[holes] - 1
    hole_b = np.bincount(b, minlength=n) > 0
    out = dsm.copy()
    out[holes] = np.where(use_med[b], med[b], dem[holes] + np.nan_to_num(approx_hgt_m[b], nan=0.0))
    out = fill_holes(out, max_search_px=outside_max_px)
    return out, {"roof_hole_buildings_median": int((hole_b & use_med).sum()),
                 "roof_hole_buildings_approx_hgt": int((hole_b & ~use_med).sum())}


def building_mask(footprints, grid: Grid, id_col: str | None = None) -> np.ndarray:
    """Rasterise footprints (already in grid CRS). Returns int32 id raster (0 = none)."""
    ids = footprints[id_col].values if id_col else np.arange(1, len(footprints) + 1)
    shapes = ((g, int(i)) for g, i in zip(footprints.geometry, ids))
    return rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=0,
                     dtype="int32", all_touched=False)


def write_cog(path: Path, arr: np.ndarray, grid: Grid, nodata=None, scale_dm=False):
    """Write a single-band COG. scale_dm stores metres as uint16 decimetres."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if scale_dm:
        data = np.clip(np.nan_to_num(arr, nan=0) * 10, 0, 65535).round().astype(np.uint16)
        nodata = None
    else:
        data = arr
    prof = dict(driver="COG", height=grid.ny, width=grid.nx, count=1, dtype=data.dtype,
                crs=grid.crs, transform=grid.transform, nodata=nodata, compress="DEFLATE",
                predictor=2, blocksize=512, overview_resampling="NEAREST")
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(data, 1)
        if scale_dm:
            dst.update_tags(units="decimetre", scale=0.1)


def read_dm(path: Path) -> np.ndarray:
    """Read a uint16 decimetre raster back as float32 metres."""
    with rasterio.open(path) as src:
        return src.read(1).astype(np.float32) / 10
