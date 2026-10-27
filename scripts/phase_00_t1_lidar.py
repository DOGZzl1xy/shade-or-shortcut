"""T1: locate, download and read NOAA 9848 LAZ tiles covering the test area."""

import json
import time

import laspy
import matplotlib.pyplot as plt
import numpy as np

from coolpath import load_config
from coolpath.acquire import lidar
from coolpath.areas import test_area, test_area_bbox4326

ASPRS = {1: "unclassified", 2: "ground", 3: "low veg", 4: "medium veg", 5: "high veg",
         6: "building", 7: "low noise", 9: "water", 17: "bridge deck", 18: "high noise",
         20: "ignored ground", 22: "temporal exclusion"}


def main():
    cfg = load_config()
    figs = cfg["paths"]["figures"]
    sel = lidar.tiles_for_bbox(cfg, *test_area_bbox4326(cfg))
    t0 = time.time()
    paths = [lidar.download_tile(cfg, i) for i in sel.id]
    print(f"{len(paths)} tiles ready ({time.time() - t0:.0f} s)")

    headers = []
    for p in paths:
        with laspy.open(p) as f:
            h = f.header
            crs = h.parse_crs()
            area = (h.maxs[0] - h.mins[0]) * (h.maxs[1] - h.mins[1])
            headers.append({"file": p.name, "points": int(h.point_count), "size_mb": p.stat().st_size / 1e6,
                            "las_version": str(h.version), "point_format": h.point_format.id,
                            "bbox": [float(v) for v in (*h.mins[:2], *h.maxs[:2])],
                            "z_range": [float(h.mins[2]), float(h.maxs[2])],
                            "density_bbox_pts_m2": h.point_count / area})
    horiz, vert = crs.sub_crs_list
    crs_info = {"compound": crs.name, "horizontal": f"EPSG:{horiz.to_epsg()} {horiz.name}",
                "horizontal_units": horiz.axis_info[0].unit_name,
                "vertical": f"EPSG:{vert.to_epsg()} {vert.name}", "vertical_units": vert.axis_info[0].unit_name}

    # full read of the central tile for classification / return statistics
    area_c = test_area(cfg).iloc[0].centroid
    centre = min(headers, key=lambda r: (np.mean(r["bbox"][::2]) - area_c.x) ** 2 + (np.mean(r["bbox"][1::2]) - area_c.y) ** 2)
    p = next(q for q in paths if q.name == centre["file"])
    t0 = time.time()
    las = laspy.read(p, laz_backend=laspy.LazBackend.LazrsParallel)
    read_s = time.time() - t0
    cls = np.asarray(las.classification)
    codes, counts = np.unique(cls, return_counts=True)
    first = np.asarray(las.return_number) == 1
    x, y = np.asarray(las.x), np.asarray(las.y)
    x0, y0 = np.floor(x.min()), np.floor(y.min())
    nx, ny = int(np.ceil(x.max() - x0)), int(np.ceil(y.max() - y0))
    dens = np.zeros((ny, nx), np.int32)
    np.add.at(dens, (ny - 1 - (y - y0).astype(int).clip(0, ny - 1), (x - x0).astype(int).clip(0, nx - 1)), 1)
    covered = dens > 0
    result = {
        "n_tiles": len(paths), "total_points": int(sum(h["points"] for h in headers)),
        "total_size_gb": sum(h["size_mb"] for h in headers) / 1e3, "crs": crs_info,
        "acquisition_datetime_stac": sorted(set(sel.datetime.str[:10])),
        "central_tile": centre["file"], "central_read_s": read_s,
        "central_points": int(cls.size), "central_density_all_pts_m2": float(dens[covered].mean()),
        "central_density_first_returns_m2": float(first.sum() / covered.sum()),
        "central_density_median_px": float(np.median(dens[covered])),
        "central_empty_px_share": float(1 - covered.mean()),
        "classification": {int(c): {"name": ASPRS.get(int(c), "?"), "n": int(n), "share": float(n / cls.size)}
                           for c, n in zip(codes, counts)},
        "tiles": headers,
    }
    (cfg["paths"]["tables"] / "t1_lidar.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "tiles"}, indent=2))

    fig, ax = plt.subplots(1, 3, figsize=(17, 5.5))
    sel.to_crs(cfg["project"]["crs"]).boundary.plot(ax=ax[0], color="k", lw=0.7)
    for _, r in sel.to_crs(cfg["project"]["crs"]).iterrows():
        c = r.geometry.centroid
        ax[0].text(c.x, c.y, r["id"].split("_")[-1].split(".")[0], ha="center", fontsize=7)
    test_area(cfg).boundary.plot(ax=ax[0], color="red", lw=2)
    ax[0].set_title("LAZ tiles (black) vs test area (red), EPSG:32618")
    im = ax[1].imshow(np.clip(dens, 0, 60), cmap="magma", extent=(x0, x0 + nx, y0, y0 + ny))
    plt.colorbar(im, ax=ax[1], shrink=0.8, label="points / m²")
    ax[1].set_title(f"Point density, {centre['file']}")
    labels = [f"{c} {ASPRS.get(int(c), '?')}" for c in codes]
    ax[2].barh(labels, counts / cls.size * 100)
    ax[2].set_xlabel("% of points")
    ax[2].set_title("Classification codes (central tile)")
    plt.tight_layout()
    plt.savefig(figs / "t1_lidar.png", dpi=130)


if __name__ == "__main__":
    main()
