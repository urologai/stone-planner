# Claude Code notes for stone-planner

Read PLAN.md first. README.md is for physician collaborators; keep it free of stack detail.

Phase 1 scope only. Do not add web code, calyx inference, flexible-scope reach, soft penalties, or any model training. Those are Phase 2 and 3 and are listed in PLAN.md.

Module contracts are in the docstrings of stoneplanner/ingest.py, segment.py, geometry.py, StonePlanner/StonePlanner.py, and scripts/validate.py. Implement to those contracts; change a contract only with a reason stated in the commit message.

Rules:
- No imaging data in the repo, ever. .gitignore blocks it; do not weaken that.
- Physical mm in RAS for all geometry. Voxel indices only inside mask lookups.
- Tests run on synthetic phantoms (tests/), never on real scans.
- Parameters go in PlanParams and get written into output JSON.
- TotalSegmentator on CPU by default. MPS is unverified.
- Prose style: no em dashes, no filler, sentence-case headings.

Order of work: geometry.py with phantom tests first (the contribution), then segment.py, then ingest.py, then the Slicer module, then validate.py.
