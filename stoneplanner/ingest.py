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

Implementation notes (how the above is made concrete):

  - De-identification is an allowlist, which removes a superset of the
    PS3.15 basic profile. Only the top-level attributes in KEEP_KEYWORDS are
    copied: geometry, pixel encoding, rescale, scanner make and model, kVp,
    kernel, patient position, contrast agent, burned-in flag, and the UIDs.
    Every sequence, private tag, overlay, name, ID, address, description and
    comment is dropped, so PHI hidden in nested or vendor fields cannot come
    through. Dates become YYYY0101; times are dropped. Study, series, frame
    of reference and SOP instance UIDs are regenerated, consistently across
    the series. PatientIdentityRemoved = YES and DeidentificationMethod are
    set. Output files are renamed 00001.dcm, 00002.dcm, ... so original file
    names, which can carry MRNs, do not survive.
  - Only single-frame CT Image Storage is accepted. A folder holding more
    than one series is refused, not guessed at.
  - Burned-in heuristic: in the top and bottom bands of each slice, count
    small, short connected blobs above BURNED_IN_HU. Text renders as a row
    of such blobs; anatomy at the edge of the field of view does not. Pixel
    data that cannot be decoded counts as suspicious.
  - Conversion runs on the de-identified copy. The NIfTI is rebuilt from
    data and affine with nibabel, reoriented to the closest RAS axes, and
    stored as int16 when the HU values are whole numbers in range, so no
    header text from the converter carries over and HU are unscaled.
  - The CLI refuses a case_id that does not start with a letter, so a bare
    MRN or accession number cannot be passed by mistake.
  - For the local ingest page (stoneplanner_api.library): list_series reads
    a whole study export and recommend_series picks the thin axial CT;
    deidentify_series takes the chosen series_uid. A postoperative KUB
    (CR or DX radiograph) can be de-identified with kinds=("radiograph",);
    it is kept as de-identified DICOM, not converted, and its burned-in
    check always reports suspected, because the HU heuristic does not
    apply to plain films.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

CT_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.2"
# Plain radiographs, for a postoperative KUB: computed and digital X-ray.
RADIOGRAPH_STORAGE = {
    "1.2.840.10008.5.1.4.1.1.1": "CR",
    "1.2.840.10008.5.1.4.1.1.1.1": "DX",
    "1.2.840.10008.5.1.4.1.1.1.1.1": "DX",
}
DEID_METHOD = "stoneplanner allowlist (superset of DICOM PS3.15 basic profile)"
CASE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

KEEP_KEYWORDS: tuple[str, ...] = (
    "SpecificCharacterSet", "ImageType", "SOPClassUID", "SOPInstanceUID",
    "StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate",
    "Modality", "Manufacturer", "ManufacturerModelName",
    "ContrastBolusAgent", "SliceThickness", "KVP", "SpacingBetweenSlices",
    "DataCollectionDiameter", "ReconstructionDiameter", "GantryDetectorTilt",
    "ConvolutionKernel", "PatientPosition",
    "StudyInstanceUID", "SeriesInstanceUID", "SeriesNumber", "AcquisitionNumber",
    "InstanceNumber", "ImagePositionPatient", "ImageOrientationPatient",
    "FrameOfReferenceUID", "SliceLocation",
    "SamplesPerPixel", "PhotometricInterpretation", "Rows", "Columns", "PixelSpacing",
    "BitsAllocated", "BitsStored", "HighBit", "PixelRepresentation", "PixelPaddingValue",
    "WindowCenter", "WindowWidth", "RescaleIntercept", "RescaleSlope", "RescaleType",
    "BurnedInAnnotation", "LossyImageCompression", "PixelData",
    # Radiograph geometry and display, for a postoperative KUB.
    "ImagerPixelSpacing", "PixelIntensityRelationship", "PixelIntensityRelationshipSign",
    "PresentationLUTShape", "PresentationIntentType", "ViewPosition",
)
_DATE_KEYWORDS = ("StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate")
_REGENERATED_UIDS = ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID",
                     "FrameOfReferenceUID")

BURNED_IN_BAND_ROWS = 40
BURNED_IN_HU = 2000.0
BURNED_IN_MIN_BLOBS = 4
_BLOB_MAX_AREA = 400
_BLOB_MAX_HEIGHT = 25


# ---------------------------------------------------------------------------
# Reading


def series_kind(ds) -> str:
    """ct (single-frame CT), radiograph (CR/DX), or other."""
    sop = str(ds.get("SOPClassUID", ""))
    if sop == CT_IMAGE_STORAGE and ds.get("Modality") == "CT":
        return "ct"
    if sop in RADIOGRAPH_STORAGE:
        return "radiograph"
    return "other"


