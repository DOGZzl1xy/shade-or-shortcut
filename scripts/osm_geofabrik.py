"""Fallback for the phase-1 OSM step when the Overpass API is down: the same feature groups from a
Geofabrik extract (Pennsylvania .osm.pbf), read with GDAL's OSM driver.

    uv run python -m scripts.osm_geofabrik [group ...]      # default: every group whose parquet is missing

The PBF is expected at data/raw/osm/geofabrik/pennsylvania-latest.osm.pbf (download it and its .md5 from
https://download.geofabrik.de/north-america/us/). ogr2ogr comes from the SOLWEIG conda env (GDAL with the OSM
driver) or PATH. Features are selected with the tag filters of coolpath.acquire.osm.GROUPS (any value of the key;
for corridor highway=corridor), kept when they intersect the city polygon (as osmnx.features_from_polygon does),
and written to data/raw/osm/osm_<group>.parquet with the same columns. Existing files (from Overpass) are not
overwritten. Element ids follow OSM: points = nodes, lines = ways, multipolygons = ways or relations.
"""

import shutil
import subprocess
import sys

import geopandas as gpd
import pandas as pd

from coolpath import ROOT, load_config
from coolpath.acquire import osm
from coolpath.acquire.manifest import record
from coolpath.areas import city

PBF = "pennsylvania-latest.osm.pbf"
LAYERS = ["points", "lines", "multilinestrings", "multipolygons"]


def ogr2ogr() -> str:
    p = ROOT.parent / "envs" / "solweig" / "bin" / "ogr2ogr"
    return str(p) if p.exists() else shutil.which("ogr2ogr")


def osmconf(default: str, out) -> None:
    """GDAL's osmconf.ini with every layer exposing the tags osm.KEEP needs as columns."""
    keep = ",".join(k for k in osm.KEEP)
    lines = []
    for ln in open(default):
        lines.append(f"attributes={keep}\n" if ln.startswith("attributes=") else ln)
    out.write_text("".join(lines))


def extract(cfg, gpkg):
    exe = ogr2ogr()
    default = ROOT.parent / "envs" / "solweig" / "share" / "gdal" / "osmconf.ini"
    conf = gpkg.with_suffix(".ini")
    osmconf(default, conf)
    bbox = gpd.GeoSeries([city(cfg)], crs=cfg["project"]["crs"]).to_crs(4326).total_bounds
    pbf = cfg["paths"]["raw"] / "osm" / "geofabrik" / PBF
    gpkg.unlink(missing_ok=True)
    subprocess.run([exe, "-f", "GPKG", str(gpkg), str(pbf), "-oo", f"CONFIG_FILE={conf}",
                    "-spat", *map(str, bbox), *LAYERS], check=True)


def main(groups):
    cfg = load_config()
    root = cfg["paths"]["raw"] / "osm"
    groups = groups or [g for g in osm.GROUPS if not (root / f"osm_{g}.parquet").exists()]
    if not groups:
        print("nothing missing")
        return
    gpkg = root / "geofabrik" / "philadelphia_bbox.gpkg"
    if not gpkg.exists():
        extract(cfg, gpkg)
    parts = []
    for lyr in LAYERS:
        g = gpd.read_file(gpkg, layer=lyr, on_invalid="ignore")      # a few broken OSM multipolygons
        if lyr == "points":
            g["element"], g["id"] = "node", g.osm_id
        elif lyr == "lines":
            g["element"], g["id"] = "way", g.osm_id
        elif lyr == "multilinestrings":
            g["element"], g["id"] = "relation", g.osm_id
        else:
            way = g.osm_way_id.notna()
            g["element"] = way.map({True: "way", False: "relation"})
            g["id"] = g.osm_way_id.where(way, g.osm_id)
        parts.append(g)
    allf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs)
    allf["id"] = pd.to_numeric(allf["id"]).astype("int64")
    poly = gpd.GeoSeries([city(cfg)], crs=cfg["project"]["crs"]).to_crs(allf.crs).iloc[0]
    allf = allf[allf.intersects(poly)]
    counts = {}
    for grp in groups:
        (key, val), = osm.GROUPS[grp].items()
        sel = allf[allf[key].notna()] if val is True else allf[allf[key] == val]
        cols = ["element", "id", *[c for c in osm.KEEP if c in sel.columns and sel[c].notna().any()], "geometry"]
        out = sel[cols].to_crs(cfg["project"]["crs"]).reset_index(drop=True)
        dest = root / f"osm_{grp}.parquet"
        if dest.exists():
            print(f"{grp}: {dest.name} exists (Overpass), not overwritten")
            continue
        out.to_parquet(dest)
        counts[grp] = len(out)
        print(f"{grp}: {len(out)} features -> {dest.name}  {out.element.value_counts().to_dict()}")
    if counts:
        md5 = (root / "geofabrik" / f"{PBF}.md5")
        record(cfg, "osm_geofabrik", f"https://download.geofabrik.de/north-america/us/{PBF}",
               [root / f"osm_{g}.parquet" for g in counts], counts=counts,
               pbf_md5=md5.read_text().split()[0] if md5.exists() else None,
               note="Overpass API returned 502 (at capacity) on 2026-11-09; same tag filters via GDAL OSM driver")


if __name__ == "__main__":
    main(sys.argv[1:])
