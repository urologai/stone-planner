"""Candidate tract generation and scoring. The Phase 1 contribution.

Input:  CT NIfTI, organ label map, stone label map, metadata JSON.
Output: <case_id>_tracts.csv, one row per candidate tract, ranked:
            rank, entry_xyz_mm, target_xyz_mm, reachable_fraction,
            tract_length_mm, parenchymal_depth_mm, nearest_organ, nearest_organ_mm,
            supracostal (bool), collision (bool), collision_organ
        <case_id>_tracts.json with the same plus the parameters used.

Coordinate convention: everything in physical mm, RAS, matching the NIfTI
affine. Never work in voxel indices outside of mask lookups.

Algorithm (Phase 1, rigid scope, stone-centroid target):

  1. Skin mesh. Marching cubes on the body mask (skimage.measure.marching_cubes),
     transform vertices to mm. Keep only vertices on the posterior and
     posterolateral flank of the operative side: posterior to the mid-axillary
     line and between the iliac crest and two interspaces above the 12th rib.
     Define these from the vertebra and rib masks, not from hard-coded mm.
     Subsample to ~2000 to 5000 candidate entry points.

  2. Targets. Phase 1 uses the centroid of each stone with volume above a
     threshold (default: the largest stone, plus any stone > 100 mm3). Phase 2
     replaces this with inferred posterior calyces. Keep the target as an
     injectable list of points so the swap is trivial.

  3. Ray cast. For each (entry, target) pair, sample the segment every 0.5 mm.
     Hard fail (collision=True) if any sample lands in colon, spleen, liver,
     lung, or rib. Record the nearest organ and its distance for the clear
     tracts (distance transform on each organ mask, sampled along the ray).
     Supracostal = the entry point is cranial to the 12th rib's inferior edge
     at that mediolateral position.

  4. Parenchymal entry. First sample along the ray that lies inside the kidney
     mask. parenchymal_depth_mm = distance from that point to the target.
     tract_length_mm = entry to target.

  5. Reach. Model the rigid nephroscope as a cone with apex at the parenchymal
     entry point, axis along the tract, half-angle SWEEP_DEG (default 25),
     depth limit SCOPE_DEPTH_MM (default 120). reachable_fraction = stone
     voxels inside the cone / total stone voxels. Count across all stones,
     not only the targeted one, so a tract that catches two stones scores
     higher.

  6. Rank by reachable_fraction among collision-free tracts, tie-break on
     nearest_organ_mm (larger is better) then tract_length_mm (shorter is
     better). Return the top N (default 10, display top 3).

Parameters live in a dataclass so the Slicer module and the validation
script can override them and so they appear in the output JSON.

Known limitations to carry into the paper: supine CT vs prone surgery,
respiratory phase, no vessels on NCCT, no collecting system on NCCT.

Implementation notes (how the steps above are made concrete):

  - Organ label values come from PlanParams.organ_labels, keyed by
    TotalSegmentator class name. The default is segment.ORGAN_LABELS: the
    "total" task class map of TotalSegmentator 2.18 plus segment.BODY_LABEL.
    Organ groups match by name prefix, so the collision organ "rib" covers
    rib_left_1 .. rib_right_12 and "lung" covers every lung lobe.
  - Body mask: if organ_labels has a "body" entry and that label occurs in the
    organ map, every nonzero organ voxel is body. Otherwise use
    segment.body_mask on the CT at body_hu_threshold, unioned with the organ
    voxels.
  - Midline: median mediolateral position of the vertebra voxels.
    Mid-axillary line: the anteroposterior position of the most lateral
    operative-side rib voxels (the widest point of the rib cage).
    Iliac crest: top of hip_<side> if labelled, else the inferior edge of the
    lowest labelled vertebra. Upper bound: top of rib 12 on the posterior
    flank plus two rib pitches (rib 11 minus rib 12, else mean vertebral
    pitch). These are global planes, not per-slice curves.
  - Operative side: "auto" picks the side holding more stone volume. Only
    stones on the operative side are targeted and counted in the reachable
    fraction denominator; a contralateral stone is not reachable through any
    tract on this side and would only scale every score down.
  - Nearest organ distance is the distance from the ray samples to the
    nearest boundary voxel centre of each collision organ, computed with a
    KD-tree on mm coordinates. This equals sampling a Euclidean distance
    transform along the ray, without allocating one per organ.
  - A tract that never enters the kidney mask gets parenchymal_depth_mm NaN
    and the cone apex at the target.
  - plan() returns collision-free tracts first, then colliding ones, each
    group in the rank order of step 6, truncated to top_n.

CLI:
    stoneplanner-plan <case.nii.gz> <organs.nii.gz> <stones.nii.gz> <out_dir>
        [--sweep-deg 25] [--scope-depth-mm 120] [--side left|right|auto]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from stoneplanner import segment


@dataclass
class PlanParams:
    sweep_deg: float = 25.0
    scope_depth_mm: float = 120.0
    ray_step_mm: float = 0.5
    n_entry_points: int = 3000
    min_target_stone_mm3: float = 100.0
    collision_organs: list[str] = field(
        default_factory=lambda: ["colon", "spleen", "liver", "lung", "rib"]
    )
    top_n: int = 10
    organ_labels: dict[str, int] = field(default_factory=lambda: dict(segment.ORGAN_LABELS))
    body_hu_threshold: float = -500.0
    midaxillary_band_mm: float = 5.0
    rib_edge_bin_mm: float = 2.0


@dataclass
class Tract:
    entry_mm: np.ndarray
    target_mm: np.ndarray
    reachable_fraction: float
    tract_length_mm: float
    parenchymal_depth_mm: float
    nearest_organ: str
    nearest_organ_mm: float
    supracostal: bool
    collision: bool
    collision_organ: str | None


# ---------------------------------------------------------------------------
# Coordinate and label helpers


def _in_group(name: str, group: str) -> bool:
    return name == group or name.startswith(group + "_")


def _group_ids(params: PlanParams, group: str) -> list[int]:
    return [v for k, v in params.organ_labels.items() if _in_group(k, group)]


def _voxel_to_mm(ijk: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return np.asarray(ijk, dtype=float) @ affine[:3, :3].T + affine[:3, 3]


def _mask_points_mm(mask: np.ndarray, affine: np.ndarray) -> np.ndarray:
    """(N, 3) mm coordinates of the voxel centres where mask is true."""
    return _voxel_to_mm(np.argwhere(mask), affine)


def _label_points_mm(organs: np.ndarray, ids: list[int], affine: np.ndarray) -> np.ndarray:
    if not ids:
        return np.empty((0, 3))
    return _mask_points_mm(np.isin(organs, ids), affine)


def _lookup(volume: np.ndarray, pts_mm: np.ndarray, inv_affine: np.ndarray) -> np.ndarray:
    """Nearest-voxel values of volume at mm points; 0 outside the grid."""
    ijk = np.rint(pts_mm @ inv_affine[:3, :3].T + inv_affine[:3, 3]).astype(np.int64)
    inside = np.all((ijk >= 0) & (ijk < np.array(volume.shape)), axis=1)
    out = np.zeros(len(pts_mm), dtype=volume.dtype)
    out[inside] = volume[ijk[inside, 0], ijk[inside, 1], ijk[inside, 2]]
    return out


def _side_sign(side: str) -> float:
    """RAS: +x is patient right."""
    if side == "right":
        return 1.0
    if side == "left":
        return -1.0
    raise ValueError(f"side must be 'left' or 'right', got {side!r}")


def _midline_x(organs: np.ndarray, affine: np.ndarray, params: PlanParams) -> float:
    pts = _label_points_mm(organs, _group_ids(params, "vertebrae"), affine)
    if len(pts) == 0:
        raise ValueError("no vertebra voxels in the organ map; cannot place the midline")
    return float(np.median(pts[:, 0]))


def _body_mask(ct: np.ndarray, organs: np.ndarray, affine: np.ndarray,
               params: PlanParams) -> np.ndarray:
    body_label = params.organ_labels.get("body")
    if body_label is not None and np.any(organs == body_label):
        return organs > 0
    mask = segment.body_mask(ct, tuple(np.linalg.norm(affine[:3, :3], axis=0)),
                             int(np.argmax(np.abs(affine[2, :3]))), params.body_hu_threshold)
    return mask | (organs > 0)


# ---------------------------------------------------------------------------
# Step 1: skin entry points


@dataclass
class _Flank:
    midline_x: float
    midaxillary_y: float
    z_low: float
    z_high: float


def _flank_bounds(organs: np.ndarray, affine: np.ndarray, side: str,
                  params: PlanParams) -> _Flank:
    sign = _side_sign(side)
    midline = _midline_x(organs, affine, params)

    ribs = _label_points_mm(organs, _group_ids(params, f"rib_{side}"), affine)
    if len(ribs) == 0:
        raise ValueError(f"no rib_{side} voxels in the organ map")
    lateral = sign * (ribs[:, 0] - midline)
    band = lateral >= lateral.max() - params.midaxillary_band_mm
    midaxillary_y = float(np.median(ribs[band, 1]))

    def posterior_flank(pts: np.ndarray) -> np.ndarray:
        keep = (pts[:, 1] < midaxillary_y) & (sign * (pts[:, 0] - midline) > 0)
        return pts[keep] if keep.any() else pts

    rib12 = posterior_flank(
        _label_points_mm(organs, _group_ids(params, f"rib_{side}_12"), affine))
    if len(rib12) == 0:
        raise ValueError(f"no rib_{side}_12 voxels in the organ map")
    rib11 = posterior_flank(
        _label_points_mm(organs, _group_ids(params, f"rib_{side}_11"), affine))

    vert_ids = _group_ids(params, "vertebrae")
    if len(rib11):
        pitch = float(np.median(rib11[:, 2]) - np.median(rib12[:, 2]))
    else:
        present = [i for i in vert_ids if np.any(organs == i)]
        verts = _label_points_mm(organs, present, affine)
        pitch = float(np.ptp(verts[:, 2])) / max(len(present), 1)
    z_high = float(rib12[:, 2].max()) + 2.0 * abs(pitch)

    hip = _label_points_mm(organs, _group_ids(params, f"hip_{side}"), affine)
    if len(hip):
        z_low = float(hip[:, 2].max())
    else:
        z_low = float(_label_points_mm(organs, vert_ids, affine)[:, 2].min())

    return _Flank(midline, midaxillary_y, z_low, z_high)


def _skin_vertices_mm(body_mask: np.ndarray, affine: np.ndarray) -> np.ndarray:
    from skimage.measure import marching_cubes

    # Pad so the surface closes where the body is cut by the scan edge.
    padded = np.pad(body_mask.astype(np.uint8), 1)
    verts, _, _, _ = marching_cubes(padded, level=0.5)
    return _voxel_to_mm(verts - 1.0, affine)


def skin_entry_points(body_mask: np.ndarray, organs: np.ndarray, affine: np.ndarray,
                      side: str, params: PlanParams) -> np.ndarray:
    """Return (N, 3) array of candidate entry points in mm on the operative flank."""
    sign = _side_sign(side)
    flank = _flank_bounds(organs, affine, side, params)
    verts = _skin_vertices_mm(body_mask, affine)
    keep = (
        (sign * (verts[:, 0] - flank.midline_x) > 0)
        & (verts[:, 1] < flank.midaxillary_y)
        & (verts[:, 2] >= flank.z_low)
        & (verts[:, 2] <= flank.z_high)
    )
    pts = verts[keep]
    if len(pts) > params.n_entry_points:
        # Marching cubes emits vertices slice by slice, so an even stride over
        # the list spreads the sample across the flank.
        idx = np.linspace(0, len(pts) - 1, params.n_entry_points).round().astype(int)
        pts = pts[np.unique(idx)]
    return pts


# ---------------------------------------------------------------------------
# Step 2: targets


def _stone_table(stones: np.ndarray, affine: np.ndarray):
    """Return (labels, volumes_mm3, centroids_mm) for every nonzero stone label."""
    idx = np.nonzero(stones)
    lab = stones[idx].astype(np.int64)
    if lab.size == 0:
        return np.empty(0, dtype=np.int64), np.empty(0), np.empty((0, 3))
    counts = np.bincount(lab)
    labels = np.nonzero(counts)[0]
    centroid_vox = np.stack(
        [np.bincount(lab, weights=idx[a])[labels] / counts[labels] for a in range(3)], axis=1)
    voxel_mm3 = abs(np.linalg.det(affine[:3, :3]))
    return labels, counts[labels] * voxel_mm3, _voxel_to_mm(centroid_vox, affine)


def target_points(stones: np.ndarray, affine: np.ndarray, params: PlanParams) -> np.ndarray:
    """Return (M, 3) array of target points in mm. Phase 1: stone centroids."""
    _, volumes, centroids = _stone_table(stones, affine)
    if len(volumes) == 0:
        return np.empty((0, 3))
    order = np.argsort(-volumes, kind="stable")
    keep = [order[0]] + [i for i in order[1:] if volumes[i] > params.min_target_stone_mm3]
    return centroids[keep]


# ---------------------------------------------------------------------------
# Step 3 and 4: ray cast


class _Scene:
    """Per-case lookups shared by every ray: label-to-organ table and KD-trees."""

    def __init__(self, organs: np.ndarray, affine: np.ndarray, params: PlanParams):
        self.inv_affine = np.linalg.inv(affine)
        self.groups = list(params.collision_organs)
        max_label = max([int(organs.max()) if organs.size else 0]
                        + list(params.organ_labels.values()))
        self.group_of_label = np.full(max_label + 1, -1, dtype=np.int16)
        for name, value in params.organ_labels.items():
            for g, group in enumerate(self.groups):
                if _in_group(name, group):
                    self.group_of_label[value] = g
                    break
        self.trees: dict[str, cKDTree] = {}
        group_vol = (self.group_of_label[organs] + 1).astype(np.int16)
        for g, box in enumerate(ndimage.find_objects(group_vol, max_label=len(self.groups))):
            if box is None:
                continue
            mask = group_vol[box] == g + 1
            shell = mask & ~ndimage.binary_erosion(mask)
            ijk = np.argwhere(shell) + np.array([s.start for s in box])
            self.trees[self.groups[g]] = cKDTree(_voxel_to_mm(ijk, affine))


def cast_ray(entry_mm: np.ndarray, target_mm: np.ndarray, organs: np.ndarray,
             kidney_mask: np.ndarray, affine: np.ndarray, params: PlanParams,
             scene: _Scene | None = None) -> dict:
    """Walk the segment; return collision info, parenchymal entry, nearest organ.

    Keys: collision, collision_organ, parenchymal_entry_mm, parenchymal_depth_mm,
    tract_length_mm, nearest_organ, nearest_organ_mm. Pass a prebuilt scene
    when casting many rays through the same case.
    """
    if scene is None:
        scene = _Scene(organs, affine, params)
    entry_mm = np.asarray(entry_mm, dtype=float)
    target_mm = np.asarray(target_mm, dtype=float)
    length = float(np.linalg.norm(target_mm - entry_mm))
    n = max(math.ceil(length / params.ray_step_mm) + 1, 2)
    samples = entry_mm + np.linspace(0.0, 1.0, n)[:, None] * (target_mm - entry_mm)

    labels = _lookup(organs, samples, scene.inv_affine).astype(np.int64)
    labels = np.clip(labels, 0, len(scene.group_of_label) - 1)
    groups = scene.group_of_label[labels]
    hits = np.nonzero(groups >= 0)[0]
    collision = hits.size > 0
    collision_organ = scene.groups[groups[hits[0]]] if collision else None

    in_kidney = np.nonzero(_lookup(kidney_mask, samples, scene.inv_affine))[0]
    if in_kidney.size:
        parenchymal_entry = samples[in_kidney[0]]
        depth = float(np.linalg.norm(target_mm - parenchymal_entry))
    else:
        parenchymal_entry = None
        depth = float("nan")

    if collision:
        nearest_organ, nearest_mm = collision_organ, 0.0
    else:
        nearest_organ, nearest_mm = "", float("inf")
        for name, tree in scene.trees.items():
            d = float(tree.query(samples)[0].min())
            if d < nearest_mm:
                nearest_organ, nearest_mm = name, d

    return {
        "collision": bool(collision),
        "collision_organ": collision_organ,
        "parenchymal_entry_mm": parenchymal_entry,
        "parenchymal_depth_mm": depth,
        "tract_length_mm": length,
        "nearest_organ": nearest_organ,
        "nearest_organ_mm": nearest_mm,
    }


class _Rib12Edge:
    """Inferior edge height of rib 12 as a function of mediolateral position."""

    def __init__(self, organs: np.ndarray, affine: np.ndarray, side: str,
                 params: PlanParams):
        pts = _label_points_mm(organs, _group_ids(params, f"rib_{side}_12"), affine)
        if len(pts) == 0:
            raise ValueError(f"no rib_{side}_12 voxels in the organ map")
        bins = np.floor(pts[:, 0] / params.rib_edge_bin_mm).astype(np.int64)
        uniq, inv = np.unique(bins, return_inverse=True)
        low = np.full(len(uniq), np.inf)
        np.minimum.at(low, inv, pts[:, 2])
        self.x = (uniq + 0.5) * params.rib_edge_bin_mm
        self.z = low

    def __call__(self, x_mm: float) -> float:
        # np.interp holds the end values beyond the rib's mediolateral extent.
        return float(np.interp(x_mm, self.x, self.z))


def supracostal(entry_mm: np.ndarray, organs: np.ndarray, affine: np.ndarray,
                side: str, params: PlanParams) -> bool:
    """True if the entry is cranial to rib 12's inferior edge at its x position."""
    edge = _Rib12Edge(organs, affine, side, params)
    return bool(entry_mm[2] > edge(entry_mm[0]))


