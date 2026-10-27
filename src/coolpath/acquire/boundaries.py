"""City boundary and census tracts (TIGER/Line via pygris)."""

import geopandas as gpd
import pygris


def county(cfg: dict) -> gpd.GeoDataFrame:
    dest = cfg["paths"]["raw"] / "boundaries" / "philadelphia_county.parquet"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        fips = cfg["sources"]["census_fips_county"]
        c = pygris.counties(state=fips[:2], cb=False, year=cfg["sources"]["tiger_year"], cache=True)
        c[c.GEOID == fips].to_crs(cfg["project"]["crs"]).to_parquet(dest)
    return gpd.read_parquet(dest)


def tracts(cfg: dict) -> gpd.GeoDataFrame:
    dest = cfg["paths"]["raw"] / "boundaries" / "philadelphia_tracts.parquet"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        fips = cfg["sources"]["census_fips_county"]
        t = pygris.tracts(state=fips[:2], county=fips[2:], cb=False, year=cfg["sources"]["tiger_year"], cache=True)
        t.to_crs(cfg["project"]["crs"]).to_parquet(dest)
    return gpd.read_parquet(dest)
