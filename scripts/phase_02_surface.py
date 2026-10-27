"""Phase 2: city-wide 1 m DEM / DSM / building and tree heights.

    caffeinate -i uv run python -m scripts.phase_02_surface [A] [B] [C] [--area test|city]

Every stage is resumable (finished tiles / blocks are skipped). `--area test` runs the same
code on the phase-0 test tiles only (output under data/interim/surface_test) for validation.
"""

import argparse
import json
import shutil
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from joblib import Parallel, delayed
from rasterio.shutil import copy as rio_copy
from rasterio.windows import Window

from coolpath import load_config
from coolpath.acquire import lidar
from coolpath.areas import city, city_grid, test_area_bbox4326
from coolpath.surface import Grid
from coolpath.surface_city import mosaic_block, surface_block, tile_raster

BLOCK = 2048
MARGIN = 300

OUTPUTS = {  # name: (dtype, nodata, scale to uint16 decimetres)
    "dem": ("float32", None, False), "dsm": ("float32", None, False),
    "surface_building": ("uint16", None, True), "surface_tree": ("uint16", None, True),
    "building_source": ("uint8", None, False),
}


def setup(cfg, area):
    if area == "test":
        tiles = lidar.tiles_for_bbox(cfg, *test_area_bbox4326(cfg)).to_crs(cfg["project"]["crs"])
        grid = Grid.from_bounds(*tiles.total_bounds)
        root = cfg["paths"]["interim"] / "surface_test"
        out = root
    else:
        tiles = gpd.read_parquet(cfg["paths"]["raw"] / "lidar" / "tiles_city.parquet")
        grid = city_grid(cfg)
        root = cfg["paths"]["interim"] / "surface"
        out = cfg["paths"]["processed"]
    return tiles, grid, root, out


def open_outputs(paths: dict, grid, specs: dict, resume: bool):
    """Open tiled GTiffs for block-wise writing (r+ when resuming)."""
    handles = {}
    for name, (dtype, nodata, _) in specs.items():
        p = paths[name]
        if resume and p.exists():
            handles[name] = rasterio.open(p, "r+")
        else:
            handles[name] = rasterio.open(p, "w", driver="GTiff", height=grid.ny, width=grid.nx, count=1,
                                          dtype=dtype, crs=grid.crs, transform=grid.transform, nodata=nodata,
                                          tiled=True, blockxsize=512, blockysize=512, compress="DEFLATE",
                                          BIGTIFF="YES")
    return handles


def stage_a(cfg, tiles, root):
    tdir = root / "tiles"
    n_jobs = cfg["surface"]["max_laz_processes"]
    t0 = time.time()
    metas = Parallel(n_jobs=n_jobs, verbose=5)(delayed(tile_raster)(cfg, i, tdir) for i in tiles.id)
    m = pd.DataFrame(metas)
    m["classes"] = m["classes"].map(json.dumps)
    m.to_parquet(root / "tiles_meta.parquet")
    print(f"A: {len(m)} tiles, {m.points.sum() / 1e9:.2f} G points, {time.time() - t0:.0f} s wall")
    return m


def stage_b(cfg, tiles, grid, root):
    tdir = root / "tiles"
    paths = sorted(tdir.glob("*.tif"))
    paths = [p for p in paths if not p.name.endswith(".part.tif")]
    boxes = []
    for p in paths:
        with rasterio.open(p) as s:
            boxes.append(shapely.box(*s.bounds))
    boxes = np.array(boxes, dtype=object)
    specs = {"dsm_raw": ("float32", np.nan, False), "dem_pts": ("float32", np.nan, False)}
    files = {k: root / f"{k}.tif" for k in specs}
    done_f = root / "B.done"
    done = set(done_f.read_text().split()) if done_f.exists() else set()
    h = open_outputs(files, grid, specs, resume=bool(done))
    t0 = time.time()
    for r0, c0, hh, ww in grid.blocks(4096):
        k = f"{r0}_{c0}"
        if k in done:
            continue
        res = mosaic_block(paths, boxes, grid.sub(r0, c0, hh, ww))
        for name, arr in res.items():
            h[name].write(arr, 1, window=Window(c0, r0, ww, hh))
        with open(done_f, "a") as f:
            f.write(k + "\n")
    for x in h.values():
        x.close()
    print(f"B: mosaic {time.time() - t0:.0f} s")


def exclude_ids(cfg) -> set:
    """objectids excluded from approx_hgt extrusion after manual review (D-033)."""
    from coolpath import ROOT
    p = ROOT / cfg["footprint_height"]["exclude_csv"]
    if not p.exists():
        return set()
    d = pd.read_csv(p)
    return set(d.loc[d["exclude"].astype(bool), "objectid"].astype(int))


