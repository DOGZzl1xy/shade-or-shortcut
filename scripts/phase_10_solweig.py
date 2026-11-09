"""Phase 10: mean radiant temperature and UTCI with SOLWEIG in University City (D-060).

    uv run python -m scripts.phase_10_solweig met          # ERA5 hourly (Open-Meteo) -> UMEP met file
    uv run python -m scripts.phase_10_solweig inputs       # phase-2 demo surfaces -> SOLWEIG rasters per sub-domain
    ../envs/solweig/bin/python scripts/solweig_run.py run    # SOLWEIG on the GPU, one run per sub-domain
    ../envs/solweig/bin/python scripts/solweig_run.py merge  # cores -> domain rasters (Tmrt, UTCI, shadow, SVF)
    uv run python -m scripts.phase_10_solweig network      # sample along the demo network; per-edge heat table
    uv run python -m scripts.phase_10_solweig figure       # check figure
    uv run python -m scripts.phase_10_solweig routes       # shortest vs shade path vs heat (UTCI) path
    uv run python -m scripts.phase_10_solweig frontier     # the same costs at equal detour (fixed θ sweep, key hour)

Domain: solweig.half_size_m around the demo centre, cut into core_m cores. Each run covers a core plus
margin_m on every side and is a single SOLWEIG tile: solweig-gpu's own tiling extends the overlap only to the
right / bottom, so obstacles left of or above a tile edge would cast no shadow into the tile. Only the cores
are kept.
Inputs (float32 on the phase-2 1 m grid): Building_DSM = DEM + building / structure height; DEM; Trees = tree
height above ground (0 elsewhere); Landcover = UMEP classes from the 2018 land cover, where canopy pixels take
the class of the nearest non-canopy pixel (the surface under the tree).
Met (UMEP format, 24 hourly rows, local time): Ta, RH, pressure, 10 m wind and global radiation from ERA5 via
Open-Meteo for the cell over University City. SOLWEIG splits global radiation into direct and diffuse itself
(onlyglobal = 1); row h holds the radiation mean over h-1..h and the sun is placed at h-0:30.
Network: outdoor demo edges (site/app/data/demo_network.json) sampled every 2 m as in phase 3; bridge decks
flagged with the phase-3 rule on OSM highway bridges (observer on the deck; SOLWEIG blanks UTCI where
DSM - DEM >= 2 m, so deck points use UTCI computed from the deck pixel's Tmrt). The project's ray caster is
rerun at SOLWEIG's sun times so that shade routing and heat routing describe the same day and instants.
edges_heat.parquet: per edge and hour L_sun / L_tree / L_bldg, mean Tmrt and UTCI, and metres in each UTCI
heat-stress category (Bröde et al. 2012: < 26 none, 26-32 moderate, 32-38 strong, 38-46 very strong, >= 46 extreme).
"""

import json
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pvlib
import rasterio
import requests
from rasterio.transform import from_origin
from scipy import ndimage
from shapely.geometry import LineString

from coolpath import ROOT, load_config
from coolpath.areas import solweig_bbox4326, solweig_box
from coolpath.network import bridge_deck
from coolpath.shadow import cast_z, local_max, sample_points
from coolpath.sun import sun_table
from scripts.phase_08_demo import demo_square

UMEP_COLS = ["iy", "id", "it", "imin", "qn", "qh", "qe", "qs", "qf", "U", "RH", "Tair", "pres", "rain", "kdown",
             "snow", "ldown", "fcld", "wuh", "xsmd", "lai", "kdiff", "kdir", "wdir"]
HOURLY = ["temperature_2m", "relative_humidity_2m", "surface_pressure", "wind_speed_10m", "wind_direction_10m",
          "shortwave_radiation", "direct_radiation", "diffuse_radiation", "precipitation", "cloud_cover"]


def folder(cfg):
    return cfg["paths"]["interim"] / "solweig"


