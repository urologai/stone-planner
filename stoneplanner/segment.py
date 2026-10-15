"""Organ and stone segmentation on a de-identified noncontrast CT.

Input:  <case_id>.nii.gz from ingest.
Output: <case_id>_organs.nii.gz   integer label map from TotalSegmentator
        <case_id>_stones.nii.gz   integer label map, one label per stone
        <case_id>_stones.csv      one row per stone: label, volume_mm3, mean_hu,
                                  max_hu, centroid_xyz_mm, laterality, bbox

Organs (TotalSegmentator, roi_subset to keep CPU time down):
    kidney_left, kidney_right, liver, spleen, colon, lung_lower_lobe_left,
    lung_lower_lobe_right, rib_left_*, rib_right_* (ribs 10 to 12 at minimum),
    vertebrae_L1 to L3 and T11 to T12, aorta, and the body/skin mask
    (TotalSegmentator "body" task, or threshold at -500 HU plus largest
    connected component and hole filling if that task is unavailable).

Stones (no model, thresholding only):
    1. Restrict to the kidney masks dilated by ~5 mm so pelvic and proximal
       ureteral stones are not missed.
    2. Candidate voxels: HU > 130 (conservative; tune on the validation set).
    3. Remove anything overlapping the vertebra or rib masks.
    4. Connected components in 3D; drop components < 3 voxels.
    5. Per component, re-threshold at 0.5 * max HU of that component (half-max)
       to correct partial volume averaging, as in Elton et al. 2022
       (PMC10407943). Volume is counted after this step.
    6. Known false positive: renal sinus vascular calcification. Phase 1 just
       reports it; the Slicer segment editor is the correction tool.

The stone CSV is validated against manual volumes in scripts/validate.py.
Target: automated volume within 10% of manual on the 20-case set.

CLI:
    stoneplanner-segment <case.nii.gz> <out_dir> [--device cpu|cuda|mps]

MPS (Apple Silicon) support in TotalSegmentator is unverified. Default to cpu.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

ORGAN_ROI_SUBSET: list[str] = [
    # Fill from TotalSegmentator's class list for the installed version.
]

STONE_HU_THRESHOLD = 130
KIDNEY_DILATION_MM = 5.0
MIN_STONE_VOXELS = 3


def run_totalsegmentator(nifti_path: Path, out_dir: Path, device: str = "cpu") -> Path:
    """Run TotalSegmentator with ORGAN_ROI_SUBSET. Return path to organ label map."""
    raise NotImplementedError


def body_mask(ct_hu: np.ndarray) -> np.ndarray:
    """Binary body mask: threshold, largest component, fill holes."""
    raise NotImplementedError


def segment_stones(ct_hu: np.ndarray, organs: np.ndarray, spacing_mm: tuple[float, float, float]):
    """Return (stone_label_map, stone_table) per the module docstring."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("nifti", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda", "mps"])
    args = parser.parse_args(argv)
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
