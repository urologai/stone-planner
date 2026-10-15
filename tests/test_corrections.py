"""Correction log tests on the synthetic phantom. No real imaging."""

import json

import numpy as np

from stoneplanner import corrections, phantom
from stoneplanner.segment import ORGAN_LABELS


def test_unedited_case_has_no_edits():
    _, organs, stones = phantom.make_phantom()
    rec = corrections.correction_record("c1", organs, organs.copy(), stones, stones.copy(),
                                        phantom.AFFINE, ORGAN_LABELS)
    assert rec["any_edit"] is False
    assert rec["organs_edited"] == []
    assert rec["stones"]["edited"] is False


def test_organ_edit_counted_on_both_labels():
    _, organs, stones = phantom.make_phantom()
    after = organs.copy()
    kidney = organs == ORGAN_LABELS["kidney_right"]
    idx = np.argwhere(kidney)[:10]
    after[tuple(idx.T)] = ORGAN_LABELS["colon"]          # 10 voxels moved kidney -> colon
    rec = corrections.correction_record("c1", organs, after, stones, stones,
                                        phantom.AFFINE, ORGAN_LABELS)
    rows = {r["name"]: r for r in rec["organs"]}
    assert rec["any_edit"] is True
    assert set(rec["organs_edited"]) == {"kidney_right", "colon"}
    assert rows["kidney_right"]["removed_mm3"] == 10.0
    assert rows["colon"]["added_mm3"] == 10.0
    assert rows["hip_right"]["edited"] is False
    assert all(r["label"] != 0 for r in rec["organs"])


def test_stone_renumbering_is_not_an_edit_but_painting_is():
    _, organs, stones = phantom.make_phantom(extra_stones=[((16.0, -8.0, 8.0), 1.5)])
    renumbered = np.where(stones == 1, 2, np.where(stones == 2, 1, 0)).astype(stones.dtype)
    rec = corrections.correction_record("c1", organs, organs, stones, renumbered,
                                        phantom.AFFINE, ORGAN_LABELS)
    assert rec["stones"]["edited"] is False
    assert rec["stones"]["n_before"] == rec["stones"]["n_after"] == 2

    painted = stones.copy()
    painted[stones == 2] = 0                              # a false positive removed
    rec = corrections.correction_record("c1", organs, organs, stones, painted,
                                        phantom.AFFINE, ORGAN_LABELS)
    assert rec["stones"]["edited"] is True
    assert rec["stones"]["n_after"] == 1
    assert rec["stones"]["removed_mm3"] == float((stones == 2).sum())


def test_write_record(tmp_path):
    _, organs, stones = phantom.make_phantom()
    rec = corrections.correction_record("case_009", organs, organs, stones, stones,
                                        phantom.AFFINE, ORGAN_LABELS)
    path = corrections.write_correction_record(rec, tmp_path)
    assert path.name == "case_009_corrections.json"
    assert json.loads(path.read_text())["any_edit"] is False
