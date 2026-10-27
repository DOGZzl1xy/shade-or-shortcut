"""Website data export (phase 9): demo network JSON for the in-browser router, city sidewalk shade
for tippecanoe, and small GeoJSON layers. The browser router (site/app/js/router.js) and
`load_network_json` below read the same file, so the JS / Python consistency test compares like with like."""

import json

import geopandas as gpd
import numpy as np
import shapely

from coolpath.routing import CSR

KIND_CODES = {"connector": 1, "indoor": 2, "open_air": 3}      # 0 = outdoor edge present in every scenario
SCENARIOS = {"outdoor": "Outdoor only", "no_penncard": "+ buildings without PennCard", "all": "+ all annotated buildings"}


def r(a, nd):
    return np.round(np.asarray(a, np.float64), nd).tolist()


def network_json(edges: gpd.GeoDataFrame, labels, routing_cfg, theta_curve) -> dict:
    """Columnar demo network. Node ids are re-indexed 0..n-1; lengths and shade lengths are rounded
    to 1 cm, and the router uses exactly these rounded values."""
    ids, inv = np.unique(np.r_[edges.u.to_numpy(), edges.v.to_numpy()], return_inverse=True)
    m = len(edges)
    g4326 = edges.to_crs(4326)
    starts = shapely.get_coordinates(shapely.get_point(edges.geometry.values, 0))
    ends = shapely.get_coordinates(shapely.get_point(edges.geometry.values, -1))
    nxy = np.zeros((len(ids), 2))
    nxy[inv[:m]] = starts
    nxy[inv[m:]] = ends
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(edges.crs, 4326, always_xy=True).transform(nxy[:, 0], nxy[:, 1])
    kind = edges.kind.map(KIND_CODES).fillna(0).astype(int).to_numpy()
    return {
        "crs_note": "lon/lat (EPSG:4326); lengths in metres",
        "times": list(labels),
        "params": {"theta": theta_curve, "tree_factor": routing_cfg["tree_factor"],
                   "indoor_factor": routing_cfg["indoor_factor"]},
        "scenarios": SCENARIOS,
        "nodes": {"lon": r(lon, 6), "lat": r(lat, 6)},
        "edges": {
            "u": inv[:m].tolist(), "v": inv[m:].tolist(),
            "len": r(edges.length, 2), "kind": kind.tolist(),
            "building": edges.building_id.astype(int).tolist(),
            "penncard": edges.needs_penncard.astype(int).tolist(),
            "geom": [r(np.asarray(gm.coords)[:, :2].ravel(), 6) for gm in g4326.geometry.values],
        },
        "L": {k: [r(edges[f"L_{k}_{lab}"], 2) for lab in labels] for k in ("sun", "tree", "bldg", "indoor")},
    }


def load_network_json(path):
    """Arrays as the browser router sees them (for the consistency test and Python cross-checks)."""
    d = json.loads(open(path).read())
    e = d["edges"]
    out = {"n": len(d["nodes"]["lon"]), "u": np.array(e["u"]), "v": np.array(e["v"]), "len": np.array(e["len"], float),
           "kind": np.array(e["kind"]), "penncard": np.array(e["penncard"], bool), "times": d["times"],
           "L": {k: np.array(v, float) for k, v in d["L"].items()}, "params": d["params"]}
    return out


def scenario_mask(net, scenario):
    indoor_side = net["kind"] > 0
    if scenario == "outdoor":
        return ~indoor_side
    if scenario == "no_penncard":
        return ~indoor_side | ~net["penncard"]
    return np.ones(len(net["u"]), bool)


def scenario_csr(net, scenario):
    """CSR over the edges of one scenario, in file order (the router builds the same)."""
    sel = np.where(scenario_mask(net, scenario))[0]
    return CSR(net["u"][sel], net["v"][sel], net["n"]), sel
