"""Geometry engine tests on synthetic phantoms. No real imaging in tests.

Build small numpy phantoms (e.g. 64^3, 1 mm isotropic) with:
    - a body mask (ellipsoid)
    - a kidney mask (smaller ellipsoid on one side)
    - a stone (sphere inside the kidney)
    - a colon (tube placed to block some rays)
    - a rib (thin slab)

Cases to cover:
    - a tract with a clear line to the stone has collision == False
    - placing the colon on the line flips collision == True and names "colon"
    - reachable_fraction is 1.0 for a stone entirely inside the cone and
      0.0 for a stone outside it
    - a larger sweep angle never decreases reachable_fraction
    - supracostal flag flips when the entry point moves above the rib slab

The phantom here is an 80^3, 1 mm grid. The body is an elliptic cylinder
(semi-axes 36 x 26 mm) so rays in the axial plane are easy to reason about,
and ribs 11 and 12 are thin arcs inside the flank wall so the mid-axillary
line and the supracostal edge come from rib masks as they do on real scans.
Shapes are defined in mm and voxelized through the affine, so the same
physical phantom can be stored in a different voxel order.
"""

import itertools
import math

import numpy as np
import pytest

from stoneplanner import geometry as g

N = 80
BODY_A, BODY_B = 36.0, 26.0          # body semi-axes in x (lateral) and y (AP), mm
STONE_C = np.array([16.0, -8.0, 0.0])  # right kidney centre and stone centre, RAS mm
KIDNEY_R = 8.0                       # kidney radius in the axial plane, mm
RIB12_Z = (10.0, 12.0)
RIB11_Z = (22.0, 24.0)

AFFINE = np.array([
    [1.0, 0.0, 0.0, -40.0],
    [0.0, 1.0, 0.0, -40.0],
    [0.0, 0.0, 1.0, -40.0],
    [0.0, 0.0, 0.0, 1.0],
])
# Same physical grid stored with x and y reversed (LPS-like voxel order).
AFFINE_FLIPPED = np.array([
    [-1.0, 0.0, 0.0, 39.0],
    [0.0, -1.0, 0.0, 39.0],
    [0.0, 0.0, 1.0, -40.0],
    [0.0, 0.0, 0.0, 1.0],
])

LABELS = g.PlanParams().organ_labels


def _mm_grid(affine, shape=(N, N, N)):
    ijk = np.indices(shape).reshape(3, -1).T
    xyz = g._voxel_to_mm(ijk, affine)
    return [c.reshape(shape) for c in xyz.T]


def _body_r(x, y):
    return np.sqrt((x / BODY_A) ** 2 + (y / BODY_B) ** 2)