def _read_all(dicom_dir: Path, stop_before_pixels: bool) -> list:
    import pydicom
    from pydicom.errors import InvalidDicomError

    datasets = []
    for path in sorted(Path(dicom_dir).rglob("*")):
        if not path.is_file() or path.name.upper() == "DICOMDIR":
            continue
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=stop_before_pixels)
        except (InvalidDicomError, OSError):
            continue
        if "SOPClassUID" not in ds:
            continue
        if not stop_before_pixels and "PixelData" not in ds:
            continue
        datasets.append(ds)
    if not datasets:
        raise ValueError(f"no DICOM images found under {dicom_dir}")
    return datasets


def _read_series(dicom_dir: Path, stop_before_pixels: bool = False,
                 series_uid: str | None = None, kinds: tuple[str, ...] = ("ct",)) -> list:
    """Image datasets of one series under dicom_dir, sorted along the scan.

    With series_uid, only that series is read from a folder that may hold
    several; without it, the folder must hold exactly one series.
    """
    datasets = _read_all(dicom_dir, stop_before_pixels)
    if series_uid is not None:
        datasets = [ds for ds in datasets if ds.get("SeriesInstanceUID") == series_uid]
        if not datasets:
            raise ValueError("the chosen series is not in the uploaded files")
    series = {ds.get("SeriesInstanceUID") for ds in datasets}
    if len(series) != 1:
        raise ValueError(f"{dicom_dir} holds {len(series)} series; pass one series per call")
    for ds in datasets:
        if series_kind(ds) not in kinds:
            wanted = "single-frame CT Image Storage" if kinds == ("ct",) else "/".join(kinds)
            raise ValueError(f"expected {wanted}, got {ds.get('Modality')} / {ds.SOPClassUID}")
    return sorted(datasets, key=_slice_key)


def _orientation(ds) -> str:
    try:
        iop = np.asarray(ds.ImageOrientationPatient, dtype=float)
    except (AttributeError, ValueError):
        return "unknown"
    normal = np.abs(np.cross(iop[:3], iop[3:]))
    if normal.max() < 0.9:
        return "oblique"
    return ("sagittal", "coronal", "axial")[int(np.argmax(normal))]


def list_series(dicom_dir: Path) -> list[dict]:
    """Every series in a folder (a whole study export is fine), headers only.

    Each entry: series_uid, series_number, description, modality, kind
    (ct / radiograph / other), n_images, slice_thickness_mm, orientation,
    usable (a CT or radiograph this module can take). Descriptions can hold
    free text, so the list is for local display only; nothing here is stored.
    """
    groups: dict[str, list] = {}
    for ds in _read_all(dicom_dir, stop_before_pixels=True):
        groups.setdefault(str(ds.get("SeriesInstanceUID", "")), []).append(ds)
    out = []
    for uid, items in groups.items():
        first = items[0]
        kind = series_kind(first)
        thickness = first.get("SliceThickness")
        out.append({
            "series_uid": uid,
            "series_number": int(first.get("SeriesNumber", 0) or 0),
            "description": str(first.get("SeriesDescription", "") or ""),
            "modality": str(first.get("Modality", "") or ""),
            "kind": kind,
            "n_images": len(items),
            "slice_thickness_mm": float(thickness) if thickness not in (None, "") else None,
            "orientation": _orientation(first) if kind == "ct" else "projection",
            "usable": kind in ("ct", "radiograph"),
        })
    return sorted(out, key=lambda e: (e["series_number"], e["description"]))


def recommend_series(series: list[dict], kind: str = "ct") -> str | None:
    """The series to use: for CT the axial one with most images, thinnest first."""
    pool = [e for e in series if e["kind"] == kind]
    if kind == "ct":
        axial = [e for e in pool if e["orientation"] == "axial" and e["n_images"] >= 10]
        pool = axial or pool
        pool.sort(key=lambda e: (-e["n_images"], e["slice_thickness_mm"] or 99.0))
    return pool[0]["series_uid"] if pool else None


def _slice_key(ds) -> tuple:
    """Order by position along the slice normal, then instance number."""
    try:
        iop = np.asarray(ds.ImageOrientationPatient, dtype=float)
        normal = np.cross(iop[:3], iop[3:])
        along = float(np.dot(normal, np.asarray(ds.ImagePositionPatient, dtype=float)))
    except (AttributeError, ValueError):
        along = 0.0
    return along, int(ds.get("InstanceNumber", 0) or 0)


def _hu(ds) -> np.ndarray:
    slope = float(ds.get("RescaleSlope", 1) or 1)
    intercept = float(ds.get("RescaleIntercept", 0) or 0)
    return ds.pixel_array.astype(np.float32) * slope + intercept


