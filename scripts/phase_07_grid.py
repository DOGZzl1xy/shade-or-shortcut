"""Phase 7 (D-038): summaries by mode and trip-length bin, rapid-transit stations, 500 m grid of
the high-frequency bus trips, K-means on the grid (k from config, D-040), CP1 stations as cases.

    uv run python -m scripts.phase_07_grid distribution   # histogram + CDF used to pick the threshold
    uv run python -m scripts.phase_07_grid                # everything (needs metrics.no_alt_threshold)

Indicators (city sample, dynamic θ), main time 15:00 (both dates pooled):
  sun_share      mean sun share of the shortest path
  sun_red        mean achievable sun reduction (trips with no sun on the shortest path excluded)
  detour         mean detour ratio
  no_alt_share   share of trips whose sun reduction < threshold ("no cooler alternative")
Every comparison between stations or places is made within one trip-length bin.
Grid: bus trips are assigned to the 500 m cell of their home parcel; cells are aligned to the
1 m surface rasters. K-means uses the cluster_length_bin trips of cells with >= min_trips_per_cell
trips; cell features (building height, canopy cover, network density) only describe the clusters.
"""

import json
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import statsmodels.api as sm
from rasterio.features import geometry_mask
from rasterio.windows import Window
from shapely.geometry import box
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from coolpath import load_config
from coolpath.areas import city
from coolpath.transit import home_stops

IND = ["sun_share", "sun_red", "detour", "no_alt_share"]
FEAT = ["bldg_height_mean_m", "canopy_cover", "network_km_per_km2"]
T15 = ["0621_15", "0801_15"]


def load(cfg):
    df = pd.read_parquet(cfg["paths"]["processed"] / "trips_metrics.parquet")
    df["time"] = df.time.astype(str)
    df["theta"] = df.theta.astype(str)
    return df[df.theta == "dynamic"]


