"""Synthetic CT phantom for tests and the Slicer module self-test. No real imaging.

An 80^3, 1 mm grid in RAS mm. The body is an elliptic cylinder (semi-axes
36 x 26 mm) holding both kidneys, a stone in the right kidney, a spine split
into L1 and T12, rib 11 and 12 arcs inside each flank wall, both hips, and an
optional colon tube. Shapes are defined in mm and voxelized through the
affine, so the same physical phantom can be stored in any voxel order.
Organ labels are the TotalSegmentator IDs in segment.ORGAN_LABELS.
"""

from __future__ import annotations

import numpy as np

from stoneplanner.segment import ORGAN_LABELS as LABELS

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

def mm_grid(affine, shape=(N, N, N)):
    ijk = np.indices(shape).reshape(3, -1).T
    xyz = ijk @ affine[:3, :3].T + affine[:3, 3]
    return [c.reshape(shape) for c in xyz.T]


def body_r(x, y):
    return np.sqrt((x / BODY_A) ** 2 + (y / BODY_B) ** 2)


def make_phantom(affine=AFFINE, colon_xy=(16.0, 12.0), extra_stones=()):
    """Return ct, organs, stones on the grid described by affine.

    extra_stones: iterable of (centre_xyz_mm, radius_mm) added as labels 2, 3, ...
    """
    x, y, z = mm_grid(affine)
    organs = np.zeros((N, N, N), dtype=np.int16)

    body = (body_r(x, y) <= 1.0) & (np.abs(z) <= 38.0)
    ct = np.where(body, 40.0, -1000.0).astype(np.float32)

    for sign, side in ((1.0, "right"), (-1.0, "left")):
        kidney = (((x - sign * STONE_C[0]) ** 2 + (y - STONE_C[1]) ** 2) / KIDNEY_R ** 2
                  + (z / 14.0) ** 2) <= 1.0
        organs[kidney] = LABELS[f"kidney_{side}"]
        arc = (body_r(x, y) >= 0.80) & (body_r(x, y) <= 0.90) & (sign * x > 4.0) & (y < 6.0)
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
