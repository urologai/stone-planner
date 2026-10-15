# Methods note, Phase 1

Draft. Numbers in the results section are filled in after the 20-case run. Research software; not for clinical use.

## Aim

Rank candidate PCNL access tracts on preoperative noncontrast CT by the fraction of stone volume a rigid nephroscope can reach, excluding tracts that cross colon, spleen, liver, lung or rib, and test the ranking against the access surgeons actually used.

## Data

Twenty retrospective PCNL cases with preoperative noncontrast CT, operative notes, and postoperative stone status, under an IRB determination for retrospective de-identified imaging. For each case the reference standard is the calyceal group of the actual puncture (upper, mid, lower, pelvis), whether it was supracostal, and a manual stone volume.

## De-identification and conversion

DICOM headers were reduced to an allowlist of acquisition and geometry attributes, which removes a superset of the DICOM PS3.15 basic profile; dates were truncated to year and UIDs regenerated. Each series was screened for burned-in annotation by header flag and by a pixel heuristic on the top and bottom image rows, and flagged cases were reviewed by hand. Series were converted to NIfTI with dcm2niix and reoriented to RAS with Hounsfield units preserved.

## Segmentation

Organs were segmented with TotalSegmentator (v2, "total" task restricted to kidneys, liver, spleen, colon, lower lung lobes, ribs 9 to 12, vertebrae T11 to L4, hips and aorta) and the body outline with its "body" task. Stones were segmented without training: voxels above 130 HU within 5 mm of either kidney, excluding components touching vertebra or rib, components of at least 3 voxels, each re-thresholded at half its maximum HU to correct partial volume, after Elton et al. (2022). Segmentations were reviewed and corrected in 3D Slicer before planning, and the number of corrections was recorded.

## Tract planning

Candidate entry points were vertices of the skin surface (marching cubes on the body mask) on the operative-side flank: posterior to the mid-axillary line, taken as the most lateral point of the rib cage, between the iliac crest and two rib interspaces above the 12th rib. Up to 3000 points were sampled. The target was the centroid of the largest stone and of any other stone over 100 mm3 on that side.

Each entry-to-target segment was sampled every 0.5 mm. A tract failed if any sample fell in colon, spleen, liver, lung or rib. For clear tracts we recorded the nearest of those organs and its distance, the tract length, the parenchymal depth (first sample inside the kidney to the target), and whether the entry was cranial to the inferior edge of the 12th rib (supracostal).

Reach was modelled as a cone with its apex at the parenchymal entry, its axis along the tract, a 25 degree half-angle and a 120 mm depth limit. The reachable fraction is the share of all operative-side stone voxels inside the cone. Collision-free tracts were ranked by reachable fraction, then by distance to the nearest organ (larger first), then by tract length (shorter first).

## Evaluation

The calyceal group of the top-ranked tract was assigned from the target's position within the kidney mask: upper, middle or lower third of its craniocaudal extent, or pelvis if inside the kidney's convex hull but outside the parenchyma. Phase 1 succeeds if this group matches the operative group in a majority of the 20 cases and automated stone volumes agree with manual volumes within 10%. Supracostal agreement is reported alongside.

Before use on patient data, each component was tested on synthetic phantoms with known answers: collision detection, reach (full, none, monotonic in sweep angle), the supracostal flag, de-identification of planted identifiers, orientation and Hounsfield unit preservation through conversion, and stone volume recovery. On blurred phantom spheres, half-maximum thresholding recovered volume within 3% when stone radius was at least four times the blur width, and underestimated by about 30% when the radius was near twice the blur width, so small stones on thick slices are expected to read low.

## Results

To be completed: volume agreement (mean and median absolute percent error, cases within 10%), calyceal concordance (k of 20), supracostal concordance, segmentation correction frequency, and run time per case.

## Limitations

- CT is supine; most PCNL is prone or supine by preference, and colon and spleen positions shift between the two.
- Respiratory phase at CT changes pleural risk for supracostal tracts.
- Noncontrast CT shows no vessels and no collecting system. The stone centroid stands in for the target calyx, which suits staghorn and calyceal stones and suits a pelvic stone with an empty entry calyx poorly.
- The scope is rigid. Flexible-scope reach and soft penalties for organ proximity are Phase 2.
- Flank boundaries are single planes per case, not per-slice curves.
- Calyceal group is assigned by thirds of the kidney, a crude proxy replaced in Phase 2.

## Software

Python, with pydicom, dcm2niix, SimpleITK, nibabel, NumPy, SciPy, scikit-image and TotalSegmentator; the planning interface is a 3D Slicer scripted module. The geometry engine is open source.

## References

- Elton DC et al. A deep learning system for automated kidney stone detection and volumetric segmentation on non-contrast CT scans. Med Phys 2022. PMC10407943.
- Wasserthal J et al. TotalSegmentator: robust segmentation of 104 anatomic structures in CT images. Radiol Artif Intell 2023. arXiv:2208.05868.
