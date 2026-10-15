"""Segmentation tests on synthetic phantoms. No real imaging, no TotalSegmentator.

The phantom is a 64^3, 1 mm grid in RAS mm with both kidneys, a spine, a
rib block close to the right kidney, and HU blobs standing in for stones and
calcifications. TotalSegmentator is replaced by a stub module so the wiring
(roi_subset, device names, body merge, fallback) is tested without torch.
"""

import sys
import types

import nibabel as nib
import numpy as np
import pytest
from scipy import ndimage

from stoneplanner import geometry, segment

N = 64
AFFINE = np.array([
    [1.0, 0.0, 0.0, -32.0],
    [0.0, 1.0, 0.0, -32.0],
    [0.0, 0.0, 1.0, -32.0],
    [0.0, 0.0, 0.0, 1.0],
])
AFFINE_FLIPPED = np.array([
    [-1.0, 0.0, 0.0, 31.0],
    [0.0, -1.0, 0.0, 31.0],
    [0.0, 0.0, 1.0, -32.0],
    [0.0, 0.0, 0.0, 1.0],
])
L = segment.ORGAN_LABELS
RIGHT_STONE = np.array([14.0, -6.0, 0.0])
LEFT_STONE = np.array([-14.0, -6.0, 3.0])


def _mm_grid(affine, shape=(N, N, N)):
    ijk = np.indices(shape).reshape(3, -1).T
    xyz = ijk @ affine[:3, :3].T + affine[:3, 3]
    return [c.reshape(shape) for c in xyz.T]


