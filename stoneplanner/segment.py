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

Implementation notes (how the steps above are made concrete):

  - Organ label values are the TotalSegmentator "total" task class IDs
    (ORGAN_LABELS, checked against TotalSegmentator 2.18). roi_subset keeps
    those IDs. The body mask is merged in as BODY_LABEL on every body voxel
    no organ claims, so the organ map alone gives the body: organs > 0.
  - Body: the TotalSegmentator "body" task (free, no licence), class
    body_trunc only, so arms resting at the sides never count as flank skin.
    The threshold fallback opens each axial slice before taking the largest
    component, which cuts thin contacts with the CT table.
  - The roi_subset adds ribs 9, vertebra L4 and both hips to the list above.
    The geometry engine places the iliac crest from the hip masks and the
    mid-axillary line from the most lateral rib; without hips the lower
    flank bound falls back to the lowest vertebra, which sits above the crest.
  - Step 3 drops whole candidate components that touch the bone masks grown
    by one voxel, so a cortical rim the bone mask just misses goes with it.
  - Step 5 keeps the component voxels at or above half its max HU. The HU
    130 floor from step 2 still applies, so a faint stone is not grown.
    On blurred phantom spheres (1 mm voxels, Gaussian blur sigma), half-max
    lands within 3% of true volume when the radius is at least 4 sigma, but
    reads about 30% low when the radius is near 2 sigma, because blur cuts
    the peak. Expect small stones on thick slices to read low against manual.
  - Stone labels are renumbered by volume, largest = 1. Laterality is the
    side whose kidney is nearer to most of the stone's voxels. Centroid and
    bbox are RAS mm (bbox from voxel centres), which is why segment_stones
    takes the affine rather than only the spacing.

Nothing here is run on real scans in tests; tests/test_segment.py uses
synthetic phantoms and a stub in place of TotalSegmentator.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage


def _totalsegmentator_total_labels() -> dict[str, int]:
    """Class IDs used here from the TotalSegmentator 2.18 "total" task."""
    labels = {
        "spleen": 1,
        "kidney_right": 2,
        "kidney_left": 3,
        "liver": 5,
        "lung_upper_lobe_left": 10,
        "lung_lower_lobe_left": 11,
        "lung_upper_lobe_right": 12,
        "lung_middle_lobe_right": 13,
        "lung_lower_lobe_right": 14,
        "colon": 20,
        "vertebrae_L5": 27,
        "vertebrae_L4": 28,
        "vertebrae_L3": 29,
        "vertebrae_L2": 30,
        "vertebrae_L1": 31,
        "vertebrae_T12": 32,
        "vertebrae_T11": 33,
        "vertebrae_T10": 34,
        "vertebrae_T9": 35,
        "aorta": 52,
        "hip_left": 77,
        "hip_right": 78,
    }
    for i in range(1, 13):
        labels[f"rib_left_{i}"] = 91 + i
        labels[f"rib_right_{i}"] = 103 + i
    return labels


# Body voxels not claimed by any organ. Outside the TotalSegmentator ID range.
BODY_LABEL = 200

ORGAN_LABELS: dict[str, int] = {**_totalsegmentator_total_labels(), "body": BODY_LABEL}

ORGAN_ROI_SUBSET: list[str] = [
    "kidney_left", "kidney_right", "liver", "spleen", "colon",
    "lung_lower_lobe_left", "lung_lower_lobe_right",
    *[f"rib_{side}_{i}" for side in ("left", "right") for i in range(9, 13)],
    "vertebrae_T11", "vertebrae_T12", "vertebrae_L1", "vertebrae_L2",
    "vertebrae_L3", "vertebrae_L4",
    "hip_left", "hip_right",
    "aorta",
]

STONE_HU_THRESHOLD = 130
KIDNEY_DILATION_MM = 5.0
MIN_STONE_VOXELS = 3
BODY_HU_THRESHOLD = -500.0
BODY_OPENING_MM = 3.0

_TS_DEVICE = {"cpu": "cpu", "cuda": "gpu", "mps": "mps"}


# ---------------------------------------------------------------------------
# Helpers


def _spacing(affine: np.ndarray) -> np.ndarray:
    return np.linalg.norm(affine[:3, :3], axis=0)


def _axial_axis(affine: np.ndarray) -> int:
    """Voxel axis that runs most nearly along RAS z."""
    return int(np.argmax(np.abs(affine[:3, :3][2])))


def _ids(prefixes: tuple[str, ...]) -> list[int]:
    return [v for k, v in ORGAN_LABELS.items()
            if any(k == p or k.startswith(p + "_") for p in prefixes)]


