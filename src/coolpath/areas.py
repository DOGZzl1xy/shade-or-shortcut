"""Study-area geometries in the project CRS."""

import geopandas as gpd
from shapely.geometry import box


def test_area(cfg: dict) -> gpd.GeoSeries:
    t = cfg["test_area"]
    g = gpd.GeoSeries([box(t["lon_min"], t["lat_min"], t["lon_max"], t["lat_max"])], crs="EPSG:4326")
    return g.to_crs(cfg["project"]["crs"])


def test_area_bbox4326(cfg: dict, buffer_deg: float = 0.0) -> tuple:
    t = cfg["test_area"]
    return (t["lon_min"] - buffer_deg, t["lat_min"] - buffer_deg,
            t["lon_max"] + buffer_deg, t["lat_max"] + buffer_deg)


def solweig_box(cfg: dict, margin: bool = True):
    """SOLWEIG domain around the demo centre (project CRS); with the shadow margin by default."""
    s, c = cfg["solweig"], cfg["demo_area"]["center"]
    p = gpd.GeoSeries.from_xy([c["lon"]], [c["lat"]], crs="EPSG:4326").to_crs(cfg["project"]["crs"]).iloc[0]
    h = s["half_size_m"] + (s["margin_m"] if margin else 0)
    x, y = round(p.x), round(p.y)
    return box(x - h, y - h, x + h, y + h)


def solweig_bbox4326(cfg: dict) -> tuple:
    return tuple(gpd.GeoSeries([solweig_box(cfg)], crs=cfg["project"]["crs"]).to_crs(4326).total_bounds)


def city(cfg: dict):
    """Philadelphia County polygon (project CRS)."""
    from coolpath.acquire.boundaries import county
    return county(cfg).geometry.iloc[0]


def city_grid(cfg: dict, buffer_m: float | None = None):
    """1 m project grid over the city boundary buffered by the ray length (spec: 1 km)."""
    from coolpath.surface import Grid
    buf = cfg["shadow"]["max_ray_m"] if buffer_m is None else buffer_m
    return Grid.from_bounds(*city(cfg).buffer(buf).bounds, res=cfg["project"]["resolution_m"],
                            crs=cfg["project"]["crs"])
