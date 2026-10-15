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
                      cases/case_001/case_001_stones.nii.gz   cases/case_001 [--side left|right|auto] \
                      [--targets stone|calyx|both] [--params-json my_values.json]
```

`--targets` picks stone centroids, posterior calyx proxies, or both (default). `--params-json` overrides planner parameters; see [parameters.md](parameters.md) for what each one does and why the defaults are placeholders.

Outputs in `cases/case_001/`:

- `case_001.nii.gz`, `case_001.json`: de-identified CT and metadata. If ingest prints `BURNED-IN ANNOTATION SUSPECTED`, look at the images before going further.
- `case_001_organs.nii.gz`, `case_001_stones.nii.gz`, `case_001_stones.csv`: segmentation.
- `case_001_ts_total.nii.gz`, `case_001_ts_body.nii.gz`: raw TotalSegmentator output, kept for audit.
- `case_001_tracts.csv`, `case_001_tracts.json`: ranked tracts and the parameters used. The JSON also holds the best clear tract for each calyceal group.

Segmentation takes minutes per case on CPU. MPS on Apple Silicon is unverified.

## In 3D Slicer

1. Install the package into Slicer's Python, from the Python console: `slicer.util.pip_install("-e /path/to/stone-planner")`. TotalSegmentator is installed with it.
2. Edit > Application Settings > Modules > Additional module paths: add the `StonePlanner` folder of this repo. Restart. The module is under "Research".
3. Load `case_001.nii.gz` and select it. Press Run segmentation, or, if `stoneplanner-segment` already ran on this case, Load segmentation files (it reads `case_001_organs.nii.gz` and `case_001_stones.nii.gz` from the CT's folder; much faster).
4. Fix organs and stones in Segment Editor. Keep organ segment names as they are; the planner reads label values from them. Stones can be added, removed or painted.
5. Choose targets (both, calyx, stone). To aim at a calyx you pick yourself, place points in a point list and choose it under Manual targets. A parameters file is optional.
6. Press Plan tracts. The top three tracts appear as lines: green, yellow, orange, labelled with target, score, and rigid and flexible reach.
7. Press Export plan, into the case folder. It writes the tracts CSV and JSON, `case_001_corrections.json` (what you changed in step 4, for the correction frequency), the corrected organ and stone label maps, and `case_001_stones_corrected.csv`. The validation uses the corrected stones when they exist.

The correction log compares against the segmentation as it was when you ran or loaded it in this Slicer session. If you reload the scene, load the segmentation files again before editing.

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

The Phase 2 report adds, per case, the rigid and flexible reach of the best tract aimed at the calyceal group the surgeon actually used, and in the summary: AUC of that reach for stone-free versus residual cases, Spearman's rho against residual size, and how often segmentations were corrected and which organs.
