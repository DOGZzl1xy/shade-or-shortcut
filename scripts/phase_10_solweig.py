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
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import pvlib
import rasterio
import requests
import shapely
from rasterio.transform import from_origin
from scipy import ndimage
from shapely.geometry import LineString

from coolpath import ROOT, load_config
from coolpath.areas import city, solweig_bbox4326, solweig_box
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
    """ERA5 met files for every date in solweig.dates: the full day (demo run) and the met_hours rows (city run)."""
    res = {d: met_day(cfg, d) for d in cfg["solweig"]["dates"]}
    (cfg["paths"]["tables"] / "phase_10_met.json").write_text(json.dumps(res, indent=2))


def met_day(cfg, date):
    s, c = cfg["solweig"], cfg["demo_area"]["center"]
    raw = cfg["paths"]["raw"] / "met"
    raw.mkdir(parents=True, exist_ok=True)
    dest = raw / f"era5_openmeteo_{date}.json"
    if not dest.exists():
        r = requests.get(s["met_api"], timeout=120, params={
            "latitude": c["lat"], "longitude": c["lon"], "start_date": date, "end_date": date,
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
    out = folder(cfg) / f"met_{date}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = ["%d"] * 4 + ["%.2f"] * (len(UMEP_COLS) - 4)
    np.savetxt(out, m.to_numpy(), fmt=fmt, delimiter=" ", header=" ".join(UMEP_COLS), comments="")
    h0, h1 = s["met_hours"]                         # the city run's file: rows h0..h1 only
    sub = m[(m.it >= h0) & (m.it <= h1)]
    np.savetxt(met_file(cfg, date), sub.to_numpy(), fmt=fmt, delimiter=" ", header=" ".join(UMEP_COLS), comments="")
    day = h[(t.dt.hour >= min(s["hours"])) & (t.dt.hour <= max(s["hours"]))]
    res = {"date": date, "source": f"{s['met_api']} (models=era5)", "grid_lat": js["latitude"],
           "grid_lon": js["longitude"], "grid_elevation_m": js["elevation"], "met_file": out.name,
           "ta_max_c": float(h.temperature_2m.max()), "ta_15h_c": float(h.temperature_2m[t.dt.hour == 15].iloc[0]),
           "rh_15h": float(h.relative_humidity_2m[t.dt.hour == 15].iloc[0]),
           "wind_15h_ms": float(h.wind_speed_10m[t.dt.hour == 15].iloc[0]),
           "kdown_max_wm2": float(h.shortwave_radiation.max()),
           "cloud_cover_mean_report_hours": float(day.cloud_cover.mean())}
    print(json.dumps(res, indent=2))
    print(h[["time", *HOURLY[:7], "cloud_cover"]].to_string())
    return res


def met_file(cfg, date=None):
    h0, h1 = cfg["solweig"]["met_hours"]
    return folder(cfg) / f"met_{date or cfg['solweig']['date']}_h{h0:02d}-{h1:02d}.txt"


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


def label(cfg, h, date=None):
    """Time label MMDD_HH (the demo run's date unless `date` is given)."""
    return f"{(date or cfg['solweig']['date'])[5:].replace('-', '')}_{h:02d}"


def sun_at(cfg, date):
    """Azimuth / elevation at SOLWEIG's sun times (h - 0:30) of the report hours on `date`, at the demo centre."""
    s, c0 = cfg["solweig"], cfg["demo_area"]["center"]
    times = pd.DatetimeIndex([pd.Timestamp(f"{date} {h:02d}:00", tz=cfg["project"]["timezone"]) for h in s["hours"]])
    sp = pvlib.solarposition.get_solarposition(times - pd.Timedelta(minutes=30), c0["lat"], c0["lon"])
    return sp.azimuth.to_numpy(), sp.apparent_elevation.to_numpy()


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


def demo_points(cfg):
    """2 m sample points of the demo network's outdoor edges (indoor parts are not sampled) with bridge-deck flags
    (phase-3 rule on OSM highway bridges). Returns edges, outdoor edges, their flags, x, y, edge index, seg, on_bridge."""
    sc, bd = cfg["shadow"], cfg["bridge_deck"]
    edges = demo_edges(cfg)
    outdoor = edges[edges.kind != 2]
    px, py, k, seg = sample_points(outdoor, sc["sample_spacing_m"])
    flags = bridge_deck(outdoor, osm_bridges(cfg), bd["buffer_m"], bd["min_length_share"], bd["max_angle_deg"])
    return edges, outdoor, flags, px, py, outdoor.index.to_numpy()[k], seg, flags.on_bridge.to_numpy()[k]


def network(cfg):
    """Sample SOLWEIG outputs and the project's own shadows along the demo network; per-edge table + checks."""
    s, sc = cfg["solweig"], cfg["shadow"]
    d = folder(cfg)
    edges, outdoor, flags, px, py, eid, seg, on_b = demo_points(cfg)

    # project ray caster on the phase-2 demo surfaces, at SOLWEIG's sun times (h - 0:30)
    dem, tr = read_surface(cfg, "dem")
    dsm, _ = read_surface(cfg, "dsm")
    hb, _ = read_surface(cfg, "surface_building")
    ht, _ = tree_heights(cfg)
    r = ((tr.f - py) / -tr.e).astype(int)
    c = ((px - tr.c) / tr.a).astype(int)
    z0 = np.where(on_b, dsm[r, c], dem[r, c]) + sc["observer_height_m"]
    az, el = sun_at(cfg, s["date"])
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


# ---------------------------------------------------------------- city-wide run (D-061)
def city_folder(cfg):
    return cfg["paths"]["interim"] / "solweig_city"


def city_runs(cfg) -> dict:
    """core_m cores tiling the city bounding box from its south-west corner (cores that miss the city are skipped);
    run window = core + margin_m on every side. Keys x{column from west}_y{row from south}."""
    c = cfg["solweig"]["city"]
    k, mg = c["core_m"], c["margin_m"]
    poly = city(cfg)
    x0, y0, x1, y1 = poly.bounds
    x0, y0 = float(np.floor(x0)), float(np.floor(y0))
    out = {}
    for i in range(int(np.ceil((x1 - x0) / k))):
        for j in range(int(np.ceil((y1 - y0) / k))):
            core = (x0 + i * k, y0 + j * k, x0 + (i + 1) * k, y0 + (j + 1) * k)
            if shapely.box(*core).intersects(poly):
                out[f"x{i:02d}_y{j:02d}"] = {"core": core, "run": (core[0] - mg, core[1] - mg, core[2] + mg, core[3] + mg)}
    return out


def city_points(cfg):
    """Sample points of the phase-4 graph: the phase-3 points plus the phase-4 additions (OSM covered ways,
    connectors) resampled as phase 4 does (same edges in the same order, 2 m, ground observers), with the
    SOLWEIG core each point falls in. Written to solweig_city/points_city.parquet."""
    proc, sc = cfg["paths"]["processed"], cfg["shadow"]
    p3 = pd.read_parquet(proc / "shadow_points_xyz.parquet", columns=["edge_id", "seg_m", "x", "y", "z0", "on_bridge"])
    ge = gpd.read_parquet(proc / "graph_edges.parquet", columns=["edge_id", "geometry"])
    new = ge[~ge.edge_id.isin(np.unique(p3.edge_id))]
    px, py, k, seg = sample_points(new, sc["sample_spacing_m"])
    with rasterio.open(proc / "dem.tif") as f:
        z0 = np.array([v[0] for v in f.sample(zip(px, py))], np.float64) + sc["observer_height_m"]
    p4 = pd.DataFrame({"edge_id": new.edge_id.to_numpy()[k], "seg_m": seg, "x": px, "y": py, "z0": z0, "on_bridge": False})
    pts = pd.concat([p3, p4], ignore_index=True)
    rr = city_runs(cfg)
    k_ = cfg["solweig"]["city"]["core_m"]
    x0, y0 = min(r["core"][0] for r in rr.values()), min(r["core"][1] for r in rr.values())
    i = np.floor((pts.x - x0) / k_).astype(int)
    j = np.floor((pts.y - y0) / k_).astype(int)
    pts["key"] = [f"x{a:02d}_y{b:02d}" for a, b in zip(i, j)]
    missing = set(pts.key) - set(rr)
    assert not missing, f"points outside every core: {sorted(missing)[:5]}"
    pts.index.name = "pid"
    city_folder(cfg).mkdir(parents=True, exist_ok=True)
    pts.reset_index().to_parquet(city_folder(cfg) / "points_city.parquet")
    res = {"points": len(pts), "phase3_points": len(p3), "phase4_new_points": len(p4), "bridge_points": int(pts.on_bridge.sum()),
           "cores": len(rr), "cores_with_points": int(pts.key.nunique()),
           "points_per_core_quantiles": pts.key.value_counts().quantile([0, .1, .5, .9, 1]).to_dict()}
    print(json.dumps(res, indent=2))
    return res


def city_package(cfg):
    """One tar for the cloud-drive upload: surfaces (DEM as LERC with 1 mm max error, building / tree heights,
    land cover), the points (pid, x, y, on_bridge, key), the 7-19 h met file, runs_city.json (cores that hold sample
    points only) and the runner. Unpack and run on the server: python solweig_run.py city solweig_city ..."""
    import shutil
    import tarfile
    s = cfg["solweig"]
    d = city_folder(cfg)
    pk = d / "package"
    (pk / "rasters").mkdir(parents=True, exist_ok=True)
    proc = cfg["paths"]["processed"]
    with rasterio.open(proc / "dem.tif") as f:
        prof = f.profile | {"driver": "GTiff", "compress": "LERC_ZSTD", "max_z_error": 0.001, "tiled": True,
                            "blockxsize": 512, "blockysize": 512, "BIGTIFF": "YES"}
        prof.pop("predictor", None)
        with rasterio.open(pk / "rasters" / "dem.tif", "w", **prof) as dst:
            for _, w in f.block_windows(1):
                dst.write(f.read(1, window=w), 1, window=w)
    for src in (proc / "surface_building.tif", proc / "surface_tree.tif",
                cfg["paths"]["raw"] / "landcover" / "landcover2018_city_1m.tif"):
        shutil.copy(src, pk / "rasters" / src.name.replace("landcover2018_city_1m", "landcover"))
    pts = pd.read_parquet(d / "points_city.parquet", columns=["pid", "x", "y", "on_bridge", "key"])
    pts.to_parquet(pk / "points.parquet", compression="zstd")
    shutil.copy(met_file(cfg), pk / "met.txt")
    shutil.copy(ROOT / "scripts" / "solweig_run.py", pk / "solweig_run.py")
    runs = {k: v for k, v in city_runs(cfg).items() if k in set(pts.key)}      # cores without sample points are skipped
    man = {"date": s["date"], "met_file": "met.txt", "crs": cfg["project"]["crs"], "runs": runs,
           "tree_max_m": s["tree_max_m"], "landcover_umep": s["landcover_umep"], "utci_scale": s["city"]["utci_scale"],
           "met_hours": s["met_hours"], "rasters": {"dem": "dem.tif", "building": "surface_building.tif",
                                                    "tree": "surface_tree.tif", "landcover": "landcover.tif"}}
    (pk / "runs_city.json").write_text(json.dumps(man, indent=2))
    tar = d / "solweig_city_package.tar"
    with tarfile.open(tar, "w") as t:
        t.add(pk, arcname="solweig_city")
    sizes = {p.name: round(p.stat().st_size / 1e6, 1) for p in sorted(pk.rglob("*")) if p.is_file()}
    print(json.dumps(sizes, indent=2), f"\ntar: {tar} {tar.stat().st_size / 1e9:.2f} GB, {len(man['runs'])} runs")


def city_labels(cfg, dates=None):
    """(date, hour, label) for every city date (or the given dates) and report hour."""
    return [(d, h, label(cfg, h, d)) for d in (dates or cfg["solweig"]["dates"]) for h in cfg["solweig"]["hours"]]


def city_dates(cfg):
    """City dates whose server output is unpacked in solweig_city/results/<date>/out."""
    r = city_folder(cfg) / "results"
    ds = [d for d in cfg["solweig"]["dates"] if (r / d / "out").is_dir()]
    if len(ds) < len(cfg["solweig"]["dates"]):
        print("server output not (yet) unpacked for:", sorted(set(cfg["solweig"]["dates"]) - set(ds)))
    assert ds, "no server output under solweig_city/results/<date>/out"
    return ds


def city_shade(cfg):
    """The project's ray caster on every city sample point at SOLWEIG's sun times (h - 0:30 on each city date,
    report hours), with the same tree cap as the SOLWEIG inputs: the shade path of the city comparison."""
    from scripts.phase_03_shadow import cast_points
    s = cfg["solweig"]
    pts = pd.read_parquet(city_folder(cfg) / "points_city.parquet", columns=["pid", "x", "y", "z0"])
    sun = [sun_at(cfg, d) for d in s["dates"]]
    az, el = np.concatenate([a for a, _ in sun]), np.concatenate([e for _, e in sun])
    st, stats = cast_points(cfg, pts.x.to_numpy(), pts.y.to_numpy(), pts.z0.to_numpy(), az, el, tree_max_m=s["tree_max_m"])
    out = pd.DataFrame({"pid": pts.pid})
    labs = [lab for _, _, lab in city_labels(cfg)]
    for t, lab in enumerate(labs):
        out[f"state_{lab}"] = st[:, t]
    out.to_parquet(city_folder(cfg) / "shade_states.parquet")
    print(json.dumps({k: v for k, v in stats.items()}, default=float),
          {lab: round(float((st[:, t] == 0).mean()), 4) for t, lab in enumerate(labs)})


def city_bundle(cfg):
    """Small per-date bundles for the server for every city date after the first (the surfaces are already there):
    met file, runs_city.json for that date, and the demo network's outdoor sample points (pids after the city
    points, same core grid) so the route tool gets values too. Written to solweig_city/bundle_<date>/."""
    s = cfg["solweig"]
    d = city_folder(cfg)
    base = json.loads((d / "package" / "runs_city.json").read_text())
    n_city = len(pd.read_parquet(d / "points_city.parquet", columns=["pid"]))
    _, _, _, px, py, eid, seg, on_b = demo_points(cfg)
    rr, k_ = city_runs(cfg), s["city"]["core_m"]
    x0, y0 = min(r["core"][0] for r in rr.values()), min(r["core"][1] for r in rr.values())
    keys = [f"x{a:02d}_y{b:02d}" for a, b in zip(np.floor((px - x0) / k_).astype(int), np.floor((py - y0) / k_).astype(int))]
    assert set(keys) <= set(base["runs"]), "demo points outside the run cores"
    demo = pd.DataFrame({"pid": n_city + np.arange(len(px)), "x": px, "y": py, "on_bridge": on_b, "key": keys})
    demo.assign(edge=eid, seg_m=seg).to_parquet(d / "demo_points.parquet")
    for date in s["dates"][1:]:
        b = d / f"bundle_{date}"
        b.mkdir(parents=True, exist_ok=True)
        demo.to_parquet(b / "demo_points.parquet", compression="zstd")
        import shutil
        shutil.copy(met_file(cfg, date), b / "met.txt")
        (b / "runs_city.json").write_text(json.dumps(base | {"date": date}, indent=2))
        print(date, {p.name: p.stat().st_size for p in b.iterdir()}, f"{len(demo)} demo points")


def city_collect(cfg):
    """Server output (one tar per date, unpacked into solweig_city/results/<date>/out/) -> per-edge heat table.

    Per edge and report hour, as for the demo: L_sun / L_tree / L_bldg from city_shade (covered edges: building
    shade), mean Tmrt and UTCI, metres per UTCI heat-stress category. Covered walkways are under a roof SOLWEIG does
    not see, so they take the hour's median UTCI / Tmrt of building-shaded points. Checks go to phase_10_city.json."""
    s = cfg["solweig"]
    d = city_folder(cfg)
    pts = pd.read_parquet(d / "points_city.parquet", columns=["pid", "edge_id", "seg_m", "on_bridge", "key"])
    pts = pts.merge(pd.read_parquet(d / "shade_states.parquet"), on="pid")
    files = {}
    dates = city_dates(cfg)
    for date in dates:
        files[date] = sorted((d / "results" / date / "out").glob("points_*.parquet"))
        sol = pd.concat([pd.read_parquet(f) for f in files[date]], ignore_index=True)
        ren = {f"{v}_{h:02d}": f"{v}_{label(cfg, h, date)}" for h in s["hours"] for v in ("utci", "tmrt", "shadow")}
        sol = sol.rename(columns=ren)[["pid", *ren.values()] + (["moved_m", "svf"] if date == dates[0] else [])]
        pts = pts.merge(sol, on="pid", how="left")                  # demo-only pids (pid >= n_city) drop out here
    labs = {lab: lab for _, _, lab in city_labels(cfg, dates)}
    ge = pd.read_parquet(cfg["paths"]["processed"] / "graph_edges.parquet", columns=["edge_id", "length", "covered"])
    row = pd.Series(np.arange(len(ge)), index=ge.edge_id).reindex(pts.edge_id).to_numpy()
    n, seg, cov = len(ge), pts.seg_m.to_numpy(np.float64), ge.covered.to_numpy()
    out = pd.DataFrame({"edge_id": ge.edge_id, "length": ge.length, "covered": cov})
    by_hour = []
    for lab in labs.values():
        st = pts[f"state_{lab}"].to_numpy()
        u, tm = pts[f"utci_{lab}"].to_numpy(np.float64), pts[f"tmrt_{lab}"].to_numpy(np.float64)
        u_b, tm_b = float(np.nanmedian(u[st == 2])), float(np.nanmedian(tm[st == 2]))
        for j, name in enumerate(("sun", "tree", "bldg")):
            Lk = np.bincount(row, seg * (st == j), n)
            out[f"L_{name}_{lab}"] = np.where(cov, out.length if name == "bldg" else 0.0, Lk).astype(np.float32)
        ok = np.isfinite(u)
        wt = np.bincount(row[ok], seg[ok], n)
        mu = np.bincount(row[ok], seg[ok] * u[ok], n) / np.where(wt > 0, wt, np.nan)
        mt = np.bincount(row[ok], seg[ok] * tm[ok], n) / np.where(wt > 0, wt, np.nan)
        out[f"utci_{lab}"] = np.where(cov, u_b, mu).astype(np.float32)
        out[f"tmrt_{lab}"] = np.where(cov, tm_b, mt).astype(np.float32)
        cat = np.digitize(u[ok], UTCI_BINS)
        cat_cov = np.digitize([u_b], UTCI_BINS)[0]
        for j, name in enumerate(UTCI_CATS):
            Lk = np.bincount(row[ok], seg[ok] * (cat == j), n)
            out[f"L_{name}_{lab}"] = np.where(cov, out.length * (cat_cov == j), Lk).astype(np.float32)
        a, b = st > 0, pts[f"shadow_{lab}"].to_numpy() < 0.5
        po = float((a == b).mean())
        pe = float(a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean()))
        by_hour.append({"label": lab, "agree": po, "kappa": (po - pe) / (1 - pe), "project_shaded": float(a.mean()),
                        "solweig_shaded": float(b.mean()), "u_sun": float(np.nanmedian(u[st == 0])),
                        "u_tree": float(np.nanmedian(u[st == 1])), "u_bldg": u_b,
                        "utci_mean_sun": float(np.nanmean(u[st == 0])), "utci_mean_tree": float(np.nanmean(u[st == 1])),
                        "utci_mean_bldg": float(np.nanmean(u[st == 2]))})
    out.to_parquet(d / "edges_heat_city.parquet")
    bh = pd.DataFrame(by_hour)
    bh["tree_relief"] = (bh.u_sun - bh.u_tree) / (bh.u_sun - bh.u_bldg)
    res = {"server_files": {k: len(v) for k, v in files.items()}, "points": len(pts),
           "points_with_utci": {lab: int(pts[f"utci_{lab}"].notna().sum()) for lab in labs if lab.endswith(f"_{s['hours'][0]:02d}")},
           "bridge_points": int(pts.on_bridge.sum()), "points_moved_off_buildings": int((pts.moved_m > 0).sum()),
           "moved_m_p99": float(pts.moved_m.quantile(0.99)), "covered_edges": int(cov.sum()),
           "edges_without_utci": {lab: int(out[f"utci_{lab}"].isna().sum()) for lab in labs
                                  if lab.endswith(f"_{cfg['study']['key_hour']:02d}")},
           "by_hour": bh.to_dict("records")}
    (cfg["paths"]["tables"] / "phase_10_city.json").write_text(json.dumps(res, indent=2, default=float))
    print(bh.round(3).to_string())
    print(json.dumps({k: v for k, v in res.items() if k != "by_hour"}, indent=2, default=float))


def city_refs(cfg):
    """Per label (MMDD_HH): the medians of UTCI at sunlit and at building-shaded sample points (classes from the
    project's ray caster), written by city-collect; the two ends of the relief scale of the heat cost."""
    bh = json.loads((cfg["paths"]["tables"] / "phase_10_city.json").read_text())["by_hour"]
    return {r["label"]: (r["u_sun"], r["u_bldg"]) for r in bh}


def city_routes(cfg):
    """City sample (phase 6 walks home from transit), every report hour: shortest vs shade path (Wen et al. on the
    project's shadows at SOLWEIG's sun times) vs heat path (UTCI relief cost, as `routes`). One-to-many searches per
    stop with phase 6's radius; path sums along the search trees (tree_totals). Writes routes_city.parquet and the
    summary by mode x length band in phase_10_city_routes.json."""
    from coolpath.routing import load_city_graph, search, theta_params, tree_totals
    from scripts.phase_06_trips import length_bin
    s, rc = cfg["solweig"], cfg["routing"]
    d = city_folder(cfg)
    g, L, _ = load_city_graph(cfg)
    e = pd.read_parquet(d / "edges_heat_city.parquet").set_index("edge_id").loc[g.edge_ids].reset_index()
    par = pd.read_parquet(cfg["paths"]["processed"] / "trips_city_parcels.parquet",
                          columns=["node", "stop_node", "stop_dist", "mode", "station"]).reset_index(drop=True)
    tp, tf, fac = theta_params(cfg), rc["tree_factor"], rc["search_radius_factor"]
    z = np.zeros_like(L)
    cols = {k: [] for k in ("trip", "time")}
    vals = []
    t0 = time.time()
    dates = [dd for dd in s["dates"] if f"utci_{label(cfg, s['hours'][0], dd)}" in e.columns]    # collected dates
    labs = [lab for _, _, lab in city_labels(cfg, dates)]
    refs = city_refs(cfg)
    for lab in labs:
        ls, lt, lb = (e[f"L_{k}_{lab}"].to_numpy(np.float64) for k in ("sun", "tree", "bldg"))
        u = e[f"utci_{lab}"].to_numpy(np.float64)
        u_sun, u_bldg = refs[lab]
        u = np.nan_to_num(u, nan=u_sun)
        r = np.clip((u_sun - u) / (u_sun - u_bldg), -1, 1.5)
        uL = u * L
        ext = e[f"L_extreme_{lab}"].to_numpy(np.float64)
        for sn, grp in par.groupby("stop_node"):
            o, dests = int(sn), grp.node.to_numpy(np.int64)
            rad = max(grp.stop_dist.max(), 1.0) * fac + 1
            _, _, p0, e0 = search(g, L, z, z, z, o, "shortest", max_dist=rad)
            _, _, p1, e1 = search(g, L, ls, lt, lb, o, "shade", tp=tp, tree_factor=tf, max_dist=rad)
            _, _, p2, e2 = search(g, L, L * (1 - r), z, L * r, o, "shade", tp=tp, tree_factor=tf, max_dist=rad)
            A = tree_totals(p0, e0, o, dests, L, ls, uL, ext, tp, tf, -1.0, e0)
            B = tree_totals(p1, e1, o, dests, L, ls, uL, ext, tp, tf, -1.0, e0)
            C = tree_totals(p2, e2, o, dests, L, ls, uL, ext, tp, tf, -1.0, e1)
            cols["trip"].append(grp.index.to_numpy())
            cols["time"] += [lab] * len(dests)
            vals.append(np.c_[A[:, :4], B[:, :4], B[:, 5], C[:, :4], C[:, 5]].astype(np.float32))
        print(lab, f"{time.time() - t0:.0f} s", flush=True)
    V = np.concatenate(vals)
    names = [f"{p}_{k}" for p in ("short",) for k in ("len", "sun", "utcixL", "extreme")] + \
            [f"shade_{k}" for k in ("len", "sun", "utcixL", "extreme")] + ["shade_same_as_short"] + \
            [f"heat_{k}" for k in ("len", "sun", "utcixL", "extreme")] + ["heat_same_as_shade"]
    df = pd.DataFrame(V, columns=names)
    df["trip"] = np.concatenate(cols["trip"])
    df["time"] = pd.Categorical(cols["time"])
    df = df.join(par[["mode", "stop_dist"]], on="trip")
    df["length_bin"] = length_bin(df.stop_dist, cfg)
    for p in ("short", "shade", "heat"):
        df[f"{p}_utci"] = df[f"{p}_utcixL"] / df[f"{p}_len"]
    for p in ("shade", "heat"):
        df[f"{p}_detour"] = df[f"{p}_len"] / df.short_len - 1
        df[f"{p}_sun_red"] = 1 - df[f"{p}_sun"] / df.short_sun.where(df.short_sun > 0)
        df[f"{p}_dutci"] = df[f"{p}_utci"] - df.short_utci
        df[f"{p}_extreme_red"] = 1 - df[f"{p}_extreme"] / df.short_extreme.where(df.short_extreme > 0)
    df = df[np.isfinite(df.short_len) & np.isfinite(df.heat_len) & np.isfinite(df.shade_len)]
    df.to_parquet(d / "routes_city.parquet")
    agg = {"trips": ("trip", "size"), "short_utci": ("short_utci", "mean"), "short_extreme_m": ("short_extreme", "mean")}
    for p in ("shade", "heat"):
        for m in ("detour", "sun_red", "dutci", "extreme_red"):
            agg[f"{p}_{m}"] = (f"{p}_{m}", "mean")
    agg["shade_changed"] = ("shade_same_as_short", lambda x: 1 - x.mean())
    agg["heat_eq_shade"] = ("heat_same_as_shade", "mean")
    keys = [label(cfg, cfg["study"]["key_hour"], dd) for dd in dates]          # key hour pooled over dates (phase 7)
    t = df.groupby("time", observed=True).agg(**agg)
    mb = df[df.time.isin(keys)].groupby(["mode", "length_bin"], observed=True).agg(**agg)
    res = {"dates": dates, "trips": int(df.trip.nunique()), "rows": len(df), "seconds": time.time() - t0,
           "by_time": t.reset_index().to_dict("records"), "by_mode_bin_key_hour": mb.reset_index().to_dict("records")}
    (cfg["paths"]["tables"] / "phase_10_city_routes.json").write_text(json.dumps(res, indent=2, default=float))
    with pd.option_context("display.width", 250):
        print(t.round(4).to_string())
        print(mb.round(4).to_string())


def city_sensitivity(cfg):
    """Overnight server jobs (results/extra/<job>/out) against the main run of the same date, point by point.

    tau05 / tau15: vegetation transmissivity 5 % / 15 % (SOLWEIG's fixed 3 %; Konarska et al. 2014 measured
    1.3-5.3 % through foliated crowns) on 40 cores stratified by canopy cover: hourly medians of UTCI at sunlit,
    tree- and building-shaded points (classes from the main run's SOLWEIG shadow) and the tree relief
    (U_sun - U_tree) / (U_sun - U_bldg). margin800: the 8 cores with most > 150 m buildings 400-800 m away, run with an
    800 m margin: UTCI differences and shade-class changes against the 400 m margin. Table + figure."""
    import matplotlib.pyplot as plt
    s = cfg["solweig"]
    r = city_folder(cfg) / "results"
    main1 = r / s["dates"][0] / "out"
    hours = s["hours"]

    def paired(job, cols):
        files = sorted((r / "extra" / job / "out").glob("points_*.parquet"))
        parts = []
        for f in files:
            a = pd.read_parquet(main1 / f.name, columns=["pid", *cols])
            b = pd.read_parquet(f, columns=["pid", *cols])
            parts.append(a.merge(b, on="pid", suffixes=("_main", "_job")))
        return pd.concat(parts, ignore_index=True), len(files)

    res, rows = {"hours": hours}, []
    cols = [f"{v}_{h:02d}" for h in hours for v in ("utci", "shadow")]
    for job, tau in (("tau05", 0.05), ("tau15", 0.15)):
        m, n = paired(job, cols)
        res[job] = {"cores": n, "points": len(m), "transmissivity": tau}
        for h in hours:
            sh = m[f"shadow_{h:02d}_main"]
            cls = {"sun": sh > 0.99, "tree": (sh > 0.02) & (sh < 0.05), "bldg": sh < 0.01}
            for which, tag in (("main", 0.03), ("job", tau)):
                u = {k: float(m.loc[v, f"utci_{h:02d}_{which}"].median()) for k, v in cls.items()}
                rows.append({"job": job, "hour": h, "transmissivity": tag, **{f"u_{k}": x for k, x in u.items()},
                             "tree_relief": (u["sun"] - u["tree"]) / (u["sun"] - u["bldg"]),
                             "n_tree": int(cls["tree"].sum())})
    tab = pd.DataFrame(rows).drop_duplicates(["hour", "transmissivity"])
    res["transmissivity_by_hour"] = tab.drop(columns="job").to_dict("records")
    m, n = paired("margin800", cols)
    mg = []
    for h in hours:
        dd = m[f"utci_{h:02d}_job"] - m[f"utci_{h:02d}_main"]
        flip = (m[f"shadow_{h:02d}_job"] < 0.5) != (m[f"shadow_{h:02d}_main"] < 0.5)
        mg.append({"hour": h, "mean_diff": float(dd.mean()), "share_abs_gt_0.5": float((dd.abs() > 0.5).mean()),
                   "share_abs_gt_0.1": float((dd.abs() > 0.1).mean()), "max_abs": float(dd.abs().max()),
                   "shade_class_changed": float(flip.mean())})
    res["margin800"] = {"cores": n, "points": len(m), "by_hour": mg}
    (cfg["paths"]["tables"] / "phase_10_sensitivity.json").write_text(json.dumps(res, indent=2))
    with pd.option_context("display.width", 200):
        print(tab.pivot(index="hour", columns="transmissivity", values="tree_relief").round(3).to_string())
        print(pd.DataFrame(mg).round(4).to_string(index=False))

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6), facecolor="#fcfcfb")
    for tau, col in ((0.03, "#2a78d6"), (0.05, "#1baf7a"), (0.15, "#eb6834")):
        tt = tab[tab.transmissivity == tau]
        ax[0].plot(tt.hour, tt.tree_relief, color=col, lw=2, marker="o", ms=4, label=f"{tau:.0%}" + (" (SOLWEIG)" if tau == 0.03 else ""))
    ax[0].axhline(0.5, color="#9a9893", lw=1, ls="--")
    ax[0].annotate("Wen et al. (tree = ½ building)", (hours[0], 0.5), xytext=(0, 5), textcoords="offset points", fontsize=9, color="#52514e")
    ax[0].axhline(1, color="#9a9893", lw=0.8, ls=":")
    ax[0].set(xlabel="hour (sun at h − 0:30)", ylabel="tree relief  (U_sun − U_tree) / (U_sun − U_bldg)", ylim=(0.3, 1.3))
    ax[0].set_title(f"Tree shade stays as cool as building shade\nvegetation transmissivity, {res['tau05']['cores']} cores, "
                    f"{s['dates'][0]}", loc="left", fontsize=11)
    ax[0].legend(frameon=False, title="transmissivity", loc="lower right")
    g = pd.DataFrame(mg)
    ax[1].bar(g.hour - 0.2, 100 * g["share_abs_gt_0.1"], width=0.4, color="#2a78d6", label="|ΔUTCI| > 0.1 °C")
    ax[1].bar(g.hour + 0.2, 100 * g["share_abs_gt_0.5"], width=0.4, color="#eb6834", label="|ΔUTCI| > 0.5 °C")
    ax[1].set(xlabel="hour (sun at h − 0:30)", ylabel="share of sample points (%)")
    ax[1].set_title(f"An 800 m margin changes almost nothing\n{res['margin800']['points']:,} points in the "
                    f"{n} cores nearest the towers", loc="left", fontsize=11)
    ax[1].legend(frameon=False)
    for a in ax:
        for sp_ in ("top", "right"):
            a.spines[sp_].set_visible(False)
        a.grid(axis="y", color="#e6e5e0", lw=0.8)
    out = cfg["paths"]["figures"] / "phase_10_sensitivity.png"
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    print("figure:", out)