def _voxel_to_mm(ijk: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return np.asarray(ijk, dtype=float) @ affine[:3, :3].T + affine[:3, 3]


def _largest_component(mask: np.ndarray) -> np.ndarray:
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == int(np.argmax(sizes))


def _disk(radius_px: np.ndarray) -> np.ndarray:
    ry, rx = (max(round(r), 1) for r in radius_px)
    y, x = np.ogrid[-ry:ry + 1, -rx:rx + 1]
    return (y / ry) ** 2 + (x / rx) ** 2 <= 1.0


# ---------------------------------------------------------------------------
# Body


def body_mask(ct_hu: np.ndarray, spacing_mm: tuple[float, float, float] | None = None,
              axial_axis: int = 2, hu_threshold: float = BODY_HU_THRESHOLD) -> np.ndarray:
    """Binary body mask: threshold, largest component, fill holes.

    Each axial slice is opened by BODY_OPENING_MM before the component step
    to cut thin bridges to the table, and holes are filled per axial slice
    so lung and bowel gas count as body even where they reach the scan edge.
    """
    spacing = np.ones(3) if spacing_mm is None else np.asarray(spacing_mm, dtype=float)
    in_plane = np.delete(spacing, axial_axis)
    disk = _disk(BODY_OPENING_MM / in_plane)

    mask = np.moveaxis(ct_hu > hu_threshold, axial_axis, 0).copy()
    for k in range(mask.shape[0]):
        mask[k] = ndimage.binary_opening(mask[k], structure=disk)
    mask = _largest_component(mask)
    for k in range(mask.shape[0]):
        mask[k] = ndimage.binary_fill_holes(mask[k])
    return np.moveaxis(mask, 0, axial_axis)


def merge_body(organs: np.ndarray, body: np.ndarray) -> np.ndarray:
    """Write BODY_LABEL into body voxels no organ claims."""
    out = organs.astype(np.uint8, copy=True)
    out[(out == 0) & body.astype(bool)] = BODY_LABEL
    return out


# ---------------------------------------------------------------------------
# TotalSegmentator


def run_totalsegmentator(nifti_path: Path, out_dir: Path, device: str = "cpu") -> Path:
    """Run TotalSegmentator with ORGAN_ROI_SUBSET. Return path to organ label map.

    The returned map also carries the body mask as BODY_LABEL.
    """
    import nibabel as nib

    try:
        from totalsegmentator.python_api import totalsegmentator
    except ImportError as exc:
        raise RuntimeError("TotalSegmentator is not installed in this environment") from exc

    nifti_path = Path(nifti_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_id = _case_id(nifti_path)
    ts_device = _TS_DEVICE[device]

    ct_img = nib.load(str(nifti_path))
    organ_img = totalsegmentator(nifti_path, out_dir / f"{case_id}_ts_total.nii.gz", ml=True,
                                 task="total", roi_subset=ORGAN_ROI_SUBSET, device=ts_device)
    organs = np.asarray(organ_img.dataobj)
    if organs.shape != ct_img.shape:
        raise RuntimeError(f"TotalSegmentator output shape {organs.shape} != CT {ct_img.shape}")

    try:
        body_img = totalsegmentator(nifti_path, out_dir / f"{case_id}_ts_body.nii.gz", ml=True,
                                    task="body", device=ts_device)
        body = np.asarray(body_img.dataobj) == 1   # body_trunc
    except Exception as exc:  # noqa: BLE001  any failure means fall back, but say so
        print(f"TotalSegmentator body task failed ({exc}); using HU threshold body mask",
              file=sys.stderr)
        ct = np.asarray(ct_img.get_fdata(dtype=np.float32))
        body = body_mask(ct, tuple(_spacing(ct_img.affine)), _axial_axis(ct_img.affine))

    out_path = out_dir / f"{case_id}_organs.nii.gz"
    merged = nib.Nifti1Image(merge_body(organs, body), ct_img.affine)
    merged.set_data_dtype(np.uint8)
    nib.save(merged, str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# Stones


def _kidney_distances(organs: np.ndarray, box: tuple[slice, ...], spacing: np.ndarray):
    """Distance in mm from each voxel in box to the left and right kidney."""
    sub = organs[box]
    out = []
    for name in ("kidney_left", "kidney_right"):
        kidney = sub == ORGAN_LABELS[name]
        if kidney.any():
            out.append(ndimage.distance_transform_edt(~kidney, sampling=spacing))
        else:
            out.append(np.full(sub.shape, np.inf))
    return out


def segment_stones(ct_hu: np.ndarray, organs: np.ndarray, affine: np.ndarray):
    """Return (stone_label_map, stone_table) per the module docstring.

    stone_table is a list of dicts, one per stone, in label order.
    """
    if ct_hu.shape != organs.shape:
        raise ValueError("ct_hu and organs must share one grid")
    stones = np.zeros(ct_hu.shape, dtype=np.int16)
    kidneys = np.isin(organs, [ORGAN_LABELS["kidney_left"], ORGAN_LABELS["kidney_right"]])
    if not kidneys.any():
        return stones, []

    # Work inside the kidneys' bounding box plus the dilation margin.
    spacing = _spacing(affine)
    margin = np.ceil(KIDNEY_DILATION_MM / spacing).astype(int) + 1
    idx = np.argwhere(kidneys)
    lo = np.maximum(idx.min(axis=0) - margin, 0)
    hi = np.minimum(idx.max(axis=0) + margin + 1, ct_hu.shape)
    box = tuple(slice(a, b) for a, b in zip(lo, hi))
    offset = lo

    # Steps 1 and 2.
    d_left, d_right = _kidney_distances(organs, box, spacing)
    region = np.minimum(d_left, d_right) <= KIDNEY_DILATION_MM
    hu = ct_hu[box]
    candidates = region & (hu > STONE_HU_THRESHOLD)

    # Steps 3 and 4.
    structure = np.ones((3, 3, 3), dtype=bool)
    lab, n = ndimage.label(candidates, structure=structure)
    if n == 0:
        return stones, []
    bone = np.isin(organs[box], _ids(("vertebrae", "rib")))
    bone = ndimage.binary_dilation(bone, structure=structure)
    touches_bone = np.zeros(n + 1, dtype=bool)
    touches_bone[np.unique(lab[bone & (lab > 0)])] = True
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    keep = ~touches_bone & (sizes >= MIN_STONE_VOXELS)
    keep[0] = False

    # Step 5, then renumber by volume.
    voxel_mm3 = abs(np.linalg.det(affine[:3, :3]))
    found = []
    for comp, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None or not keep[comp]:
            continue
        in_comp = lab[sl] == comp
        peak = float(hu[sl][in_comp].max())
        final = in_comp & (hu[sl] >= 0.5 * peak)
        ijk_local = np.argwhere(final) + np.array([s.start for s in sl])
        found.append((len(ijk_local), ijk_local))
    found.sort(key=lambda f: -f[0])

    table = []
    for label, (count, ijk_local) in enumerate(found, start=1):
        ijk = ijk_local + offset
        stones[tuple(ijk.T)] = label
        values = ct_hu[tuple(ijk.T)]
        mm = _voxel_to_mm(ijk, affine)
        nearer_left = d_left[tuple(ijk_local.T)] < d_right[tuple(ijk_local.T)]
        table.append({
            "label": label,
            "volume_mm3": float(count * voxel_mm3),
            "mean_hu": float(values.mean()),
            "max_hu": float(values.max()),
            "centroid_xyz_mm": mm.mean(axis=0),
            "laterality": "left" if nearer_left.mean() > 0.5 else "right",
            "bbox": np.concatenate([mm.min(axis=0), mm.max(axis=0)]),
        })
    return stones, table


def write_stone_table(table: list[dict], path: Path) -> Path:
    """CSV with the columns in the module docstring. Coordinates as 'x y z' in mm."""
    columns = ["label", "volume_mm3", "mean_hu", "max_hu", "centroid_xyz_mm", "laterality",
               "bbox"]
    with Path(path).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in table:
            writer.writerow({
                "label": row["label"],
                "volume_mm3": f"{row['volume_mm3']:.1f}",
                "mean_hu": f"{row['mean_hu']:.1f}",
                "max_hu": f"{row['max_hu']:.1f}",
                "centroid_xyz_mm": " ".join(f"{v:.2f}" for v in row["centroid_xyz_mm"]),
                "laterality": row["laterality"],
                "bbox": " ".join(f"{v:.2f}" for v in row["bbox"]),
            })
    return Path(path)


def _case_id(path: Path) -> str:
    name = Path(path).name
    for suffix in (".nii.gz", ".nii"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(path).stem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("nifti", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda", "mps"])
    args = parser.parse_args(argv)

    import nibabel as nib

    case_id = _case_id(args.nifti)
    organ_path = run_totalsegmentator(args.nifti, args.out_dir, device=args.device)
    ct_img = nib.load(str(args.nifti))
    ct = np.asarray(ct_img.get_fdata(dtype=np.float32))
    organs = np.asarray(nib.load(str(organ_path)).dataobj)
    stones, table = segment_stones(ct, organs, ct_img.affine)

    stone_img = nib.Nifti1Image(stones, ct_img.affine)
    stone_img.set_data_dtype(np.int16)
    nib.save(stone_img, str(args.out_dir / f"{case_id}_stones.nii.gz"))
    csv_path = write_stone_table(table, args.out_dir / f"{case_id}_stones.csv")
    total = sum(r["volume_mm3"] for r in table)
    print(f"{len(table)} stones, {total:.0f} mm3 total: {organ_path}, {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
