"""Phase 4: final pedestrian graph and the edge shade table (the hub of the pipeline).

    uv run python -m scripts.phase_04_edges

- network from phase 3 (D-030) + OSM covered walkways / building passages (permanent shade, D-034):
  DVRPC edges running along them are flagged `covered`; OSM lines with no matching DVRPC edge are
  added as new edges and tied to the nearest node with short connector edges.
- new edges are sampled at 2 m and shadow-cast exactly like phase 3.
- edges_shade.parquet: one row per edge, L_sun / L_tree / L_bldg (m) for each of the 24 times
  (np.bincount over sample points); covered edges are building shade at every time.
"""

import json

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString

from coolpath import load_config
from coolpath.network import bridge_deck, edge_components, node_lines
from coolpath.shadow import sample_points
from scripts.phase_03_shadow import cast_points


def osm_permanent(cfg):
    ps = cfg["network"]["permanent_shade"]
    parts = []
    for g in ps["osm_groups"]:
        f = gpd.read_parquet(cfg["paths"]["raw"] / "osm" / f"osm_{g}.parquet")
        f = f[f.geom_type.isin(["LineString", "MultiLineString"]) & f.highway.isin(ps["highway_types"])]
        parts.append(f.assign(osm_group=g))
    p = pd.concat(parts, ignore_index=True).explode(index_parts=False)
    p = p.drop_duplicates(subset=["id"]).reset_index(drop=True)
    return gpd.GeoDataFrame(p, crs=parts[0].crs)