# ---------------------------------------------------------------------------
# Step 5: reach


def _cone_fraction(apex_mm: np.ndarray, axis_mm: np.ndarray, stone_pts_mm: np.ndarray,
                   params: PlanParams) -> float:
    if len(stone_pts_mm) == 0:
        return 0.0
    norm = np.linalg.norm(axis_mm)
    if norm == 0:
        return 0.0
    v = stone_pts_mm - apex_mm
    along = v @ (axis_mm / norm)
    dist = np.linalg.norm(v, axis=1)
    inside = (
        (along >= 0.0)
        & (along <= params.scope_depth_mm)
        & (along >= dist * math.cos(math.radians(params.sweep_deg)) - 1e-9)
    )
    return float(inside.mean())


def reachable_fraction(apex_mm: np.ndarray, axis_mm: np.ndarray, stones: np.ndarray,
                       affine: np.ndarray, params: PlanParams) -> float:
    """Fraction of all stone voxels inside the scope cone."""
    return _cone_fraction(np.asarray(apex_mm, dtype=float), np.asarray(axis_mm, dtype=float),
                          _mask_points_mm(stones > 0, affine), params)


# ---------------------------------------------------------------------------
# Step 6: full pipeline


def _resolve_side(stones: np.ndarray, organs: np.ndarray, affine: np.ndarray,
                  params: PlanParams) -> str:
    _, volumes, centroids = _stone_table(stones, affine)
    if len(volumes) == 0:
        raise ValueError("side='auto' needs at least one stone")
    midline = _midline_x(organs, affine, params)
    right = volumes[centroids[:, 0] > midline].sum()
    left = volumes[centroids[:, 0] <= midline].sum()
    return "right" if right >= left else "left"


