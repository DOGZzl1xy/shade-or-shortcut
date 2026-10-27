"""ACS 5-year (Census API), OPA properties (Carto SQL) and SEPTA GTFS."""

import io
import os
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from coolpath.acquire.vector import _download


def acs(cfg: dict) -> pd.DataFrame:
    """Tract-level ACS variables listed in config `acs`. Needs CENSUS_API_KEY in the environment
    (or in a gitignored .env file at the project root)."""
    a, src = cfg["acs"], cfg["sources"]
    dest = cfg["paths"]["raw"] / "acs" / f"acs5_{a['year']}_tracts.parquet"
    if dest.exists():
        return pd.read_parquet(dest)
    key = os.environ.get("CENSUS_API_KEY") or _dotenv(cfg).get("CENSUS_API_KEY")
    if not key:
        raise RuntimeError("CENSUS_API_KEY is not set (the Census API now requires a key)")
    codes = sorted({c for v in a["variables"].values() for c in v["num"] + v.get("den", [])})
    fips = src["census_fips_county"]
    frames = []
    for i in range(0, len(codes), 45):                          # API limit: 50 variables per call
        params = {"get": ",".join(codes[i:i + 45]), "for": "tract:*",
                  "in": f"state:{fips[:2]} county:{fips[2:]}", "key": key}
        r = requests.get(src["acs_api"].format(year=a["year"]), params=params, timeout=120)
        r.raise_for_status()
        js = r.json()
        frames.append(pd.DataFrame(js[1:], columns=js[0]).set_index(["state", "county", "tract"]))
    raw = pd.concat(frames, axis=1).apply(pd.to_numeric, errors="coerce")
    raw[raw < -1e8] = pd.NA                                       # Census missing-value codes
    out = pd.DataFrame(index=raw.index)
    for name, v in a["variables"].items():
        num = raw[v["num"]].sum(axis=1, min_count=1)
        out[name] = num / raw[v["den"]].sum(axis=1, min_count=1) if v.get("den") else num
    out = out.join(raw).reset_index()
    out["GEOID"] = out.state + out.county + out.tract
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dest)
    return out


def _dotenv(cfg: dict) -> dict:
    p = cfg["paths"]["raw"].parent.parent / ".env"
    if not p.exists():
        return {}
    return dict(line.split("=", 1) for line in p.read_text().splitlines() if "=" in line and not line.startswith("#"))


def opa(cfg: dict) -> gpd.GeoDataFrame:
    """OPA property points: id, category and tract only (no owner fields)."""
    dest = cfg["paths"]["raw"] / "opa" / "opa_properties.parquet"
    if dest.exists():
        return gpd.read_parquet(dest)
    q = (f"SELECT parcel_number, category_code, category_code_description, census_tract, "
         f"ST_X(the_geom) AS lon, ST_Y(the_geom) AS lat FROM {cfg['sources']['opa_table']}")
    r = requests.get(cfg["sources"]["opa_carto_sql"], params={"q": q, "format": "csv"}, timeout=600)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), dtype={"parcel_number": str, "category_code": str, "census_tract": str})
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=4326).to_crs(cfg["project"]["crs"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(dest)
    return gdf


def gtfs(cfg: dict) -> dict[str, Path]:
    """Download SEPTA's public GTFS and unpack its nested feeds (rail, bus/trolley/subway)."""
    root = cfg["paths"]["raw"] / "gtfs"
    z = _download(cfg["sources"]["septa_gtfs"], root / "gtfs_public.zip")
    feeds = {}
    with zipfile.ZipFile(z) as zf:
        for name in zf.namelist():
            if name.endswith(".zip"):
                dest = root / Path(name).stem
                if not dest.exists():
                    with zipfile.ZipFile(io.BytesIO(zf.read(name))) as inner:
                        inner.extractall(dest)
                feeds[Path(name).stem] = dest
    return feeds
