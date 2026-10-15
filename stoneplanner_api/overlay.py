"""Tract overlay for the viewer: the top tracts burned into a label volume.

The browser viewer (NiiVue) shows NIfTI volumes, so the top three tracts are
rasterized onto the CT grid as labels 1, 2, 3 (rank), each a line about one
voxel thick, with the target marked by a small ball of the same label. This
draws in every slice view and in 3D without a separate mesh format.
"""

from __future__ import annotations

import nibabel as nib
import numpy as np
from scipy import ndimage

N_DRAWN = 3

# Organ groups shown in the viewer, as values 1..9 of the display map.
DISPLAY_GROUPS = ("kidney", "liver", "spleen", "colon", "lung", "rib", "vertebrae", "hip",
                  "aorta")


def organ_display(organs: nib.Nifti1Image, organ_labels: dict[str, int]) -> nib.Nifti1Image:
    """Compact copy of the organ map for the viewer: one value per organ group.

    Background and body become 0. The viewer's label tables then stay small
    and dense (NiiVue mis-indexes large sparse tables), and the full label map
    stays untouched for planning and saved cases.
    """
    data = np.asanyarray(organs.dataobj)
    lut = np.zeros(max(int(data.max()), max(organ_labels.values())) + 1, dtype=np.uint8)
    for name, value in organ_labels.items():
        for g, group in enumerate(DISPLAY_GROUPS, start=1):
            if name == group or name.startswith(group + "_"):
                lut[value] = g
    out = nib.Nifti1Image(lut[data.astype(np.int64)], organs.affine)
    out.set_qform(organs.affine, code=1)
    out.set_sform(organs.affine, code=1)
    return out


def tract_overlay(records: list[dict], shape: tuple[int, int, int], affine: np.ndarray,
                  step_mm: float = 0.25, target_radius_mm: float = 2.0) -> nib.Nifti1Image:
    """records: tract rows with entry_xyz_mm and target_xyz_mm, in rank order."""
    labels = np.zeros(shape, dtype=np.uint8)
    inv = np.linalg.inv(affine)
    spacing = np.linalg.norm(affine[:3, :3], axis=0)
    for rank, row in reversed(list(enumerate(records[:N_DRAWN], start=1))):
        entry = np.asarray(row["entry_xyz_mm"], dtype=float)
        target = np.asarray(row["target_xyz_mm"], dtype=float)
        n = max(int(np.linalg.norm(target - entry) / step_mm) + 1, 2)
        pts = entry + np.linspace(0.0, 1.0, n)[:, None] * (target - entry)
        ijk = np.rint(pts @ inv[:3, :3].T + inv[:3, 3]).astype(int)
        ijk = ijk[np.all((ijk >= 0) & (ijk < shape), axis=1)]
        line = np.zeros(shape, dtype=bool)
        line[tuple(ijk.T)] = True
        line = ndimage.binary_dilation(line)               # about one voxel each side
        t = np.rint(inv[:3, :3] @ target + inv[:3, 3]).astype(int)
        r = np.ceil(target_radius_mm / spacing).astype(int)
        lo, hi = np.maximum(t - r, 0), np.minimum(t + r + 1, shape)
        if np.all(hi > lo):
            grid = np.indices(hi - lo).reshape(3, -1).T + lo
            d = np.linalg.norm((grid - t) * spacing, axis=1)
            ball = grid[d <= target_radius_mm]
            line[tuple(ball.T)] = True
        # Lower ranks are drawn first, so rank 1 wins where tracts overlap.
        labels[line] = rank
    out = nib.Nifti1Image(labels, affine)
    out.set_qform(affine, code=1)
    out.set_sform(affine, code=1)
    return out
