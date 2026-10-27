"""Routing on small synthetic graphs with hand-computed answers."""

import networkx as nx
import numpy as np

from coolpath.routing import CSR, path_edges, search, theta

TP = np.array([0.3961, 0.0111, 859.5746, 0.1958])


def th(l):
    return 0.3961 / (1 + np.exp(-0.0111 * (l - 859.5746))) + 0.1958


def graph(edges):
    """edges: (u, v, length, sun, tree, bldg)."""
    a = np.array(edges, float)
    u, v = a[:, 0].astype(int), a[:, 1].astype(int)
    g = CSR(u, v, int(max(u.max(), v.max())) + 1)
    return g, a[:, 2], a[:, 3], a[:, 4], a[:, 5]


# square: 0-1-3 sunny (100 + 100), 0-2-3 building shade (110 + 110)
SQUARE = [(0, 1, 100, 100, 0, 0), (1, 3, 100, 100, 0, 0), (0, 2, 110, 0, 0, 110), (2, 3, 110, 0, 0, 110)]


def test_theta_curve_values():
    assert abs(theta(0.0, TP) - 0.19583) < 1e-4
    assert abs(theta(859.5746, TP) - (0.3961 / 2 + 0.1958)) < 1e-9
    assert abs(theta(5000.0, TP) - 0.5919) < 1e-4


def test_shortest_square():
    g, L, s, t, b = graph(SQUARE)
    cost, dist, pred, pe = search(g, L, s, t, b, 0, "shortest")
    assert dist[3] == 200
    assert list(path_edges(pred, pe, 3)) == [0, 1]


def test_shade_square_hand_computed():
    g, L, s, t, b = graph(SQUARE)
    cost, dist, pred, pe = search(g, L, s, t, b, 0, "shade", tp=TP)
    hand = (1 - th(0)) * 110 + (1 - th(110)) * 110      # = 176.9 < 200 (sunny route)
    assert list(path_edges(pred, pe, 3)) == [2, 3]
    assert abs(cost[3] - hand) < 1e-9
    assert dist[3] == 220


def test_shade_rejects_long_detour():
    # shaded route 2 x 130: (1-0.196)*260 = 209 > 200 -> stay on the sunny shortest route
    g, L, s, t, b = graph([(0, 1, 100, 100, 0, 0), (1, 3, 100, 100, 0, 0),
                           (0, 2, 130, 0, 0, 130), (2, 3, 130, 0, 0, 130)])
    _, _, pred, pe = search(g, L, s, t, b, 0, "shade", tp=TP)
    assert list(path_edges(pred, pe, 3)) == [0, 1]


def test_tree_shade_worth_half_building():
    # tree route 2 x 110: (1 - 0.5*0.196)*220 = 198.5 < 200 -> taken; 2 x 112 -> 202 > 200 -> not
    for leg, expect in [(110, [2, 3]), (112, [0, 1])]:
        g, L, s, t, b = graph([(0, 1, 100, 100, 0, 0), (1, 3, 100, 100, 0, 0),
                               (0, 2, leg, 0, leg, 0), (2, 3, leg, 0, leg, 0)])
        _, _, pred, pe = search(g, L, s, t, b, 0, "shade", tp=TP)
        assert list(path_edges(pred, pe, 3)) == expect


def test_dynamic_theta_grows_with_walked_distance():
    # choice after a 0 m vs 1500 m sunny approach: sun 100 vs building shade 140
    for approach, expect_shade in [(0, False), (1500, True)]:
        e = [(0, 1, max(approach, 1e-9), max(approach, 1e-9), 0, 0),
             (1, 2, 100, 100, 0, 0), (1, 3, 70, 0, 0, 70), (3, 2, 70, 0, 0, 70)]
        g, L, s, t, b = graph(e)
        _, _, pred, pe = search(g, L, s, t, b, 0, "shade", tp=TP)
        took_shade = 2 in set(path_edges(pred, pe, 2))
        assert took_shade == expect_shade
    # hand values: (1-θ(0))·140 = 112.6 > 100; (1-θ(1500))·140 = 57.2 < 100
    assert (1 - th(0)) * 140 > 100 and (1 - th(1500)) * 140 < 100


def test_fixed_theta_matches_networkx():
    rng = np.random.default_rng(42)
    G = nx.gnm_random_graph(200, 600, seed=42)
    G = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    G = nx.convert_node_labels_to_integers(G)
    rows = []
    for a, c in G.edges():
        L = rng.uniform(10, 200)
        sh = rng.dirichlet([1, 1, 1]) * L
        rows.append((a, c, L, *sh))
    g, L, s, t, b = graph(rows)
    for th_fixed in (0.2, 0.4, 0.6):
        w = s + (1 - 0.5 * th_fixed) * t + (1 - th_fixed) * b
        for (a, c), wi in zip(G.edges(), w):
            G[a][c]["w"] = wi
        ref = nx.single_source_dijkstra_path_length(G, 0, weight="w")
        cost, *_ = search(g, L, s, t, b, 0, "shade", theta_fixed=th_fixed, tp=TP)
        assert all(abs(cost[k] - v) < 1e-6 for k, v in ref.items())


