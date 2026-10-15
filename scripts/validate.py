"""Phase 1 exit-criterion report over the 20-case set.

Inputs:
    cases_dir/              one subfolder per case_id holding the ingest,
                            segment, and plan outputs
    manual.csv              case_id, manual_stone_volume_mm3, laterality,
                            operative_calyx (upper|mid|lower|pelvis),
                            operative_supracostal (0|1), stone_free (0|1),
                            residual_mm (blank if stone free)

Outputs:
    validation.csv          per case: automated vs manual volume, percent
                            error, top-ranked tract calyceal group, match (0|1),
                            top-ranked supracostal, match (0|1)
    validation_summary.txt  mean and median absolute percent error on volume,
                            fraction within 10%, calyceal concordance count,
                            supracostal concordance count

Calyceal group of a tract in Phase 1: assign by the target stone centroid's
craniocaudal position within the kidney mask (upper third / middle third /
lower third) or "pelvis" if the centroid lies in the renal sinus region
(inside the kidney convex hull but outside the parenchyma mask). This is
crude and is replaced in Phase 2 by the calyx proxy.

The manual.csv and all imaging stay outside the repo.

Implementation notes:

  - Files read per case, from cases_dir/<case_id>/: <case_id>_stones.csv
    and <case_id>_organs.nii.gz (segment), <case_id>_tracts.json (plan).
    A missing file leaves that case's affected columns blank with a note;
    blank cases are left out of the counts, and the summary says how many.
  - Automated volume is the sum of stones whose laterality matches the
    manual laterality, or all stones if the manual laterality is neither
    left nor right.
  - The top-ranked tract is rank 1 of the plan output when it is collision
    free. If every tract collides, the group is "none" and both matches
    are 0.
  - The kidney is the operative side from the plan JSON. Thirds split the
    kidney mask's superior-inferior extent in RAS mm. A centroid outside
    the convex hull (a UPJ or proximal ureteral stone) is "outside", which
    matches no operative calyx.

Usage:
    python scripts/validate.py <cases_dir> <manual.csv> <out_dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

import numpy as np

GROUPS = ("upper", "mid", "lower", "pelvis")
COLUMNS = ["case_id", "auto_volume_mm3", "manual_volume_mm3", "volume_pct_error",
           "top_tract_group", "operative_calyx", "calyx_match",
           "top_supracostal", "operative_supracostal", "supracostal_match", "note"]


def read_manual(path: Path) -> dict[str, dict]:
    with Path(path).open(newline="") as f:
        return {row["case_id"].strip(): row for row in csv.DictReader(f)}


def auto_volume(stones_csv: Path, laterality: str) -> float:
    side = (laterality or "").strip().lower()
    total = 0.0
    with Path(stones_csv).open(newline="") as f:
        for row in csv.DictReader(f):
            if side in ("left", "right") and row["laterality"] != side:
                continue
            total += float(row["volume_mm3"])
    return total


def calyceal_group(point_mm: np.ndarray, kidney: np.ndarray, affine: np.ndarray) -> str:
    """upper / mid / lower by thirds of the kidney, pelvis in the sinus, else outside."""
    from scipy.spatial import ConvexHull, Delaunay

    ijk = np.argwhere(kidney)
    if len(ijk) < 4:
        return "outside"
    pts = ijk @ affine[:3, :3].T + affine[:3, 3]
    point_mm = np.asarray(point_mm, dtype=float)

    inv = np.linalg.inv(affine)
    v = np.rint(inv[:3, :3] @ point_mm + inv[:3, 3]).astype(int)
    in_mask = bool(np.all((v >= 0) & (v < kidney.shape)) and kidney[tuple(v)])
    if not in_mask:
        hull = Delaunay(pts[ConvexHull(pts).vertices])
        return "pelvis" if hull.find_simplex(point_mm[None, :])[0] >= 0 else "outside"

    z_low, z_high = pts[:, 2].min(), pts[:, 2].max()
    t = (point_mm[2] - z_low) / max(z_high - z_low, 1e-9)
    if t > 2.0 / 3.0:
        return "upper"
    if t < 1.0 / 3.0:
        return "lower"
    return "mid"


def _flag(value) -> int | None:
    text = str(value).strip().lower()
    if text in ("1", "true", "yes"):
        return 1
    if text in ("0", "false", "no"):
        return 0
    return None


def validate_case(case_dir: Path, case_id: str, manual: dict) -> dict:
    """One validation.csv row for one case."""
    row = {k: "" for k in COLUMNS}
    row["case_id"] = case_id
    row["operative_calyx"] = manual.get("operative_calyx", "").strip().lower()
    row["operative_supracostal"] = _flag(manual.get("operative_supracostal", ""))
    notes = []
    if row["operative_calyx"] not in GROUPS:
        notes.append(f"operative_calyx {row['operative_calyx']!r} not one of {GROUPS}")

    stones_csv = case_dir / f"{case_id}_stones.csv"
    manual_volume = manual.get("manual_stone_volume_mm3", "").strip()
    if stones_csv.exists() and manual_volume:
        auto = auto_volume(stones_csv, manual.get("laterality", ""))
        ref = float(manual_volume)
        row["auto_volume_mm3"] = round(auto, 1)
        row["manual_volume_mm3"] = ref
        row["volume_pct_error"] = round(100.0 * (auto - ref) / ref, 2) if ref else ""
    else:
        notes.append("no stones csv" if not stones_csv.exists() else "no manual volume")

    tracts_json = case_dir / f"{case_id}_tracts.json"
    organs_path = case_dir / f"{case_id}_organs.nii.gz"
    if not tracts_json.exists():
        notes.append("no tracts json")
    elif not organs_path.exists():
        notes.append("no organ map")
    else:
        plan = json.loads(tracts_json.read_text())
        top = plan["tracts"][0] if plan.get("tracts") else None
        if top is None or top["collision"]:
            row["top_tract_group"] = "none"
            row["calyx_match"] = 0
            row["supracostal_match"] = 0
            notes.append("no collision-free tract")
        else:
            import nibabel as nib

            from stoneplanner.segment import ORGAN_LABELS

            img = nib.load(str(organs_path))
            organs = np.asarray(img.dataobj)
            kidney = organs == ORGAN_LABELS[f"kidney_{plan['side']}"]
            group = calyceal_group(np.array(top["target_xyz_mm"]), kidney, img.affine)
            row["top_tract_group"] = group
            row["calyx_match"] = int(group == row["operative_calyx"])
            row["top_supracostal"] = int(bool(top["supracostal"]))
            if row["operative_supracostal"] is not None:
                row["supracostal_match"] = int(row["top_supracostal"]
                                               == row["operative_supracostal"])
    if row["operative_supracostal"] is None:
        row["operative_supracostal"] = ""
    row["note"] = "; ".join(notes)
    return row


def summarize(rows: list[dict]) -> str:
    errors = [abs(float(r["volume_pct_error"])) for r in rows if r["volume_pct_error"] != ""]
    calyx = [r["calyx_match"] for r in rows if r["calyx_match"] != ""]
    supra = [r["supracostal_match"] for r in rows if r["supracostal_match"] != ""]
    lines = [f"Cases: {len(rows)}"]
    if errors:
        within = sum(e <= 10.0 for e in errors)
        lines += [
            f"Volume, cases with both values: {len(errors)}",
            f"Mean absolute percent error: {statistics.mean(errors):.1f}",
            f"Median absolute percent error: {statistics.median(errors):.1f}",
            f"Within 10%: {within}/{len(errors)} ({100.0 * within / len(errors):.0f}%)",
        ]
    else:
        lines.append("Volume: no cases with both values")
    if calyx:
        k = sum(calyx)
        lines.append(f"Calyceal concordance: {k}/{len(calyx)}"
                     f" (majority: {'yes' if k > len(calyx) / 2 else 'no'})")
    else:
        lines.append("Calyceal concordance: no cases with a plan")
    if supra:
        lines.append(f"Supracostal concordance: {sum(supra)}/{len(supra)}")
    else:
        lines.append("Supracostal concordance: no cases with both values")
    skipped = [r["case_id"] for r in rows if r["note"]]
    if skipped:
        lines.append(f"Cases with notes (see validation.csv): {', '.join(skipped)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("cases_dir", type=Path)
    parser.add_argument("manual_csv", type=Path)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)

    manual = read_manual(args.manual_csv)
    rows = [validate_case(args.cases_dir / case_id, case_id, m)
            for case_id, m in sorted(manual.items())]
    extra = sorted(p.name for p in args.cases_dir.iterdir()
                   if p.is_dir() and p.name not in manual)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "validation.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize(rows)
    if extra:
        summary += f"Case folders not in manual.csv (ignored): {', '.join(extra)}\n"
    (args.out_dir / "validation_summary.txt").write_text(summary)
    print(summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
