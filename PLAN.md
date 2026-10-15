# PCNL access planner

Research software for planning percutaneous nephrolithotomy access tracts from autocontoured noncontrast CT. Proof of concept only. Not for clinical use. Planning only, no intraoperative navigation.

## Core idea

The AI part is segmentation (off the shelf). The planning part is geometry, not machine learning. For a candidate tract we compute the fraction of stone volume reachable by a nephroscope pivoting at the parenchymal entry point, subject to collision constraints with surrounding organs. Tracts are ranked by that score.

## What already exists

- Stone volume from NCCT: solved. HU threshold inside a kidney mask with a per-stone half-max cutoff correlates with manual volume at r2 = 0.95 (Elton et al., NIH, PMC10407943). A 410-scan multiview model from Ulsan reached Dice 87.56% on stone segmentation and R2 > 0.964 on volume, density and skin-to-stone distance.
- Organ segmentation: TotalSegmentator (nnU-Net) covers kidneys, liver, spleen, colon, lungs, ribs, vertebrae, aorta. Runs from CLI or inside 3D Slicer. No stone class as far as we know; stones threshold trivially once bone is excluded.
- Tract planning: thin literature. AUA 2026 (Zein) used YOLOv8 on 2D slices and cannot model reach or collision. Frontiers in Surgery 2022 pilot used an open-source 3D tool for semi-automated puncture planning. RSIP Vision sells a commercial planner. MICCAI 2022 did ellipsoid fitting for concentric-tube robots on 7 to 8 datasets. No published 3D tool outputs reachable stone fraction with collision penalties. That is the gap.

## Central design problem

Stone patients get noncontrast CT. The collecting system is not visible. Options in rising difficulty:

1. Target the stone and let it stand in for the calyx. Valid for staghorn and large calyceal stones. Weak for pelvic stones with an empty entry calyx.
2. Infer posterior calyces from the renal pelvis axis and sinus fat geometry (Brodel line roughly 30 degrees posterior to the coronal plane). Heuristic, not segmentation.
3. Train a collecting-system model on CT urogram excretory phase and apply to NCCT. Separate research project.

Phase 1 uses option 1. Phase 2 adds option 2. Phase 3 attempts option 3.

## Known limitations of the method class

State these up front in any write-up:

- CT is supine; surgery is prone or supine by preference. Colon and spleen positions shift.
- Respiratory phase at CT changes pleural risk for supracostal tracts.
- No vascular map on NCCT.

## Stack

Python throughout.

| Layer | Choice | Why |
|---|---|---|
| DICOM handling | pydicom, dcm2niix | Standard, scriptable |
| De-identification | pydicom header scrub, burned-in text check | Abdominal CT needs no defacing |
| Volume ops | SimpleITK, numpy, scipy | Ray casting and masks |
| Meshes | scikit-image marching cubes, trimesh | Skin surface and organ meshes |
| Segmentation | TotalSegmentator | Organs; restrict to a subset for speed |
| Stones | HU threshold in kidney mask, half-max per stone | No training needed |
| Prototype UI | 3D Slicer Python module | Viewer, segment editor and markups already exist |
| Web backend | FastAPI | Serves NIfTI and masks, runs scoring |
| Web viewer | NiiVue | Lightweight WebGL, NIfTI-native, works on phone |
| GPU inference | Modal or RunPod | Cents per scan, no local GPU dependency |
| Hosting | Netlify (static front end) | Already in use |

TotalSegmentator runs on CPU in minutes, acceptable for research. Apple Silicon MPS support is unverified; do not assume the laptop is the inference box.

Alternative viewer: OHIF/Cornerstone3D if PACS-grade DICOM behavior is ever needed. Not before.

## Data

- Primary: in-house de-identified PCNL CTs. They carry the answer key: actual puncture site, operative note, postop residual. Retrospective de-identified imaging needs an IRB determination. File it in week one.
- Public, for sanity checks only: KiTS23 (contrast, tumors), NIH CT colonography set (Elton paper), 257-case NCCT kidney and stone set from Li et al. 2022 Diagnostics.

De-identified imaging stays private. The geometry engine is open source.

## Geometry engine specification

Inputs: skin mesh, kidney mask, stone mask, organ masks (colon, spleen, liver, lung, ribs, vertebrae, aorta).

For each candidate entry point on the posterior and posterolateral flank (subcostal and supracostal) and each target:

- Collision: hard fail if the line intersects colon, spleen, liver, lung or rib. Soft penalty on distance to nearest colon and pleura.
- Reach: model the rigid nephroscope as a cone pivoting at the parenchymal entry point with a parameterized sweep (start at 25 degrees) and depth. Count stone voxels inside the cone. Report reachable fraction. Repeat with a wider sweep for a flexible scope.
- Tract metrics: tract length, parenchymal depth, infundibular angle, supracostal flag.

Rank by reachable fraction subject to collision constraints. The score, not the chosen tract, is what gets tested against surgeons.

## Phase 1: Proof of concept

