"""Ray-marching shadow casting (numba) from sample points toward the sun.

State per point and time: 0 sun, 1 tree shade, 2 building shade (building wins).
"""

import numpy as np
from numba import njit, prange
from scipy import ndimage

SUN, TREE, BUILDING = 0, 1, 2


def local_max(top: np.ndarray, res: float, block_m: float, radius_m: float) -> np.ndarray:
    """Per-pixel upper bound of obstacle-top elevation within radius_m.

    Block maxima over block_m squares, then a max filter over the blocks within radius_m
    (plus one block to cover partial overlap), expanded back to pixels.
    """
    b = max(int(round(block_m / res)), 1)
    ny, nx = top.shape
    py, px = (-ny) % b, (-nx) % b
    t = np.pad(top, ((0, py), (0, px)), constant_values=-np.inf)
    blk = t.reshape(t.shape[0] // b, b, t.shape[1] // b, b).max(axis=(1, 3))
    k = 2 * (int(np.ceil(radius_m / (b * res))) + 1) + 1
    blk = ndimage.maximum_filter(blk, size=k, mode="nearest")
    return np.repeat(np.repeat(blk, b, 0), b, 1)[:ny, :nx].astype(np.float32)


@njit(parallel=True, cache=True)
def cast_z(px, py, z0, dem, hb, ht, lmax, x0, y1, res, az_deg, el_deg, obs_h, max_d, step, terrain):
    """Shadow state per point and time for observers at elevation z0 (m).

    px, py: point coordinates; dem: ground elevation; hb, ht: building / tree height above ground;
    lmax: local max of dem + max(hb, ht) (early termination). A pixel blocks the ray when
    ground + obstacle > ray height; buildings win over trees; the start pixel's building is
    ignored, its tree (taller than obs_h) gives tree shade. terrain=True also lets bare ground
    block the ray (recorded as BUILDING, i.e. solid / non-vegetation shade; D-031).
    Points with NaN z0 or outside the grid stay 0. Returns uint8 [n_points, n_times]."""
    n, nt = px.size, az_deg.size
    ny, nx = dem.shape
    out = np.zeros((n, nt), np.uint8)
    dx = np.sin(np.radians(az_deg))
    dy = np.cos(np.radians(az_deg))
    tn = np.tan(np.radians(el_deg))
    for i in prange(n):
        c0 = int((px[i] - x0) / res)
        r0 = int((y1 - py[i]) / res)
        if c0 < 0 or c0 >= nx or r0 < 0 or r0 >= ny or np.isnan(z0[i]):
            continue
        zmax = lmax[r0, c0]
        tree0 = ht[r0, c0] > obs_h       # under a canopy: tree shade at every hour
        for t in range(nt):
            if el_deg[t] <= 0:
                out[i, t] = BUILDING
                continue
            state = TREE if tree0 else SUN
            d = step
            while d <= max_d:
                zr = z0[i] + d * tn[t]
                if zr > zmax:
                    break
                c = int((px[i] + d * dx[t] - x0) / res)
                r = int((y1 - (py[i] + d * dy[t])) / res)
                if c < 0 or c >= nx or r < 0 or r >= ny:
                    break
                if r != r0 or c != c0:          # building in the start pixel is ignored
                    g = dem[r, c]
                    if (hb[r, c] > 0 or terrain) and g + hb[r, c] > zr:
                        state = BUILDING
                        break
                    if state == SUN and ht[r, c] > 0 and g + ht[r, c] > zr:
                        state = TREE
                d += step
            out[i, t] = state
    return out


def cast(px, py, dem, hb, ht, lmax, x0, y1, res, az_deg, el_deg, obs_h, max_d, step, terrain=False):
    """Observers at ground + obs_h (DEM sampled at each point)."""
    ny, nx = dem.shape
    c = ((px - x0) / res).astype(np.int64)
    r = ((y1 - py) / res).astype(np.int64)
    ok = (c >= 0) & (c < nx) & (r >= 0) & (r < ny)
    z0 = np.full(px.size, np.nan)
    z0[ok] = dem[r[ok], c[ok]] + obs_h
    return cast_z(px, py, z0, dem, hb, ht, lmax, x0, y1, res, az_deg, el_deg, obs_h, max_d, step, terrain)


def sample_points(edges, spacing: float):
    """Points every `spacing` m along each edge (offset spacing/2 from the start).

    edges: GeoDataFrame of undirected edges. Returns (x, y, edge_index, seg_len) arrays where
    seg_len is the length each point represents (sums to the edge length)."""
    import shapely
    geoms = edges.geometry.values
    lens = shapely.length(geoms)
    k = np.maximum(np.round(lens / spacing).astype(int), 1)
    eid = np.repeat(np.arange(len(geoms)), k)
    j = np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)
    frac = (j + 0.5) / k[eid]
    pts = shapely.line_interpolate_point(geoms[eid], frac, normalized=True)
    xy = shapely.get_coordinates(pts)
    return xy[:, 0], xy[:, 1], eid, (lens / k)[eid]
