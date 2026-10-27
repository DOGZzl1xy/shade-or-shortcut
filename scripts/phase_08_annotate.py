"""Phase 8, step 1 (CP2): build the single-file indoor annotation page for the demo area.

    uv run python -m scripts.phase_08_annotate

Writes annotate/index.html (MapLibre, opens from disk, no server). The demo-area building
footprints, OSM entrances / corridors and DVRPC sidewalks are embedded as GeoJSON; imagery is the
PASDA 2025 orthophoto (MapServer export, CORS allows file://) with Esri World Imagery as fallback.
The page exports buildings.geojson, entrances.geojson and indoor_edges.geojson (spec, phase 8).
MapLibre GL JS 4.7.1 is inlined (cached in data/raw/vendor/) so the page needs no CDN: a blocked or slow
unpkg request in <head> otherwise leaves the page blank.
"""

import json
import re

import geopandas as gpd
import shapely
from pyproj import Transformer

from coolpath import ROOT, load_config

TEMPLATE = ROOT / "scripts" / "templates" / "annotate.html"
OUT = ROOT / "annotate" / "index.html"
MAPLIBRE = "https://unpkg.com/maplibre-gl@4.7.1/dist/{}"
PAD_M = 60          # buildings / sidewalks slightly beyond the demo square are shown for context


def fc(gdf, cols):
    g = gdf[cols + ["geometry"]].to_crs(4326)
    g = g.set_geometry(shapely.set_precision(g.geometry.values, 1e-7))
    return json.loads(g.to_json(drop_id=True))


def vendor(cfg, name):
    """MapLibre file from data/raw/vendor (downloaded once), without the source-map comment."""
    import requests
    p = cfg["paths"]["raw"] / "vendor" / name
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(MAPLIBRE.format(name), timeout=120)
        r.raise_for_status()
        p.write_bytes(r.content)
    txt = ascii_cjk(p.read_text())
    assert "</script" not in txt and "</style" not in txt
    return "\n".join(line for line in txt.splitlines() if "sourceMappingURL" not in line)


def ascii_cjk(txt):
    """Write CJK and full-width characters as \\uXXXX escapes (identical in JS strings): MapLibre's vertical-punctuation
    table is the only place they occur, and the repository keeps no such characters."""
    return re.sub("[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef\ufe10-\ufe4f]", lambda m: f"\\u{ord(m.group()):04x}", txt)


def main():
    cfg = load_config()
    raw, proc, crs = cfg["paths"]["raw"], cfg["paths"]["processed"], cfg["project"]["crs"]
    c, h = cfg["demo_area"]["center"], cfg["demo_area"]["half_size_m"]
    x, y = Transformer.from_crs(4326, crs, always_xy=True).transform(c["lon"], c["lat"])
    demo = shapely.box(x - h, y - h, x + h, y + h)
    view = demo.buffer(PAD_M, join_style="mitre")

    b = gpd.read_parquet(raw / "buildings" / "buildings_city.parquet").to_crs(crs)
    b = b[b.intersects(demo)].copy()
    b["building_id"] = b.objectid.astype(int)
    b["name"] = b.building_name.fillna("")
    b["address"] = b.address.fillna("")
    b["height_m"] = (b.approx_hgt.fillna(0) * cfg["footprint_height"]["approx_hgt_unit_m"]).round(1)
    b["geometry"] = b.geometry.buffer(0)
    ent = gpd.read_parquet(raw / "osm" / "osm_entrance.parquet").to_crs(crs)
    ent = ent[ent.intersects(view)].copy()
    ent["geometry"] = ent.representative_point()
    ent["osm_id"] = ent.element + "/" + ent.id.astype(str)
    ent["entrance"] = ent.entrance.fillna("yes")
    ent["access"] = ent.access.fillna("")
    cor = gpd.read_parquet(raw / "osm" / "osm_corridor.parquet").to_crs(crs)
    cor = cor[cor.intersects(view)].copy()
    cor["name"] = cor.name.fillna("")
    cor["level"] = cor.level.fillna("")
    sw = gpd.read_parquet(proc / "graph_edges.parquet", columns=["edge_id", "kind", "covered", "geometry"])
    sw = sw[sw.intersects(view)].copy()
    sw["kind"] = sw.kind.fillna("")
    data = {"demo": json.loads(gpd.GeoSeries([demo], crs=crs).to_crs(4326).to_json(drop_id=True)),
            "center": [c["lon"], c["lat"]],
            "buildings": fc(b, ["building_id", "name", "address", "height_m"]),
            "osm_entrances": fc(ent, ["osm_id", "entrance", "access"]),
            "osm_corridors": fc(cor, ["name", "level"]),
            "sidewalks": fc(sw, ["edge_id", "kind", "covered"])}
    html = (TEMPLATE.read_text()
            .replace("/*__MAPLIBRE_CSS__*/", vendor(cfg, "maplibre-gl.css"))
            .replace("/*__MAPLIBRE_JS__*/", vendor(cfg, "maplibre-gl.js"))
            .replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html)
    stats = {"buildings": len(b), "named_buildings": int((b.name != "").sum()), "osm_entrances": len(ent),
             "osm_corridors": len(cor), "sidewalk_edges": len(sw), "html_kb": round(OUT.stat().st_size / 1024)}
    (cfg["paths"]["tables"] / "phase_08_annotate.json").write_text(json.dumps(stats, indent=2))
    print(stats)


if __name__ == "__main__":
    main()
