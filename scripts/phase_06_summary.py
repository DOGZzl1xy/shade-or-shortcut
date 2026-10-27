"""Phase 6 summary: metrics by sample / mode / length bin / time, θ sensitivity, the decomposition
of the gap between sun reduction and weighted-exposure reduction, and the map of parcels with no
home stop within 1.2 km (D-038).

Identity used for the decomposition: C = L - 0.5 θ L_tree - θ L_bldg (θ varying along the path), so
    C_short - C_shade = -(L_shade - L_short)  +  G,
where G = (C_short - C_shade) + (L_shade - L_short) is the θ-weighted shade gained on the shade path.
"""

import json

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from coolpath import load_config
from coolpath.areas import city
from coolpath.transit import home_stops


def summarise(d):
    return pd.Series({
        "trips": d.trip_id.nunique(), "detour_mean": d.detour.mean(), "detour_p95": d.detour.quantile(0.95),
        "sun_red_mean": d.sun_red.mean(), "sun_red_median": d.sun_red.median(), "sun_red_missing": int(d.sun_red.isna().sum()),
        "exposure_red_mean": d.exposure_red.mean(), "sun_increase_share": d.sun_increase.mean(),
        "short_sun_share": d.short_sun_share.mean(), "short_tree_share": d.short_tree_share.mean(),
        "short_bldg_share": d.short_bldg_share.mean(), "shade_tree_share": d.shade_tree_share.mean(),
        "shade_bldg_share": d.shade_bldg_share.mean(), "path_changed": 1 - d.same_path.mean()})


def group(d, keys):
    return d.groupby(keys, observed=True).apply(summarise, include_groups=False).round(4)


def gap(d):
    d = d[d.short_C > 0]
    detour_m = d.shade_len - d.short_len
    dC = d.short_C - d.shade_C
    sun_moved = (d.short_sun - d.shade_sun).sum()
    return {"trips_x_times": int(len(d)), "short_len_mean_m": float(d.short_len.mean()),
            "sun_red_mean_of_ratios": float(d.sun_red.mean()), "exposure_red_mean_of_ratios": float(d.exposure_red.mean()),
            "sun_red_ratio_of_sums": float(sun_moved / d.short_sun.sum()), "exposure_red_ratio_of_sums": float(dC.sum() / d.short_C.sum()),
            "weighted_gain_per_sunny_metre_avoided": float((dC + detour_m).sum() / sun_moved),
            "detour_m_per_sunny_metre_avoided": float(detour_m.sum() / sun_moved),
            "net_C_saved_per_sunny_metre_avoided": float(dC.sum() / sun_moved)}


