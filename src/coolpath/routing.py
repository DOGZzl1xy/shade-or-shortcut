"""Shortest and shade-weighted routing on a CSR pedestrian graph (numba).

Shade cost (Wen et al. 2025): C = L_sun + (1 - 0.5 θ) L_tree + (1 - θ) L_bldg [+ (1 - 1.5 θ) L_indoor], with
θ(L_past) = a / (1 + exp(-k (L_past - x0))) + b, L_past = walked distance on entering an edge.
The dynamic-θ search keeps one label (cost, distance) per node and pops by cost, so it is a
heuristic: not guaranteed globally optimal (as in the original paper).
"""

import heapq

import numpy as np
from numba import njit


class CSR:
    """Undirected graph as CSR: every edge appears in both directions with the same edge id."""

    def __init__(self, u: np.ndarray, v: np.ndarray, n_nodes: int):
        m = u.size
        src = np.concatenate([u, v])
        dst = np.concatenate([v, u])
        eid = np.concatenate([np.arange(m), np.arange(m)])
        order = np.argsort(src, kind="stable")
        self.indptr = np.zeros(n_nodes + 1, np.int64)
        np.add.at(self.indptr, src + 1, 1)
        self.indptr = np.cumsum(self.indptr)
        self.nbr = dst[order].astype(np.int64)
        self.eid = eid[order].astype(np.int64)
        self.n = n_nodes
        self.m = m


def theta_params(cfg: dict) -> np.ndarray:
    t = cfg["routing"]["theta_curve"]
    return np.array([t["a"], t["k"], t["x0"], t["b"]], np.float64)


@njit(cache=True)
def theta(l_past, p):
    return p[0] / (1.0 + np.exp(-p[1] * (l_past - p[2]))) + p[3]


@njit(cache=True)
def _search(indptr, nbr, eid, length, l_sun, l_tree, l_bldg, src, max_dist,
            mode, theta_fixed, tp, tree_factor, nx, ny, ell, l_indoor, indoor_factor):
    """mode 0: shortest by length; mode 1: shade cost (theta_fixed < 0 -> dynamic θ).

    ell = (ox, oy, dx, dy, a): when a > 0, nodes whose straight-line distance to O plus to D
    exceeds a (an ellipse with foci O, D) are not entered; nx, ny are node coordinates.
    Returns cost, dist (walked metres), pred node, pred edge per node (-1 if unreached)."""
    n = indptr.size - 1
    cost = np.full(n, np.inf)
    dist = np.full(n, np.inf)
    pred = np.full(n, -1, np.int64)
    pred_e = np.full(n, -1, np.int64)
    done = np.zeros(n, np.bool_)
    cost[src] = 0.0
    dist[src] = 0.0
    heap = [(0.0, src)]
    while len(heap) > 0:
        c, a = heapq.heappop(heap)
        if done[a]:
            continue
        done[a] = True
        if mode == 1:
            th = theta(dist[a], tp) if theta_fixed < 0 else theta_fixed
        for j in range(indptr[a], indptr[a + 1]):
            b = nbr[j]
            if done[b]:
                continue
            e = eid[j]
            nd = dist[a] + length[e]
            if nd > max_dist:
                continue
            if ell[4] > 0:
                f = np.sqrt((nx[b] - ell[0]) ** 2 + (ny[b] - ell[1]) ** 2) + \
                    np.sqrt((nx[b] - ell[2]) ** 2 + (ny[b] - ell[3]) ** 2)
                if f > ell[4]:
                    continue
            if mode == 0:
                w = length[e]
            else:
                w = l_sun[e] + (1.0 - tree_factor * th) * l_tree[e] + (1.0 - th) * l_bldg[e] + \
                    (1.0 - indoor_factor * th) * l_indoor[e]
            nc = c + w
            if nc < cost[b]:
                cost[b] = nc
                dist[b] = nd
                pred[b] = a
                pred_e[b] = e
                heapq.heappush(heap, (nc, b))
    return cost, dist, pred, pred_e


