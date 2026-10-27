"""Phase 6: trip samples and per-trip, per-time metrics.

    caffeinate -i uv run python -m scripts.phase_06_trips

Station sample (D-035): from each of the 10 CP1 stations, 60 destinations within 1 km network
distance — OSM entrances first, building-footprint centroids to fill (D-036).
City sample (D-038): every residential OPA parcel is assigned to the network-nearest home stop
(rapid transit + surface stops with >= 6 departures / hour of one route-direction, weekday 15-19 h,
SEPTA GTFS fall 2026); parcels farther than 1.2 km are dropped (counted and mapped); every parcel within
1.2 km is one trip (D-040; rapid-transit platforms merged into stations). Trips run stop -> home, so each
stop needs one one-to-many search per time (radius = farthest assigned parcel x 1.5).
For every trip and time: shortest path (computed once) and shade path (dynamic θ), plus fixed θ
0.2 / 0.4 / 0.6 at 15:00; path totals are accumulated along the search trees (tree_totals).
Metrics include weighted exposure C (D-011).
Direction check: 500 city trips of >= 400 m recomputed home -> stop with pair searches (ellipse bound).
"""

import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from coolpath import load_config
from coolpath.areas import city
from coolpath.routing import (load_city_graph, nearest_source, path_cost, path_edges, search, theta_params,
                              tree_totals)
from coolpath.transit import home_stops
from scripts.phase_06_station_candidates import stations

def snap(g, x, y):
    d, k = cKDTree(np.c_[g.x, g.y]).query(np.c_[x, y])
    return k.astype(np.int64), d


def totals(p, L, ls, lt, lb):
    return L[p].sum(), ls[p].sum(), lt[p].sum(), lb[p].sum()


def run_origin(g, L, shade, labels, o, dests, radius, tp, tf, fixed):
    """All trips from origin node o to dest nodes: shortest once, shade per time (+ fixed θ at 15:00).

    Yields (time, theta name, short totals, shade totals); totals are (n_dest, 6) arrays of
    length, sun, tree, bldg, C, same_path (short: C of the shortest path under the same θ)."""
    zero = np.zeros_like(L)
    _, _, pr, pe = search(g, L, zero, zero, zero, o, "shortest", max_dist=radius)
    for lab in labels:
        ls, lt, lb = shade[lab]
        runs = [("dynamic", -1.0)] + ([(f"fixed_{x}", float(x)) for x in fixed] if lab.endswith("_15") else [])
        for name, thf in runs:
            _, _, pr2, pe2 = search(g, L, ls, lt, lb, o, "shade", theta_fixed=thf, tp=tp, tree_factor=tf, max_dist=radius)
            A = tree_totals(pr, pe, o, dests, L, ls, lt, lb, tp, tf, thf, pe)
            B = tree_totals(pr2, pe2, o, dests, L, ls, lt, lb, tp, tf, thf, pe)
            yield lab, name, A, B


class Collector:
    """Accumulates run_origin output as arrays: one row per trip x time x θ."""

    def __init__(self):
        self.trip, self.time, self.theta, self.A, self.B = [], [], [], [], []

    def add(self, trip_idx, runs):
        for lab, name, A, B in runs:
            ok = np.isfinite(A[:, 0]) & np.isfinite(B[:, 0])
            self.trip.append(trip_idx[ok])
            self.time += [lab] * int(ok.sum())
            self.theta += [name] * int(ok.sum())
            self.A.append(A[ok])
            self.B.append(B[ok])

    def frame(self, trips):
        A, B = np.concatenate(self.A), np.concatenate(self.B)
        m = trips.iloc[np.concatenate(self.trip)].reset_index(drop=True)
        m["time"] = pd.Categorical(self.time)
        m["theta"] = pd.Categorical(self.theta)
        for j, k in enumerate(("len", "sun", "tree", "bldg")):
            m[f"short_{k}"] = A[:, j]
            m[f"shade_{k}"] = B[:, j]
        m["short_C"], m["shade_C"], m["same_path"] = A[:, 4], B[:, 4], B[:, 5] == 1.0
        return m


