"""Ingest tests on synthetic DICOM series. No real imaging.

Each test writes a small CT series with pydicom (tests/dicom_series.py):
12 slices of 48 x 48, 0.8 mm pixels, 2.5 mm slices, LPS orientation as a
scanner writes it, with fake PHI in plain, nested, private, and file-name
locations so the de-identification can be checked byte for byte.
"""

import json
import shutil

import nibabel as nib
import numpy as np
import pydicom
import pytest
from dicom_series import (
    COLS,
    N_SLICES,
    PHI_STRINGS,
    PIXEL_MM,
    ROWS,
    SLICE_MM,
    expected_marker_ras,
    write_radiograph,
    write_series,
)

from stoneplanner import ingest

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
        for s in PHI_STRINGS + [b"1.3.6.1.4.1.99999.7.111", b"ACME PRIVATE"]:
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
    write_series(tmp_path / "two" / "other", series_uid="1.3.6.1.4.1.99999.7.999")
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


# ---------------------------------------------------------------------------
# Whole study folders (the local ingest page)


def _study(tmp_path):
    study = tmp_path / "study"
    write_series(study / "axial", series_uid="1.3.6.1.4.1.99999.7.10", series_number=3,
                 description="AXIAL 2.5")
    write_series(study / "coronal", series_uid="1.3.6.1.4.1.99999.7.11", series_number=4,
                 description="COR REFORMAT", orientation="coronal")
    write_radiograph(study / "scout", series_uid="1.3.6.1.4.1.99999.7.12")
    return study


def test_list_and_recommend_series(tmp_path):
    series = ingest.list_series(_study(tmp_path))
    by_uid = {s["series_uid"]: s for s in series}
    assert by_uid["1.3.6.1.4.1.99999.7.10"]["orientation"] == "axial"
    assert by_uid["1.3.6.1.4.1.99999.7.11"]["orientation"] == "coronal"
    assert by_uid["1.3.6.1.4.1.99999.7.12"]["kind"] == "radiograph"
    assert by_uid["1.3.6.1.4.1.99999.7.10"]["n_images"] == N_SLICES
    assert by_uid["1.3.6.1.4.1.99999.7.10"]["slice_thickness_mm"] == SLICE_MM
    assert ingest.recommend_series(series) == "1.3.6.1.4.1.99999.7.10"
    assert ingest.recommend_series(series, "radiograph") == "1.3.6.1.4.1.99999.7.12"


def test_deidentify_one_series_from_a_study(tmp_path):
    study = _study(tmp_path)
    with pytest.raises(ValueError, match="series"):
        ingest.deidentify_series(study, tmp_path / "all")
    out = ingest.deidentify_series(study, tmp_path / "deid",
                                   series_uid="1.3.6.1.4.1.99999.7.10")
    files = list(out.iterdir())
    assert len(files) == N_SLICES
    assert {pydicom.dcmread(f).ImageOrientationPatient[4] for f in files} == {1.0}


def test_radiograph_deidentified_and_flagged(tmp_path):
    src = write_radiograph(tmp_path / "kub")
    with pytest.raises(ValueError, match="CT"):
        ingest.deidentify_series(src, tmp_path / "no")
    out = ingest.deidentify_series(src, tmp_path / "deid", kinds=("radiograph",))
    (f,) = list(out.iterdir())
    raw = f.read_bytes()
    for s in (b"DOE^JOHN", b"MRN12345", b"Mercy General", b"20240402"):
        assert s not in raw
    ds = pydicom.dcmread(f)
    assert ds.Modality == "DX" and list(ds.ImagerPixelSpacing) == [0.15, 0.15]
    assert ingest.detect_burned_in_text(out, kinds=("radiograph",)) is True


def test_phantom_series_converts_onto_the_phantom_grid(tmp_path):
    from dicom_series import write_phantom_series

    from stoneplanner import phantom

    src = write_phantom_series(tmp_path / "ph")
    deid = ingest.deidentify_series(src, tmp_path / "deid")
    img = nib.load(str(ingest.convert_to_nifti(deid, tmp_path / "ph.nii.gz")))
    np.testing.assert_allclose(img.affine, phantom.AFFINE, atol=1e-4)
    ct, _, stones = phantom.make_phantom()
    ct[stones > 0] = 1000
    np.testing.assert_array_equal(np.asarray(img.dataobj), ct.astype(np.int16))