def city_frontier(cfg):
    """City-wide version of `frontier`: the three costs (Wen et al. shade, shade with tree = building, UTCI relief)
    at equal detour, fixed θ swept 0.05-0.6, for the city sample walks of >= 300 m at the key hour of every collected
    date (pooled). Mean detour and mean change of the walk's length-weighted UTCI against the shortest walk."""
    import matplotlib.pyplot as plt
    from coolpath.routing import load_city_graph, search, tree_totals
    s, rc = cfg["solweig"], cfg["routing"]
    d = city_folder(cfg)
    g, L, _ = load_city_graph(cfg)
    e = pd.read_parquet(d / "edges_heat_city.parquet").set_index("edge_id").loc[g.edge_ids].reset_index()
    par = pd.read_parquet(cfg["paths"]["processed"] / "trips_city_parcels.parquet", columns=["node", "stop_node", "stop_dist"])
    par = par[par.stop_dist >= 300].reset_index(drop=True)
    fac, z = rc["search_radius_factor"], np.zeros_like(L)
    dates = [dd for dd in s["dates"] if f"utci_{label(cfg, s['hours'][0], dd)}" in e.columns]
    thetas = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    acc = {}                                   # (cost, θ) -> [sum detour, sum dutci, n]
    t0 = time.time()
    for dd in dates:
        lab = label(cfg, cfg["study"]["key_hour"], dd)
        ls, lt, lb = (e[f"L_{k}_{lab}"].to_numpy(np.float64) for k in ("sun", "tree", "bldg"))
        u = e[f"utci_{lab}"].to_numpy(np.float64)
        u_sun, u_bldg = city_refs(cfg)[lab]
        u = np.nan_to_num(u, nan=u_sun)
        r = np.clip((u_sun - u) / (u_sun - u_bldg), -1, 1.5)
        uL = u * L
        costs = {"shade": (ls, lt, lb, rc["tree_factor"]), "shade_tf1": (ls, lt, lb, 1.0), "heat": (L * (1 - r), z, L * r, 0.5)}
        for sn, grp in par.groupby("stop_node"):
            o, dests = int(sn), grp.node.to_numpy(np.int64)
            rad = max(grp.stop_dist.max(), 1.0) * fac + 1
            _, _, p0, e0 = search(g, L, z, z, z, o, "shortest", max_dist=rad)
            A = tree_totals(p0, e0, o, dests, L, uL, z, z, np.zeros(4), 0.5, 0.0, e0)
            for name, (a, b, c, tf) in costs.items():
                for th in thetas:
                    _, _, p1, e1 = search(g, L, a, b, c, o, "shade", theta_fixed=th, tree_factor=tf, max_dist=rad)
                    B = tree_totals(p1, e1, o, dests, L, uL, z, z, np.zeros(4), 0.5, 0.0, e0)
                    ok = np.isfinite(A[:, 0]) & np.isfinite(B[:, 0])
                    det = B[ok, 0] / A[ok, 0] - 1
                    du = B[ok, 1] / B[ok, 0] - A[ok, 1] / A[ok, 0]
                    v = acc.setdefault((name, th), [0.0, 0.0, 0])
                    v[0] += det.sum(); v[1] += du.sum(); v[2] += int(ok.sum())
        print(lab, f"{time.time() - t0:.0f} s", flush=True)
    rows = [{"cost": k[0], "theta": k[1], "detour": v[0] / v[2], "dutci": v[1] / v[2], "trips": v[2]} for k, v in acc.items()]
    fr = pd.DataFrame(rows)
    (cfg["paths"]["tables"] / "phase_10_city_frontier.json").write_text(json.dumps(
        {"labels": [label(cfg, cfg["study"]["key_hour"], dd) for dd in dates], "min_walk_m": 300, "rows": rows}, indent=2))
    print(fr.round(4).to_string())
    names = {"shade": ("shade classes, tree = ½ building (Wen et al.)", "#2a78d6"),
             "shade_tf1": ("shade classes, tree = building", "#1baf7a"), "heat": ("SOLWEIG UTCI", "#eb6834")}
    fig, ax = plt.subplots(figsize=(8, 5.2), facecolor="#fcfcfb")
    for k, (lab_, col) in names.items():
        f = fr[fr.cost == k]
        ax.plot(f.detour * 100, -f.dutci, color=col, lw=2, marker="o", ms=5, label=lab_)
    for sp_ in ("top", "right"):
        ax.spines[sp_].set_visible(False)
    ax.grid(color="#e6e5e0", lw=0.8)
    ax.set_xlim(0, fr.detour.max() * 100 * 1.1)
    ax.set(xlabel="mean detour over the shortest walk (%)", ylabel="mean UTCI along the walk: reduction (°C)")
    ax.set_title(f"Same detour, cooler walk, city-wide\n{fr.trips.iloc[0] // len(dates):,} walks home from transit (≥ 300 m) × "
                 f"{len(dates)} days at {cfg['study']['key_hour']}:00; points = fixed θ 0.05–0.6", loc="left", fontsize=11)
    ax.legend(frameon=False, loc="lower right")
    out = cfg["paths"]["figures"] / "phase_10_city_frontier.png"
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    print("figure:", out)


