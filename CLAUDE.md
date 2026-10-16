# Claude Code notes for stone-planner

Read PLAN.md first. README.md is for physician collaborators; keep it free of stack detail.

Phase 2 scope: posterior calyx proxy with manual target pick, soft penalties and a weighted score, flexible-scope reach as a second score, correction logging in Slicer, and the residual-fragment statistics in validate.py. The web app (stoneplanner_api/, frontend/, modal_app/, Dockerfile, render.yaml) was started at the user's request before the scoring is stable; see docs/deploy.md. No model training (Phase 3).

Clinical defaults (penalty weights, margins, calyx angle and depth, flexible-scope sweep) are placeholders until surgeons set them. Keep every one a PlanParams field, written into the output JSON.

Module contracts are in the docstrings of stoneplanner/ingest.py, segment.py, geometry.py, calyx.py, corrections.py, StonePlanner/StonePlanner.py, scripts/validate.py, stoneplanner_api/__init__.py and modal_app/segment_gpu.py. Implement to those contracts; change a contract only with a reason stated in the commit message.

Rules:
- No imaging data in the repo, ever. .gitignore blocks it; do not weaken that.
- The web server accepts de-identified NIfTI only and keeps nothing: no database, no persistent disk. Work persists as the user's saved-case zip. Do not add server-side storage of imaging without the user's decision.
- The local ingest page (stoneplanner_api/library.py, frontend/src/Ingest.tsx) is the one exception, by the user's decision: it runs only when DATA_DIR and KEY_FILE are set, refuses to start when RENDER is set, and keeps case folders in DATA_DIR on the user's machine. It accepts DICOM and de-identifies it before anything is written there. The MRN goes only to KEY_FILE, which must sit outside DATA_DIR. Clinical fields are choices and numbers, never free text, so no name or date can reach a case folder. Uploaded file names are discarded.
- The web image must not carry PyTorch or TotalSegmentator; segmentation runs on Modal.
- Physical mm in RAS for all geometry. Voxel indices only inside mask lookups.
- Tests run on synthetic phantoms (tests/), never on real scans.
- Parameters go in PlanParams and get written into output JSON.
- TotalSegmentator on CPU by default. MPS is unverified.
- Prose style: no em dashes, no filler, sentence-case headings.

Order of work (Phase 2): calyx.py and the scoring changes in geometry.py with phantom tests first, then corrections.py and the Slicer module, then validate.py.
