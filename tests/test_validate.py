"""Validation report tests on a synthetic kidney. No real imaging.

The kidney is an ellipsoid with a cavity open on its medial side, standing in
for the renal sinus. Calyceal grouping itself is tested in test_calyx.py;
here it is exercised through the report for Phase 1 plans, which carry no
target_group.
"""

import csv
import importlib.util
import json
from pathlib import Path

import nibabel as nib
import numpy as np

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
MANUAL_HEADER = ("case_id,manual_stone_volume_mm3,laterality,operative_calyx,"
                 "operative_supracostal,stone_free,residual_mm\n")


def kidney_mask():
    ijk = np.indices((N, N, N)).reshape(3, -1).T
    x, y, z = (ijk @ AFFINE[:3, :3].T + AFFINE[:3, 3]).T.reshape(3, N, N, N)
    outer = ((x - 16) / 8) ** 2 + (y / 8) ** 2 + (z / 16) ** 2 <= 1.0
    sinus = ((x - 11) / 4) ** 2 + (y / 4) ** 2 + (z / 6) ** 2 <= 1.0
    return outer & ~sinus


def _stones_csv(path, stones):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "volume_mm3", "mean_hu", "max_hu", "centroid_xyz_mm",
                    "laterality", "bbox"])
        for i, (side, vol) in enumerate(stones, start=1):
            w.writerow([i, vol, 900, 1200, "0 0 0", side, "0 0 0 0 0 0"])


def _write_case(root, case_id, target, supracostal=False, collision=False, stones=None,
                group=None, best=None, corrected_stones=None, corrections=None):
    """A case folder. group=None writes a Phase 1 plan (no target_group)."""
    folder = root / case_id
    folder.mkdir(parents=True)
    organs = np.where(kidney_mask(), ORGAN_LABELS["kidney_right"], 0).astype(np.uint8)
    nib.save(nib.Nifti1Image(organs, AFFINE), str(folder / f"{case_id}_organs.nii.gz"))
    _stones_csv(folder / f"{case_id}_stones.csv", stones or [("right", 900.0), ("left", 50.0)])
    if corrected_stones:
        _stones_csv(folder / f"{case_id}_stones_corrected.csv", corrected_stones)
    tract = {"rank": 1, "entry_xyz_mm": [40, -30, 0], "target_xyz_mm": list(target),
             "reachable_fraction": 0.9, "supracostal": supracostal, "collision": collision}
    plan = {"case_id": case_id, "side": "right", "tracts": [tract]}
    if group is not None:
        tract.update(target_group=group, target_source="calyx")
        plan["best_by_group"] = best or {}
    (folder / f"{case_id}_tracts.json").write_text(json.dumps(plan))
    if corrections is not None:
        (folder / f"{case_id}_corrections.json").write_text(json.dumps(corrections))


def _rows(out):
    with (out / "validation.csv").open(newline="") as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


def test_phase1_plans(tmp_path):
    cases = tmp_path / "cases"
    _write_case(cases, "case_001", (16.0, 0.0, 12.0), supracostal=True)   # upper, match
    _write_case(cases, "case_002", (12.0, 0.0, 0.0))                      # pelvis vs lower
    _write_case(cases, "case_003", (16.0, 0.0, -12.0), collision=True)    # nothing clear
    (cases / "case_099").mkdir()                                          # not in manual
    manual = tmp_path / "manual.csv"
    manual.write_text(MANUAL_HEADER
                      + "case_001,1000,right,upper,1,1,\n"
                      + "case_002,800,right,lower,0,0,4\n"
                      + "case_003,900,right,lower,0,1,\n"
                      + "case_004,500,left,mid,0,1,\n")
    out = tmp_path / "out"
    assert validate.main([str(cases), str(manual), str(out)]) == 0

    rows = _rows(out)
    assert rows["case_001"]["auto_volume_mm3"] == "900.0"           # right stones only
    assert rows["case_001"]["volume_pct_error"] == "-10.0"
    assert rows["case_001"]["stones_source"] == "automatic"
    assert rows["case_001"]["top_tract_group"] == "upper"
    assert rows["case_001"]["calyx_match"] == "1"
    assert rows["case_001"]["supracostal_match"] == "1"
    assert rows["case_002"]["top_tract_group"] == "pelvis"
    assert rows["case_002"]["calyx_match"] == "0"
    assert rows["case_002"]["volume_pct_error"] == "12.5"
    assert rows["case_002"]["residual_mm"] == "4.0"
    assert rows["case_003"]["top_tract_group"] == "none"
    assert rows["case_004"]["note"] == "no stones csv; no tracts json"

    summary = (out / "validation_summary.txt").read_text()
    assert "Cases: 4" in summary
    assert "Within 10%: 2/3" in summary              # -10.0, +12.5, 0.0
    assert "Calyceal concordance: 1/3 (majority: no)" in summary
    assert "Supracostal concordance: 2/3" in summary  # case_003 has no clear tract
    assert "Segmentation corrected: no correction logs" in summary
    assert "case_099" in summary


