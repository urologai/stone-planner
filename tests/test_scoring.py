"""Phase 2 scoring tests: soft penalties, weighted score, flexible reach, targets.

Unit tests build Tract objects by hand so each trade-off is exact. Pipeline
tests run plan() on the synthetic phantom in stoneplanner.phantom.
"""

import json
import math

import nibabel as nib
import numpy as np
import pytest

from stoneplanner import geometry as g
from stoneplanner import phantom as ph

FAST = {"n_entry_points": 800}
ZERO = {k: 0.0 for k in ("w_colon", "w_pleura", "w_supracostal", "w_parenchyma",
                         "w_infundibular")}


def tract(**kw):
    base = {"entry_mm": np.zeros(3), "target_mm": np.ones(3), "reachable_fraction": 1.0,
            "tract_length_mm": 80.0, "parenchymal_depth_mm": 20.0, "nearest_organ": "rib",
            "nearest_organ_mm": 10.0, "supracostal": False, "collision": False,
            "collision_organ": None, "parenchymal_thickness_mm": 20.0}
    return g.Tract(**{**base, **kw})


# ---------------------------------------------------------------------------
# Penalties and score


def test_proximity_penalties():
    params = g.PlanParams()
    assert g.penalties(tract(colon_mm=0.0), params)["colon"] == 1.0
    assert g.penalties(tract(colon_mm=10.0), params)["colon"] == pytest.approx(0.5)
    assert g.penalties(tract(colon_mm=25.0), params)["colon"] == 0.0
    assert g.penalties(tract(pleura_mm=5.0), params)["pleura"] == pytest.approx(0.75)
    assert g.penalties(tract(), params)["colon"] == 0.0           # colon absent: inf


def test_parenchyma_band_penalty():
    params = g.PlanParams(parenchyma_band_mm=[10.0, 30.0])
    assert g.penalties(tract(parenchymal_thickness_mm=20.0), params)["parenchyma"] == 0.0
    assert g.penalties(tract(parenchymal_thickness_mm=5.0), params)["parenchyma"] == 0.5
    assert g.penalties(tract(parenchymal_thickness_mm=45.0), params)["parenchyma"] == 0.5
    assert g.penalties(tract(parenchymal_thickness_mm=0.0), params)["parenchyma"] == 1.0


def test_infundibular_and_supracostal_penalties():
    params = g.PlanParams()
    assert g.penalties(tract(infundibular_angle_deg=45.0), params)["infundibular"] == 0.5
    assert g.penalties(tract(), params)["infundibular"] == 0.0     # stone target: NaN
    assert g.penalties(tract(supracostal=True), params)["supracostal"] == 1.0


def test_zero_weights_score_is_reach():
    params = g.PlanParams(**ZERO)
    t = tract(reachable_fraction=0.7, colon_mm=1.0, supracostal=True,
              infundibular_angle_deg=80.0, parenchymal_thickness_mm=2.0)
    assert g.score(t, params) == pytest.approx(0.7)


def test_colon_weight_trades_reach_for_distance():
    close = tract(reachable_fraction=1.0, colon_mm=2.0)
    far = tract(reachable_fraction=0.9, colon_mm=30.0)
    for weight, winner in ((0.0, close), (0.5, far)):
        params = g.PlanParams(**{**ZERO, "w_colon": weight})
        for t in (close, far):
            t.score = g.score(t, params)
        assert g.rank_tracts([close, far], None)[0] is winner


def test_supracostal_weight_prefers_subcostal():
    supra = tract(reachable_fraction=1.0, supracostal=True)
    sub = tract(reachable_fraction=0.95, supracostal=False)
    params = g.PlanParams(**{**ZERO, "w_supracostal": 0.1})
    for t in (supra, sub):
        t.score = g.score(t, params)
    assert g.rank_tracts([supra, sub], None)[0] is sub


def test_collision_still_hard_fails():
    blocked = tract(reachable_fraction=1.0, collision=True, collision_organ="colon")
    clear = tract(reachable_fraction=0.1)
    for t in (blocked, clear):
        t.score = g.score(t, g.PlanParams())
    assert g.rank_tracts([blocked, clear], None)[0] is clear


# ---------------------------------------------------------------------------
# Pipeline


@pytest.fixture(scope="module")
def phantom_case():
    return ph.make_phantom()