def demo_utci(cfg, date):
    """UTCI per demo network edge (site/app/data/demo_network.json, current build) and report hour from the city run
    of `date`: points every 2 m (demo_points); off bridges the nearest pixel with UTCI in the server's core rasters
    (as the server's post-processing does), on bridge decks the nearest point the server sampled on a deck (deck UTCI
    from the deck pixel's Tmrt) within 5 m, else the raster. Returns {label: array per edge} (NaN on indoor parts)."""
    from scipy.spatial import cKDTree
    s = cfg["solweig"]
    d = city_folder(cfg)
    out_dir = d / "results" / date / "out"
    edges, _, _, px, py, eid, seg, on_b = demo_points(cfg)
    rr, k_ = city_runs(cfg), s["city"]["core_m"]
    x0, y0 = min(r["core"][0] for r in rr.values()), min(r["core"][1] for r in rr.values())
    keys = np.array([f"x{a:02d}_y{b:02d}" for a, b in zip(np.floor((px - x0) / k_).astype(int), np.floor((py - y0) / k_).astype(int))])
    hours = s["hours"]
    vals = np.full((len(px), len(hours)), np.nan)
    for key in np.unique(keys):
        sel = np.where(keys == key)[0]
        with rasterio.open(out_dir / f"utci_{key}.tif") as f:
            met_hours = [int(h) for h in f.tags()["hours"].split(",")]
            arr = f.read().astype(np.float64)
            arr[arr == f.nodata] = np.nan
            arr /= s["city"]["utci_scale"]
            T = f.transform
        r = np.clip(((T.f - py[sel]) / -T.e).astype(int), 0, arr.shape[1] - 1)
        c = np.clip(((px[sel] - T.c) / T.a).astype(int), 0, arr.shape[2] - 1)
        _, (ir, ic) = ndimage.distance_transform_edt(np.isnan(arr[0]), return_indices=True)
        for j, h in enumerate(hours):
            vals[sel, j] = arr[met_hours.index(h), ir[r, c], ic[r, c]]
    if on_b.any():                                   # bridge decks: nearest server deck point
        cols = [f"utci_{h:02d}" for h in hours]
        sol = pd.concat([pd.read_parquet(f, columns=["pid", *cols]) for f in sorted(out_dir.glob("points_*.parquet"))])
        bp = pd.read_parquet(d / "points_city.parquet", columns=["pid", "x", "y", "on_bridge"])
        if (d / "demo_points.parquet").exists():
            bp = pd.concat([bp, pd.read_parquet(d / "demo_points.parquet", columns=["pid", "x", "y", "on_bridge"])])
        bp = bp[bp.on_bridge].merge(sol, on="pid")
        dist, k = cKDTree(bp[["x", "y"]].to_numpy()).query(np.c_[px[on_b], py[on_b]])
        near = dist <= 5
        vals[np.where(on_b)[0][near]] = bp[cols].to_numpy()[k[near]]
    n = len(edges)
    w = np.bincount(eid, seg, n)
    out = {}
    for j, h in enumerate(hours):
        ok = np.isfinite(vals[:, j])
        wt = np.bincount(eid[ok], seg[ok], n)
        out[label(cfg, h, date)] = np.bincount(eid[ok], seg[ok] * vals[ok, j], n) / np.where(wt > 0, wt, np.nan)
    out["_points"] = {"points": len(px), "bridge": int(on_b.sum()), "edges_sampled": int((w > 0).sum())}
    return out


