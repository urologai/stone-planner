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
- Clinical judgment. The reach model has parameters (nephroscope sweep angle, depth) and the scoring has weights (how much to penalize a near-colon tract). These should come from surgeons, not from a programmer's guess. Argue with the defaults.
- Code. If you write Python, the geometry engine and the Slicer module are where the work is.

## How to look at it

Phase 1 runs inside 3D Slicer, a free desktop program for medical imaging. You load a case, run the module, and the top tracts appear as lines on the CT with their scores. Phase 2 adds a web viewer so you can review cases from a browser or phone without installing anything.

## Data policy

No imaging ever goes in this repository. DICOM, NIfTI, and segmentation files are blocked by `.gitignore`. De-identified imaging lives on institutional storage and is shared only under the IRB determination. The code is open source.

## Where to argue

Open an issue on this repo for anything: a case to contribute, a parameter you think is wrong, a limitation we missed, a reference we should have cited. Disagreement about the surgical assumptions is the most valuable input at this stage.
