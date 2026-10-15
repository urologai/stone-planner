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

CLI:
    stoneplanner-plan <case.nii.gz> <organs.nii.gz> <stones.nii.gz> <out_dir>
        [--sweep-deg 25] [--scope-depth-mm 120] [--side left|right|auto]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


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


def skin_entry_points(body_mask: np.ndarray, organs: np.ndarray, affine: np.ndarray,
                      side: str, params: PlanParams) -> np.ndarray:
    """Return (N, 3) array of candidate entry points in mm on the operative flank."""
    raise NotImplementedError


def target_points(stones: np.ndarray, affine: np.ndarray, params: PlanParams) -> np.ndarray:
    """Return (M, 3) array of target points in mm. Phase 1: stone centroids."""
    raise NotImplementedError


def cast_ray(entry_mm: np.ndarray, target_mm: np.ndarray, organs: np.ndarray,
             kidney_mask: np.ndarray, affine: np.ndarray, params: PlanParams) -> dict:
    """Walk the segment; return collision info, parenchymal entry, nearest organ."""
    raise NotImplementedError


def reachable_fraction(apex_mm: np.ndarray, axis_mm: np.ndarray, stones: np.ndarray,
                       affine: np.ndarray, params: PlanParams) -> float:
    """Fraction of all stone voxels inside the scope cone."""
    raise NotImplementedError


def plan(ct: np.ndarray, organs: np.ndarray, stones: np.ndarray, affine: np.ndarray,
         side: str = "auto", params: PlanParams | None = None) -> list[Tract]:
    """Full pipeline: entry points -> targets -> ray cast -> reach -> rank."""
    raise NotImplementedError


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
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
