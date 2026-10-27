"""OSM features via OSMnx (Overpass): covered walkways, building passages, entrances,
indoor corridors and bridges. Raw responses are cached by OSMnx under data/raw/osm/cache."""

import geopandas as gpd
import osmnx as ox

GROUPS = {
    "covered": {"covered": "yes"},
    "building_passage": {"tunnel": "building_passage"},
    "entrance": {"entrance": True},
    "corridor": {"highway": "corridor"},
    "bridge": {"bridge": True},
}
KEEP = ["name", "highway", "footway", "covered", "tunnel", "bridge", "layer", "entrance",
        "access", "level", "building", "railway"]


def features(cfg: dict, polygon4326) -> dict[str, gpd.GeoDataFrame]:
    root = cfg["paths"]["raw"] / "osm"
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(root / "cache")
    ox.settings.requests_timeout = 600
    out = {}
    for name, tags in GROUPS.items():
        dest = root / f"osm_{name}.parquet"
        if not dest.exists():
            root.mkdir(parents=True, exist_ok=True)
            g = ox.features_from_polygon(polygon4326, tags).reset_index()
            cols = ["element", "id", *[c for c in KEEP if c in g.columns], "geometry"]
            g = g[cols].to_crs(cfg["project"]["crs"])
            g.to_parquet(dest)
        out[name] = gpd.read_parquet(dest)
    return out
