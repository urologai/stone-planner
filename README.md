# stone-planner

A research tool for planning the access tract in percutaneous nephrolithotomy (PCNL) from a patient's noncontrast CT.

This is a software proof of concept. It does not touch patients, it is not a medical device, and it is not for clinical use. The goal is to find out whether a computer can rank candidate tracts well enough to help a surgeon plan, and to test that claim against surgeons at different experience levels.

The full build plan, stack, and references are in [PLAN.md](PLAN.md). This page is the overview for collaborators.

## What the tool does

1. Takes a de-identified noncontrast CT.
2. Automatically contours the kidneys, stone, and surrounding organs (colon, spleen, liver, lung, ribs, vertebrae, aorta) using an off-the-shelf segmentation model.
3. Measures stone volume and density.
4. Samples candidate skin entry points on the posterior and posterolateral flank and, for each one, computes two things:
   - Collision: does the tract pass through colon, spleen, liver, lung, or rib, and how close does it come?
   - Reach: if a rigid nephroscope enters at that point and pivots through its normal sweep, what fraction of the stone volume can it reach?
5. Ranks tracts by reachable stone fraction subject to the collision constraints, and shows the top candidates on the CT.

The segmentation is AI. The tract ranking is geometry. We think the geometry is the part nobody has published in 3D with a reach-and-collision score, and that is the contribution.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/workflow-graph-dark.svg">
  <img src="docs/diagrams/workflow-graph-light.svg" alt="Workflow of the stone planner in five bands. Ingest, on your computer: the preop CT, postop imaging, operative report and MRN go into a folder scan, de-identification, a clinical form and a study ID; you check and save, which writes a case folder, manual.csv, and a separate MRN key file. Segmentation: the de-identified CT goes to a GPU service and comes back as an organ map, and stones are detected. Planning: targets, skin entry points and parameters feed a collision check and rigid and flexible reach for every tract, which are ranked. Review: the planner view shows the top tracts, you review and correct them, and the results are saved to the case folder. Validation: the saved results and manual.csv produce the validation report.">
</picture>

Rose marks identifiable data: the DICOM export, the chart and the MRN. It stays on the computer that runs the ingest page. Amber marks the places where you decide: before a case is saved, the planner parameters, and the review of the tracts. Teal marks what the study tests: the ranked tracts and the validation report. Dashed shapes keep nothing: the GPU service gets a de-identified CT, returns the organ map and keeps neither. A standalone version of the diagram is in [docs/diagrams/workflow-graph.html](docs/diagrams/workflow-graph.html).

1. **Ingest.** You choose the exported CT study folder. The page lists every series, picks the axial CT and shows its slice thickness. It removes identifiers from the image headers and warns if text may be burned into the pixels. You fill in the case form from the operative report and chart. The case gets the next study ID (case_001, case_002, ...), and the MRN goes only into a key file kept in a separate folder.
2. **Segmentation.** The de-identified CT goes to a GPU service, which outlines the kidneys and surrounding organs and keeps nothing. Stones are found as dense voxels inside the kidney, with their volume and density.
3. **Planning.** For every pair of skin entry point and target, the planner checks whether the tract hits colon, spleen, liver, lung or rib (any hit rules it out) and how much of the stone a rigid scope reaches from it. Closeness to colon and pleura, a supracostal entry, and other penalties lower the score. The parameters behind these are placeholders until surgeons set them.
4. **Review.** You see the CT with organs, stones and the top three tracts, and a ranked table. If the segmentation is wrong, fix it in 3D Slicer; the corrections are logged. Saving writes the results into the case folder so nothing is recomputed.
5. **Validation.** A report compares the top tract with the calyx actually used, the automatic stone volume with a manual one, and predicted reach with residual fragments after surgery.

## What it does not do

- It does not see the collecting system. Stone patients get noncontrast CT, so calyces are invisible. Early versions target the stone itself as a stand-in for the calyx, which works for staghorn and large calyceal stones and poorly for a pelvic stone with an empty entry calyx. Later phases add a posterior-calyx estimate and, if it works, a model trained on CT urograms.
- It does not account for positioning. CT is supine; you may operate prone. Colon and spleen move.
- It does not account for respiratory phase, which changes pleural risk for supracostal tracts.
- It does not show vessels. Noncontrast CT has none.
- It does not navigate. This is preoperative planning only, which is what keeps it a research tool rather than a device.

