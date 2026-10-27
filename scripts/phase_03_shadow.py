"""Phase 3: pedestrian edges (D-030), 2 m sample points, sun positions, tiled shadow casting.

    caffeinate -i uv run python -m scripts.phase_03_shadow [net] [points] [cast]

Outputs (data/processed): network_edges.parquet, network_nodes.parquet, sun.parquet,
shadow_points.parquet (one row per sample point, one uint8 column per time: 0 sun / 1 tree /
2 building or other solid shade).
"""

import argparse
import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.windows import Window, from_bounds

from coolpath import load_config
from coolpath.acquire import vector
from coolpath.areas import city
from coolpath.network import (bridge_deck, edge_components, load_lines, node_lines, split_at_polygons,
                              split_at_snapped_ends)
from coolpath.shadow import BUILDING, TREE, cast_z, local_max, sample_points
from coolpath.sun import sun_table


def osm_highway_bridges(cfg):
    br = gpd.read_parquet(cfg["paths"]["raw"] / "osm" / "osm_bridge.parquet")
    br = br[br.geom_type.isin(["LineString", "MultiLineString"])]
    if cfg["bridge_deck"]["osm_bridge_highway_only"]:
        br = br[br.highway.notna()]
    return br.reset_index(drop=True)


def step_net(cfg):
    crs, proc = cfg["project"]["crs"], cfg["paths"]["processed"]
    bd, nw = cfg["bridge_deck"], cfg["network"]
    t0 = time.time()
    lines = load_lines(vector.dvrpc_pedestrian(cfg), gpd.GeoSeries([city(cfg)], crs=crs), crs)
    n_raw = len(lines)
    br = osm_highway_bridges(cfg)
    lines = split_at_polygons(lines, shapely.union_all(br.buffer(bd["buffer_m"]).values))   # D-027
    n_bridge_split = len(lines)
    lines = split_at_snapped_ends(lines, nw["snap_tolerance_m"])
    edges, nodes = node_lines(lines, nw["snap_tolerance_m"])
    edges["component"] = edge_components(edges)
    flags = bridge_deck(edges, br, bd["buffer_m"], bd["min_length_share"], bd["max_angle_deg"])
    edges = edges.join(flags)
    if bd["include_dvrpc_grade_separated"]:
        edges["on_bridge"] |= edges.feat_type.eq("GRADE_SEPARATED")
    edges.to_parquet(proc / "network_edges.parquet")
    nodes.to_parquet(proc / "network_nodes.parquet")
    deg = np.bincount(np.concatenate([edges.u, edges.v]))
    lcc = edges.component == 0
    st = {"dvrpc_lines": n_raw, "after_bridge_split": n_bridge_split, "after_noding": len(lines),
          "edges": len(edges), "nodes": len(nodes), "km": float(edges.length.sum() / 1e3),
          "components": int(edges.component.max() + 1), "lcc_edge_share": float(lcc.mean()),
          "lcc_length_share": float(edges.length[lcc].sum() / edges.length.sum()),
          "dangling_nodes": int((deg[nodes.index] == 1).sum()),
          "on_bridge_edges": int(edges.on_bridge.sum()), "on_bridge_km": float(edges.length[edges.on_bridge].sum() / 1e3),
          "by_kind_km": {k: float(v / 1e3) for k, v in edges.groupby("kind").length.sum().items()},
          "seconds": time.time() - t0}
    print(json.dumps(st, indent=2))
    return st


def step_points(cfg):
    proc, sc = cfg["paths"]["processed"], cfg["shadow"]
    edges = gpd.read_parquet(proc / "network_edges.parquet")
    px, py, eid, seg = sample_points(edges, sc["sample_spacing_m"])
    on_b = edges.on_bridge.to_numpy()[eid]
    surf = {}
    for name in ("dem", "dsm"):
        with rasterio.open(proc / f"{name}.tif") as s:
            surf[name] = np.array([v[0] for v in s.sample(zip(px, py))], np.float32)
    base = np.where(on_b, surf["dsm"], surf["dem"])                     # D-014 / D-019
    pts = pd.DataFrame({"edge_id": edges.edge_id.to_numpy()[eid].astype(np.int32), "seg_m": seg.astype(np.float32),
                        "x": px, "y": py, "z0": (base + sc["observer_height_m"]).astype(np.float32),
                        "on_bridge": on_b, "ground": surf["dem"]})
    pts.to_parquet(proc / "shadow_points_xyz.parquet")
    sun = sun_table(cfg)
    sun.to_parquet(proc / "sun.parquet")
    st = {"points": len(pts), "bridge_points": int(on_b.sum()),
          "bridge_deck_above_ground_median_m": float(np.median((surf["dsm"] - surf["dem"])[on_b])) if on_b.any() else None}
    print(json.dumps(st, indent=2), "\n", sun.to_string())
    return st