def _operative_side_stones(stones: np.ndarray, organs: np.ndarray, affine: np.ndarray,
                           side: str, params: PlanParams) -> np.ndarray:
    labels, _, centroids = _stone_table(stones, affine)
    if len(labels) == 0:
        return stones
    sign = _side_sign(side)
    midline = _midline_x(organs, affine, params)
    keep = labels[sign * (centroids[:, 0] - midline) > 0]
    return np.where(np.isin(stones, keep), stones, 0)


def rank_tracts(tracts: list[Tract], top_n: int) -> list[Tract]:
    """Collision-free first, then by reach, nearest organ distance, tract length."""
    def key(t: Tract):
        return (t.collision, -t.reachable_fraction, -t.nearest_organ_mm, t.tract_length_mm)
    return sorted(tracts, key=key)[:top_n]


def plan(ct: np.ndarray, organs: np.ndarray, stones: np.ndarray, affine: np.ndarray,
         side: str = "auto", params: PlanParams | None = None,
         targets_mm: np.ndarray | None = None) -> list[Tract]:
    """Full pipeline: entry points -> targets -> ray cast -> reach -> rank.

    targets_mm overrides the stone-centroid targets (Phase 2 calyx proxy hook).
    """
    params = params or PlanParams()
    if not (ct.shape == organs.shape == stones.shape):
        raise ValueError("ct, organs and stones must share one grid")
    if side == "auto":
        side = _resolve_side(stones, organs, affine, params)
    _side_sign(side)

    stones = _operative_side_stones(stones, organs, affine, side, params)
    if targets_mm is None:
        targets_mm = target_points(stones, affine, params)
    targets_mm = np.atleast_2d(np.asarray(targets_mm, dtype=float))
    if targets_mm.size == 0:
        return []

    body = _body_mask(ct, organs, affine, params)
    entries = skin_entry_points(body, organs, affine, side, params)
    kidney_ids = _group_ids(params, f"kidney_{side}")
    kidney = np.isin(organs, kidney_ids)
    scene = _Scene(organs, affine, params)
    rib_edge = _Rib12Edge(organs, affine, side, params)
    stone_pts = _mask_points_mm(stones > 0, affine)

    tracts: list[Tract] = []
    for target in targets_mm:
        for entry in entries:
            ray = cast_ray(entry, target, organs, kidney, affine, params, scene=scene)
            apex = ray["parenchymal_entry_mm"]
            if apex is None:
                apex = target
            tracts.append(Tract(
                entry_mm=entry.copy(),
                target_mm=target.copy(),
                reachable_fraction=_cone_fraction(apex, target - entry, stone_pts, params),
                tract_length_mm=ray["tract_length_mm"],
                parenchymal_depth_mm=ray["parenchymal_depth_mm"],
                nearest_organ=ray["nearest_organ"],
                nearest_organ_mm=ray["nearest_organ_mm"],
                supracostal=bool(entry[2] > rib_edge(entry[0])),
                collision=ray["collision"],
                collision_organ=ray["collision_organ"],
            ))
    return rank_tracts(tracts, params.top_n)