## How we will test it

Three phases, each with an exit criterion. Details in PLAN.md.

- Phase 1, proof of concept: 20 retrospective PCNL cases with known access. Does the top-ranked tract land in the same calyceal group as the one actually used? Do automated stone volumes match manual ones within 10%?
- Phase 2, refinement: 50 to 80 cases. Add the calyx estimate and softer penalties. Does predicted reachable fraction correlate with postoperative residual fragments? Build the web viewer.
- Phase 3, presentation ready: a reader study. Residents, fellows, and attendings plan tracts on 30 cases with and without the tool. We measure reachable fraction of the chosen tract, predicted collisions, time to decision, and agreement with expert consensus. The hypothesis is that residents converge toward attending choices when they have the tool.

No patient is enrolled, examined, or treated at any phase. Everything is retrospective, de-identified imaging plus a reader study on that imaging.

## How you can help

- Cases. Retrospective PCNL CTs with the operative note and postop imaging are the dataset. Each case needs the actual puncture site (calyceal group, supracostal or subcostal) and stone-free status. If you have cases and are willing to share under the IRB determination, that is the single most useful contribution.
- Ground truth. Manual stone volume measurements and manual calyx labeling on a subset, so we can check the automated output.
- Reader study. Phase 3 needs surgeons at every level to plan tracts on the case set, with and without the tool. Takes a few hours spread over a few sessions.
- Clinical judgment. The reach model has parameters (nephroscope sweep angle, depth) and the scoring has weights (how much to penalize a near-colon tract). These should come from surgeons, not from a programmer's guess. Argue with the defaults: [docs/parameters.md](docs/parameters.md) lists each one and the question it raises.
- Code. If you write Python, the geometry engine and the Slicer module are where the work is.

## What we need from each case

Minimum per case for the 20-case Phase 1 set:

- Preoperative noncontrast CT, full axial series as DICOM. Not the coronal or sagittal reformats alone, and not a screenshot or PDF. Thin slices if available, 1 to 3 mm. Note the slice thickness.
- Patient position on the CT (supine is assumed) and whether it was done here or outside, since outside protocols vary.
- Operative report, with these fields pulled out explicitly even if the narrative has them: laterality, OR position (prone or supine), number of tracts, and for each tract the calyceal group targeted (upper, mid, lower, or pelvis) and supracostal or subcostal. Sheath size and fluoroscopic versus ultrasound access are useful but not required.
- Stone description from the surgeon's or radiologist's read: location, staghorn or not, largest dimension.
- Postoperative imaging and stone-free status: the postop CT or KUB as DICOM if one exists, and a yes/no on residual fragments with size if present. This is the outcome the reach score gets tested against.
- Complications relevant to tract choice: pleural injury, colon injury, transfusion. Expect zeros; record them anyway.
- Age, sex, BMI. BMI matters because skin-to-stone distance limits tract length. The planner measures that distance on the CT itself; BMI lets us check the measurement and describe the cases.
- A study ID key linking the de-identified case number to the MRN, kept off this repo and off any shared drive.

Nice to have, not required for Phase 1: a manual stone volume measured by someone on the team, and the surgeon's own annotation of the puncture site on the preop CT.

## How to use it

The tool runs on your own computer in a free program called Docker, which packages the software so there is nothing else to install. These steps are for a Mac. Use a computer your institution approves for patient data, with disk encryption (FileVault) turned on: the DICOM export and the MRN key are identifiable. 3D Slicer is optional, for correcting a segmentation by hand ([docs/running.md](docs/running.md)).

### Once, to set it up

1. Install Docker Desktop from docker.com and open it. Wait until it says Docker is running. It has to be open whenever you use the tool.
2. Open Terminal (Applications, Utilities) and paste these lines, one at a time. The first may ask to install the Mac developer tools; say yes and run it again when that finishes.

   ```
   git clone https://github.com/urologai/stone-planner.git
   cd stone-planner
   docker build -t stone-planner .
   mkdir -p ~/stone-data ~/stone-key ~/stone-report
   ```

   The build takes a few minutes. `stone-data` will hold the de-identified cases. `stone-key` will hold the file that links study IDs to MRNs. `stone-report` will hold the validation report. Keep both out of iCloud Drive, Dropbox, OneDrive and any shared drive.

