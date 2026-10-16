# GPU worker on a home PC

A PC with an NVIDIA card can do the segmentation for the Mac mini, instead of Modal. The Mac sends a de-identified CT over Tailscale, the PC runs TotalSegmentator on its GPU and sends the organ map back. Nothing is kept on the PC except the model weights. The worker is `gpu_worker/server.py`, packaged as a Docker image.

What the card needs is graphics memory, not speed: roughly 8 GB for typical abdominal CTs, more for large thin-slice scans. A 12 GB card (RTX 4070 Ti) is enough; the Admin tab's test reports the peak so you can see how close a case comes.

**Setting it up:** follow [get-it-running.md](get-it-running.md), Parts B and C, step by step. This page is the background.

## How it works

- `POST /segment` takes the CT as .nii.gz bytes and returns the organ map, with `X-Seconds` and `X-Peak-GPU-GB` headers that the Admin tab's test shows. `GET /health` reports the GPU, its memory and the versions. Both need `Authorization: Bearer <WORKER_TOKEN>`; the worker refuses to start with a token shorter than 32 characters.
- Input and output exist only in a temporary folder for the length of the call. The model weights (about 1.6 GB, downloaded on the first segmentation) live in the `stoneplanner-weights` Docker volume and survive updates.
- The image installs from `gpu_worker/requirements.txt`, a hash-pinned lock. PyTorch's Linux build from PyPI carries CUDA 13, which is why the PC needs NVIDIA driver 580 or newer. It covers RTX 40 and 50 series cards.
- Reach it only over Tailscale: publish the port on the PC's Tailscale address, or publish it normally and allow it in the firewall only from 100.64.0.0/10. Tailscale encrypts the traffic, so plain `http://` on the tailnet is fine. Never forward the port on a router.

## Notes

- The worker runs one CT at a time; a second request waits.
- TotalSegmentator's usage telemetry is switched off (`stoneplanner/segment.py`), here as on Modal and the Mac.
- A game running on the same card competes for its memory. Close it before segmenting.
- Linux with an NVIDIA card works the same way, with the NVIDIA Container Toolkit installed for `--gpus all`.

- To update: see [get-it-running.md](get-it-running.md#updating-later).