def make_phantom(affine=AFFINE, colon_xy=(16.0, 12.0), extra_stones=()):
    """Return ct, organs, stones on the grid described by affine.

    extra_stones: iterable of (centre_xyz_mm, radius_mm) added as labels 2, 3, ...
    """
    x, y, z = _mm_grid(affine)
    organs = np.zeros((N, N, N), dtype=np.int16)

    body = (_body_r(x, y) <= 1.0) & (np.abs(z) <= 38.0)
    ct = np.where(body, 40.0, -1000.0).astype(np.float32)

    for sign, side in ((1.0, "right"), (-1.0, "left")):
        kidney = (((x - sign * STONE_C[0]) ** 2 + (y - STONE_C[1]) ** 2) / KIDNEY_R ** 2
                  + (z / 14.0) ** 2) <= 1.0
        organs[kidney] = LABELS[f"kidney_{side}"]
        arc = (_body_r(x, y) >= 0.80) & (_body_r(x, y) <= 0.90) & (sign * x > 4.0) & (y < 6.0)
        organs[arc & (z >= RIB12_Z[0]) & (z <= RIB12_Z[1])] = LABELS[f"rib_{side}_12"]
        organs[arc & (z >= RIB11_Z[0]) & (z <= RIB11_Z[1])] = LABELS[f"rib_{side}_11"]
        hip = ((sign * x >= 10) & (sign * x <= 30) & (y >= -14) & (y <= 4)
               & (z >= -36) & (z <= -30))
        organs[hip] = LABELS[f"hip_{side}"]

    spine = x ** 2 + (y + 15.0) ** 2 <= 25.0
    organs[spine & (z < 0)] = LABELS["vertebrae_L1"]
    organs[spine & (z >= 0)] = LABELS["vertebrae_T12"]

    if colon_xy is not None:
        colon = ((x - colon_xy[0]) ** 2 + (y - colon_xy[1]) ** 2 <= 1.5 ** 2) & body
        organs[colon] = LABELS["colon"]

    stones = np.zeros((N, N, N), dtype=np.int16)
    sx, sy, sz = STONE_C
    stones[(x - sx) ** 2 + (y - sy) ** 2 + (z - sz) ** 2 <= 2.5 ** 2] = 1
    for k, (c, r) in enumerate(extra_stones, start=2):
        stones[(x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2 <= r ** 2] = k
    return ct, organs, stones


def skin_point(origin, direction):
    """Point where a ray from inside the body crosses the cylinder wall."""
    d = np.asarray(direction, dtype=float)
    d = d / np.linalg.norm(d)
    lo, hi = 0.0, 200.0
    for _ in range(60):
        mid = (lo + hi) / 2
        p = origin + mid * d
        if _body_r(p[0], p[1]) <= 1.0:
            lo = mid
        else:
            hi = mid
    return origin + lo * d


POSTEROLATERAL = np.array([1.0, -1.0, 0.0]) / math.sqrt(2.0)


def _kidney_right(organs):
    return organs == LABELS["kidney_right"]


# ---------------------------------------------------------------------------
# Ray cast and collision


def test_clear_tract_has_no_collision():
    params = g.PlanParams()
    _ct, organs, _stones = make_phantom()
    entry = skin_point(STONE_C, POSTEROLATERAL)
    ray = g.cast_ray(entry, STONE_C, organs, _kidney_right(organs), AFFINE, params)
    assert ray["collision"] is False
    assert ray["collision_organ"] is None
    assert ray["tract_length_mm"] == pytest.approx(np.linalg.norm(STONE_C - entry))
    # The ray enters the kidney at its axial radius from the centre.
    assert ray["parenchymal_depth_mm"] == pytest.approx(KIDNEY_R, abs=1.0)
    assert ray["nearest_organ"] in {"rib", "colon"}
    assert ray["nearest_organ_mm"] > 0.0


def test_colon_on_the_line_collides():
    params = g.PlanParams()
    blocker = STONE_C + 11.0 * POSTEROLATERAL   # in the flank wall, outside the kidney
    _ct, organs, _stones = make_phantom(colon_xy=(blocker[0], blocker[1]))
    entry = skin_point(STONE_C, POSTEROLATERAL)
    ray = g.cast_ray(entry, STONE_C, organs, _kidney_right(organs), AFFINE, params)
    assert ray["collision"] is True
    assert ray["collision_organ"] == "colon"
    assert ray["nearest_organ_mm"] == 0.0


def test_rib_on_the_line_collides():
    params = g.PlanParams()
    _ct, organs, _stones = make_phantom()
    # An axial ray at rib 12 height into the upper pole crosses the rib arc.
    target = STONE_C + np.array([0.0, 0.0, 11.0])
    entry = skin_point(target, POSTEROLATERAL)
    ray = g.cast_ray(entry, target, organs, _kidney_right(organs), AFFINE, params)
    assert ray["collision"] is True
    assert ray["collision_organ"] == "rib"


# ---------------------------------------------------------------------------
# Reach


def _sphere_stone(centre, radius, shape=(64, 64, 64)):
    affine = np.eye(4)
    affine[:3, 3] = -32.0
    x, y, z = _mm_grid(affine, shape)
    stones = ((x - centre[0]) ** 2 + (y - centre[1]) ** 2 + (z - centre[2]) ** 2
              <= radius ** 2).astype(np.int16)
    return stones, affine


def test_reach_full_inside_and_zero_outside():
    params = g.PlanParams()
    stones, affine = _sphere_stone((0.0, 0.0, 20.0), 3.0)
    apex = np.zeros(3)
    assert g.reachable_fraction(apex, np.array([0.0, 0.0, 1.0]), stones, affine, params) == 1.0
    assert g.reachable_fraction(apex, np.array([1.0, 0.0, 0.0]), stones, affine, params) == 0.0
    assert g.reachable_fraction(apex, np.array([0.0, 0.0, -1.0]), stones, affine, params) == 0.0


def test_reach_respects_scope_depth():
    stones, affine = _sphere_stone((0.0, 0.0, 20.0), 3.0)
    short = g.PlanParams(scope_depth_mm=10.0)
    assert g.reachable_fraction(np.zeros(3), np.array([0.0, 0.0, 1.0]),
                                stones, affine, short) == 0.0


def test_wider_sweep_never_decreases_reach():
    stones, affine = _sphere_stone((0.0, 0.0, 20.0), 6.0)
    tilt = math.radians(25.0)
    axis = np.array([math.sin(tilt), 0.0, math.cos(tilt)])
    fractions = [
        g.reachable_fraction(np.zeros(3), axis, stones, affine, g.PlanParams(sweep_deg=s))
        for s in range(0, 91, 5)
    ]
    assert all(b >= a for a, b in itertools.pairwise(fractions))
    assert fractions[0] == 0.0 or fractions[0] < 0.05
    assert any(0.0 < f < 1.0 for f in fractions)
    assert fractions[-1] == 1.0


def test_reach_counts_every_stone():
    params = g.PlanParams()
    affine = np.eye(4)
    affine[:3, 3] = -32.0
    x, y, z = _mm_grid(affine, (64, 64, 64))
    stones = np.zeros((64, 64, 64), dtype=np.int16)
    stones[x ** 2 + y ** 2 + (z - 15) ** 2 <= 4] = 1
    stones[x ** 2 + y ** 2 + (z - 25) ** 2 <= 4] = 2
    stones[(x - 25) ** 2 + y ** 2 + z ** 2 <= 4] = 3   # off axis
    frac = g.reachable_fraction(np.zeros(3), np.array([0.0, 0.0, 1.0]), stones, affine, params)
    assert frac == pytest.approx(2.0 / 3.0, abs=0.01)


# ---------------------------------------------------------------------------
# Supracostal


def test_supracostal_flips_above_rib():
    params = g.PlanParams()
    _ct, organs, _stones = make_phantom()
    theta = math.radians(-60.0)
    x, y = BODY_A * math.cos(theta), BODY_B * math.sin(theta)
    below = np.array([x, y, RIB12_Z[0] - 5.0])
    above = np.array([x, y, RIB12_Z[1] + 3.0])
    assert g.supracostal(below, organs, AFFINE, "right", params) is False
    assert g.supracostal(above, organs, AFFINE, "right", params) is True


# ---------------------------------------------------------------------------
# Entry points and targets


def test_entry_points_on_operative_posterior_flank():
    params = g.PlanParams()
    ct, organs, _stones = make_phantom()
    body = g._body_mask(ct, organs, AFFINE, params)
    pts = g.skin_entry_points(body, organs, AFFINE, "right", params)
    flank = g._flank_bounds(organs, AFFINE, "right", params)
    assert 0 < len(pts) <= params.n_entry_points
    assert np.all(pts[:, 0] > 0.0)
    assert np.all(pts[:, 1] < flank.midaxillary_y)
    # Iliac crest from the hip label; upper bound two rib pitches above rib 12.
    assert flank.z_low == pytest.approx(-30.0)
    assert flank.z_high == pytest.approx(RIB12_Z[1] + 2 * (RIB11_Z[0] - RIB12_Z[0]), abs=1.0)
    assert np.all((pts[:, 2] >= flank.z_low) & (pts[:, 2] <= flank.z_high))
    # On the skin: elliptic radius within a voxel of 1.
    r = _body_r(pts[:, 0], pts[:, 1])
    assert np.all(np.abs(r - 1.0) < 0.05)

    left = g.skin_entry_points(body, organs, AFFINE, "left", params)
    assert np.all(left[:, 0] < 0.0)


def test_entry_points_subsample():
    params = g.PlanParams(n_entry_points=200)
    ct, organs, _stones = make_phantom()
    body = g._body_mask(ct, organs, AFFINE, params)
    assert len(g.skin_entry_points(body, organs, AFFINE, "right", params)) == 200


def test_targets_largest_plus_big_stones():
    params = g.PlanParams()
    # The phantom's own stone is ~65 mm3, under the 100 mm3 cutoff.
    _ct, _organs, stones = make_phantom(extra_stones=[
        ((14.0, -6.0, 9.0), 1.0),    # ~4 mm3, dropped
        ((18.0, -10.0, -8.0), 4.0),  # ~270 mm3, the largest
        ((14.0, -4.0, 8.0), 3.2),    # ~140 mm3, kept
    ])
    targets = g.target_points(stones, AFFINE, params)
    assert targets.shape == (2, 3)
    np.testing.assert_allclose(targets[0], [18.0, -10.0, -8.0], atol=0.2)
    np.testing.assert_allclose(targets[1], [14.0, -4.0, 8.0], atol=0.2)


def test_targets_keep_largest_even_if_small():
    params = g.PlanParams(min_target_stone_mm3=1e6)
    _ct, _organs, stones = make_phantom()
    targets = g.target_points(stones, AFFINE, params)
    assert targets.shape == (1, 3)
    np.testing.assert_allclose(targets[0], STONE_C, atol=0.2)


# ---------------------------------------------------------------------------
# Full pipeline


def test_plan_ranks_clear_full_reach_first():
    params = g.PlanParams()
    ct, organs, stones = make_phantom()
    tracts = g.plan(ct, organs, stones, AFFINE, side="right", params=params)
    assert 0 < len(tracts) <= params.top_n
    top = tracts[0]
    assert top.collision is False
    assert top.reachable_fraction == pytest.approx(1.0)
    np.testing.assert_allclose(top.target_mm, STONE_C, atol=0.2)
    assert top.entry_mm[0] > 0.0
    keys = [(t.collision, -t.reachable_fraction, -t.nearest_organ_mm, t.tract_length_mm)
            for t in tracts]
    assert keys == sorted(keys)


def test_plan_avoids_colon_blocked_tracts():
    params = g.PlanParams(top_n=10_000)
    blocker = STONE_C + 11.0 * POSTEROLATERAL
    ct, organs, stones = make_phantom(colon_xy=(blocker[0], blocker[1]))
    tracts = g.plan(ct, organs, stones, AFFINE, side="right", params=params)
    colon_hits = [t for t in tracts if t.collision_organ == "colon"]
    assert colon_hits, "the colon tube should block some candidate tracts"
    first_collision = next(i for i, t in enumerate(tracts) if t.collision)
    assert all(t.collision for t in tracts[first_collision:])
    assert tracts[0].collision is False


def test_plan_auto_side_and_contralateral_stone():
    params = g.PlanParams()
    # Small left stone; the right stone is larger, so auto picks right and the
    # left stone does not dilute the reachable fraction.
    ct, organs, stones = make_phantom(extra_stones=[((-16.0, -8.0, 0.0), 1.5)])
    assert g._resolve_side(stones, organs, AFFINE, params) == "right"
    tracts = g.plan(ct, organs, stones, AFFINE, side="auto", params=params)
    assert tracts[0].entry_mm[0] > 0.0
    assert tracts[0].reachable_fraction == pytest.approx(1.0)


def test_plan_is_independent_of_voxel_order():
    params = g.PlanParams(n_entry_points=100_000, top_n=5)
    a = g.plan(*make_phantom(AFFINE), AFFINE, side="right", params=params)
    b = g.plan(*make_phantom(AFFINE_FLIPPED), AFFINE_FLIPPED, side="right", params=params)
    for ta, tb in zip(a, b):
        assert ta.reachable_fraction == pytest.approx(tb.reachable_fraction)
        assert ta.nearest_organ_mm == pytest.approx(tb.nearest_organ_mm, abs=1e-6)
        assert ta.tract_length_mm == pytest.approx(tb.tract_length_mm, abs=1e-6)


def test_injected_targets_override_centroids():
    params = g.PlanParams()
    ct, organs, stones = make_phantom()
    target = STONE_C + np.array([0.0, 0.0, 6.0])
    tracts = g.plan(ct, organs, stones, AFFINE, side="right", params=params,
                    targets_mm=target[None, :])
    assert all(np.allclose(t.target_mm, target) for t in tracts)


def test_write_outputs(tmp_path):
    params = g.PlanParams(top_n=3)
    ct, organs, stones = make_phantom()
    tracts = g.plan(ct, organs, stones, AFFINE, side="right", params=params)
    csv_path, json_path = g.write_outputs(tracts, tmp_path, "phantom", "right", params)
    import json
    payload = json.loads(json_path.read_text())
    assert payload["params"]["sweep_deg"] == 25.0
    assert [row["rank"] for row in payload["tracts"]] == [1, 2, 3]
    header = csv_path.read_text().splitlines()[0].split(",")
    assert header[:4] == ["rank", "entry_xyz_mm", "target_xyz_mm", "reachable_fraction"]