### Each time you use it

1. In Terminal, start the tool. Pick your own password in place of `choose-a-password`:

   ```
   cd ~/stone-planner
   docker run -p 127.0.0.1:8000:8000 --user "$(id -u):$(id -g)" \
     -v ~/stone-data:/data -v ~/stone-key:/key \
     -e DATA_DIR=/data -e KEY_FILE=/key/mrn_key.csv \
     -e APP_PASSWORD=choose-a-password -e COOKIE_SECURE=false stone-planner
   ```

2. When the Terminal shows `Uvicorn running`, open http://localhost:8000 in your browser and log in with that password. The page is reachable only from this computer.
3. To stop, click the Terminal window and press Control-C.

To get an update: `cd ~/stone-planner`, then `git pull`, then `docker build -t stone-planner .` again.

### Enter a case (Ingest tab)

1. From PACS, export the preoperative noncontrast CT study to a folder as DICOM. The whole study is fine; the page picks the axial series. File names do not matter and are not kept.
2. The case ID is filled in for you. Type the MRN.
3. Under Preoperative CT, choose the study folder. Check that the series marked is the axial CT, and note the slice thickness shown. Thin slices, 1 to 3 mm, are best. Set the patient position on the CT and whether it was done here or outside.
4. Under Operative report, set laterality, OR position and the number of tracts, then for each tract the calyceal group and supracostal or subcostal. Add sheath size and fluoroscopic or ultrasound access if the note has them.
5. Under Stone, tick every location, choose staghorn (no, partial, complete) and enter the largest dimension in mm. Add a manual stone volume if someone measured one.
6. Under Postoperative outcome, choose stone free yes or no. If no, enter the largest residual fragment in mm. If there is a postop CT or KUB, export it as DICOM and choose that folder.
7. Under Complications, the answers start at no; change any that happened.
8. Under Patient, enter age (90 and over is recorded as 90), sex and BMI. Height and weight will fill BMI in for you.
9. The box at the bottom lists anything still missing. When it is empty, press Ingest case.
10. Read the message that appears. If it says text may be burned into the images, look at them before using the case. A postop KUB always carries this warning, because an X-ray cannot be checked automatically.

The case now appears under Cases on this computer.

### Plan a case (Planner tab)

1. In the case list, press Open in planner.
2. Press Segment on GPU. This needs the GPU service connected; see [docs/deploy.md](docs/deploy.md). It takes a few minutes.
3. Leave the side on auto unless you want a specific side. Leave the planner parameters at their defaults unless you are testing them ([docs/parameters.md](docs/parameters.md)). Press Plan tracts.
4. The top tracts appear on the CT in green, yellow and orange, with a ranked table below. Click a row to centre the images on that target.
5. Press Save to case folder. Opening the case again later shows the saved result without recomputing.

### Get the validation report

When cases are planned, run this in Terminal from `~/stone-planner`:

```
docker run --rm --user "$(id -u):$(id -g)" -v ~/stone-data:/data -v ~/stone-report:/report \
  -v "$PWD/scripts":/scripts:ro --entrypoint python stone-planner \
  /scripts/validate.py /data /data/manual.csv /report
```

The summary prints in Terminal. `validation.csv` in `~/stone-report` has one row per case.

### The hosted version

There is also a version on a web server for looking at cases away from this computer. It accepts only a CT that has already been de-identified, segments and plans it the same way, and keeps nothing: Save case downloads a file you open again later. It has no ingest page and never sees DICOM or MRNs.

## Data policy

No imaging ever goes in this repository. DICOM, NIfTI, and segmentation files are blocked by `.gitignore`. De-identified imaging lives on institutional storage and is shared only under the IRB determination. The code is open source.

## Where to argue

Open an issue on this repo for anything: a case to contribute, a parameter you think is wrong, a limitation we missed, a reference we should have cited. Disagreement about the surgical assumptions is the most valuable input at this stage.