def web_export(cfg):
    """UTCI for the website (site/app/data), on top of the phase-9 export.

    demo_network.json gains U (per time of `times`, per edge, 0.1 C; null on indoor parts) and heat (the relief
    references u_sun / u_bldg per time and the clip range); demo UTCI from the city run of each date (demo_utci).
    The study dates are the SOLWEIG dates (config study.dates), so a time label means the same moment for shade and
    heat. city_shade.pmtiles is rebuilt with u0..u23 (UTCI x 10) next to s0..s23."""
    import subprocess
    s = cfg["solweig"]
    app = ROOT / "site" / "app" / "data"
    path = app / "demo_network.json"
    net = json.loads(path.read_text())
    times = net["times"]
    assert set(t[:4] for t in times) == {d[5:].replace("-", "") for d in s["dates"]}, \
        "demo_network.json times are not the SOLWEIG dates: rerun phases 3-9 with study.dates = solweig.dates"
    lab_of = {t: t for t in times}
    refs = city_refs(cfg)
    kind = np.asarray(net["edges"]["kind"])
    n = len(kind)
    U, pts_info = {}, {}
    for date in s["dates"]:
        r = demo_utci(cfg, date)
        pts_info[date] = r.pop("_points")
        U.update(r)
    rnd = lambda a: [None if (k == 2 or not np.isfinite(x)) else round(float(x), 1) for x, k in zip(a, kind)]
    net["U"] = [rnd(U[lab_of[t]]) for t in times]
    net["heat"] = {"u_sun": [round(refs[lab_of[t]][0], 2) for t in times], "u_bldg": [round(refs[lab_of[t]][1], 2) for t in times],
                   "clip": [-1, 1.5], "note": "UTCI from SOLWEIG (city run); sun of hour h at h - 0:30, as the shade"}
    missing = sum(v is None for t in net["U"] for v, k in zip(t, kind) if k != 2)
    path.write_text(json.dumps(net, separators=(",", ":"), allow_nan=False))
    print(f"demo_network.json: U for {len(times)} times x {n} edges, {missing} outdoor values missing, "
          f"{path.stat().st_size / 1e6:.1f} MB; points {pts_info}")

    # city sidewalks: shade share s0..s23 (phase-9 definition) and UTCI x 10 u0..u23, one feature per graph edge
    proc = cfg["paths"]["processed"]
    ge = gpd.read_parquet(proc / "graph_edges.parquet", columns=["edge_id", "geometry"])
    tab = pd.read_parquet(proc / "edges_shade.parquet", columns=["edge_id", "length", *[f"L_sun_{t}" for t in times]])
    eh = pd.read_parquet(city_folder(cfg) / "edges_heat_city.parquet", columns=["edge_id", *[f"utci_{lab_of[t]}" for t in times]])
    ge = ge.merge(tab, on="edge_id").merge(eh, on="edge_id", how="left")
    for i, t in enumerate(times):
        ge[f"s{i}"] = np.clip(np.round(100 * (1 - ge[f"L_sun_{t}"] / ge.length.clip(lower=1e-6))), 0, 100).astype(np.uint8)
        ge[f"u{i}"] = np.round(10 * ge[f"utci_{lab_of[t]}"].fillna(0)).astype(np.int16)
    ge = ge[["edge_id", *[f"s{i}" for i in range(len(times))], *[f"u{i}" for i in range(len(times))], "geometry"]].to_crs(4326)
    seq = cfg["paths"]["interim"] / "city_shade.geojsonl"
    seq.unlink(missing_ok=True)
    ge.to_file(seq, driver="GeoJSONSeq")
    pm = app / "city_shade.pmtiles"
    tip = ROOT.parent / "envs" / "tiles" / "bin" / "tippecanoe"
    subprocess.run([str(tip) if tip.exists() else "tippecanoe", "-o", str(pm), "--force", "-l", "sidewalks",
                    "--minimum-zoom=13", "--maximum-zoom=16", "--drop-densest-as-needed", "--no-tile-size-limit", "-q",
                    str(seq)], check=True)
    print(f"city_shade.pmtiles: {len(ge)} edges, {pm.stat().st_size / 1e6:.1f} MB")


