"""Client for the GPU segmentation service on Modal.

The server sends the CT (.nii.gz bytes) to the deployed Modal function
(modal_app/segment_gpu.py) and gets the organ label map back as .nii.gz
bytes, with the body merged in as segment.BODY_LABEL. Modal credentials
come from MODAL_TOKEN_ID and MODAL_TOKEN_SECRET in the environment.

The segmenter is a plain callable, so tests and local runs can swap in
anything with the same signature.
"""

from __future__ import annotations

from collections.abc import Callable

Segmenter = Callable[[bytes], bytes]


class SegmentationUnavailable(RuntimeError):
    pass


def modal_segmenter(app_name: str, function_name: str) -> Segmenter:
    def segment(ct_nifti_gz: bytes) -> bytes:
        try:
            import modal
        except ImportError as exc:
            raise SegmentationUnavailable("the modal package is not installed") from exc
        fn = modal.Function.from_name(app_name, function_name)
        return fn.remote(ct_nifti_gz)
    return segment