def test_max_dist_prunes():
    g, L, s, t, b = graph(SQUARE)
    _, dist, _, _ = search(g, L, s, t, b, 0, "shortest", max_dist=150)
    assert np.isinf(dist[3]) and dist[1] == 100


def test_ellipse_keeps_shortest_and_prunes_far_nodes():
    # path 0-1-2 straight (200 m); node 3 is a far detour
    g, L, s, t, b = graph([(0, 1, 100, 100, 0, 0), (1, 2, 100, 100, 0, 0), (0, 3, 300, 0, 0, 300), (3, 2, 300, 0, 0, 300)])
    g.x = np.array([0.0, 100.0, 200.0, 100.0])
    g.y = np.array([0.0, 0.0, 0.0, 280.0])
    _, d, pred, pe = search(g, L, s, t, b, 0, "shade", tp=TP, ellipse=(2, 1.5, 200.0))
    assert np.isinf(d[3])                         # outside the ellipse (sum of focal distances 2 x 297 > 300)
    assert list(path_edges(pred, pe, 2)) == [0, 1]


def test_path_cost_matches_search_cost():
    from coolpath.routing import path_cost
    g, L, s, t, b = graph(SQUARE)
    cost, _, pred, pe = search(g, L, s, t, b, 0, "shade", tp=TP)
    p = path_edges(pred, pe, 3)
    assert abs(path_cost(p, L, s, t, b, TP) - cost[3]) < 1e-9
    assert abs(path_cost(np.array([0, 1]), L, s, t, b, TP) - 200.0) < 1e-9   # sunny path: C = length


def test_nearest_source():
    from coolpath.routing import nearest_source
    # line 0-1-2-3-4, 100 m each; sources at 0 and 4
    g, L, s, t, b = graph([(0, 1, 100, 0, 0, 0), (1, 2, 100, 0, 0, 0), (2, 3, 100, 0, 0, 0), (3, 4, 100, 0, 0, 0)])
    d, own = nearest_source(g, L, [0, 4])
    assert list(d) == [0, 100, 200, 100, 0]
    assert own[1] == 0 and own[3] == 1


def test_tree_totals_match_extracted_paths():
    from coolpath.routing import path_cost, tree_totals
    rng = np.random.default_rng(42)
    G = nx.connected_watts_strogatz_graph(300, 6, 0.3, seed=42)
    edges = [(a, b, float(rng.uniform(5, 80)), float(rng.uniform(0, 1))) for a, b in G.edges]
    u = np.array([e[0] for e in edges])
    v = np.array([e[1] for e in edges])
    L = np.array([e[2] for e in edges])
    f = np.array([e[3] for e in edges])
    s, t, b = L * f * 0.5, L * f * 0.3, L * f * 0.2
    g = CSR(u, v, 300)
    z = np.zeros_like(L)
    _, _, p0, e0 = search(g, L, z, z, z, 0, "shortest")
    _, _, p1, e1 = search(g, L, s, t, b, 0, "shade", tp=TP)
    dests = np.arange(300)
    for thf in (-1.0, 0.4):
        tot = tree_totals(p1, e1, 0, dests, L, s, t, b, TP, 0.5, thf, e0)
        for k in rng.choice(300, 40, replace=False):
            a, c = path_edges(p0, e0, k), path_edges(p1, e1, k)
            assert abs(tot[k, 0] - L[c].sum()) < 1e-9 and abs(tot[k, 1] - s[c].sum()) < 1e-9
            assert abs(tot[k, 4] - path_cost(c, L, s, t, b, TP, 0.5, thf)) < 1e-9
            same = a.size == c.size and np.array_equal(np.sort(a), np.sort(c))
            assert bool(tot[k, 5]) == same


def test_indoor_edge_cost_and_totals():
    """An indoor shortcut (1 - 1.5θ per metre) beats an equally long building-shaded street."""
    from coolpath.routing import path_cost, tree_totals
    # 0-1-3: shaded street 100 + 100; 0-2-3: indoor 100 + 100 (edges 2, 3)
    g, L, s, t, b = graph([(0, 1, 100, 0, 0, 100), (1, 3, 100, 0, 0, 100), (0, 2, 100, 0, 0, 0), (2, 3, 100, 0, 0, 0)])
    ind = np.array([0.0, 0.0, 100.0, 100.0])
    cost, _, pred, pe = search(g, L, s, t, b, 0, "shade", theta_fixed=0.4, tp=TP, l_indoor=ind)
    assert abs(cost[3] - 200 * (1 - 1.5 * 0.4)) < 1e-9 and pred[3] == 2
    p = path_edges(pred, pe, 3)
    assert abs(path_cost(p, L, s, t, b, TP, 0.5, 0.4, l_indoor=ind) - cost[3]) < 1e-9
    tot = tree_totals(pred, pe, 0, np.array([3]), L, s, t, b, TP, 0.5, 0.4, pe, l_indoor=ind)
    assert abs(tot[0, 6] - 200) < 1e-9 and abs(tot[0, 4] - cost[3]) < 1e-9
