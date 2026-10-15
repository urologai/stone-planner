"""Segmentation correction log. Phase 2.

PLAN.md asks for the frequency of manual correction in the Slicer Segment
Editor; it goes in the paper. This module compares the automatic label maps
with the ones the planner finally used and writes what changed.

Input:  the automatic and the corrected organ label maps, the automatic and
        the corrected stone label maps, all on one grid, and the affine.
Output: <case_id>_corrections.json:
            case_id, voxel_mm3, any_edit (bool),
            organs: one entry per organ label present before or after:
                name, before_mm3, after_mm3, added_mm3, removed_mm3, edited
            organs_edited: names with edited = true
            stones: n_before, n_after, before_mm3, after_mm3, added_mm3,
                removed_mm3, edited

Organs are compared label by label, so a voxel moved from liver to colon
counts as removed from one and added to the other. Stones are compared as a
single mask (any stone label), because renumbering after an edit is not a
correction; the stone counts before and after are reported separately.

Comparison is exact. A segmentation exported from Slicer at the reference
geometry with no edits round-trips voxel for voxel, so any difference is an
edit. The volumes are in the record, so a later analysis can apply its own
threshold for "trivial" edits.

Nothing here reads or writes imaging; callers pass arrays.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def _voxel_mm3(affine: np.ndarray) -> float:
    return float(abs(np.linalg.det(np.asarray(affine)[:3, :3])))


def compare_organs(before: np.ndarray, after: np.ndarray, names: dict[str, int],
                   voxel_mm3: float) -> list[dict]:
    """Per-label volumes before and after, and voxels added and removed."""
    if before.shape != after.shape:
        raise ValueError("label maps must share one grid")
    name_of = {v: k for k, v in names.items()}
    present = {int(v) for v in np.unique(before)} | {int(v) for v in np.unique(after)}
    labels = sorted(present - {0})
    rows = []
    for label in labels:
        b = before == label
        a = after == label
        added = int(np.count_nonzero(a & ~b))
        removed = int(np.count_nonzero(b & ~a))
        rows.append({
            "name": name_of.get(label, f"label_{label}"),
            "label": label,
            "before_mm3": round(int(b.sum()) * voxel_mm3, 1),
            "after_mm3": round(int(a.sum()) * voxel_mm3, 1),
            "added_mm3": round(added * voxel_mm3, 1),
            "removed_mm3": round(removed * voxel_mm3, 1),
            "edited": bool(added or removed),
        })
    return rows


def compare_stones(before: np.ndarray, after: np.ndarray, voxel_mm3: float) -> dict:
    """Stones as one mask, plus the stone counts before and after."""
    if before.shape != after.shape:
        raise ValueError("label maps must share one grid")
    b = before > 0
    a = after > 0
    added = int(np.count_nonzero(a & ~b))
    removed = int(np.count_nonzero(b & ~a))
    return {
        "n_before": len([v for v in np.unique(before) if v > 0]),
        "n_after": len([v for v in np.unique(after) if v > 0]),
        "before_mm3": round(int(b.sum()) * voxel_mm3, 1),
        "after_mm3": round(int(a.sum()) * voxel_mm3, 1),
        "added_mm3": round(added * voxel_mm3, 1),
        "removed_mm3": round(removed * voxel_mm3, 1),
        "edited": bool(added or removed),
    }


def correction_record(case_id: str, organs_before: np.ndarray, organs_after: np.ndarray,
                      stones_before: np.ndarray, stones_after: np.ndarray,
                      affine: np.ndarray, names: dict[str, int]) -> dict:
    voxel_mm3 = _voxel_mm3(affine)
    organs = compare_organs(organs_before, organs_after, names, voxel_mm3)
    stones = compare_stones(stones_before, stones_after, voxel_mm3)
    edited = [row["name"] for row in organs if row["edited"]]
    return {
        "case_id": case_id,
        "voxel_mm3": round(voxel_mm3, 4),
        "any_edit": bool(edited or stones["edited"]),
        "organs_edited": edited,
        "organs": organs,
        "stones": stones,
    }


def write_correction_record(record: dict, out_dir: Path) -> Path:
    path = Path(out_dir) / f"{record['case_id']}_corrections.json"
    path.write_text(json.dumps(record, indent=2))
    return path
