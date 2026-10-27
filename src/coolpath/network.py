"""Pedestrian graph from DVRPC lines: noding, 1 m snapping, OSMnx conversion, statistics."""

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import shapely
from scipy.cluster.hierarchy import DisjointSet
from scipy.spatial import cKDTree
from shapely.geometry import LineString
from shapely.ops import substring

LINE_TYPES = {1: "sidewalk", 2: "crosswalk", 3: "other"}


def load_lines(path, area_utm: gpd.GeoSeries, crs: str) -> gpd.GeoDataFrame:
    g = gpd.read_parquet(path)
    g = g[g.intersects(area_utm.to_crs(4326).iloc[0])]
    g = g.to_crs(crs)
    g = gpd.clip(g, area_utm.iloc[0]).explode(index_parts=False)
    g = g[g.geom_type == "LineString"]
    g = g[g.length > 0.01]
    g["kind"] = g["line_type"].map(LINE_TYPES).fillna("other")
    return g[["objectid", "line_type", "kind", "feat_type", "geometry"]].reset_index(drop=True)


def split_at_snapped_ends(lines: gpd.GeoDataFrame, tol: float) -> gpd.GeoDataFrame:
    """Split each line where another line's endpoint lies within tol of its interior."""
    geoms = lines.geometry.values
    ends = np.concatenate([shapely.get_point(geoms, 0), shapely.get_point(geoms, -1)])
    owner = np.tile(np.arange(len(geoms)), 2)
    tree = shapely.STRtree(geoms)
    pi, li = tree.query(ends, predicate="dwithin", distance=tol)
    keep = owner[pi] != li
    pi, li = pi[keep], li[keep]
    cuts: dict[int, list[float]] = {}
    for p, l in zip(pi, li):
        d = geoms[l].project(ends[p])
        if tol < d < geoms[l].length - tol:
            cuts.setdefault(int(l), []).append(d)
    rows = []
    for i, row in enumerate(lines.itertuples(index=False)):
        g = row.geometry
        ds = sorted(set(np.round(cuts.get(i, []), 3)))
        bounds = [0.0, *ds, g.length]
        for a, b in zip(bounds[:-1], bounds[1:]):
            if b - a > 1e-3:
                rows.append({**row._asdict(), "geometry": substring(g, a, b)})
    return gpd.GeoDataFrame(rows, crs=lines.crs)


def node_lines(lines: gpd.GeoDataFrame, snap_tol: float = 0.0):
    """Nodes = endpoints merged within snap_tol (exact match when 0).

    Returns (edges, nodes): undirected edges with u, v, length, edge_id and geometry whose ends sit
    exactly on the merged node positions; nodes with x, y (index = node id)."""
    geoms = lines.geometry.values
    ends = np.concatenate([shapely.get_coordinates(shapely.get_point(geoms, 0)),
                           shapely.get_coordinates(shapely.get_point(geoms, -1))])
    if snap_tol > 0:
        ds = DisjointSet(range(len(ends)))
        for i, j in cKDTree(ends).query_pairs(snap_tol):
            ds.merge(i, j)
        root = np.array([ds[i] for i in range(len(ends))])
    else:
        _, root = np.unique(np.round(ends, 3), axis=0, return_inverse=True)
    _, nid = np.unique(root, return_inverse=True)
    nxy = pd.DataFrame(ends).groupby(nid).mean().to_numpy()
    n = len(geoms)
    u, v = nid[:n], nid[n:]
    new_geoms = []
    for g, a, b in zip(geoms, u, v):
        c = np.asarray(g.coords).copy()
        c[0], c[-1] = nxy[a], nxy[b]
        new_geoms.append(LineString(c))
    edges = lines.drop(columns="geometry").copy()
    edges["geometry"] = new_geoms
    edges["u"], edges["v"] = u, v
    edges = edges[edges.u != edges.v].copy()          # collapsed short pieces
    edges["length"] = shapely.length(edges.geometry.values)
    edges["edge_id"] = np.arange(len(edges))
    edges = gpd.GeoDataFrame(edges.reset_index(drop=True), crs=lines.crs)
    used = np.unique(np.concatenate([edges.u, edges.v]))
    nodes = gpd.GeoDataFrame({"x": nxy[used, 0], "y": nxy[used, 1]}, index=pd.Index(used, name="node_id"),
                             geometry=gpd.points_from_xy(nxy[used, 0], nxy[used, 1]), crs=lines.crs)
    return edges, nodes


def build_graph(lines: gpd.GeoDataFrame, snap_tol: float = 0.0):
    """OSMnx MultiDiGraph (both directions) from node_lines()."""
    edges, nodes = node_lines(lines, snap_tol)
    edges = edges.assign(osmid=edges.edge_id)          # edge id, shared by both directions
    rev = edges.copy()
    rev["u"], rev["v"] = edges.v.values, edges.u.values
    rev["geometry"] = shapely.reverse(edges.geometry.values)
    both = pd.concat([edges, rev], ignore_index=True)
    both["key"] = both.groupby(["u", "v"]).cumcount()
    gdf_e = gpd.GeoDataFrame(both, crs=lines.crs).set_index(["u", "v", "key"])
    gdf_n = nodes.rename_axis("osmid")
    return ox.graph_from_gdfs(gdf_n, gdf_e, graph_attrs={"crs": lines.crs})