def distribution(cfg, df):
    figs = cfg["paths"]["figures"]
    times = ["0621_09", "0621_12", "0621_15", "0621_18", "0801_15"]
    d = df[df.network_dist_m >= 300]
    fig, ax = plt.subplots(2, 2, figsize=(15, 9))
    for j, smp in enumerate(("city", "station")):
        x = d[d["sample"] == smp]
        for t in times:
            v = x.loc[x.time == t, "sun_red"].dropna().clip(lower=-0.2) * 100
            ax[0, j].hist(v, bins=np.arange(-20, 101, 1), histtype="step", lw=1.3, label=t)
            xs = np.sort(v)
            ax[1, j].plot(xs, np.arange(1, xs.size + 1) / xs.size, label=t)
        ax[0, j].set(title=f"{smp} sample, trips 300–1,200 m: sun reduction per trip", xlabel="%", ylabel="trips")
        ax[0, j].set_yscale("log")
        ax[1, j].set(title=f"{smp} sample, trips 300–1,200 m: cumulative distribution", xlabel="sun reduction (%)",
                     ylabel="share of trips")
        ax[1, j].set_xlim(-2, 40)
        ax[1, j].set_xticks(np.arange(0, 41, 2))
        for a in ax[:, j]:
            a.legend(fontsize=8)
            a.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(figs / "phase_07_sun_red_distribution.png", dpi=110)
    out = {}
    for smp in ("city", "station"):
        v = d[(d["sample"] == smp) & d.time.isin(T15)].sun_red.dropna()
        out[smp] = {"n": int(v.size), "share_exactly_0": float((v.abs() < 1e-9).mean()),
                    "share_below": {f"{t:.2f}": float((v < t).mean()) for t in np.arange(0.01, 0.21, 0.01)},
                    "cdf_step_per_1pct": [round(float(((v >= t) & (v < t + 0.01)).mean()), 4) for t in np.arange(0, 0.2, 0.01)]}
    (cfg["paths"]["tables"] / "phase_07_distribution.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


def indicators(d, thr, sens=()):
    s = pd.Series({"trips": d.trip_id.nunique(), "sun_share": d.short_sun_share.mean(), "sun_red": d.sun_red.mean(),
                   "detour": d.detour.mean(), "no_alt_share": (d.sun_red.dropna() < thr).mean(),
                   "exposure_red": d.exposure_red.mean()})
    for t in sens:
        s[f"no_alt_share_{t}"] = (d.sun_red.dropna() < t).mean()
    return s


def table(d, keys, thr, sens=()):
    return d.groupby(keys, observed=True).apply(lambda x: indicators(x, thr, sens), include_groups=False)


def cell_features(cfg, cells, x0, y0, n_rows, n_cols, gm):
    """Per 500 m cell: mean building height over building pixels, canopy cover and network density
    over the part of the cell inside the city."""
    proc = cfg["paths"]["processed"]
    poly = city(cfg)
    hsum = np.zeros((n_rows, n_cols))
    hcnt = np.zeros((n_rows, n_cols))
    tcnt = np.zeros((n_rows, n_cols))
    land = np.zeros((n_rows, n_cols))
    with rasterio.open(proc / "surface_building.tif") as sb, rasterio.open(proc / "surface_tree.tif") as stt:
        W = n_cols * gm
        for r in range(n_rows):
            win = Window(0, r * gm, min(W, sb.width), min(gm, sb.height - r * gm))
            hb = sb.read(1, window=win).astype(np.float32) / 10
            ht = stt.read(1, window=win)
            inside = ~geometry_mask([poly], hb.shape, sb.window_transform(win))
            pad = lambda a: np.pad(a, ((0, gm - a.shape[0]), (0, W - a.shape[1])))  # noqa: E731
            hb, ht, inside = pad(hb), pad(ht), pad(inside)
            blk = lambda a: a.reshape(gm, n_cols, gm).sum(axis=(0, 2))  # noqa: E731
            b = (hb > 0) & inside
            hsum[r] = blk(np.where(b, hb, 0))
            hcnt[r] = blk(b)
            tcnt[r] = blk((ht > 0) & inside)
            land[r] = blk(inside)
    pts = pd.read_parquet(proc / "shadow_points.parquet", columns=["x", "y", "seg_m"])
    ci = ((pts.x - x0) // gm).astype(int)
    ri = ((y0 - pts.y) // gm).astype(int)
    ok = (ci >= 0) & (ci < n_cols) & (ri >= 0) & (ri < n_rows)
    net = np.bincount((ri[ok] * n_cols + ci[ok]), weights=pts.seg_m[ok], minlength=n_rows * n_cols).reshape(n_rows, n_cols)
    r, c = cells.row.to_numpy(), cells.col.to_numpy()
    land_km2 = land[r, c] / 1e6
    return pd.DataFrame({"bldg_height_mean_m": np.where(hcnt[r, c] > 0, hsum[r, c] / np.maximum(hcnt[r, c], 1), 0.0),
                         "bldg_coverage": hcnt[r, c] / np.maximum(land[r, c], 1),
                         "canopy_cover": tcnt[r, c] / np.maximum(land[r, c], 1),
                         "network_km_per_km2": net[r, c] / 1e3 / np.maximum(land_km2, 1e-6),
                         "land_km2": land_km2}, index=cells.index)


def main():
    cfg = load_config()
    proc, figs, rep = cfg["paths"]["processed"], cfg["paths"]["figures"], cfg["paths"]["tables"]
    df = load(cfg)
    if "distribution" in sys.argv[1:]:
        distribution(cfg, df)
        return
    mc = cfg["metrics"]
    thr, sens = mc["no_alt_threshold"], mc["no_alt_threshold_sensitivity"]
    crs, seed = cfg["project"]["crs"], cfg["project"]["seed"]
    d15 = df[df.time.isin(T15)]
    cty = d15[d15["sample"] == "city"]
    res = {"threshold": thr, "sensitivity": sens}

    # ---- by mode x length bin
    mb = table(cty, ["mode", "length_bin"], thr, sens)
    res["by_mode_bin_15h"] = mb.round(4).reset_index().to_dict("records")
    allt = table(df[df["sample"] == "city"], ["mode", "length_bin"], thr)
    res["by_mode_bin_alltimes"] = allt.round(4).reset_index().to_dict("records")

    # ---- rapid transit: one row per station x bin
    stops = home_stops(cfg)
    rap = stops[stops["mode"] == "rapid"].dissolve("station", aggfunc={"kind": "first"}).centroid
    st = table(cty[cty["mode"] == "rapid"], ["station", "length_bin"], thr).reset_index()
    st = st[st.trips >= mc["min_trips_per_station"]]
    st.round(4).to_csv(rep / "phase_07_rapid_stations.csv", index=False)
    stg = gpd.GeoDataFrame(st, geometry=rap.reindex(st.station).to_numpy(), crs=crs)
    stg.to_parquet(proc / "stations_metrics.parquet")
    res["rapid_stations"] = {str(b): {"stations": int(len(x)), **x[IND].describe().loc[["min", "50%", "max"]].round(4).to_dict()}
                             for b, x in st.groupby("length_bin", observed=True)}

    # ---- bus trips -> 500 m grid of the home parcel
    gm = int(mc["grid_m"])
    with rasterio.open(proc / "surface_building.tif") as s:
        x0, y0, W, H = s.bounds.left, s.bounds.top, s.width, s.height
    n_cols, n_rows = -(-W // gm), -(-H // gm)
    par = pd.read_parquet(proc / "trips_city_parcels.parquet", columns=["x", "y"])
    par["trip_id"] = "P" + par.index.astype(str)
    bus = cty[cty["mode"] == "bus"].merge(par, on="trip_id")
    bus["col"] = ((bus.x - x0) // gm).astype(int)
    bus["row"] = ((y0 - bus.y) // gm).astype(int)
    grid = table(bus, ["row", "col", "length_bin"], thr, sens).reset_index()
    cells = grid[["row", "col"]].drop_duplicates().reset_index(drop=True)
    cells = pd.concat([cells, cell_features(cfg, cells, x0, y0, n_rows, n_cols, gm)], axis=1)
    geom = [box(x0 + c * gm, y0 - (r + 1) * gm, x0 + (c + 1) * gm, y0 - r * gm) for r, c in zip(cells.row, cells.col)]
    cells = gpd.GeoDataFrame(cells, geometry=geom, crs=crs)
    wide = grid.pivot_table(index=["row", "col"], columns="length_bin", values=IND + ["trips", "exposure_red"], observed=True)
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    cells = cells.merge(wide.reset_index(), on=["row", "col"])

    # ---- K-means on the cluster bin
    cb = mc["cluster_length_bin"]
    use = cells[cells[f"trips_{cb}"] >= mc["min_trips_per_cell"]].copy()
    X = use[[f"{c}_{cb}" for c in IND]].to_numpy()
    Z = StandardScaler().fit_transform(X)
    sil, models = {}, {}
    for k in range(3, 7):
        km = KMeans(n_clusters=k, n_init=50, random_state=seed).fit(Z)
        sil[k] = float(silhouette_score(Z, km.labels_))
        models[k] = km
    kbest = mc["cluster_k"]                  # D-040: user-chosen k; silhouette reported for k = 3-6
    use["cluster"] = models[kbest].labels_
    cells = cells.merge(use[["row", "col", "cluster"]], on=["row", "col"], how="left")
    prof = use.groupby("cluster").agg(cells=("row", "size"), trips=(f"trips_{cb}", "sum"),
                                      **{c: (f"{c}_{cb}", "mean") for c in IND + ["exposure_red"]},
                                      **{f: (f, "median") for f in FEAT + ["bldg_coverage"]})
    res.update({"grid": {"cells_with_bus_trips": int(len(cells)), "cells_clustered": int(len(use)),
                         "cluster_bin": cb, "min_trips_per_cell": mc["min_trips_per_cell"]},
                "silhouette": sil, "k": kbest, "cluster_profile": prof.round(4).to_dict("index")})

    # ---- what explains the cell indicators (cluster bin): Spearman + OLS on standardized features
    res["spearman_features"] = {y: {f: float(use[[f"{y}_{cb}", f]].corr(method="spearman").iloc[0, 1]) for f in FEAT}
                                for y in IND}
    F = use[FEAT].copy()
    F["bldg_height_mean_m"] = np.log1p(F.bldg_height_mean_m)
    Fz = sm.add_constant((F - F.mean()) / F.std())
    ols = {}
    for y in IND:
        m = sm.OLS(use[f"{y}_{cb}"], Fz).fit(cov_type="HC3")
        ols[y] = {"n": int(m.nobs), "r2": float(m.rsquared), "coef": m.params.round(5).to_dict(), "p": m.pvalues.round(4).to_dict()}
        (rep / f"phase_07_ols_{y}.txt").write_text(m.summary().as_text())
    res["ols_features"] = ols
    res["feature_corr"] = F.corr(method="spearman").round(3).to_dict()

    # ---- CP1 stations as cases (station sample, own destinations within 1 km)
    cases = table(d15[d15["sample"] == "station"], ["origin", "length_bin"], thr).reset_index()
    cases.round(4).to_csv(rep / "phase_07_cp1_cases.csv", index=False)
    res["cp1_cases"] = cases.round(4).to_dict("records")

    cells.to_parquet(proc / "grid_metrics.parquet")
    (rep / "phase_07_stats.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps({k: v for k, v in res.items() if k not in ("cp1_cases", "by_mode_bin_alltimes")}, indent=2, default=float))

    # ---- figures
    cb_poly = gpd.GeoSeries([city(cfg)], crs=crs)
    titles = {"sun_share": "Sun share of shortest path", "sun_red": "Achievable sun reduction",
              "detour": "Detour of shade path", "no_alt_share": f"No cooler alternative (< {thr:.0%})"}
    fig, ax = plt.subplots(1, 4, figsize=(26, 8))
    for a, c in zip(ax, IND):
        cb_poly.boundary.plot(ax=a, color="k", lw=0.6)
        cells[cells[f"trips_{cb}"].fillna(0) < mc["min_trips_per_cell"]].plot(ax=a, color="#e5e5e5")
        cells[cells[f"trips_{cb}"] >= mc["min_trips_per_cell"]].plot(ax=a, column=f"{c}_{cb}",
                                                                     cmap="viridis" if c == "sun_red" else "magma_r",
                                                                     legend=True, legend_kwds={"shrink": 0.5})
        a.set_title(f"{titles[c]}\nbus trips {cb} m, 15:00, 500 m cells (≥ {mc['min_trips_per_cell']} trips)")
        a.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_07_grid_indicators.png", dpi=90)

    fig, ax = plt.subplots(1, 2, figsize=(20, 9), gridspec_kw={"width_ratios": [1.1, 1]})
    cb_poly.boundary.plot(ax=ax[0], color="k", lw=0.6)
    cells[cells.cluster.isna()].plot(ax=ax[0], color="#e5e5e5")
    cl = cells[cells.cluster.notna()]
    cl.assign(label="cluster " + cl.cluster.astype(int).astype(str)).plot(ax=ax[0], column="label", categorical=True,
                                                                           cmap="tab10", legend=True)
    ax[0].set_title(f"K-means on 500 m cells (bus trips {cb} m, 15:00), k = {kbest}, silhouette {sil[kbest]:.2f}")
    ax[0].set_axis_off()
    pos = np.arange(kbest)
    for i, f in enumerate(FEAT):
        sub = ax[1].inset_axes([0.0, 1 - (i + 1) / 3 + 0.06, 1, 1 / 3 - 0.1])
        sub.boxplot([use.loc[use.cluster == k, f] for k in range(kbest)], positions=pos, showfliers=False)
        sub.set_ylabel(f, fontsize=9)
        if f == "bldg_height_mean_m":
            sub.set_yscale("log")
        sub.set_xticks(pos, [f"cluster {k}" for k in range(kbest)], fontsize=8)
    ax[1].set_axis_off()
    ax[1].set_title("Cell features by cluster (not used for clustering)")
    plt.tight_layout()
    plt.savefig(figs / "phase_07_clusters.png", dpi=90)

    fig, ax = plt.subplots(1, 3, figsize=(24, 8))
    for a, b in zip(ax, ["<300", "300-800", "800-1200"]):
        x = stg[stg.length_bin == b]
        cb_poly.boundary.plot(ax=a, color="k", lw=0.6)
        x.plot(ax=a, column="sun_red", cmap="viridis", markersize=40, legend=True, legend_kwds={"shrink": 0.5},
               vmin=0, vmax=max(stg.sun_red.quantile(0.95), 0.01))
        a.set_title(f"Rapid-transit stations, trips {b} m (≥ {mc['min_trips_per_station']} trips): "
                    f"achievable sun reduction, 15:00 ({len(x)} stations)", fontsize=10)
        a.set_axis_off()
    plt.tight_layout()
    plt.savefig(figs / "phase_07_rapid_stations.png", dpi=90)


if __name__ == "__main__":
    main()
