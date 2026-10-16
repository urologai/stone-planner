"""Synthetic DICOM writers for tests. No real imaging.

write_series: a small CT series (12 slices of 48 x 48, 0.8 mm pixels, 2.5 mm
slices, LPS orientation as a scanner writes it) with fake PHI planted in
plain, nested, private and file-name locations, so de-identification can be
checked byte for byte. Axial by default; orientation="coronal" writes a
reformat-like series.

write_radiograph: a one-image DX "KUB" with the same fake PHI.

write_phantom_series: stoneplanner.phantom's CT (80^3, 1 mm, stone at 1000 HU)
as an axial DICOM series, for tests that ingest and then plan.
"""

import numpy as np
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


def write_series(folder, burned=False, modality="CT", series_uid=None, tag_burned=False,
                 orientation="axial", series_number=3, description="CT ABD DOE"):
    folder.mkdir(parents=True, exist_ok=True)
    # Planted UIDs use a root pydicom's generate_uid() never produces, so a new
    # random UID cannot contain one by chance.
    study_uid = "1.3.6.1.4.1.99999.7.111"
    series_uid = series_uid or "1.3.6.1.4.1.99999.7.222"
    frame_uid = "1.3.6.1.4.1.99999.7.333"
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
        ds.SeriesDescription = description
        ds.Manufacturer = "ACME"
        ds.ManufacturerModelName = "Scanner 9000"
        ds.KVP = "120"
        ds.SliceThickness = str(SLICE_MM)
        ds.PatientPosition = "FFS"
        ds.InstanceNumber = N_SLICES - k              # reversed, to test sorting
        if orientation == "coronal":
            ds.ImagePositionPatient = [float(v) for v in IPP0 + [0, k * SLICE_MM, 0]]
            ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 0.0, -1.0]
        else:
            ds.ImagePositionPatient = [float(v) for v in IPP0 + [0, 0, k * SLICE_MM]]
            ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.SeriesNumber = series_number
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
        ds.save_as(folder / f"MRN12345_{series_number}_{k}.dcm", enforce_file_format=True)
    return folder


def expected_marker_ras():
    r, c, k = MARKER
    lps = IPP0 + np.array([c * PIXEL_MM, r * PIXEL_MM, k * SLICE_MM])
    return np.array([-lps[0], -lps[1], lps[2]])




def write_radiograph(folder, series_uid="1.3.6.1.4.1.99999.7.444"):
    """One DX image standing in for a postoperative KUB, with planted PHI."""
    folder.mkdir(parents=True, exist_ok=True)
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.1.1"
    ds.SOPInstanceUID = generate_uid()
    ds.StudyInstanceUID = "1.3.6.1.4.1.99999.7.555"
    ds.SeriesInstanceUID = series_uid
    ds.Modality = "DX"
    ds.PatientName = "DOE^JOHN"
    ds.PatientID = "MRN12345"
    ds.InstitutionName = "Mercy General"
    ds.StudyDate = "20240402"
    ds.SeriesNumber = 1
    ds.SeriesDescription = "KUB AP"
    ds.ImagerPixelSpacing = [0.15, 0.15]
    ds.ViewPosition = "AP"
    ds.Rows, ds.Columns = 64, 48
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 12, 11
    ds.PixelRepresentation = 0
    ds.PixelData = (np.arange(64 * 48, dtype=np.uint16) % 4096).tobytes()
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = ds.SOPClassUID
    meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = meta
    ds.save_as(folder / "MRN12345_kub.dcm", enforce_file_format=True)
    return folder


# DICOM grid for the phantom: voxel (i, j, k) -> RAS (39 - i, 39 - j, k - 40),
# i.e. LPS image position (-39, -39, -40) with identity orientation.
PHANTOM_IPP = np.array([-39.0, -39.0, -40.0])
PHANTOM_DICOM_AFFINE = np.array([
    [-1.0, 0.0, 0.0, 39.0],
    [0.0, -1.0, 0.0, 39.0],
    [0.0, 0.0, 1.0, -40.0],
    [0.0, 0.0, 0.0, 1.0],
])


def write_phantom_series(folder, series_uid="1.3.6.1.4.1.99999.7.666"):
    """The planner phantom's CT as an axial DICOM series, stone at 1000 HU."""
    from stoneplanner import phantom

    ct, _, stones = phantom.make_phantom(affine=PHANTOM_DICOM_AFFINE)
    ct[stones > 0] = 1000
    folder.mkdir(parents=True, exist_ok=True)
    n = ct.shape[2]
    for k in range(n):
        ds = Dataset()
        ds.SOPClassUID = ingest.CT_IMAGE_STORAGE
        ds.SOPInstanceUID = generate_uid()
        ds.StudyInstanceUID = "1.3.6.1.4.1.99999.7.777"
        ds.SeriesInstanceUID = series_uid
        ds.FrameOfReferenceUID = "1.3.6.1.4.1.99999.7.888"
        ds.Modality = "CT"
        ds.PatientName = "DOE^JOHN"
        ds.PatientID = "MRN12345"
        ds.StudyDate = ds.AcquisitionDate = "20240315"
        ds.SeriesNumber = 2
        ds.SeriesDescription = "AXIAL 1MM"
        ds.SliceThickness = "1.0"
        ds.PatientPosition = "FFS"
        ds.InstanceNumber = k + 1
        ds.ImagePositionPatient = [float(v) for v in PHANTOM_IPP + [0, 0, k]]
        ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.PixelSpacing = [1.0, 1.0]
        ds.Rows, ds.Columns = ct.shape[1], ct.shape[0]
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
        ds.PixelRepresentation = 1
        ds.RescaleIntercept, ds.RescaleSlope = "-1024", "1"
        ds.PixelData = (ct[:, :, k].T + 1024).astype(np.int16).tobytes()   # rows = j
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = ds.SOPClassUID
        meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.file_meta = meta
        ds.save_as(folder / f"img_{k:03d}.dcm", enforce_file_format=True)
    return folder