def length_bin(d, cfg):
    e = cfg["trips"]["length_bins_m"]
    lab = [f"<{e[1]}"] + [f"{a}-{b}" for a, b in zip(e[1:-1], e[2:])]
    return pd.cut(d, e, right=False, labels=lab)


COLS_M = ["time", "theta", "short_len", "short_sun", "short_tree", "short_bldg",
          "shade_len", "shade_sun", "shade_tree", "shade_bldg", "short_C", "shade_C", "same_path"]


def add_metrics(df):
    df["detour"] = (df.shade_len - df.short_len) / df.short_len
    df["sun_red"] = (df.short_sun - df.shade_sun) / df.short_sun.where(df.short_sun > 0)
    df["exposure_red"] = (df.short_C - df.shade_C) / df.short_C.where(df.short_C > 0)
    for p in ("short", "shade"):
        for k in ("sun", "tree", "bldg"):
            df[f"{p}_{k}_share"] = df[f"{p}_{k}"] / df[f"{p}_len"].where(df[f"{p}_len"] > 0)
    df["sun_increase"] = df.shade_sun > df.short_sun + 1e-6
    return df


def main():
    cfg = load_config()
    tr, rc = cfg["trips"], cfg["routing"]
    proc, interim = cfg["paths"]["processed"], cfg["paths"]["interim"]
    rng = np.random.default_rng(cfg["project"]["seed"])
    g, L, shade = load_city_graph(cfg)
    labels = sorted(shade)
    tp, tf, fac, fixed = theta_params(cfg), rc["tree_factor"], rc["search_radius_factor"], rc["theta_fixed_sensitivity"]
    zero = np.zeros_like(L)
    stats = {}
    t_all = time.time()

    # ---------------- station sample
    st_all = stations(cfg)
    sel = [(d["name"], d["mode"]) for d in tr["station_sample"]]
    st = pd.concat([st_all[(st_all.stop_name == n) & (st_all["mode"] == m)] for n, m in sel], ignore_index=True)
    assert len(st) == len(sel), "station sample not found in the pool"
    ent = gpd.read_parquet(cfg["paths"]["raw"] / "osm" / "osm_entrance.parquet")
    ent = ent.set_geometry(ent.representative_point())
    g_crs = cfg["project"]["crs"]
    bld = gpd.read_parquet(cfg["paths"]["raw"] / "buildings" / "buildings_city.parquet",
                           columns=["objectid", "geometry"]).to_crs(g_crs)
    bld = bld.set_geometry(bld.representative_point())
    en_node, en_d = snap(g, ent.geometry.x.to_numpy(), ent.geometry.y.to_numpy())
    bl_node, bl_d = snap(g, bld.geometry.x.to_numpy(), bld.geometry.y.to_numpy())
    strips, sinfo = [], []
    col = Collector()
    for si, s in st.iterrows():
        o, od = snap(g, np.array([s.geometry.x]), np.array([s.geometry.y]))
        o = int(o[0])
        _, d, _, _ = search(g, L, zero, zero, zero, o, "shortest", max_dist=tr["station_trip_radius_m"])
        e_ok = np.where(np.isfinite(d[en_node]) & (en_d <= 30))[0]
        n_ent = min(len(e_ok), tr["station_n_dest"])
        pick_e = rng.choice(e_ok, n_ent, replace=False) if n_ent else np.array([], int)
        b_ok = np.where(np.isfinite(d[bl_node]) & (bl_d <= 30))[0]
        n_b = min(tr["station_n_dest"] - n_ent, len(b_ok))
        pick_b = rng.choice(b_ok, n_b, replace=False) if n_b else np.array([], int)
        dests = np.r_[en_node[pick_e], bl_node[pick_b]]
        kinds = ["osm_entrance"] * n_ent + ["footprint_centroid"] * n_b
        tidx = np.arange(len(strips), len(strips) + len(dests))
        strips += [("station", f"{s.stop_name} ({s['mode']})", f"S{si}_{j}", kinds[j], "rapid", f"{s['mode']}: {s.stop_name}",
                    int(dests[j]), float(d[dests[j]])) for j in range(len(dests))]
        col.add(tidx, run_origin(g, L, shade, labels, o, dests.astype(np.int64), tr["station_trip_radius_m"] * fac,
                                 tp, tf, fixed))
        sinfo.append({"station": s.stop_name, "mode": s["mode"], "snap_m": float(od[0]), "osm_entrances_available": int(len(e_ok)),
                      "osm_entrances_used": n_ent, "footprint_centroids_used": int(n_b), "destinations": int(len(dests)),
                      "median_network_dist_m": float(np.median(d[dests])) if len(dests) else None})
    stats["stations"] = sinfo
    print(pd.DataFrame(sinfo).to_string())

    # ---------------- city sample (D-038)
    hs = tr["home_stops"]
    stops = home_stops(cfg)
    opa = gpd.read_parquet(cfg["paths"]["raw"] / "opa" / "opa_properties.parquet")
    opa = opa[opa.category_code.isin(cfg["sources"]["opa_residential_categories"]) & opa.geometry.notna() & ~opa.geometry.is_empty]
    opa = opa[opa.within(city(cfg))][["parcel_number", "geometry"]].reset_index(drop=True)
    p_node, p_d = snap(g, opa.geometry.x.to_numpy(), opa.geometry.y.to_numpy())
    s_node, s_d = snap(g, stops.geometry.x.to_numpy(), stops.geometry.y.to_numpy())
    dist, owner = nearest_source(g, L, s_node, hs["max_network_m"])
    opa["node"], opa["snap_m"] = p_node, p_d
    opa["stop_idx"], opa["stop_dist"] = owner[p_node], dist[p_node]
    cov = opa.stop_idx >= 0
    unc = opa[~cov]
    unc.drop(columns="geometry").assign(x=unc.geometry.x, y=unc.geometry.y).to_parquet(proc / "trips_city_uncovered.parquet")
    par = opa[cov].copy()
    for c in ("stop_id", "station", "mode", "kind"):
        par[c] = stops[c].to_numpy()[par.stop_idx]
    par["stop_node"] = s_node[par.stop_idx]
    n_assigned = par.groupby("station").size()
    par = par.reset_index(drop=True)                                      # all parcels within 1.2 km (D-040)
    par["length_bin"] = length_bin(par.stop_dist, cfg)
    stats["city"] = {
        "gtfs": hs["gtfs_timetable"], "rep_date": hs["rep_date"], "window": hs["window"], "min_per_hour": hs["min_per_hour"],
        "home_stops": int(len(stops)), "home_stops_by_kind": stops.kind.value_counts().to_dict(),
        "stations_by_mode": stops.groupby("mode").station.nunique().to_dict(),
        "stop_snap_m_median": float(np.median(s_d)),
        "parcels_residential": int(len(opa)), "parcel_snap_m_median": float(np.median(p_d)),
        "parcels_within_1200m": int(cov.sum()), "parcels_uncovered": int((~cov).sum()),
        "parcels_uncovered_share": float((~cov).mean()),
        "stations_with_parcels": int(len(n_assigned)),
        "parcels_per_station_quantiles": n_assigned.quantile([.1, .5, .9, 1]).to_dict(),
        "trips": int(len(par)), "trips_by_mode": par["mode"].value_counts().to_dict(),
        "trips_by_length_bin": par.length_bin.value_counts().sort_index().to_dict(),
        "trips_by_mode_and_bin": par.groupby(["mode", "length_bin"], observed=True).size().unstack().to_dict("index"),
        "stop_dist_quantiles_m": par.stop_dist.quantile([.1, .25, .5, .75, .9]).round(1).to_dict(),
        "stop_dist_quantiles_all_covered_parcels_m": opa.stop_dist[cov].quantile([.1, .25, .5, .75, .9]).round(1).to_dict(),
        "origin_stops_used": int(par.stop_node.nunique())}
    print(json.dumps(stats["city"], indent=2, default=float), flush=True)
    ctrips = [("city", r.stop_id, f"P{i}", "residential_parcel", r["mode"], r.station, int(r.node), float(r.stop_dist))
              for i, r in par.iterrows()]
    off = len(strips)
    t0 = time.time()
    for k, (sn, grp) in enumerate(par.groupby("stop_node")):
        col.add(off + grp.index.to_numpy(), run_origin(g, L, shade, labels, int(sn), grp.node.to_numpy(np.int64),
                                                       max(grp.stop_dist.max(), 1.0) * fac + 1, tp, tf, fixed))
        if k % 200 == 0:
            print(f"stop {k} / {par.stop_node.nunique()}  {time.time() - t0:.0f}s", flush=True)

    trips = pd.DataFrame(strips + ctrips, columns=["sample", "origin", "trip_id", "dest_kind", "mode", "station",
                                                    "dest_node", "network_dist_m"])
    trips["length_bin"] = length_bin(trips.network_dist_m, cfg)
    for c in ("sample", "origin", "dest_kind", "mode", "station"):
        trips[c] = trips[c].astype("category")
    df = add_metrics(col.frame(trips))
    df.to_parquet(proc / "trips_metrics.parquet")
    par.drop(columns="geometry").assign(x=par.geometry.x, y=par.geometry.y).to_parquet(proc / "trips_city_parcels.parquet")

    # ---------------- direction check: 500 city trips home -> stop (pair searches)
    chk = par[par.stop_dist >= tr["bidirectional_min_m"]].sample(tr["bidirectional_check_n"], random_state=cfg["project"]["seed"])
    rows = []
    for _, r in chk.iterrows():
        h, s_ = int(r.node), int(r.stop_node)
        _, d, pr, pe = search(g, L, zero, zero, zero, h, "shortest", max_dist=r.stop_dist * fac + 1)
        a = path_edges(pr, pe, s_)
        for lab in labels:
            ls, lt, lb = shade[lab]
            _, _, pr2, pe2 = search(g, L, ls, lt, lb, h, "shade", tp=tp, tree_factor=tf, max_dist=d[s_] * fac + 1,
                                    ellipse=(s_, fac, d[s_]))
            cp = path_edges(pr2, pe2, s_)
            rows.append((f"P{r.name}", lab, *totals(a, L, ls, lt, lb), *totals(cp, L, ls, lt, lb),
                         path_cost(a, L, ls, lt, lb, tp, tf), path_cost(cp, L, ls, lt, lb, tp, tf), False))
    rev = add_metrics(pd.DataFrame([(x[0], x[1], "dynamic", *x[2:]) for x in rows], columns=["trip_id"] + COLS_M))
    fwd = df[(df["sample"] == "city") & (df.theta == "dynamic") & df.trip_id.isin(rev.trip_id)]
    fwd = fwd.assign(time=fwd.time.astype(str))
    m = fwd.merge(rev, on=["trip_id", "time"], suffixes=("_fwd", "_rev"))
    stats["direction_check"] = {
        "trips": int(m.trip_id.nunique()), "pairs": int(len(m)),
        "sun_red_mean_stop_to_home": float(m.sun_red_fwd.mean()), "sun_red_mean_home_to_stop": float(m.sun_red_rev.mean()),
        "sun_red_mean_abs_diff": float((m.sun_red_fwd - m.sun_red_rev).abs().mean()),
        "sun_red_corr": float(m[["sun_red_fwd", "sun_red_rev"]].corr().iloc[0, 1]),
        "detour_mean_stop_to_home": float(m.detour_fwd.mean()), "detour_mean_home_to_stop": float(m.detour_rev.mean()),
        "same_shortest_length_share": float((abs(m.short_len_fwd - m.short_len_rev) < 0.01).mean()),
        "exposure_red_mean_stop_to_home": float(m.exposure_red_fwd.mean()),
        "exposure_red_mean_home_to_stop": float(m.exposure_red_rev.mean()),
        "same_shade_path_length_share": float((abs(m.shade_len_fwd - m.shade_len_rev) < 0.01).mean()),
        "trip_length_m_quantiles": m.drop_duplicates("trip_id").short_len_fwd.quantile([.1, .5, .9]).round(1).to_dict()}
    rev.to_parquet(interim / "phase06_direction_check.parquet")
    stats["wall_s"] = time.time() - t_all
    (cfg["paths"]["tables"] / "phase_06_stats.json").write_text(json.dumps(stats, indent=2, default=float))
    print(json.dumps(stats, indent=2, default=float))


if __name__ == "__main__":
    main()