def search(g: CSR, length, l_sun, l_tree, l_bldg, src: int, mode: str, theta_fixed: float = -1.0,
           tp=None, tree_factor: float = 0.5, max_dist: float = np.inf, ellipse=None,
           l_indoor=None, indoor_factor: float = 1.5):
    """ellipse = (dst_node, factor): restrict to the ellipse with foci src, dst and major axis
    factor x the shortest network distance (passed as the third element: (dst, factor, d_short)).
    l_indoor: indoor length per edge (phase 8), costed (1 - indoor_factor θ); None = no indoor edges."""
    tp = np.zeros(4) if tp is None else tp
    nx = getattr(g, "x", np.zeros(1))
    ny = getattr(g, "y", np.zeros(1))
    ell = np.zeros(5)
    if ellipse is not None:
        dst, factor, d_short = ellipse
        ell = np.array([nx[src], ny[src], nx[dst], ny[dst], factor * d_short], np.float64)
    l_indoor = np.zeros_like(length) if l_indoor is None else l_indoor
    return _search(g.indptr, g.nbr, g.eid, length, l_sun, l_tree, l_bldg, int(src), float(max_dist),
                   0 if mode == "shortest" else 1, float(theta_fixed), tp, float(tree_factor), nx, ny, ell,
                   l_indoor, float(indoor_factor))


@njit(cache=True)
def path_edges(pred, pred_e, dst):
    out = []
    b = dst
    while pred[b] >= 0:
        out.append(pred_e[b])
        b = pred[b]
    return np.array(out[::-1], np.int64)


def path_totals(edges_idx, length, l_sun, l_tree, l_bldg):
    return {"length": float(length[edges_idx].sum()), "sun": float(l_sun[edges_idx].sum()),
            "tree": float(l_tree[edges_idx].sum()), "bldg": float(l_bldg[edges_idx].sum())}


def path_cost(edges_idx, length, l_sun, l_tree, l_bldg, tp, tree_factor=0.5, theta_fixed=-1.0,
              l_indoor=None, indoor_factor=1.5) -> float:
    """Weighted exposure (D-011): the shade cost C of a given path, walked in order from its start,
    with θ from the distance already walked (or fixed θ)."""
    c = d = 0.0
    for e in edges_idx:
        th = theta_fixed if theta_fixed >= 0 else theta(d, tp)
        c += l_sun[e] + (1 - tree_factor * th) * l_tree[e] + (1 - th) * l_bldg[e]
        if l_indoor is not None:
            c += (1 - indoor_factor * th) * l_indoor[e]
        d += length[e]
    return float(c)


def tree_totals(pred, pred_e, src, dests, length, l_sun, l_tree, l_bldg, tp, tree_factor, theta_fixed, ref_pe,
                l_indoor=None, indoor_factor=1.5):
    """Totals along the search-tree path from src to every dest, without extracting paths.

    Returns (n_dest, 7): length, sun, tree, bldg, cost C (walked from src, θ from distance walked,
    or theta_fixed >= 0), 1.0 if the path equals the path in the reference tree ref_pe (same
    pred edge at every node of the chain) else 0.0, and indoor length. Unreached dests get NaN."""
    l_indoor = np.zeros_like(length) if l_indoor is None else l_indoor
    return _tree_totals(pred, pred_e, int(src), np.asarray(dests, np.int64), length, l_sun, l_tree, l_bldg, tp,
                        float(tree_factor), float(theta_fixed), ref_pe, l_indoor, float(indoor_factor))