def edge_components(edges: pd.DataFrame) -> np.ndarray:
    """Connected-component label per edge (largest component = 0)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    n = int(max(edges.u.max(), edges.v.max())) + 1
    m = coo_matrix((np.ones(len(edges)), (edges.u, edges.v)), shape=(n, n))
    _, lab = connected_components(m, directed=False)
    el = lab[edges.u.to_numpy()]
    sizes = np.bincount(el)
    rank = np.argsort(-sizes)
    remap = np.empty_like(rank)
    remap[rank] = np.arange(rank.size)
    return remap[el]


def split_at_polygons(lines: gpd.GeoDataFrame, polys) -> gpd.GeoDataFrame:
    """Cut lines at the boundary of `polys` (a polygon / multipolygon): pieces inside and outside."""
    hit = lines.intersects(polys)
    keep = lines[~hit]
    parts = []
    for row in lines[hit].itertuples(index=False):
        for piece in (row.geometry.intersection(polys), row.geometry.difference(polys)):
            for g in getattr(piece, "geoms", [piece]):
                if g.geom_type == "LineString" and g.length > 0.01:
                    parts.append({**row._asdict(), "geometry": g})
    out = pd.concat([keep, gpd.GeoDataFrame(parts, crs=lines.crs)], ignore_index=True)
    return gpd.GeoDataFrame(out, crs=lines.crs)


def graph_stats(G) -> dict:
    U = nx.Graph(ox.convert.to_undirected(G))
    comps = sorted(nx.connected_components(U), key=len, reverse=True)
    lcc = U.subgraph(comps[0])
    deg = dict(U.degree())
    # crosswalk ends: node shared with at least one non-crosswalk edge?
    kinds = {}
    for a, b, d in U.edges(data=True):
        kinds.setdefault(a, set()).add(d["kind"])
        kinds.setdefault(b, set()).add(d["kind"])
    cw_nodes = [n for n, k in kinds.items() if "crosswalk" in k]
    cw_ok = sum(1 for n in cw_nodes if kinds[n] - {"crosswalk"})
    return {
        "nodes": U.number_of_nodes(), "edges": U.number_of_edges(), "components": len(comps),
        "lcc_edge_share": lcc.number_of_edges() / U.number_of_edges(),
        "lcc_length_share": sum(d["length"] for *_, d in lcc.edges(data=True))
                            / sum(d["length"] for *_, d in U.edges(data=True)),
        "dangling_nodes": sum(1 for n in deg if deg[n] == 1),
        "crosswalk_nodes": len(cw_nodes),
        "crosswalk_nodes_touching_non_crosswalk_share": cw_ok / max(len(cw_nodes), 1),
        "total_length_km": sum(d["length"] for *_, d in U.edges(data=True)) / 1e3,
    }


def largest_component(G):
    U = ox.convert.to_undirected(G)
    keep = max(nx.connected_components(nx.Graph(U)), key=len)
    return G.subgraph(keep).copy()


def _bearing(geoms) -> np.ndarray:
    """Overall direction of each line (start -> end), degrees in [0, 180)."""
    a = shapely.get_coordinates(shapely.get_point(geoms, 0))
    b = shapely.get_coordinates(shapely.get_point(geoms, -1))
    return np.degrees(np.arctan2(b[:, 1] - a[:, 1], b[:, 0] - a[:, 0])) % 180


def bridge_deck(edges: gpd.GeoDataFrame, bridges: gpd.GeoDataFrame, buffer_m: float,
                min_share: float, max_angle: float) -> pd.DataFrame:
    """Flag edges that run along a bridge (D-019), not under it.

    Bridges whose nearest segment (to the edge midpoint) is within max_angle degrees of the edge
    direction count as aligned; an edge is on a bridge when >= min_share of its length lies in
    the union of the aligned bridges' buffer_m buffers. `bridges` needs a unique index.
    Returns per-edge share, smallest angle, index of that bridge, and the flag."""
    segs = bridges.geometry.explode(index_parts=False)
    seg_geoms, seg_owner = [], []
    for owner, g in zip(segs.index, segs.values):
        c = np.asarray(g.coords)
        for a, b in zip(c[:-1], c[1:]):
            seg_geoms.append(LineString([a, b]))
            seg_owner.append(owner)
    seg_geoms = np.array(seg_geoms, dtype=object)
    seg_owner = np.array(seg_owner)
    seg_bear = _bearing(seg_geoms)
    buf = bridges.geometry.buffer(buffer_m)
    eg = edges.geometry.values
    lens = shapely.length(eg)
    e_idx, b_idx = shapely.STRtree(buf.values).query(eg, predicate="intersects")
    share = np.zeros(len(eg))
    angle = np.full(len(eg), np.nan)
    which = np.full(len(eg), -1)
    mids = shapely.line_interpolate_point(eg, 0.5, normalized=True)
    ebear = _bearing(eg)
    owner_pos = {o: np.where(seg_owner == o)[0] for o in np.unique(seg_owner)}
    pairs = pd.DataFrame({"e": e_idx, "b": b_idx})
    for e, grp in pairs.groupby("e"):
        aligned, best = [], (np.inf, -1)
        for b in grp.b:
            cand = owner_pos[buf.index[b]]
            k = cand[np.argmin(shapely.distance(seg_geoms[cand], mids[e]))]
            d = abs(ebear[e] - seg_bear[k]) % 180
            d = min(d, 180 - d)
            best = min(best, (d, b))
            if d < max_angle:
                aligned.append(b)
        angle[e], which[e] = best
        if aligned:   # length share within the union of all aligned bridge buffers
            u = shapely.union_all(buf.values[aligned])
            share[e] = shapely.length(shapely.intersection(eg[e], u)) / max(lens[e], 1e-9)
    on = (share >= min_share) & (angle < max_angle)
    return pd.DataFrame({"bridge_share": share, "bridge_angle": angle, "bridge_idx": which, "on_bridge": on},
                        index=edges.index)
