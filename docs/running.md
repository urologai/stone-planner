# Running Phase 1

How to take the 20 cases from DICOM to the validation report. Imaging and outputs stay on institutional storage, never in this repo.

## Install

Python 3.10 or later, in a virtual environment:

```
pip install -e ".[dev]"
```

This pulls in TotalSegmentator and PyTorch. dcm2niix is optional but preferred for conversion (`pip install dcm2niix` or your package manager); without it SimpleITK is used. If your DICOM is JPEG-compressed, also install `pylibjpeg pylibjpeg-libjpeg pylibjpeg-openjpeg`, or every compressed series is flagged as possibly carrying burned-in text, because the check cannot read its pixels.

Run the tests once: `pytest`. They use synthetic phantoms only.

## Per case

Pick a case ID that starts with a letter (`case_001`). The MRN-to-case key lives outside the repo.

```
stoneplanner-ingest   /path/dicom/series   cases/case_001 --case-id case_001
stoneplanner-segment  cases/case_001/case_001.nii.gz   cases/case_001 [--device cpu|cuda|mps]
stoneplanner-plan     cases/case_001/case_001.nii.gz   cases/case_001/case_001_organs.nii.gz \
                      cases/case_001/case_001_stones.nii.gz   cases/case_001 [--side left|right|auto]
```

Outputs in `cases/case_001/`:

- `case_001.nii.gz`, `case_001.json`: de-identified CT and metadata. If ingest prints `BURNED-IN ANNOTATION SUSPECTED`, look at the images before going further.
- `case_001_organs.nii.gz`, `case_001_stones.nii.gz`, `case_001_stones.csv`: segmentation.
- `case_001_ts_total.nii.gz`, `case_001_ts_body.nii.gz`: raw TotalSegmentator output, kept for audit.
- `case_001_tracts.csv`, `case_001_tracts.json`: ranked tracts and the parameters used.

Segmentation takes minutes per case on CPU. MPS on Apple Silicon is unverified.

## In 3D Slicer

1. Install the package into Slicer's Python, from the Python console: `slicer.util.pip_install("-e /path/to/stone-planner")`. TotalSegmentator is installed with it.
2. Edit > Application Settings > Modules > Additional module paths: add the `StonePlanner` folder of this repo. Restart. The module is under "Research".
3. Load `case_001.nii.gz`, select it, press Run segmentation.
4. Fix organs and stones in Segment Editor. Keep organ segment names as they are; the planner reads label values from them. Stones can be added, removed or painted.
5. Press Plan tracts. The top three tracts appear as lines: green, yellow, orange.
6. Press Export plan to write the tracts CSV and JSON. Record how many corrections step 4 needed; it goes in the paper.

Developer mode > Reload and Test runs the module's self-test on a synthetic phantom.

## Validation report

Fill `manual.csv` with one row per case:

```
case_id,manual_stone_volume_mm3,laterality,operative_calyx,operative_supracostal,stone_free,residual_mm
case_001,1840,left,lower,0,1,
```

`operative_calyx` is one of upper, mid, lower, pelvis. Then:

```
python scripts/validate.py cases/ manual.csv report/
```

`report/validation.csv` has one row per case; `report/validation_summary.txt` has the exit-criterion numbers.
