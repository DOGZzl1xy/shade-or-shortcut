"""Synthetic shadow test: a single 20 m block on flat ground."""

import numpy as np
import pytest

from coolpath.shadow import BUILDING, SUN, TREE, cast, local_max

H = 20.0
N = 400          # 400 x 400 m grid, 1 m pixels, origin (0, 400) top-left
B0, B1 = 190, 210  # block occupies x, y in [190, 210)


def scene():
    dem = np.zeros((N, N), np.float32)
    hb = np.zeros((N, N), np.float32)
    hb[N - B1:N - B0, B0:B1] = H        # rows are measured from the top
    ht = np.zeros((N, N), np.float32)
    return dem, hb, ht


def shadow_length(az, el, obs_h, dem=None, hb=None, ht=None):
    """Distance from the block's anti-sun face to the last shaded sample along the shadow axis."""
    if dem is None:
        dem, hb, ht = scene()
    lmax = local_max(dem + np.maximum(hb, ht), 1.0, 32, 1000)
    # walk away from the sun starting at the block's centre line
    d = np.arange(0.05, 150, 0.1)
    ax, ay = -np.sin(np.radians(az)), -np.cos(np.radians(az))
    cx = cy = (B0 + B1) / 2
    # distance from block centre to its face along the axis (axis-aligned az only)
    face = (B1 - B0) / 2
    px, py = cx + (face + d) * ax, cy + (face + d) * ay
    st = cast(px, py, dem, hb, ht, lmax, 0.0, float(N), 1.0,
              np.array([az]), np.array([el]), obs_h, 1000.0, 0.5)[:, 0]
    shaded = d[st == BUILDING]
    return shaded.max() if shaded.size else 0.0


@pytest.mark.parametrize("az", [90.0, 180.0, 270.0, 0.0])
@pytest.mark.parametrize("el", [15.0, 30.0, 45.0, 60.0])
def test_block_shadow_length_ground_observer(az, el):
    L = shadow_length(az, el, obs_h=0.0)
    assert abs(L - H / np.tan(np.radians(el))) <= 1.0


@pytest.mark.parametrize("el", [15.0, 30.0, 60.0])
def test_block_shadow_length_pedestrian(el):
    L = shadow_length(180.0, el, obs_h=1.5)
    assert abs(L - (H - 1.5) / np.tan(np.radians(el))) <= 1.0


def test_no_shadow_on_sun_side():
    dem, hb, ht = scene()
    lmax = local_max(dem + hb, 1.0, 32, 1000)
    st = cast(np.array([200.0]), np.array([150.0]), dem, hb, ht, lmax, 0.0, float(N), 1.0,
              np.array([0.0]), np.array([30.0]), 1.5, 1000.0, 0.5)   # sun in the north, point south
    assert st[0, 0] == SUN


def test_tree_then_building_priority():
    dem, hb, ht = scene()
    ht[N - 185:N - 180, 170:B1] = 8.0     # a tree row (y 180-185) just south of the block
    lmax = local_max(dem + np.maximum(hb, ht), 1.0, 32, 1000)
    # sun in the north: a point whose ray passes the tree but misses the block -> tree
    st = cast(np.array([180.5]), np.array([175.0]), dem, hb, ht, lmax, 0.0, float(N), 1.0,
              np.array([0.0]), np.array([30.0]), 1.5, 1000.0, 0.5)
    assert st[0, 0] == TREE
    # a point whose ray passes the tree and then the block -> building wins
    st = cast(np.array([200.0]), np.array([175.0]), dem, hb, ht, lmax, 0.0, float(N), 1.0,
              np.array([0.0]), np.array([30.0]), 1.5, 1000.0, 0.5)
    assert st[0, 0] == BUILDING


def test_under_canopy_is_tree_shade():
    dem, hb, ht = scene()
    ht[100, 100] = 10.0
    lmax = local_max(dem + np.maximum(hb, ht), 1.0, 32, 1000)
    st = cast(np.array([100.5]), np.array([N - 100.5]), dem, hb, ht, lmax, 0.0, float(N), 1.0,
              np.array([180.0]), np.array([80.0]), 1.5, 1000.0, 0.5)
    assert st[0, 0] == TREE


def test_local_max_bounds_everything_within_radius():
    rng = np.random.default_rng(42)
    top = rng.random((300, 300)).astype(np.float32) * 50
    lm = local_max(top, 1.0, 32, 100)
    r, c = 150, 150
    assert lm[r, c] >= top[r - 100:r + 101, c - 100:c + 101].max()


def test_terrain_shadow_only_when_enabled():
    dem = np.zeros((N, N), np.float32)
    dem[:, 220:] = 30.0                       # 30 m cliff east of x = 220
    hb = np.zeros((N, N), np.float32)
    ht = np.zeros((N, N), np.float32)
    lmax = local_max(dem, 1.0, 32, 1000)
    args = (np.array([200.0]), np.array([200.0]), dem, hb, ht, lmax, 0.0, float(N), 1.0,
            np.array([90.0]), np.array([30.0]), 1.5, 1000.0, 0.5)
    assert cast(*args)[0, 0] == SUN
    assert cast(*args, terrain=True)[0, 0] == BUILDING


def test_bridge_deck_observer():
    from coolpath.shadow import cast_z
    dem = np.zeros((N, N), np.float32)
    hb = np.zeros((N, N), np.float32)
    hb[N - 210:N - 190, 100:300] = 8.0        # an 8 m deck running east-west
    ht = np.zeros((N, N), np.float32)
    lmax = local_max(dem + hb, 1.0, 32, 1000)
    px, py = np.array([200.0, 200.0]), np.array([200.0, 200.0])
    st = cast_z(px, py, np.array([8.0 + 1.5, 0.0 + 1.5]), dem, hb, ht, lmax, 0.0, float(N), 1.0,
                np.array([180.0]), np.array([45.0]), 1.5, 1000.0, 0.5, False)
    assert st[0, 0] == SUN          # on the deck: sunny
    assert st[1, 0] == BUILDING     # under the deck: shaded by it
