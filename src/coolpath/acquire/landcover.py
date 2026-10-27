"""PASDA PhillyLULC MapServer, layer 2 'Philadelphia Land Cover 2018' (UVM 7-class).

The MapServer only renders images, so each class is recovered from its legend colour.
Classes: 1 tree canopy, 2 grass/shrub, 3 bare soil, 4 water, 5 buildings, 6 roads, 7 other paved.
"""

import base64
import io

import numpy as np
import requests
from PIL import Image

LAYER = 2
MAX_PX = 4096


def legend_colours(url: str) -> dict[int, tuple]:
    js = requests.get(url + "/legend", params={"f": "json"}, timeout=60).json()
    lyr = next(l for l in js["layers"] if l["layerId"] == LAYER)
    out = {}
    for e in lyr["legend"]:
        img = np.asarray(Image.open(io.BytesIO(base64.b64decode(e["imageData"]))).convert("RGB"))
        out[int(e["label"])] = tuple(int(v) for v in img[img.shape[0] // 2, img.shape[1] // 2])
    return out


def _export(url, bbox, w, h, crs_epsg):
    params = {"bbox": ",".join(map(str, bbox)), "bboxSR": crs_epsg, "imageSR": crs_epsg,
              "size": f"{w},{h}", "layers": f"show:{LAYER}", "format": "png32",
              "transparent": "false", "dpi": 96, "f": "image"}
    r = requests.get(url + "/export", params=params, timeout=300)
    r.raise_for_status()
    return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))


def classes_on_grid(cfg: dict, grid) -> np.ndarray:
    """Land-cover class (0 = unknown colour) for every pixel of a 1 m grid."""
    url = cfg["sources"]["landcover_pasda"]
    colours = legend_colours(url)
    epsg = int(grid.crs.split(":")[1])
    rgb = np.zeros((*grid.shape, 3), np.uint8)
    x0, y0, x1, y1 = grid.bounds
    for r0 in range(0, grid.ny, MAX_PX):
        for c0 in range(0, grid.nx, MAX_PX):
            h, w = min(MAX_PX, grid.ny - r0), min(MAX_PX, grid.nx - c0)
            bbox = (x0 + c0 * grid.res, y1 - (r0 + h) * grid.res, x0 + (c0 + w) * grid.res, y1 - r0 * grid.res)
            rgb[r0:r0 + h, c0:c0 + w] = _export(url, bbox, w, h, epsg)
    cls = np.zeros(grid.shape, np.uint8)
    for k, c in colours.items():
        cls[np.all(rgb == np.array(c, np.uint8), axis=-1)] = k
    return cls


def city_cog(cfg: dict, grid):
    """2018 land cover classes on the city 1 m grid (uint8 COG, 0 = no data / unknown colour)."""
    from coolpath.surface import write_tiled
    dest = cfg["paths"]["raw"] / "landcover" / "landcover2018_city_1m.tif"
    if not dest.exists():
        write_tiled(dest, grid, "uint8", lambda g: classes_on_grid(cfg, g), block=MAX_PX, nodata=0,
                    tags={"classes": "1 tree,2 grass/shrub,3 bare,4 water,5 building,6 road,7 other paved",
                          "source": "PASDA PhillyLULC layer 2 (UVM 2018 Tree Canopy Assessment)"})
    return dest
