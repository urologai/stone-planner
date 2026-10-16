"""Web API for the stone planner: FastAPI backend behind the browser app.

Contract (see docs/deploy.md for the deployment side):

  - Uploads are de-identified NIfTI only. De-identification happens on the
    user's machine with stoneplanner-ingest; the server refuses DICOM and
    rewrites every NIfTI it accepts with a fresh header, so no free-text
    header field survives.
  - Nothing is kept. Each case lives in a scratch folder under WORK_DIR
    while it is used, is deleted after CASE_TTL_MINUTES idle, and the folder
    is wiped at start-up. Work is kept on the user's disk instead: "Save
    case" downloads a bundle (zip) that "Open case" accepts, so a saved case
    resumes without re-running segmentation.
  - Segmentation (TotalSegmentator) runs outside this server: on Modal
    (modal_app/segment_gpu.py), on a GPU worker the user runs
    (gpu_worker/server.py), or, on a native local install, on this computer.
    A local install chooses on its Admin page (admin.py); a hosted server
    uses Modal from the environment. The server sends the de-identified CT
    and gets the organ map back; stone thresholding and planning run here on
    CPU. Users can also upload organ and stone maps segmented locally.
  - Access is gated by one shared password (APP_PASSWORD) and a signed,
    HttpOnly session cookie. A case is visible only to the session that
    created it.

Modules: config (settings from env), auth, cases (scratch store and
bundles), jobs (background segmentation and planning), overlay (tract
rasterization for the viewer), segmentation (providers), admin (local Admin page settings), app (routes).
"""
