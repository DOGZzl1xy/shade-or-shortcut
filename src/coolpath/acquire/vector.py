"""Vector sources: building footprints (ArcGIS FeatureServer) and DVRPC pedestrian network."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests


def _download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    return dest


def featureserver_bbox(url: str, bbox4326: tuple, page: int = 2000) -> gpd.GeoDataFrame:
    """All features of an ArcGIS FeatureServer layer intersecting a lon/lat bbox."""
    parts, offset = [], 0
    while True:
        params = {
            "where": "1=1", "outFields": "*", "f": "geojson", "outSR": 4326,
            "geometry": ",".join(map(str, bbox4326)), "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "resultOffset": offset, "resultRecordCount": page, "orderByFields": "objectid",
        }
        r = requests.get(url.rstrip("/") + "/query", params=params, timeout=300)
        r.raise_for_status()
        js = r.json()
        feats = js.get("features", [])
        if feats:
            parts.append(gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326"))
        if len(feats) < page and not js.get("properties", {}).get("exceededTransferLimit"):
            break
        offset += len(feats)
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")


def featureserver_by_id(url: str, id_field: str = "objectid", chunk: int = 2000, workers: int = 4,
                        start_after: int = 0) -> gpd.GeoDataFrame:
    """All features of a layer, fetched by contiguous object-id ranges (offset paging can stop early)."""
    from concurrent.futures import ThreadPoolExecutor
    ids = requests.get(url.rstrip("/") + "/query", params={"where": "1=1", "returnIdsOnly": "true", "f": "json"},
                       timeout=300).json()["objectIds"]
    hi = max(ids)

    def fetch(lo):
        params = {"where": f"{id_field} > {lo} AND {id_field} <= {lo + chunk}", "outFields": "*",
                  "f": "geojson", "outSR": 4326}
        for attempt in range(5):
            try:
                r = requests.get(url.rstrip("/") + "/query", params=params, timeout=300)
                r.raise_for_status()
                feats = r.json().get("features", [])
                return gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326") if feats else None
            except (requests.RequestException, ValueError):
                if attempt == 4:
                    raise
    with ThreadPoolExecutor(workers) as ex:
        parts = [p for p in ex.map(fetch, range(start_after, hi, chunk)) if p is not None]
    gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
    assert gdf[id_field].nunique() == len(gdf)
    return gdf


def buildings_city(cfg: dict) -> gpd.GeoDataFrame:
    """Every footprint in the city layer; resumes from a partial download by object id."""
    dest = cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet"
    url = cfg["sources"]["building_footprints_service"]
    old = gpd.read_parquet(dest) if dest.exists() else None
    n_server = requests.get(url + "/query", params={"where": "1=1", "returnCountOnly": "true", "f": "json"},
                            timeout=120).json()["count"]
    if old is not None and len(old) == n_server:
        return old
    start = int(old.objectid.max()) if old is not None else 0
    new = featureserver_by_id(url, start_after=start)
    gdf = new if old is None else gpd.GeoDataFrame(pd.concat([old.to_crs(4326), new], ignore_index=True), crs=4326)
    gdf = gdf.drop_duplicates("objectid")
    dest.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(dest)
    return gdf


def buildings(cfg: dict, bbox4326: tuple, name: str) -> gpd.GeoDataFrame:
    dest = cfg["paths"]["raw"] / "buildings" / f"buildings_{name}.parquet"
    if dest.exists():
        return gpd.read_parquet(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    gdf = featureserver_bbox(cfg["sources"]["building_footprints_service"], bbox4326)
    gdf.to_parquet(dest)
    return gdf


def dvrpc_pedestrian(cfg: dict) -> Path:
    return _download(cfg["sources"]["dvrpc_ped_geoparquet"],
                     cfg["paths"]["raw"] / "dvrpc" / "pedestriannetwork_lines.parquet")
