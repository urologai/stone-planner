"""De-identify a DICOM CT series and convert it to NIfTI.

Input:  a directory containing one axial noncontrast CT series as DICOM.
Output: <out_dir>/<case_id>.nii.gz and <out_dir>/<case_id>.json

The JSON holds what downstream steps need and nothing identifying:
    case_id, voxel spacing (x, y, z mm), slice thickness, matrix size,
    manufacturer and model (useful for protocol variation), kVp if present,
    patient position tag (expect HFS or FFS; record it), acquisition year only.

De-identification:
    - Strip every PHI tag per the DICOM PS3.15 basic profile (PatientName,
      PatientID, PatientBirthDate, all dates to year only, institution,
      referring physician, accession, study/series UIDs regenerated, etc.).
    - Check for burned-in annotation: BurnedInAnnotation tag, and a heuristic
      scan of the top and bottom ~40 rows of each slice for high-contrast text
      blocks. Flag the case in the JSON if suspicious; do not try to redact
      pixels automatically in Phase 1.
    - The MRN-to-case_id key is NEVER written here. It lives outside the repo.

Conversion:
    Prefer dcm2niix (subprocess) because it handles gantry tilt and orientation
    correctly. Fall back to SimpleITK ImageSeriesReader if dcm2niix is absent.
    Output must be RAS-oriented with HU values intact (no rescale loss).

CLI:
    stoneplanner-ingest <dicom_dir> <out_dir> --case-id case_001

Phase 1 scope: one case per call. Batch wrapper can come later.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def deidentify_series(dicom_dir: Path, work_dir: Path) -> Path:
    """Copy the series into work_dir with PHI tags removed. Return the new dir."""
    raise NotImplementedError


def detect_burned_in_text(dicom_dir: Path) -> bool:
    """Return True if any slice looks like it carries burned-in annotation."""
    raise NotImplementedError


def convert_to_nifti(dicom_dir: Path, out_path: Path) -> Path:
    """Run dcm2niix (or SimpleITK fallback). Return path to the .nii.gz."""
    raise NotImplementedError


def write_metadata(dicom_dir: Path, nifti_path: Path, case_id: str, out_path: Path) -> None:
    """Write the non-identifying metadata JSON described in the module docstring."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dicom_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--case-id", required=True)
    args = parser.parse_args(argv)
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