# ---------------------------------------------------------------------------
# De-identification


def deidentify_series(dicom_dir: Path, work_dir: Path, series_uid: str | None = None,
                      kinds: tuple[str, ...] = ("ct",)) -> Path:
    """Copy the series into work_dir with PHI tags removed. Return the new dir.

    series_uid picks one series from a folder holding several; kinds may add
    "radiograph" for a postoperative KUB.
    """
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import generate_uid

    datasets = _read_series(dicom_dir, series_uid=series_uid, kinds=kinds)
    out_dir = Path(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    uid_map: dict[str, str] = {}

    def new_uid(old: str) -> str:
        if old not in uid_map:
            uid_map[old] = generate_uid()
        return uid_map[old]

    for n, src in enumerate(datasets, start=1):
        dst = Dataset()
        for keyword in KEEP_KEYWORDS:
            if keyword in src:
                dst[keyword] = src[keyword]
        for keyword in _DATE_KEYWORDS:
            if keyword in dst and dst[keyword].value:
                dst[keyword].value = f"{str(dst[keyword].value)[:4]}0101"
        for keyword in _REGENERATED_UIDS:
            if keyword in dst and dst[keyword].value:
                setattr(dst, keyword, new_uid(str(dst[keyword].value)))
        dst.PatientName = ""
        dst.PatientID = ""
        dst.PatientIdentityRemoved = "YES"
        dst.DeidentificationMethod = DEID_METHOD
        dst.LongitudinalTemporalInformationModified = "MODIFIED"

        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = dst.SOPClassUID
        meta.MediaStorageSOPInstanceUID = dst.SOPInstanceUID
        meta.TransferSyntaxUID = src.file_meta.TransferSyntaxUID
        dst.file_meta = meta
        dst.save_as(out_dir / f"{n:05d}.dcm", enforce_file_format=True)
    return out_dir


# ---------------------------------------------------------------------------
# Burned-in annotation


def _text_like_blobs(band: np.ndarray) -> int:
    from scipy import ndimage

    lab, n = ndimage.label(band >= BURNED_IN_HU, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return 0
    count = 0
    for sl in ndimage.find_objects(lab):
        height = sl[0].stop - sl[0].start
        area = int(np.count_nonzero(lab[sl]))
        if 3 <= area <= _BLOB_MAX_AREA and height <= _BLOB_MAX_HEIGHT:
            count += 1
    return count


def detect_burned_in_text(dicom_dir: Path, kinds: tuple[str, ...] = ("ct",)) -> bool:
    """Return True if any slice looks like it carries burned-in annotation.

    Radiographs always return True: their pixel values are not HU, so the
    text heuristic cannot clear them, and plain films often carry burned-in
    markers or labels. They need a look by eye.
    """
    for ds in _read_series(dicom_dir, kinds=kinds):
        if str(ds.get("BurnedInAnnotation", "")).upper() == "YES":
            return True
        if series_kind(ds) == "radiograph":
            return True
        try:
            hu = _hu(ds)
        except Exception:  # noqa: BLE001  undecodable pixels cannot be cleared
            return True
        rows = min(BURNED_IN_BAND_ROWS, hu.shape[0] // 4)
        for band in (hu[:rows], hu[-rows:]):
            if _text_like_blobs(band) >= BURNED_IN_MIN_BLOBS:
                return True
    return False


# ---------------------------------------------------------------------------
# Conversion


def find_dcm2niix() -> str | None:
    """dcm2niix on PATH, or next to the running Python.

    The pip package installs it beside the interpreter (.venv/bin), which is
    not on PATH when the planner is started by launchd rather than a shell.
    """
    found = shutil.which("dcm2niix")
    if found:
        return found
    beside = Path(sys.executable).parent / "dcm2niix"
    return str(beside) if beside.is_file() and os.access(beside, os.X_OK) else None


def _dcm2niix(dicom_dir: Path, exe: str = "dcm2niix"):
    import nibabel as nib

    with tempfile.TemporaryDirectory() as tmp:
        cmd = [exe, "-z", "n", "-b", "n", "-f", "series", "-o", tmp, str(dicom_dir)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        outputs = sorted(Path(tmp).glob("*.nii"))
        if result.returncode != 0 or not outputs:
            raise RuntimeError(f"dcm2niix failed ({result.returncode}): {result.stderr.strip()}")
        # Gantry tilt or uneven spacing makes dcm2niix write a corrected copy too.
        corrected = [p for p in outputs if "_Tilt" in p.name or "_Eq" in p.name]
        chosen = corrected or outputs
        if len(chosen) != 1:
            raise RuntimeError(f"dcm2niix wrote {len(outputs)} volumes: "
                               f"{[p.name for p in outputs]}")
        img = nib.load(str(chosen[0]))
        return nib.Nifti1Image(np.asarray(img.get_fdata(dtype=np.float32)), img.affine)


def _simpleitk(dicom_dir: Path):
    import nibabel as nib
    import SimpleITK as sitk

    reader = sitk.ImageSeriesReader()
    series = reader.GetGDCMSeriesIDs(str(dicom_dir))
    if len(series) != 1:
        raise RuntimeError(f"SimpleITK found {len(series)} series in {dicom_dir}")
    image = sitk.ReadImage(reader.GetGDCMSeriesFileNames(str(dicom_dir), series[0]),
                           sitk.sitkFloat32)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "series.nii"
        sitk.WriteImage(image, str(path))   # ITK writes the RAS affine NIfTI expects
        img = nib.load(str(path))
        return nib.Nifti1Image(np.asarray(img.get_fdata(dtype=np.float32)), img.affine)


def _canonical_hu(img):
    """Closest-RAS axes, HU as int16 when lossless, fresh header."""
    import nibabel as nib

    canon = nib.as_closest_canonical(img)
    data = np.asarray(canon.get_fdata(dtype=np.float32))
    info = np.iinfo(np.int16)
    if np.all(np.round(data) == data) and data.min() >= info.min and data.max() <= info.max:
        data = data.astype(np.int16)
    out = nib.Nifti1Image(data, canon.affine)
    out.set_qform(canon.affine, code=1)
    out.set_sform(canon.affine, code=1)
    out.header.set_xyzt_units("mm", "sec")
    return out


def convert_to_nifti(dicom_dir: Path, out_path: Path) -> Path:
    """Run dcm2niix (or SimpleITK fallback). Return path to the .nii.gz."""
    import nibabel as nib

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exe = find_dcm2niix()
    img = _dcm2niix(dicom_dir, exe) if exe else _simpleitk(dicom_dir)
    nib.save(_canonical_hu(img), str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# Metadata


def _year(ds) -> int | None:
    for keyword in ("AcquisitionDate", "SeriesDate", "StudyDate"):
        value = str(ds.get(keyword, "") or "")
        if len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
    return None


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_metadata(dicom_dir: Path, nifti_path: Path, case_id: str, out_path: Path,
                   burned_in: bool | None = None) -> None:
    """Write the non-identifying metadata JSON described in the module docstring.

    burned_in is the result of detect_burned_in_text; it is computed if not given.
    """
    import nibabel as nib

    from stoneplanner import __version__

    datasets = _read_series(dicom_dir, stop_before_pixels=True)
    first = datasets[0]
    img = nib.load(str(nifti_path))
    if burned_in is None:
        burned_in = detect_burned_in_text(dicom_dir)
    meta = {
        "case_id": case_id,
        "voxel_spacing_mm": [round(float(z), 4) for z in img.header.get_zooms()[:3]],
        "slice_thickness_mm": _number(first.get("SliceThickness")),
        "matrix_size": [int(n) for n in img.shape[:3]],
        "n_slices": len(datasets),
        "manufacturer": str(first.get("Manufacturer", "") or "") or None,
        "manufacturer_model": str(first.get("ManufacturerModelName", "") or "") or None,
        "kvp": _number(first.get("KVP")),
        "patient_position": str(first.get("PatientPosition", "") or "") or None,
        "acquisition_year": _year(first),
        "gantry_tilt_deg": _number(first.get("GantryDetectorTilt")),
        "contrast_agent_tag_present": bool(str(first.get("ContrastBolusAgent", "") or "")),
        "burned_in_annotation_suspected": bool(burned_in),
        "deidentification": DEID_METHOD,
        "stoneplanner_version": __version__,
    }
    Path(out_path).write_text(json.dumps(meta, indent=2))


# ---------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dicom_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--case-id", required=True)
    args = parser.parse_args(argv)
    if not CASE_ID_PATTERN.match(args.case_id):
        parser.error("case_id must start with a letter and use only letters, digits, _ or - "
                     "(a bare MRN or accession number is refused)")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    nifti_path = args.out_dir / f"{args.case_id}.nii.gz"
    json_path = args.out_dir / f"{args.case_id}.json"
    with tempfile.TemporaryDirectory() as tmp:
        deid_dir = deidentify_series(args.dicom_dir, Path(tmp) / "deid")
        burned_in = detect_burned_in_text(deid_dir)
        convert_to_nifti(deid_dir, nifti_path)
        write_metadata(deid_dir, nifti_path, args.case_id, json_path, burned_in=burned_in)
    flag = "  BURNED-IN ANNOTATION SUSPECTED: review before use" if burned_in else ""
    print(f"{nifti_path}, {json_path}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