def _block_job(cfg, paths, grid, r0, c0, h, w, bld):
    """Window = core block + margin covering every building that touches the core."""
    core = shapely.box(*grid.sub(r0, c0, h, w).bounds)
    mx0, my0, mx1, my1 = core.buffer(MARGIN, join_style="mitre").bounds
    if len(bld):
        bx0, by0, bx1, by1 = bld.total_bounds
        mx0, my0, mx1, my1 = min(mx0, bx0 - 50), min(my0, by0 - 50), max(mx1, bx1 + 50), max(my1, by1 + 50)
    win = Grid.from_bounds(mx0, my0, mx1, my1, crs=grid.crs)
    rr = int(round((win.y1 - (grid.y1 - r0 * grid.res)) / grid.res))   # core offset inside window
    cc = int(round((grid.x0 + c0 * grid.res - win.x0) / grid.res))
    bld = bld.assign(wid=np.arange(1, len(bld) + 1))
    out, stats = surface_block(cfg, paths, win, (rr, cc, h, w), bld)
    stats.update({"r0": r0, "c0": c0, "win_px": win.nx * win.ny})
    return r0, c0, h, w, out, stats


def stage_c(cfg, grid, root, out_dir, n_jobs):
    paths = {"dsm_raw": root / "dsm_raw.tif", "dem_pts": root / "dem_pts.tif",
             "landcover": cfg["paths"]["raw"] / "landcover" / "landcover2018_city_1m.tif",
             "chmv2": cfg["paths"]["raw"] / "chmv2" / "chmv2_city_1m.tif", "city": city(cfg),
             "exclude_ids": exclude_ids(cfg)}
    bld = gpd.read_parquet(cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet").to_crs(grid.crs)
    bld = bld[bld.geometry.notna() & bld.is_valid & ~bld.is_empty][["objectid", "approx_hgt", "geometry"]]
    bld = bld.reset_index(drop=True)
    sidx = bld.sindex
    files = {k: root / f"{k}.partial.tif" for k in OUTPUTS}
    done_f = root / "C.done"
    done = set(done_f.read_text().split()) if done_f.exists() else set()
    h = open_outputs(files, grid, OUTPUTS, resume=bool(done))
    jobs = []
    for r0, c0, hh, ww in grid.blocks(BLOCK):
        if f"{r0}_{c0}" in done:
            continue
        core = shapely.box(*grid.sub(r0, c0, hh, ww).bounds)
        sel = bld.iloc[sidx.query(core, predicate="intersects")]
        jobs.append(delayed(_block_job)(cfg, paths, grid, r0, c0, hh, ww, sel))
    stats_f = root / "C_stats.jsonl"
    bdir = root / "C_buildings"
    bdir.mkdir(exist_ok=True)
    t0 = time.time()
    for r0, c0, hh, ww, out, st in Parallel(n_jobs=n_jobs, return_as="generator_unordered", verbose=5)(jobs):
        win = Window(c0, r0, ww, hh)
        for name, (dtype, _, dm) in OUTPUTS.items():
            a = out[name]
            if dm:
                a = np.clip(np.round(a * 10), 0, 65535)
            h[name].write(a.astype(dtype), 1, window=win)
        if "_buildings" in out:
            out["_buildings"].to_parquet(bdir / f"{r0}_{c0}.parquet")
        with open(stats_f, "a") as f:
            f.write(json.dumps(st) + "\n")
        with open(done_f, "a") as f:
            f.write(f"{r0}_{c0}\n")
    for name, x in h.items():
        if OUTPUTS[name][2]:
            x.update_tags(units="decimetre", scale="0.1")
        x.close()
    print(f"C: {len(jobs)} blocks {time.time() - t0:.0f} s")
    for name in OUTPUTS:
        dst = out_dir / f"{name}.tif"
        rio_copy(files[name], dst, driver="COG", compress="DEFLATE", BIGTIFF="YES",
             overview_resampling="NEAREST")
    for p in files.values():
        p.unlink()
    print("C: COGs written to", out_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stages", nargs="*", default=["A", "B", "C"])
    ap.add_argument("--area", default="city", choices=["city", "test"])
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args()
    cfg = load_config()
    tiles, grid, root, out = setup(cfg, a.area)
    root.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{a.area}: {len(tiles)} tiles, grid {grid.shape}")
    if "A" in a.stages:
        stage_a(cfg, tiles, root)
    if "B" in a.stages:
        stage_b(cfg, tiles, grid, root)
    if "C" in a.stages:
        stage_c(cfg, grid, root, out, a.jobs or cfg["surface"]["max_laz_processes"])
    print("disk free GB:", shutil.disk_usage(root).free / 1e9)


if __name__ == "__main__":
    main()
