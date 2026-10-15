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
  - Segmentation (TotalSegmentator) runs on a GPU service on Modal
    (modal_app/segment_gpu.py). The server sends the CT and gets the organ
    map back; stone thresholding and planning run here on CPU. Users can
    also upload organ and stone maps segmented locally.
  - Access is gated by one shared password (APP_PASSWORD) and a signed,
    HttpOnly session cookie. A case is visible only to the session that
    created it.

Modules: config (settings from env), auth, cases (scratch store and
bundles), jobs (background segmentation and planning), overlay (tract
rasterization for the viewer), segmentation (Modal client), app (routes).
"""