Slicer only. 4 to 8 weeks of evenings.

Goal: compute and rank reachable stone fraction for tracts on real NCCT.

Tasks:

- [ ] IRB determination filed
- [ ] De-id pipeline: DICOM in, header scrub, burned-in text check, dcm2niix out. 20 cases.
- [ ] TotalSegmentator run on all 20, restricted to kidneys, liver, spleen, colon, lungs, ribs, vertebrae, aorta, body contour
- [ ] Stone mask by HU threshold inside kidney mask, bone excluded, half-max per stone. Output volume and mean HU.
- [ ] Validate stone volumes against manual measurement
- [ ] Geometry engine v1 as a Slicer Python module: skin sampling, ray casting, hard-fail collisions, rigid cone reach to stone centroid
- [ ] Output top 3 tracts as line markups with reachable fraction, tract length, nearest-organ distance

Exit criterion: on 20 cases, top-ranked tract lands in the same calyceal group as actual operative access in a majority, and stone volumes agree with manual within 10%. Poor concordance means the stone-centroid target is the problem; Phase 2 fixes it.

Deliverables: Slicer module, 20-case de-identified set, one-page methods note.

## Phase 2: Refinement

8 to 16 weeks.

Goal: make the planner anatomically honest and testable.

Tasks:

- [ ] Posterior calyx proxy from renal pelvis axis and sinus fat geometry; manual target-calyx pick as fallback
- [ ] Soft penalties: colon and pleura distance, supracostal flag, parenchymal thickness, infundibular angle. Weighted score replaces hard-fail-only.
- [ ] Flexible-scope reach as a second score
- [ ] Manual correction loop in Slicer segment editor; log correction frequency (goes in the paper)
- [ ] Grow to 50 to 80 cases
- [ ] Concordance with actual access; correlation of predicted reachable fraction with postop residual fragment status
- [ ] Web wrapper, only after scoring is stable: FastAPI, NiiVue, GPU inference on Modal or RunPod, Netlify front end

Exit criterion: concordance and residual-fragment correlation good enough to write up; web demo loads a case and shows ranked tracts in under 2 minutes.

Deliverables: refined engine, web demo, abstract-grade retrospective dataset.

## Phase 3: Presentation ready

12 to 20 weeks.

Goal: a study and a demo for AUA or WCET and a journal.

Tasks:

- [ ] Reader study: residents, fellows, attendings plan tracts on 30 cases with and without the tool, counterbalanced. Outcomes: reachable fraction of chosen tract, predicted collisions, time to decision, agreement with expert consensus, inter-rater variability. Hypothesis: residents converge toward attending choices with the tool.
- [ ] Retrospective outcome tie-in: predicted reachable fraction of actual tract versus stone-free status on postop imaging
- [ ] Collecting-system model: nnU-Net on CT urogram excretory phase, applied to NCCT, compared against Phase 2 heuristic. Swap in if it wins; report if it doesn't.
- [ ] Web app polish: case upload, side-by-side tracts, plan export (PDF or screenshot), basic auth, phone layout
- [ ] Limitations section written first
- [ ] Abstract and manuscript
- [ ] Open-source release of the geometry engine

Deliverables: abstract, manuscript, live web demo, public repo.

## What to avoid

- Do not start with the web viewer. Slicer first.
- Do not train a stone model. Thresholding is enough.
- Do not aim at live navigation or intraoperative use. Planning-only keeps this software-only and off the FDA's radar.
- Do not pursue phone photos of CT images. Three photos in three planes are three slices out of hundreds; HU, spacing and the volume are gone. Coarse stone sizing from a photo is a toy, not a planner. Phone access is the web app rendered on a phone with the volume from PACS export.

## References

- Elton DC et al. A deep learning system for automated kidney stone detection and volumetric segmentation on non-contrast CT scans. https://pmc.ncbi.nlm.nih.gov/articles/PMC10407943
- Deep-learning segmentation of urinary stones in noncontrast CT (Ulsan). https://oak.ulsan.ac.kr/handle/2021.oak/17316
- Wasserthal J et al. TotalSegmentator. https://arxiv.org/abs/2208.05868v2
- Zein M. AUA 2026, CNN for CT-guided stone identification and needle path planning. https://www.urotoday.com/conference-highlights/aua-2026/aua-2026-endourology-and-stone-disease/168982-aua-2026-convolutional-neural-network-for-ct-guided-kidney-stone-identification-and-needle-path-planning-in-percutaneous-nephrolithotomy.html
- 3D rendering for PCNL puncture planning, pilot. Frontiers in Surgery 2022. https://www.frontiersin.org/journals/surgery/articles/10.3389/fsurg.2022.891596
- Ellipsoid optimization for PCNL planning with concentric-tube robots. MICCAI 2022. https://conferences.miccai.org/2022/papers/361-Paper2729.html
- Li et al. Deep segmentation networks for segmenting kidneys and detecting kidney stones in unenhanced abdominal CT. Diagnostics 2022. https://doi.org/10.3390/diagnostics12081788
