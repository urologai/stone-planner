"""Posterior calyx proxy tests on synthetic kidneys. No real imaging.

The kidney is an ellipsoid (semi-axes 22 lateral, 18 AP, 40 long, mm) on a
1 mm grid with a sinus cavity open on its medial side, as the renal sinus is.
A right kidney sits at +x in RAS, so its medial side faces -x.
"""

import math

import numpy as np
import pytest

from stoneplanner import calyx

N = 100
AFFINE = np.array([
    [1.0, 0.0, 0.0, -50.0],
    [0.0, 1.0, 0.0, -50.0],
    [0.0, 0.0, 1.0, -50.0],
    [0.0, 0.0, 0.0, 1.0],
])
CENTRE = np.array([10.0, 0.0, 0.0])


def _grid(affine=AFFINE):
    ijk = np.indices((N, N, N)).reshape(3, -1).T
    return (ijk @ affine[:3, :3].T + affine[:3, 3]).T.reshape(3, N, N, N)


def kidney(rotation=None, with_sinus=True, mirror=False, affine=AFFINE):
    """Kidney mask; rotation is applied about CENTRE in RAS mm."""
    rotation = np.eye(3) if rotation is None else rotation
    x, y, z = _grid(affine)
    pts = np.stack([x, y, z], axis=-1)
    centre = CENTRE * ([-1, 1, 1] if mirror else 1)
    local = (pts - centre) @ rotation       # body-to-kidney coordinates
    if mirror:
        local[..., 0] *= -1
    lx, ly, lz = local[..., 0], local[..., 1], local[..., 2]
    mask = (lx / 22) ** 2 + (ly / 18) ** 2 + (lz / 40) ** 2 <= 1.0
    if with_sinus:
        sinus = ((lx + 14) / 10) ** 2 + (ly / 6) ** 2 + (lz / 16) ** 2 <= 1.0
        mask &= ~sinus
    return mask


def _rot_y(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])


# ---------------------------------------------------------------------------
# Frame


def test_frame_axes_right_kidney():
    f = calyx.kidney_frame(kidney(), AFFINE, "right")
    assert f.sinus_found
    np.testing.assert_allclose(f.long_axis, [0, 0, 1], atol=0.02)
    np.testing.assert_allclose(f.lateral, [1, 0, 0], atol=0.05)
    np.testing.assert_allclose(f.posterior, [0, -1, 0], atol=0.05)
    assert f.axis_origin_mm[0] < CENTRE[0]          # the sinus is medial


def test_frame_left_kidney_mirrors():
    f = calyx.kidney_frame(kidney(mirror=True), AFFINE, "left")
    np.testing.assert_allclose(f.lateral, [-1, 0, 0], atol=0.05)
    np.testing.assert_allclose(f.posterior, [0, -1, 0], atol=0.05)


def test_frame_without_sinus_falls_back_to_medial():
    f = calyx.kidney_frame(kidney(with_sinus=False), AFFINE, "right")
    assert not f.sinus_found
    np.testing.assert_allclose(f.lateral, [1, 0, 0], atol=0.05)


def test_frame_follows_tilted_kidney():
    # Lower pole swung laterally by 20 degrees, as kidneys usually lie.
    rot = _rot_y(20)
    f = calyx.kidney_frame(kidney(rotation=rot), AFFINE, "right")
    expected = rot @ np.array([0.0, 0.0, 1.0])
    assert abs(f.long_axis @ expected) > 0.995


# ---------------------------------------------------------------------------
# Posterior calyces


def test_three_posterior_calyces_inside_kidney():
    mask = kidney()
    targets = calyx.posterior_calyces(mask, AFFINE, "right")
    assert [t.group for t in targets] == ["lower", "mid", "upper"]
    assert all(t.source == "calyx" for t in targets)
    z = [t.point_mm[2] for t in targets]
    assert z[0] < z[1] < z[2]
    inv = np.linalg.inv(AFFINE)
    for t in targets:
        ijk = np.rint(inv[:3, :3] @ t.point_mm + inv[:3, 3]).astype(int)
        assert mask[tuple(ijk)], t


