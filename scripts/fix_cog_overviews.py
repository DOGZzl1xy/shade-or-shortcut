"""Rebuild COG overviews with NEAREST resampling (GDAL's default cubic smears sparse heights
and mixes categorical classes). Full-resolution pixels are unchanged."""

import sys
from pathlib import Path

import rasterio
from rasterio.shutil import copy as rio_copy

for p in map(Path, sys.argv[1:]):
    tmp = p.with_suffix(".nearest.tif")
    with rasterio.open(p) as src:
        tags = src.tags()
    rio_copy(p, tmp, driver="COG", compress="DEFLATE", BIGTIFF="YES", overview_resampling="NEAREST", overviews="IGNORE_EXISTING")
    with rasterio.open(tmp) as dst:
        assert dst.tags() == tags or all(dst.tags().get(k) == v for k, v in tags.items())
    tmp.replace(p)
    print("rebuilt", p)
