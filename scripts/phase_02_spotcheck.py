"""Phase 2 spot checks.

1. 20 random D-015 extrusions: 2022 vs 2025 PASDA orthophotos + nDSM, footprint outlined
   -> classified by eye (new / demolished / offset / other) in reports/tables/phase_02_extrusion_sample.csv
2. 5 rail bridges / viaducts over streets: DEM / DSM / NOAA 9849 profile along the sidewalk
   passing under the bridge (DEM must be the street, not the deck).
"""

import io
import json
import time

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import requests
import shapely
from PIL import Image
from rasterio.windows import from_bounds

from coolpath import load_config
from coolpath.areas import city
from coolpath.network import _bearing, load_lines
from coolpath.acquire import vector
from coolpath.surface import Grid
from coolpath.surface_city import noaa_dem

ORTHO = "https://imagery.pasda.psu.edu/arcgis/rest/services/pasda/PhiladelphiaImagery{y}/MapServer/export"


def ortho(year, bounds, px=400, tries=4, layers="show:0"):
    """PASDA orthophoto for a UTM bbox; retries when the server returns a blank image."""
    p = {"bbox": ",".join(f"{v:.1f}" for v in bounds), "bboxSR": 32618, "imageSR": 32618,
         "size": f"{px},{px}", "format": "png", "f": "image", "layers": layers, "transparent": "false"}
    for _ in range(tries):
        r = requests.get(ORTHO.format(y=year), params=p, timeout=120)
        r.raise_for_status()
        a = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        if a.std() > 5:
            return a
        time.sleep(2)
    raise RuntimeError(f"blank {year} orthophoto for {bounds}")


def window(path, bounds):
    with rasterio.open(path) as s:
        return s.read(1, window=from_bounds(*bounds, transform=s.transform), boundless=True, fill_value=0)