# ---------------------------------------------------------------------------
# Output


def _finite_or_none(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def tract_records(tracts: list[Tract]) -> list[dict]:
    """JSON-ready rows in rank order. Non-finite numbers become None."""
    rows = []
    for rank, t in enumerate(tracts, start=1):
        rows.append({
            "rank": rank,
            "entry_xyz_mm": [round(float(v), 2) for v in t.entry_mm],
            "target_xyz_mm": [round(float(v), 2) for v in t.target_mm],
            "reachable_fraction": round(t.reachable_fraction, 4),
            "tract_length_mm": _finite_or_none(round(t.tract_length_mm, 2)),
            "parenchymal_depth_mm": _finite_or_none(round(t.parenchymal_depth_mm, 2)),
            "nearest_organ": t.nearest_organ,
            "nearest_organ_mm": _finite_or_none(round(t.nearest_organ_mm, 2)),
            "supracostal": t.supracostal,
            "collision": t.collision,
            "collision_organ": t.collision_organ,
        })
    return rows


def write_outputs(tracts: list[Tract], out_dir: Path, case_id: str, side: str,
                  params: PlanParams) -> tuple[Path, Path]:
    """Write <case_id>_tracts.csv and <case_id>_tracts.json. Return both paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = tract_records(tracts)
    csv_path = out_dir / f"{case_id}_tracts.csv"
    columns = ["rank", "entry_xyz_mm", "target_xyz_mm", "reachable_fraction",
               "tract_length_mm", "parenchymal_depth_mm", "nearest_organ",
               "nearest_organ_mm", "supracostal", "collision", "collision_organ"]
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key in ("entry_xyz_mm", "target_xyz_mm"):
                flat[key] = " ".join(f"{v:.2f}" for v in row[key])
            flat["collision_organ"] = row["collision_organ"] or ""
            writer.writerow({k: "" if flat[k] is None else flat[k] for k in columns})
    json_path = out_dir / f"{case_id}_tracts.json"
    payload = {"case_id": case_id, "side": side, "params": asdict(params), "tracts": rows}
    json_path.write_text(json.dumps(payload, indent=2))
    return csv_path, json_path


def _case_id(path: Path) -> str:
    name = path.name
    for suffix in (".nii.gz", ".nii"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ct", type=Path)
    parser.add_argument("organs", type=Path)
    parser.add_argument("stones", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--sweep-deg", type=float, default=25.0)
    parser.add_argument("--scope-depth-mm", type=float, default=120.0)
    parser.add_argument("--side", default="auto", choices=["left", "right", "auto"])
    args = parser.parse_args(argv)

    import nibabel as nib

    ct_img = nib.load(str(args.ct))
    organ_img = nib.load(str(args.organs))
    stone_img = nib.load(str(args.stones))
    affine = ct_img.affine
    for img, name in ((organ_img, "organs"), (stone_img, "stones")):
        if img.shape != ct_img.shape or not np.allclose(img.affine, affine, atol=1e-3):
            parser.error(f"{name} grid does not match the CT grid")

    params = PlanParams(sweep_deg=args.sweep_deg, scope_depth_mm=args.scope_depth_mm)
    ct = np.asarray(ct_img.get_fdata(dtype=np.float32))
    organs = np.asarray(organ_img.dataobj).astype(np.int32)
    stones = np.asarray(stone_img.dataobj).astype(np.int32)
    side = args.side
    if side == "auto":
        side = _resolve_side(stones, organs, affine, params)
    tracts = plan(ct, organs, stones, affine, side=side, params=params)
    csv_path, json_path = write_outputs(tracts, args.out_dir, _case_id(args.ct), side, params)
    print(f"{len(tracts)} tracts, side {side}: {csv_path}, {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
