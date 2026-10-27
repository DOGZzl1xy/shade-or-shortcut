"""T4: DVRPC pedestrian network for the test area -> OSMnx graph; connectivity before/after snapping."""

import json
import pickle
import time

import matplotlib.pyplot as plt
import networkx as nx
import osmnx as ox

from coolpath import load_config
from coolpath.acquire import vector
from coolpath.areas import test_area
from coolpath.network import build_graph, graph_stats, largest_component, load_lines, split_at_snapped_ends


def main():
    cfg = load_config()
    figs, out = cfg["paths"]["figures"], cfg["paths"]["interim"] / "test"
    tol = cfg["network"]["snap_tolerance_m"]
    lines = load_lines(vector.dvrpc_pedestrian(cfg), test_area(cfg), cfg["project"]["crs"])
    print(len(lines), lines.kind.value_counts().to_dict())

    t0 = time.time()
    G_raw = build_graph(lines, 0.0)
    raw = graph_stats(G_raw)
    split = split_at_snapped_ends(lines, tol)
    G_fix = build_graph(split, tol)
    fix = graph_stats(G_fix)
    dt = time.time() - t0
    passed_raw = raw["lcc_edge_share"] >= cfg["network"]["min_lcc_edge_share"]
    passed_fix = fix["lcc_edge_share"] >= cfg["network"]["min_lcc_edge_share"]
    res = {"lines": len(lines), "lines_by_kind": lines.kind.value_counts().to_dict(),
           "raw": raw, "snapped_1m": fix, "build_s": dt,
           "pass_raw": bool(passed_raw), "pass_snapped": bool(passed_fix)}
    print(json.dumps(res, indent=2))
    (cfg["paths"]["tables"] / "t4_network.json").write_text(json.dumps(res, indent=2))

    G = largest_component(G_fix)
    with open(out / "graph_test.pkl", "wb") as f:
        pickle.dump(G, f)
    ox.save_graph_geopackage(G, out / "graph_test.gpkg")

    fig, ax = plt.subplots(1, 3, figsize=(21, 8))
    for a, GG, title, st in [(ax[0], G_raw, "Raw (exact endpoint match)", raw),
                             (ax[1], G_fix, f"Split + {tol:g} m snap", fix)]:
        U = nx.Graph(ox.convert.to_undirected(GG))
        comps = sorted(nx.connected_components(U), key=len, reverse=True)
        lcc = comps[0]
        e = ox.graph_to_gdfs(GG, nodes=False)
        e["in_lcc"] = [uv[0] in lcc for uv in e.index]
        e[e.in_lcc].plot(ax=a, color="#999", lw=0.5)
        e[~e.in_lcc].plot(ax=a, color="red", lw=1.2)
        dang = [n for n, d in U.degree() if d == 1]
        nd = ox.graph_to_gdfs(GG, edges=False).loc[dang]
        nd.plot(ax=a, color="blue", markersize=2)
        a.set_title(f"{title}: LCC {st['lcc_edge_share']:.1%} of edges, {st['components']} comps, "
                    f"{st['dangling_nodes']} dangling (blue)", fontsize=10)
        a.set_axis_off()
    # zoom on an intersection to show sidewalk/crosswalk joins
    e = ox.graph_to_gdfs(G, nodes=False)
    n = ox.graph_to_gdfs(G, edges=False)
    cx, cy = 483950, 4422630   # 34th & Walnut
    box = (cx - 60, cx + 60, cy - 60, cy + 60)
    for k, c in [("sidewalk", "k"), ("crosswalk", "orange"), ("other", "purple")]:
        e[e.kind == k].plot(ax=ax[2], color=c, lw=2, label=k)
    n.plot(ax=ax[2], color="blue", markersize=12, zorder=3)
    ax[2].set_xlim(box[:2])
    ax[2].set_ylim(box[2:])
    ax[2].legend()
    ax[2].set_title("Zoom (~34th & Walnut): sidewalks, crosswalks, nodes")
    plt.tight_layout()
    plt.savefig(figs / "t4_network.png", dpi=110)


if __name__ == "__main__":
    main()
