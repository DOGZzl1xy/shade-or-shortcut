"""Phase 8, step 2: indoor passages in the University City demo and the three scenarios.

    uv run python -m scripts.phase_08_demo build      # demo graph + connectors + shadows (D-042)
    uv run python -m scripts.phase_08_demo trips      # three scenarios, metrics, ICI (D-043)
    uv run python -m scripts.phase_08_demo figure     # redraw the comparison figure
    uv run python -m scripts.phase_08_demo            # build + trips

Graph (D-042): phase-4 edges within the demo square + 500 m, plus the user's annotations
(data/raw/indoor). Each entrance gets a straight connector to the nearest point on the nearest
sidewalk edge, which is split there; a connector may not cross a building footprint (the next
nearest edge is tried instead); connectors > 30 m are listed for review. Connectors and the parts of
passages outside their building's footprint (open air) are sampled every 2 m and cast at the 24
times with the phase-3 algorithm; split edges keep their phase-3 sample points. Indoor parts cost
(1 - 1.5 θ) L_indoor and are open at all times.
Scenarios: outdoor only / + buildings without PennCard / + all buildings.
Trips (D-043): campus trips between demo buildings (300-1,200 m, the main sample) and the city-sample
home trips with both ends in the demo square. Metrics as phase 6, plus the Indoor Contribution
Index of Wen et al. (2025, eq. 6): ICI = (1 - L_shadiest / L_shortest,outdoor) * S_indoor / S_total.
"""

import json
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
import shapely
import shapely.ops
from matplotlib.collections import LineCollection
from pyproj import Transformer
from scipy.spatial import cKDTree

from coolpath import load_config
from coolpath.network import edge_components, node_lines
from coolpath.routing import CSR, search, theta_params, tree_totals
from coolpath.shadow import sample_points
from coolpath.transit import home_stops
from scripts.phase_02_spotcheck import ortho
from scripts.phase_03_shadow import cast_points
from scripts.phase_06_trips import length_bin

KINDS_NEW = ("connector", "indoor", "open_air")          # only in the indoor scenarios; OSM paths are in all
OSM_QUERY = ["footway", "path", "pedestrian", "steps", "living_street", "cycleway"]   # superset; filtered by config
SCEN = {"outdoor": "outdoor only", "no_penncard": "+ no-PennCard buildings", "all": "+ all buildings"}


def demo_square(cfg):
    c, h = cfg["demo_area"]["center"], cfg["demo_area"]["half_size_m"]
    x, y = Transformer.from_crs(4326, cfg["project"]["crs"], always_xy=True).transform(c["lon"], c["lat"])
    return shapely.box(x - h, y - h, x + h, y + h)


def load_indoor(cfg):
    ic, crs = cfg["indoor"], cfg["project"]["crs"]
    d = cfg["paths"]["raw"] / "indoor"
    b = gpd.read_file(d / "buildings.geojson").to_crs(crs)
    b["name"] = b.name.fillna("").str.strip()                                  # stray "\r" in source names
    b["needs_penncard"] = b.needs_penncard.astype(bool)
    for bid, v in ic["penncard_override"].items():                              # user corrections (D-042)
        b.loc[b.building_id == int(bid), "needs_penncard"] = bool(v)
    e = gpd.read_file(d / "entrances.geojson").to_crs(crs)
    p = gpd.read_file(d / "indoor_edges.geojson").to_crs(crs)
    return b.set_index("building_id"), e, p


def bearing_at(line, pos, half=2.0):
    """Local direction of `line` around position `pos` (degrees in [0, 180))."""
    a = line.interpolate(max(pos - half, 0.0))
    b = line.interpolate(min(pos + half, line.length))
    return np.degrees(np.arctan2(b.y - a.y, b.x - a.x)) % 180