def read_m(path, bounds):
    with rasterio.open(path) as s:
        w = from_bounds(*bounds, transform=s.transform)
        w = Window(round(w.col_off), round(w.row_off), round(w.width), round(w.height))
        a = s.read(1, window=w, boundless=True, fill_value=0).astype(np.float32)
        if s.tags().get("units") == "decimetre":
            a /= 10
        return a, s.window_transform(w)


def cast_points(cfg, x, y, z0, az, el, log=True):
    """Shadow states for arbitrary points (observer elevation z0), tiled like spec phase 3:
    2 km tiles read with a 1 km buffer. Returns (states uint8 [n, n_times], stats)."""
    proc, sc = cfg["paths"]["processed"], cfg["shadow"]
    T, buf = sc["tile_size_m"], sc["tile_buffer_m"]
    tx, ty = np.floor(x / T).astype(int), np.floor(y / T).astype(int)
    states = np.zeros((len(x), len(az)), np.uint8)
    terrain_only = 0
    t_read = t_cast = 0.0
    tiles = sorted(set(zip(tx, ty)))
    for k, (i, j) in enumerate(tiles):
        sel = np.where((tx == i) & (ty == j))[0]
        b = (i * T - buf, j * T - buf, (i + 1) * T + buf, (j + 1) * T + buf)
        t0 = time.time()
        dem, tr = read_m(proc / "dem.tif", b)
        hb, _ = read_m(proc / "surface_building.tif", b)
        ht, _ = read_m(proc / "surface_tree.tif", b)
        lmax = local_max(dem + np.maximum(hb, ht), tr.a, sc["localmax_block_m"], sc["max_ray_m"])
        t_read += time.time() - t0
        t0 = time.time()
        args = (x[sel], y[sel], z0[sel].astype(np.float64), dem, hb, ht, lmax, tr.c, tr.f, tr.a,
                az, el, sc["observer_height_m"], float(sc["max_ray_m"]), sc["ray_step_m"])
        s_t = cast_z(*args, sc["terrain_occlusion"])
        if sc["terrain_occlusion"]:
            terrain_only += int((s_t != cast_z(*args, False)).sum())
        states[sel] = s_t
        t_cast += time.time() - t0
        if log and k % 20 == 0:
            print(f"tile {k + 1}/{len(tiles)} ({len(sel)} pts) read {t_read:.0f}s cast {t_cast:.0f}s", flush=True)
    return states, {"tiles": len(tiles), "read_s": t_read, "cast_s": t_cast, "terrain_changed_point_times": terrain_only}


def step_cast(cfg):
    proc = cfg["paths"]["processed"]
    pts = pd.read_parquet(proc / "shadow_points_xyz.parquet")
    sun = pd.read_parquet(proc / "sun.parquet")
    az, el = sun.azimuth.to_numpy(), sun.elevation.to_numpy()
    t_all = time.time()
    states, st = cast_points(cfg, pts.x.to_numpy(), pts.y.to_numpy(), pts.z0.to_numpy(), az, el)
    out = pts[["edge_id", "seg_m", "x", "y", "z0", "on_bridge"]].copy()
    for t, lab in enumerate(sun.label):
        out[f"s_{lab}"] = states[:, t]
    out.to_parquet(proc / "shadow_points.parquet")
    share = pd.DataFrame({"label": sun.label, "elev": el.round(1), "az": az.round(1),
                          "sun": (states == 0).mean(0), "tree": (states == TREE).mean(0),
                          "solid": (states == BUILDING).mean(0)})
    st.update({"wall_s": time.time() - t_all, "terrain_changed_share": st["terrain_changed_point_times"] / states.size,
               "share_by_time": share.to_dict("records")})
    print(share.to_string())
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("steps", nargs="*", default=["net", "points", "cast"])
    a = ap.parse_args()
    cfg = load_config()
    stf = cfg["paths"]["tables"] / "phase_03_stats.json"
    st = json.loads(stf.read_text()) if stf.exists() else {}
    for name, fn in [("net", step_net), ("points", step_points), ("cast", step_cast)]:
        if name in a.steps:
            st[name] = fn(cfg)
            stf.write_text(json.dumps(st, indent=2, default=str))


if __name__ == "__main__":
    main()
