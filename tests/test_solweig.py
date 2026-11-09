"""Phase 10 helpers: land-cover reclassification, sub-domain layout, met-file column order."""

import numpy as np
import shapely

from coolpath import load_config
from coolpath.areas import solweig_box
from scripts.phase_10_solweig import UMEP_COLS, runs, umep_landcover

MAP = {1: 0, 2: 5, 3: 6, 4: 7, 5: 2, 6: 1, 7: 1}


def test_canopy_takes_class_of_surface_underneath():
    lc = np.array([[6, 6, 1, 1, 2, 2],
                   [6, 6, 1, 1, 2, 2]], np.uint8)          # road | canopy | grass
    u = umep_landcover(lc, MAP)
    assert u.tolist() == [[1, 1, 1, 5, 5, 5]] * 2
    assert set(np.unique(u)) <= {1, 2, 5, 6, 7}           # no UMEP 3 / 4 (SOLWEIG rejects them)


def test_unknown_pixels_are_filled_too():
    lc = np.array([[0, 5, 5]], np.uint8)
    assert umep_landcover(lc, MAP).tolist() == [[2, 2, 2]]


def test_cores_tile_the_domain_and_runs_add_the_margin():
    cfg = load_config()
    s = cfg["solweig"]
    rr = runs(cfg)
    cores = [shapely.box(*r["core"]) for r in rr.values()]
    dom = solweig_box(cfg, margin=False)
    assert abs(shapely.union_all(cores).area - dom.area) < 1e-6
    assert abs(sum(c.area for c in cores) - dom.area) < 1e-6        # no overlap between cores
    for r in rr.values():
        assert shapely.box(*r["run"]).equals(shapely.box(*r["core"]).buffer(s["margin_m"], join_style="mitre"))
        assert solweig_box(cfg).contains(shapely.box(*r["run"]))      # every run lies on the phase-2 surfaces


def test_met_columns_where_solweig_gpu_reads_them():
    # utci_process.compute_utci: Ws 9, RH 10, Ta 11, P (kPa) 12, global radiation 14, kdiff 21, kdir 22, wdir 23
    assert [UMEP_COLS[i] for i in (1, 2, 3, 9, 10, 11, 12, 14, 21, 22, 23)] == \
        ["id", "it", "imin", "U", "RH", "Tair", "pres", "kdown", "kdiff", "kdir", "wdir"]