def osm_paths(cfg, region, base):
    """OSM footways for the demo network (D-044): filtered, and with pieces that duplicate an existing
    edge on the same side (within dedup_m and < dedup_angle_deg) removed."""
    import osmnx as ox
    oc, crs = cfg["indoor"]["osm"], cfg["project"]["crs"]
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cfg["paths"]["raw"] / "osm" / "cache")
    ox.settings.requests_timeout = 300
    raw = cfg["paths"]["raw"] / "osm" / "osm_demo_paths.parquet"
    if not raw.exists():
        import time
        poly = gpd.GeoSeries([region], crs=crs).to_crs(4326).iloc[0]
        for k in range(5):                             # Overpass is often at capacity: back off and retry
            try:
                f = ox.features_from_polygon(poly, {"highway": OSM_QUERY}).reset_index()
                break
            except Exception as err:                   # noqa: BLE001
                if k == 4:
                    raise
                print(f"Overpass failed ({type(err).__name__}), retry in {30 * 2 ** k} s", flush=True)
                time.sleep(30 * 2 ** k)
        keep = [c for c in ("element", "id", "highway", "footway", "access", "indoor", "tunnel", "bridge", "layer",
                            "covered", "name") if c in f.columns]
        f[keep + ["geometry"]].to_parquet(raw)
    f = gpd.read_parquet(raw)
    f = f[f.highway.isin(oc["highway"]) & (f.geometry.geom_type == "LineString")].to_crs(crs)
    for c in ("footway", "access", "indoor", "tunnel", "bridge", "layer", "covered"):
        if c not in f:
            f[c] = None
    st = {"osm_ways": int(len(f))}
    drop = (f.footway.isin(oc["exclude_footway"]) | f.access.isin(oc["exclude_access"])
            | f.indoor.isin(oc["exclude_indoor"]) | f.tunnel.notna())
    st["dropped_by_tags"] = {"footway_sidewalk_crossing": int(f.footway.isin(oc["exclude_footway"]).sum()),
                             "access": int(f.access.isin(oc["exclude_access"]).sum()),
                             "indoor_or_tunnel": int((f.indoor.isin(oc["exclude_indoor"]) | f.tunnel.notna()).sum())}
    f = f[~drop].reset_index(drop=True)
    st["kept_by_tags"] = int(len(f))
    st["kept_by_tags_km"] = float(f.length.sum() / 1e3)
    # de-duplicate in pieces of <= dedup_piece_m
    btree = shapely.STRtree(base.geometry.values)
    bg = base.geometry.values
    rows = []
    for r in f.itertuples():
        g = r.geometry
        n = max(int(np.ceil(g.length / oc["dedup_piece_m"])), 1)
        cuts = np.linspace(0, g.length, n + 1)
        keep = []
        for a, b in zip(cuts[:-1], cuts[1:]):
            mid = g.interpolate((a + b) / 2)
            ob = bearing_at(g, (a + b) / 2, (b - a) / 2)
            dup = False
            for j in btree.query(mid, predicate="dwithin", distance=oc["dedup_m"]):
                diff = abs(ob - bearing_at(bg[j], bg[j].project(mid)))
                if min(diff, 180 - diff) < oc["dedup_angle_deg"]:
                    dup = True
                    break
            keep.append(not dup)
        k = 0
        while k < n:                                  # runs of kept pieces -> lines
            if not keep[k]:
                k += 1
                continue
            j = k
            while j + 1 < n and keep[j + 1]:
                j += 1
            seg = shapely.ops.substring(g, cuts[k], cuts[j + 1])
            if seg.length >= oc["min_piece_m"]:
                rows.append({"osm_id": int(r.id), "highway": r.highway, "footway": r.footway,
                             "flat": not (pd.notna(r.bridge) or (pd.notna(r.layer) and str(r.layer) not in ("0", ""))),
                             "covered": r.covered == "yes", "geometry": seg})
            k = j + 1
    o = gpd.GeoDataFrame(rows, columns=["osm_id", "highway", "footway", "flat", "covered", "geometry"], crs=crs)
    st["after_dedup_pieces"] = int(len(o))
    st["after_dedup_km"] = float(o.length.sum() / 1e3)
    return o, st


def cut_lines(df, cuts, tol=0.5):
    """Cut each line at positions (m); each piece keeps its source interval [a, b] (src_a + local)."""
    rows = []
    for i, r in enumerate(df.itertuples(index=False)):
        g = r.geometry
        ps = []
        for p_ in sorted(cuts.get(i, [])):
            if tol < p_ < g.length - tol and (not ps or p_ - ps[-1] > tol):
                ps.append(p_)
        bounds = [0.0, *ps, g.length]
        for a, b in zip(bounds[:-1], bounds[1:]):
            rows.append({**r._asdict(), "geometry": shapely.ops.substring(g, a, b), "a": r.a + a, "b": r.a + b})
    return gpd.GeoDataFrame(rows, crs=df.crs)


def nearest_valid(point, lines, tree, fp_tree, fp_in, max_d, tol, exclude=()):
    """Nearest point on `lines` (by distance, <= max_d) whose straight link from `point` crosses no footprint."""
    cand = tree.query(point.buffer(max_d))
    d = shapely.distance(lines[cand], point)
    cand, d = cand[d <= max_d], d[d <= max_d]
    for rank, j in enumerate(cand[np.argsort(d)]):
        if j in exclude:
            continue
        q = shapely.ops.nearest_points(lines[j], point)[0]
        link = shapely.LineString([point, q])
        if link.length < 1e-6 or not any(shapely.intersection(fp_in[h], link).length > tol
                                         for h in fp_tree.query(link, predicate="intersects")):
            return j, q, link, rank
    return None


