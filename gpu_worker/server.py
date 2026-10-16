"""GPU segmentation worker: TotalSegmentator on a machine you run (a home PC).

The planner on the Mac mini sends a de-identified CT here and gets the organ
label map back, the same thing modal_app/segment_gpu.py does on Modal and
stoneplanner-segment does locally, because all three call
segment.run_totalsegmentator.

    POST /segment   body: CT as .nii.gz bytes. Returns the organ map (.nii.gz),
                    with X-Seconds and X-Peak-GPU-GB headers for the admin page.
    GET  /health    GPU name and memory, versions, whether a job is running.

Both need "Authorization: Bearer <WORKER_TOKEN>". Run it only on a private
network (Tailscale); never expose the port to the internet. One segmentation
runs at a time; a second request waits for the first. Nothing is kept: input
and output live in a temporary folder for the length of the call. Model
weights are downloaded on first use into TOTALSEG_HOME_DIR (mount a volume
there so they survive restarts).

Environment:
    WORKER_TOKEN        required, at least 32 characters
    WORKER_DEVICE       cuda (default) or cpu
    MAX_CT_MB           largest CT accepted, default 600
"""

from __future__ import annotations

import asyncio
import gzip
import hmac
import io
import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response

MIN_TOKEN_LEN = 32
# A NIfTI-1 file starts with sizeof_hdr = 348, in either byte order.
NIFTI1_SIZEOF_HDR = (b"\x5c\x01\x00\x00", b"\x00\x00\x01\x5c")


def create_app(token: str | None = None, device: str | None = None,
               run=None) -> FastAPI:
    """run(ct_path, out_dir, device) -> organ map path; tests pass a stub."""
    token = token if token is not None else os.environ.get("WORKER_TOKEN", "")
    if len(token) < MIN_TOKEN_LEN:
        raise RuntimeError(f"set WORKER_TOKEN to a random string of {MIN_TOKEN_LEN}+ "
                           "characters (the same value goes on the planner's Admin page)")
    device = device or os.environ.get("WORKER_DEVICE", "cuda")
    max_bytes = int(os.environ.get("MAX_CT_MB", "600")) * 1024 * 1024
    if run is None:
        from stoneplanner import segment

        run = segment.run_totalsegmentator
    lock = threading.Lock()
    app = FastAPI(title="Stone planner GPU worker", docs_url=None, redoc_url=None,
                  openapi_url=None)

    def authorized(request: Request) -> None:
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "wrong or missing token")

    @app.get("/health")
    def health(_: None = Depends(authorized)):
        info = {"ok": True, "device": device, "busy": lock.locked(), "gpu": None,
                "gpu_memory_gb": None}
        try:
            import torch

            info["torch"] = torch.__version__
            info["cuda"] = torch.version.cuda
            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                info["gpu"] = props.name
                info["gpu_memory_gb"] = round(props.total_memory / 1024**3, 1)
        except ImportError:
            info["torch"] = None
        try:
            from importlib.metadata import version

            info["totalsegmentator"] = version("TotalSegmentator")
        except Exception:  # noqa: BLE001  not installed is a valid answer
            info["totalsegmentator"] = None
        return info

    def work(data: bytes) -> tuple[bytes, float, float | None]:
        with lock:
            start = time.monotonic()
            on_gpu = _reset_peak()
            with tempfile.TemporaryDirectory() as tmp:
                ct_path = Path(tmp) / "case.nii.gz"
                ct_path.write_bytes(data)
                try:
                    out = run(ct_path, Path(tmp) / "out", device=device).read_bytes()
                except Exception as exc:
                    raise HTTPException(500, f"segmentation failed: {exc}") from exc
            return out, time.monotonic() - start, _peak_gb() if on_gpu else None

    @app.post("/segment")
    async def segment_ct(request: Request, _: None = Depends(authorized)):
        data = await request.body()
        if len(data) > max_bytes:
            raise HTTPException(413, f"CT larger than {max_bytes // 1024**2} MB")
        if data[:2] != b"\x1f\x8b":
            raise HTTPException(400, "send the CT as .nii.gz bytes")
        try:
            sizeof_hdr = gzip.GzipFile(fileobj=io.BytesIO(data)).read(4)
        except (OSError, EOFError) as exc:
            raise HTTPException(400, "not a gzip file") from exc
        if sizeof_hdr not in NIFTI1_SIZEOF_HDR:
            raise HTTPException(400, "not a NIfTI-1 image")
        # On a worker thread, so /health still answers while a CT is running.
        out, seconds, peak = await asyncio.to_thread(work, data)
        headers = {"X-Seconds": f"{seconds:.1f}"}
        if peak is not None:
            headers["X-Peak-GPU-GB"] = f"{peak:.2f}"
        return Response(out, media_type="application/gzip", headers=headers)

    return app


def _reset_peak() -> bool:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
            return True
    except ImportError:
        pass
    return False


def _peak_gb() -> float:
    import torch

    return torch.cuda.max_memory_reserved() / 1024**3


def app_from_env() -> FastAPI:
    return create_app()
