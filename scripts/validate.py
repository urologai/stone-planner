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
    When the Slicer export wrote <case_id>_stones_corrected.csv or
    <case_id>_organs_corrected.nii.gz, those are used instead and the
    stones_source column says so. A missing file leaves that case's
    affected columns blank with a note; blank cases are left out of the
    counts, and the summary says how many.
  - Automated volume is the sum of stones whose laterality matches the
    manual laterality, or all stones if the manual laterality is neither
    left nor right.
  - The top-ranked tract is rank 1 of the plan output when it is collision
    free. If every tract collides, the group is "none" and both matches
    are 0.
  - Calyceal group: the target_group the plan wrote for the tract (Phase 2
    plans). For a Phase 1 plan without it, calyx.calyceal_group on the
    operative kidney: thirds along the kidney's long axis, "pelvis" in the
    hull but outside the mask, "outside" beyond the hull (a UPJ or proximal
    ureteral stone), which matches no operative calyx.

Phase 2 additions:

  - reach_operative_group and flex_reach_operative_group: rigid and
    flexible reachable fraction of the best collision-free tract aimed at
    the calyceal group the surgeon actually used (best_by_group in the plan
    JSON). This stands in for the actual tract, whose coordinates are not
    recorded.
  - Residual fragments: AUC of reach_operative_group for stone-free versus
    residual cases (Mann-Whitney U / n1 n2, 0.5 = no discrimination) with
    its two-sided p, and Spearman's rho against residual_mm (0 when stone
    free). Both report n; with the 20 to 80 cases planned, read them as
    effect sizes with wide uncertainty.
  - Correction frequency: from <case_id>_corrections.json when the Slicer
    export wrote one. Cases with any edit, with organ edits, with stone
    edits, and how often each organ was edited.

Usage:
    python scripts/validate.py <cases_dir> <manual.csv> <out_dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from pathlib import Path

import numpy as np

GROUPS = ("upper", "mid", "lower", "pelvis")
COLUMNS = ["case_id", "stones_source", "auto_volume_mm3", "manual_volume_mm3",
           "volume_pct_error", "top_tract_group", "top_tract_source", "operative_calyx",
           "calyx_match", "top_supracostal", "operative_supracostal", "supracostal_match",
           "reach_operative_group", "flex_reach_operative_group", "stone_free", "residual_mm",
           "corrections_any", "organs_edited", "stones_edited", "note"]


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


def _flag(value) -> int | None:
    text = str(value).strip().lower()
    if text in ("1", "true", "yes"):
        return 1
    if text in ("0", "false", "no"):
        return 0
    return None


def _number(value) -> float | None:
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _first_existing(*paths: Path) -> Path | None:
    return next((p for p in paths if p.exists()), None)


def _group_from_organs(organs_path: Path, side: str, point_mm) -> str:
    import nibabel as nib

    from stoneplanner.calyx import calyceal_group
    from stoneplanner.segment import ORGAN_LABELS

    img = nib.load(str(organs_path))
    kidney = np.asarray(img.dataobj) == ORGAN_LABELS[f"kidney_{side}"]
    return calyceal_group(np.array(point_mm, dtype=float), kidney, img.affine)