def main():
    cfg = load_config()
    proc, figs, rep = cfg["paths"]["processed"], cfg["paths"]["figures"], cfg["paths"]["tables"]
    df = pd.read_parquet(proc / "trips_metrics.parquet")
    df["time"] = df.time.astype(str)
    dyn = df[df.theta == "dynamic"]
    d15 = dyn[dyn.time.str.endswith("_15")]
    res = {"overall": group(dyn, ["sample", "mode", "length_bin"]).reset_index().to_dict("records"),
           "at_15h": group(d15, ["sample", "mode", "length_bin"]).reset_index().to_dict("records"),
           "at_15h_all_bins": group(d15, ["sample", "mode"]).reset_index().to_dict("records")}
    city_trips = d15[d15["sample"] == "city"]
    res["city_zero_length_trips"] = int((city_trips.drop_duplicates("trip_id").short_len == 0).sum())
    bt = group(dyn, ["sample", "mode", "length_bin", "time"])
    bt.to_csv(rep / "phase_06_by_time.csv")
    st = group(d15[d15["sample"] == "station"], ["origin", "length_bin"])
    st.to_csv(rep / "phase_06_stations_15h.csv")
    res["theta_sensitivity_15h"] = (df[df.time.str.endswith("_15")].groupby(["sample", "mode", "length_bin", "theta"], observed=True)
                                    [["detour", "sun_red", "exposure_red"]].mean().round(4).reset_index().to_dict("records"))
    res["gap_explanation"] = {f"{s}_{b}": gap(x) for (s, b), x in dyn.groupby(["sample", "length_bin"], observed=True)}
    (rep / "phase_06_summary.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps({k: res[k] for k in ("at_15h", "gap_explanation")}, indent=2, default=float))

    # ---- figures: reduction / detour by time, per sample x mode, one panel per length bin
    bins = list(df.length_bin.cat.categories)
    fig, ax = plt.subplots(2, len(bins), figsize=(7 * len(bins), 9), sharey="row")
    styles = {("city", "bus"): ("#1f77b4", "o"), ("city", "rapid"): ("#ff7f0e", "s"), ("station", "rapid"): ("#2a9d3a", "^")}
    for j, b in enumerate(bins):
        for (smp, mode), (c, mk) in styles.items():
            if (smp, mode, b) not in bt.index:
                continue
            x = bt.loc[(smp, mode, b)]
            t = np.arange(len(x))
            ax[0, j].plot(t, x.sun_red_mean * 100, color=c, marker=mk, ms=4, label=f"{smp} / {mode}: sun reduction")
            ax[0, j].plot(t, x.exposure_red_mean * 100, color=c, marker=mk, ms=4, ls="--", label=f"{smp} / {mode}: weighted exposure")
            ax[1, j].plot(t, x.detour_mean * 100, color=c, marker=mk, ms=4, label=f"{smp} / {mode}")
        ax[0, j].set_title(f"trip length {b} m: mean reduction (dynamic θ)")
        ax[1, j].set_title(f"trip length {b} m: mean detour")
        for a in ax[:, j]:
            a.set_xticks(t, x.index, rotation=90, fontsize=7)
            a.grid(alpha=0.3)
            a.set_ylabel("%")
    ax[0, 0].legend(fontsize=7)
    ax[1, 0].legend(fontsize=7)
    plt.tight_layout()
    plt.savefig(figs / "phase_06_metrics.png", dpi=100)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    keys = [k for k in res["gap_explanation"] if k.startswith("city")]
    xs = np.arange(len(keys))
    gg = [res["gap_explanation"][k] for k in keys]
    ax.bar(xs - 0.25, [g["weighted_gain_per_sunny_metre_avoided"] for g in gg], 0.25, color="#2a9d3a", label="θ-weighted shade gained")
    ax.bar(xs, [-g["detour_m_per_sunny_metre_avoided"] for g in gg], 0.25, color="#c0392b", label="detour metres")
    ax.bar(xs + 0.25, [g["net_C_saved_per_sunny_metre_avoided"] for g in gg], 0.25, color="#2b2d7a", label="net cost saved")
    ax.set_xticks(xs, [k.replace("city_", "city, ") + " m" for k in keys])
    ax.axhline(0, color="k", lw=0.8)
    ax.set(ylabel="metres of cost C per sunny metre avoided", title="Why weighted exposure falls much less than sun exposure")
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(figs / "phase_06_gap.png", dpi=110)

    # ---- parcels with no home stop within 1.2 km
    crs = cfg["project"]["crs"]
    unc = pd.read_parquet(proc / "trips_city_uncovered.parquet")
    par = pd.read_parquet(proc / "trips_city_parcels.parquet")
    stops = home_stops(cfg)
    fig, ax = plt.subplots(figsize=(11, 12))
    gpd.GeoSeries([city(cfg)], crs=crs).boundary.plot(ax=ax, color="k", lw=0.7)
    ax.scatter(unc.x, unc.y, s=0.2, c="#d62728", alpha=0.4, label=f"no home stop within 1.2 km ({len(unc):,} parcels)")
    sm = par.sample(min(len(par), 30000), random_state=cfg["project"]["seed"])
    ax.scatter(sm.x, sm.y, s=0.2, c="#888", alpha=0.3, label="sampled parcels (30k shown)")
    for k, c, s in (("surface", "#1f77b4", 3), ("Regional Rail", "#2a9d3a", 16), ("MFL", "#ff7f0e", 16),
                    ("BSL", "#ff7f0e", 16), ("trolley tunnel", "#9467bd", 16)):
        ss = stops[stops.kind == k]
        ax.scatter(ss.geometry.x, ss.geometry.y, s=s, c=c, label=f"{k} ({len(ss)})")
    cx.add_basemap(ax, crs=crs, source=cx.providers.Esri.WorldGrayCanvas, zoom=11)
    ax.legend(loc="lower right", fontsize=8, markerscale=3)
    ax.set_title(f"Residential parcels without a home stop within 1.2 km network distance\n"
                 f"SEPTA GTFS fall 2026 timetable (v202609271), weekday {cfg['trips']['home_stops']['rep_date']} 15:00–19:00, "
                 f"surface stops ≥ {cfg['trips']['home_stops']['min_per_hour']}/h")
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_06_uncovered.png", dpi=100)


if __name__ == "__main__":
    main()
