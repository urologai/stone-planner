# stone-planner

Research software for planning percutaneous nephrolithotomy (PCNL) access tracts from autocontoured noncontrast CT. Proof of concept only. Not for clinical use. Planning only, no intraoperative navigation.

See [PLAN.md](PLAN.md) for the three-phase build plan, stack, geometry engine spec, and references.

## Data policy

No imaging data is committed to this repository. DICOM, NIfTI, and segmentation masks live outside the repo and are ignored by `.gitignore`. De-identified imaging stays private; the geometry engine is open source.
