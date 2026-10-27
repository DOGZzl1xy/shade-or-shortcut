"""T6: shortest vs shade routing for 50 random test-area OD pairs x 24 times; timing."""

import json
import time

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from coolpath import load_config
from coolpath.routing import CSR, path_edges, path_totals, search, theta_params

OD_MIN_M, OD_MAX_M = 400.0, 2000.0     # phase-0 OD distance band (typical walk)


def main():
    cfg = load_config()
    rc = cfg["routing"]
    figs, out = cfg["paths"]["figures"], cfg["paths"]["interim"] / "test"
    rng = np.random.default_rng(cfg["project"]["seed"])

    pts = np.load(out / "shadow_points.npz", allow_pickle=True)
    edges = pd.read_parquet(out / "edges_undirected_attrs.parquet")
    labels = list(pts["labels"])
    m, T = len(edges), len(labels)
    st, eid, seg = pts["state"], pts["eid"], pts["seg"]
    Ls = np.stack([np.bincount(eid, weights=seg * (st[:, t] == 0), minlength=m) for t in range(T)], 1)
    Lt = np.stack([np.bincount(eid, weights=seg * (st[:, t] == 1), minlength=m) for t in range(T)], 1)
    Lb = np.stack([np.bincount(eid, weights=seg * (st[:, t] == 2), minlength=m) for t in range(T)], 1)
    length = edges["length"].to_numpy()
    assert np.allclose(Ls + Lt + Lb, length[:, None], atol=1e-6)

    nodes, inv = np.unique(np.concatenate([edges.u, edges.v]), return_inverse=True)
    u, v = inv[:m], inv[m:]
    g = CSR(u, v, nodes.size)
    tp = theta_params(cfg)
    tf = rc["tree_factor"]
    zero = np.zeros(m)

    # OD sampling: random origin, random destination within the distance band
    ods = []
    while len(ods) < cfg["trips"]["feasibility_n"]:
        o = int(rng.integers(nodes.size))
        _, d, _, _ = search(g, length, zero, zero, zero, o, "shortest", max_dist=OD_MAX_M)
        cand = np.where((d >= OD_MIN_M) & (d <= OD_MAX_M))[0]
        if cand.size:
            ods.append((o, int(rng.choice(cand)), float(d[cand].max())))

    rows = []
    t_short = t_shade = 0.0
    n_shade = 0
    for k, (o, dst, _) in enumerate(ods):
        t0 = time.time()
        _, d, pred, pe = search(g, length, zero, zero, zero, o, "shortest")
        t_short += time.time() - t0
        sp = path_edges(pred, pe, dst)
        dmax = d[dst] * rc["search_radius_factor"]
        for t in range(T):
            runs = [("dynamic", -1.0)]
            if labels[t].endswith("_15"):
                runs += [(f"fixed_{x}", x) for x in rc["theta_fixed_sensitivity"]]
            for name, thf in runs:
                t0 = time.time()
                _, _, pr, pe2 = search(g, length, Ls[:, t].copy(), Lt[:, t].copy(), Lb[:, t].copy(), o, "shade",
                                       theta_fixed=thf, tp=tp, tree_factor=tf, max_dist=dmax)
                t_shade += time.time() - t0
                n_shade += 1
                cp = path_edges(pr, pe2, dst)
                a = path_totals(sp, length, Ls[:, t], Lt[:, t], Lb[:, t])
                b = path_totals(cp, length, Ls[:, t], Lt[:, t], Lb[:, t])
                rows.append({"od": k, "o": o, "d": dst, "time": labels[t], "theta": name,
                             "short_len": a["length"], "short_sun": a["sun"], "short_tree": a["tree"],
                             "short_bldg": a["bldg"], "shade_len": b["length"], "shade_sun": b["sun"],
                             "shade_tree": b["tree"], "shade_bldg": b["bldg"],
                             "same_path": bool(np.array_equal(np.sort(sp), np.sort(cp)))})
    df = pd.DataFrame(rows)
    df["detour"] = (df.shade_len - df.short_len) / df.short_len
    df["sun_red"] = np.where(df.short_sun > 0, (df.short_sun - df.shade_sun) / df.short_sun.where(df.short_sun > 0), np.nan)
    df.to_parquet(out / "t6_trips.parquet")
    dyn = df[df.theta == "dynamic"]

    # one-to-all timing with the spec's radius bound (farthest destination x 1.5)
    t0 = time.time()
    for o, _, far in ods:
        search(g, length, Ls[:, 0].copy(), Lt[:, 0].copy(), Lb[:, 0].copy(), o, "shade", tp=tp,
               tree_factor=tf, max_dist=far * rc["search_radius_factor"])
    per_one_to_all = (time.time() - t0) / len(ods)

    by_time = dyn.groupby("time").agg(detour_mean=("detour", "mean"), sun_red_mean=("sun_red", "mean"),
                                      sun_red_median=("sun_red", "median"), changed=("same_path", lambda s: 1 - s.mean()))
    sens = df[df.time.str.endswith("_15")].groupby("theta").agg(detour=("detour", "mean"), sun_red=("sun_red", "mean"),
                                                               changed=("same_path", lambda s: 1 - s.mean()))
    res = {
        "graph_nodes": int(nodes.size), "graph_edges": int(m), "n_od": len(ods), "n_times": T,
        "od_band_m": [OD_MIN_M, OD_MAX_M],
        "od_short_len_m": {"median": float(dyn.short_len.median()), "min": float(dyn.short_len.min()),
                           "max": float(dyn.short_len.max())},
        "negative_detour_count": int((dyn.detour < -1e-9).sum()),
        "sun_increase_count": int((dyn.shade_sun > dyn.short_sun + 1e-6).sum()),
        "sun_increase_max_m": float((dyn.shade_sun - dyn.short_sun).max()),
        "short_sun_zero_count": int((dyn.short_sun <= 0).sum()),
        "detour_mean": float(dyn.detour.mean()), "detour_p95": float(dyn.detour.quantile(0.95)),
        "detour_max": float(dyn.detour.max()),
        "sun_red_mean": float(dyn.sun_red.mean()), "sun_red_median": float(dyn.sun_red.median()),
        "path_changed_share": float(1 - dyn.same_path.mean()),
        "by_time": by_time.reset_index().to_dict("records"),
        "theta_sensitivity_15h": sens.reset_index().to_dict("records"),
        "ms_per_shortest": t_short / len(ods) * 1e3, "ms_per_shade_pair": t_shade / n_shade * 1e3,
        "ms_per_shade_one_to_all_radius": per_one_to_all * 1e3,
    }
    (cfg["paths"]["tables"] / "t6_routing.json").write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k not in ("by_time",)}, indent=2))
    print(by_time.to_string())

    # figure: distributions + two example routes at 15:00
    fig = plt.figure(figsize=(18, 10))
    a1, a2, a3 = fig.add_subplot(2, 3, 1), fig.add_subplot(2, 3, 2), fig.add_subplot(2, 3, 3)
    h15 = dyn[dyn.time == "0621_15"]
    a1.hist(h15.detour * 100, bins=25)
    a1.set(title="Detour ratio, 50 ODs, 06-21 15:00", xlabel="%")
    a2.hist(h15.sun_red.dropna() * 100, bins=25, color="#f2b705")
    a2.set(title="Sun-exposure reduction, 06-21 15:00", xlabel="%")
    x = np.arange(len(by_time))
    a3.plot(x, by_time.sun_red_mean * 100, "o-", label="mean sun reduction %")
    a3.plot(x, by_time.detour_mean * 100, "s-", label="mean detour %")
    a3.set_xticks(x, by_time.index, rotation=90, fontsize=7)
    a3.legend()
    a3.set_title("By time (dynamic θ)")
    eg = gpd.read_file(out / "graph_test.gpkg", layer="edges")
    eg = eg.drop_duplicates("osmid").set_index("osmid").loc[edges["osmid"]].reset_index()
    t15 = labels.index("0621_15")
    shade_frac = 1 - Ls[:, t15] / length
    pick = h15.assign(gain=h15.short_sun - h15.shade_sun).nlargest(2, "gain")
    for j, (_, r) in enumerate(pick.iterrows()):
        ax = fig.add_subplot(2, 3, 4 + j)
        eg.plot(ax=ax, column=shade_frac, cmap="Greys", lw=0.6, vmin=0, vmax=1.5)
        _, _, p1, e1 = search(g, length, zero, zero, zero, int(r.o), "shortest")
        _, _, p2, e2 = search(g, length, Ls[:, t15].copy(), Lt[:, t15].copy(), Lb[:, t15].copy(), int(r.o),
                              "shade", tp=tp, tree_factor=tf)
        eg.iloc[path_edges(p1, e1, int(r.d))].plot(ax=ax, color="red", lw=3, label="shortest")
        eg.iloc[path_edges(p2, e2, int(r.d))].plot(ax=ax, color="blue", lw=2, label="shade")
        xs = eg.iloc[np.concatenate([path_edges(p1, e1, int(r.d)), path_edges(p2, e2, int(r.d))])].total_bounds
        ax.set_xlim(xs[0] - 150, xs[2] + 150)
        ax.set_ylim(xs[1] - 150, xs[3] + 150)
        ax.legend()
        ax.set_title(f"OD {r.od}: +{r.detour:.0%} length, −{r.sun_red:.0%} sun (06-21 15:00)")
        ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "t6_routing.png", dpi=110)


if __name__ == "__main__":
    main()
