"""Phase 2 QA: building heights vs approx_hgt, rule counts, canopy vs land cover / CHMv2,
DEM vs NOAA 9849, figures."""

import json

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import rasterio.windows
from matplotlib.colors import ListedColormap
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.windows import from_bounds

from coolpath import load_config
from coolpath.acquire import boundaries
from coolpath.areas import city
from coolpath.surface_city import FT, noaa_dem
from coolpath.surface import Grid

DEC = 4   # decimation for city-wide shares / maps


def read_dec(path, f=DEC, resampling=Resampling.nearest):
    with rasterio.open(path) as s:
        a = s.read(1, out_shape=(s.height // f, s.width // f), resampling=resampling)
        t = s.transform * s.transform.scale(s.width / a.shape[1], s.height / a.shape[0])
        scale = 0.1 if s.tags().get("units") == "decimetre" else 1.0
    return a.astype(np.float32) * scale, t


def main():
    cfg = load_config()
    proc, root, figs = cfg["paths"]["processed"], cfg["paths"]["interim"] / "surface", cfg["paths"]["figures"]
    crs = cfg["project"]["crs"]
    c = city(cfg)
    res = {}

    # ---- building heights vs approx_hgt (per building, phase-2 medians)
    bl = pd.concat([pd.read_parquet(p) for p in (root / "C_buildings").glob("*.parquet")]).drop_duplicates("objectid")
    b = gpd.read_parquet(cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet")[["objectid", "approx_hgt"]]
    b = b.merge(bl, on="objectid")
    b["approx_m"] = b.approx_hgt * FT
    ok = (~b.extruded) & (b.approx_hgt > 0) & b.lidar_med_m.notna()
    k = b[ok]
    res["buildings"] = {
        "n": int(len(b)), "extruded_D015": int(b.extruded.sum()), "compared": int(ok.sum()),
        "pearson_r": float(np.corrcoef(k.lidar_med_m, k.approx_m)[0, 1]),
        "spearman_r": float(k.lidar_med_m.corr(k.approx_m, method="spearman")),
        "median_err_m": float((k.lidar_med_m - k.approx_m).median()),
        "median_abs_err_m": float((k.lidar_med_m - k.approx_m).abs().median()),
    }
    st = pd.read_json(root / "C_stats.jsonl", lines=True)
    res["blocks"] = {"n": int(len(st)), "unmapped_blobs": int(st.unmapped_blobs_core.fillna(0).sum()),
                     "unmapped_km2": float(st.unmapped_px_core.sum() / 1e6),
                     "extruded_km2": float(st.extruded_px_core.sum() / 1e6),
                     "dem_px_from_noaa": int(st.dem_px_from_noaa.fillna(0).sum()),
                     "dem_px_zero_fill": int(st.dem_px_zero_fill.fillna(0).sum())}

    # ---- city-wide shares (decimated) inside the city boundary
    src, t = read_dec(proc / "building_source.tif")
    ht, _ = read_dec(proc / "surface_tree.tif")
    hb, _ = read_dec(proc / "surface_building.tif")
    lc, _ = read_dec(cfg["paths"]["raw"] / "landcover" / "landcover2018_city_1m.tif")
    chm, _ = read_dec(cfg["paths"]["raw"] / "chmv2" / "chmv2_city_1m.tif")
    inside = rasterize([(c, 1)], out_shape=src.shape, transform=t, fill=0).astype(bool)
    res["shares_in_city"] = {
        "building_footprint_lidar": float((src[inside] == 1).mean()),
        "building_footprint_approx": float((src[inside] == 2).mean()),
        "unmapped_structure": float((src[inside] == 3).mean()),
        "tree_phase2": float((ht[inside] > 0).mean()),
        "landcover_canopy": float((lc[inside] == 1).mean()),
        "chmv2_ge2m_nonbuilding": float(((chm >= 2) & (hb == 0))[inside].mean()),
        "tree_height_median_m": float(np.median(ht[inside & (ht > 0)])),
        "building_height_p99_m": float(np.percentile(hb[inside & (hb > 0)], 99)),
    }
    lcm = lc[inside & (src == 3)]
    names = {1: "tree", 2: "grass_shrub", 3: "bare", 4: "water", 5: "building", 6: "road", 7: "other_paved"}
    res["unmapped_landcover_composition"] = {names.get(int(k), str(int(k))): float((lcm == k).mean())
                                             for k in np.unique(lcm)}
    # on-bridge sidewalks (D-019) should sit on structure pixels
    fl = pd.read_parquet(cfg["paths"]["interim"] / "dvrpc_bridge_flags.parquet")
    ped = gpd.read_parquet(cfg["paths"]["raw"] / "dvrpc" / "pedestriannetwork_lines.parquet", columns=["objectid", "geometry"])
    onb = ped.merge(fl[fl.on_bridge], on="objectid").to_crs(crs)
    pts = onb.geometry.interpolate(0.5, normalized=True)
    with rasterio.open(proc / "building_source.tif") as s:
        sv = np.array([v[0] for v in s.sample(zip(pts.x, pts.y))])
    with rasterio.open(proc / "surface_building.tif") as s:
        hv = np.array([v[0] for v in s.sample(zip(pts.x, pts.y))]) / 10
    with rasterio.open(proc / "dsm.tif") as s:
        dv = np.array([v[0] for v in s.sample(zip(pts.x, pts.y))])
    with rasterio.open(proc / "dem.tif") as s:
        ev = np.array([v[0] for v in s.sample(zip(pts.x, pts.y))])
    res["on_bridge_midpoints"] = {"n": int(len(pts)), "share_structure_px": float((sv == 3).mean()),
                                  "share_any_building_px": float((hv > 0).mean()),
                                  "dsm_minus_dem_median_m": float(np.median(dv - ev))}

    # tallest structure at full resolution (decimated reads can miss single pixels)
    best = (0, None)
    with rasterio.open(proc / "surface_building.tif") as s:
        for r0 in range(0, s.height, 4096):
            w = rasterio.windows.Window(0, r0, s.width, min(4096, s.height - r0))
            a = s.read(1, window=w)
            if a.max() > best[0]:
                rr, cc = np.unravel_index(int(a.argmax()), a.shape)
                best = (int(a.max()), s.transform * (cc + 0.5, r0 + rr + 0.5))
    res["shares_in_city"]["max_building_m"] = best[0] / 10
    res["shares_in_city"]["max_building_lonlat"] = list(gpd.GeoSeries(gpd.points_from_xy(*[[v] for v in best[1]]),
                                                                       crs=crs).to_crs(4326).iloc[0].coords[0])
    # per-tract canopy cover: phase-2 trees vs CHMv2 (reference) vs land cover
    tr = boundaries.tracts(cfg).reset_index(drop=True)
    lab = rasterize(((g, i + 1) for i, g in enumerate(tr.geometry)), out_shape=src.shape, transform=t, fill=0)
    n = np.bincount(lab.ravel(), minlength=len(tr) + 1)[1:]
    cov = pd.DataFrame({
        "tree": np.bincount(lab.ravel(), (ht > 0).ravel(), len(tr) + 1)[1:] / np.maximum(n, 1),
        "chm": np.bincount(lab.ravel(), ((chm >= 2) & (hb == 0)).ravel(), len(tr) + 1)[1:] / np.maximum(n, 1),
        "lc": np.bincount(lab.ravel(), (lc == 1).ravel(), len(tr) + 1)[1:] / np.maximum(n, 1)})
    res["tract_canopy_r"] = {"tree_vs_lc": float(cov.tree.corr(cov.lc)), "tree_vs_chmv2": float(cov.tree.corr(cov.chm))}

    # ---- DEM vs NOAA 9849 on 2 km windows at random city points
    rng = np.random.default_rng(cfg["project"]["seed"])
    pts = gpd.GeoSeries(gpd.points_from_xy(*[rng.uniform(a, b_, 400) for a, b_ in
                                             zip(c.bounds[:2], c.bounds[2:])]), crs=crs)
    pts = pts[pts.within(c)].iloc[:8]
    diffs = []
    with rasterio.open(proc / "dem.tif") as s:
        for p in pts:
            g = Grid.from_bounds(p.x - 250, p.y - 250, p.x + 250, p.y + 250)
            a = s.read(1, window=from_bounds(*g.bounds, transform=s.transform))
            nd = noaa_dem(cfg, g)
            d = (a - nd)[np.isfinite(nd)]
            diffs.append(d)
    d = np.concatenate(diffs)
    res["dem_vs_noaa"] = {"windows": len(pts), "median_m": float(np.median(d)), "mad_m": float(np.median(np.abs(d))),
                          "p95_abs_m": float(np.percentile(np.abs(d), 95))}

    meta = pd.read_parquet(root / "tiles_meta.parquet")
    res["tiles"] = {"n": int(len(meta)), "points_G": float(meta.points.sum() / 1e9),
                    "laz_GB": float(meta.bytes.sum() / 1e9), "download_s_sum": float(meta.download_s.sum()),
                    "rasterise_s_sum": float(meta.rasterise_s.sum()), "empty_px_share_median": float(meta.empty_px_share.median())}
    sizes = {p.name: p.stat().st_size / 1e9 for p in proc.glob("*.tif")}
    res["outputs_GB"] = sizes
    (cfg["paths"]["tables"] / "phase_02_qa.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))

    # ---- figures
    ext = (t.c, t.c + t.a * src.shape[1], t.f + t.e * src.shape[0], t.f)
    fig, ax = plt.subplots(1, 2, figsize=(20, 10))
    im = ax[0].imshow(np.where(hb > 0, np.clip(hb, 0, 60), np.nan), cmap="magma", extent=ext, interpolation="nearest")
    plt.colorbar(im, ax=ax[0], shrink=0.6, label="building height (m, clipped at 60)")
    ax[0].set_title(f"Building height (1 m, shown at {DEC} m)")
    im = ax[1].imshow(np.where(ht > 0, np.clip(ht, 0, 30), np.nan), cmap="Greens", extent=ext, interpolation="nearest")
    plt.colorbar(im, ax=ax[1], shrink=0.6, label="tree height (m)")
    ax[1].set_title(f"Tree height (D-012); canopy share in city {res['shares_in_city']['tree_phase2']:.1%}")
    for a in ax:
        gpd.GeoSeries([c]).boundary.plot(ax=a, color="k", lw=0.6)
        a.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_02_city.png", dpi=100)

    # University City zoom: building source + trees on aerial
    x0, y0, x1, y1 = 482300, 4421500, 484700, 4423500
    with rasterio.open(proc / "building_source.tif") as s:
        w = from_bounds(x0, y0, x1, y1, transform=s.transform)
        sz = s.read(1, window=w)
    with rasterio.open(proc / "surface_tree.tif") as s:
        tz = s.read(1, window=from_bounds(x0, y0, x1, y1, transform=s.transform))
    fig, ax = plt.subplots(figsize=(14, 12))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    cx.add_basemap(ax, crs=crs, source=cx.providers.Esri.WorldImagery, zoom=16)
    lay = np.where(tz > 0, 4, sz).astype(float)
    lay[lay == 0] = np.nan
    cmap = ListedColormap(["#4361ee", "#f72585", "#ff9f1c", "#2a9d3a"])
    ax.imshow(lay, cmap=cmap, vmin=0.5, vmax=4.5, alpha=0.6, extent=(x0, x1, y0, y1), interpolation="nearest")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=c_, label=l) for c_, l in zip(
        ["#4361ee", "#f72585", "#ff9f1c", "#2a9d3a"],
        ["footprint, LiDAR height", "footprint, approx_hgt extrusion (D-015)", "unmapped structure / bridge (D-013)",
         "tree canopy (D-012)"])], loc="lower left", fontsize=11)
    ax.set_title("Phase 2 surfaces, University City (blocks shown on Esri imagery)")
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_02_ucity.png", dpi=110)


if __name__ == "__main__":
    main()