def _ball(x, y, z, c, r):
    return (x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2 <= r ** 2


def make_phantom(affine=AFFINE):
    """Return ct_hu, organs on the grid described by affine."""
    x, y, z = _mm_grid(affine)
    body = (x / 30.0) ** 2 + (y / 22.0) ** 2 <= 1.0
    ct = np.where(body, 40.0, -1000.0).astype(np.float32)
    organs = np.zeros((N, N, N), dtype=np.uint8)

    for sign, side in ((1.0, "right"), (-1.0, "left")):
        kidney = (((x - sign * 14.0) / 7.0) ** 2 + ((y + 6.0) / 7.0) ** 2
                  + (z / 12.0) ** 2) <= 1.0
        organs[kidney] = L[f"kidney_{side}"]
    organs[x ** 2 + (y + 14.0) ** 2 <= 16.0] = L["vertebrae_L1"]
    ct[x ** 2 + (y + 14.0) ** 2 <= 16.0] = 700.0

    # Rib 2 to 4 mm lateral to the right kidney; its bright rim is one voxel
    # wider than the rib mask, as when the mask misses cortical edge.
    rib_mask = (x >= 23) & (x <= 25) & (y >= -7) & (y <= -5) & (z >= -1) & (z <= 1)
    organs[rib_mask] = L["rib_right_12"]
    ct[(x >= 22) & (x <= 26) & (y >= -8) & (y <= -4) & (z >= -2) & (z <= 2)] = 700.0

    ct[_ball(x, y, z, RIGHT_STONE, 3.0)] = 1000.0
    ct[_ball(x, y, z, LEFT_STONE, 2.0)] = 600.0
    # Two-voxel speck in the right kidney: below MIN_STONE_VOXELS.
    ct[(x == 14) & (y == -6) & (z >= 8) & (z <= 9)] = 500.0
    # Aortic calcification well outside the dilated kidneys.
    ct[_ball(x, y, z, (0.0, 4.0, 0.0), 2.0)] = 400.0
    return ct, organs


# ---------------------------------------------------------------------------
# Stones


def test_stones_found_labelled_and_lateralised():
    ct, organs = make_phantom()
    stones, table = segment.segment_stones(ct, organs, AFFINE)
    assert [row["label"] for row in table] == [1, 2]
    right, left = table
    assert right["laterality"] == "right"
    assert left["laterality"] == "left"
    np.testing.assert_allclose(right["centroid_xyz_mm"], RIGHT_STONE, atol=0.01)
    np.testing.assert_allclose(left["centroid_xyz_mm"], LEFT_STONE, atol=0.01)
    assert right["volume_mm3"] > left["volume_mm3"]
    assert right["max_hu"] == 1000.0
    assert left["mean_hu"] == pytest.approx(600.0)
    assert set(np.unique(stones)) == {0, 1, 2}
    assert np.all(stones[ct < segment.STONE_HU_THRESHOLD] == 0)


def test_bone_touching_component_removed():
    ct, organs = make_phantom()
    stones, _ = segment.segment_stones(ct, organs, AFFINE)
    x, y, z = _mm_grid(AFFINE)
    rib_block = (x >= 22) & (x <= 26) & (y >= -8) & (y <= -4) & (z >= -2) & (z <= 2)
    assert not stones[rib_block].any()
    assert not stones[x ** 2 + (y + 14.0) ** 2 <= 16.0].any()


def test_small_and_distant_candidates_dropped():
    ct, organs = make_phantom()
    stones, _ = segment.segment_stones(ct, organs, AFFINE)
    x, y, z = _mm_grid(AFFINE)
    assert not stones[(x == 14) & (y == -6) & (z >= 8) & (z <= 9)].any()
    assert not stones[_ball(x, y, z, (0.0, 4.0, 0.0), 2.0)].any()


def test_stone_just_outside_kidney_kept():
    ct, organs = make_phantom()
    x, y, z = _mm_grid(AFFINE)
    # Upper pole of the right kidney is at z = 12; this blob sits 3 mm above it.
    ct[_ball(x, y, z, (14.0, -6.0, 15.0), 1.5)] = 800.0
    _, table = segment.segment_stones(ct, organs, AFFINE)
    centroids = np.array([row["centroid_xyz_mm"] for row in table])
    assert np.any(np.linalg.norm(centroids - [14.0, -6.0, 15.0], axis=1) < 0.5)


def test_half_max_corrects_partial_volume():
    true_r = 4.0
    ct, organs = make_phantom()
    x, y, z = _mm_grid(AFFINE)
    sharp = np.where(_ball(x, y, z, RIGHT_STONE, true_r), 1200.0, 40.0)
    blurred = ndimage.gaussian_filter(sharp, sigma=1.0)
    ct = np.where(_ball(x, y, z, RIGHT_STONE, 8.0), blurred, ct).astype(np.float32)
    _, table = segment.segment_stones(ct, organs, AFFINE)
    true_mm3 = int(_ball(x, y, z, RIGHT_STONE, true_r).sum())
    naive_mm3 = int(((blurred > segment.STONE_HU_THRESHOLD)
                     & _ball(x, y, z, RIGHT_STONE, 8.0)).sum())
    measured = table[0]["volume_mm3"]
    assert naive_mm3 > 1.3 * true_mm3            # the blur really inflates the naive volume
    assert abs(measured - true_mm3) / true_mm3 < 0.10


def test_half_max_never_grows_faint_stone():
    ct, organs = make_phantom()
    x, y, z = _mm_grid(AFFINE)
    faint = _ball(x, y, z, (14.0, -1.0, -6.0), 2.0)
    ct[faint] = 200.0                       # half max = 100, below the 130 floor
    ct[_ball(x, y, z, (14.0, -1.0, -6.0), 3.0) & ~faint] = 120.0
    _, table = segment.segment_stones(ct, organs, AFFINE)
    row = next(r for r in table if np.allclose(r["centroid_xyz_mm"], (14.0, -1.0, -6.0)))
    assert row["volume_mm3"] == faint.sum()


def test_stones_independent_of_voxel_order():
    a = segment.segment_stones(*make_phantom(AFFINE), AFFINE)[1]
    b = segment.segment_stones(*make_phantom(AFFINE_FLIPPED), AFFINE_FLIPPED)[1]
    assert len(a) == len(b)
    for ra, rb in zip(a, b):
        assert ra["volume_mm3"] == rb["volume_mm3"]
        assert ra["laterality"] == rb["laterality"]
        np.testing.assert_allclose(ra["centroid_xyz_mm"], rb["centroid_xyz_mm"], atol=1e-6)
        np.testing.assert_allclose(ra["bbox"], rb["bbox"], atol=1e-6)


def test_no_kidneys_no_stones():
    ct, organs = make_phantom()
    organs[:] = 0
    stones, table = segment.segment_stones(ct, organs, AFFINE)
    assert table == [] and not stones.any()


def test_stone_csv(tmp_path):
    ct, organs = make_phantom()
    _, table = segment.segment_stones(ct, organs, AFFINE)
    path = segment.write_stone_table(table, tmp_path / "s.csv")
    lines = path.read_text().splitlines()
    assert lines[0] == "label,volume_mm3,mean_hu,max_hu,centroid_xyz_mm,laterality,bbox"
    assert len(lines) == 1 + len(table)
    assert lines[1].split(",")[5] == "right"


# ---------------------------------------------------------------------------
# Body


def test_body_mask_fills_lungs_and_drops_table():
    x, y, _ = _mm_grid(AFFINE)
    body = (x / 30.0) ** 2 + (y / 22.0) ** 2 <= 1.0
    ct = np.where(body, 40.0, -1000.0).astype(np.float32)
    lungs = ((np.abs(x) - 12.0) / 6.0) ** 2 + (y / 8.0) ** 2 <= 1.0
    ct[lungs] = -850.0                                   # through every slice
    table = (y >= -31) & (y <= -28) & (np.abs(x) <= 28)
    bridge = (y > -28) & (y < -21) & (np.abs(x) <= 0.5)
    ct[table | bridge] = 0.0
    mask = segment.body_mask(ct, (1.0, 1.0, 1.0), axial_axis=2)
    assert mask[lungs].all()
    assert not mask[table].any()
    interior = (x / 28.0) ** 2 + (y / 20.0) ** 2 <= 1.0
    assert mask[interior].all()


def test_body_mask_respects_axial_axis():
    x, y, _ = _mm_grid(AFFINE)
    body = (x / 30.0) ** 2 + (y / 22.0) ** 2 <= 1.0
    ct = np.where(body, 40.0, -1000.0).astype(np.float32)
    ct[((np.abs(x) - 12.0) / 6.0) ** 2 + (y / 8.0) ** 2 <= 1.0] = -850.0
    moved = np.moveaxis(ct, 2, 0)
    a = segment.body_mask(ct, (1.0, 1.0, 1.0), axial_axis=2)
    b = segment.body_mask(moved, (1.0, 1.0, 1.0), axial_axis=0)
    assert np.array_equal(np.moveaxis(a, 2, 0), b)


def test_merge_body_and_geometry_reads_it():
    ct, organs = make_phantom()
    body = segment.body_mask(ct, (1.0, 1.0, 1.0))
    merged = segment.merge_body(organs, body)
    assert np.array_equal(merged[organs > 0], organs[organs > 0])
    assert np.all(merged[(organs == 0) & body] == segment.BODY_LABEL)
    assert np.all(merged[~body & (organs == 0)] == 0)
    params = geometry.PlanParams()
    assert params.organ_labels["body"] == segment.BODY_LABEL
    np.testing.assert_array_equal(geometry._body_mask(ct, merged, AFFINE, params), merged > 0)


# ---------------------------------------------------------------------------
# TotalSegmentator wiring, with a stub in place of the real package


@pytest.fixture
def fake_ts(monkeypatch):
    calls = []
    ct, organs = make_phantom()
    x, y, _ = _mm_grid(AFFINE)
    body_trunc = ((x / 30.0) ** 2 + (y / 22.0) ** 2 <= 1.0).astype(np.uint8)
    state = {"body_fails": False}

    def totalsegmentator(input, output=None, ml=False, task="total", roi_subset=None,
                         device="gpu", **kwargs):
        calls.append({"task": task, "ml": ml, "roi_subset": roi_subset, "device": device})
        if task == "body":
            if state["body_fails"]:
                raise RuntimeError("weights unavailable")
            return nib.Nifti1Image(body_trunc, AFFINE)
        keep = np.isin(organs, [L[name] for name in roi_subset])
        return nib.Nifti1Image(np.where(keep, organs, 0).astype(np.uint8), AFFINE)

    pkg = types.ModuleType("totalsegmentator")
    api = types.ModuleType("totalsegmentator.python_api")
    api.totalsegmentator = totalsegmentator
    pkg.python_api = api
    monkeypatch.setitem(sys.modules, "totalsegmentator", pkg)
    monkeypatch.setitem(sys.modules, "totalsegmentator.python_api", api)
    return calls, ct, organs, body_trunc.astype(bool), state


def _write_ct(tmp_path, ct):
    path = tmp_path / "case_001.nii.gz"
    nib.save(nib.Nifti1Image(ct, AFFINE), str(path))
    return path


def test_run_totalsegmentator_wiring(tmp_path, fake_ts):
    calls, ct, organs, body, _ = fake_ts
    out = segment.run_totalsegmentator(_write_ct(tmp_path, ct), tmp_path / "out", device="cuda")
    assert out.name == "case_001_organs.nii.gz"
    assert [c["task"] for c in calls] == ["total", "body"]
    assert calls[0]["roi_subset"] == segment.ORGAN_ROI_SUBSET
    assert all(c["device"] == "gpu" and c["ml"] for c in calls)
    merged = np.asarray(nib.load(str(out)).dataobj)
    assert np.array_equal(merged[organs > 0], organs[organs > 0])
    assert np.all(merged[body & (organs == 0)] == segment.BODY_LABEL)


def test_run_totalsegmentator_body_fallback(tmp_path, fake_ts, capsys):
    calls, ct, organs, _, state = fake_ts
    state["body_fails"] = True
    out = segment.run_totalsegmentator(_write_ct(tmp_path, ct), tmp_path / "out")
    assert calls[0]["device"] == "cpu"
    assert "threshold" in capsys.readouterr().err
    merged = np.asarray(nib.load(str(out)).dataobj)
    expected = segment.body_mask(ct, (1.0, 1.0, 1.0))
    assert np.array_equal(merged > 0, expected | (organs > 0))


def test_main_writes_outputs(tmp_path, fake_ts):
    _, ct, _, _, _ = fake_ts
    out_dir = tmp_path / "out"
    assert segment.main([str(_write_ct(tmp_path, ct)), str(out_dir)]) == 0
    stones = np.asarray(nib.load(str(out_dir / "case_001_stones.nii.gz")).dataobj)
    assert set(np.unique(stones)) == {0, 1, 2}
    rows = (out_dir / "case_001_stones.csv").read_text().splitlines()
    assert len(rows) == 3