def main():
    cfg = load_config()
    proc, figs = cfg["paths"]["processed"], cfg["paths"]["figures"]
    ps, sc = cfg["network"]["permanent_shade"], cfg["shadow"]
    edges = gpd.read_parquet(proc / "network_edges.parquet")
    nodes = gpd.read_parquet(proc / "network_nodes.parquet")
    sun = pd.read_parquet(proc / "sun.parquet")
    labels = list(sun.label)
    res = {}

    # 1. DVRPC edges along OSM covered walkways / passages -> covered
    perm = osm_permanent(cfg)
    m = bridge_deck(edges, perm, ps["match_buffer_m"], ps["match_min_share"], ps["match_max_angle_deg"])
    edges["covered"] = m.on_bridge.to_numpy()
    edges["source"] = "dvrpc"
    matched_perm = set(m.bridge_idx[m.on_bridge])
    res["osm_permanent_lines"] = len(perm)
    res["osm_permanent_km"] = float(perm.length.sum() / 1e3)
    res["dvrpc_edges_covered"] = int(edges.covered.sum())
    res["dvrpc_covered_km"] = float(edges.length[edges.covered].sum() / 1e3)

    # 2. unmatched OSM lines -> their own sub-network (noded at 1 m); every OSM node within
    #    connector_max_m of a network node gets a connector (one per OSM node, to the nearest);
    #    OSM components with no connector are dropped
    add = perm[~perm.index.isin(matched_perm)].copy()
    add["kind"] = "other"
    add["source"] = "osm_" + add.osm_group
    sub_e, sub_n = node_lines(add[["kind", "source", "geometry"]], cfg["network"]["snap_tolerance_m"])
    off_n = int(nodes.index.max()) + 1
    off_e = int(edges.edge_id.max()) + 1
    sub_e["u"] += off_n
    sub_e["v"] += off_n
    sub_n.index = sub_n.index + off_n
    sub_e["edge_id"] = np.arange(len(sub_e)) + off_e
    sub_e["covered"] = True
    sub_e["on_bridge"] = False
    tree = cKDTree(nodes[["x", "y"]].to_numpy())
    d, k = tree.query(sub_n[["x", "y"]].to_numpy())
    con = []
    for nid, dd, kk in zip(sub_n.index, d, k):
        if dd <= ps["connector_max_m"]:
            tgt = int(nodes.index[kk])
            con.append({"u": tgt, "v": int(nid), "kind": "connector", "source": "connector", "covered": False,
                        "on_bridge": False, "geometry": LineString([tuple(nodes.loc[tgt, ["x", "y"]]),
                                                                    tuple(sub_n.loc[nid, ["x", "y"]])])})
    con = gpd.GeoDataFrame(con, crs=edges.crs)
    con["edge_id"] = np.arange(len(con)) + off_e + len(sub_e)
    # keep OSM components that touch the network
    comp = edge_components(pd.concat([sub_e[["u", "v"]], con[["u", "v"]]], ignore_index=True))
    comp_sub = comp[:len(sub_e)]
    linked = set(comp[len(sub_e):])
    keep = np.isin(comp_sub, list(linked))
    res["osm_subnetwork_edges"] = len(sub_e)
    res["osm_edges_dropped_unlinked"] = int((~keep).sum())
    res["osm_km_dropped_unlinked"] = float(sub_e.length[~keep].sum() / 1e3)
    sub_e = sub_e[keep]
    used = np.unique(np.concatenate([sub_e.u, sub_e.v]))
    nn = sub_n.loc[used]
    con = con[con.v.isin(used)]
    new = gpd.GeoDataFrame(pd.concat([sub_e, con], ignore_index=True), crs=edges.crs)
    new["length"] = new.length
    res["osm_edges_added"] = int(len(sub_e))
    res["osm_km_added"] = float(sub_e.length.sum() / 1e3)
    res["connectors_added"] = int(len(con))
    # 3. sample and cast the new edges like phase 3 (ground observers)
    px, py, eid, seg = sample_points(new, sc["sample_spacing_m"])
    import rasterio
    with rasterio.open(proc / "dem.tif") as s:
        z0 = np.array([v[0] for v in s.sample(zip(px, py))], np.float64) + sc["observer_height_m"]
    st_new, _ = cast_points(cfg, px, py, z0, sun.azimuth.to_numpy(), sun.elevation.to_numpy(), log=False)
    new_pts = pd.DataFrame({"edge_id": new.edge_id.to_numpy()[eid], "seg_m": seg})
    for t, lab in enumerate(labels):
        new_pts[f"s_{lab}"] = st_new[:, t]

    # 4. merged graph
    allv = pd.concat([edges, new], ignore_index=True)
    allv = gpd.GeoDataFrame(allv, crs=edges.crs)
    allv["component"] = edge_components(allv)
    nodes_all = pd.concat([nodes, nn])
    allv.to_parquet(proc / "graph_edges.parquet")
    gpd.GeoDataFrame(nodes_all, crs=nodes.crs).to_parquet(proc / "graph_nodes.parquet")

    # 5. edge shade table via bincount
    pts = pd.read_parquet(proc / "shadow_points.parquet", columns=["edge_id", "seg_m"] + [f"s_{l}" for l in labels])
    pts = pd.concat([pts, new_pts], ignore_index=True)
    row = pd.Series(np.arange(len(allv)), index=allv.edge_id).reindex(pts.edge_id).to_numpy()
    w = pts.seg_m.to_numpy(np.float64)
    out = {"edge_id": allv.edge_id.to_numpy(), "length": allv.length.to_numpy()}
    cov = allv.covered.to_numpy()
    for lab in labels:
        s = pts[f"s_{lab}"].to_numpy()
        for k, name in enumerate(("sun", "tree", "bldg")):
            L = np.bincount(row, weights=w * (s == k), minlength=len(allv))
            if name == "bldg":
                L = np.where(cov, out["length"], L)
            else:
                L = np.where(cov, 0.0, L)
            out[f"L_{name}_{lab}"] = L.astype(np.float32)
    tab = pd.DataFrame(out)
    tot = sum(tab[f"L_{k}_{labels[0]}"] for k in ("sun", "tree", "bldg"))
    res["max_abs_length_mismatch_m"] = float(np.abs(tot - tab.length).max())
    res["edges_without_points"] = int((tot == 0).sum())
    tab.to_parquet(proc / "edges_shade.parquet")

    lcc = allv.component == 0
    res.update({"edges": len(allv), "nodes": len(nodes_all), "km": float(allv.length.sum() / 1e3),
                "covered_edges_total": int(allv.covered.sum()), "covered_km_total": float(allv.length[allv.covered].sum() / 1e3),
                "lcc_edge_share": float(lcc.mean()), "components": int(allv.component.max() + 1),
                "table_columns": len(tab.columns), "table_MB": float((proc / "edges_shade.parquet").stat().st_size / 1e6)})
    i15 = labels.index("0621_15")
    for k in ("sun", "tree", "bldg"):
        res[f"share_{k}_0621_15"] = float(tab[f"L_{k}_0621_15"].sum() / tab.length.sum())
    (cfg["paths"]["tables"] / "phase_04_stats.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))

    # figure: Center City covered edges + 15:00 edge shade share
    x0, y0, x1, y1 = 484400, 4421600, 487200, 4423600
    v = allv.cx[x0:x1, y0:y1].merge(tab[["edge_id", "L_sun_0621_15", "length"]].rename(columns={"length": "Ltab"}), on="edge_id")
    v["shade"] = 1 - v.L_sun_0621_15 / v.Ltab.clip(lower=1e-6)
    fig, ax = plt.subplots(1, 2, figsize=(20, 9))
    v.plot(ax=ax[0], color="#ccc", lw=0.5)
    v[v.covered & (v.source == "dvrpc")].plot(ax=ax[0], color="#e4002b", lw=2.5, label="DVRPC edge flagged covered")
    v[v.source.str.startswith("osm")].plot(ax=ax[0], color="#7a3fa0", lw=2.5, label="OSM covered / passage added")
    v[v.source == "connector"].plot(ax=ax[0], color="#00b4d8", lw=2.5, label="connector")
    ax[0].legend(loc="lower left")
    ax[0].set_title("Permanent shade (D-034), Center City")
    v.plot(ax=ax[1], column="shade", cmap="cividis_r", vmin=0, vmax=1, lw=1.2, legend=True,
           legend_kwds={"shrink": 0.6, "label": "shaded share 06-21 15:00"})
    ax[1].set_title("Edge shade table: shaded share at 06-21 15:00")
    for a in ax:
        a.set_xlim(x0, x1)
        a.set_ylim(y0, y1)
        a.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_04_edges.png", dpi=110)


if __name__ == "__main__":
    main()