def test_flexible_reach_never_below_rigid(phantom_case):
    tracts = g.plan(*phantom_case, ph.AFFINE, side="right", params=g.PlanParams(**FAST),
                    return_all=True)
    assert all(t.flexible_reachable_fraction >= t.reachable_fraction for t in tracts)
    assert any(t.flexible_reachable_fraction > t.reachable_fraction for t in tracts)


def test_flexible_sweep_beyond_90_reaches_behind_apex():
    stones, affine = np.zeros((32, 32, 32), dtype=np.int16), np.eye(4)
    affine[:3, 3] = -16.0
    stones[23:26, 16, 14] = 1                      # x = 7..9, z = -2: about 104 deg off axis
    axis = np.array([0.0, 0.0, 1.0])
    params = g.PlanParams(flex_sweep_deg=170.0)
    assert g.reachable_fraction(np.zeros(3), axis, stones, affine, params) == 0.0
    assert g.reachable_fraction(np.zeros(3), axis, stones, affine, params, flexible=True) == 1.0


def test_target_modes(phantom_case):
    for mode, sources in (("stone", {"stone"}), ("calyx", {"calyx"}),
                          ("both", {"stone", "calyx"})):
        params = g.PlanParams(target_mode=mode, **FAST)
        tracts = g.plan(*phantom_case, ph.AFFINE, side="right", params=params, return_all=True)
        assert {t.target_source for t in tracts} == sources
    calyx_tracts = g.plan(*phantom_case, ph.AFFINE, side="right",
                          params=g.PlanParams(target_mode="calyx", **FAST), return_all=True)
    assert {t.target_group for t in calyx_tracts} == {"upper", "mid", "lower"}
    with pytest.raises(ValueError):
        g.plan(*phantom_case, ph.AFFINE, side="right", params=g.PlanParams(target_mode="x"))


def test_infundibular_weight_aligns_tract_with_calyx(phantom_case):
    angles = []
    for weight in (0.0, 5.0):
        params = g.PlanParams(**{**ZERO, **FAST, "target_mode": "calyx",
                                 "w_infundibular": weight})
        top = g.plan(*phantom_case, ph.AFFINE, side="right", params=params)[0]
        angles.append(top.infundibular_angle_deg)
    assert angles[1] < 5.0
    assert angles[1] < angles[0]


def test_stone_targets_have_groups_and_no_angle(phantom_case):
    params = g.PlanParams(target_mode="stone", **FAST)
    tracts = g.plan(*phantom_case, ph.AFFINE, side="right", params=params)
    assert tracts[0].target_group == "mid"
    assert math.isnan(tracts[0].infundibular_angle_deg)


def test_best_by_group_spans_all_tracts(phantom_case, tmp_path):
    params = g.PlanParams(top_n=2, **FAST)
    tracts = g.plan(*phantom_case, ph.AFFINE, side="right", params=params, return_all=True)
    best = g.best_by_group(tracts)
    assert set(best) == {"upper", "mid", "lower"}
    assert all(not row["collision"] for row in best.values())
    _, json_path = g.write_outputs(tracts, tmp_path, "phantom", "right", params)
    payload = json.loads(json_path.read_text())
    assert len(payload["tracts"]) == 2
    assert set(payload["best_by_group"]) == {"upper", "mid", "lower"}
    assert payload["params"]["w_colon"] == params.w_colon


def test_cli_params_json_override(tmp_path, phantom_case):
    ct, organs, stones = phantom_case
    paths = []
    for name, arr in (("case", ct), ("organs", organs), ("stones", stones)):
        path = tmp_path / f"{name}.nii.gz"
        nib.save(nib.Nifti1Image(arr, ph.AFFINE), str(path))
        paths.append(str(path))
    overrides = tmp_path / "weights.json"
    overrides.write_text(json.dumps({"w_colon": 0.9, "top_n": 4, **FAST}))
    out = tmp_path / "out"
    assert g.main([*paths, str(out), "--side", "right", "--targets", "calyx",
                   "--params-json", str(overrides)]) == 0
    payload = json.loads((out / "case_tracts.json").read_text())
    assert payload["params"]["w_colon"] == 0.9
    assert payload["params"]["target_mode"] == "calyx"
    assert len(payload["tracts"]) == 4

    overrides.write_text(json.dumps({"w_colonn": 1.0}))
    with pytest.raises(SystemExit):
        g.main([*paths, str(out), "--params-json", str(overrides)])