def extrusions(cfg, n=20):
    proc, root = cfg["paths"]["processed"], cfg["paths"]["interim"] / "surface"
    bl = pd.concat([pd.read_parquet(p) for p in (root / "C_buildings").glob("*.parquet")]).drop_duplicates("objectid")
    ext = bl[bl.extruded]
    b = gpd.read_parquet(cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet").to_crs(32618)
    b = b.merge(ext[["objectid", "lidar_med_m"]], on="objectid")
    s = b.sample(n, random_state=cfg["project"]["seed"]).reset_index(drop=True)
    figs = []
    for part in range(0, n, 5):
        fig, ax = plt.subplots(5, 3, figsize=(13, 22))
        for i in range(5):
            r = s.iloc[part + i]
            c = r.geometry.centroid
            half = max(40, 0.75 * max(r.geometry.bounds[2] - r.geometry.bounds[0], r.geometry.bounds[3] - r.geometry.bounds[1]))
            bnd = (c.x - half, c.y - half, c.x + half, c.y + half)
            nd = window(proc / "dsm.tif", bnd) - window(proc / "dem.tif", bnd)
            for j, (img, title) in enumerate([(ortho(2022, bnd), "2022 ortho"), (ortho(2025, bnd), "2025 ortho"),
                                              (np.clip(nd, 0, 30), "nDSM (LiDAR Mar 2022)")]):
                a = ax[i, j]
                a.imshow(img, extent=(bnd[0], bnd[2], bnd[1], bnd[3]), cmap="viridis" if j == 2 else None)
                gpd.GeoSeries([r.geometry]).boundary.plot(ax=a, color="yellow" if j < 2 else "red", lw=1.5)
                a.set_xlim(bnd[0], bnd[2])
                a.set_ylim(bnd[1], bnd[3])
                a.set_axis_off()
                a.set_title(f"#{part + i + 1} {title}" + (f"\n{r.address}, approx {r.approx_hgt} ft" if j == 0 else ""),
                            fontsize=9)
        plt.tight_layout()
        f = cfg["paths"]["figures"] / f"phase_02_extrusion_sample_{part // 5 + 1}.png"
        plt.savefig(f, dpi=70)
        plt.close(fig)
        figs.append(f)
    s4326 = s.to_crs(4326)
    out = pd.DataFrame({"n": range(1, n + 1), "objectid": s.objectid, "address": s.address,
                        "building_name": s.building_name, "approx_hgt_ft": s.approx_hgt,
                        "lidar_median_m": s.lidar_med_m.round(2),
                        "lon": s4326.centroid.x.round(6), "lat": s4326.centroid.y.round(6), "class": ""})
    return out, figs


def rail_crossings(cfg, k=5):
    crs = cfg["project"]["crs"]
    br = gpd.read_parquet(cfg["paths"]["raw"] / "osm" / "osm_bridge.parquet")
    rail = br[br.railway.notna() & br.highway.isna() & br.geom_type.isin(["LineString", "MultiLineString"])]
    rail = rail.explode(index_parts=False).reset_index(drop=True)
    ped = load_lines(vector.dvrpc_pedestrian(cfg), gpd.GeoSeries([city(cfg)], crs=crs), crs)
    ped = ped[ped.kind == "sidewalk"].reset_index(drop=True)
    pi, ri = shapely.STRtree(rail.geometry.values).query(ped.geometry.values, predicate="crosses")
    rows = []
    for p, r in zip(pi, ri):
        x = ped.geometry.iloc[p].intersection(rail.geometry.iloc[r])
        if x.geom_type != "Point":
            continue
        d = abs(_bearing(np.array([ped.geometry.iloc[p]]))[0] - _bearing(np.array([rail.geometry.iloc[r]]))[0]) % 180
        if min(d, 180 - d) >= 45 and ped.geometry.iloc[p].length > 120:
            rows.append({"ped": p, "rail": r, "x": x.x, "y": x.y, "name": rail.name.iloc[r]})
    cand = pd.DataFrame(rows)
    # spread over the city: farthest-point sampling, seed 42
    rng = np.random.default_rng(cfg["project"]["seed"])
    pick = [int(rng.integers(len(cand)))]
    xy = cand[["x", "y"]].to_numpy()
    while len(pick) < k:
        dmin = np.min(np.linalg.norm(xy[:, None] - xy[pick][None], axis=2), axis=1)
        pick.append(int(dmin.argmax()))
    sel = cand.iloc[pick].reset_index(drop=True)
    proc = cfg["paths"]["processed"]
    fig, ax = plt.subplots(k, 2, figsize=(14, 4 * k))
    res = []
    for i, r in sel.iterrows():
        line = ped.geometry.iloc[r.ped]
        s0 = line.project(shapely.Point(r.x, r.y))
        ss = np.arange(max(0, s0 - 60), min(line.length, s0 + 60), 1.0)
        pts = [line.interpolate(v) for v in ss]
        xs, ys = np.array([p.x for p in pts]), np.array([p.y for p in pts])
        vals = {}
        for name in ("dem", "dsm"):
            with rasterio.open(proc / f"{name}.tif") as s:
                vals[name] = np.array([v[0] for v in s.sample(zip(xs, ys))])
        g = Grid.from_bounds(xs.min() - 5, ys.min() - 5, xs.max() + 5, ys.max() + 5)
        nd = noaa_dem(cfg, g)
        vals["noaa"] = np.array([nd[int(g.y1 - y), int(x - g.x0)] for x, y in zip(xs, ys)])
        at = np.argmin(abs(ss - s0))
        ends = (ss < s0 - 30) | (ss > s0 + 30)
        lin = np.interp(ss, ss[ends], vals["dem"][ends])
        res.append({"n": i + 1, "rail": r["name"], "lonlat": list(gpd.GeoSeries.from_xy([r.x], [r.y], crs=crs).to_crs(4326).iloc[0].coords[0]),
                    "dem_at_crossing": float(vals["dem"][at]), "dem_minus_trend_at_crossing": float(vals["dem"][at] - lin[at]),
                    "dsm_minus_dem_at_crossing": float(vals["dsm"][at] - vals["dem"][at]),
                    "dem_minus_noaa_at_crossing": float(vals["dem"][at] - vals["noaa"][at]),
                    "max_abs_dem_minus_trend_within_15m": float(np.max(np.abs(vals["dem"] - lin)[abs(ss - s0) <= 15]))})
        a = ax[i, 0]
        a.plot(ss - s0, vals["dsm"], color="#aaa", label="DSM (deck / surface)")
        a.plot(ss - s0, vals["dem"], color="k", lw=2, label="DEM (phase 2)")
        a.plot(ss - s0, vals["noaa"], "--", color="tab:orange", label="NOAA 9849")
        a.axvline(0, color="r", lw=0.8)
        a.set(xlabel="distance along sidewalk from rail centreline (m)", ylabel="m NAVD88",
              title=f"#{i + 1} {r['name'] or 'rail bridge'}: DSM−DEM {res[-1]['dsm_minus_dem_at_crossing']:.1f} m, "
                    f"DEM−trend {res[-1]['dem_minus_trend_at_crossing']:+.2f} m")
        a.legend(fontsize=8)
        bnd = (r.x - 70, r.y - 70, r.x + 70, r.y + 70)
        ax[i, 1].imshow(ortho(2022, bnd), extent=(bnd[0], bnd[2], bnd[1], bnd[3]))
        gpd.GeoSeries([line]).plot(ax=ax[i, 1], color="cyan", lw=2)
        gpd.GeoSeries([rail.geometry.iloc[r.rail]]).plot(ax=ax[i, 1], color="red", lw=1, ls="--")
        ax[i, 1].set_xlim(bnd[0], bnd[2])
        ax[i, 1].set_ylim(bnd[1], bnd[3])
        ax[i, 1].set_axis_off()
    plt.tight_layout()
    plt.savefig(cfg["paths"]["figures"] / "phase_02_rail_crossings.png", dpi=90)
    return res, len(cand)


def main():
    cfg = load_config()
    out, figs = extrusions(cfg)
    dest = cfg["paths"]["tables"] / "phase_02_extrusion_sample.csv"
    if dest.exists():   # keep classifications already entered
        old = pd.read_csv(dest)
        out["class"] = out.objectid.map(dict(zip(old.objectid, old["class"]))).fillna("")
    out.to_csv(dest, index=False)
    res, n = rail_crossings(cfg)
    (cfg["paths"]["tables"] / "phase_02_rail_crossings.json").write_text(json.dumps({"candidates": n, "checked": res}, indent=2))
    print(out.to_string())
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
