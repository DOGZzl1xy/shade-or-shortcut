"""Meta CHMv2 (dinov3_global_chm_v2_ml3): tile lookup, acquisition dates, windowed read."""

import json

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.warp import reproject

from coolpath.acquire.vector import _download


def tiles(cfg: dict) -> gpd.GeoDataFrame:
    p = _download(cfg["sources"]["chmv2_tiles_index"], cfg["paths"]["raw"] / "chmv2" / "tiles.geojson")
    return gpd.read_file(p)


def tiles_for(cfg: dict, geom4326) -> list[str]:
    t = tiles(cfg)
    return t[t.intersects(geom4326)].tile.tolist()


def acq_dates(cfg: dict, tile: str) -> gpd.GeoDataFrame:
    dest = cfg["paths"]["raw"] / "chmv2" / f"meta_{tile}.geojson"
    if not dest.exists():
        js = requests.get(cfg["sources"]["chmv2_http_base"] + f"metadata/{tile}.geojson", timeout=120).json()
        dest.write_text(json.dumps(js))
    return gpd.read_file(dest)


def read_to_grid(cfg: dict, tile_ids: list[str], grid) -> np.ndarray:
    """Window-read CHMv2 (EPSG:3857, ~1.19 m, uint8 m) via /vsicurl/ onto the 1 m grid."""
    out = np.full(grid.shape, np.nan, np.float32)
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES")
    with rasterio.Env(**env):
        for t in tile_ids:
            part = np.full(grid.shape, np.nan, np.float32)
            with rasterio.open("/vsicurl/" + cfg["sources"]["chmv2_http_base"] + f"chm/{t}.tif") as src:
                reproject(rasterio.band(src, 1), part, src_crs="EPSG:3857", dst_transform=grid.transform,
                          dst_crs=grid.crs, resampling=Resampling.bilinear, dst_nodata=np.nan)
            out = np.where(np.isfinite(part), part, out)
    return out


def city_cog(cfg: dict, grid, tile_ids: list[str]):
    """CHMv2 on the city 1 m grid as a uint8 COG (metres; 0 also where no data). Reference only (D-012)."""
    from coolpath.surface import write_tiled
    dest = cfg["paths"]["raw"] / "chmv2" / "chmv2_city_1m.tif"
    if not dest.exists():
        write_tiled(dest, grid, "uint8", lambda g: np.nan_to_num(read_to_grid(cfg, tile_ids, g)).round().clip(0, 255),
                    tags={"units": "metre", "source": "Meta CHMv2 dinov3_global_chm_v2_ml3, CC BY 4.0"})
    return dest
