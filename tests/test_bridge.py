"""Bridge-deck detection (D-019): along the bridge yes, under it no."""

import geopandas as gpd
from shapely.geometry import LineString

from coolpath.network import bridge_deck


def run(edges):
    bridges = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)])])
    e = gpd.GeoDataFrame(geometry=edges)
    return bridge_deck(e, bridges, buffer_m=15, min_share=0.8, max_angle=20)


def test_rules():
    r = run([
        LineString([(10, 10), (190, 10)]),     # sidewalk on the deck, spans both OSM ways
        LineString([(100, -60), (100, 60)]),   # street passing under the bridge
        LineString([(100, 10), (400, 10)]),    # starts on the bridge, mostly on land (~37 %)
        LineString([(0, 10), (190, 50)]),      # parallel-ish but 12° and leaves the buffer
        LineString([(20, 5), (180, 12)]),      # slightly oblique (2.5°), stays in the buffer
        LineString([(500, 500), (600, 500)]),  # far away
    ])
    assert list(r.on_bridge) == [True, False, False, False, True, False]
    assert r.bridge_angle[1] == 90
    assert 0.33 < r.bridge_share[2] < 0.40     # (115 m incl. round cap) / 300 m
