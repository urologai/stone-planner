"""Validation report tests on a synthetic kidney. No real imaging.

The kidney is an ellipsoid with a cavity open on its medial side, standing in
for the renal sinus: inside the convex hull, outside the parenchyma mask.
"""

import csv
import importlib.util
import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from stoneplanner.segment import ORGAN_LABELS

_spec = importlib.util.spec_from_file_location(
    "validate", Path(__file__).resolve().parents[1] / "scripts" / "validate.py")
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)

N = 64
AFFINE = np.array([
    [1.0, 0.0, 0.0, -32.0],
    [0.0, 1.0, 0.0, -32.0],
    [0.0, 0.0, 1.0, -32.0],
    [0.0, 0.0, 0.0, 1.0],
])


def kidney_mask(affine=AFFINE):
    ijk = np.indices((N, N, N)).reshape(3, -1).T
    x, y, z = (ijk @ affine[:3, :3].T + affine[:3, 3]).T.reshape(3, N, N, N)
    outer = ((x - 16) / 8) ** 2 + (y / 8) ** 2 + (z / 16) ** 2 <= 1.0
    sinus = ((x - 11) / 4) ** 2 + (y / 4) ** 2 + (z / 6) ** 2 <= 1.0
    return outer & ~sinus


@pytest.mark.parametrize("point, group", [
    ((16.0, 0.0, 12.0), "upper"),
    ((19.0, 0.0, 0.0), "mid"),
    ((16.0, 0.0, -12.0), "lower"),
    ((12.0, 0.0, 0.0), "pelvis"),
    ((40.0, 0.0, 0.0), "outside"),
])
def test_calyceal_group(point, group):
    assert validate.calyceal_group(np.array(point), kidney_mask(), AFFINE) == group


def test_calyceal_group_independent_of_voxel_order():
    flipped = np.array([
        [-1.0, 0.0, 0.0, 31.0],
        [0.0, 1.0, 0.0, -32.0],
        [0.0, 0.0, -1.0, 31.0],
        [0.0, 0.0, 0.0, 1.0],
    ])
    for point, group in [((16.0, 0.0, 12.0), "upper"), ((12.0, 0.0, 0.0), "pelvis")]:
        assert validate.calyceal_group(np.array(point), kidney_mask(flipped), flipped) == group


def _write_case(root, case_id, target, supracostal=False, collision=False, stones=None):
    folder = root / case_id
    folder.mkdir(parents=True)
    organs = np.where(kidney_mask(), ORGAN_LABELS["kidney_right"], 0).astype(np.uint8)
    nib.save(nib.Nifti1Image(organs, AFFINE), str(folder / f"{case_id}_organs.nii.gz"))
    stones = stones or [("right", 900.0), ("left", 50.0)]
    with (folder / f"{case_id}_stones.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "volume_mm3", "mean_hu", "max_hu", "centroid_xyz_mm",
                    "laterality", "bbox"])
        for i, (side, vol) in enumerate(stones, start=1):
            w.writerow([i, vol, 900, 1200, "0 0 0", side, "0 0 0 0 0 0"])
    tract = {"rank": 1, "entry_xyz_mm": [40, -30, 0], "target_xyz_mm": list(target),
             "reachable_fraction": 0.9, "supracostal": supracostal, "collision": collision}
    (folder / f"{case_id}_tracts.json").write_text(
        json.dumps({"case_id": case_id, "side": "right", "tracts": [tract]}))


def test_main_report(tmp_path):
    cases = tmp_path / "cases"
    _write_case(cases, "case_001", (16.0, 0.0, 12.0), supracostal=True)   # upper, match
    _write_case(cases, "case_002", (12.0, 0.0, 0.0))                      # pelvis vs lower
    _write_case(cases, "case_003", (16.0, 0.0, -12.0), collision=True)    # nothing clear
    (cases / "case_099").mkdir()                                          # not in manual
    manual = tmp_path / "manual.csv"
    manual.write_text(
        "case_id,manual_stone_volume_mm3,laterality,operative_calyx,operative_supracostal,"
        "stone_free,residual_mm\n"
        "case_001,1000,right,upper,1,1,\n"
        "case_002,800,right,lower,0,0,4\n"
        "case_003,900,right,lower,0,1,\n"
        "case_004,500,left,mid,0,1,\n")
    out = tmp_path / "out"
    assert validate.main([str(cases), str(manual), str(out)]) == 0

    with (out / "validation.csv").open(newline="") as f:
        rows = {r["case_id"]: r for r in csv.DictReader(f)}
    assert rows["case_001"]["auto_volume_mm3"] == "900.0"           # right stones only
    assert rows["case_001"]["volume_pct_error"] == "-10.0"
    assert rows["case_001"]["top_tract_group"] == "upper"
    assert rows["case_001"]["calyx_match"] == "1"
    assert rows["case_001"]["supracostal_match"] == "1"
    assert rows["case_002"]["top_tract_group"] == "pelvis"
    assert rows["case_002"]["calyx_match"] == "0"
    assert rows["case_002"]["volume_pct_error"] == "12.5"
    assert rows["case_003"]["top_tract_group"] == "none"
    assert rows["case_004"]["note"] == "no stones csv; no tracts json"

    summary = (out / "validation_summary.txt").read_text()
    assert "Cases: 4" in summary
    assert "Within 10%: 2/3" in summary              # -10.0, +12.5, 0.0
    assert "Calyceal concordance: 1/3 (majority: no)" in summary
    assert "Supracostal concordance: 2/3" in summary  # case_003 has no clear tract
    assert "case_099" in summary
