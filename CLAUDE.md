# Claude Code notes for stone-planner

Read PLAN.md first. README.md is for physician collaborators; keep it free of stack detail.

Phase 2 scope: posterior calyx proxy with manual target pick, soft penalties and a weighted score, flexible-scope reach as a second score, correction logging in Slicer, and the residual-fragment statistics in validate.py. The web app (stoneplanner_api/, frontend/, modal_app/, Dockerfile, render.yaml) was started at the user's request before the scoring is stable; see docs/deploy.md. No model training (Phase 3).

Clinical defaults (penalty weights, margins, calyx angle and depth, flexible-scope sweep) are placeholders until surgeons set them. Keep every one a PlanParams field, written into the output JSON.

Module contracts are in the docstrings of stoneplanner/ingest.py, segment.py, geometry.py, calyx.py, corrections.py, StonePlanner/StonePlanner.py, scripts/validate.py, stoneplanner_api/__init__.py and modal_app/segment_gpu.py. Implement to those contracts; change a contract only with a reason stated in the commit message.

Rules:
- No imaging data in the repo, ever. .gitignore blocks it; do not weaken that.
- The web server accepts de-identified NIfTI only and keeps nothing: no database, no persistent disk. Work persists as the user's saved-case zip. Do not add server-side storage of imaging without the user's decision.
- The web image must not carry PyTorch or TotalSegmentator; segmentation runs on Modal.
- Physical mm in RAS for all geometry. Voxel indices only inside mask lookups.
- Tests run on synthetic phantoms (tests/), never on real scans.
- Parameters go in PlanParams and get written into output JSON.
- TotalSegmentator on CPU by default. MPS is unverified.
- Prose style: no em dashes, no filler, sentence-case headings.

Order of work (Phase 2): calyx.py and the scoring changes in geometry.py with phantom tests first, then corrections.py and the Slicer module, then validate.py.