@njit(cache=True)
def _tree_totals(pred, pred_e, src, dests, length, l_sun, l_tree, l_bldg, tp, tree_factor, theta_fixed, ref_pe,
                 l_indoor, indoor_factor):
    n = pred.size
    acc = np.full((n, 7), np.nan)
    acc[src, :5] = 0.0
    acc[src, 5] = 1.0
    acc[src, 6] = 0.0
    stack = np.empty(n, np.int64)
    out = np.full((dests.size, 7), np.nan)
    for i in range(dests.size):
        k = dests[i]
        h = 0
        while k != src and np.isnan(acc[k, 0]) and pred[k] >= 0:
            stack[h] = k
            h += 1
            k = pred[k]
        if k != src and np.isnan(acc[k, 0]):
            continue                      # unreached
        for j in range(h - 1, -1, -1):
            b = stack[j]
            p = pred[b]
            e = pred_e[b]
            th = theta_fixed if theta_fixed >= 0 else theta(acc[p, 0], tp)
            acc[b, 0] = acc[p, 0] + length[e]
            acc[b, 1] = acc[p, 1] + l_sun[e]
            acc[b, 2] = acc[p, 2] + l_tree[e]
            acc[b, 3] = acc[p, 3] + l_bldg[e]
            acc[b, 4] = acc[p, 4] + l_sun[e] + (1 - tree_factor * th) * l_tree[e] + (1 - th) * l_bldg[e] + \
                (1 - indoor_factor * th) * l_indoor[e]
            acc[b, 5] = 1.0 if (acc[p, 5] == 1.0 and ref_pe[b] == e) else 0.0
            acc[b, 6] = acc[p, 6] + l_indoor[e]
        out[i] = acc[dests[i]]
    return out


def load_city_graph(cfg, lcc_only: bool = True):
    """CSR graph + per-time edge arrays from phase 4 (graph_edges / edges_shade).

    Returns (g, length, shade) where shade[label] = (l_sun, l_tree, l_bldg) float64 arrays indexed
    like the CSR edge ids, and g.x / g.y hold node coordinates, g.node_ids the original ids."""
    import pandas as pd
    proc = cfg["paths"]["processed"]
    e = pd.read_parquet(proc / "graph_edges.parquet", columns=["edge_id", "u", "v", "component"])
    tab = pd.read_parquet(proc / "edges_shade.parquet")
    nodes = pd.read_parquet(proc / "graph_nodes.parquet", columns=["x", "y"])
    if lcc_only:
        e = e[e.component == 0]
    tab = tab.set_index("edge_id").loc[e.edge_id].reset_index()
    ids, inv = np.unique(np.concatenate([e.u.to_numpy(), e.v.to_numpy()]), return_inverse=True)
    m = len(e)
    g = CSR(inv[:m], inv[m:], ids.size)
    g.node_ids = ids
    g.x = nodes.loc[ids, "x"].to_numpy(np.float64)
    g.y = nodes.loc[ids, "y"].to_numpy(np.float64)
    g.edge_ids = tab.edge_id.to_numpy()
    labels = sorted({c.split("_", 2)[2] for c in tab.columns if c.startswith("L_sun_")})
    shade = {lab: tuple(tab[f"L_{k}_{lab}"].to_numpy(np.float64) for k in ("sun", "tree", "bldg")) for lab in labels}
    return g, tab.length.to_numpy(np.float64), shade


@njit(cache=True)
def _multi_source(indptr, nbr, eid, length, srcs, max_dist):
    n = indptr.size - 1
    dist = np.full(n, np.inf)
    owner = np.full(n, -1, np.int64)
    done = np.zeros(n, np.bool_)
    heap = [(0.0, np.int64(0))]
    heap.pop()
    for i in range(srcs.size):
        s = srcs[i]
        if dist[s] > 0.0:
            dist[s] = 0.0
            owner[s] = i
            heapq.heappush(heap, (0.0, s))
    while len(heap) > 0:
        d, a = heapq.heappop(heap)
        if done[a]:
            continue
        done[a] = True
        for j in range(indptr[a], indptr[a + 1]):
            b = nbr[j]
            nd = d + length[eid[j]]
            if nd <= max_dist and nd < dist[b]:
                dist[b] = nd
                owner[b] = owner[a]
                heapq.heappush(heap, (nd, b))
    return dist, owner


def nearest_source(g: CSR, length, srcs, max_dist: float = np.inf):
    """Multi-source Dijkstra by length: network distance to, and index (into srcs) of, the nearest source."""
    return _multi_source(g.indptr, g.nbr, g.eid, length, np.asarray(srcs, np.int64), float(max_dist))
