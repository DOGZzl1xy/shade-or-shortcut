"""NOAA 9848 (Philadelphia 2022) COPC LAZ tiles: tile index and download."""

import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from coolpath.acquire import s3

STAC_KEY = "stac/noaa_copc_item_collection_m9848.json"


def tile_index(cfg: dict) -> gpd.GeoDataFrame:
    """Tile footprints (EPSG:4326) from the STAC item collection, cached in data/raw."""
    src = cfg["sources"]
    cache = cfg["paths"]["raw"] / "lidar" / "stac_items_9848.json"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        body = s3.client().get_object(Bucket=src["lidar_noaa_bucket"],
                                      Key=src["lidar_noaa_prefix"] + STAC_KEY)["Body"].read()
        cache.write_bytes(body)
    feats = json.loads(cache.read_text())["features"]
    rows = []
    for f in feats:
        x0, y0, _, x1, y1, _ = f["bbox"]
        rows.append({"id": f["id"], "count": f["properties"]["pc:count"],
                     "datetime": f["properties"]["datetime"], "geometry": box(x0, y0, x1, y1)})
    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


def tiles_for_bbox(cfg: dict, lon_min, lat_min, lon_max, lat_max) -> gpd.GeoDataFrame:
    idx = tile_index(cfg)
    return idx[idx.intersects(box(lon_min, lat_min, lon_max, lat_max))].copy()


def download_tile(cfg: dict, tile_id: str) -> Path:
    src = cfg["sources"]
    name = f"{tile_id}.laz" if not tile_id.endswith(".laz") else tile_id
    return s3.download(src["lidar_noaa_bucket"], src["lidar_noaa_prefix"] + name,
                       cfg["paths"]["raw"] / "lidar" / name)


def city_manifest(cfg: dict, area_utm) -> gpd.GeoDataFrame:
    """Tiles intersecting `area_utm` (city + 1 km), with S3 key and size; saved as GeoParquet.

    LAZ files themselves are downloaded tile by tile in phase 2 and deleted after use (D-018)."""
    src = cfg["sources"]
    idx = tile_index(cfg).to_crs(cfg["project"]["crs"])
    sel = idx[idx.intersects(area_utm)].copy()
    sizes = {o["Key"]: o["Size"] for o in s3.list_objects(src["lidar_noaa_bucket"], src["lidar_noaa_prefix"])}
    sel["key"] = src["lidar_noaa_prefix"] + sel["id"] + ".laz"
    sel["bytes"] = sel["key"].map(sizes)
    sel["url"] = f"https://{src['lidar_noaa_bucket']}.s3.amazonaws.com/" + sel["key"]
    dest = cfg["paths"]["raw"] / "lidar" / "tiles_city.parquet"
    sel.to_parquet(dest)
    return sel