def build(cfg):
    ic, oc, crs = cfg["indoor"], cfg["indoor"]["osm"], cfg["project"]["crs"]
    proc, tabs, sc = cfg["paths"]["processed"], cfg["paths"]["tables"], cfg["shadow"]
    sun = pd.read_parquet(proc / "sun.parquet")
    labels = list(sun.label)
    sq = demo_square(cfg)
    region = sq.buffer(ic["graph_buffer_m"], join_style="mitre")
    stats = {}

    # ---- base: phase-4 edges in the region; their phase-3 sample points (positions along the edge)
    e = gpd.read_parquet(proc / "graph_edges.parquet", columns=["edge_id", "kind", "covered", "on_bridge", "component", "geometry"])
    e = e[(e.component == 0) & e.intersects(region)].reset_index(drop=True)
    # phase 4 calls its links to OSM covered walkways "connector": rename so they stay in every scenario
    base = gpd.GeoDataFrame({"src": "base", "src_id": e.edge_id, "kind": e.kind.fillna("other").replace({"connector": "covered_link"}),
                             "covered": e.covered.fillna(False).astype(bool), "flat": ~e.on_bridge.fillna(False).astype(bool),
                             "building_id": -1, "a": 0.0, "geometry": e.geometry}, crs=crs)
    cols = ["edge_id", "seg_m", "x", "y"] + [f"s_{l}" for l in labels]
    pts = pq.read_table(proc / "shadow_points.parquet", columns=cols,
                        filters=[("edge_id", "in", e.edge_id.tolist())]).to_pandas()
    gi = pd.Series(np.arange(len(e)), index=e.edge_id)
    pts["pos"] = shapely.line_locate_point(e.geometry.values[gi.loc[pts.edge_id].to_numpy()], shapely.points(pts.x, pts.y))
    stats["base_edges"] = int(len(e))
    stats["base_edges_without_points"] = int((~e.edge_id.isin(pts.edge_id)).sum())    # phase-4 OSM additions

    # ---- D-044: OSM footways, attached at their ends, noded at flat crossings
    osm, stats["osm"] = osm_paths(cfg, region, base)
    osm = gpd.GeoDataFrame({"src": "osm", "src_id": osm.osm_id, "kind": "osm_path", "covered": osm.covered,
                            "flat": osm.flat, "building_id": -1, "a": 0.0, "geometry": osm.geometry}, crs=crs)
    lines = pd.concat([base, osm], ignore_index=True)
    lines = gpd.GeoDataFrame(lines, crs=crs)
    lg = lines.geometry.values
    ltree = shapely.STRtree(lg)
    fp = gpd.read_parquet(cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet", columns=["objectid", "geometry"]).to_crs(crs)
    fp = fp[fp.intersects(region)].copy()
    fp_in = fp.geometry.buffer(0).buffer(-ic["crossing_tolerance_m"]).values
    fp_tree = shapely.STRtree(fp_in)
    tol = ic["crossing_tolerance_m"]
    cuts = {}
    links = []
    n_osm = len(osm)
    oi = np.arange(len(base), len(lines))
    n_snap = n_link = n_dangle = 0
    for i in oi:
        g = lg[i]
        for end in (shapely.Point(g.coords[0]), shapely.Point(g.coords[-1])):
            res = nearest_valid(end, lg, ltree, fp_tree, fp_in, oc["attach_max_m"], tol, exclude={i})
            if res is None:
                n_dangle += 1
                continue
            j, q, link, _ = res
            cuts.setdefault(j, []).append(lg[j].project(q))
            if link.length > oc["attach_snap_m"]:
                links.append({"src": "link", "src_id": -1, "kind": "osm_link", "covered": False, "flat": True,
                              "building_id": -1, "a": 0.0, "geometry": link})
                n_link += 1
            else:
                n_snap += 1
    # flat crossings: OSM x (base or OSM), both flat
    a_idx, b_idx = ltree.query(lg[oi], predicate="crosses")
    n_cross = 0
    for ia, j in zip(oi[a_idx], b_idx):
        if ia == j or not (lines.flat.iat[ia] and lines.flat.iat[j]):
            continue
        x = shapely.intersection(lg[ia], lg[j])
        for p_ in getattr(x, "geoms", [x]):
            if p_.geom_type == "Point":
                cuts.setdefault(ia, []).append(lg[ia].project(p_))
                cuts.setdefault(j, []).append(lg[j].project(p_))
                n_cross += 1
    stats["osm_attach"] = {"ends_snapped_le_1m": n_snap, "ends_linked_1_15m": n_link, "ends_dangling": n_dangle,
                           "crossings_noded": n_cross // 2 if n_cross else 0}
    pieces = cut_lines(lines, cuts)
    pieces = pd.concat([pieces, gpd.GeoDataFrame(links, crs=crs)], ignore_index=True)
    ed, _ = node_lines(gpd.GeoDataFrame(pieces, crs=crs), oc["attach_snap_m"])
    ed["component"] = edge_components(ed)
    keep = ed.component == 0
    stats["connectivity"] = {
        "osm_km_in_main_component": float(ed.length[keep & (ed.kind == "osm_path")].sum() / 1e3),
        "osm_km_dropped_disconnected": float(ed.length[~keep & (ed.kind == "osm_path")].sum() / 1e3),
        "base_km_dropped_disconnected": float(ed.length[~keep & (ed.src == "base")].sum() / 1e3),
        "components_dropped": int(ed.component[~keep].nunique())}
    net = ed[keep].drop(columns=["u", "v", "length", "edge_id", "component"]).reset_index(drop=True)

    # ---- D-042: entrances -> nearest valid point on the network (incl. OSM paths)
    bld, ent, pas = load_indoor(cfg)
    ng = net.geometry.values
    ntree = shapely.STRtree(ng)
    cuts, con, unconnected = {}, [], []
    dropped = set(ic.get("drop_connectors", []))          # D-045: checked by the user, not walkable
    for r in ent.itertuples():
        res = None if r.entrance_id in dropped else nearest_valid(r.geometry, ng, ntree, fp_tree, fp_in,
                                                                  ic["connector_search_m"], tol)
        if res is None:
            near = shapely.distance(ng[ntree.query(r.geometry.buffer(ic["connector_search_m"]))], r.geometry)
            unconnected.append({"entrance_id": r.entrance_id, "building_id": int(r.building_id),
                                "nearest_edge_m": float(near.min()) if len(near) else np.nan,
                                "reason": "dropped by user (D-045)" if r.entrance_id in dropped else "every link crosses a footprint"})
            continue
        j, q, link, rank = res
        cuts.setdefault(j, []).append(ng[j].project(q))
        con.append({"entrance_id": r.entrance_id, "building_id": int(r.building_id), "rank": rank,
                    "target_kind": net.kind.iat[j], "length": link.length, "geometry": link})
    con = gpd.GeoDataFrame(con, crs=crs)
    net = cut_lines(net, cuts)
    conl = gpd.GeoDataFrame({"src": "connector", "src_id": -1, "kind": "connector", "covered": False, "flat": True,
                             "building_id": con.building_id, "a": 0.0, "geometry": con.geometry}, crs=crs)
    # passages: indoor parts and open-air parts (outside the building footprint)
    prow, open_air = [], 0.0
    for r in pas.itertuples():
        g, foot = r.geometry, bld.loc[r.building_id].geometry
        outside = g.difference(foot)
        parts = [p_ for p_ in getattr(outside, "geoms", [outside]) if not p_.is_empty and p_.length > ic["open_air_min_m"]]
        iv = [tuple(sorted((g.project(shapely.Point(p_.coords[0])), g.project(shapely.Point(p_.coords[-1]))))) for p_ in parts]
        bps = sorted({0.0, g.length, *[x for ab in iv for x in ab]})
        for a, b in zip(bps[:-1], bps[1:]):
            if b - a < 1e-6:
                continue
            seg = shapely.ops.substring(g, a, b)
            kind = "open_air" if any(lo - 1e-6 <= (a + b) / 2 <= hi + 1e-6 for lo, hi in iv) else "indoor"
            open_air += seg.length if kind == "open_air" else 0.0
            prow.append({"src": "passage", "src_id": -1, "kind": kind, "covered": False, "flat": True,
                         "building_id": int(r.building_id), "passage_id": r.edge_id, "a": 0.0, "geometry": seg})
    allp = gpd.GeoDataFrame(pd.concat([net, conl, gpd.GeoDataFrame(prow, crs=crs)], ignore_index=True), crs=crs)
    edges, nodes = node_lines(allp, oc["attach_snap_m"])

    # ---- shade per edge: base pieces from their phase-3 points; everything else outdoor is cast now
    is_base = (edges.src == "base").to_numpy()
    has_pts = edges.src_id.isin(pts.edge_id).to_numpy() & is_base
    for lab in labels:
        for k in ("sun", "tree", "bldg", "indoor"):
            edges[f"L_{k}_{lab}"] = 0.0
    # (a) base pieces with phase-3 points: points whose position falls in the piece interval
    P = pts.merge(edges.loc[has_pts, ["src_id", "a", "b"]].reset_index().rename(columns={"index": "row"}),
                  left_on="edge_id", right_on="src_id")
    P = P[(P.pos >= P.a) & (P.pos < P.b) | ((P.pos == P.b) & np.isclose(P.b, P.groupby("edge_id").b.transform("max")))]
    P = P.drop_duplicates(["edge_id", "pos", "x", "y"])
    rowsum = np.bincount(P.row, weights=P.seg_m, minlength=len(edges))
    for lab in labels:
        s = P[f"s_{lab}"].to_numpy()
        for k, name in enumerate(("sun", "tree", "bldg")):
            v = np.bincount(P.row, weights=P.seg_m * (s == k), minlength=len(edges))
            edges[f"L_{name}_{lab}"] = np.where(has_pts, v * edges.length / np.maximum(rowsum, 1e-9), edges[f"L_{name}_{lab}"])
    # pieces too short to hold a point: per-metre shares of the whole source edge
    tab = pd.read_parquet(proc / "edges_shade.parquet").set_index("edge_id")
    empty = has_pts & (rowsum == 0)
    for lab in labels:
        t = tab.loc[edges.src_id[empty]]
        tot = np.maximum(t[f"L_sun_{lab}"] + t[f"L_tree_{lab}"] + t[f"L_bldg_{lab}"], 1e-9).to_numpy()
        for name in ("sun", "tree", "bldg"):
            edges.loc[empty, f"L_{name}_{lab}"] = t[f"L_{name}_{lab}"].to_numpy() / tot * edges.length[empty].to_numpy()
    # (b) cast: OSM paths and links, entrance connectors, open-air parts, base edges without points
    cast = ((~is_base | ~has_pts) & (edges.kind != "indoor")).to_numpy()
    px, py, ei, seg = sample_points(edges[cast], sc["sample_spacing_m"])
    with rasterio.open(proc / "dem.tif") as s_:
        z0 = np.array([v[0] for v in s_.sample(zip(px, py))], np.float64) + sc["observer_height_m"]
    states, _ = cast_points(cfg, px, py, z0, sun.azimuth.to_numpy(), sun.elevation.to_numpy(), log=False)
    rows_c = np.where(cast)[0]
    for t, lab in enumerate(labels):
        for k, name in enumerate(("sun", "tree", "bldg")):
            v = np.bincount(ei, weights=seg * (states[:, t] == k), minlength=cast.sum())
            edges.loc[cast, f"L_{name}_{lab}"] = v
    for lab in labels:
        cov = edges.covered.astype(bool).to_numpy()
        edges.loc[cov, f"L_sun_{lab}"] = 0.0
        edges.loc[cov, f"L_tree_{lab}"] = 0.0
        edges.loc[cov, f"L_bldg_{lab}"] = edges.length[cov]
        ind = (edges.kind == "indoor").to_numpy()
        edges.loc[ind, f"L_indoor_{lab}"] = edges.length[ind]
    edges["building_id"] = edges.building_id.fillna(-1).astype(int)
    edges["needs_penncard"] = edges.building_id.map(bld.needs_penncard).fillna(False).astype(bool)
    edges["component"] = edge_components(edges)
    stats["final_components"] = edges.groupby("component").length.sum().round(1).head(5).to_dict()
    edges.to_parquet(proc / "demo_edges.parquet")
    nodes.drop(columns="geometry").rename_axis("node").reset_index().to_parquet(proc / "demo_nodes.parquet")
    con.to_parquet(proc / "demo_connectors.parquet")

    long = con[con.length > ic["connector_flag_m"]].copy()
    long["name"] = long.building_id.map(bld.name)
    long[["entrance_id", "building_id", "name", "length", "rank", "target_kind"]].round(1) \
        .sort_values("length", ascending=False).to_csv(tabs / "phase_08_long_connectors.csv", index=False)
    unc = pd.DataFrame(unconnected, columns=["entrance_id", "building_id", "nearest_edge_m", "reason"])
    unc["name"] = unc.building_id.map(bld.name)
    unc.round(1).to_csv(tabs / "phase_08_unconnected_entrances.csv", index=False)
    pen = bld.needs_penncard
    stats.update({
        "edges": int(len(edges)), "nodes": int(len(nodes)),
        "km_by_kind": edges.groupby("kind").length.sum().div(1e3).round(3).to_dict(),
        "entrances": int(len(ent)), "connectors": int(len(con)),
        "connector_m": con.length.describe(percentiles=[.5, .9]).round(1).to_dict(),
        "connectors_to_osm_path": int((con.target_kind == "osm_path").sum()),
        "connectors_not_nearest_edge": int((con["rank"] > 0).sum()),
        "connectors_over_30m": int(len(long)), "entrances_unconnected": int(len(unc)),
        "passages": int(len(pas)), "indoor_km": float(edges.length[edges.kind == "indoor"].sum() / 1e3),
        "open_air_m": round(open_air, 1), "cast_points": int(len(px)),
        "buildings": {"total": int(len(bld)), "needs_penncard": int(pen.sum()), "no_penncard": int((~pen).sum())}})
    (tabs / "phase_08_build.json").write_text(json.dumps(stats, indent=2, default=float))
    print(json.dumps(stats, indent=2, default=float))
    connector_figure(cfg, edges, con, long, bld, sq, ent[ent.entrance_id.isin(unc.entrance_id)])
    return stats


def connector_figure(cfg, allv, con, long, bld, sq, unc):
    figs = cfg["paths"]["figures"]
    b = sq.bounds
    fig, ax = plt.subplots(1, 2, figsize=(22, 11))
    img = ortho(2025, b, 1600, layers="show:3")
    ax[0].imshow(img, extent=(b[0], b[2], b[1], b[3]))
    gpd.GeoSeries(bld.geometry).boundary.plot(ax=ax[0], color="w", lw=0.8)
    side = allv[allv.kind.isin(["sidewalk", "connector"]) | ~allv.kind.isin(KINDS_NEW)]
    side[~side.kind.isin(KINDS_NEW)].plot(ax=ax[0], color="#74c0fc", lw=0.6)
    ind = allv[allv.kind == "indoor"]
    ind.plot(ax=ax[0], color=np.where(ind.needs_penncard, "#ff8a4c", "#51cf66"), lw=2.5)
    allv[allv.kind == "open_air"].plot(ax=ax[0], color="#ffd43b", lw=3)
    con.plot(ax=ax[0], color=np.where(con.length > cfg["indoor"]["connector_flag_m"], "#e03131", "#ffffff"), lw=2)
    if len(unc):
        unc.plot(ax=ax[0], color="#e03131", marker="x", markersize=60)
    ax[0].set_xlim(b[0], b[2]); ax[0].set_ylim(b[1], b[3]); ax[0].set_axis_off()
    ax[0].set_title("Indoor passages (green no PennCard / orange PennCard), open-air parts (yellow),\n"
                    f"connectors (white; red > {cfg['indoor']['connector_flag_m']} m), unconnected entrances (red ×), sidewalks (blue)")
    # zoom panel: long connectors
    if len(long):
        u = shapely.union_all(long.geometry.values).buffer(60).bounds
        h = max(u[2] - u[0], u[3] - u[1]) / 2
        cx, cy = (u[0] + u[2]) / 2, (u[1] + u[3]) / 2
        v = (cx - h, cy - h, cx + h, cy + h)
        ax[1].imshow(ortho(2025, v, 1200, layers="show:3"), extent=(v[0], v[2], v[1], v[3]))
        gpd.GeoSeries(bld.geometry).boundary.plot(ax=ax[1], color="w", lw=1)
        allv[~allv.kind.isin(KINDS_NEW)].plot(ax=ax[1], color="#74c0fc", lw=1)
        long.plot(ax=ax[1], color="#e03131", lw=3)
        for r in long.itertuples():
            x, y = r.geometry.coords[0]
            ax[1].annotate(f"{r.entrance_id} {r.length:.0f} m", (x, y), color="w", fontsize=9, weight="bold")
        ax[1].set_xlim(v[0], v[2]); ax[1].set_ylim(v[1], v[3])
        ax[1].set_title(f"Connectors > {cfg['indoor']['connector_flag_m']} m ({len(long)}) for review")
    ax[1].set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_08_connectors.png", dpi=90)
    plt.close(fig)


def load_demo(cfg):
    proc = cfg["paths"]["processed"]
    e = gpd.read_parquet(proc / "demo_edges.parquet")
    nodes = pd.read_parquet(proc / "demo_nodes.parquet").set_index("node")
    ids, inv = np.unique(np.r_[e.u.to_numpy(), e.v.to_numpy()], return_inverse=True)
    m = len(e)
    e["ui"], e["vi"] = inv[:m], inv[m:]
    xy = nodes.loc[ids]
    return e, ids, xy.x.to_numpy(), xy.y.to_numpy()


def scenario_graph(e, n, mask):
    sub = e[mask]
    g = CSR(sub.ui.to_numpy(), sub.vi.to_numpy(), n)
    return g, sub


def trips(cfg):
    ic, rc = cfg["indoor"], cfg["routing"]
    proc, figs, tabs = cfg["paths"]["processed"], cfg["paths"]["figures"], cfg["paths"]["tables"]
    labels = list(pd.read_parquet(proc / "sun.parquet").label)
    tp, tf, fac, inf = theta_params(cfg), rc["tree_factor"], rc["search_radius_factor"], rc["indoor_factor"]
    rng = np.random.default_rng(cfg["project"]["seed"])
    e, ids, nx_, ny_ = load_demo(cfg)
    n = len(ids)
    sq = demo_square(cfg)
    is_new = e.kind.isin(KINDS_NEW)
    masks = {"outdoor": ~is_new, "no_penncard": ~is_new | ~e.needs_penncard, "all": np.ones(len(e), bool)}
    G = {k: scenario_graph(e, n, np.asarray(m, bool)) for k, m in masks.items()}
    # endpoints snap only to sidewalk nodes (not to entrances or passage vertices)
    side = e[~is_new]
    sn = np.unique(np.r_[side.ui, side.vi])
    kd = cKDTree(np.c_[nx_[sn], ny_[sn]])
    snap = lambda x, y: sn[kd.query(np.c_[x, y])[1]]  # noqa: E731

    # ---- trip sets (D-043)
    go, so = G["outdoor"]
    zero = np.zeros(len(so))
    Lo = so.length.to_numpy()
    fp = gpd.read_parquet(cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet", columns=["objectid", "geometry"]) \
        .to_crs(cfg["project"]["crs"])
    fp = fp[fp.intersects(sq)].reset_index(drop=True)
    rp = fp.representative_point()
    fp["node"] = snap(rp.x, rp.y)
    lo, hi = ic["campus_trip_range_m"]
    orig = rng.choice(len(fp), ic["campus_origins"], replace=False)
    T = []
    for i in orig:
        o = int(fp.node[i])
        _, d, _, _ = search(go, Lo, zero, zero, zero, o, "shortest", max_dist=hi)
        dd = d[fp.node.to_numpy()]
        ok = np.where((dd >= lo) & (dd <= hi))[0]
        for j in rng.choice(ok, min(ic["campus_dest_per_origin"], len(ok)), replace=False):
            T.append(("campus", f"C{i}_{j}", o, int(fp.node[j]), float(dd[j])))
    # station sample: rapid-transit stations near the demo -> every demo building within 1.2 km
    hs = home_stops(cfg)
    rap = hs[(hs["mode"] == "rapid") & (hs.distance(sq) <= ic["station_buffer_m"])].copy()
    rap["node"] = snap(rap.geometry.x, rap.geometry.y)
    bn = fp.node.to_numpy()
    st_list = []
    for name, grp in rap.groupby("station"):
        D = np.vstack([search(go, Lo, zero, zero, zero, int(o), "shortest", max_dist=ic["station_trip_max_m"])[1][bn]
                       for o in grp.node])
        best = D.argmin(axis=0)
        dmin = D[best, np.arange(len(bn))]
        ok = np.where(dmin <= ic["station_trip_max_m"])[0]
        st_list.append({"station": name, "platforms": int(len(grp)), "buildings": int(len(ok))})
        for j in ok:
            T.append(("station", f"S{name}|{j}", int(grp.node.iloc[best[j]]), int(bn[j]), float(dmin[j])))
    (tabs / "phase_08_station_sample.json").write_text(json.dumps(st_list, indent=2))
    par = pd.read_parquet(proc / "trips_city_parcels.parquet")
    st = hs.set_index("stop_id")
    sx, sy = st.geometry.x.reindex(par.stop_id).to_numpy(), st.geometry.y.reindex(par.stop_id).to_numpy()
    inq = shapely.contains_xy(sq, par.x, par.y) & shapely.contains_xy(sq, sx, sy)
    h = par[inq]
    for i, o, d_ in zip(h.index, snap(sx[inq], sy[inq]), snap(h.x, h.y)):
        T.append(("home", f"P{i}", int(o), int(d_), np.nan))
    trips_ = pd.DataFrame(T, columns=["set", "trip_id", "o", "d", "dist0"])
    trips_.to_parquet(proc / "demo_trips.parquet")

    # ---- routing: per scenario, per origin, shortest once + shade per time (dynamic θ)
    rows = []
    for sname, (g, sub) in G.items():
        L = sub.length.to_numpy()
        ind = {lab: sub[f"L_indoor_{lab}"].to_numpy() for lab in labels}
        for o, grp in trips_.groupby("o"):
            dests = grp.d.to_numpy(np.int64)
            _, d0, p0, e0 = search(g, L, np.zeros_like(L), np.zeros_like(L), np.zeros_like(L), int(o), "shortest", max_dist=5000)
            rad = np.nanmax(d0[dests]) * fac + 1
            for lab in labels:
                ls, lt, lb = (sub[f"L_{k}_{lab}"].to_numpy() for k in ("sun", "tree", "bldg"))
                _, _, p1, e1 = search(g, L, ls, lt, lb, int(o), "shade", tp=tp, tree_factor=tf, max_dist=rad,
                                      l_indoor=ind[lab], indoor_factor=inf)
                A = tree_totals(p0, e0, o, dests, L, ls, lt, lb, tp, tf, -1.0, e0, ind[lab], inf)
                B = tree_totals(p1, e1, o, dests, L, ls, lt, lb, tp, tf, -1.0, e0, ind[lab], inf)
                for k, tid in enumerate(grp.trip_id):
                    rows.append((sname, tid, lab, *A[k, [0, 1, 2, 3, 6, 4]], *B[k, [0, 1, 2, 3, 6, 4]], B[k, 5]))
    cols = ["scenario", "trip_id", "time"] + [f"{p}_{k}" for p in ("short", "shade") for k in ("len", "sun", "tree", "bldg", "indoor", "C")] + ["same_path"]
    df = pd.DataFrame(rows, columns=cols).merge(trips_[["set", "trip_id"]], on="trip_id")
    out0 = df[df.scenario == "outdoor"][["trip_id", "time", "short_len", "short_sun", "short_C"]] \
        .rename(columns={"short_len": "out_short_len", "short_sun": "out_short_sun", "short_C": "out_short_C"})
    df = df.merge(out0, on=["trip_id", "time"])
    df = df[df.short_len > 0].copy()
    df["length_bin"] = length_bin(df.out_short_len, cfg)
    df["detour"] = (df.shade_len - df.short_len) / df.short_len
    df["sun_red"] = (df.short_sun - df.shade_sun) / df.short_sun.where(df.short_sun > 0)
    df["exposure_red"] = (df.short_C - df.shade_C) / df.short_C.where(df.short_C > 0)
    df["sun_red_vs_outdoor"] = (df.out_short_sun - df.shade_sun) / df.out_short_sun.where(df.out_short_sun > 0)
    df["len_vs_outdoor"] = df.shade_len / df.out_short_len - 1
    df["exposure_red_vs_outdoor"] = (df.out_short_C - df.shade_C) / df.out_short_C.where(df.out_short_C > 0)
    shade_tot = df.shade_tree + df.shade_bldg + df.shade_indoor
    df["ici"] = (1 - df.shade_len / df.out_short_len) * (df.shade_indoor / shade_tot.where(shade_tot > 0)).fillna(0)
    df["short_uses_indoor"] = df.short_indoor > 0
    df["shade_uses_indoor"] = df.shade_indoor > 0
    df["shade_extra_indoor"] = df.shade_indoor > df.short_indoor + 1e-6
    df["shade_sun_share"] = df.shade_sun / df.shade_len
    df["short_sun_share"] = df.short_sun / df.short_len
    df["no_alt"] = df.sun_red < cfg["metrics"]["no_alt_threshold"]
    df.to_parquet(proc / "demo_trips_metrics.parquet")
    summarise(cfg, df, trips_)
    figure(cfg, e, G, trips_, labels)


def summarise(cfg, df, trips_):
    tabs = cfg["paths"]["tables"]

    def s(x):
        u = x[x.shade_uses_indoor]
        return pd.Series({
            "trips": x.trip_id.nunique(), "short_len_m": x.short_len.mean(), "short_sun_share": x.short_sun_share.mean(),
            "detour": x.detour.mean(), "sun_red": x.sun_red.mean(), "exposure_red": x.exposure_red.mean(),
            "no_alt_share": x.no_alt[x.sun_red.notna()].mean(), "path_changed": 1 - x.same_path.mean(),
            "short_uses_indoor": x.short_uses_indoor.mean(), "shade_uses_indoor": x.shade_uses_indoor.mean(),
            "shade_extra_indoor": x.shade_extra_indoor.mean(),
            "indoor_share_of_shade_len": (x.shade_indoor.sum() / x.shade_len.sum()),
            "len_vs_outdoor": x.len_vs_outdoor.mean(), "sun_red_vs_outdoor": x.sun_red_vs_outdoor.mean(),
            "exposure_red_vs_outdoor": x.exposure_red_vs_outdoor.mean(),
            "ici_mean_all": x.ici.mean(), "ici_mean_indoor_trips": u.ici.mean() if len(u) else np.nan,
            "ici_pos_share_indoor_trips": (u.ici > 0).mean() if len(u) else np.nan})
    t15 = df[df.time.str.endswith("_15")]
    res = {"trips": trips_.groupby("set").size().to_dict(),
           "at_15h": t15.groupby(["set", "scenario"]).apply(s, include_groups=False).round(4).reset_index().to_dict("records"),
           "at_15h_by_bin": t15.groupby(["set", "scenario", "length_bin"], observed=True).apply(s, include_groups=False)
           .round(4).reset_index().to_dict("records"),
           "all_times": df.groupby(["set", "scenario"]).apply(s, include_groups=False).round(4).reset_index().to_dict("records")}
    bt = df[df.set == "campus"].groupby(["scenario", "time"]).apply(s, include_groups=False).round(4)
    bt.to_csv(tabs / "phase_08_by_time.csv")
    (tabs / "phase_08_scenarios.json").write_text(json.dumps(res, indent=2, default=float))
    print(pd.DataFrame(res["at_15h"]).to_string())


def figure(cfg, e, G, trips_, labels):
    """06-21 15:00 campus trips: edge use by shade paths per scenario + key numbers."""
    figs, tabs = cfg["paths"]["figures"], cfg["paths"]["tables"]
    rc = cfg["routing"]
    tp = theta_params(cfg)
    lab = "0621_15"
    sq = demo_square(cfg)
    b = sq.bounds
    camp = trips_[trips_.set == "campus"]
    res = json.loads((tabs / "phase_08_scenarios.json").read_text())
    r15 = pd.DataFrame(res["at_15h"])
    r15 = r15[r15.set == "campus"].set_index("scenario")
    fig = plt.figure(figsize=(24, 14))
    gs = fig.add_gridspec(2, 3, height_ratios=[3, 1.25])
    from coolpath.routing import path_edges
    for c, (sname, (g, sub)) in enumerate(G.items()):
        L = sub.length.to_numpy()
        ls, lt, lb, li = (sub[f"L_{k}_{lab}"].to_numpy() for k in ("sun", "tree", "bldg", "indoor"))
        use = np.zeros(len(sub))
        for o, grp in camp.groupby("o"):
            _, _, p1, e1 = search(g, L, ls, lt, lb, int(o), "shade", tp=tp, tree_factor=rc["tree_factor"], max_dist=2500,
                                  l_indoor=li, indoor_factor=rc["indoor_factor"])
            for d in grp.d:
                np.add.at(use, path_edges(p1, e1, int(d)), 1)
        ax = fig.add_subplot(gs[0, c])
        geo = sub.geometry.values
        segs = [np.asarray(x.coords)[:, :2] for x in geo]
        shade = 1 - ls / np.maximum(L, 1e-9)
        ax.add_collection(LineCollection(segs, colors=plt.cm.Greys(0.25 + 0.5 * (1 - shade)), lw=0.5))
        k = use > 0
        kind = sub.kind.to_numpy()
        col = np.where(kind == "indoor", np.where(sub.needs_penncard, "#e8590c", "#2b8a3e"),
                       np.where(kind == "connector", "#7048e8", "#1971c2"))
        ax.add_collection(LineCollection([segs[i] for i in np.where(k)[0]], colors=col[k],
                                         lw=0.4 + 4.5 * np.sqrt(use[k] / use.max())))
        ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_aspect("equal"); ax.set_axis_off()
        x = r15.loc[sname]
        ax.set_title(f"{SCEN[sname]}\nshade paths of {int(x.trips):,} campus trips, 06-21 + 08-01 15:00 metrics:\n"
                     f"sun −{x.sun_red_vs_outdoor:.1%} vs outdoor shortest, length {x.len_vs_outdoor:+.1%}, "
                     f"{x.shade_uses_indoor:.0%} use indoor", fontsize=11)
    mets = [("sun_red_vs_outdoor", "sun reduction\nvs outdoor shortest"),
            ("exposure_red_vs_outdoor", "weighted-exposure reduction\nvs outdoor shortest"),
            ("len_vs_outdoor", "length change\nvs outdoor shortest"), ("shade_uses_indoor", "shade paths\nusing indoor")]
    allr = pd.DataFrame(res["at_15h"]).set_index(["set", "scenario"])
    gs2 = gs[1, :].subgridspec(1, 2)
    xs = np.arange(len(mets))
    for c, (smp, title) in enumerate([("campus", "Campus trips between buildings (300–1,200 m)"),
                                      ("station", "Station trips: 11 rapid-transit stations -> buildings within 1.2 km")]):
        ax = fig.add_subplot(gs2[0, c])
        for j, (sname, colr) in enumerate(zip(SCEN, ["#868e96", "#2b8a3e", "#e8590c"])):
            v = [allr.loc[(smp, sname), m] * 100 for m, _ in mets]
            ax.bar(xs + (j - 1) * 0.27, v, 0.27, color=colr, label=SCEN[sname])
            for xx, vv in zip(xs + (j - 1) * 0.27, v):
                ax.text(xx, vv, f"{vv:.1f}", ha="center", va="bottom", fontsize=8)
        ax.set_xticks(xs, [m[1] for m in mets], fontsize=9)
        ax.axhline(0, color="k", lw=0.7)
        ax.set_ylabel("%")
        ax.set_title(f"{title}, {int(allr.loc[(smp, 'all'), 'trips']):,} trips, 15:00, dynamic θ", fontsize=11)
        if c == 0:
            ax.legend(fontsize=9)
    fig.suptitle("University City demo: shade paths with and without indoor passages (blue sidewalk, green no-PennCard "
                 "indoor, orange PennCard indoor, purple connector; width ∝ trips)", fontsize=13)
    plt.tight_layout(rect=(0, 0, 1, 0.97))
    plt.savefig(figs / "phase_08_scenarios.png", dpi=85)
    plt.close(fig)


def main():
    cfg = load_config()
    args = sys.argv[1:]
    if not args or "build" in args:
        build(cfg)
    if not args or "trips" in args:
        trips(cfg)
    elif "figure" in args:                     # redraw from saved trips / summaries
        e, ids, *_ = load_demo(cfg)
        is_new = e.kind.isin(KINDS_NEW)
        masks = {"outdoor": ~is_new, "no_penncard": ~is_new | ~e.needs_penncard, "all": np.ones(len(e), bool)}
        G = {k: scenario_graph(e, len(ids), np.asarray(m, bool)) for k, m in masks.items()}
        figure(cfg, e, G, pd.read_parquet(cfg["paths"]["processed"] / "demo_trips.parquet"), None)


if __name__ == "__main__":
    main()