def validate_case(case_dir: Path, case_id: str, manual: dict) -> dict:
    """One validation.csv row for one case."""
    row = {k: "" for k in COLUMNS}
    row["case_id"] = case_id
    row["operative_calyx"] = manual.get("operative_calyx", "").strip().lower()
    row["operative_supracostal"] = _flag(manual.get("operative_supracostal", ""))
    stone_free = _flag(manual.get("stone_free", ""))
    row["stone_free"] = "" if stone_free is None else stone_free
    residual = 0.0 if stone_free == 1 else _number(manual.get("residual_mm", ""))
    row["residual_mm"] = "" if residual is None else residual
    notes = []
    if row["operative_calyx"] not in GROUPS:
        notes.append(f"operative_calyx {row['operative_calyx']!r} not one of {GROUPS}")

    stones_csv = _first_existing(case_dir / f"{case_id}_stones_corrected.csv",
                                 case_dir / f"{case_id}_stones.csv")
    manual_volume = manual.get("manual_stone_volume_mm3", "").strip()
    if stones_csv and manual_volume:
        auto = auto_volume(stones_csv, manual.get("laterality", ""))
        ref = float(manual_volume)
        row["stones_source"] = "corrected" if "corrected" in stones_csv.name else "automatic"
        row["auto_volume_mm3"] = round(auto, 1)
        row["manual_volume_mm3"] = ref
        row["volume_pct_error"] = round(100.0 * (auto - ref) / ref, 2) if ref else ""
    else:
        notes.append("no stones csv" if not stones_csv else "no manual volume")

    tracts_json = case_dir / f"{case_id}_tracts.json"
    organs_path = _first_existing(case_dir / f"{case_id}_organs_corrected.nii.gz",
                                  case_dir / f"{case_id}_organs.nii.gz")
    if not tracts_json.exists():
        notes.append("no tracts json")
    else:
        plan = json.loads(tracts_json.read_text())
        top = plan["tracts"][0] if plan.get("tracts") else None
        if top is None or top["collision"]:
            row["top_tract_group"] = "none"
            row["calyx_match"] = 0
            row["supracostal_match"] = 0
            notes.append("no collision-free tract")
        elif not top.get("target_group") and organs_path is None:
            notes.append("no organ map")
        else:
            group = top.get("target_group") or _group_from_organs(
                organs_path, plan["side"], top["target_xyz_mm"])
            row["top_tract_group"] = group
            row["top_tract_source"] = top.get("target_source", "")
            row["calyx_match"] = int(group == row["operative_calyx"])
            row["top_supracostal"] = int(bool(top["supracostal"]))
            if row["operative_supracostal"] is not None:
                row["supracostal_match"] = int(row["top_supracostal"]
                                               == row["operative_supracostal"])
        best = plan.get("best_by_group", {}).get(row["operative_calyx"])
        if best is not None:
            row["reach_operative_group"] = best["reachable_fraction"]
            if best.get("flexible_reachable_fraction") is not None:
                row["flex_reach_operative_group"] = best["flexible_reachable_fraction"]
        elif "best_by_group" in plan:
            notes.append(f"no clear tract to the {row['operative_calyx']} group")

    corrections = case_dir / f"{case_id}_corrections.json"
    if corrections.exists():
        record = json.loads(corrections.read_text())
        row["corrections_any"] = int(bool(record["any_edit"]))
        row["organs_edited"] = " ".join(record["organs_edited"])
        row["stones_edited"] = int(bool(record["stones"]["edited"]))

    if row["operative_supracostal"] is None:
        row["operative_supracostal"] = ""
    row["note"] = "; ".join(notes)
    return row


def residual_statistics(rows: list[dict], column: str = "reach_operative_group") -> list[str]:
    """AUC for stone-free vs residual, and Spearman's rho against residual size."""
    from scipy import stats

    lines = []
    usable = [r for r in rows if r[column] != ""]
    free = [float(r[column]) for r in usable if r["stone_free"] == 1]
    resid = [float(r[column]) for r in usable if r["stone_free"] == 0]
    if free and resid:
        u, p = stats.mannwhitneyu(free, resid, alternative="two-sided")
        auc = float(u) / (len(free) * len(resid))
        lines.append(f"  AUC stone-free vs residual: {auc:.2f} (p = {p:.3f}; "
                     f"n = {len(free)} free, {len(resid)} residual)")
    else:
        lines.append("  AUC stone-free vs residual: needs both stone-free and residual cases")
    pairs = [(float(r[column]), float(r["residual_mm"])) for r in usable
             if r["residual_mm"] != ""]
    if len(pairs) >= 3 and len({b for _, b in pairs}) > 1 and len({a for a, _ in pairs}) > 1:
        rho, p = stats.spearmanr(*zip(*pairs))
        lines.append(f"  Spearman rho vs residual_mm: {rho:.2f} (p = {p:.3f}; n = {len(pairs)})")
    else:
        lines.append("  Spearman rho vs residual_mm: needs 3 or more varied cases")
    return lines


def summarize(rows: list[dict]) -> str:
    errors = [abs(float(r["volume_pct_error"])) for r in rows if r["volume_pct_error"] != ""]
    calyx = [r["calyx_match"] for r in rows if r["calyx_match"] != ""]
    supra = [r["supracostal_match"] for r in rows if r["supracostal_match"] != ""]
    lines = [f"Cases: {len(rows)}"]
    if errors:
        within = sum(e <= 10.0 for e in errors)
        corrected = sum(r["stones_source"] == "corrected" for r in rows)
        lines += [
            f"Volume, cases with both values: {len(errors)} ({corrected} from corrected stones)",
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

    lines.append("Residual fragments, rigid reach of the best tract to the operative group:")
    lines += residual_statistics(rows, "reach_operative_group")
    lines.append("Residual fragments, flexible reach of the same tract:")
    lines += residual_statistics(rows, "flex_reach_operative_group")

    logged = [r for r in rows if r["corrections_any"] != ""]
    if logged:
        organ_counts = Counter(name for r in logged for name in r["organs_edited"].split())
        lines.append(
            f"Segmentation corrected: {sum(r['corrections_any'] for r in logged)}/{len(logged)} "
            f"cases with a correction log (organs in "
            f"{sum(bool(r['organs_edited']) for r in logged)}, stones in "
            f"{sum(r['stones_edited'] for r in logged)})")
        if organ_counts:
            lines.append("Organs edited: " + ", ".join(
                f"{name} {n}" for name, n in organ_counts.most_common()))
    else:
        lines.append("Segmentation corrected: no correction logs")

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
