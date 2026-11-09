"""SOLWEIG runs for phase 10 (D-060), in the separate solweig env (conda-forge GDAL + PyTorch + solweig-gpu 2.0.0):

    ../envs/solweig/bin/python scripts/solweig_run.py run [key ...]   # every sub-domain, or the named ones
    ../envs/solweig/bin/python scripts/solweig_run.py merge

run: solweig_gpu.thermal_comfort on each sub-domain written by `phase_10_solweig inputs`, as one tile
(finished runs are skipped). Own met file, no UHI term, no wind coefficients (ERA5 10 m wind everywhere).
merge: crop each run to its core and mosaic onto the domain grid (data/interim/solweig/*.tif, band b = hour b-1):
  tmrt.tif        Tmrt (C) for a standing person
  utci.tif        UTCI (C) as SOLWEIG writes it: NaN where DSM - DEM >= 2 m (buildings, bridges, viaducts)
  utci_deck.tif   UTCI from Tmrt on every pixel (same polynomial and met), so bridge decks get a value
  shadow.tif      SOLWEIG shadow (1 sunlit, 0 shaded; trees pass 3 % of the beam)
  svf.tif         total sky-view factor (1 band)
Standalone: reads config.yaml with PyYAML, does not import coolpath.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import torch
import yaml
from rasterio.transform import from_origin
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load(open(ROOT / "config.yaml"))
DIR = ROOT / CFG["paths"]["interim"] / "solweig"
OUT = {"tmrt": "TMRT", "utci": "UTCI", "shadow": "Shadow"}


def out_file(key, name):
    if name == "SVF":
        return DIR / "runs" / key / "processed_inputs" / "SVF" / "SkyViewFactor_0_0.tif"
    return DIR / "runs" / key / "output_folder" / "0_0" / f"{name}_0_0.tif"


def run(keys):
    from solweig_gpu import thermal_comfort
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
    from solweig_gpu.calculate_utci import utci_calculator
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


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["run"]
    run(rest) if cmd == "run" else merge()
