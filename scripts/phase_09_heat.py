"""Summer heat for the home page (D-059): Landsat land surface temperature and airport air temperature.

    uv run python -m scripts.phase_09_heat

- Land surface temperature: one clear Landsat 8 Collection 2 Level-2 scene (config heat.landsat_scene), band ST_B10
  read through Microsoft Planetary Computer, cloud / cloud-shadow pixels (QA_PIXEL bits 1, 3, 4) removed, clipped to
  the city boundary and warped to Web Mercator. Written as a coloured PNG for a MapLibre image layer.
- Air temperature: daily maxima at Philadelphia International Airport from NOAA ACIS (the source behind the NWS
  NOWData tool) for the scene day and for summer 2025.
Raw downloads go to data/raw/heat/; site/app/data/heat.json and lst.png are what the website reads.
"""

import json

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.features import geometry_mask
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds, transform_geom
from rasterio.windows import from_bounds
from shapely.geometry import mapping

from coolpath import ROOT, load_config
from coolpath.areas import city

OUT = ROOT / "site" / "app" / "data"
# web colours, cool to hot: deep violet, red, the site's sun orange, pale yellow
RAMP = [(0.0, (43, 29, 58)), (0.4, (184, 67, 47)), (0.7, (255, 138, 61)), (1.0, (255, 230, 160))]


def colour(v):
    """Values in 0..1 -> RGB along RAMP."""
    xs = [x for x, _ in RAMP]
    return np.stack([np.interp(v, xs, [c[i] for _, c in RAMP]) for i in range(3)], axis=-1).astype(np.uint8)


def acis(cfg, sdate, edate):
    r = requests.post(cfg["sources"]["acis_stndata"], timeout=60,
                      json={"sid": cfg["heat"]["station"], "sdate": sdate, "edate": edate, "elems": [{"name": "maxt"}], "meta": ["name"]})
    r.raise_for_status()
    return r.json()


def main():
    cfg = load_config()
    h, raw = cfg["heat"], cfg["paths"]["raw"] / "heat"
    raw.mkdir(parents=True, exist_ok=True)

    # ---- Landsat surface temperature over the city
    sid = h["landsat_scene"]
    item = requests.get(cfg["sources"]["landsat_stac"] + sid, timeout=60).json()
    token = requests.get(cfg["sources"]["landsat_sas"], timeout=60).json()["token"]
    boundary = city(cfg)
    bands = {}
    for key in ("lwir11", "qa_pixel"):
        with rasterio.open(item["assets"][key]["href"] + "?" + token) as src:
            win = from_bounds(*transform_bounds(cfg["project"]["crs"], src.crs, *boundary.buffer(300).bounds), src.transform)
            bands[key], tr, crs = src.read(1, window=win), src.window_transform(win), src.crs
    dn, qa = bands["lwir11"], bands["qa_pixel"]
    lst = dn.astype("float32") * h["lst_scale"] + h["lst_offset"] - 273.15
    bad = (dn == 0) | (((qa >> 1) & 1) | ((qa >> 3) & 1) | ((qa >> 4) & 1)).astype(bool)
    inside = ~geometry_mask([transform_geom(cfg["project"]["crs"], crs, mapping(boundary))], lst.shape, tr)
    lst[bad | ~inside] = np.nan
    with rasterio.open(raw / f"{sid}_lst_c.tif", "w", driver="GTiff", height=lst.shape[0], width=lst.shape[1], count=1,
                       dtype="float32", crs=crs, transform=tr, nodata=np.nan, compress="deflate") as dst:
        dst.write(lst, 1)
    # Web Mercator for the map image
    t3, w3, h3 = calculate_default_transform(crs, "EPSG:3857", lst.shape[1], lst.shape[0], *rasterio.transform.array_bounds(*lst.shape, tr))
    merc = np.full((h3, w3), np.nan, "float32")
    reproject(lst, merc, src_transform=tr, src_crs=crs, dst_transform=t3, dst_crs="EPSG:3857",
              resampling=Resampling.nearest, src_nodata=np.nan, dst_nodata=np.nan)
    lo, hi = h["lst_ramp_c"]
    rgba = np.zeros((h3, w3, 4), np.uint8)
    ok = np.isfinite(merc)
    rgba[..., :3] = colour(np.clip((np.nan_to_num(merc, nan=lo) - lo) / (hi - lo), 0, 1))
    rgba[..., 3] = np.where(ok, 230, 0)
    # a 64-colour palette keeps the PNG small (the ramp is smooth, the image only ~30 m detail)
    Image.fromarray(rgba, "RGBA").quantize(64, method=Image.Quantize.FASTOCTREE).save(OUT / "lst.png", optimize=True)
    west, south, east, north = transform_bounds("EPSG:3857", "EPSG:4326", *rasterio.transform.array_bounds(h3, w3, t3))
    v = lst[np.isfinite(lst)]

    # ---- airport air temperature: the scene day and summer 2025
    day = item["properties"]["datetime"][:10]
    a_day = acis(cfg, day, day)
    a_sum = acis(cfg, *h["summer"])
    (raw / "acis_phl.json").write_text(json.dumps({"scene_day": a_day, "summer": a_sum}, indent=1))
    temps = [(d, int(t)) for d, t in a_sum["data"] if t.isdigit()]
    hottest = max(temps, key=lambda x: x[1])
    out = {
        "lst": {"scene": sid, "date": day, "time_local": "11:40", "image": "lst.png",
                "coordinates": [[west, north], [east, north], [east, south], [west, south]],
                "ramp_c": [lo, hi], "ramp": [[x, "#%02x%02x%02x" % c] for x, c in RAMP],
                "median_c": round(float(np.median(v)), 1), "p95_c": round(float(np.percentile(v, 95)), 1),
                "max_c": round(float(v.max()), 1), "pixels": int(v.size)},
        "air": {"station": a_sum["meta"]["name"], "scene_day_max_f": int(a_day["data"][0][1]),
                "summer": h["summer"], "hottest_day": hottest[0], "hottest_f": hottest[1],
                "days_at_or_above": h["hot_day_f"], "hot_days": sum(t >= h["hot_day_f"] for _, t in temps),
                "days_with_data": len(temps)},
    }
    (OUT / "heat.json").write_text(json.dumps(out, indent=1))
    (cfg["paths"]["tables"] / "phase_09_heat.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: {kk: vv for kk, vv in d.items() if kk not in ("coordinates", "ramp")} for k, d in out.items()}, indent=1))


if __name__ == "__main__":
    main()
