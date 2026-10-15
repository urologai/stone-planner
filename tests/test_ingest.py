"""Ingest tests on synthetic DICOM series. No real imaging.

Each test writes a small CT series with pydicom: 12 slices of 48 x 48,
0.8 mm pixels, 2.5 mm slices, LPS orientation as a scanner writes it. The
headers are loaded with fake PHI in plain, nested, private, and file-name
locations so the de-identification can be checked byte for byte.
"""

import json
import shutil

import nibabel as nib
import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from stoneplanner import ingest

ROWS = COLS = 48
N_SLICES = 12
PIXEL_MM = 0.8
SLICE_MM = 2.5
IPP0 = np.array([-19.2, -19.2, -100.0])     # LPS mm of the first voxel of slice 0
MARKER = (30, 10, 7)                         # row, column, slice of a 1000 HU voxel

PHI_STRINGS = [b"DOE^JOHN", b"MRN12345", b"Mercy General", b"ACC998877",
               b"HOUSE^GREGORY", b"19500102", b"20240315", b"101500", b"CT ABD DOE"]


def _slice_hu(k, burned=False):
    r, c = np.mgrid[:ROWS, :COLS]
    hu = np.where((r - 24) ** 2 + (c - 24) ** 2 <= 20 ** 2, 40, -1000).astype(np.int16)
    if k == MARKER[2]:
        hu[MARKER[0], MARKER[1]] = 1000
    if burned:
        for j in range(6):                  # a row of glyph-sized bright blocks
            hu[1:6, 2 + 7 * j: 5 + 7 * j] = 3000
    return hu


def write_series(folder, burned=False, modality="CT", series_uid=None, tag_burned=False):
    folder.mkdir(parents=True, exist_ok=True)
    study_uid = "1.2.826.0.1.3680043.8.498.111"
    series_uid = series_uid or "1.2.826.0.1.3680043.8.498.222"
    frame_uid = "1.2.826.0.1.3680043.8.498.333"
    for k in range(N_SLICES):
        ds = Dataset()
        ds.SOPClassUID = ingest.CT_IMAGE_STORAGE
        ds.SOPInstanceUID = generate_uid()
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.FrameOfReferenceUID = frame_uid
        ds.Modality = modality
        ds.PatientName = "DOE^JOHN"
        ds.PatientID = "MRN12345"
        ds.PatientBirthDate = "19500102"
        ds.InstitutionName = "Mercy General"
        ds.AccessionNumber = "ACC998877"
        ds.ReferringPhysicianName = "HOUSE^GREGORY"
        ds.StudyDate = ds.SeriesDate = ds.AcquisitionDate = "20240315"
        ds.StudyTime = "101500"
        ds.StudyDescription = "CT ABD DOE"
        ds.Manufacturer = "ACME"
        ds.ManufacturerModelName = "Scanner 9000"
        ds.KVP = "120"
        ds.SliceThickness = str(SLICE_MM)
        ds.PatientPosition = "FFS"
        ds.InstanceNumber = N_SLICES - k              # reversed, to test sorting
        ds.ImagePositionPatient = [float(v) for v in IPP0 + [0, 0, k * SLICE_MM]]
        ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.PixelSpacing = [PIXEL_MM, PIXEL_MM]
        ds.Rows, ds.Columns = ROWS, COLS
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
        ds.PixelRepresentation = 1
        ds.RescaleIntercept, ds.RescaleSlope = "-1024", "1"
        if tag_burned:
            ds.BurnedInAnnotation = "YES"
        ds.PixelData = (_slice_hu(k, burned) + 1024).astype(np.int16).tobytes()

        ds.add_new(0x00090010, "LO", "ACME PRIVATE")
        ds.add_new(0x00091001, "LO", "MRN12345")
        item = Dataset()
        item.RequestedProcedureID = "MRN12345"
        item.ScheduledProcedureStepID = "ACC998877"
        ds.RequestAttributesSequence = Sequence([item])

        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = ds.SOPClassUID
        meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.file_meta = meta
        ds.save_as(folder / f"MRN12345_{k}.dcm", enforce_file_format=True)
    return folder


def expected_marker_ras():
    r, c, k = MARKER
    lps = IPP0 + np.array([c * PIXEL_MM, r * PIXEL_MM, k * SLICE_MM])
    return np.array([-lps[0], -lps[1], lps[2]])


# ---------------------------------------------------------------------------
# De-identification


def test_deidentify_removes_phi(tmp_path):
    src = write_series(tmp_path / "src")
    out = ingest.deidentify_series(src, tmp_path / "deid")
    files = sorted(out.iterdir())
    assert len(files) == N_SLICES
    assert all("MRN" not in f.name for f in files)
    for f in files:
        raw = f.read_bytes()
        for s in PHI_STRINGS + [b"1.2.826.0.1.3680043.8.498.111", b"ACME PRIVATE"]:
            assert s not in raw, (f.name, s)
        ds = pydicom.dcmread(f)
        assert ds.PatientIdentityRemoved == "YES"
        assert ds.StudyDate == "20240101"
        assert "PatientBirthDate" not in ds
        assert "StudyTime" not in ds
        assert not any(elem.VR == "SQ" for elem in ds)
        assert not any(elem.tag.is_private for elem in ds)


