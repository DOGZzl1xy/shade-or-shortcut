"""JS / Python consistency (spec phase 9): 100 random origin-destination pairs on the exported demo
network, random scenario and time; the browser router must return the same paths as coolpath.routing,
with lengths within 0.1 m. As in the analysis (phase 8), the shortest walk is always taken on the
outdoor-only network and the coolest walk on the scenario network."""

import json
import shutil
import subprocess

import numpy as np
import pytest

from coolpath import ROOT
from coolpath.export import load_network_json, scenario_csr
from coolpath.routing import path_edges, search

NET = ROOT / "site" / "app" / "data" / "demo_network.json"
CLI = ROOT / "tests" / "js" / "route_cli.mjs"

pytestmark = pytest.mark.skipif(not NET.exists() or shutil.which("node") is None,
                                reason="needs site/app/data/demo_network.json (scripts.phase_09_export) and node")


def python_route(net, scenario, t, o, d):
    go, so = scenario_csr(net, "outdoor")
    z = np.zeros(len(so))
    _, _, p0, e0 = search(go, net["len"][so], z, z, z, o, "shortest")
    g, sel = scenario_csr(net, scenario)
    L = net["len"][sel]
    ls, lt, lb, li = (net["L"][k][t][sel] for k in ("sun", "tree", "bldg", "indoor"))
    p = net["params"]
    tp = np.array([p["theta"][k] for k in ("a", "k", "x0", "b")], float)
    _, _, p1, e1 = search(g, L, ls, lt, lb, o, "shade", tp=tp, tree_factor=p["tree_factor"], l_indoor=li,
                          indoor_factor=p["indoor_factor"])
    a, c = so[path_edges(p0, e0, d)], sel[path_edges(p1, e1, d)]
    return a, c, net["len"][a].sum(), net["len"][c].sum()


def test_js_matches_python(tmp_path):
    net = load_network_json(NET)
    rng = np.random.default_rng(42)
    g, sel = scenario_csr(net, "outdoor")
    _, d0, _, _ = search(g, net["len"][sel], *(np.zeros(len(sel)),) * 3, 0, "shortest")
    reach = np.where(np.isfinite(d0))[0]                    # nodes connected in every scenario
    scen = ["outdoor", "no_penncard", "all"]
    ods = [{"o": int(rng.choice(reach)), "d": int(rng.choice(reach)), "scenario": scen[int(rng.integers(3))],
            "t": int(rng.integers(len(net["times"])))} for _ in range(100)]
    (tmp_path / "ods.json").write_text(json.dumps(ods))
    js = json.loads(subprocess.run(["node", str(CLI), str(NET), str(tmp_path / "ods.json")], check=True,
                                   capture_output=True, text=True).stdout)
    for q, r in zip(ods, js):
        a, c, la, lc = python_route(net, q["scenario"], q["t"], q["o"], q["d"])
        assert r["shortest"] == a.tolist() and r["coolest"] == c.tolist(), q
        assert abs(r["shortest_len"] - la) < 0.1 and abs(r["coolest_len"] - lc) < 0.1
        assert not (net["kind"][a] > 0).any()              # the shortest walk never uses indoor-side edges
