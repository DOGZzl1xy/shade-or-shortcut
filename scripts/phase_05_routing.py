"""Phase 5: shortest vs shade routing on the city graph — validation, θ sensitivity, timing.

    uv run python -m scripts.phase_05_routing

Pair searches use the spec's ellipse bound (foci O, D; major axis 1.5 x shortest distance);
one-to-many searches use a radius bound (farthest destination x 1.5).
"""

import json
import time

import geopandas as gpd
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd

from coolpath import load_config
from coolpath.routing import load_city_graph, path_cost, path_edges, path_totals, search, theta_params

N_OD = 300
OD_BAND = (400.0, 2000.0)


def main():
    cfg = load_config()
    rc = cfg["routing"]
    proc, figs = cfg["paths"]["processed"], cfg["paths"]["figures"]
    rng = np.random.default_rng(cfg["project"]["seed"])
    t0 = time.time()
    g, L, shade = load_city_graph(cfg)
    labels = sorted(shade)
    tp, tf, fac = theta_params(cfg), rc["tree_factor"], rc["search_radius_factor"]
    zero = np.zeros_like(L)
    res = {"graph_nodes": int(g.n), "graph_edges": int(g.m), "load_s": time.time() - t0}

    # OD sample
    ods = []
    while len(ods) < N_OD:
        o = int(rng.integers(g.n))
        _, d, _, _ = search(g, L, zero, zero, zero, o, "shortest", max_dist=OD_BAND[1])
        cand = np.where((d >= OD_BAND[0]) & (d <= OD_BAND[1]))[0]
        if cand.size:
            ods.append((o, int(rng.choice(cand))))

    rows, t_pair, n_pair = [], 0.0, 0
    for k, (o, dst) in enumerate(ods):
        _, d, pr, pe = search(g, L, zero, zero, zero, o, "shortest", max_dist=OD_BAND[1] * fac)
        sp = path_edges(pr, pe, dst)
        dshort = d[dst]
        for lab in labels:
            ls, lt, lb = shade[lab]
            runs = [("dynamic", -1.0)]
            if lab.endswith("_15"):
                runs += [(f"fixed_{x}", float(x)) for x in rc["theta_fixed_sensitivity"]]
            for name, thf in runs:
                t1 = time.time()
                _, _, pr2, pe2 = search(g, L, ls, lt, lb, o, "shade", theta_fixed=thf, tp=tp, tree_factor=tf,
                                        max_dist=dshort * fac, ellipse=(dst, fac, dshort))
                t_pair += time.time() - t1
                n_pair += 1
                cp = path_edges(pr2, pe2, dst)
                a, b = path_totals(sp, L, ls, lt, lb), path_totals(cp, L, ls, lt, lb)
                rows.append({"od": k, "time": lab, "theta": name,
                             "short_len": a["length"], "short_sun": a["sun"], "short_tree": a["tree"], "short_bldg": a["bldg"],
                             "shade_len": b["length"], "shade_sun": b["sun"], "shade_tree": b["tree"], "shade_bldg": b["bldg"],
                             "short_C": path_cost(sp, L, ls, lt, lb, tp, tf, thf),
                             "shade_C": path_cost(cp, L, ls, lt, lb, tp, tf, thf),
                             "same_path": bool(np.array_equal(np.sort(sp), np.sort(cp)))})
    df = pd.DataFrame(rows)
    df["detour"] = (df.shade_len - df.short_len) / df.short_len
    df["sun_red"] = (df.short_sun - df.shade_sun) / df.short_sun.where(df.short_sun > 0)
    df["exposure_red"] = (df.short_C - df.shade_C) / df.short_C.where(df.short_C > 0)
    df.to_parquet(cfg["paths"]["interim"] / "phase05_trips.parquet")
    dyn = df[df.theta == "dynamic"]

    # ellipse vs unbounded on 50 ODs at 15:00
    lab15 = "0621_15"
    ls, lt, lb = shade[lab15]
    diff = 0
    for o, dst in ods[:50]:
        _, d, _, _ = search(g, L, zero, zero, zero, o, "shortest", max_dist=OD_BAND[1] * fac)
        _, _, p1, e1 = search(g, L, ls, lt, lb, o, "shade", tp=tp, tree_factor=tf, max_dist=d[dst] * fac,
                              ellipse=(dst, fac, d[dst]))
        _, _, p2, e2 = search(g, L, ls, lt, lb, o, "shade", tp=tp, tree_factor=tf)
        diff += not np.array_equal(path_edges(p1, e1, dst), path_edges(p2, e2, dst))

    # exactness vs networkx for fixed θ = 0.4 (5 sources, nodes within 1 km)
    th = 0.4
    w = ls + (1 - tf * th) * lt + (1 - th) * lb
    G = nx.Graph()
    src_e = np.repeat(np.arange(g.n), np.diff(g.indptr))
    for a_, b_, e_ in zip(src_e, g.nbr, g.eid):
        if a_ < b_ and (not G.has_edge(a_, b_) or G[a_][b_]["w"] > w[e_]):
            G.add_edge(int(a_), int(b_), w=w[e_])
    maxerr, checked = 0.0, 0
    for o, _ in ods[:5]:
        ref = nx.single_source_dijkstra_path_length(G, o, cutoff=1000, weight="w")
        c, *_ = search(g, L, ls, lt, lb, o, "shade", theta_fixed=th, tp=tp, tree_factor=tf)
        for n_, v in ref.items():
            maxerr = max(maxerr, abs(c[n_] - v))
            checked += 1

    # one-to-many timing (radius 2 km x 1.5), dynamic θ
    srcs = rng.integers(g.n, size=200)
    t1 = time.time()
    for s_ in srcs:
        search(g, L, ls, lt, lb, int(s_), "shade", tp=tp, tree_factor=tf, max_dist=2000 * fac)
    ms_one_to_many = (time.time() - t1) / len(srcs) * 1e3

    i15 = dyn.time == lab15
    res.update({
        "n_od": len(ods), "od_short_len_m": dyn.short_len.describe()[["min", "50%", "max"]].round(1).to_dict(),
        "negative_detour": int((dyn.detour < -1e-9).sum()),
        "shade_C_gt_short_C": int((dyn.shade_C > dyn.short_C + 1e-6).sum()),
        "sun_increase_count": int((dyn.shade_sun > dyn.short_sun + 1e-6).sum()),
        "sun_increase_share": float((dyn.shade_sun > dyn.short_sun + 1e-6).mean()),
        "short_sun_zero": int((dyn.short_sun <= 0).sum()),
        "detour_mean": float(dyn.detour.mean()), "detour_p95": float(dyn.detour.quantile(0.95)),
        "sun_red_mean": float(dyn.sun_red.mean()), "sun_red_median": float(dyn.sun_red.median()),
        "exposure_red_mean": float(dyn.exposure_red.mean()),
        "path_changed_share": float(1 - dyn.same_path.mean()),
        "at_0621_15": {"detour_mean": float(dyn[i15].detour.mean()), "sun_red_mean": float(dyn[i15].sun_red.mean()),
                       "exposure_red_mean": float(dyn[i15].exposure_red.mean())},
        "theta_sensitivity_15h": df[df.time.str.endswith("_15")].groupby("theta")[["detour", "sun_red", "exposure_red"]]
                                 .mean().round(4).to_dict("index"),
        "by_time": dyn.groupby("time")[["detour", "sun_red", "exposure_red"]].mean().round(4).to_dict("index"),
        "ellipse_vs_unbounded_paths_differ": f"{diff}/50",
        "networkx_fixed_theta_max_abs_err": maxerr, "networkx_nodes_checked": checked,
        "ms_per_pair_search": t_pair / n_pair * 1e3, "ms_per_one_to_many_3km": ms_one_to_many,
    })
    (cfg["paths"]["tables"] / "phase_05_stats.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps({k: v for k, v in res.items() if k != "by_time"}, indent=2, default=float))

    # figure
    fig, ax = plt.subplots(1, 3, figsize=(18, 5))
    h = dyn[i15]
    ax[0].hist(h.detour * 100, bins=30)
    ax[0].set(title=f"Detour, {len(ods)} city ODs, 06-21 15:00", xlabel="%")
    ax[1].hist(h.sun_red.dropna() * 100, bins=30, color="#f2b705")
    ax[1].set(title="Sun-exposure reduction, 06-21 15:00", xlabel="%")
    bt = dyn.groupby("time")[["detour", "sun_red", "exposure_red"]].mean()
    x = np.arange(len(bt))
    ax[2].plot(x, bt.sun_red * 100, "o-", label="sun reduction %")
    ax[2].plot(x, bt.exposure_red * 100, "^-", label="weighted exposure reduction %")
    ax[2].plot(x, bt.detour * 100, "s-", label="detour %")
    ax[2].set_xticks(x, bt.index, rotation=90, fontsize=7)
    ax[2].legend()
    ax[2].set_title("By time (dynamic θ)")
    plt.tight_layout()
    plt.savefig(figs / "phase_05_routing.png", dpi=110)


if __name__ == "__main__":
    main()