def test_calyx_axis_is_brodel_rotation():
    targets = calyx.posterior_calyces(kidney(), AFFINE, "right", angle_deg=30.0)
    a = targets[1].axis_mm
    np.testing.assert_allclose(a, [math.cos(math.radians(30)), -math.sin(math.radians(30)), 0],
                               atol=0.05)
    flat = calyx.posterior_calyces(kidney(), AFFINE, "right", angle_deg=0.0)[1].axis_mm
    np.testing.assert_allclose(flat, [1, 0, 0], atol=0.05)


def test_calyx_target_sits_depth_below_capsule():
    mask = kidney()
    for depth in (10.0, 15.0):
        mid = calyx.posterior_calyces(mask, AFFINE, "right", depth_mm=depth)[1]
        inv = np.linalg.inv(AFFINE)
        steps = np.arange(0.0, 60.0, 0.25)
        ray = mid.point_mm + steps[:, None] * mid.axis_mm
        ijk = np.rint(ray @ inv[:3, :3].T + inv[:3, 3]).astype(int)
        on_grid = np.all((ijk >= 0) & (ijk < N), axis=1)
        inside = np.zeros(len(ray), dtype=bool)
        inside[on_grid] = mask[tuple(ijk[on_grid].T)]
        to_capsule = steps[np.nonzero(inside)[0][-1]]
        assert to_capsule == pytest.approx(depth, abs=1.0)


def test_custom_levels():
    targets = calyx.posterior_calyces(kidney(), AFFINE, "right", levels={"mid": 0.5})
    assert [t.group for t in targets] == ["mid"]


def test_calyces_independent_of_voxel_order():
    flipped = np.array([
        [-1.0, 0.0, 0.0, 49.0],
        [0.0, -1.0, 0.0, 49.0],
        [0.0, 0.0, 1.0, -50.0],
        [0.0, 0.0, 0.0, 1.0],
    ])
    a = calyx.posterior_calyces(kidney(), AFFINE, "right")
    b = calyx.posterior_calyces(kidney(affine=flipped), flipped, "right")
    # Ray samples that land on a voxel half-boundary round differently in the
    # two storage orders, so targets agree to within one 0.5 mm ray step.
    for ta, tb in zip(a, b):
        np.testing.assert_allclose(ta.point_mm, tb.point_mm, atol=0.5)
        np.testing.assert_allclose(ta.axis_mm, tb.axis_mm, atol=1e-6)


# ---------------------------------------------------------------------------
# Groups and angles


@pytest.mark.parametrize("point, group", [
    ((10.0, 0.0, 30.0), "upper"),
    ((25.0, 0.0, 0.0), "mid"),
    ((10.0, 0.0, -30.0), "lower"),
    ((-4.0, 0.0, 0.0), "pelvis"),
    ((45.0, 0.0, 0.0), "outside"),
])
def test_calyceal_group(point, group):
    assert calyx.calyceal_group(np.array(point), kidney(), AFFINE) == group


def test_infundibular_angle():
    t = calyx.Target(np.zeros(3), "mid", "calyx", np.array([1.0, 0.0, 0.0]))
    assert calyx.infundibular_angle_deg(np.array([50.0, 0.0, 0.0]), t) == pytest.approx(0.0)
    assert calyx.infundibular_angle_deg(np.array([0.0, -50.0, 0.0]), t) == pytest.approx(90.0)
    assert calyx.infundibular_angle_deg(np.array([50.0, -50.0, 0.0]), t) == pytest.approx(45.0)
    stone = calyx.Target(np.zeros(3), "mid", "stone")
    assert math.isnan(calyx.infundibular_angle_deg(np.array([50.0, 0.0, 0.0]), stone))