def test_deidentify_keeps_geometry_pixels_and_consistent_uids(tmp_path):
    src = write_series(tmp_path / "src")
    out = ingest.deidentify_series(src, tmp_path / "deid")
    before = sorted((pydicom.dcmread(p) for p in src.iterdir()), key=ingest._slice_key)
    after = [pydicom.dcmread(p) for p in sorted(out.iterdir())]
    assert len({ds.StudyInstanceUID for ds in after}) == 1
    assert len({ds.SeriesInstanceUID for ds in after}) == 1
    assert len({ds.SOPInstanceUID for ds in after}) == N_SLICES
    assert after[0].StudyInstanceUID != before[0].StudyInstanceUID
    for a, b in zip(after, before):
        np.testing.assert_array_equal(a.pixel_array, b.pixel_array)
        assert list(a.ImagePositionPatient) == list(b.ImagePositionPatient)
        assert a.file_meta.MediaStorageSOPInstanceUID == a.SOPInstanceUID
        assert a.KVP == b.KVP and a.PatientPosition == "FFS"


def test_refuses_mixed_series_and_non_ct(tmp_path):
    folder = write_series(tmp_path / "two")
    write_series(tmp_path / "two" / "other", series_uid="1.2.826.0.1.3680043.8.498.999")
    with pytest.raises(ValueError, match="2 series"):
        ingest.deidentify_series(folder, tmp_path / "deid")
    with pytest.raises(ValueError, match="CT"):
        ingest.deidentify_series(write_series(tmp_path / "mr", modality="MR"), tmp_path / "d2")


# ---------------------------------------------------------------------------
# Burned-in annotation


def test_burned_in_clean_series(tmp_path):
    assert ingest.detect_burned_in_text(write_series(tmp_path / "clean")) is False


def test_burned_in_text_pixels(tmp_path):
    assert ingest.detect_burned_in_text(write_series(tmp_path / "text", burned=True)) is True


def test_burned_in_tag(tmp_path):
    assert ingest.detect_burned_in_text(write_series(tmp_path / "tag", tag_burned=True)) is True


# ---------------------------------------------------------------------------
# Conversion


def _check_nifti(path):
    img = nib.load(str(path))
    data = np.asarray(img.dataobj)
    assert data.dtype == np.int16
    assert set(np.unique(data)) == {-1000, 40, 1000}
    assert nib.aff2axcodes(img.affine) == ("R", "A", "S")
    np.testing.assert_allclose(img.header.get_zooms()[:3], (PIXEL_MM, PIXEL_MM, SLICE_MM),
                               atol=1e-4)
    marker = np.argwhere(data == 1000)
    assert len(marker) == 1
    ras = img.affine[:3, :3] @ marker[0] + img.affine[:3, 3]
    np.testing.assert_allclose(ras, expected_marker_ras(), atol=1e-3)


def test_convert_simpleitk_fallback(tmp_path, monkeypatch):
    src = ingest.deidentify_series(write_series(tmp_path / "src"), tmp_path / "deid")
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None)
    _check_nifti(ingest.convert_to_nifti(src, tmp_path / "out" / "case.nii.gz"))


@pytest.mark.skipif(shutil.which("dcm2niix") is None, reason="dcm2niix not installed")
def test_convert_dcm2niix(tmp_path):
    src = ingest.deidentify_series(write_series(tmp_path / "src"), tmp_path / "deid")
    _check_nifti(ingest.convert_to_nifti(src, tmp_path / "out" / "case.nii.gz"))


# ---------------------------------------------------------------------------
# Metadata and CLI


def test_metadata(tmp_path):
    src = ingest.deidentify_series(write_series(tmp_path / "src"), tmp_path / "deid")
    nifti = ingest.convert_to_nifti(src, tmp_path / "case_001.nii.gz")
    out = tmp_path / "case_001.json"
    ingest.write_metadata(src, nifti, "case_001", out)
    text = out.read_text()
    meta = json.loads(text)
    assert meta["case_id"] == "case_001"
    assert meta["acquisition_year"] == 2024
    assert meta["matrix_size"] == [COLS, ROWS, N_SLICES]
    assert meta["voxel_spacing_mm"] == pytest.approx([PIXEL_MM, PIXEL_MM, SLICE_MM])
    assert meta["slice_thickness_mm"] == SLICE_MM
    assert meta["kvp"] == 120.0
    assert meta["manufacturer"] == "ACME"
    assert meta["patient_position"] == "FFS"
    assert meta["burned_in_annotation_suspected"] is False
    assert meta["contrast_agent_tag_present"] is False
    for s in PHI_STRINGS:
        assert s.decode() not in text


def test_main_end_to_end(tmp_path, capsys):
    src = write_series(tmp_path / "src", burned=True)
    out_dir = tmp_path / "out"
    assert ingest.main([str(src), str(out_dir), "--case-id", "case_007"]) == 0
    assert sorted(p.name for p in out_dir.iterdir()) == ["case_007.json", "case_007.nii.gz"]
    assert json.loads((out_dir / "case_007.json").read_text())["burned_in_annotation_suspected"]
    assert "BURNED-IN" in capsys.readouterr().out


def test_main_refuses_mrn_like_case_id(tmp_path):
    src = write_series(tmp_path / "src")
    with pytest.raises(SystemExit):
        ingest.main([str(src), str(tmp_path / "out"), "--case-id", "12345678"])
