"""GPU segmentation service on Modal: TotalSegmentator for the web app.

Deploy once (and after changes to this file or to stoneplanner/segment.py):

    pip install modal && modal setup
    modal deploy modal_app/segment_gpu.py

The Render service calls segment_organs by name (stoneplanner_api/segmentation.py)
with the de-identified CT as .nii.gz bytes and gets back the organ label map
as .nii.gz bytes: TotalSegmentator "total" task restricted to
segment.ORGAN_ROI_SUBSET, with the "body" task merged in as BODY_LABEL. This
is exactly what stoneplanner-segment produces locally, because it calls the
same run_totalsegmentator.

Nothing is stored on Modal except the model weights. They are downloaded
into a Modal Volume on first use and reused afterwards, so later runs skip
the download. Inputs and outputs live only in the container's temporary
folder for the length of the call.

Cost control: containers scale to zero after SCALEDOWN_S idle, and at most
MAX_CONTAINERS run at once. A T4 is enough for TotalSegmentator.
"""

from __future__ import annotations

from pathlib import Path

import modal

APP_NAME = "stone-planner-segment"
GPU = "T4"
TIMEOUT_S = 20 * 60
SCALEDOWN_S = 120
MAX_CONTAINERS = 2
WEIGHTS_DIR = "/weights"

app = modal.App(APP_NAME)
weights = modal.Volume.from_name("stone-planner-totalseg-weights", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("TotalSegmentator==2.18.0", "nibabel>=5.0", "numpy", "scipy",
                 "scikit-image")
    .env({"TOTALSEG_HOME_DIR": f"{WEIGHTS_DIR}/totalseg"})
    .add_local_python_source("stoneplanner")
)


@app.function(image=image, gpu=GPU, volumes={WEIGHTS_DIR: weights}, timeout=TIMEOUT_S,
              scaledown_window=SCALEDOWN_S, max_containers=MAX_CONTAINERS)
def segment_organs(ct_nifti_gz: bytes) -> bytes:
    """CT (.nii.gz bytes) in, organ label map with body (.nii.gz bytes) out."""
    import tempfile

    from stoneplanner import segment

    with tempfile.TemporaryDirectory() as tmp:
        ct_path = Path(tmp) / "case.nii.gz"
        ct_path.write_bytes(ct_nifti_gz)
        organ_path = segment.run_totalsegmentator(ct_path, Path(tmp) / "out", device="cuda")
        result = organ_path.read_bytes()
    weights.commit()   # keep any weights downloaded by this call
    return result


@app.local_entrypoint()
def main(ct: str, out: str = "organs.nii.gz"):
    """Try it from a workstation: modal run modal_app/segment_gpu.py --ct case.nii.gz"""
    Path(out).write_bytes(segment_organs.remote(Path(ct).read_bytes()))
    print(f"wrote {out}")