def _best(reach, flex):
    return {"reachable_fraction": reach, "flexible_reachable_fraction": flex}


def test_phase2_plans_outcomes_and_corrections(tmp_path):
    cases = tmp_path / "cases"
    edit = {"any_edit": True, "organs_edited": ["kidney_right", "colon"],
            "stones": {"edited": True}}
    none = {"any_edit": False, "organs_edited": [], "stones": {"edited": False}}
    # Higher reach to the operative group in the stone-free cases.
    specs = [
        ("c1", "lower", 0.95, 1.0, "1", "", edit),
        ("c2", "lower", 0.90, 1.0, "1", "", none),
        ("c3", "mid", 0.85, 0.9, "1", "", none),
        ("c4", "lower", 0.40, 0.8, "0", "6", edit),
        ("c5", "mid", 0.30, 0.7, "0", "9", none),
    ]
    lines = [MANUAL_HEADER]
    for case_id, group, reach, flex, free, resid, corr in specs:
        _write_case(cases, case_id, (16.0, 0.0, -12.0), group="lower",
                    best={group: _best(reach, flex)},
                    corrected_stones=[("right", 1000.0)] if case_id == "c1" else None,
                    corrections=corr)
        lines.append(f"{case_id},1000,right,{group},0,{free},{resid}\n")
    manual = tmp_path / "manual.csv"
    manual.write_text("".join(lines))
    out = tmp_path / "out"
    validate.main([str(cases), str(manual), str(out)])

    rows = _rows(out)
    assert rows["c1"]["stones_source"] == "corrected"
    assert rows["c1"]["volume_pct_error"] == "0.0"
    assert rows["c2"]["volume_pct_error"] == "-10.0"
    assert rows["c1"]["top_tract_group"] == "lower"           # from the plan, not the mask
    assert rows["c1"]["top_tract_source"] == "calyx"
    assert rows["c3"]["calyx_match"] == "0"                   # plan says lower, surgeon mid
    assert rows["c3"]["reach_operative_group"] == "0.85"
    assert rows["c4"]["flex_reach_operative_group"] == "0.8"
    assert rows["c1"]["residual_mm"] == "0.0"
    assert rows["c1"]["organs_edited"] == "kidney_right colon"

    summary = (out / "validation_summary.txt").read_text()
    assert "AUC stone-free vs residual: 1.00" in summary
    assert "n = 3 free, 2 residual" in summary
    assert "Spearman rho vs residual_mm: -0.89" in summary
    assert "Segmentation corrected: 2/5 cases with a correction log" in summary
    assert "(organs in 2, stones in 2)" in summary
    assert "Organs edited: kidney_right 2, colon 2" in summary


def test_missing_best_group_is_noted(tmp_path):
    cases = tmp_path / "cases"
    _write_case(cases, "c1", (16.0, 0.0, -12.0), group="lower", best={})
    manual = tmp_path / "manual.csv"
    manual.write_text(MANUAL_HEADER + "c1,1000,right,upper,0,1,\n")
    out = tmp_path / "out"
    validate.main([str(cases), str(manual), str(out)])
    assert _rows(out)["c1"]["note"] == "no clear tract to the upper group"
