"""Posterior calyx proxy on noncontrast CT. Phase 2, PLAN.md option 2.

Noncontrast CT does not show the collecting system. This module infers where
the posterior calyces are likely to be from the kidney mask alone, so the
planner can aim at a calyx instead of at the stone centroid. It is a
heuristic, not segmentation.

Input:  one kidney mask (boolean, on the CT grid) and the NIfTI affine.
Output: Target objects in RAS mm, each with a calyceal group label and, for
        calyx targets, the calyx axis used for the infundibular angle.

Kidney frame:
  - Long axis: first principal axis of the kidney voxels, pointing superior.
  - Hilum: the renal sinus is the kidney mask closed with a ball of
    sinus_closing_mm (default 15), minus the kidney mask, eroded by one
    voxel, largest component. The closing fills the hilar notch the way a
    convex hull would, at a fraction of the cost on a full-size kidney. The
    hilum direction is from the kidney centroid to the sinus centroid,
    perpendicular to the long axis. If no sinus is found, or it sits on the
    lateral side, the hilum is taken as medial (toward the midline).
  - Lateral: opposite the hilum. The kidney's coronal plane holds the long
    axis and the lateral direction. Posterior is the normal to that plane
    on the patient's posterior side.

Posterior calyces (one per level):
  - Levels are fractions of the kidney's extent along the long axis:
    lower 0.2, mid 0.5, upper 0.8 by default.
  - At each level, a ray leaves the collecting-system axis (the line through
    the sinus centroid parallel to the long axis) in the direction rotated
    from lateral toward posterior by posterior_calyx_deg (default 30, after
    Brodel's line). The calyx axis is that direction.
  - The ray's last point inside the kidney is the capsule. The target is
    calyx_depth_mm back along the ray from the capsule (default 15 mm, a
    typical parenchymal thickness), or the ray origin if the kidney is
    thinner than that.
  - Ideal access runs down the calyx axis, so the infundibular angle of a
    tract is the angle between the tract and the calyx axis reversed.

Calyceal group of any point: upper, mid or lower third of the kidney along
its long axis if the point is in the kidney mask; pelvis if inside the
convex hull but outside the mask; outside otherwise. Phase 1 used RAS z
for the thirds; the long axis is what the calyx levels use, so both now
agree.

Defaults are placeholders until surgeons set them. They are passed in from
geometry.PlanParams and written to the plan JSON.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

DEFAULT_LEVELS = {"lower": 0.2, "mid": 0.5, "upper": 0.8}
GROUPS = ("upper", "mid", "lower", "pelvis")


@dataclass
class Target:
    point_mm: np.ndarray
    group: str                       # upper | mid | lower | pelvis | outside
    source: str                      # stone | calyx | manual
    axis_mm: np.ndarray | None = None  # calyx axis, pointing out of the kidney


@dataclass
class KidneyFrame:
    centroid_mm: np.ndarray
    long_axis: np.ndarray            # unit, superior
    lateral: np.ndarray              # unit, away from the hilum
    posterior: np.ndarray            # unit, posterior side of the kidney's coronal plane
    s_min: float                     # extent along the long axis, relative to centroid
    s_max: float
    axis_origin_mm: np.ndarray       # a point on the collecting-system axis
    sinus_found: bool


def _voxel_to_mm(ijk, affine):
    return np.asarray(ijk, dtype=float) @ affine[:3, :3].T + affine[:3, 3]


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _lookup(mask, pts_mm, inv_affine):
    ijk = np.rint(pts_mm @ inv_affine[:3, :3].T + inv_affine[:3, 3]).astype(np.int64)
    inside = np.all((ijk >= 0) & (ijk < np.array(mask.shape)), axis=1)
    out = np.zeros(len(pts_mm), dtype=bool)
    out[inside] = mask[ijk[inside, 0], ijk[inside, 1], ijk[inside, 2]]
    return out


def closing_mm(mask: np.ndarray, affine: np.ndarray, radius_mm: float) -> np.ndarray:
    """Morphological closing with a ball of radius_mm, via two distance transforms."""
    spacing = np.linalg.norm(affine[:3, :3], axis=0)
    ijk = np.argwhere(mask)
    out = np.zeros(mask.shape, dtype=bool)
    if len(ijk) == 0:
        return out
    pad = np.ceil(radius_mm / spacing).astype(int) + 2
    lo = ijk.min(axis=0) - pad
    hi = ijk.max(axis=0) + pad + 1
    sub = np.zeros(hi - lo, dtype=bool)
    sub[tuple((ijk - lo).T)] = True
    dilated = ndimage.distance_transform_edt(~sub, sampling=spacing) <= radius_mm
    closed = ndimage.distance_transform_edt(dilated, sampling=spacing) > radius_mm
    # Write back the part of the padded box that lies on the grid.
    src_lo = np.maximum(-lo, 0)
    dst_lo = np.maximum(lo, 0)
    dst_hi = np.minimum(hi, mask.shape)
    src = tuple(slice(a, a + (c - b)) for a, b, c in zip(src_lo, dst_lo, dst_hi))
    dst = tuple(slice(b, c) for b, c in zip(dst_lo, dst_hi))
    out[dst] = closed[src]
    return out | mask


def sinus_mask(kidney: np.ndarray, affine: np.ndarray, closing_radius_mm: float = 15.0):
    """Renal sinus proxy: closed kidney minus kidney, eroded once, largest component."""
    gap = closing_mm(kidney, affine, closing_radius_mm) & ~kidney
    gap = ndimage.binary_erosion(gap, structure=np.ones((3, 3, 3), dtype=bool))
    lab, n = ndimage.label(gap)
    if n == 0:
        return gap
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == int(np.argmax(sizes))


def kidney_frame(kidney: np.ndarray, affine: np.ndarray, side: str,
                 sinus_closing_mm: float = 15.0) -> KidneyFrame:
    pts = _voxel_to_mm(np.argwhere(kidney), affine)
    if len(pts) < 10:
        raise ValueError("kidney mask is empty or too small for a kidney frame")
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    u = vt[0] if vt[0][2] >= 0 else -vt[0]
    s = (pts - c) @ u

    def perp(v):
        return v - (v @ u) * u

    medial = perp(np.array([-1.0 if side == "right" else 1.0, 0.0, 0.0]))
    sinus = sinus_mask(kidney, affine, sinus_closing_mm)
    sinus_found = bool(sinus.sum() >= 10)
    origin = c
    hilum = _unit(medial)
    if sinus_found:
        sc = _voxel_to_mm(np.argwhere(sinus), affine).mean(axis=0)
        h = perp(sc - c)
        # Trust the sinus only if it sits off-centre and on the medial side.
        if np.linalg.norm(h) >= 1.0 and h @ medial > 0:
            hilum, origin = _unit(h), sc
        else:
            sinus_found = False
    lateral = -hilum
    n = _unit(np.cross(u, lateral))
    if n[1] > 0:  # RAS: posterior is -y
        n = -n
    return KidneyFrame(c, u, lateral, n, float(s.min()), float(s.max()), origin, sinus_found)


def posterior_calyces(kidney: np.ndarray, affine: np.ndarray, side: str,
                      angle_deg: float = 30.0, depth_mm: float = 15.0,
                      levels: dict[str, float] | None = None,
                      step_mm: float = 0.5, sinus_closing_mm: float = 15.0,
                      frame: KidneyFrame | None = None) -> list[Target]:
    """One posterior calyx target per level, with its calyx axis."""
    levels = levels or DEFAULT_LEVELS
    frame = frame or kidney_frame(kidney, affine, side, sinus_closing_mm)
    inv = np.linalg.inv(affine)
    theta = np.radians(angle_deg)
    axis = _unit(np.cos(theta) * frame.lateral + np.sin(theta) * frame.posterior)
    reach = float(frame.s_max - frame.s_min) + 50.0
    t = np.arange(0.0, reach, step_mm)
    targets = []
    for group, f in levels.items():
        s = frame.s_min + f * (frame.s_max - frame.s_min)
        base = frame.axis_origin_mm
        s_base = (base - frame.centroid_mm) @ frame.long_axis
        origin = base + (s - s_base) * frame.long_axis
        ray = origin + t[:, None] * axis
        inside = np.nonzero(_lookup(kidney, ray, inv))[0]
        if inside.size == 0:
            continue
        capsule = ray[inside[-1]]
        back = min(depth_mm, float(t[inside[-1]]))
        targets.append(Target(capsule - back * axis, group, "calyx", axis.copy()))
    return targets


def calyceal_group(point_mm: np.ndarray, kidney: np.ndarray, affine: np.ndarray,
                   frame: KidneyFrame | None = None) -> str:
    """upper / mid / lower third along the long axis, pelvis in the hull, else outside."""
    from scipy.spatial import ConvexHull, Delaunay

    ijk = np.argwhere(kidney)
    if len(ijk) < 10:
        return "outside"
    point_mm = np.asarray(point_mm, dtype=float)
    inv = np.linalg.inv(affine)
    if not _lookup(kidney, point_mm[None, :], inv)[0]:
        pts = _voxel_to_mm(ijk, affine)
        hull = Delaunay(pts[ConvexHull(pts).vertices])
        return "pelvis" if hull.find_simplex(point_mm[None, :])[0] >= 0 else "outside"
    if frame is None:
        pts = _voxel_to_mm(ijk, affine)
        c = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
        u = vt[0] if vt[0][2] >= 0 else -vt[0]
        s = (pts - c) @ u
        s_min, s_max, c_mm = float(s.min()), float(s.max()), c
    else:
        u, s_min, s_max, c_mm = frame.long_axis, frame.s_min, frame.s_max, frame.centroid_mm
    f = ((point_mm - c_mm) @ u - s_min) / max(s_max - s_min, 1e-9)
    if f > 2.0 / 3.0:
        return "upper"
    if f < 1.0 / 3.0:
        return "lower"
    return "mid"


def infundibular_angle_deg(entry_mm: np.ndarray, target: Target) -> float:
    """Angle between the tract (entry to target) and the calyx axis reversed.

    0 means the tract runs straight down the calyx axis. NaN when the target
    has no calyx axis (stone and manual targets).
    """
    if target.axis_mm is None:
        return float("nan")
    tract = _unit(np.asarray(target.point_mm, float) - np.asarray(entry_mm, float))
    cos = float(np.clip(tract @ -_unit(target.axis_mm), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos)))
