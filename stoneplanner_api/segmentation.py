"""Segmentation providers: where TotalSegmentator runs for the web app.

Every provider is a plain callable, CT (.nii.gz bytes) in, organ label map
with the body merged in as segment.BODY_LABEL (.nii.gz bytes) out, so the
job code and the tests do not care which one is in use. Providers:

    modal   Modal cloud GPU (modal_app/segment_gpu.py), called by name. The
            token comes from the admin page or MODAL_TOKEN_ID/SECRET.
    remote  A GPU machine you run, such as a home PC with an NVIDIA card,
            running gpu_worker/server.py; reached over a private network
            (Tailscale) with a bearer token.
    local   TotalSegmentator on this computer (CPU, or Apple's GPU via mps,
            which is unverified). Needs the "segment" extra installed.
    none    No segmentation; organ and stone maps are uploaded instead.

check() answers quickly whether a provider is reachable and set up, without
running a segmentation: it is what the admin page's Check button calls.
Only de-identified CTs are ever sent to a provider.
"""

from __future__ import annotations

import gzip
import json
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

Segmenter = Callable[[bytes], bytes]

PROVIDERS = ("modal", "remote", "local", "none")
REMOTE_TIMEOUT_S = 30 * 60
CHECK_TIMEOUT_S = 15


class SegmentationUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Modal


def _modal_function(app_name: str, function_name: str, token_id: str | None,
                    token_secret: str | None):
    try:
        import modal
    except ImportError as exc:
        raise SegmentationUnavailable("the modal package is not installed") from exc
    client = modal.Client.from_credentials(token_id, token_secret) \
        if token_id and token_secret else None
    return modal.Function.from_name(app_name, function_name, client=client), client


def modal_segmenter(app_name: str, function_name: str, token_id: str | None = None,
                    token_secret: str | None = None) -> Segmenter:
    """Without a token, the Modal client reads MODAL_TOKEN_ID/SECRET itself."""
    def segment(ct_nifti_gz: bytes) -> bytes:
        fn, _ = _modal_function(app_name, function_name, token_id, token_secret)
        return fn.remote(ct_nifti_gz)
    return segment


def modal_check(app_name: str, function_name: str, token_id: str | None,
                token_secret: str | None) -> str:
    """Look the function up (token valid, app deployed) without starting a GPU."""
    fn, client = _modal_function(app_name, function_name, token_id, token_secret)
    try:
        fn.hydrate(client=client)
    except Exception as exc:
        raise SegmentationUnavailable(f"Modal: {exc}") from exc
    return f"Modal function {app_name}/{function_name} is deployed and the token works."


# ---------------------------------------------------------------------------
# Remote GPU worker (gpu_worker/server.py)


def _remote_request(url: str, token: str, path: str, data: bytes | None, timeout: float):
    req = urllib.request.Request(
        url.rstrip("/") + path, data=data, method="POST" if data is not None else "GET",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/octet-stream"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise SegmentationUnavailable(f"GPU worker answered {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SegmentationUnavailable(f"GPU worker not reachable at {url} ({exc})") from exc


def remote_segmenter(url: str, token: str) -> Segmenter:
    """segment.last_stats keeps the worker's timing and peak graphics memory."""
    def segment(ct_nifti_gz: bytes) -> bytes:
        with _remote_request(url, token, "/segment", ct_nifti_gz, REMOTE_TIMEOUT_S) as r:
            segment.last_stats = {"worker_seconds": r.headers.get("X-Seconds"),
                                  "peak_gpu_gb": r.headers.get("X-Peak-GPU-GB")}
            return r.read()
    segment.last_stats = {}
    return segment


def remote_check(url: str, token: str) -> dict:
    """The worker's /health: device, graphics memory, versions."""
    with _remote_request(url, token, "/health", None, CHECK_TIMEOUT_S) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------------------
# This computer


def local_segmenter(device: str) -> Segmenter:
    def segment(ct_nifti_gz: bytes) -> bytes:
        from stoneplanner import segment as seg

        with tempfile.TemporaryDirectory() as tmp:
            ct_path = Path(tmp) / "case.nii.gz"
            ct_path.write_bytes(ct_nifti_gz if ct_nifti_gz[:2] == b"\x1f\x8b"
                                else gzip.compress(ct_nifti_gz))
            try:
                return seg.run_totalsegmentator(ct_path, Path(tmp) / "out",
                                                device=device).read_bytes()
            except RuntimeError as exc:
                raise SegmentationUnavailable(str(exc)) from exc
    return segment


def local_check(device: str) -> str:
    try:
        import torch
        import totalsegmentator  # noqa: F401
    except ImportError as exc:
        raise SegmentationUnavailable(
            "TotalSegmentator is not installed in this install. On the Mac mini, install "
            'the planner with the segment extra (pip install -e ".[web,segment]"); the '
            "Docker image never carries it.") from exc
    if device == "mps" and not torch.backends.mps.is_available():
        raise SegmentationUnavailable("Apple GPU (mps) is not available here; use cpu")
    return f"TotalSegmentator is installed; it will run on {device} (torch {torch.__version__})."