def web_layers(cfg):
    """Heat next to sun on the website's phase-7 layers (site/app/data, after phase 9's export).

    From routes_city.parquet at the key hour of every date (pooled, as phase 7):
    grid.geojson (bus walks, cluster length band, cells as exported by phase 9) and stations.geojson (rapid walks,
    per length band) gain u_short (mean UTCI along the shortest walk, C), u_cool (how much cooler the coolest walk
    is, C), ext_m (metres of extreme heat stress on the shortest walk), ext_red (share of them the coolest walk
    avoids, walks that have any) and same (coolest walk = shadiest walk). summary.json gains phase10: the city
    medians of UTCI by shade class per time and the mode x length band table."""
    app = ROOT / "site" / "app" / "data"
    keys = [label(cfg, cfg["study"]["key_hour"], d) for d in city_dates(cfg)]
    r = pd.read_parquet(city_folder(cfg) / "routes_city.parquet",
                        columns=["trip", "time", "mode", "length_bin", "short_utci", "heat_dutci", "short_extreme",
                                 "heat_extreme_red", "heat_same_as_shade"])
    r = r[r.time.isin(keys)]
    par = pd.read_parquet(cfg["paths"]["processed"] / "trips_city_parcels.parquet", columns=["x", "y", "station"])
    r = r.join(par, on="trip")
    agg = dict(u_short=("short_utci", "mean"), u_cool=("heat_dutci", lambda x: -x.mean()), ext_m=("short_extreme", "mean"),
               ext_red=("heat_extreme_red", "mean"), same=("heat_same_as_shade", "mean"), n=("trip", "size"))
    rnd = {"u_short": 2, "u_cool": 3, "ext_m": 1, "ext_red": 3, "same": 3}

    # grid: phase 7's cells (500 m from the surface grid's north-west corner), bus walks of the cluster band
    gm, cb = int(cfg["metrics"]["grid_m"]), cfg["metrics"]["cluster_length_bin"]
    with rasterio.open(cfg["paths"]["processed"] / "surface_building.tif") as f:
        x0, y0 = f.bounds.left, f.bounds.top
    b = r[(r["mode"] == "bus") & (r.length_bin == cb)].copy()
    b["col"], b["row"] = ((b.x - x0) // gm).astype(int), ((y0 - b.y) // gm).astype(int)
    cell = b.groupby(["row", "col"]).agg(**agg)
    g = json.loads((app / "grid.geojson").read_text())
    cen = gpd.GeoSeries([shapely.geometry.shape(f["geometry"]).centroid for f in g["features"]], crs=4326).to_crs(cfg["project"]["crs"])
    hit = 0
    for f, c in zip(g["features"], cen):
        key = (int((y0 - c.y) // gm), int((c.x - x0) // gm))
        if key in cell.index:
            hit += 1
            f["properties"].update({k: (None if not np.isfinite(v) else round(float(v), rnd[k])) for k, v in cell.loc[key].items() if k != "n"})
    (app / "grid.geojson").write_text(json.dumps(g, separators=(",", ":")))

    # rapid-transit stations, every length band
    st = r[r["mode"] == "rapid"].groupby(["station", "length_bin"], observed=True).agg(**agg)
    s = json.loads((app / "stations.geojson").read_text())
    for f in s["features"]:
        p = f["properties"]
        for bn in ("<300", "300-800", "800-1200"):
            row = st.loc[(p["station"], bn)] if (p["station"], bn) in st.index else None
            for k in rnd:
                v = None if row is None else row[k]
                p[f"{k}_{bn}"] = None if v is None or not np.isfinite(v) else round(float(v), rnd[k])
    (app / "stations.geojson").write_text(json.dumps(s, separators=(",", ":")))

    sm = json.loads((app / "summary.json").read_text())
    c10 = json.loads((cfg["paths"]["tables"] / "phase_10_city.json").read_text())
    rt = json.loads((cfg["paths"]["tables"] / "phase_10_city_routes.json").read_text())
    sm["phase10"] = {
        "by_hour": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items()
                     if k in ("label", "u_sun", "u_tree", "u_bldg", "tree_relief")} for x in c10["by_hour"]],
        "by_mode_bin_key_hour": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()}
                                 for x in rt["by_mode_bin_key_hour"]],
        "key_labels": keys, "utci_categories": UTCI_BINS}
    (app / "summary.json").write_text(json.dumps(sm, separators=(",", ":")))
    print(f"grid.geojson: heat for {hit}/{len(g['features'])} cells; stations.geojson: {len(s['features'])} stations")
    print(cell[["u_short", "u_cool", "ext_m", "ext_red", "same"]].describe().round(3).to_string())


def main(args):
    cfg = load_config()
    steps = {"met": met, "inputs": inputs, "network": network, "figure": figure, "routes": routes,
             "frontier": frontier, "city-points": city_points, "city-package": city_package,
             "city-shade": city_shade, "city-bundle": city_bundle, "city-collect": city_collect,
             "city-routes": city_routes, "city-sensitivity": city_sensitivity, "city-frontier": city_frontier,
             "web-export": web_export, "web-layers": web_layers}
    for a in args or ["met", "inputs"]:
        steps[a](cfg)


if __name__ == "__main__":
    main(sys.argv[1:])