def met(cfg):
    s, c = cfg["solweig"], cfg["demo_area"]["center"]
    raw = cfg["paths"]["raw"] / "met"
    raw.mkdir(parents=True, exist_ok=True)
    dest = raw / f"era5_openmeteo_{s['date']}.json"
    if not dest.exists():
        r = requests.get(s["met_api"], timeout=120, params={
            "latitude": c["lat"], "longitude": c["lon"], "start_date": s["date"], "end_date": s["date"],
            "hourly": ",".join(HOURLY), "wind_speed_unit": "ms", "timezone": cfg["project"]["timezone"],
            "models": "era5"})
        r.raise_for_status()
        dest.write_text(r.text)
    js = json.loads(dest.read_text())
    h = pd.DataFrame(js["hourly"])
    t = pd.to_datetime(h.time)
    assert len(h) == 24 and h[HOURLY[:6]].notna().all().all(), "incomplete ERA5 day"
    m = pd.DataFrame(-999.0, index=h.index, columns=UMEP_COLS)
    m["iy"], m["id"], m["it"], m["imin"] = t.dt.year, t.dt.day_of_year, t.dt.hour, 0
    m["U"] = h.wind_speed_10m
    m["RH"] = h.relative_humidity_2m
    m["Tair"] = h.temperature_2m
    m["pres"] = h.surface_pressure / 10          # hPa -> kPa
    m["rain"] = h.precipitation
    m["kdown"] = h.shortwave_radiation
    # wdir stays -999: no directional wind coefficients, every pixel gets the 10 m ERA5 wind
    out = folder(cfg) / f"met_{s['date']}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = ["%d"] * 4 + ["%.2f"] * (len(UMEP_COLS) - 4)
    np.savetxt(out, m.to_numpy(), fmt=fmt, delimiter=" ", header=" ".join(UMEP_COLS), comments="")
    day = h[(t.dt.hour >= min(s["hours"])) & (t.dt.hour <= max(s["hours"]))]
    res = {"date": s["date"], "source": f"{s['met_api']} (models=era5)", "grid_lat": js["latitude"],
           "grid_lon": js["longitude"], "grid_elevation_m": js["elevation"], "met_file": out.name,
           "ta_max_c": float(h.temperature_2m.max()), "ta_15h_c": float(h.temperature_2m[t.dt.hour == 15].iloc[0]),
           "rh_15h": float(h.relative_humidity_2m[t.dt.hour == 15].iloc[0]),
           "wind_15h_ms": float(h.wind_speed_10m[t.dt.hour == 15].iloc[0]),
           "kdown_max_wm2": float(h.shortwave_radiation.max()),
           "cloud_cover_mean_report_hours": float(day.cloud_cover.mean())}
    (cfg["paths"]["tables"] / "phase_10_met.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    print(h[["time", *HOURLY[:7], "cloud_cover"]].to_string())


def runs(cfg) -> dict:
    """Sub-domain cores and run windows (project CRS bounds), keyed r{row from north}_c{col from west}."""
    s = cfg["solweig"]
    x0, y0, x1, y1 = solweig_box(cfg, margin=False).bounds
    k, mg = s["core_m"], s["margin_m"]
    n = int(np.ceil((x1 - x0) / k))
    out = {}
    for i in range(n):
        for j in range(n):
            core = (x0 + j * k, y1 - (i + 1) * k, x0 + (j + 1) * k, y1 - i * k)
            out[f"r{i}_c{j}"] = {"core": core, "run": (core[0] - mg, core[1] - mg, core[2] + mg, core[3] + mg)}
    return out


def umep_landcover(lc: np.ndarray, mapping: dict) -> np.ndarray:
    """PASDA classes -> UMEP; 0 (canopy, unknown) takes the class of the nearest pixel with a class."""
    lut = np.zeros(256, np.uint8)
    for k, v in mapping.items():
        lut[int(k)] = v
    u = lut[lc]
    if (u == 0).all():
        raise ValueError("no land-cover class to fill from")
    idx = ndimage.distance_transform_edt(u == 0, return_distances=False, return_indices=True)
    return u[tuple(idx)]


def inputs(cfg):
    s = cfg["solweig"]
    src = cfg["paths"]["interim"] / "surface_demo"
    with rasterio.open(src / "dem.tif") as f:
        dem, tr, crs = f.read(1).astype(np.float32), f.transform, f.crs
    with rasterio.open(src / "surface_building.tif") as f:
        hb = f.read(1).astype(np.float32) / 10
    ht, n_tall = tree_heights(cfg)
    with rasterio.open(cfg["paths"]["raw"] / "landcover" / "landcover2018_demo_1m.tif") as f:
        assert f.transform == tr and f.shape == dem.shape
        lc = umep_landcover(f.read(1), s["landcover_umep"])
    layers = {"Building_DSM": dem + hb, "DEM": dem, "Trees": np.where(hb > 0, 0, ht),
              "Landcover": lc.astype(np.float32)}
    rr = runs(cfg)
    out = folder(cfg) / "runs"
    for key, r in rr.items():
        x0, y0, x1, y1 = r["run"]
        c0, r0 = int(round((x0 - tr.c) / tr.a)), int(round((tr.f - y1) / -tr.e))
        w, h = int(x1 - x0), int(y1 - y0)
        assert c0 >= 0 and r0 >= 0 and c0 + w <= dem.shape[1] and r0 + h <= dem.shape[0], key
        d = out / key
        d.mkdir(parents=True, exist_ok=True)
        for name, a in layers.items():
            with rasterio.open(d / f"{name}.tif", "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
                               crs=crs, transform=from_origin(x0, y1, 1.0, 1.0), compress="DEFLATE",
                               predictor=3, tiled=True) as dst:
                dst.write(a[r0:r0 + h, c0:c0 + w], 1)
    man = {"date": s["date"], "met_file": str(folder(cfg) / f"met_{s['date']}.txt"),
           "domain": solweig_box(cfg, margin=False).bounds, "crs": str(crs), "runs": rr,
           "keep_svf_intermediates": s["keep_svf_intermediates"]}
    (folder(cfg) / "runs.json").write_text(json.dumps(man, indent=2))
    lc_share = {int(k): float(v) for k, v in zip(*np.unique(lc, return_counts=True))}
    res = {"runs": len(rr), "run_px": int((s["core_m"] + 2 * s["margin_m"]) ** 2),
           "landcover_umep_share": {k: v / lc.size for k, v in lc_share.items()},
           "building_px_share": float((hb >= 2).mean()), "tree_px_share": float((layers["Trees"] > 0).mean()),
           "max_building_m": float(hb.max()), "max_tree_m": float(ht.max()), "tall_tree_px_dropped": n_tall}
    (cfg["paths"]["tables"] / "phase_10_inputs.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


UTCI_BINS = [26.0, 32.0, 38.0, 46.0]                 # Bröde et al. (2012) heat-stress categories
UTCI_CATS = ["none", "moderate", "strong", "very_strong", "extreme"]


def label(cfg, h):
    return f"{cfg['solweig']['date'][5:].replace('-', '')}_{h:02d}"


def demo_edges(cfg) -> gpd.GeoDataFrame:
    """Edges of the exported demo network (site/app/data/demo_network.json) in the project CRS, in file order."""
    d = json.loads((ROOT / "site" / "app" / "data" / "demo_network.json").read_text())
    e = d["edges"]
    geoms = [LineString(np.asarray(g).reshape(-1, 2)) for g in e["geom"]]
    g = gpd.GeoDataFrame({"kind": e["kind"], "len": e["len"], "building": e["building"], "penncard": e["penncard"]},
                         geometry=geoms, crs=4326).to_crs(cfg["project"]["crs"])
    return g


def osm_bridges(cfg) -> gpd.GeoDataFrame:
    """OSM bridge lines in the SOLWEIG domain (cached), filtered like phase 3."""
    import osmnx as ox
    root = cfg["paths"]["raw"] / "osm"
    dest = root / "osm_bridge_demo.parquet"
    if not dest.exists():
        root.mkdir(parents=True, exist_ok=True)
        ox.settings.use_cache = True
        ox.settings.cache_folder = str(root / "cache")
        g = ox.features_from_bbox(solweig_bbox4326(cfg), {"bridge": True}).reset_index()
        keep = ["element", "id", *[c for c in ("name", "highway", "bridge", "layer", "railway") if c in g.columns]]
        g[[*keep, "geometry"]].to_crs(cfg["project"]["crs"]).to_parquet(dest)
    br = gpd.read_parquet(dest)
    br = br[br.geom_type.isin(["LineString", "MultiLineString"])]
    if cfg["bridge_deck"]["osm_bridge_highway_only"]:
        br = br[br.highway.notna()]
    return br.reset_index(drop=True)


def read_surface(cfg, name):
    with rasterio.open(cfg["paths"]["interim"] / "surface_demo" / f"{name}.tif") as f:
        a = f.read(1).astype(np.float32)
        return (a / 10 if f.tags().get("units") == "decimetre" else a), f.transform


def tree_heights(cfg):
    """Phase-2 tree heights with pixels above solweig.tree_max_m set to 0 (used by SOLWEIG and the ray caster)."""
    ht, _ = read_surface(cfg, "surface_tree")
    tall = ht > cfg["solweig"]["tree_max_m"]
    ht[tall] = 0
    return ht, int(tall.sum())


def network(cfg):
    """Sample SOLWEIG outputs and the project's own shadows along the demo network; per-edge table + checks."""
    s, sc, bd = cfg["solweig"], cfg["shadow"], cfg["bridge_deck"]
    d = folder(cfg)
    edges = demo_edges(cfg)
    outdoor = edges[edges.kind != 2]                                    # indoor parts are not sampled
    px, py, k, seg = sample_points(outdoor, sc["sample_spacing_m"])
    eid = outdoor.index.to_numpy()[k]
    flags = bridge_deck(outdoor, osm_bridges(cfg), bd["buffer_m"], bd["min_length_share"], bd["max_angle_deg"])
    on_b = flags.on_bridge.to_numpy()[k]

    # project ray caster on the phase-2 demo surfaces, at SOLWEIG's sun times (h - 0:30)
    dem, tr = read_surface(cfg, "dem")
    dsm, _ = read_surface(cfg, "dsm")
    hb, _ = read_surface(cfg, "surface_building")
    ht, _ = tree_heights(cfg)
    r = ((tr.f - py) / -tr.e).astype(int)
    c = ((px - tr.c) / tr.a).astype(int)
    z0 = np.where(on_b, dsm[r, c], dem[r, c]) + sc["observer_height_m"]
    c0 = cfg["demo_area"]["center"]
    times = pd.DatetimeIndex([pd.Timestamp(f"{s['date']} {h:02d}:00", tz=cfg["project"]["timezone"]) for h in s["hours"]])
    sp = pvlib.solarposition.get_solarposition(times - pd.Timedelta(minutes=30), c0["lat"], c0["lon"])
    az, el = sp.azimuth.to_numpy(), sp.apparent_elevation.to_numpy()
    lmax = local_max(dem + np.maximum(hb, ht), tr.a, sc["localmax_block_m"], sc["max_ray_m"])
    def cast(az_, el_):
        return cast_z(px, py, z0.astype(np.float64), dem, hb, ht, lmax, tr.c, tr.f, tr.a, az_, el_,
                      sc["observer_height_m"], float(sc["max_ray_m"]), sc["ray_step_m"], sc["terrain_occlusion"])
    state = cast(az, el)
    # reproduction check: the same caster at the original study times (first date, hh:00) vs the shipped
    # demo network, whose shade lengths come from the city-wide phase 3 / phase 8 run
    sun0 = sun_table(cfg)
    sun0 = sun0[sun0.date == cfg["study"]["dates"][0]]
    state0 = cast(sun0.azimuth.to_numpy(), sun0.elevation.to_numpy())
    shipped = json.loads((ROOT / "site" / "app" / "data" / "demo_network.json").read_text())
    ti = [shipped["times"].index(l) for l in sun0.label]
    n = len(edges)
    lens = np.bincount(eid, seg, n)
    big = np.isin(np.arange(n), outdoor.index) & (lens >= 5)
    a = np.stack([np.bincount(eid, seg * (state0[:, t] == 0), n) for t in range(len(ti))], 1)[big] / lens[big, None]
    b = np.array(shipped["L"]["sun"])[ti].T[big] / np.asarray(shipped["edges"]["len"])[big, None]
    repro = {"edges": int(big.sum()), "times": list(sun0.label), "sun_share_corr": float(np.corrcoef(a.ravel(), b.ravel())[0, 1]),
             "sun_share_mean_abs_diff": float(np.abs(a - b).mean()),
             "edge_times_within_0.1": float((np.abs(a - b) <= 0.1).mean()),
             "network_sun_share_rebuilt": float(a.mean()), "network_sun_share_shipped": float(b.mean())}
    print("reproduction:", json.dumps(repro))
    del dsm, hb, ht, lmax

    # SOLWEIG at the points: bridge decks read the deck pixel (UTCI from Tmrt); other points that fall on a
    # building / structure pixel (NaN UTCI) take the nearest pixel with a value
    bands = [h + 1 for h in s["hours"]]
    with rasterio.open(d / "utci.tif") as f:
        T = f.transform
        nan0 = np.isnan(f.read(bands[0]))
    rr = ((T.f - py) / -T.e).astype(int)
    cc = ((px - T.c) / T.a).astype(int)
    dist, (ir, ic) = ndimage.distance_transform_edt(nan0, return_indices=True)
    nr, nc = np.where(on_b, rr, ir[rr, cc]), np.where(on_b, cc, ic[rr, cc])
    moved = np.where(on_b, 0.0, dist[rr, cc])
    del dist, ir, ic
    val = {}
    for name in ("tmrt", "utci", "utci_deck", "shadow"):
        with rasterio.open(d / f"{name}.tif") as f:
            val[name] = np.stack([f.read(b)[nr, nc] for b in bands], axis=1)
    with rasterio.open(d / "svf.tif") as f:
        svf = f.read(1)[nr, nc]
    utci = np.where(on_b[:, None], val["utci_deck"], val["utci"])
    tmrt, shade_sw = val["tmrt"], val["shadow"]

    labs = [label(cfg, h) for h in s["hours"]]
    pts = pd.DataFrame({"edge": eid, "seg_m": seg, "x": px, "y": py, "on_bridge": on_b, "moved_m": moved, "svf": svf})
    for t, lab in enumerate(labs):
        pts[f"state_{lab}"] = state[:, t]
        pts[f"utci_{lab}"] = utci[:, t]
        pts[f"tmrt_{lab}"] = tmrt[:, t]
        pts[f"shadow_{lab}"] = shade_sw[:, t]
    pts.to_parquet(d / "points.parquet")

    # per edge and hour: project shade lengths, mean Tmrt / UTCI, length per UTCI heat-stress category
    out = pd.DataFrame({"kind": edges.kind, "len": edges.len, "on_bridge": False})
    out.loc[outdoor.index, "on_bridge"] = flags.on_bridge.to_numpy()
    w = np.bincount(eid, seg, n)
    for t, lab in enumerate(labs):
        for j, name in enumerate(("sun", "tree", "bldg")):
            out[f"L_{name}_{lab}"] = np.bincount(eid, seg * (state[:, t] == j), n)
        ok = np.isfinite(utci[:, t])
        wt = np.bincount(eid[ok], seg[ok], n)
        out[f"utci_{lab}"] = np.bincount(eid[ok], seg[ok] * utci[ok, t], n) / np.where(wt > 0, wt, np.nan)
        out[f"tmrt_{lab}"] = np.bincount(eid[ok], seg[ok] * tmrt[ok, t], n) / np.where(wt > 0, wt, np.nan)
        cat = np.digitize(utci[ok, t], UTCI_BINS)
        for j, name in enumerate(UTCI_CATS):
            out[f"L_{name}_{lab}"] = np.bincount(eid[ok], seg[ok] * (cat == j), n)
    out["sampled_m"] = w
    out.to_parquet(d / "edges_heat.parquet")

    # checks: shadow agreement, UTCI / Tmrt by project shade class
    shaded_p = state > 0
    shaded_s = shade_sw < 0.5
    by_hour = []
    for t, lab in enumerate(labs):
        a, b = shaded_p[:, t], shaded_s[:, t]
        po = float((a == b).mean())
        pe = float(a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean()))
        row = {"label": lab, "sun_elev": float(el[t]), "sun_az": float(az[t]), "agree": po,
               "kappa": (po - pe) / (1 - pe) if pe < 1 else None,
               "project_shaded": float(a.mean()), "solweig_shaded": float(b.mean()),
               "solweig_shaded_project_sun": float((b & ~a).mean()), "project_shaded_solweig_sun": float((a & ~b).mean())}
        for j, name in enumerate(("sun", "tree", "bldg")):
            m = state[:, t] == j
            row[f"utci_{name}_mean"] = float(np.nanmean(utci[m, t])) if m.any() else None
            row[f"tmrt_{name}_mean"] = float(np.nanmean(tmrt[m, t])) if m.any() else None
            row[f"share_{name}"] = float(m.mean())
        by_hour.append(row)
    res = {"points": len(pts), "bridge_points": int(on_b.sum()), "bridge_edges": int(flags.on_bridge.sum()),
           "points_moved_off_buildings": int((moved > 0).sum()), "moved_m_p99": float(np.percentile(moved, 99)),
           "points_without_utci": int(np.isnan(utci).any(axis=1).sum()), "reproduction": repro, "by_hour": by_hour}
    (cfg["paths"]["tables"] / "phase_10_network.json").write_text(json.dumps(res, indent=2))
    print(pd.DataFrame(by_hour).round(3).to_string())
    print(json.dumps({k: v for k, v in res.items() if k != "by_hour"}, indent=2))


def relief_refs(cfg) -> pd.DataFrame:
    """Per hour: median UTCI at sunlit and at building-shaded sample points (the two ends of the relief scale)."""
    pts = pd.read_parquet(folder(cfg) / "points.parquet")
    rows = []
    for h in cfg["solweig"]["hours"]:
        lab = label(cfg, h)
        u, st = pts[f"utci_{lab}"], pts[f"state_{lab}"]
        rows.append({"label": lab, "u_sun": float(u[st == 0].median()), "u_tree": float(u[st == 1].median()),
                     "u_bldg": float(u[st == 2].median())})
    r = pd.DataFrame(rows).set_index("label")
    r["tree_relief"] = (r.u_sun - r.u_tree) / (r.u_sun - r.u_bldg)      # Wen et al. fix this at 0.5
    return r


def route_setup(cfg):
    """Outdoor demo graph, per-edge heat table in CSR order, relief references and the walk sample."""
    from coolpath.export import load_network_json, scenario_csr
    from coolpath.routing import search
    d = folder(cfg)
    e = pd.read_parquet(d / "edges_heat.parquet")
    refs = relief_refs(cfg)
    path = ROOT / "site" / "app" / "data" / "demo_network.json"
    net = load_network_json(path)
    g, sel = scenario_csr(net, "outdoor")
    L = net["len"][sel]
    ee = e.iloc[sel].reset_index(drop=True)
    nodes = json.loads(path.read_text())["nodes"]
    xy = gpd.GeoSeries.from_xy(nodes["lon"], nodes["lat"], crs=4326).to_crs(cfg["project"]["crs"])
    inside = xy.within(demo_square(cfg)).to_numpy()
    rng = np.random.default_rng(cfg["project"]["seed"])
    z = np.zeros_like(L)
    lo, hi = cfg["indoor"]["campus_trip_range_m"]
    cand = np.where(inside)[0]
    origins = rng.choice(cand, cfg["indoor"]["campus_origins"], replace=False)
    dests = {}
    for o in origins:
        _, dist, _, _ = search(g, L, z, z, z, int(o), "shortest", max_dist=hi)
        ok = cand[(dist[cand] >= lo) & (dist[cand] <= hi)]
        dests[int(o)] = rng.choice(ok, min(len(ok), cfg["indoor"]["campus_dest_per_origin"]), replace=False)
    return g, L, ee, refs, dests


def routes(cfg):
    """Shortest vs shade path (Wen et al., project shadows) vs heat path (same cost, relief from SOLWEIG UTCI).

    Heat cost per metre: 1 - θ r, r = (U_sun - U_edge) / (U_sun - U_bldg) clipped to [-1, 1.5] (U_sun, U_bldg:
    hourly medians at sunlit / building-shaded points): typical building shade costs (1 - θ) as in Wen et al.,
    typical sun 1, hotter-than-typical sun > 1. As L (1 - θ r) = L (1 - r) + (1 - θ) L r, the existing router
    runs it with l_sun = L (1 - r), l_bldg = L r. A fourth path, shade_tf1, is Wen et al. with tree shade worth as
    much as building shade (tree factor 1, what UTCI says; see relief_refs), to separate the tree factor from the
    rest of what UTCI adds. Walks: random node pairs on the outdoor demo network with both ends in the demo
    square and a shortest walk of 300-1,200 m (indoor scenarios are left for later)."""
    from coolpath.routing import path_edges, search, theta_params
    s, rc, d = cfg["solweig"], cfg["routing"], folder(cfg)
    g, L, ee, refs, dests = route_setup(cfg)
    tp, tf = theta_params(cfg), rc["tree_factor"]
    z = np.zeros_like(L)
    kinds = ["short", "shade", "shade_tf1", "heat"]
    rows = []
    for h in s["hours"]:
        lab = label(cfg, h)
        ls, lt, lb = (ee[f"L_{k}_{lab}"].to_numpy() for k in ("sun", "tree", "bldg"))
        ref = refs.loc[lab]
        u = np.nan_to_num(ee[f"utci_{lab}"].to_numpy(), nan=ref.u_sun)
        r = np.clip((ref.u_sun - u) / (ref.u_sun - ref.u_bldg), -1, 1.5)
        ext = ee[f"L_extreme_{lab}"].to_numpy()
        for o, ds in dests.items():
            res = {"short": search(g, L, z, z, z, o, "shortest"),
                   "shade": search(g, L, ls, lt, lb, o, "shade", tp=tp, tree_factor=tf),
                   "shade_tf1": search(g, L, ls, lt, lb, o, "shade", tp=tp, tree_factor=1.0),
                   "heat": search(g, L, L * (1 - r), z, L * r, o, "shade", tp=tp, tree_factor=tf)}
            for t in ds:
                rec = {"o": o, "d": int(t), "time": lab}
                paths = {k: path_edges(v[2], v[3], t) for k, v in res.items()}
                for k, p in paths.items():
                    rec[f"{k}_len"] = float(L[p].sum())
                    rec[f"{k}_sun"] = float(ls[p].sum())
                    rec[f"{k}_utci"] = float((u[p] * L[p]).sum() / max(L[p].sum(), 1e-9))
                    rec[f"{k}_extreme"] = float(ext[p].sum())
                    rec[f"{k}_edges"] = tuple(p.tolist())
                rows.append(rec)
    df = pd.DataFrame(rows)
    for k in kinds[1:]:
        df[f"{k}_changed"] = df[f"{k}_edges"] != df.short_edges
        df[f"{k}_detour"] = df[f"{k}_len"] / df.short_len - 1
        df[f"{k}_sun_red"] = 1 - df[f"{k}_sun"] / df.short_sun.where(df.short_sun > 0)
        df[f"{k}_dutci"] = df[f"{k}_utci"] - df.short_utci
        df[f"{k}_extreme_red"] = 1 - df[f"{k}_extreme"] / df.short_extreme.where(df.short_extreme > 0)
    df["heat_eq_shade"] = df.heat_edges == df.shade_edges
    df["heat_eq_shade_tf1"] = df.heat_edges == df.shade_tf1_edges
    df.drop(columns=[f"{k}_edges" for k in kinds]).to_parquet(d / "routes.parquet")
    cols = {"trips": ("o", "size"), "short_len": ("short_len", "mean"), "short_utci": ("short_utci", "mean"),
            "short_extreme_m": ("short_extreme", "mean")}
    for k in kinds[1:]:
        for m in ("changed", "detour", "sun_red", "dutci", "extreme_red"):
            cols[f"{k}_{m}"] = (f"{k}_{m}", "mean")
    cols["heat_eq_shade"] = ("heat_eq_shade", "mean")
    cols["heat_eq_shade_tf1"] = ("heat_eq_shade_tf1", "mean")
    agg = df.groupby("time").agg(**cols)
    res = {"pairs": int(sum(len(v) for v in dests.values())), "origins": len(dests), "hours": s["hours"],
           "relief_refs": refs.reset_index().to_dict("records"), "by_time": agg.reset_index().to_dict("records")}
    (cfg["paths"]["tables"] / "phase_10_routes.json").write_text(json.dumps(res, indent=2, default=float))
    print(refs.round(2).to_string())
    with pd.option_context("display.width", 250):
        for k in kinds[1:]:
            print(f"\n{k}:")
            print(agg[[c for c in agg.columns if c.startswith(k + "_") and not c.startswith("shade_tf1") or
                       (k == "shade_tf1" and c.startswith("shade_tf1"))]].round(4).to_string())
        print(agg[["trips", "short_len", "short_utci", "short_extreme_m", "heat_eq_shade", "heat_eq_shade_tf1"]].round(3).to_string())


def frontier(cfg):
    """Detour vs heat reduction at the key hour for each cost, with fixed θ swept from 0.05 to 0.6: compares the
    shade costs and the heat cost at equal detour (the dynamic-θ results mix information and aggressiveness)."""
    from coolpath.routing import path_edges, search
    g, L, ee, refs, dests = route_setup(cfg)
    lab = label(cfg, cfg["study"]["key_hour"])
    ls, lt, lb = (ee[f"L_{k}_{lab}"].to_numpy() for k in ("sun", "tree", "bldg"))
    ref = refs.loc[lab]
    u = np.nan_to_num(ee[f"utci_{lab}"].to_numpy(), nan=ref.u_sun)
    r = np.clip((ref.u_sun - u) / (ref.u_sun - ref.u_bldg), -1, 1.5)
    z = np.zeros_like(L)
    costs = {"shade": (ls, lt, lb, cfg["routing"]["tree_factor"]), "shade_tf1": (ls, lt, lb, 1.0),
             "heat": (L * (1 - r), z, L * r, 0.5)}
    short = {o: search(g, L, z, z, z, o, "shortest") for o in dests}
    base = {(o, int(t)): path_edges(short[o][2], short[o][3], t) for o, ds in dests.items() for t in ds}
    rows = []
    for name, (a, b, c, tf) in costs.items():
        for th in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
            det, du = [], []
            for o, ds in dests.items():
                res = search(g, L, a, b, c, o, "shade", theta_fixed=th, tree_factor=tf)
                for t in ds:
                    p, q = path_edges(res[2], res[3], t), base[(o, int(t))]
                    det.append(L[p].sum() / L[q].sum() - 1)
                    du.append((u[p] * L[p]).sum() / L[p].sum() - (u[q] * L[q]).sum() / L[q].sum())
            rows.append({"cost": name, "theta": th, "detour": float(np.mean(det)), "dutci": float(np.mean(du))})
    fr = pd.DataFrame(rows)
    (cfg["paths"]["tables"] / "phase_10_frontier.json").write_text(json.dumps({"time": lab, "rows": rows}, indent=2))
    print(fr.round(4).to_string())

    import matplotlib.pyplot as plt
    names = {"shade": ("shade classes, tree = ½ building (Wen et al.)", "#2a78d6"),
             "shade_tf1": ("shade classes, tree = building", "#1baf7a"),
             "heat": ("SOLWEIG UTCI", "#eb6834")}
    fig, ax = plt.subplots(figsize=(8, 5.2), facecolor="#fcfcfb")
    for k, (lab_, col) in names.items():
        f = fr[fr.cost == k]
        ax.plot(f.detour * 100, -f.dutci, color=col, lw=2, marker="o", ms=5, label=lab_)
        ax.annotate(k.replace("_tf1", " (tree = bldg)"), (f.detour.iloc[-1] * 100, -f.dutci.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", color="#52514e", fontsize=9)
    for sp_ in ("top", "right"):
        ax.spines[sp_].set_visible(False)
    ax.grid(color="#e6e5e0", lw=0.8)
    ax.set_xlim(0, fr.detour.max() * 100 * 1.25)
    ax.set(xlabel="mean detour over the shortest walk (%)", ylabel="mean UTCI along the walk: reduction (°C)")
    ax.set_title(f"Same detour, cooler walk: routing on UTCI beats shade classes\n"
                 f"{len(base):,} University City walks, {cfg['solweig']['date']} {cfg['study']['key_hour']}:00; "
                 f"points = fixed θ 0.05–0.6", loc="left", fontsize=11)
    ax.legend(frameon=False, loc="lower right")
    out = cfg["paths"]["figures"] / "phase_10_frontier.png"
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    print("figure:", out)


CLASS_COLOURS = {"sun": "#eb6834", "tree": "#1baf7a", "bldg": "#2a78d6"}     # categorical slots 2, 3, 1
ORANGES = ["#fdeee6", "#f9c9ad", "#f29a6c", "#eb6834", "#c24a1c", "#8f3210", "#5c1f08"]


def figure(cfg):
    """Check figure: UTCI map at the key hour, shadow agreement by hour, UTCI by project shade class."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    s, d = cfg["solweig"], folder(cfg)
    st = json.loads((cfg["paths"]["tables"] / "phase_10_network.json").read_text())
    pts = pd.read_parquet(d / "points.parquet")
    by = pd.DataFrame(st["by_hour"])
    hours = [int(x[-2:]) for x in by.label]
    key = label(cfg, cfg["study"]["key_hour"])
    with rasterio.open(d / "utci.tif") as f:
        u = f.read(cfg["study"]["key_hour"] + 1)
        ext = (f.bounds.left, f.bounds.right, f.bounds.bottom, f.bounds.top)
    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#9a9893",
                         "axes.labelcolor": "#52514e", "xtick.color": "#52514e", "ytick.color": "#52514e",
                         "font.size": 10})
    fig = plt.figure(figsize=(17, 9.5), facecolor="#fcfcfb")
    gs = fig.add_gridspec(2, 3, width_ratios=[1.75, 1, 1], hspace=0.38, wspace=0.3)
    ax = fig.add_subplot(gs[:, 0])
    cmap = LinearSegmentedColormap.from_list("oranges", ORANGES)
    lo, hi = np.nanpercentile(u, [1, 99])
    im = ax.imshow(u, cmap=cmap, vmin=lo, vmax=hi, extent=ext, interpolation="nearest")
    ax.set_facecolor("#d9d8d3")                                          # buildings (no UTCI)
    sq = demo_square(cfg)
    ax.plot(*sq.exterior.xy, color="#0b0b0b", lw=1.2)
    ax.set_xticks([]), ax.set_yticks([])
    for sp_ in ax.spines.values():
        sp_.set_visible(False)
    cb = fig.colorbar(im, cax=ax.inset_axes([0, -0.07, 1, 0.03]), orientation="horizontal")
    cb.set_label(f"UTCI (°C), {s['date']} {cfg['study']['key_hour']}:00; grey = buildings; box = demo square")
    ax.set_title("SOLWEIG UTCI at pedestrian height", loc="left", color="#0b0b0b")

    ax = fig.add_subplot(gs[0, 1])
    ax.plot(hours, by.agree, color="#2a78d6", lw=2, marker="o", ms=4)
    ax.set_ylim(0.7, 1)
    ax.set(xlabel="hour (sun at h − 0:30)", ylabel="share of sample points")
    ax.set_title(f"Shaded or not: SOLWEIG agrees with the project ray caster\n"
                 f"at {by.agree.min():.0%}–{by.agree.max():.0%} of points (κ {by.kappa.min():.2f}–{by.kappa.max():.2f})",
                 loc="left", color="#0b0b0b")
    ax.grid(axis="y", color="#e6e5e0", lw=0.8)

    ax = fig.add_subplot(gs[0, 2])
    for name, lab in (("sun", "sun"), ("tree", "tree shade"), ("bldg", "building shade")):
        y = by[f"utci_{name}_mean"]
        ax.plot(hours, y, color=CLASS_COLOURS[name], lw=2, label=lab)
    k = hours.index(cfg["study"]["key_hour"])
    ax.annotate("sun", (hours[k], by.utci_sun_mean.iloc[k]), xytext=(0, 6), textcoords="offset points",
                ha="center", color="#52514e", fontsize=9)
    j = hours.index(11)
    ax.annotate("tree ≈ building shade", (hours[j], min(by.utci_tree_mean.iloc[j], by.utci_bldg_mean.iloc[j])),
                xytext=(8, -22), textcoords="offset points", ha="left", color="#52514e", fontsize=9)
    ax.set(xlabel="hour", ylabel="mean UTCI at sample points (°C)")
    ax.set_title("UTCI by the project's shade class", loc="left", color="#0b0b0b")
    ax.grid(axis="y", color="#e6e5e0", lw=0.8)
    ax.legend(frameon=False, loc="lower center")

    ax = fig.add_subplot(gs[1, 1:])
    bins = np.arange(np.floor(np.nanmin(pts[f"utci_{key}"])), np.ceil(np.nanmax(pts[f"utci_{key}"])) + 0.5, 0.5)
    for j, (name, lab) in enumerate((("sun", "sun"), ("tree", "tree shade"), ("bldg", "building shade"))):
        v = pts.loc[pts[f"state_{key}"] == j, f"utci_{key}"].dropna()
        ax.hist(v, bins=bins, histtype="step", lw=2, color=CLASS_COLOURS[name], label=f"{lab} (n = {len(v):,})")
    for x in UTCI_BINS:
        if bins[0] < x < bins[-1]:
            ax.axvline(x, color="#9a9893", lw=0.8, ls=":")
            ax.annotate(f"{x:.0f} °C", (x, 1), xycoords=("data", "axes fraction"), xytext=(3, -12),
                        textcoords="offset points", color="#52514e", fontsize=8)
    ax.set(xlabel=f"UTCI at {cfg['study']['key_hour']}:00 (°C); dotted = heat-stress category limits",
           ylabel="sample points (2 m each)")
    ax.set_title("Within one shade class UTCI still varies by several degrees", loc="left", color="#0b0b0b")
    ax.legend(frameon=False)
    ax.grid(axis="y", color="#e6e5e0", lw=0.8)
    out = cfg["paths"]["figures"] / "phase_10_solweig_check.png"
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    print("figure:", out)


def main(args):
    cfg = load_config()
    steps = {"met": met, "inputs": inputs, "network": network, "figure": figure, "routes": routes,
             "frontier": frontier}
    for a in args or ["met", "inputs"]:
        steps[a](cfg)


if __name__ == "__main__":
    main(sys.argv[1:])
