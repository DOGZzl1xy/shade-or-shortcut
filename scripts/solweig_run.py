"""SOLWEIG runs for phase 10 (D-060, D-061), in the separate solweig env (conda-forge GDAL + PyTorch + solweig-gpu 2.0.0).

University City demo (reads config.yaml; inputs from `phase_10_solweig inputs`):

    ../envs/solweig/bin/python scripts/solweig_run.py run [key ...]   # every sub-domain, or the named ones
    ../envs/solweig/bin/python scripts/solweig_run.py merge

run: solweig_gpu.thermal_comfort on each sub-domain as one tile (finished runs are skipped). Own met file, no UHI
term, no wind coefficients (ERA5 10 m wind everywhere).
merge: crop each run to its core and mosaic onto the domain grid (data/interim/solweig/*.tif, band b = hour b-1):
  tmrt.tif        Tmrt (C) for a standing person
  utci.tif        UTCI (C) as SOLWEIG writes it: NaN where DSM - DEM >= 2 m (buildings, bridges, viaducts)
  utci_deck.tif   UTCI from Tmrt on every pixel (same polynomial and met), so bridge decks get a value
  shadow.tif      SOLWEIG shadow (1 sunlit, 0 shaded; trees pass 3 % of the beam)
  svf.tif         total sky-view factor (1 band)

City (D-061; ROOT = the unpacked package from `phase_10_solweig city-package`, standalone, no config.yaml):

    python solweig_run.py city ROOT [--gpus 0,1] [--per-gpu 2] [--cpu 4] [--keys k1,k2] [--limit N]
    python solweig_run.py city-status ROOT

Each run window (core + margin) goes through two subprocesses:
  cpu stage  inputs cut from the city rasters (trees above tree_max_m dropped, no trees on buildings, land cover
             to UMEP classes with canopy filled from the nearest other class), solweig_gpu.preprocess (one tile,
             7-19 h met) and run_walls_aspect (CPU)                                      -> runs/KEY/READY
  gpu stage  calculate_svf + run_utci_tiles, then the points of the core (bridge decks: deck pixel, UTCI from its
             Tmrt; points on other building pixels: nearest pixel with UTCI), the core UTCI raster, and the run
             folder is deleted                                                            -> out/KEY.done
The scheduler keeps the CPU stage ahead of the GPU slots (--per-gpu processes on each of --gpus). State lives in
the marker files, so the run can be stopped at any time and restarted with the same command.
out/points_KEY.parquet: pid, moved_m, svf, utci_HH, tmrt_HH, shadow_HH (float32) for the met hours HH.
out/utci_KEY.tif: core UTCI, int16 in 1/utci_scale degrees, nodata -32768 (buildings), one band per met hour.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import from_bounds

OUT = {"tmrt": "TMRT", "utci": "UTCI", "shadow": "Shadow"}


# ---------------------------------------------------------------- University City demo
def demo_dir():
    import yaml
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load(open(root / "config.yaml"))
    return root / cfg["paths"]["interim"] / "solweig"


def out_file(key, name):
    d = demo_dir()
    if name == "SVF":
        return d / "runs" / key / "processed_inputs" / "SVF" / "SkyViewFactor_0_0.tif"
    return d / "runs" / key / "output_folder" / "0_0" / f"{name}_0_0.tif"


def run(keys):
    import torch
    from solweig_gpu import thermal_comfort
    DIR = demo_dir()
    man = json.loads((DIR / "runs.json").read_text())
    log = DIR / "runs_log.jsonl"
    for key in keys or sorted(man["runs"]):
        if out_file(key, "UTCI").exists():
            print("done:", key)
            continue
        d = DIR / "runs" / key
        x0, y0, x1, y1 = man["runs"][key]["run"]
        size = int(max(x1 - x0, y1 - y0))
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        thermal_comfort(str(d), man["date"], building_dsm_filename="Building_DSM.tif", dem_filename="DEM.tif",
                        trees_filename="Trees.tif", landcover_filename="Landcover.tif", ERA_5_z0_find=False,
                        tile_size=size, overlap=0, use_own_met=True, own_met_file=man["met_file"], use_uhi=False,
                        save_tmrt=True, save_shadow=True)
        rec = {"key": key, "s": round(time.time() - t0, 1), "px": size * size,
               "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
               "gpu_peak_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2) if torch.cuda.is_available() else None}
        if not man["keep_svf_intermediates"]:              # keep SkyViewFactor_0_0.tif, drop the GB-sized caches
            for f in (d / "processed_inputs" / "SVF").glob("*_0_0.*"):
                if f.suffix in (".npz", ".zip"):
                    f.unlink()
        with open(log, "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(rec, flush=True)


def write(path, arr, x0, y1, crs):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
                       dtype="float32", crs=crs, transform=from_origin(x0, y1, 1.0, 1.0), nodata=np.nan,
                       compress="DEFLATE", predictor=3, tiled=True, BIGTIFF="YES") as dst:
        dst.write(arr.astype(np.float32))


def merge():
    import torch
    from solweig_gpu.calculate_utci import utci_calculator
    DIR = demo_dir()
    man = json.loads((DIR / "runs.json").read_text())
    X0, Y0, X1, Y1 = man["domain"]
    H, W = int(Y1 - Y0), int(X1 - X0)
    met = np.loadtxt(man["met_file"], skiprows=1, delimiter=" ")
    nt = len(met)

    def mosaic(name, bands):
        a = np.full((bands, H, W), np.nan, np.float32)
        for key, r in man["runs"].items():
            cx0, cy0, cx1, cy1 = r["core"]
            with rasterio.open(out_file(key, name)) as src:
                assert src.count == bands, (name, key, src.count)
                win = from_bounds(cx0, cy0, cx1, cy1, transform=src.transform).round_offsets().round_lengths()
                r0, c0 = int(Y1 - cy1), int(cx0 - X0)
                a[:, r0:r0 + win.height, c0:c0 + win.width] = src.read(window=win)
        return a

    for name, src_name in OUT.items():
        a = mosaic(src_name, nt)
        if name == "utci":
            a[a == -999] = np.nan
        write(DIR / f"{name}.tif", a, X0, Y1, man["crs"])
        if name == "tmrt":
            deck = np.empty_like(a)
            for i in range(nt):
                tm = torch.from_numpy(a[i])
                deck[i] = utci_calculator(torch.full_like(tm, met[i, 11]), torch.full_like(tm, met[i, 10]), tm,
                                          torch.full_like(tm, max(met[i, 9], 0.15))).numpy()
            write(DIR / "utci_deck.tif", deck, X0, Y1, man["crs"])
            del deck
        print("merged", name, flush=True)
        del a
    write(DIR / "svf.tif", mosaic("SVF", 1), X0, Y1, man["crs"])
    print("merged svf")


# ---------------------------------------------------------------- city-wide run
def manifest(root):
    return json.loads((root / "runs_city.json").read_text())


def umep_landcover(lc, mapping):
    """PASDA classes -> UMEP; 0 (canopy, unknown) takes the class of the nearest pixel with a class
    (same rule as scripts/phase_10_solweig.umep_landcover)."""
    from scipy import ndimage
    lut = np.zeros(256, np.uint8)
    for k, v in mapping.items():
        lut[int(k)] = v
    u = lut[lc]
    if (u == 0).all():
        u[:] = 1
        return u
    idx = ndimage.distance_transform_edt(u == 0, return_distances=False, return_indices=True)
    return u[tuple(idx)]


def read_window(path, bounds, fill):
    """Window of a 1 m raster (boundless, `fill` outside it), in metres for decimetre rasters."""
    with rasterio.open(path) as f:
        w = from_bounds(*bounds, transform=f.transform).round_offsets().round_lengths()
        a = f.read(1, window=w, boundless=True, fill_value=fill)
        dm = f.tags().get("units") == "decimetre"
    a = a.astype(np.float32) if a.dtype != np.uint8 else a
    return a / 10 if dm else a


def city_cpu(root, key):
    from scipy import ndimage
    from solweig_gpu import preprocess, run_walls_aspect
    man = manifest(root)
    d = root / "runs" / key
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    t0 = time.time()
    b = man["runs"][key]["run"]
    rs = {k: root / "rasters" / v for k, v in man["rasters"].items()}
    dem = read_window(rs["dem"], b, np.nan)
    if np.isnan(dem).any():                             # outside the city grid: nearest ground
        idx = ndimage.distance_transform_edt(np.isnan(dem), return_distances=False, return_indices=True)
        dem = dem[tuple(idx)]
    hb = read_window(rs["building"], b, 0)
    ht = read_window(rs["tree"], b, 0)
    ht[(ht > man["tree_max_m"]) | (hb > 0)] = 0
    lc = umep_landcover(read_window(rs["landcover"], b, 0), man["landcover_umep"])
    for name, a in {"Building_DSM": dem + hb, "DEM": dem, "Trees": ht, "Landcover": lc.astype(np.float32)}.items():
        with rasterio.open(d / f"{name}.tif", "w", driver="GTiff", height=a.shape[0], width=a.shape[1], count=1,
                           dtype="float32", crs=man["crs"], transform=from_origin(b[0], b[3], 1.0, 1.0),
                           compress="DEFLATE", predictor=3, tiled=True) as dst:
            dst.write(a.astype(np.float32), 1)
    size = int(max(dem.shape))
    pdir = preprocess(str(d), man["date"], building_dsm_filename="Building_DSM.tif", dem_filename="DEM.tif",
                      trees_filename="Trees.tif", landcover_filename="Landcover.tif", tile_size=size, overlap=0,
                      use_own_met=True, own_met_file=str(root / man["met_file"]), use_uhi=False)
    t1 = time.time()
    run_walls_aspect(pdir)
    (d / "READY").write_text(json.dumps({"pdir": pdir, "inputs_s": round(t1 - t0, 1), "walls_s": round(time.time() - t1, 1)}))


def city_gpu(root, key):
    import pandas as pd
    import torch
    from scipy import ndimage
    from solweig_gpu import calculate_svf, run_utci_tiles
    from solweig_gpu.calculate_utci import utci_calculator
    man = manifest(root)
    d = root / "runs" / key
    ready = json.loads((d / "READY").read_text())
    pdir = ready["pdir"]
    o = d / "output_folder" / "0_0"
    svf_tif = Path(pdir) / "SVF" / "SkyViewFactor_0_0.tif"
    torch.cuda.reset_peak_memory_stats()
    t0 = t1 = time.time()
    if not (svf_tif.exists() and all((o / f"{v}_0_0.tif").exists() for v in OUT.values())):
        shutil.rmtree(Path(pdir) / "SVF", ignore_errors=True)     # a previous attempt died before its outputs
        shutil.rmtree(d / "output_folder", ignore_errors=True)
        calculate_svf(pdir, patch_option=2)
        t1 = time.time()
        run_utci_tiles(str(d), pdir, man["date"], save_tmrt=True, save_shadow=True)
    else:
        print("SVF and UTCI outputs found; post-processing only", flush=True)
    t2 = time.time()
    gpu_peak = torch.cuda.max_memory_allocated() / 1e9

    with rasterio.open(o / "UTCI_0_0.tif") as f:
        utci, T = f.read().astype(np.float32), f.transform
    tmrt = rasterio.open(o / "TMRT_0_0.tif").read().astype(np.float32)
    shadow = rasterio.open(o / "Shadow_0_0.tif").read().astype(np.float32)
    svf = rasterio.open(svf_tif).read(1)
    utci[utci <= -998] = np.nan
    met = np.loadtxt(root / man["met_file"], skiprows=1, delimiter=" ", ndmin=2)
    hours = [int(h) for h in met[:, 2]]
    assert utci.shape[0] == len(hours), (utci.shape, hours)

    pts = pd.read_parquet(root / "points.parquet", filters=[("key", "==", key)])
    r = np.floor((T.f - pts.y.to_numpy()) / -T.e).astype(int)
    c = np.floor((pts.x.to_numpy() - T.c) / T.a).astype(int)
    on_b = pts.on_bridge.to_numpy()
    dist, (ir, ic) = ndimage.distance_transform_edt(np.isnan(utci[0]), return_indices=True)
    nr, nc = np.where(on_b, r, ir[r, c]), np.where(on_b, c, ic[r, c])
    out = pd.DataFrame({"pid": pts.pid.to_numpy(), "moved_m": np.where(on_b, 0.0, dist[r, c]).astype(np.float32),
                        "svf": svf[nr, nc].astype(np.float32)})
    for t, h in enumerate(hours):
        u = utci[t, nr, nc]
        if on_b.any():                                  # bridge decks: UTCI from the deck pixel's Tmrt
            tm = torch.from_numpy(tmrt[t, r[on_b], c[on_b]])
            u[on_b] = utci_calculator(torch.full_like(tm, met[t, 11]), torch.full_like(tm, met[t, 10]), tm,
                                      torch.full_like(tm, max(met[t, 9], 0.15))).numpy()
        out[f"utci_{h:02d}"] = u.astype(np.float32)
        out[f"tmrt_{h:02d}"] = tmrt[t, nr, nc]
        out[f"shadow_{h:02d}"] = shadow[t, nr, nc]
    od = root / "out"
    od.mkdir(exist_ok=True)
    out.to_parquet(od / f"points_{key}.parquet", compression="zstd")

    cx0, cy0, cx1, cy1 = man["runs"][key]["core"]
    w = from_bounds(cx0, cy0, cx1, cy1, transform=T).round_offsets().round_lengths()
    core = utci[:, w.row_off:w.row_off + w.height, w.col_off:w.col_off + w.width]
    q = np.where(np.isnan(core), -32768, np.round(core * man["utci_scale"])).astype(np.int16)
    with rasterio.open(od / f"utci_{key}.tif", "w", driver="GTiff", height=q.shape[1], width=q.shape[2], count=q.shape[0],
                       dtype="int16", nodata=-32768, crs=man["crs"], transform=from_origin(cx0, cy1, 1.0, 1.0),
                       compress="DEFLATE", predictor=2, tiled=True) as dst:
        dst.write(q)
        dst.update_tags(scale=1 / man["utci_scale"], units="degC", hours=",".join(map(str, hours)))
    rec = {"key": key, **ready, "svf_s": round(t1 - t0, 1), "utci_s": round(t2 - t1, 1),
           "post_s": round(time.time() - t2, 1), "gpu_peak_gb": round(gpu_peak, 2), "points": len(out),
           "bridge_points": int(on_b.sum()), "moved_points": int((out.moved_m > 0).sum()),
           "gpu": torch.cuda.get_device_name(0), "nan_points": int(out.filter(like="utci_").isna().any(axis=1).sum())}
    shutil.rmtree(d)
    (od / f"{key}.done").write_text(json.dumps(rec))


def city_status(root, quiet=False):
    man = manifest(root)
    keys = sorted(man["runs"])
    done = [k for k in keys if (root / "out" / f"{k}.done").exists()]
    ready = [k for k in keys if (root / "runs" / k / "READY").exists() and k not in done]
    failed = sorted(p.name for p in (root / "failed").glob("*")) if (root / "failed").exists() else []
    if not quiet:
        recs = [json.loads((root / "out" / f"{k}.done").read_text()) for k in done]
        print(f"{len(done)} / {len(keys)} done, {len(ready)} ready for the GPU, {len(failed)} failed {failed[:5]}")
        if recs:
            for f in ("inputs_s", "walls_s", "svf_s", "utci_s", "post_s", "gpu_peak_gb"):
                print(f"  {f:12s} median {np.median([r[f] for r in recs]):7.1f}  max {max(r[f] for r in recs):7.1f}")
    return keys, set(done), set(ready), failed


def city(root, gpus, per_gpu, n_cpu, keys=None, limit=None):
    """Scheduler: CPU stages run ahead (at most 2 x GPU slots + n_cpu prepared), GPU stages fill the slots."""
    man = manifest(root)
    todo = keys or sorted(man["runs"])
    if limit:
        todo = todo[:limit]
    (root / "logs").mkdir(exist_ok=True)
    (root / "failed").mkdir(exist_ok=True)
    slots = [g for g in gpus for _ in range(per_gpu)]
    ahead = 2 * len(slots) + n_cpu
    running = {}                                        # Popen -> (stage, key, gpu)
    t_start, last = time.time(), 0

    def start(stage, key, gpu=None):
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="" if gpu is None else str(gpu), PYTHONUNBUFFERED="1")
        log = open(root / "logs" / f"{key}.{stage}.log", "w")
        p = subprocess.Popen([sys.executable, __file__, f"city-{stage}", str(root), key], stdout=log,
                             stderr=subprocess.STDOUT, env=env)
        running[p] = (stage, key, gpu)

    while True:
        _, done, ready, failed = city_status(root, quiet=True)
        failed_keys = {f.split(".")[0] for f in failed}
        for p, (stage, key, gpu) in list(running.items()):
            if p.poll() is not None:
                del running[p]
                if p.returncode != 0:
                    (root / "failed" / f"{key}.{stage}").write_text(str(p.returncode))
                    print(time.strftime("%H:%M"), f"FAILED {stage} {key} (logs/{key}.{stage}.log)", flush=True)
        busy = {k for _, k, _ in running.values()}
        pending = [k for k in todo if k not in done and k not in failed_keys]
        if not pending and not running:
            break
        n_cpu_run = sum(s == "cpu" for s, _, _ in running.values())
        for k in pending:
            if n_cpu_run >= n_cpu or len(ready - busy) + n_cpu_run >= ahead:
                break
            if k not in busy and k not in ready:
                start("cpu", k)
                busy.add(k)
                n_cpu_run += 1
        used = [g for s, _, g in running.values() if s == "gpu"]
        free = list(slots)
        for g in used:
            free.remove(g)
        for k in [k for k in pending if k in ready and k not in busy]:
            if not free:
                break
            start("gpu", k, free.pop(0))
            busy.add(k)
        if time.time() - last > 300:
            last = time.time()
            n = len(done & set(todo))
            el = (time.time() - t_start) / 3600
            print(time.strftime("%H:%M"), f"{n}/{len(todo)} done, {len(ready)} ready, running "
                  f"{sorted((s, k) for s, k, _ in running.values())}, {el:.1f} h", flush=True)
        time.sleep(3)
    city_status(root)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--gpus", default="0")
    ap.add_argument("--per-gpu", type=int, default=1)
    ap.add_argument("--cpu", type=int, default=2)
    ap.add_argument("--keys", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.args)
    elif a.cmd == "merge":
        merge()
    elif a.cmd == "city":
        city(Path(a.args[0]).resolve(), [int(g) for g in a.gpus.split(",")], a.per_gpu, a.cpu,
             a.keys.split(",") if a.keys else None, a.limit)
    elif a.cmd == "city-status":
        city_status(Path(a.args[0]).resolve())
    elif a.cmd == "city-cpu":
        city_cpu(Path(a.args[0]).resolve(), a.args[1])
    elif a.cmd == "city-gpu":
        city_gpu(Path(a.args[0]).resolve(), a.args[1])
    else:
        raise SystemExit(f"unknown command {a.cmd}")
