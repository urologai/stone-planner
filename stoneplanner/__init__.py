"""stoneplanner: PCNL access tract planning from noncontrast CT.

Research proof of concept. Not for clinical use. Planning only, no navigation.

Pipeline (Phase 1):
    ingest   DICOM folder -> de-identified NIfTI + metadata JSON
    segment  NIfTI -> organ label map (TotalSegmentator) + stone label map + stone table
    geometry label maps -> ranked candidate tracts with reach and collision scores
"""

__version__ = "0.1.0"
